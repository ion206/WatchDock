"""Deterministic safety tests: guardrails, toolbox, compose patcher. No model, no Docker.
    python tests/test_safety.py
"""
import asyncio, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import guardrails
from composepatch import set_resources
from toolbox import Toolbox

BASE = """services:
  proxy:
    image: traefik:v3.6
    ports: ["8080:8080"]
  web:
    build: ./app
    environment:
      - MODE=cpu  # keep this comment
    labels:
      - traefik.enable=true
    deploy:
      replicas: 1
      resources:
        limits:
          memory: 128m
          cpus: "0.5"
  db:
    image: postgres:16
    container_name: db
    ports: ["5432:5432"]
    deploy:
      resources:
        limits:
          memory: 256m
volumes:
  pgdata:
"""
MD = """---
project: t
services:
  web:
    guardrails: {min_replicas: 1, max_replicas: 3, max_memory: 256m, max_total_memory: 512m, max_cpus: 1.0, allowed_actions: [scale, set_cpus], max_heals_per_hour: 2}
  db:
    stateful: true
    guardrails: {max_replicas: 5, max_memory: 1g, allowed_actions: [scale, set_memory]}
---
# t
## Incident ledger
| when | trigger | evidence | action | result | commit |
|---|---|---|---|---|---|
"""
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1; print("FAIL", name, detail)


def deploy(compose=BASE, md=MD):
    d = tempfile.mkdtemp(); open(f"{d}/docker-compose.yml", "w").write(compose); open(f"{d}/watchdock.md", "w").write(md)
    open(f"{d}/.env", "w").write("SECRET=hunter2\n"); return d


def v(new_compose, svc="web", md_new=None, heals=0):
    base = deploy(); prop = deploy(new_compose, md_new or MD)
    try:
        return guardrails.validate(base, prop, svc, heals)
    except guardrails.Invalid as e:
        return {"ok": False, "violations": [str(e)], "changes": {}}


# ---- guardrails ----
r = v(BASE.replace("replicas: 1", "replicas: 3")); check("scale within limits", r["ok"], r)
r = v(BASE.replace("replicas: 1", "replicas: 4")); check("scale beyond max", not r["ok"] and any("outside" in x for x in r["violations"]), r)
r = v(BASE.replace("memory: 128m", "memory: 192m")); check("action not allowed (set_memory)", not r["ok"] and any("set_memory" in x for x in r["violations"]), r)
r = v(BASE.replace('cpus: "0.5"', 'cpus: "2.0"')); check("cpus cap", not r["ok"] and any("max_cpus" in x for x in r["violations"]), r)
r = v(BASE.replace("replicas: 1", "replicas: 3").replace("memory: 128m", "memory: 200m"), md_new=None)
check("total memory 3×200m > 512m (and set_memory)", not r["ok"] and any("max_total_memory" in x for x in r["violations"]), r)
r = v(BASE.replace('"8080:8080"', '"80:8080"')); check("other service edited", not r["ok"] and any("'proxy' changed" in x for x in r["violations"]), r)
r = v(BASE.replace("  pgdata:", "  pgdata:\n  evil:")); check("top-level volumes edited", not r["ok"] and any("top-level" in x for x in r["violations"]), r)
r = v(BASE.replace("MODE=cpu", "MODE=evil")); check("environment edited", not r["ok"] and any("environment" in x for x in r["violations"]), r)
r = v(BASE.replace("build: ./app", "image: evil:latest")); check("image swap", not r["ok"], r)
r = v(BASE, md_new=MD.replace("max_replicas: 3", "max_replicas: 30")); check("front matter edit", not r["ok"] and any("front matter" in x for x in r["violations"]), r)
r = v(BASE.replace("      replicas: 1", "      replicas: 1").replace("  db:\n    image: postgres:16", "  db:\n    image: postgres:16\n    deploy_extra: 1"), svc="web")
check("db touched during web incident", not r["ok"], r)
dbscale = BASE.replace("    container_name: db\n", "    container_name: db\n").replace("          memory: 256m", "          memory: 256m\n      replicas: 2")
r = v(dbscale, svc="db"); check("stateful pins replicas", not r["ok"] and any("stateful" in x for x in r["violations"]), r)
check("container_name blocks scale", any("container_name" in x for x in r["violations"]), r)
check("fixed host port blocks scale", any("5432" in x for x in r["violations"]), r)
r = v(BASE.replace("replicas: 1", "replicas: 2"), heals=2); check("heal budget", not r["ok"] and any("budget" in x for x in r["violations"]), r)
r = v("services: [unclosed"); check("unparseable compose rejected", not r["ok"], r)
r = v(BASE.replace("replicas: 1", "replicas: 2").replace("deploy:\n      replicas: 2", "deploy:\n      replicas: 2\n      placement: {constraints: [node.role==manager]}"))
check("deploy.placement edited", not r["ok"] and any("deploy(other)" in x for x in r["violations"]), r)

