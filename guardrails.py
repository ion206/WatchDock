#!/usr/bin/env python3
"""WatchDock guardrail validator (Mechanism A).

Checks a proposed deployment (what the agent just edited) against the guardrails in
the BASELINE watchdock.md (the pre-incident snapshot). The proposal can never move
its own fence: any edit to the front matter is itself a violation.

Whole-stack: the target service may change only replicas / memory / cpus, and only
through allowed_actions. Every other service and every top-level key (volumes,
networks, secrets, configs) must be byte-for-byte identical after normalization.

Manifest front matter, single service:
    service: web
    guardrails: {max_replicas: 3, max_memory: 256m, allowed_actions: [scale], ...}
or several services in one compose file:
    services:
      web:    {guardrails: {...}, probe: {...}}
      worker: {guardrails: {...}}

Compose files are compared after `docker compose config --format json` when the
docker CLI exists (resolves .env, units, mem_limit vs deploy.resources); otherwise
the YAML is compared as written.

    python guardrails.py --baseline OLD_DIR --proposed NEW_DIR --service web
Exit 0 = safe to apply, 1 = rejected (JSON on stdout either way).
"""
from __future__ import annotations

import argparse, json, os, re, shutil, subprocess, sys, tempfile, time

import yaml

FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
LIMIT_KEYS = {"deploy", "mem_limit", "cpus", "scale"}
ACTION_FOR = {"replicas": "scale", "memory": "set_memory", "cpus": "set_cpus"}


class Invalid(Exception):
    pass


def read_manifest(text: str) -> tuple[dict, str]:
    m = FRONT.match(text)
    if not m:
        raise Invalid("watchdock.md has no YAML front matter")
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


def service_policy(meta: dict, service: str) -> dict:
    """{guardrails, probe, stateful, verify} for one service, from either manifest layout."""
    if isinstance(meta.get("services"), dict):
        if service not in meta["services"]:
            raise Invalid(f"watchdock.md has no section for service {service!r}")
        sec = meta["services"][service] or {}
    elif meta.get("service") == service:
        sec = meta
    else:
        raise Invalid(f"watchdock.md describes {meta.get('service')!r}, not {service!r}")
    g = dict(sec.get("guardrails") or {})
    if sec.get("stateful"):
        g["max_replicas"] = 1                       # stateful pins replicas, whatever else it says
    return {"guardrails": g, "probe": sec.get("probe") or {}, "stateful": bool(sec.get("stateful")),
            "verify": sec.get("verify") or {}}


def managed_services(meta: dict) -> list[str]:
    if isinstance(meta.get("services"), dict):
        return list(meta["services"])
    return [meta["service"]] if meta.get("service") else []


def to_bytes(v) -> int | None:
    if v in (None, "", 0, "0"):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?)i?b?\s*", str(v).lower())
    if not m:
        raise Invalid(f"unreadable size {v!r}")
    return int(float(m.group(1)) * 1024 ** "_kmgt".index(m.group(2) or "_"))


def effective(compose_path: str, project_dir: str | None = None) -> dict:
    """project_dir: resolve relative paths and .env from the real deployment dir, so a baseline
    snapshot in /tmp normalizes exactly like the live file."""
    if shutil.which("docker"):
        cmd = ["docker", "compose", "-f", compose_path]
        if project_dir:
            cmd += ["--project-directory", project_dir]
        r = subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise Invalid(f"compose file does not parse: {r.stderr.strip()[:400]}")
        return json.loads(r.stdout)
    try:
        with open(compose_path) as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise Invalid(f"compose file does not parse: {str(e)[:300]}")


