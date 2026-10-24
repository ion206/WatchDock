#!/usr/bin/env python3
"""dockersim: a Docker-compatible runtime for testing WatchDock where Docker cannot run.

Serves the subset of the Docker Engine API that WatchDock uses (ping, version, info,
containers list/inspect/stats/logs, events) on a unix socket, so the real docker SDK,
telemetry.py and collect_logs() run unchanged against it. Containers are real OS
processes with real enforcement:

  memory  RSS sampled every 10 ms; over the limit → SIGKILL, exit 137, OOMKilled=true, oom + die events
  cpus    CFS-style quota: 100 ms period, SIGSTOP once the quota is used, SIGCONT at the period end
  restart always / unless-stopped / on-failure with Docker's doubling backoff (100 ms → 10 s, reset after 10 s up)
  compose `up` reconciles like docker compose: unchanged config keeps containers (scale-out is zero
          downtime), changed limits recreate them, extra replicas are removed from the top
  traefik a service whose image starts with "traefik" becomes a built-in L4 round-robin proxy over every
          running container labelled traefik.enable=true (what traefik's docker provider does)
  ports   other published ports get a TCP forwarder to the first running replica

Each container gets env PORT=<unique port>; apps listen on $PORT (8000 in real Docker).

    python dockersim.py --socket /tmp/wdsim/docker.sock --ncpu 4 --mem-mb 4096
    POST /wdsim/up {"dir": "...", "project": "web"}   ·   POST /wdsim/down {"project": "web"}
"""
from __future__ import annotations

import argparse, asyncio, hashlib, itertools, json, os, re, shlex, signal, socket, sys, threading, time
from collections import deque

import yaml
from aiohttp import web

PAGE = os.sysconf("SC_PAGE_SIZE")
TICK_MS = 1000 / os.sysconf("SC_CLK_TCK")
PERIOD_MS = 100


def now_ns() -> int:
    return time.time_ns()