# ---- compose patcher ----
out = set_resources(BASE, "web", replicas=2, cpus=1)
diff = [l for l in out.splitlines() if l not in BASE.splitlines()]
check("patch changes only target lines", diff == ["      replicas: 2", '          cpus: "1.0"'], diff)
check("patch keeps comments", "# keep this comment" in out)
check("patch keeps other services", out.split("  db:")[1] == BASE.split("  db:")[1])


# ---- toolbox ----
class Inc:
    def __init__(self, d): self.dir, self.service, self.baseline, self.id = d, "web", guardrails.snapshot(d), "t"


class Ctl:
    applied = 0
    def recent_heals(self, s): return 0
    def on_tool(self, *a): pass
    async def status(self, inc): return {}
    async def apply(self, inc, summary):
        res = guardrails.validate(inc.baseline, inc.dir, inc.service, 0)
        if not res["ok"]: return {"applied": False, "refused": res["violations"]}
        Ctl.applied += 1; return {"applied": True, "changes": res["changes"]}


async def toolbox_tests():
    d = deploy(); os.makedirs(f"{d}/sub"); os.symlink("/etc", f"{d}/sub/escape")
    tb = Toolbox(Inc(d), Ctl())
    j = lambda s: json.loads(s)
    check(".env unreadable", "error" in j(await tb.call("read_file", {"path": ".env"})))
    check("../ traversal", "error" in j(await tb.call("read_file", {"path": "../../etc/passwd"})))
    check("absolute path", "error" in j(await tb.call("read_file", {"path": "/etc/hostname"})))
    check("symlink escape", "error" in j(await tb.call("read_file", {"path": "sub/escape/hostname"})))
    check("manifest readable", "content" in j(await tb.call("read_file", {"path": "watchdock.md"})))
    check("bad memory string", "error" in j(await tb.call("set_resources", {"memory": "lots"})))
    check("negative replicas", "error" in j(await tb.call("set_resources", {"replicas": -1})))
    r = j(await tb.call("set_resources", {"replicas": 9})); check("dry run flags violation", r["dry_run"]["ok"] is False, r)
    r = j(await tb.call("apply_fix", {"summary": "x"})); check("apply refuses violation", r.get("applied") is False, r)
    r = j(await tb.call("set_resources", {"replicas": 2})); check("dry run ok", r["dry_run"]["ok"], r)
    r = j(await tb.call("apply_fix", {"summary": "x"})); check("apply ok", r.get("applied") is True, r)
    check("apply_event set", tb.applied_event.is_set())
    check("second apply refused", "error" in j(await tb.call("apply_fix", {"summary": "again"})))
    check("edit after apply refused", "error" in j(await tb.call("set_resources", {"replicas": 3})))
    check("escalate after apply refused", "error" in j(await tb.call("escalate", {"reason": "x"})))
    check("unknown tool", "error" in j(await tb.call("rm_rf", {})))
    tb2 = Toolbox(Inc(deploy()), Ctl(), edit_mode="text")
    check("edit_compose needs exactly one match", "error" in j(await tb2.call("edit_compose", {"old_string": "memory", "new_string": "x"})))

asyncio.run(toolbox_tests())

# ---- envelope ids: unique across processes started in the same second on the same host ----
import subprocess
ids = set()
code = "import sys; sys.path.insert(0, %r); import forwarder; print(forwarder.envelope('probe','web','/t',{})['id'])" % os.path.dirname(HERE)
outs = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "WATCHDOCK_HOST": "pi-lab"}).stdout.strip() for _ in range(3)]
check("envelope ids unique across agents on one host", len(set(outs)) == 3, outs)
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