def view(cfg: dict, service: str) -> dict:
    s = (cfg.get("services") or {}).get(service)
    if s is None:
        raise Invalid(f"service {service!r} not in compose file")
    dep = s.get("deploy") or {}
    lim = (dep.get("resources") or {}).get("limits") or {}
    replicas = dep.get("replicas", s.get("scale", 1))
    cpus = lim.get("cpus") or s.get("cpus")
    dep_rest = {k: v for k, v in dep.items() if k not in ("replicas", "resources")}
    res_rest = {k: v for k, v in (dep.get("resources") or {}).items() if k != "limits"}
    lim_rest = {k: v for k, v in lim.items() if k not in ("memory", "cpus")}
    return {"replicas": int(replicas if replicas is not None else 1),
            "memory": to_bytes(lim.get("memory") or s.get("mem_limit")),
            "cpus": float(cpus) if cpus not in (None, "", 0) else None,
            "ports": s.get("ports") or [], "container_name": s.get("container_name"),
            "other": {**{k: v for k, v in s.items() if k not in LIMIT_KEYS},
                      "deploy(other)": [dep_rest, res_rest, lim_rest]}}


def fixed_host_ports(ports: list) -> list[str]:
    """Published host ports that can only bind once (no range). Scaling these collides."""
    out = []
    for p in ports:
        if isinstance(p, dict):
            pub = str(p.get("published") or "")
        else:
            parts = str(p).split(":")
            pub = parts[-2] if len(parts) >= 2 else ""
        if pub and "-" not in pub:
            out.append(pub)
    return out


def mb(b: int | None) -> str:
    return "unlimited" if b is None else f"{b / 2**20:.0f} MiB"


def validate(baseline_dir: str, proposed_dir: str, service: str, recent_heals: int = 0) -> dict:
    """Guardrails come from the BASELINE manifest. Both compose files resolve against proposed_dir."""
    v: list[str] = []
    base_md = open(os.path.join(baseline_dir, "watchdock.md")).read()
    meta, _ = read_manifest(base_md)
    pol = service_policy(meta, service)
    g = pol["guardrails"]
    prop_md_path = os.path.join(proposed_dir, "watchdock.md")
    if os.path.exists(prop_md_path):
        prop_front = FRONT.match(open(prop_md_path).read())
        if not prop_front or prop_front.group(1) != FRONT.match(base_md).group(1):
            v.append("watchdock.md front matter changed: guardrails are read-only to the agent")

    old_cfg = effective(os.path.join(baseline_dir, "docker-compose.yml"), proposed_dir)
    new_cfg = effective(os.path.join(proposed_dir, "docker-compose.yml"), proposed_dir)
    old, new = view(old_cfg, service), view(new_cfg, service)

    # whole stack: nothing outside the target service's limits may move
    for name in sorted(set(old_cfg.get("services") or {}) | set(new_cfg.get("services") or {})):
        if name != service and (old_cfg.get("services") or {}).get(name) != (new_cfg.get("services") or {}).get(name):
            v.append(f"service {name!r} changed: an incident on {service!r} may only edit {service!r}")
    for key in sorted((set(old_cfg) | set(new_cfg)) - {"services", "name"}):
        if old_cfg.get(key) != new_cfg.get(key):
            v.append(f"top-level {key!r} changed")

    changes = {k: [old[k], new[k]] for k in ("replicas", "memory", "cpus") if old[k] != new[k]}
    allowed = set(g.get("allowed_actions") or [])
    for k in changes:
        if ACTION_FOR[k] not in allowed:
            v.append(f"action '{ACTION_FOR[k]}' is not in allowed_actions {sorted(allowed)}")
    touched = sorted(k for k in set(old["other"]) | set(new["other"]) if old["other"].get(k) != new["other"].get(k))
    if touched:
        v.append(f"edited fields outside the guardrail envelope: {', '.join(touched)}")

    lo, hi = int(g.get("min_replicas", 1)), int(g.get("max_replicas", 1))
    if not lo <= new["replicas"] <= hi:
        v.append(f"replicas {new['replicas']} outside [{lo}, {hi}]" + (" (stateful service)" if pol["stateful"] else ""))
    cap = to_bytes(g.get("max_memory"))
    if cap and (new["memory"] is None or new["memory"] > cap):
        v.append(f"memory {mb(new['memory'])} per container exceeds max_memory {mb(cap)}")
    floor = to_bytes(g.get("min_memory"))
    if floor and new["memory"] is not None and new["memory"] < floor:
        v.append(f"memory {mb(new['memory'])} is below min_memory {mb(floor)}")
    total_cap = to_bytes(g.get("max_total_memory"))
    if total_cap and new["memory"] and new["memory"] * new["replicas"] > total_cap:
        v.append(f"replicas × memory = {mb(new['memory'] * new['replicas'])} exceeds max_total_memory {mb(total_cap)}")
    if g.get("max_cpus") and (new["cpus"] is None or new["cpus"] > float(g["max_cpus"])):
        v.append(f"cpus {new['cpus']} exceeds max_cpus {g['max_cpus']}")
    if g.get("max_total_cpus") and new["cpus"] and new["cpus"] * new["replicas"] > float(g["max_total_cpus"]):
        v.append(f"replicas × cpus = {new['cpus'] * new['replicas']:.2f} exceeds max_total_cpus {g['max_total_cpus']}")
    if new["replicas"] > 1 and new["container_name"]:
        v.append("container_name is set, so a second replica cannot be created")
    if new["replicas"] > 1 and (fixed := fixed_host_ports(new["ports"])):
        v.append(f"host port(s) {', '.join(fixed)} can bind only once; scale behind the proxy instead")
    if g.get("max_heals_per_hour") is not None and recent_heals >= int(g["max_heals_per_hour"]):
        v.append(f"heal budget spent: {recent_heals} heals in the last hour (max {g['max_heals_per_hour']})")
    return {"ok": not v, "service": service, "violations": v,
            "changes": {k: ([mb(a), mb(b)] if k == "memory" else [a, b]) for k, (a, b) in changes.items()},
            "raw_changes": changes}