def to_bytes(v) -> int:
    if v in (None, "", 0):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?)i?b?\s*", str(v).lower())
    return int(float(m.group(1)) * 1024 ** "_kmgt".index(m.group(2) or "_")) if m else 0


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Container:
    _ids = itertools.count(1)

    def __init__(self, sim, project, service, number, spec):
        self.sim, self.project, self.service, self.number, self.spec = sim, project, service, number, spec
        self.id = hashlib.sha256(f"{project}/{service}/{number}/{time.time_ns()}".encode()).hexdigest()
        self.name = f"{project}-{service}-{number}"
        self.labels = {**spec["labels"], "com.docker.compose.project": project, "com.docker.compose.service": service,
                       "com.docker.compose.container-number": str(number), "com.docker.compose.config-hash": spec["hash"]}
        self.port = free_port()
        self.proc = None
        self.status = "created"
        self.exit_code = 0
        self.oom_killed = False
        self.restart_count = 0
        self.started_at = self.finished_at = None
        self.logs: deque = deque(maxlen=5000)
        self.stopping = False
        self.removed = False
        self._backoff = 0.1
        self._restart_at = None
        self._ticks0 = 0                  # cpu ticks at period start
        self._period_end = 0.0
        self._throttled = False
        self.throttled_periods = 0
        self.cpu_ticks = 0
        self.rss = 0

    # ---- process lifecycle --------------------------------------------------
    def start(self):
        env = {**os.environ, **self.spec["env"], "PORT": str(self.port), "HOSTNAME": self.name, "PYTHONUNBUFFERED": "1"}
        self.proc = __import__("subprocess").Popen(self.spec["argv"], cwd=self.spec["cwd"], env=env, start_new_session=True,
                                                   stdout=-1, stderr=-1)
        self.status, self.started_at, self.oom_killed, self.exit_code = "running", time.time(), False, 0
        self.stopping = False
        self._ticks0, self._period_end, self._throttled = self._read_ticks(), time.monotonic() + PERIOD_MS / 1000, False
        for stream, pipe in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr)):
            threading.Thread(target=self._pump, args=(stream, pipe), daemon=True).start()
        self.sim.emit("start", self)

    def _pump(self, stream, pipe):
        for raw in iter(pipe.readline, b""):
            self.logs.append((time.time(), stream, raw.decode("utf-8", "replace").rstrip("\n")))

    def _signal(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass

    def stop(self, timeout=3.0):
        """docker stop: SIGTERM, then SIGKILL. Does not trigger the restart policy."""
        self.stopping = True
        self._restart_at = None
        if self.proc and self.proc.poll() is None:
            self._signal(signal.SIGCONT)
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout)
            except Exception:
                self._signal(signal.SIGKILL); self.proc.wait()
        self.sim.reap(self)

    def _read_ticks(self):
        try:
            f = open(f"/proc/{self.proc.pid}/stat").read().rsplit(")", 1)[1].split()
            return int(f[11]) + int(f[12])          # utime + stime (process total, all threads)
        except Exception:
            return self.cpu_ticks

    def _read_rss(self):
        try:
            return int(open(f"/proc/{self.proc.pid}/statm").read().split()[1]) * PAGE
        except Exception:
            return 0

    # ---- enforcement, called every 10 ms ------------------------------------
    def enforce(self, t):
        if self.status != "running" or not self.proc:
            return
        if self.proc.poll() is not None:
            self.sim.reap(self); return
        self.rss = self._read_rss()
        limit = self.spec["mem"]
        if limit and self.rss > limit:
            self.oom_killed = True
            self.logs.append((time.time(), "stderr", f"[dockersim] memory cgroup out of memory: Killed process {self.proc.pid} (rss {self.rss // 2**20} MiB > limit {limit // 2**20} MiB)"))
            self.sim.emit("oom", self)
            self._signal(signal.SIGCONT); self._signal(signal.SIGKILL)
            try:
                self.proc.wait(2)
            except Exception:
                pass
            self.sim.reap(self); return
        ticks = self._read_ticks()
        self.cpu_ticks = ticks
        cpus = self.spec["cpus"]
        if cpus:
            quota_ms = cpus * PERIOD_MS
            if t >= self._period_end:
                if self._throttled:
                    self._signal(signal.SIGCONT); self._throttled = False
                self._period_end = t + PERIOD_MS / 1000
                self._ticks0 = ticks
            elif not self._throttled and (ticks - self._ticks0) * TICK_MS >= quota_ms:
                self._signal(signal.SIGSTOP); self._throttled = True; self.throttled_periods += 1

    # ---- docker API views ---------------------------------------------------
    def state(self):
        return {"Status": self.status, "Running": self.status == "running", "Paused": False,
                "Restarting": self.status == "restarting", "OOMKilled": self.oom_killed, "Dead": False,
                "Pid": self.proc.pid if self.proc and self.status == "running" else 0, "ExitCode": self.exit_code,
                "StartedAt": _iso(self.started_at), "FinishedAt": _iso(self.finished_at)}

    def summary(self):
        return {"Id": self.id, "Names": ["/" + self.name], "Image": self.spec["image"], "ImageID": "sha256:" + self.spec["hash"],
                "Command": " ".join(self.spec["argv"]), "Created": int(self.started_at or time.time()), "Labels": self.labels,
                "State": self.status, "Status": ("Up" if self.status == "running" else f"Exited ({self.exit_code})"),
                "Ports": [], "HostConfig": {"NetworkMode": "host"}, "NetworkSettings": {"Networks": {}}, "Mounts": []}

    def inspect(self):
        return {"Id": self.id, "Name": "/" + self.name, "Image": "sha256:" + self.spec["hash"], "RestartCount": self.restart_count,
                "State": self.state(), "Created": _iso(self.started_at),
                "Config": {"Labels": self.labels, "Image": self.spec["image"], "Tty": False, "Env": [f"PORT={self.port}"],
                           "Cmd": self.spec["argv"], "Hostname": self.name},
                "HostConfig": {"Memory": self.spec["mem"], "NanoCpus": int((self.spec["cpus"] or 0) * 1e9),
                               "RestartPolicy": {"Name": self.spec["restart"]}, "NetworkMode": "host"},
                "NetworkSettings": {"Ports": {}, "Networks": {}}, "Mounts": []}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1e9):09d}Z" if t else "0001-01-01T00:00:00Z"


