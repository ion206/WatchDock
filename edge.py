#!/usr/bin/env python3
"""WatchDock edge agent: telemetry + probers over one store-and-forward link.

    WATCHDOCK_TOKEN=... python edge.py --ws ws://192.168.1.20:8766 \
        --probe web=http://localhost:8080/ --threshold 800 --recover 500

Requires: pip install docker websockets
"""
import argparse, asyncio, os

from forwarder import ForwardSink, StdoutSink, log
from prober import Prober
from telemetry import Telemetry, connect


async def run(a) -> None:
    sink = ForwardSink(a.ws, a.spool_dir, os.environ.get("WATCHDOCK_TOKEN")) if a.ws else StdoutSink()
    Telemetry(connect(a.docker_host), sink, a.project).start()
    probers = []
    for spec in a.probe or []:
        svc, url = spec.split("=", 1)
        probers.append(Prober(url, svc, threshold_ms=a.threshold, recover_ms=a.recover, emit=sink.emit))
        log(f"probing {svc} at {url} every 2 s (trigger > {a.threshold:.0f} ms, re-arm < {a.recover:.0f} ms)")
    await asyncio.gather(*(p.run() for p in probers), asyncio.Event().wait())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", help="Mac bridge, e.g. ws://192.168.1.20:8766 (omit for stdout)")
    ap.add_argument("--spool-dir", default="/deployments")
    ap.add_argument("--docker-host")
    ap.add_argument("--project")
    ap.add_argument("--probe", action="append", help="service=url (repeatable)")
    ap.add_argument("--threshold", type=float, default=800)
    ap.add_argument("--recover", type=float, default=500)
    try:
        asyncio.run(run(ap.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
