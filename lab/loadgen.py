#!/usr/bin/env python3
"""Closed-loop HTTP load: N users, each sends a request, waits --think seconds after the reply, repeats (think 0 = `ab -c N`).
New connection per request, so a proxy balances every request. Writes one JSON line per request.
    python loadgen.py http://127.0.0.1:18101/ --concurrency 26 --duration 120 --out load.jsonl
"""
import argparse, asyncio, json, signal, time
from urllib.parse import urlsplit


async def one(host, port, path, timeout):
    t = time.perf_counter()
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()); await w.drain()
        data = await asyncio.wait_for(r.read(), timeout); w.close()
        ok = data.startswith(b"HTTP/1.") and data[9:10] in (b"2", b"3")
        return time.perf_counter() - t, ok
    except Exception:
        return time.perf_counter() - t, False


async def main(a):
    u = urlsplit(a.url); stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    out = open(a.out, "a", buffering=1)

    async def worker():
        while not stop.is_set():
            lat, ok = await one(u.hostname, u.port, u.path or "/", a.timeout)
            out.write(json.dumps({"t": time.time(), "ms": round(lat * 1000, 1), "ok": ok}) + "\n")
            await asyncio.sleep(a.think if ok else max(a.think, 0.05))
    tasks = [asyncio.create_task(worker()) for _ in range(a.concurrency)]
    try:
        await asyncio.wait_for(stop.wait(), a.duration)
    except asyncio.TimeoutError:
        pass
    stop.set(); await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("--concurrency", type=int, default=20); ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--think", type=float, default=0.0, help="seconds each user waits between requests (0 = like ab -c)"); ap.add_argument("--out", default="load.jsonl")
    asyncio.run(main(ap.parse_args()))
