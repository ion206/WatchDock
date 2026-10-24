"""How WatchDock applies a deployment. One exact command, never shell text from a model.

ComposeDeployer   production: `docker compose -f <dir>/docker-compose.yml --project-directory <dir> -p <project> up -d --no-build`
                  against DOCKER_HOST / --context (ssh://pi@10.0.0.42 works). Scale-out keeps existing replicas;
                  changed limits recreate only the affected service.
SimDeployer       lab: the same reconcile, executed by dockersim.
"""
from __future__ import annotations

import json, os, subprocess, time, urllib.request
import http.client, socket


class ComposeDeployer:
    def __init__(self, docker_host: str | None = None, context: str | None = None, timeout: float = 120):
        self.docker_host, self.context, self.timeout = docker_host, context, timeout

    def apply(self, deploy_dir: str, project: str) -> dict:
        cmd = ["docker"] + (["--context", self.context] if self.context else []) + [
            "compose", "-f", os.path.join(deploy_dir, "docker-compose.yml"), "--project-directory", deploy_dir,
            "-p", project, "up", "-d", "--no-build"]
        env = {**os.environ, **({"DOCKER_HOST": self.docker_host} if self.docker_host else {})}
        t0 = time.monotonic()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, env=env)
        return {"ok": r.returncode == 0, "seconds": round(time.monotonic() - t0, 2), "command": " ".join(cmd),
                "output": (r.stdout + r.stderr)[-1500:]}


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost"); self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sock.connect(self.path)


class SimDeployer:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _post(self, path, body):
        c = _UnixHTTP(self.socket_path); c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse(); raw = r.read() or b"{}"
        try:
            return json.loads(raw)
        except ValueError:
            return {"ok": False, "log": [raw.decode("utf-8", "replace")[:500]]}

    def apply(self, deploy_dir: str, project: str) -> dict:
        t0 = time.monotonic()
        res = self._post("/wdsim/up", {"dir": deploy_dir, "project": project})
        return {"ok": bool(res.get("ok")), "seconds": round(time.monotonic() - t0, 2),
                "command": f"docker compose -p {project} up -d --no-build", "output": "\n".join(res.get("log", []))}

    def down(self, project: str) -> dict:
        return self._post("/wdsim/down", {"project": project})
