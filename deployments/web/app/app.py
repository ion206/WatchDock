"""Demo web service: one CPU-bound endpoint, stdlib only (python:3.12-slim runs it on arm64 and amd64).
Each request burns WORK_MS of its own thread's CPU time, so a `cpus:` limit stretches it and
throughput scales with replicas, not with threads (the GIL).
"""
import hashlib, os, socket, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORK_MS = float(os.getenv("WORK_MS", "20"))
NAME = socket.gethostname()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        end, x = time.thread_time() + WORK_MS / 1000, b"watchdock"
        while time.thread_time() < end:
            x = hashlib.sha256(x).digest()
        body = f'{{"replica":"{NAME}","work_ms":{WORK_MS}}}'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
