# WatchDock

Autonomous healing for Docker on edge devices. WatchDock watches containers on a Raspberry Pi or VM from
your Mac, notices crashes and slowdowns, asks a model what to change, and changes the compose file only
inside limits the model cannot move. Every fix is verified; a fix that does not hold is rolled back.

```
edge device                                  Mac (control plane)
───────────                                  ───────────────────
edge.py  ── telemetry.py (docker events+stats)   bridge.py ──▶ Foxglove (ws://127.0.0.1:8765) + MCAP
        ├─ prober.py (TTFB every 2 s)       WS        │
        └─ forwarder.py (spool when offline) ─────────▶ controller.py ──▶ brain (Claude | Droid | playbook)
                                                      │                     │ tools only (toolbox.py), no shell
Docker Engine ◀──────── docker compose over SSH ◀──── deploy.py ◀── guardrails.py (whole-stack, from watchdock.md)
```

## Files
| file | role |
|---|---|
| `watchdock.py` | CLI: `run` (bridge + controller), `check` (guardrail dry-run vs git HEAD), `lab` |
| `controller.py` | incident state machine: triggers, evidence bundle, brain, verification, rollback, ledger, git, budgets, locks |
| `brains.py` | `ClaudeBrain`, `DroidBrain` (hardened: no Execute, MCP tools, gated edits), `FallbackBrain`, `ScriptedBrain` |
| `toolbox.py` | the only actions a brain has: get_status, read_file, set_resources / edit_compose, validate, apply_fix, escalate |
| `guardrails.py` | Mechanism A: whole-stack validation against the pre-incident watchdock.md |
| `composepatch.py` | surgical compose edits (only the changed values move; comments and layout stay) |
| `deploy.py` | `ComposeDeployer` (one exact `docker compose up -d --no-build`), `SimDeployer` (lab) |
| `edge.py`, `telemetry.py`, `prober.py`, `forwarder.py` | edge agent: Docker events/stats, synthetic prober (B), store-and-forward (C) |
| `bridge.py` | edge ingest → Foxglove WebSocket + MCAP recording |
| `lab/dockersim.py` | Docker-Engine-API-compatible runtime with real processes, memory/CPU limits, OOM, restarts, proxy |
| `lab/run_lab.py`, `lab/loadgen.py`, `lab/apps/svc.py` | scenario suite, load generator, test services |
| `tests/test_safety.py` | 38 safety guardrail / toolbox / patcher tests |

## Run it
Mac:
```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # or: --brain droid (droid CLI logged in) / --brain fallback
python watchdock.py run --deployments deployments --docker-host ssh://pi@10.0.0.42 --brain claude
```
Device:
```
pip install docker websockets
python edge.py --ws ws://MAC_IP:8766 --probe web=http://localhost:8080/ --threshold 800 --recover 500
```
Foxglove: Open connection → Foxglove WebSocket → `ws://localhost:8765`.

## Test it (no Docker needed)
```
python tests/test_safety.py                 # 38 checks, < 5 s
python watchdock.py lab --only s01,s02      # real model; needs ANTHROPIC_API_KEY (or edit lab to use your router)
```

## Manifest (watchdock.md front matter)
```yaml
project: web
services:
  web:
    guardrails: {min_replicas: 1, max_replicas: 3, max_memory: 256m, max_total_memory: 768m, max_cpus: 1.0,
                 allowed_actions: [scale], max_heals_per_hour: 6, cooldown_s: 60}
    probe: {threshold_ms: 800, recover_ms: 500}
    verify: {consecutive: 5, timeout_s: 60}       # latency; crashes use {window_s: 30}
  db:
    stateful: true                                 # pins max_replicas to 1
    guardrails: {max_memory: 1g, allowed_actions: [set_memory]}
```

## Validation and limits

The featured lab run (20261023-215950) passed all 15 scenario assertions using real Claude calls,
real HTTP load and process workloads, but a simulated Docker API/runtime. This is not a real
Docker/Traefik/SSH integration pass. A `pass` includes correct escalation or no-action outcomes.
Run both deterministic suites: `python tests/test_safety.py` (38 checks), then
`python tests/test_release.py` (8 release failure-path checks).

Important constraints before a real deployment:
- Live Factory Droid model session and Docker Compose over SSH remain untested. The MCP transport
  was tested independently. Droid tool permissions are not OS/filesystem isolation.
- One Docker endpoint, unique service names across deployments, one controller process per stack.
  Process-local locks do not coordinate separate controllers. In-flight incident recovery after
  controller restart is not implemented.
- Guardrail caps are per service, not aggregate fleet/host admission. Configure summed caps to fit
  the device, especially when two services heal at once.
- Median probe recovery is not a p95/p99 SLO guarantee. Choose longer explicit crash observation
  windows for intermittent jobs. Partial improvements are retained and escalated, not resolved.
- Use a token and a trusted network/Tailscale for edge ingest; do not expose plain WebSocket
  ingest on the public Internet. Sample database credentials are local development fixtures.
- Build images before healing; `up -d --no-build` deliberately cannot build untrusted/new code.
- Run the initial deployment from a git repository to get commit history for each fix.

`evidence/` contains unmodified result JSON from completed runs. Compare controlled workloads and
individual scenarios, not only headline medians: case mixes, model behavior and policy changed
between exploratory runs. These are hackathon measurements, not a statistical benchmark.

## Comparison finding and relapse policy
Sonnet 5 passed the 15-scenario blind run. Haiku 4.5 passed 9/10 comparison assertions,
but masked an unbounded-cache leak with a memory increase; OOM recurred 5.4 s after resolution.
Confirmed OOM/crash/unhealthy signals during post-fix cooldown now escalate immediately,
retain the current change and stop tuning. `s16` forces a temporary memory mitigation to test this
path end to end. Neither a bounded health window nor a median probe check proves permanent health.