class Sim:
    def __init__(self, ncpu=4, mem_mb=4096):
        self.ncpu, self.mem_total = ncpu, mem_mb * 2**20
        self.containers: dict[str, Container] = {}
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.lock = threading.RLock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.proxies: dict[tuple, asyncio.AbstractServer] = {}
        self.rr = itertools.count()

    # ---- events -------------------------------------------------------------
    def emit(self, action, c: Container, extra=None):
        t = now_ns()
        attrs = {**c.labels, "name": c.name, "image": c.spec["image"], **(extra or {})}
        ev = {"status": action, "id": c.id, "from": c.spec["image"], "Type": "container", "Action": action,
              "Actor": {"ID": c.id, "Attributes": attrs}, "scope": "local", "time": t // 1_000_000_000, "timeNano": t}
        with self.lock:
            self.events.append(ev)
            del self.events[:-20000]
        if self.loop:
            for q in list(self.subscribers):
                self.loop.call_soon_threadsafe(q.put_nowait, ev)

    def reap(self, c: Container):
        """Process ended (exit, kill or OOM): die event, then the restart policy."""
        with self.lock:
            if c.status not in ("running",):
                return
            code = c.proc.poll()
            if code is None:
                try:
                    code = c.proc.wait(1)
                except Exception:
                    code = -9
            c.exit_code = 137 if c.oom_killed or code == -9 else (143 if code == -15 else (code if code >= 0 else 128 - code))
            c.status, c.finished_at = "exited", time.time()
        self.emit("die", c, {"exitCode": str(c.exit_code)})
        pol = c.spec["restart"]
        if c.stopping or c.removed:
            return
        if pol in ("always", "unless-stopped") or (pol == "on-failure" and c.exit_code != 0):
            if c.started_at and time.time() - c.started_at > 10:
                c._backoff = 0.1
            c.status = "restarting"
            c._restart_at = time.monotonic() + c._backoff
            c._backoff = min(c._backoff * 2, 10.0)

    def tick_loop(self):
        while True:
            t = time.monotonic()
            for c in list(self.containers.values()):
                try:
                    if c.status == "running":
                        c.enforce(t)
                    elif c.status == "restarting" and c._restart_at and t >= c._restart_at and not c.removed:
                        c._restart_at = None
                        c.restart_count += 1
                        c.start()
                except Exception as e:
                    print("[dockersim] tick error", c.name, e, file=sys.stderr)
            time.sleep(0.01)

    # ---- compose ------------------------------------------------------------
    def parse(self, d: str, project: str) -> dict:
        envfile = {}
        if os.path.exists(os.path.join(d, ".env")):
            for line in open(os.path.join(d, ".env")):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    envfile[k] = v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v
        cfg = yaml.safe_load(open(os.path.join(d, "docker-compose.yml")).read()) or {}
        sub = lambda t: re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: envfile.get(m.group(1), os.environ.get(m.group(1), m.group(2) or "")), t)

        def interp(v):                       # like compose: parse YAML first, then interpolate values
            if isinstance(v, str):
                return sub(v)
            if isinstance(v, list):
                return [interp(x) for x in v]
            if isinstance(v, dict):
                return {k: interp(x) for k, x in v.items()}
            return v
        cfg = interp(cfg)
        out = {}
        for name, s in (cfg.get("services") or {}).items():
            dep = s.get("deploy") or {}
            lim = (dep.get("resources") or {}).get("limits") or {}
            env = s.get("environment") or {}
            if isinstance(env, list):
                if not all(isinstance(e, str) for e in env):
                    raise ValueError(f"service {name}: environment list items must be KEY=VALUE strings")
                env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
            labels = s.get("labels") or {}
            if isinstance(labels, list):
                labels = dict(l.split("=", 1) if "=" in l else (l, "") for l in labels)
            image = s.get("image") or f"{project}-{name}"
            argv, cwd = self._argv(d, s, image)
            spec = {"image": image, "argv": argv, "cwd": cwd, "env": {k: str(v) for k, v in env.items()},
                    "labels": {k: str(v) for k, v in labels.items()},
                    "mem": to_bytes(lim.get("memory") or s.get("mem_limit")), "cpus": float(lim.get("cpus") or s.get("cpus") or 0),
                    "restart": s.get("restart", "no"), "ports": s.get("ports") or [],
                    "replicas": int(dep.get("replicas", s.get("scale", 1)) or 1), "proxy": str(image).startswith("traefik")}
            spec["hash"] = hashlib.sha256(json.dumps({k: spec[k] for k in ("image", "argv", "env", "labels", "mem", "cpus", "restart")}, sort_keys=True).encode()).hexdigest()[:16]
            out[name] = spec
        return out

    def _argv(self, d, s, image):
        cmd = s.get("command")
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        cwd, copies = d, {}
        b = s.get("build")
        if b:
            ctx = os.path.join(d, b if isinstance(b, str) else b.get("context", "."))
            cwd = ctx
            df = os.path.join(ctx, "Dockerfile")
            if os.path.exists(df):
                for line in open(df):
                    m = re.match(r"\s*COPY\s+(\S+)\s+(\S+)", line)
                    if m:
                        copies[m.group(2)] = os.path.join(ctx, m.group(1))
                    m = re.match(r"\s*CMD\s+(\[.*\])", line)
                    if m and not cmd:
                        cmd = json.loads(m.group(1))
        if not cmd:
            cmd = ["sleep", "infinity"]
        argv = [sys.executable if a in ("python", "python3") else copies.get(a, a) for a in cmd]
        return argv, cwd

    def up(self, d: str, project: str) -> dict:
        specs = self.parse(d, project)
        log = []
        with self.lock:
            mine = [c for c in self.containers.values() if c.project == project and not c.removed]
        for name, spec in specs.items():
            cur = sorted((c for c in mine if c.service == name), key=lambda c: c.number)
            if spec["proxy"]:
                self._ensure_proxy(project, name, spec); log.append(f"proxy {name} ready"); continue
            keep = []
            for c in cur:
                if c.spec["hash"] != spec["hash"] or c.number > spec["replicas"]:
                    self._remove(c); log.append(f"{'recreate' if c.number <= spec['replicas'] else 'remove'} {c.name}")
                else:
                    c.spec = {**c.spec, "replicas": spec["replicas"]}
                    keep.append(c.number)
            for n in range(1, spec["replicas"] + 1):
                if n not in keep:
                    c = Container(self, project, name, n, spec)
                    with self.lock:
                        self.containers[c.id] = c
                    self.emit("create", c); c.start(); log.append(f"start {c.name}")
            if spec["ports"]:
                self._ensure_forwarders(project, name, spec)
        for c in mine:
            if c.service not in specs:
                self._remove(c); log.append(f"remove orphan {c.name}")
        return {"ok": True, "log": log}

    def down(self, project: str):
        with self.lock:
            mine = [c for c in self.containers.values() if c.project == project and not c.removed]
        for c in mine:
            self._remove(c)
        for key in [k for k in self.proxies if k[0] == project]:
            self.proxies.pop(key).close()
        return {"ok": True}

    def _remove(self, c: Container):
        c.removed = True
        c.stop()
        self.emit("destroy", c)
        with self.lock:
            self.containers.pop(c.id, None)

    def backends(self, project):
        with self.lock:
            return [c for c in self.containers.values() if c.project == project and c.status == "running"
                    and c.labels.get("traefik.enable") == "true"]

    def _ensure_proxy(self, project, name, spec):
        for p in spec["ports"]:
            host = int(str(p).split(":")[0])
            if (project, host) not in self.proxies:
                fut = asyncio.run_coroutine_threadsafe(asyncio.start_server(
                    lambda r, w: self._pipe(r, w, lambda: self.backends(project)), "127.0.0.1", host), self.loop)
                self.proxies[(project, host)] = fut.result()

    def _ensure_forwarders(self, project, name, spec):
        for p in spec["ports"]:
            parts = str(p).split(":")
            if len(parts) < 2:
                continue
            host = int(parts[0])
            if (project, host) not in self.proxies:
                pick = lambda: [c for c in self.containers.values() if c.project == project and c.service == name and c.status == "running"][:1]
                fut = asyncio.run_coroutine_threadsafe(asyncio.start_server(lambda r, w: self._pipe(r, w, pick), "127.0.0.1", host), self.loop)
                self.proxies[(project, host)] = fut.result()

    async def _pipe(self, reader, writer, choose):
        targets = choose()
        if not targets:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\nConnection: close\r\n\r\nBad Gateway"); await writer.drain(); writer.close(); return
        c = targets[next(self.rr) % len(targets)]
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", c.port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\nConnection: close\r\n\r\nBad Gateway"); await writer.drain(); writer.close(); return

        async def copy(a, b):
            try:
                while (chunk := await a.read(65536)):
                    b.write(chunk); await b.drain()
            except Exception:
                pass
            finally:
                try:
                    b.close()
                except Exception:
                    pass
        await asyncio.gather(copy(reader, uw), copy(ur, writer))

    # ---- docker API ---------------------------------------------------------
    def find(self, ref):
        with self.lock:
            for c in self.containers.values():
                if c.id.startswith(ref) or c.name == ref.lstrip("/"):
                    return c
        return None

    def stats_sample(self, c: Container):
        t = time.monotonic_ns()
        return {"read": _iso(time.time()), "id": c.id, "name": "/" + c.name,
                "cpu_stats": {"cpu_usage": {"total_usage": int(c.cpu_ticks * TICK_MS * 1e6)}, "system_cpu_usage": t * self.ncpu,
                              "online_cpus": self.ncpu, "throttling_data": {"throttled_periods": c.throttled_periods}},
                "memory_stats": {"usage": c.rss, "limit": c.spec["mem"] or self.mem_total, "stats": {"inactive_file": 0}},
                "pids_stats": {"current": 1 if c.status == "running" else 0}, "networks": {}}


