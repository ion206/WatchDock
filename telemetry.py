#!/usr/bin/env python3
"""WatchDock telemetry: Docker event listener + per-container stats streamer.

Emits one JSON envelope per message (see forwarder.envelope). Runs on the edge
device against the local socket, or on the Mac against a remote daemon:

    python telemetry.py                                      # JSON lines on stdout
    python telemetry.py --project web | jq .                 # only one compose project
    python telemetry.py --docker-host ssh://pi@10.0.0.42     # from the Mac, over SSH
    python telemetry.py --ws ws://MAC_IP:8766 --spool-dir /deployments

Topics
    /wd/<service>/events          start · die · oom · kill · restart · health_status · destroy
    /wd/<service>/replica/<n>     per-container stats, ~1 Hz (cpu_pct, mem_mb, mem_limit_mb, rx/tx, pids)
    /wd/<service>/summary         per-service rollup, 1 Hz (replicas, cpu_pct, mem_mb, mem_peak_mb)

Crash events carry data.trigger = true; the Mac core opens an incident on them.
Requires: pip install docker websockets
"""
from __future__ import annotations

import argparse, re, threading, time
from typing import Callable

import docker
from docker.errors import APIError, NotFound

from forwarder import ForwardSink, StdoutSink, envelope, log

INTERESTING = {"start", "die", "oom", "kill", "restart", "health_status", "destroy", "stop"}
CLEAN_EXIT = {0, 143}                      # normal exit, SIGTERM from stop/recreate


def connect(docker_host: str | None = None) -> docker.DockerClient:
    if docker_host and docker_host.startswith("ssh://"):
        # use_ssh_client: shells out to your `ssh`, so ~/.ssh/config (keys, ControlMaster) applies
        return docker.DockerClient(base_url=docker_host, use_ssh_client=True, timeout=30)
    return docker.DockerClient(base_url=docker_host, timeout=30) if docker_host else docker.from_env()


def identity(labels: dict, fallback_name: str) -> tuple[str, str]:
    svc = labels.get("com.docker.compose.service") or fallback_name.lstrip("/")
    return svc, labels.get("com.docker.compose.container-number", "1")


def _net(sample: dict) -> tuple[int, int]:
    nets = sample.get("networks") or {}
    return (sum(n.get("rx_bytes", 0) for n in nets.values()), sum(n.get("tx_bytes", 0) for n in nets.values()))


def summarize(s: dict, prev: tuple[dict, float] | None) -> dict | None:
    """One `docker stats` sample → the numbers we plot. Same math as the docker CLI."""
    cpu, pre = s.get("cpu_stats") or {}, s.get("precpu_stats") or {}
    total = (cpu.get("cpu_usage") or {}).get("total_usage")
    if total is None or not pre.get("system_cpu_usage"):
        return None                                    # first sample has no baseline
    cpu_d = total - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    sys_d = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    ncpu = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    cpu_pct = cpu_d / sys_d * ncpu * 100 if sys_d > 0 and cpu_d >= 0 else 0.0

    mem = s.get("memory_stats") or {}
    st = mem.get("stats") or {}
    cache = st.get("inactive_file", st.get("total_inactive_file", 0))   # cgroup v2 / v1
    used = max(mem.get("usage", 0) - cache, 0)
    limit = mem.get("limit", 0) or 0

    rx, tx = _net(s)
    now = time.monotonic()
    rx_kbps = tx_kbps = 0.0
    if prev:
        (prx, ptx), dt = _net(prev[0]), now - prev[1]
        if dt > 0:
            rx_kbps, tx_kbps = max(rx - prx, 0) / dt / 1024, max(tx - ptx, 0) / dt / 1024
    return {
        "cpu_pct": round(cpu_pct, 2), "online_cpus": ncpu,
        "mem_mb": round(used / 2**20, 1), "mem_limit_mb": round(limit / 2**20, 1),
        "mem_pct": round(used / limit * 100, 1) if limit else 0.0,
        "rx_kbps": round(rx_kbps, 1), "tx_kbps": round(tx_kbps, 1),
        "pids": (s.get("pids_stats") or {}).get("current", 0),
        "throttled_periods": (cpu.get("throttling_data") or {}).get("throttled_periods", 0),
    }