def snapshot(deploy_dir: str) -> str:
    """Copy the files that define a deployment into a temp dir (the incident baseline)."""
    tmp = tempfile.mkdtemp(prefix="wd-baseline-")
    for name in ("docker-compose.yml", "watchdock.md", ".env"):
        if os.path.exists(os.path.join(deploy_dir, name)):
            shutil.copy2(os.path.join(deploy_dir, name), tmp)
    return tmp


def baseline_from_git(deploy_dir: str) -> str:
    """Materialize HEAD's compose + manifest (plus the untracked .env) into a temp dir."""
    deploy_dir = os.path.abspath(deploy_dir)
    top = subprocess.run(["git", "-C", deploy_dir, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True).stdout.strip()
    rel = os.path.relpath(deploy_dir, top)
    tmp = tempfile.mkdtemp(prefix="wd-baseline-")
    for name in ("docker-compose.yml", "watchdock.md"):
        blob = subprocess.run(["git", "-C", top, "show", f"HEAD:{rel}/{name}"], capture_output=True, text=True, check=True)
        open(os.path.join(tmp, name), "w").write(blob.stdout)
    if os.path.exists(os.path.join(deploy_dir, ".env")):
        shutil.copy(os.path.join(deploy_dir, ".env"), tmp)
    return tmp


def append_ledger(manifest_path: str, trigger: str, evidence: str, action: str, result: str, commit: str) -> None:
    """Code writes the ledger after verification; the model never edits watchdock.md."""
    clean = lambda s: str(s).replace("|", "/").replace("\n", " ")[:160]
    row = f"| {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} | {clean(trigger)} | {clean(evidence)} | {clean(action)} | {clean(result)} | {clean(commit)} |\n"
    text = open(manifest_path).read()
    with open(manifest_path, "a") as f:
        f.write(("" if text.endswith("\n") else "\n") + row)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deploy_dir", nargs="?", help="proposed deployment dir; baseline = git HEAD")
    ap.add_argument("--baseline")
    ap.add_argument("--proposed")
    ap.add_argument("--service", required=True)
    ap.add_argument("--recent-heals", type=int, default=0)
    a = ap.parse_args()
    proposed = a.proposed or a.deploy_dir
    baseline = a.baseline or baseline_from_git(a.deploy_dir)
    try:
        res = validate(baseline, proposed, a.service, a.recent_heals)
        res.pop("raw_changes", None)
    except Invalid as e:
        res = {"ok": False, "service": a.service, "violations": [str(e)], "changes": {}}
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