def build_app(sim: Sim) -> web.Application:
    app = web.Application()
    R = r"(?:/v[\d.]+)?"

    async def ping(_):
        return web.Response(text="OK", headers={"Api-Version": "1.41"})

    async def version(_):
        return web.json_response({"ApiVersion": "1.41", "MinAPIVersion": "1.12", "Version": "24.0.0-dockersim", "Os": "linux", "Arch": "amd64"})

    async def info(_):
        return web.json_response({"NCPU": sim.ncpu, "MemTotal": sim.mem_total, "Name": "dockersim", "ServerVersion": "24.0.0-dockersim",
                                  "Containers": len(sim.containers), "OperatingSystem": "dockersim", "CgroupVersion": "2"})

    def label_ok(c, filters):
        for lf in filters.get("label", []):
            k, _, v = lf.partition("=")
            if k not in c.labels or (v and c.labels[k] != v):
                return False
        st = filters.get("status")
        return not st or c.status in st

    async def clist(req):
        f = json.loads(req.query.get("filters", "{}") or "{}")
        f = {k: (list(v) if isinstance(v, list) else list(v.keys())) for k, v in f.items()}
        show_all = req.query.get("all") in ("1", "true", "True")
        with sim.lock:
            cs = [c for c in sim.containers.values() if (show_all or c.status == "running") and label_ok(c, f)]
        return web.json_response([c.summary() for c in cs])

    async def cinspect(req):
        c = sim.find(req.match_info["id"])
        return web.json_response(c.inspect()) if c else web.json_response({"message": "No such container"}, status=404)

    async def cstats(req):
        c = sim.find(req.match_info["id"])
        if not c:
            return web.json_response({"message": "No such container"}, status=404)
        stream = req.query.get("stream", "1") in ("1", "true", "True")
        prev = sim.stats_sample(c)
        if not stream:
            await asyncio.sleep(1); s = sim.stats_sample(c); s["precpu_stats"] = prev["cpu_stats"]; return web.json_response(s)
        resp = web.StreamResponse(headers={"Content-Type": "application/json"}); await resp.prepare(req)
        first = {**prev, "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0}}
        await resp.write((json.dumps(first) + "\n").encode())
        try:
            while c.status in ("running", "restarting") and not c.removed:
                await asyncio.sleep(1)
                s = sim.stats_sample(c); s["precpu_stats"] = prev["cpu_stats"]; prev = s
                await resp.write((json.dumps(s) + "\n").encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp

    async def clogs(req):
        c = sim.find(req.match_info["id"])
        if not c:
            return web.json_response({"message": "No such container"}, status=404)
        tail = req.query.get("tail", "all")
        lines = list(c.logs)
        if tail != "all":
            lines = lines[-int(tail):] if int(tail) > 0 else []
        ts = req.query.get("timestamps") in ("1", "true", "True")
        body = bytearray()
        for t, stream, line in lines:
            if (stream == "stdout" and req.query.get("stdout", "1") in ("0", "false")) or (stream == "stderr" and req.query.get("stderr", "1") in ("0", "false")):
                continue
            payload = ((_iso(t) + " ") if ts else "") + line + "\n"
            data = payload.encode()
            body += bytes([1 if stream == "stdout" else 2, 0, 0, 0]) + len(data).to_bytes(4, "big") + data
        return web.Response(body=bytes(body), content_type="application/vnd.docker.raw-stream")

    async def events(req):
        f = json.loads(req.query.get("filters", "{}") or "{}")
        types = f.get("type") or []
        types = list(types) if isinstance(types, list) else list(types.keys())
        since = float(req.query.get("since", "0") or 0)
        until = req.query.get("until")
        q: asyncio.Queue = asyncio.Queue()
        with sim.lock:
            backlog = [e for e in sim.events if e["time"] >= since] if since else []
            sim.subscribers.add(q)
        resp = web.StreamResponse(headers={"Content-Type": "application/json"}); await resp.prepare(req)
        try:
            for e in backlog:
                if not types or e["Type"] in types:
                    await resp.write((json.dumps(e) + "\n").encode())
            while True:
                if until and time.time() >= float(until):
                    break
                try:
                    e = await asyncio.wait_for(q.get(), 1.0)
                except asyncio.TimeoutError:
                    continue
                if not types or e["Type"] in types:
                    await resp.write((json.dumps(e) + "\n").encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            sim.subscribers.discard(q)
        return resp

    async def up(req):
        body = await req.json()
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, sim.up, body["dir"], body["project"])
        except Exception as e:
            return web.json_response({"ok": False, "log": [f"compose up failed: {type(e).__name__}: {e}"]}, status=500)
        return web.json_response(res)

    async def down(req):
        body = await req.json()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, sim.down, body["project"]))

    async def state(_):
        with sim.lock:
            return web.json_response([{"name": c.name, "service": c.service, "project": c.project, "status": c.status,
                                       "mem_limit": c.spec["mem"], "cpus": c.spec["cpus"], "rss": c.rss, "restarts": c.restart_count,
                                       "throttled": c.throttled_periods, "port": c.port} for c in sim.containers.values()])

    app.router.add_route("*", "/{tail:.*}", lambda req: dispatch(req))
    routes = [("GET", rf"^{R}/_ping$", ping), ("HEAD", rf"^{R}/_ping$", ping), ("GET", rf"^{R}/version$", version), ("GET", rf"^{R}/info$", info),
              ("GET", rf"^{R}/containers/json$", clist), ("GET", rf"^{R}/containers/(?P<id>[^/]+)/json$", cinspect),
              ("GET", rf"^{R}/containers/(?P<id>[^/]+)/stats$", cstats), ("GET", rf"^{R}/containers/(?P<id>[^/]+)/logs$", clogs),
              ("GET", rf"^{R}/events$", events), ("POST", r"^/wdsim/up$", up), ("POST", r"^/wdsim/down$", down), ("GET", r"^/wdsim/state$", state)]

    async def dispatch(req):
        for method, pat, fn in routes:
            m = re.match(pat, req.path)
            if m and req.method == method:
                req.match_info.update(m.groupdict()) if hasattr(req.match_info, "update") else None
                req._wd = m.groupdict()
                return await fn(_Req(req, m.groupdict()))
        return web.json_response({"message": f"dockersim: {req.method} {req.path} not implemented"}, status=404)
    return app


