#!/usr/bin/env python3
"""WatchDock: autonomous healing for Docker on edge devices.

    watchdock.py run   --deployments deployments/ --docker-host ssh://pi@10.0.0.42 --brain claude
    watchdock.py check deployments/web --service web          # guardrail dry-run: working tree vs git HEAD
    watchdock.py lab   --only s01,s02                          # scenario suite on dockersim (no Docker needed)

run = telemetry bridge (edge agents connect on :8766, Foxglove on :8765, MCAP recording)
      + controller (incidents, verification, rollback, ledger, git) + brain + `docker compose` over SSH.
Brains: claude (ANTHROPIC_API_KEY), droid (droid CLI logged in, or FACTORY_API_KEY), fallback (deterministic).
"""
from __future__ import annotations

import argparse, asyncio, json, os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def make_brain(a):
    from brains import ClaudeBrain, DroidBrain, FallbackBrain
    if a.brain == "claude":
        return ClaudeBrain(a.model or "claude-sonnet-5")
    if a.brain == "droid":
        return DroidBrain(a.model)
    return FallbackBrain()


async def run(a):
    import foxglove, websockets
    from bridge import Bridge
    from brains import FallbackBrain
    from controller import Controller
    from deploy import ComposeDeployer
    from telemetry import connect
    if a.context:
        resolved = subprocess.run(["docker", "context", "inspect", a.context, "--format", "{{.Endpoints.docker.Host}}"], capture_output=True, text=True, check=True).stdout.strip()
        if a.docker_host and a.docker_host != resolved:
            raise ValueError("--context and --docker-host select different daemons")
        a.docker_host = resolved
    dirs = sorted(os.path.join(a.deployments, d) for d in os.listdir(a.deployments)
                  if os.path.exists(os.path.join(a.deployments, d, "watchdock.md")))
    if not dirs:
        sys.exit(f"no deployments with a watchdock.md under {a.deployments}")
    os.makedirs("recordings", exist_ok=True)
    mcap = foxglove.open_mcap(time.strftime("recordings/watchdock-%Y%m%d-%H%M%S.mcap"))
    fh, fp = a.foxglove.rsplit(":", 1)
    foxglove.start_server(host=fh, port=int(fp))
    ctl_ref = {}
    bridge = Bridge(token=os.environ.get("WATCHDOCK_TOKEN"), on_envelope=lambda env: ctl_ref["c"].handle(env))
    ctl = Controller(dirs, ComposeDeployer(a.docker_host, a.context), connect(a.docker_host), make_brain(a),
                     fallback=None if a.no_fallback else FallbackBrain(), publish=bridge.publish, state_dir=".watchdock",
                     brain_timeout=a.brain_timeout)
    ctl_ref["c"] = ctl
    lh, lp = a.listen.rsplit(":", 1)
    async with websockets.serve(bridge.handler, lh, int(lp), max_size=None, ping_interval=5, ping_timeout=5):
        watched = ", ".join(f"{s} ({w.dep['project']})" for s, w in ctl.watch.items())
        print(f"WatchDock watching {watched}")
        print(f"  edge ingest ws://{a.listen} · Foxglove ws://{a.foxglove} · brain {a.brain} · docker {a.docker_host or a.context or 'local'}")
        stop = asyncio.Event()
        for s in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(s, stop.set)
        await stop.wait()
    mcap.close()


def check(a):
    import guardrails
    base = guardrails.baseline_from_git(a.dir)
    try:
        res = guardrails.validate(base, a.dir, a.service); res.pop("raw_changes", None)
    except guardrails.Invalid as e:
        res = {"ok": False, "violations": [str(e)], "changes": {}}
    print(json.dumps(res, indent=2)); sys.exit(0 if res["ok"] else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--deployments", default="deployments")
    r.add_argument("--docker-host", help="ssh://pi@10.0.0.42 (or set DOCKER_HOST)")
    r.add_argument("--context", help="docker context name, alternative to --docker-host")
    r.add_argument("--brain", choices=["claude", "droid", "fallback"], default="claude")
    r.add_argument("--model")
    r.add_argument("--brain-timeout", type=float, default=120)
    r.add_argument("--no-fallback", action="store_true")
    r.add_argument("--listen", default="0.0.0.0:8766")
    r.add_argument("--foxglove", default="127.0.0.1:8765")
    c = sub.add_parser("check"); c.add_argument("dir"); c.add_argument("--service", required=True)
    l = sub.add_parser("lab"); l.add_argument("--only", default=""); l.add_argument("--model", default="claude-sonnet-5")
    a = ap.parse_args()
    if a.cmd == "run":
        asyncio.run(run(a))
    elif a.cmd == "check":
        check(a)
    else:
        os.execv(sys.executable, [sys.executable, os.path.join(HERE, "lab", "run_lab.py"), "--only", a.only, "--model", a.model])


if __name__ == "__main__":
    main()