class Telemetry:
    def __init__(self, client: docker.DockerClient, sink, project: str | None = None,
                 services: list[str] | None = None, on_trigger: Callable[[dict], None] | None = None):
        self.c, self.sink, self.project = client, sink, project
        self.services = set(services or [])
        self.on_trigger = on_trigger
        self.streams: dict[str, threading.Thread] = {}
        self.latest: dict[str, tuple[str, float, dict]] = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def wanted(self, labels: dict) -> bool:
        if self.project and labels.get("com.docker.compose.project") != self.project:
            return False
        return not self.services or labels.get("com.docker.compose.service") in self.services

    # ---- lifecycle ----------------------------------------------------------
    def start(self) -> "Telemetry":
        for ctr in self.c.containers.list():
            self.watch(ctr.id)
        threading.Thread(target=self.events_loop, name="wd-events", daemon=True).start()
        threading.Thread(target=self.summary_loop, name="wd-summary", daemon=True).start()
        log(f"telemetry on {self.c.api.base_url} · {len(self.streams)} containers")
        return self

    def run_forever(self) -> None:
        self.start()
        try:
            while not self.stop.wait(1):
                pass
        except KeyboardInterrupt:
            self.stop.set()

    # ---- docker events ------------------------------------------------------
    def events_loop(self) -> None:
        since = int(time.time())
        while not self.stop.is_set():
            try:
                for ev in self.c.events(decode=True, since=since, filters={"type": "container"}):
                    since = int(ev.get("time", since))
                    self.on_event(ev)
            except Exception as e:                       # noqa: BLE001 - daemon or SSH hiccup
                log(f"event stream dropped ({type(e).__name__}); resuming from {since}")
                time.sleep(1)

    def on_event(self, ev: dict) -> None:
        action = ev.get("Action") or ev.get("status") or ""
        base = action.split(":")[0].strip()              # "health_status: unhealthy"
        if base not in INTERESTING:
            return
        actor = ev.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        cid = ev.get("id") or actor.get("ID", "")
        if not self.wanted(attrs):
            return
        svc, rep = identity(attrs, attrs.get("name", cid[:12]))
        data = {"action": base, "detail": action[len(base):].strip(" :") or None,
                "container": attrs.get("name"), "container_id": cid[:12], "replica": rep,
                "image": attrs.get("image"), "trigger": False}
        if base == "die":
            data["exit_code"] = int(attrs.get("exitCode", -1))
            try:
                data["oom_killed"] = bool(self.c.containers.get(cid).attrs["State"].get("OOMKilled"))
            except Exception:                            # noqa: BLE001 - already removed
                data["oom_killed"] = None
            data["trigger"] = bool(data["oom_killed"]) or data["exit_code"] not in CLEAN_EXIT
        elif base == "oom" or data["detail"] == "unhealthy":
            data["trigger"] = True
        env = envelope("event", svc, f"/wd/{svc}/events", data, int(ev.get("timeNano") or time.time_ns()))
        self.sink.emit(env)
        if data["trigger"] and self.on_trigger:
            self.on_trigger(env)
        if base == "start":
            self.watch(cid)

    # ---- docker stats -------------------------------------------------------
    def watch(self, cid: str) -> None:
        with self.lock:
            t = self.streams.get(cid)
            if t and t.is_alive():
                return
            t = threading.Thread(target=self.stats_loop, args=(cid,), name=f"wd-stats-{cid[:12]}", daemon=True)
            self.streams[cid] = t
        t.start()

    def stats_loop(self, cid: str) -> None:
        try:
            ctr = self.c.containers.get(cid)
            if not self.wanted(ctr.labels):
                return
            svc, rep = identity(ctr.labels, ctr.name)
            prev = None
            for s in ctr.stats(stream=True, decode=True):   # ~1 sample per second until the container stops
                if self.stop.is_set():
                    break
                sample = summarize(s, prev)
                prev = (s, time.monotonic())
                if sample is None:
                    continue
                sample.update(container=ctr.name, replica=rep)
                with self.lock:
                    self.latest[cid] = (svc, time.monotonic(), sample)
                self.sink.emit(envelope("stats", svc, f"/wd/{svc}/replica/{rep}", sample))
        except (NotFound, APIError):
            pass
        except Exception as e:                           # noqa: BLE001
            log(f"stats stream {cid[:12]} ended ({type(e).__name__})")
        finally:
            with self.lock:
                self.latest.pop(cid, None)
                self.streams.pop(cid, None)

    def summary_loop(self) -> None:
        while not self.stop.wait(1.0):
            now, by = time.monotonic(), {}
            with self.lock:
                for svc, t, smp in self.latest.values():
                    if now - t <= 3:
                        by.setdefault(svc, []).append(smp)
            for svc, ss in by.items():
                self.sink.emit(envelope("summary", svc, f"/wd/{svc}/summary", {
                    "replicas": len(ss),
                    "cpu_pct": round(sum(x["cpu_pct"] for x in ss), 1),
                    "mem_mb": round(sum(x["mem_mb"] for x in ss), 1),
                    "mem_peak_mb": max(x["mem_mb"] for x in ss),
                    "mem_limit_mb": max(x["mem_limit_mb"] for x in ss),
                }))


# ---- evidence bundle helper (used by the Mac core over SSH) -----------------
_SECRET = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*|\s+)(\S+)")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def collect_logs(client: docker.DockerClient, service: str, project: str | None = None, tail: int = 50) -> list[dict]:
    """Last `tail` log lines of every replica, secrets redacted. Never send .env to the model."""
    labels = [f"com.docker.compose.service={service}"] + ([f"com.docker.compose.project={project}"] if project else [])
    out = []
    for ctr in client.containers.list(all=True, filters={"label": labels}):
        st = ctr.attrs.get("State", {})
        out.append({"container": ctr.name, "status": ctr.status, "restart_count": ctr.attrs.get("RestartCount", 0),
                    "oom_killed": st.get("OOMKilled"), "exit_code": st.get("ExitCode"),
                    "logs_tail": redact(ctr.logs(tail=tail, timestamps=True).decode("utf-8", "replace"))})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docker-host", help="e.g. ssh://pi@10.0.0.42 (default: local socket / DOCKER_HOST)")
    ap.add_argument("--project", help="only this compose project")
    ap.add_argument("--service", action="append", help="only these compose services (repeatable)")
    ap.add_argument("--ws", help="forward to the Mac bridge, e.g. ws://192.168.1.20:8766")
    ap.add_argument("--spool-dir", default="/deployments")
    ap.add_argument("--token", default=None, help="shared secret (or env WATCHDOCK_TOKEN)")
    a = ap.parse_args()
    import os
    sink = ForwardSink(a.ws, a.spool_dir, a.token or os.environ.get("WATCHDOCK_TOKEN")) if a.ws else StdoutSink()
    Telemetry(connect(a.docker_host), sink, a.project, a.service).run_forever()


if __name__ == "__main__":
    main()