class _Req:
    """aiohttp request with match_info replaced by our regex groups."""
    def __init__(self, req, groups):
        self._r, self.match_info = req, groups

    def __getattr__(self, k):
        return getattr(self._r, k)


async def serve(a):
    sim = Sim(a.ncpu, a.mem_mb)
    sim.loop = asyncio.get_running_loop()
    threading.Thread(target=sim.tick_loop, daemon=True, name="dockersim-tick").start()
    os.makedirs(os.path.dirname(a.socket), exist_ok=True)
    if os.path.exists(a.socket):
        os.remove(a.socket)
    runner = web.AppRunner(build_app(sim)); await runner.setup()
    await web.UnixSite(runner, a.socket).start()
    print(f"[dockersim] Docker API on unix://{a.socket} · NCPU {a.ncpu} · MemTotal {a.mem_mb} MiB", flush=True)
    stop = asyncio.Event()
    for s in (signal.SIGINT, signal.SIGTERM):
        sim.loop.add_signal_handler(s, stop.set)
    await stop.wait()
    for c in list(sim.containers.values()):
        c.removed = True; c.stop(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default="/tmp/wdsim/docker.sock")
    ap.add_argument("--ncpu", type=int, default=4)
    ap.add_argument("--mem-mb", type=int, default=4096)
    asyncio.run(serve(ap.parse_args()))
