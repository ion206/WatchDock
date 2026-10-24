#!/usr/bin/env python3
"""WatchDock synthetic prober (Mechanism B).

Every `interval` seconds, request the endpoint and measure Time To First Byte.
Breach rule: `breaches` of the last `window` probes over `threshold_ms` (a
timeout or 5xx counts as a breach). Hysteresis: it re-arms only when the whole
window is under `recover_ms`. Cooldown: at most one on_breach per `cooldown`
seconds, so new replicas get time to warm up.

    python prober.py http://localhost:8080/ --service web
    python prober.py tcp://localhost:5432 --service db --threshold 200

Callbacks may be sync or async; they never block the probe cadence.
No dependencies beyond the standard library (forwarder only when --ws is used).
"""
from __future__ import annotations

import argparse, asyncio, inspect, os, ssl, statistics, sys, time
from collections import deque
from typing import Callable
from urllib.parse import urlsplit


async def probe(url: str, timeout: float) -> dict:
    """One probe. ttfb_ms is measured from connect start to the first response byte."""
    u = urlsplit(url)
    tls = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if tls else 5432 if u.scheme == "tcp" else 80)
    t0 = time.perf_counter()
    writer = None
    try:
        kw = {"ssl": ssl.create_default_context(), "server_hostname": host} if tls else {}
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, **kw), timeout)
        t_conn = time.perf_counter()
        connect_ms = (t_conn - t0) * 1000
        if u.scheme == "tcp":
            return {"ok": True, "status": 0, "connect_ms": round(connect_ms, 1), "ttfb_ms": round(connect_ms, 1)}
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        writer.write((f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUser-Agent: watchdock-prober\r\n"
                      "Accept: */*\r\nConnection: close\r\n\r\n").encode())
        await writer.drain()
        first = await asyncio.wait_for(reader.read(1), max(timeout - (t_conn - t0), 0.001))
        ttfb_ms = (time.perf_counter() - t0) * 1000
        if not first:
            raise ConnectionResetError("closed before first byte")
        line = first + await asyncio.wait_for(reader.readline(), 1.0)
        parts = line.decode("latin-1", "replace").split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return {"ok": 0 < status < 500, "status": status, "connect_ms": round(connect_ms, 1), "ttfb_ms": round(ttfb_ms, 1)}
    except asyncio.TimeoutError:
        return {"ok": False, "status": 0, "error": "timeout", "connect_ms": None, "ttfb_ms": round(timeout * 1000, 1)}
    except OSError as e:
        return {"ok": False, "status": 0, "error": type(e).__name__, "connect_ms": None, "ttfb_ms": round(timeout * 1000, 1)}
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.5)
            except Exception:                         # noqa: BLE001
                pass


class Prober:
    def __init__(self, target: str, service: str, *, threshold_ms: float = 800, recover_ms: float = 500,
                 interval: float = 2.0, timeout: float | None = None, window: int = 5, breaches: int = 3,
                 cooldown: float = 60.0, on_breach: Callable | None = None, on_recover: Callable | None = None,
                 emit: Callable[[dict], None] | None = None):
        self.target, self.service = target, service
        self.threshold_ms, self.recover_ms = threshold_ms, recover_ms
        self.interval, self.timeout = interval, timeout or max(interval * 0.9, 0.2)
        self.window: deque = deque(maxlen=window)
        self.breaches, self.cooldown = breaches, cooldown
        self.on_breach, self.on_recover, self.emit = on_breach, on_recover, emit
        self.state = "healthy"
        self._cooldown_until = 0.0
        self._tasks: set = set()

    async def run(self) -> None:
        next_t = time.monotonic()
        while True:
            r = await probe(self.target, self.timeout)
            r["breach"] = (not r["ok"]) or r["ttfb_ms"] > self.threshold_ms
            self.window.append(r)
            self._evaluate()
            self._publish("probe", f"/wd/{self.service}/probe", {
                **r, "target": self.target, "threshold_ms": self.threshold_ms, "recover_ms": self.recover_ms,
                "state": self.state, "state_code": 0 if self.state == "degraded" else 1})
            next_t += self.interval
            delay = next_t - time.monotonic()
            if delay < 0:
                next_t = time.monotonic()               # fell behind: don't burst
            await asyncio.sleep(max(delay, 0))

    def _evaluate(self) -> None:
        n = sum(1 for x in self.window if x["breach"])
        now = time.monotonic()
        if self.state == "healthy" and n >= self.breaches:
            self.state = "degraded"
            if now >= self._cooldown_until:
                self._cooldown_until = now + self.cooldown
                self._fire(self.on_breach, self.report("degraded"))
            else:
                self._log(f"degraded again inside cooldown ({self._cooldown_until - now:.0f}s left); not re-triggering")
        elif (self.state == "degraded" and len(self.window) == self.window.maxlen
              and all(x["ok"] and x["ttfb_ms"] < self.recover_ms for x in self.window)):
            self.state = "healthy"
            self._fire(self.on_recover, self.report("recovered"))

    def report(self, state: str) -> dict:
        vals = [x["ttfb_ms"] for x in self.window]
        return {"service": self.service, "target": self.target, "state": state,
                "threshold_ms": self.threshold_ms, "recover_ms": self.recover_ms,
                "p50_ms": round(statistics.median(vals), 1), "max_ms": max(vals),
                "breaches": sum(1 for x in self.window if x["breach"]), "window": vals,
                "errors": [x.get("error") or x["status"] for x in self.window if not x["ok"]]}

    def _fire(self, cb: Callable | None, rep: dict) -> None:
        self._log(f"{rep['service']} {rep['state']}: p50 {rep['p50_ms']} ms, max {rep['max_ms']} ms")
        self._publish("trigger" if rep["state"] == "degraded" else "recovered",
                      f"/wd/{self.service}/trigger", {**rep, "trigger": rep["state"] == "degraded"})
        if cb is None:
            return
        coro = cb(rep) if inspect.iscoroutinefunction(cb) else asyncio.to_thread(cb, rep)
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish(self, kind: str, topic: str, data: dict) -> None:
        if self.emit is not None:
            from forwarder import envelope
            self.emit(envelope(kind, self.service, topic, data))

    @staticmethod
    def _log(msg: str) -> None:
        print(f"[prober {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="http(s)://host:port/path or tcp://host:port")
    ap.add_argument("--service", required=True)
    ap.add_argument("--threshold", type=float, default=800)
    ap.add_argument("--recover", type=float, default=500)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--breaches", type=int, default=3)
    ap.add_argument("--cooldown", type=float, default=60)
    ap.add_argument("--ws", help="forward to the Mac bridge instead of stdout")
    ap.add_argument("--spool-dir", default="/deployments")
    a = ap.parse_args()
    from forwarder import ForwardSink, StdoutSink
    sink = ForwardSink(a.ws, a.spool_dir, os.environ.get("WATCHDOCK_TOKEN")) if a.ws else StdoutSink()
    p = Prober(a.target, a.service, threshold_ms=a.threshold, recover_ms=a.recover, interval=a.interval,
               window=a.window, breaches=a.breaches, cooldown=a.cooldown, emit=sink.emit)
    try:
        asyncio.run(p.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
