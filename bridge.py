#!/usr/bin/env python3
"""WatchDock telemetry bridge (runs on the Mac).

Edge agents connect to ws://0.0.0.0:8766 and send JSON envelopes. The bridge
republishes each topic to Foxglove's WebSocket server at ws://127.0.0.1:8765
(Foxglove's default; open it with Open connection → Foxglove WebSocket) and
records everything to recordings/<time>.mcap.

Backfill: spooled samples arrive in {"backfill": [...]} batches. Each message is
logged with log_time = the time it was measured on the device, and carries
header.stamp. Set Plot x-axis to the header stamp so the gap fills in place.
The MCAP recording is always complete and ordered.

    pip install foxglove-sdk websockets
    WATCHDOCK_TOKEN=... python bridge.py

Embed it in the core:  bridge = Bridge(on_envelope=core.handle); bridge.publish(...)
"""
from __future__ import annotations

import argparse, asyncio, json, os, signal, time
from collections import OrderedDict
from typing import Callable

import foxglove
import websockets
from foxglove import Channel


def infer_schema(obj) -> dict:
    if isinstance(obj, bool):
        return {"type": "boolean"}
    if isinstance(obj, (int, float)):
        return {"type": "number"}
    if isinstance(obj, str):
        return {"type": "string"}
    if isinstance(obj, list):
        return {"type": "array", "items": infer_schema(obj[0]) if obj else {}}
    if isinstance(obj, dict):
        return {"type": "object", "properties": {k: infer_schema(v) for k, v in obj.items() if v is not None}}
    return {}


class Bridge:
    def __init__(self, token: str | None = None, on_envelope: Callable[[dict], None] | None = None,
                 events_path: str | None = "events.jsonl"):
        self.token, self.on_envelope = token, on_envelope
        self.channels: dict[str, Channel] = {}
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.events = open(events_path, "a", buffering=1) if events_path else None   # line-buffered
        self.stats = {"live": 0, "backfilled": 0, "dupes": 0}

    # ---- Foxglove side ------------------------------------------------------
    def _channel(self, topic: str, sample: dict) -> Channel:
        ch = self.channels.get(topic)
        if ch is None:
            ch = Channel(topic, schema=infer_schema(sample), message_encoding="json")
            self.channels[topic] = ch
        return ch

    def publish(self, topic: str, data: dict, ts_ns: int | None = None, **extra) -> None:
        """Log one message to Foxglove + MCAP. The core uses this for /wd/agent and /wd/<svc>/incident."""
        ts_ns = ts_ns or time.time_ns()
        msg = {"header": {"stamp": {"sec": ts_ns // 1_000_000_000, "nsec": ts_ns % 1_000_000_000}},
               **extra, **data}
        self._channel(topic, msg).log(msg, log_time=ts_ns)

    # ---- edge side ----------------------------------------------------------
    def ingest(self, env: dict, backfill: bool = False) -> None:
        eid = env.get("id")
        if eid in self.seen:
            self.stats["dupes"] += 1
            return
        self.seen[eid] = None
        if len(self.seen) > 200_000:
            self.seen.popitem(last=False)
        self.publish(env["topic"], env.get("data") or {}, env.get("ts_ns"),
                     host=env.get("host"), service=env.get("service"), kind=env.get("kind"), backfill=backfill)
        self.stats["backfilled" if backfill else "live"] += 1
        if self.events:
            self.events.write(json.dumps(env, separators=(",", ":")) + "\n")
        if self.on_envelope and not backfill:
            self.on_envelope(env)          # the core reacts to live data only; history is for graphs

    def _link(self, host: str, state: str, backfilled: int = 0) -> None:
        code = {"down": 0, "backfilling": 1, "live": 2}[state]
        self.publish(f"/wd/link/{host}", {"host": host, "state": state, "state_code": code, "backfilled": backfilled})
        print(f"[bridge] {host}: {state}" + (f" ({backfilled} samples)" if backfilled else ""), flush=True)

    async def handler(self, ws, *_):
        host = "?"
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if self.token and hello.get("token") != self.token:
                await ws.close(code=4401, reason="bad token")
                return
            host = hello.get("hello", "?")
            self._link(host, "live")
            async for raw in ws:
                msg = json.loads(raw)
                if "backfill" in msg:
                    batch = msg["backfill"]
                    self._link(host, "backfilling", len(batch))
                    for env in batch:
                        self.ingest(env, backfill=True)
                    await ws.send(json.dumps({"ack": batch[-1]["id"]}))
                    self._link(host, "live")
                else:
                    self.ingest(msg)
        except (websockets.ConnectionClosed, asyncio.TimeoutError, json.JSONDecodeError):
            pass
        finally:
            if host != "?":
                self._link(host, "down")
            if self.events:
                self.events.flush()


async def serve(a) -> None:
    os.makedirs(a.record_dir, exist_ok=True)
    path = os.path.join(a.record_dir, time.strftime("watchdock-%Y%m%d-%H%M%S.mcap"))
    with foxglove.open_mcap(path):
        foxglove.start_server(host=a.foxglove_host, port=a.foxglove_port)
        bridge = Bridge(token=os.environ.get("WATCHDOCK_TOKEN"))
        async with websockets.serve(bridge.handler, a.listen, a.port, max_size=None, ping_interval=5, ping_timeout=5):
            print(f"[bridge] edge ingest ws://{a.listen}:{a.port} → Foxglove ws://{a.foxglove_host}:{a.foxglove_port}"
                  f" · recording {path}", flush=True)
            stop = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):          # finalize the MCAP on Ctrl-C or kill
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            await stop.wait()
    print(f"[bridge] stopped · {bridge.stats} · {path}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--foxglove-host", default="127.0.0.1")
    ap.add_argument("--foxglove-port", type=int, default=8765)
    ap.add_argument("--record-dir", default="recordings")
    try:
        asyncio.run(serve(ap.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
