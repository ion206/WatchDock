"""WatchDock store-and-forward link (Mechanism C).

emit() is thread-safe and never blocks. A background asyncio thread keeps one
WebSocket open to the Mac's telemetry bridge. While the link is down, every
envelope is appended to <spool_dir>/<service>/watchdock_spool.jsonl. On
reconnect the spool is flushed oldest-first in acknowledged batches, then live
traffic resumes, so Foxglove backfills the gap. Bridge dedupes by envelope id,
so a flush interrupted halfway is safe to resend.

Requires: pip install websockets
"""
from __future__ import annotations

import asyncio, glob, itertools, json, os, socket, sys, threading, time

import websockets

HOST = os.environ.get("WATCHDOCK_HOST") or socket.gethostname()
# unique per process: host + pid + random token. (Found in the lab: two agents on one host started in the
# same second produced identical ids, and the bridge's backfill dedupe dropped one agent's data.)
_RUN = f"{os.getpid()}-{__import__('secrets').token_hex(4)}"
_seq = itertools.count()
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[watchdock {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def envelope(kind: str, service: str | None, topic: str, data: dict, ts_ns: int | None = None) -> dict:
    """The one message shape every WatchDock component speaks."""
    return {"v": 1, "id": f"{HOST}:{_RUN}:{next(_seq)}", "ts_ns": ts_ns or time.time_ns(),
            "host": HOST, "kind": kind, "service": service or "_host", "topic": topic, "data": data}


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


class StdoutSink:
    """JSON lines on stdout: for piping, debugging, or `| jq`."""
    def emit(self, env: dict) -> None:
        with _print_lock:
            sys.stdout.write(_dumps(env) + "\n"); sys.stdout.flush()

    def close(self) -> None:
        pass


class ForwardSink:
    def __init__(self, url: str, spool_dir: str = "/deployments", token: str | None = None,
                 batch: int = 500, max_spool_mb: int = 64, echo: bool = False):
        self.url, self.spool_dir, self.token, self.batch, self.echo = url, spool_dir, token, batch, echo
        self.max_bytes = max_spool_mb * 2**20
        self.connected = False
        self.spooled = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._q: asyncio.Queue | None = None
        threading.Thread(target=self._thread, name="wd-forwarder", daemon=True).start()
        self._ready.wait()

    # ---- public -------------------------------------------------------------
    def emit(self, env: dict) -> None:
        if self.echo:
            StdoutSink().emit(env)
        self._loop.call_soon_threadsafe(self._q.put_nowait, env)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ---- internals ----------------------------------------------------------
    def _thread(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._q = asyncio.Queue()
        self._ready.set()
        try:
            self._loop.run_until_complete(self._main())
        except RuntimeError:
            pass   # loop stopped by close()

    def _spool_file(self, service: str) -> str:
        d = os.path.join(self.spool_dir, service)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "watchdock_spool.jsonl")

    def _spool(self, env: dict) -> None:
        path = self._spool_file(env.get("service") or "_host")
        try:
            if os.path.getsize(path) > self.max_bytes:
                return            # full: keep the start of the gap, drop the newest
        except OSError:
            pass
        with open(path, "a") as f:
            f.write(_dumps(env) + "\n")
        self.spooled += 1

    async def _main(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=5, ping_interval=5,
                                              ping_timeout=5, max_size=None) as ws:
                    await ws.send(_dumps({"hello": HOST, "token": self.token}))
                    backoff = 1.0
                    await self._flush(ws)                  # backfill before live
                    self.connected = True
                    log(f"link up → {self.url}")
                    while True:
                        env = await self._q.get()
                        try:
                            await ws.send(_dumps(env))
                        except Exception:
                            self._spool(env)
                            raise
            except Exception as e:                         # noqa: BLE001 - any failure means "link down"
                if self.connected:
                    log(f"link down ({type(e).__name__}); spooling to {self.spool_dir}/<service>/watchdock_spool.jsonl")
                self.connected = False
                deadline = time.monotonic() + backoff      # spool whatever arrives while we wait
                while (left := deadline - time.monotonic()) > 0:
                    try:
                        self._spool(await asyncio.wait_for(self._q.get(), timeout=left))
                    except asyncio.TimeoutError:
                        break
                backoff = min(backoff * 2, 10.0)

    async def _flush(self, ws) -> None:
        pattern = os.path.join(self.spool_dir, "*", "watchdock_spool.jsonl")
        paths = sorted(set(glob.glob(pattern)) | {p[: -len(".flushing")] for p in glob.glob(pattern + ".flushing")})
        for path in paths:
            flushing = path + ".flushing"
            sent = 0
            if os.path.exists(flushing):                  # leftover from an interrupted flush
                sent += await self._send_file(ws, flushing)
            if os.path.exists(path):
                os.replace(path, flushing)
                sent += await self._send_file(ws, flushing)
            if sent:
                log(f"backfilled {sent} samples from {path}")

    async def _send_file(self, ws, path: str) -> int:
        total, batch = 0, []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    batch.append(json.loads(line))
                except ValueError:
                    continue                               # torn last line after a crash
                if len(batch) >= self.batch:
                    await self._send_batch(ws, batch); total += len(batch); batch = []
        if batch:
            await self._send_batch(ws, batch); total += len(batch)
        os.remove(path)                                    # only after every batch was acked
        return total

    async def _send_batch(self, ws, batch: list) -> None:
        await ws.send(_dumps({"backfill": batch}))
        want = batch[-1]["id"]
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if msg.get("ack") == want:
                return
