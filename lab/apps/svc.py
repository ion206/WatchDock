"""WatchDock lab service. One stdlib program, behaviour chosen by MODE (runs the same under Docker or dockersim).

MODE=cpu        HTTP: each request burns WORK_MS of its own thread's CPU (a `cpus:` limit stretches it)
MODE=sleep      HTTP: each request waits SLEEP_MS for a slow downstream (CPU idle; more replicas don't help)
MODE=worker     no HTTP: every JOB_EVERY s loads a batch of JOB_MB into memory, processes it, frees it
MODE=badconfig  exits 1 at start unless REQUIRED_ENV is set (a deploy mistake, not a resource problem)
LOG_INJECT      optional text printed into the logs (prompt-injection test)
Listens on $PORT (8000 by default).
"""
import base64, hashlib, json, os, socket, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.getenv("APP_PROFILE"):                         # lab: knobs arrive as one opaque value
    os.environ.update({k: str(v) for k, v in json.loads(base64.b64decode(os.environ["APP_PROFILE"])).items()})
MODE = os.getenv("MODE", "cpu")
PORT = int(os.getenv("PORT", "8000"))
NAME = os.getenv("HOSTNAME", socket.gethostname())
log = lambda *a: print(time.strftime("%H:%M:%S"), NAME, *a, flush=True)

if os.getenv("LOG_INJECT"):
    log(os.getenv("LOG_INJECT"))

if MODE == "badconfig":
    need = os.getenv("REQUIRED_ENV", "DATABASE_URL")
    log("starting api v2.3.1")
    if not os.getenv(need):
        log(f"FATAL config: environment variable {need} is not set")
        print(f'Traceback (most recent call last):\n  File "/svc.py", line 31, in <module>\n    dsn = os.environ["{need}"]\nKeyError: \'{need}\'', file=sys.stderr, flush=True)
        sys.exit(1)

if MODE == "worker":
    # JOB_PLAN="0:60,20:180" = batch size by seconds since PLAN_EPOCH (the data grows over time)
    every, n = float(os.getenv("JOB_EVERY", "2")), 0
    plan = sorted((float(a), int(b)) for a, b in (x.split(":") for x in os.getenv("JOB_PLAN", "0:" + os.getenv("JOB_MB", "180")).split(",")))
    epoch = float(os.getenv("PLAN_EPOCH", time.time()))
    size = lambda: [mb for at, mb in plan if time.time() - epoch >= at][-1] if time.time() - epoch >= plan[0][0] else plan[0][1]
    log("worker up")
    while True:
        n += 1
        mb = size()
        log(f"job {n}: loading batch ({mb} MiB)")
        batch = b"\x01" * (mb * 2**20)            # touches every page, so RSS really grows
        time.sleep(0.6)
        log(f"job {n}: processed {len(batch) // 2**20} MiB ok")
        del batch
        time.sleep(every)

WORK, SLEEP = float(os.getenv("WORK_MS", "20")) / 1000, float(os.getenv("SLEEP_MS", "1200")) / 1000
LEAK, CACHE = int(os.getenv("LEAK_KB", "0")) * 1024, []        # MODE=leak: a per-request cache that is never evicted


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if MODE == "sleep":
            time.sleep(SLEEP)
        elif MODE == "leak":
            CACHE.append(b"\x01" * LEAK)
            if len(CACHE) % 100 == 0:
                log(f"response cache: {len(CACHE)} entries, {len(CACHE) * LEAK // 2**20} MiB")
        else:
            end, x = time.thread_time() + WORK, b"wd"
            while time.thread_time() < end:
                x = hashlib.sha256(x).digest()
        body = f'{{"replica":"{NAME}","mode":"{MODE}"}}'.encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_):
        pass


class S(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


log(f"listening on :{PORT}")
S(("0.0.0.0", PORT), H).serve_forever()
