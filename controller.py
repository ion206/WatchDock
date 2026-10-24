"""WatchDock controller: live telemetry in, healed (or escalated) incidents out.

Per service:  WATCHING → OPEN → [QUEUED] → COLLECTING → DIAGNOSING → APPLYING → VERIFYING → RESOLVED
                                                               │             └─ fail → ROLLING_BACK → DIAGNOSING (attempt 2)
                                                               └─ no fix / escalate / budget → ESCALATED
Triggers      prober `trigger` (latency) · `oom` or die with OOMKilled · ≥N non-clean exits in a window (crash loop) · unhealthy
Suppression   signals during an open incident feed its verification; cooldown after RESOLVED; hold after ESCALATED
Safety        one incident per deployment dir at a time (one compose file, one writer); heal budget per service;
              guardrails from the pre-incident snapshot; unapplied edits are reverted; failed fixes rolled back
"""
from __future__ import annotations

import asyncio, json, os, shutil, statistics, subprocess, sys, time
from collections import defaultdict, deque

import guardrails
from brains import BrainResult
from toolbox import Toolbox

TERMINAL = {"RESOLVED", "ESCALATED"}
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
C = {k: (f"\033[{v}m" if _TTY else "") for k, v in dict(dim="2", bold="1", red="31", green="32", amber="33", cyan="36", reset="0").items()}
STATE_COLOR = {"OPEN": "red", "DIAGNOSING": "cyan", "APPLYING": "amber", "VERIFYING": "amber", "ROLLING_BACK": "red",
               "RESOLVED": "green", "ESCALATED": "red", "QUEUED": "dim", "COLLECTING": "dim", "SUPPRESSED": "dim"}


class Incident:
    def __init__(self, service, dep, kind, signal_at, detail):
        self.service, self.project, self.dir, self.kind = service, dep["project"], dep["dir"], kind
        self.id = f"inc-{time.strftime('%m%d-%H%M%S')}-{service}"
        self.opened_at, self.first_signal_at, self.detail = time.time(), signal_at, detail
        self.state, self.timeline, self.attempts, self.signals = "OPEN", [], [], []
        self.baseline = self.applied_at = self.closed_at = None
        self.outcome = self.reason = None

    def record(self):
        return {k: getattr(self, k) for k in ("id", "service", "project", "kind", "opened_at", "first_signal_at", "detail",
                                              "state", "timeline", "attempts", "applied_at", "closed_at", "outcome", "reason")}


class Watch:
    def __init__(self, service, dep):
        self.service, self.dep = service, dep
        self.probes, self.summaries, self.events = deque(maxlen=900), deque(maxlen=900), deque(maxlen=400)
        self.replicas: dict = {}
        self.incident: Incident | None = None
        self.cooldown_until = self.hold_until = 0.0
        self.last_suppressed = 0.0


class Controller:
    def __init__(self, deployment_dirs, deployer, docker_client, brain, *, fallback=None, publish=None,
                 state_dir=".watchdock", brain_timeout=120, max_attempts=2, concurrency=2, collect_delay=1.5,
                 cooldown=60, escalation_hold=300, crash_loop=(2, 60), grace=3.0, edit_mode="structured",
                 git=True, quiet=False):
        self.deployer, self.docker, self.brain, self.fallback = deployer, docker_client, brain, fallback
        self.publish = publish or (lambda topic, data: None)
        self.state_dir = state_dir; os.makedirs(state_dir, exist_ok=True)
        self.brain_timeout, self.max_attempts, self.collect_delay = brain_timeout, max_attempts, collect_delay
        self.cooldown, self.escalation_hold, self.crash_loop, self.grace = cooldown, escalation_hold, crash_loop, grace
        self.edit_mode, self.git, self.quiet = edit_mode, git, quiet
        self.sem = asyncio.Semaphore(concurrency)
        self.dir_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.watch: dict[str, Watch] = {}
        self.policies: dict[str, dict] = {}
        self.incidents: list[Incident] = []
        self.tasks: set = set()
        self.listeners: list = []
        self._device = None
        self.heals = defaultdict(list, json.load(open(self._hp())) if os.path.exists(self._hp()) else {})
        for d in deployment_dirs:
            self.add_deployment(d)

    # ---- setup ----------------------------------------------------------------
    def add_deployment(self, d: str, project: str | None = None):
        d = os.path.abspath(d)
        meta, _ = guardrails.read_manifest(open(os.path.join(d, "watchdock.md")).read())
        dep = {"dir": d, "project": project or meta.get("project") or os.path.basename(d)}
        for svc in guardrails.managed_services(meta):
            if svc in self.watch:
                raise ValueError(f"Duplicate service name {svc!r}: use unique service names across watched deployments")
            self.policies[svc] = guardrails.service_policy(meta, svc)
            self.watch[svc] = Watch(svc, dep)
        return dep

    def remove_deployment(self, d: str):
        d = os.path.abspath(d)
        for svc in [s for s, w in self.watch.items() if w.dep["dir"] == d]:
            self.watch.pop(svc, None); self.policies.pop(svc, None)

    def _hp(self):
        return os.path.join(self.state_dir, "heals.json")

    def recent_heals(self, svc):
        cut = time.time() - 3600
        return sum(1 for t in self.heals.get(svc, []) if t > cut)

    # ---- telemetry in (called by the bridge for every live envelope) -----------------------------
    def handle(self, env: dict):
        w = self.watch.get(env.get("service"))
        if not w:
            return
        t, d, kind = env.get("ts_ns", time.time_ns()) / 1e9, env.get("data") or {}, env.get("kind")
        if kind == "probe":
            w.probes.append((t, d.get("ttfb_ms", 0), bool(d.get("ok")), bool(d.get("breach"))))
        elif kind == "summary":
            w.summaries.append((t, d))
        elif kind == "stats":
            w.replicas[d.get("replica")] = (t, d)
        elif kind == "event":
            w.events.append((t, d))
            if d.get("trigger"):
                self._crash_signal(w, t, d)
        elif kind == "trigger" and d.get("trigger"):
            self._open(w, "latency", t, {k: v for k, v in d.items() if k != "trigger"})

    def _crash_signal(self, w: Watch, t, d):
        if d.get("action") == "oom" or d.get("oom_killed"):
            return self._open(w, "oom", t, d)
        if d.get("detail") == "unhealthy":
            return self._open(w, "unhealthy", t, d)
        n, window = self.crash_loop
        dies = [e for e in w.events if e[1].get("action") == "die" and e[1].get("trigger") and e[0] > t - window]
        if len(dies) >= n:
            self._open(w, "crash", dies[0][0], {**d, "exits_in_window": len(dies)})
        elif w.incident and w.incident.state not in TERMINAL:
            w.incident.signals.append((t, d))

    def _open(self, w: Watch, kind, t, detail):
        now = time.time()
        if w.incident and w.incident.state not in TERMINAL:
            w.incident.signals.append((t, {"kind": kind, **detail}))
            return
        if now < w.cooldown_until and kind in {"oom", "crash", "unhealthy"} and now >= w.hold_until:
            # A cooldown prevents tuning thrash, not reporting a confirmed failure of a recent fix.
            inc = Incident(w.service, w.dep, kind, t, detail)
            w.incident = inc
            self.incidents.append(inc)
            self._to(inc, "OPEN", "confirmed failure during post-fix cooldown")
            task = asyncio.ensure_future(self._escalate(inc, w, "POST-FIX RELAPSE: confirmed " + kind + " during cooldown; change retained, automatic tuning stopped; human investigation required"))
            self.tasks.add(task); task.add_done_callback(self.tasks.discard)
            return
        why = "escalated, waiting for a human" if now < w.hold_until else "cooldown after a fix" if now < w.cooldown_until else None
        if why:
            if now - w.last_suppressed > 10:
                w.last_suppressed = now
                self._say(w.service, "SUPPRESSED", f"{kind} signal ignored: {why}")
                self.publish(f"/wd/{w.service}/incident", {"service": w.service, "state": "SUPPRESSED", "kind": kind, "note": why})
            return
        if kind == "latency":                        # first breaching probe of this degradation
            breach_ts = [p[0] for p in w.probes if p[3] and p[0] > now - 30]
            t = min(breach_ts) if breach_ts else t
        inc = Incident(w.service, w.dep, kind, t, detail)
        w.incident = inc
        self.incidents.append(inc)
        self._to(inc, "OPEN", self._trigger_line(inc))
        task = asyncio.ensure_future(self._run(inc, w))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)

    # ---- incident lifecycle -----------------------------------------------------------------------
    async def _run(self, inc: Incident, w: Watch):
        lock = self.dir_locks[inc.dir]
        if lock.locked():
            self._to(inc, "QUEUED", "another incident is editing this compose file")
        try:
            async with lock, self.sem:
                pol = self.policies[inc.service]
                g = pol["guardrails"]
                if g.get("max_heals_per_hour") is not None and self.recent_heals(inc.service) >= int(g["max_heals_per_hour"]):
                    return await self._escalate(inc, w, f"heal budget spent ({self.recent_heals(inc.service)} heals in the last hour)")
                self._to(inc, "COLLECTING", "gathering logs, stats and events")
                await asyncio.sleep(0.5 if inc.kind == "latency" else self.collect_delay)   # crashes: let the crash logs land
                inc.baseline = guardrails.snapshot(inc.dir)
                prev = None
                for attempt in range(1, self.max_attempts + 1):
                    bundle = await self.bundle(inc, attempt, prev)
                    rec = {"n": attempt, "started": time.time(), "bundle_bytes": len(json.dumps(bundle, default=str))}
                    inc.attempts.append(rec)
                    tb = Toolbox(inc, self, self.edit_mode)
                    self._to(inc, "DIAGNOSING", f"attempt {attempt} · {getattr(self.brain, 'model', self.brain.name)}")
                    brain_task = asyncio.ensure_future(self._think(self.brain, bundle, tb, inc, rec))
                    applied_wait = asyncio.ensure_future(tb.applied_event.wait())
                    await asyncio.wait({brain_task, applied_wait}, return_when=asyncio.FIRST_COMPLETED)
                    verify_task = None
                    if tb.applied:                         # deployed: verify now, while the model writes its summary
                        self._to(inc, "VERIFYING", self._verify_line(inc))
                        verify_task = asyncio.ensure_future(self.verify(inc, w))
                    res = await brain_task
                    applied_wait.cancel()
                    if res is None and self.fallback and not tb.applied and not tb.escalated:
                        self._restore(inc)
                        tb = Toolbox(inc, self, "structured")
                        self._to(inc, "DIAGNOSING", "model unavailable → deterministic playbook")
                        res = await self._think(self.fallback, bundle, tb, inc, rec)
                    rec.update(brain=dict(res or {}), tool_calls=tb.calls, applied=tb.applied, escalated=tb.escalated,
                               finished=time.time())
                    if not tb.applied:
                        self._restore(inc)
                        return await self._escalate(inc, w, tb.escalated or rec.get("brain_error") or "the brain ended without applying a fix")
                    if verify_task is None:
                        self._to(inc, "VERIFYING", self._verify_line(inc))
                        verify_task = asyncio.ensure_future(self.verify(inc, w))
                    ok, detail = await verify_task
                    rec["verify"] = detail
                    if ok:
                        if detail.get("partial"):
                            return await self._escalate(inc, w, "PARTIAL IMPROVEMENT, change retained: " + detail["reason"])
                        return await self._resolve(inc, w, tb.applied)
                    self._to(inc, "ROLLING_BACK", detail["reason"])
                    self._restore(inc)
                    rb = await asyncio.to_thread(self.deployer.apply, inc.dir, inc.project)
                    rec["rollback"] = {"ok": rb["ok"], "seconds": rb["seconds"], "commit": self._commit(inc, f"watchdock {inc.id}: roll back attempt {attempt}: {detail['reason']}")}
                    if not rb["ok"]:
                        return await self._escalate(inc, w, "ROLLBACK FAILED: original file restored, but runtime state is unknown; " + rb.get("output", "")[-300:])
                    prev = {"change": tb.applied.get("changes"), "summary": tb.applied.get("summary"), "verification": detail}
                    await asyncio.sleep(3)
                return await self._escalate(inc, w, f"{self.max_attempts} attempts failed verification; rolled back to the original config")
        except Exception as e:                                   # never leave an incident dangling
            import traceback; traceback.print_exc()
            self._restore(inc)
            recovery = ""
            if inc.applied_at:
                try:
                    rb = await asyncio.to_thread(self.deployer.apply, inc.dir, inc.project)
                    recovery = "; baseline redeployed" if rb["ok"] else "; ROLLBACK FAILED, runtime state unknown"
                except Exception as rb_error:
                    recovery = f"; ROLLBACK FAILED: {rb_error}"
            return await self._escalate(inc, w, f"controller error: {type(e).__name__}: {e}{recovery}")

    async def _think(self, brain, bundle, tb, inc, rec):
        emit = lambda kind, payload: self.on_thought(inc, kind, payload)
        try:
            return await asyncio.wait_for(brain.heal(bundle, tb, emit), self.brain_timeout)
        except Exception as e:                                   # timeout, API error, SDK error
            rec["brain_error"] = f"{getattr(brain, 'name', '?')}: {type(e).__name__}: {str(e)[:200]}"
            self._say(inc.service, "DIAGNOSING", rec["brain_error"])
            return None

    async def apply(self, inc: Incident, summary: str) -> dict:
        """The only path to production. Called by Toolbox.apply_fix."""
        v = guardrails.validate(inc.baseline, inc.dir, inc.service, self.recent_heals(inc.service))
        if not v["ok"]:
            return {"applied": False, "refused": v["violations"]}
        if not v["changes"]:
            return {"applied": False, "refused": ["docker-compose.yml has no change to apply"]}
        change = self._change_line(v["changes"])
        self._to(inc, "APPLYING", change)
        dep = await asyncio.to_thread(self.deployer.apply, inc.dir, inc.project)
        if not dep["ok"]:
            self._restore(inc)
            rb = await asyncio.to_thread(self.deployer.apply, inc.dir, inc.project)
            if not rb["ok"]:
                raise RuntimeError("Deploy failed and baseline redeploy failed; runtime state unknown: " + rb.get("output", "")[-300:])
            return {"applied": False, "error": dep["output"][-500:], "rollback_ok": True}
        inc.applied_at = time.time()
        self.heals[inc.service].append(inc.applied_at)
        json.dump(self.heals, open(self._hp(), "w"))
        sha = self._commit(inc, f"watchdock {inc.id}: {change} ({summary[:120]})")
        return {"applied": True, "changes": v["changes"], "summary": summary, "commit": sha, "deploy_seconds": dep["seconds"],
                "deploy": dep["output"][-400:], "note": "deployed; WatchDock is verifying and will roll back if it does not hold"}

    async def verify(self, inc: Incident, w: Watch):
        pol = self.policies[inc.service]
        vc, t_apply, grace = pol["verify"], inc.applied_at, pol["verify"].get("grace_s", self.grace)
        crash = lambda: [e for e in w.events if e[0] > t_apply + grace and e[1].get("trigger")]
        if inc.kind == "latency":
            # Robust to tail latency: judge the MEDIAN of the last N probes, and never roll back an improvement.
            # (Found in the lab: "5 consecutive under 500 ms" rolled back a scale-out that had cut user latency
            # from 3.4 s to 0.1 s, because saturated replicas still produced occasional 1.3 s tail probes.)
            recover = float(pol["probe"].get("recover_ms", 500)); threshold = float(pol["probe"].get("threshold_ms", 800))
            need = int(vc.get("consecutive", 5)); deadline = t_apply + float(vc.get("timeout_s", 60))
            before = [p[1] for p in w.probes if inc.first_signal_at - 1 <= p[0] <= t_apply]
            p50_before = statistics.median(before) if before else None
            after = lambda: [p for p in w.probes if p[0] > t_apply + grace]
            while time.time() < deadline:
                await asyncio.sleep(1)
                if crash():
                    e = crash()[0][1]; return False, {"reason": f"{e.get('action')} (exit {e.get('exit_code')}) after the change"}
                tail = after()[-need:]
                if len(tail) == need and time.time() - tail[-1][0] < float(vc.get("max_probe_age_s", 6)) and statistics.median(p[1] for p in tail) < recover and sum(1 for p in tail if not p[2]) <= 1:
                    return True, {"reason": f"median of the last {need} probes {statistics.median(p[1] for p in tail):.0f} ms < {recover:.0f} ms",
                                  "p50_ms": statistics.median(p[1] for p in tail), "p50_before_ms": p50_before, "seconds": round(time.time() - t_apply, 1)}
            tail = after()[-2 * need:]
            p50 = statistics.median(p[1] for p in tail) if tail else None
            if len(tail) >= need and time.time() - tail[-1][0] < float(vc.get("max_probe_age_s", 6)) and sum(1 for p in tail if not p[2]) <= 1 and p50 is not None and p50_before and p50 < threshold and p50 <= 0.5 * p50_before:
                return True, {"reason": f"improved: p50 {p50_before:.0f} → {p50:.0f} ms (below the {threshold:.0f} ms trigger, above the {recover:.0f} ms re-arm); change kept",
                              "partial": True, "p50_ms": p50, "p50_before_ms": p50_before, "seconds": round(time.time() - t_apply, 1)}
            return False, {"reason": (f"latency {p50_before or 0:.0f} → {p50:.0f} ms: not enough improvement {vc.get('timeout_s', 60)} s after the change" if p50 is not None
                                      else "no probe data after the change"), "p50_ms": p50, "p50_before_ms": p50_before, "seconds": round(time.time() - t_apply, 1)}
        window = vc.get("window_s")
        if window is None:          # adaptive: 3× the failure period seen before the incident, clamped to 10–60 s
            ts = sorted(t for t, d in w.events if d.get("trigger") and inc.first_signal_at - 120 <= t <= inc.opened_at + 5)
            gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a > 0.5]
            window = min(60.0, max(10.0, 3 * statistics.median(gaps) + 5)) if gaps else 30.0
        window = float(window)
        deadline = t_apply + grace + window
        while time.time() < deadline:
            await asyncio.sleep(1)
            if crash():
                e = crash()[0][1]
                return False, {"reason": f"{'OOM kill' if e.get('action') == 'oom' or e.get('oom_killed') else 'exit ' + str(e.get('exit_code'))} again {crash()[0][0] - t_apply:.0f} s after the change",
                               "seconds": round(time.time() - t_apply, 1)}
        st = await self.status(inc)
        down = [r for r in st["replicas"] if r["status"] != "running"]
        if down or not st["replicas"]:
            return False, {"reason": f"{len(down)} replica(s) not running after {window:.0f} s", "seconds": round(time.time() - t_apply, 1)}
        return True, {"reason": f"no crash or OOM for {window:.0f} s, {len(st['replicas'])} replica(s) running", "window_s": round(window, 1),
                      "seconds": round(time.time() - t_apply, 1)}

    async def _resolve(self, inc, w, applied):
        inc.outcome, inc.closed_at = "RESOLVED", time.time()
        w.cooldown_until = time.time() + float(self.policies[inc.service]["guardrails"].get("cooldown_s", self.cooldown))
        m = self.metrics(inc)
        guardrails.append_ledger(os.path.join(inc.dir, "watchdock.md"), self._trigger_line(inc), self._evidence_line(inc),
                                 self._change_line(applied["changes"]), f"resolved, {m['mttr_s']:.0f} s from first signal", applied.get("commit") or "-")
        self._commit(inc, f"watchdock {inc.id}: ledger")
        partial = (inc.attempts[-1].get("verify") or {}).get("partial") if inc.attempts else False
        self._to(inc, "RESOLVED", f"{self._change_line(applied['changes'])} · MTTR {m['mttr_s']:.0f} s" + (" · partial: improved, not fully recovered" if partial else ""))
        self._persist(inc)

    async def _escalate(self, inc, w, reason):
        inc.outcome, inc.reason, inc.closed_at = "ESCALATED", reason, time.time()
        w.hold_until = time.time() + self.escalation_hold
        try:
            guardrails.append_ledger(os.path.join(inc.dir, "watchdock.md"), self._trigger_line(inc), self._evidence_line(inc),
                                     "none", f"escalated: {reason}", "-")
            self._commit(inc, f"watchdock {inc.id}: escalated")
        except Exception:
            pass
        self._to(inc, "ESCALATED", reason)
        self._persist(inc)

    # ---- evidence & status ------------------------------------------------------------------------
    async def device(self):
        if self._device is None:
            try:
                i = await asyncio.to_thread(self.docker.info)
                self._device = {"ncpu": i.get("NCPU"), "mem_total_mb": round(i.get("MemTotal", 0) / 2**20)}
            except Exception:
                self._device = {"ncpu": None, "mem_total_mb": None}
        return self._device

    async def status(self, inc: Incident) -> dict:
        def q():
            labels = [f"com.docker.compose.service={inc.service}", f"com.docker.compose.project={inc.project}"]
            out = []
            for c in self.docker.containers.list(all=True, filters={"label": labels}):
                st = c.attrs.get("State", {})
                out.append({"container": c.name, "status": c.status, "restarts": c.attrs.get("RestartCount", 0),
                            "oom_killed": st.get("OOMKilled"), "exit_code": st.get("ExitCode")})
            return out
        w = self.watch[inc.service]
        probes = list(w.probes)[-5:]
        summ = w.summaries[-1][1] if w.summaries else {}
        return {"replicas": await asyncio.to_thread(q),
                "probe_last5_ms": [round(p[1]) for p in probes], "probe_errors_last5": sum(1 for p in probes if not p[2]),
                "cpu_pct_total": summ.get("cpu_pct"), "mem_mb_total": summ.get("mem_mb"), "mem_limit_mb": summ.get("mem_limit_mb"),
                "seconds_since_incident": round(time.time() - inc.opened_at, 1)}

    async def bundle(self, inc: Incident, attempt: int, prev: dict | None) -> dict:
        from telemetry import collect_logs
        w, pol = self.watch[inc.service], self.policies[inc.service]
        t0 = inc.first_signal_at - 60
        probes = [p for p in w.probes if p[0] >= t0]
        step = max(1, len(probes) // 30)
        summ = [s for t, s in w.summaries if t >= inc.first_signal_at - 30]
        cpu = [s.get("cpu_pct", 0) for s in summ]
        reps = [max(s.get("replicas", 1), 1) for s in summ]
        mem = [s.get("mem_peak_mb", 0) for s in summ]
        cfg = guardrails.view(guardrails.effective(os.path.join(inc.dir, "docker-compose.yml"), inc.dir), inc.service)
        evs = [e for e in w.events if e[0] >= t0][-15:]
        try:
            logs = await asyncio.to_thread(collect_logs, self.docker, inc.service, inc.project, 40)
        except Exception as e:
            logs = [{"error": str(e)}]
        logs = sorted(logs, key=lambda r: r.get("container", ""))[:3]
        for r in logs:
            r["logs_tail"] = [l[11:19] + " " + l[31:][:300] if len(l) > 31 and l[4] == "-" else l[:300]
                              for l in (r.get("logs_tail") or "").splitlines()][-40:]
        recent = [p for p in w.probes if p[0] >= inc.first_signal_at - 2][-5:] or probes[-5:]
        return {
            "incident": inc.id, "service": inc.service, "project": inc.project, "attempt": attempt, "previous_attempt": prev,
            "trigger": {"kind": inc.kind, "seconds_ago": round(time.time() - inc.first_signal_at, 1),
                        "detail": {k: v for k, v in (inc.detail or {}).items() if k not in ("window", "target", "image")}},
            "current": {"replicas": cfg["replicas"], "memory_bytes": cfg["memory"], "memory": guardrails.mb(cfg["memory"]), "cpus": cfg["cpus"]},
            "policy": pol, "device": await self.device(),
            "probe": {"threshold_ms": pol["probe"].get("threshold_ms"), "recover_ms": pol["probe"].get("recover_ms"),
                      "p50_ms": round(statistics.median([p[1] for p in recent])) if recent else None,
                      "errors_recent": sum(1 for p in recent if not p[2]),
                      "series": [[round(p[0] - inc.opened_at, 1), round(p[1]), p[2]] for p in probes[::step]]} if probes else None,
            "stats": {"replicas": reps[-1] if reps else None, "cpu_pct_avg_total": round(statistics.mean(cpu), 1) if cpu else None,
                      "cpu_pct_peak_total": max(cpu) if cpu else None,
                      "cpu_pct_avg_per_replica": round(statistics.mean(c / r for c, r in zip(cpu, reps)), 1) if cpu else None,
                      "cpu_limit_pct_per_replica": round((cfg["cpus"] or 0) * 100) or None,
                      "mem_peak_mb": max(mem) if mem else None, "mem_limit_mb": summ[-1].get("mem_limit_mb") if summ else None},
            "events": [{"t": round(t - inc.opened_at, 1), "action": d.get("action"), "replica": d.get("replica"),
                        "exit_code": d.get("exit_code"), "oom_killed": d.get("oom_killed")} for t, d in evs],
            "exit_codes": sorted({d.get("exit_code") for _, d in evs if d.get("action") == "die"} - {None}),
            "replicas_now": (await self.status(inc))["replicas"], "logs": logs,
            "watchdock_md": open(os.path.join(inc.dir, "watchdock.md")).read(),
            "docker_compose_yml": open(os.path.join(inc.dir, "docker-compose.yml")).read(),
        }

    # ---- helpers -----------------------------------------------------------------------------------
    def _restore(self, inc):
        if inc.baseline:
            shutil.copy2(os.path.join(inc.baseline, "docker-compose.yml"), os.path.join(inc.dir, "docker-compose.yml"))

    def _commit(self, inc, msg):
        if not self.git or not os.path.isdir(os.path.join(inc.dir, ".git")):
            return None
        run = lambda *a: subprocess.run(["git", "-C", inc.dir, *a], capture_output=True, text=True)
        run("add", "docker-compose.yml", "watchdock.md")
        if run("diff", "--cached", "--quiet").returncode == 0:
            return None
        run("-c", "user.name=WatchDock", "-c", "user.email=watchdock@localhost", "commit", "-q", "-m", msg)
        return run("rev-parse", "--short", "HEAD").stdout.strip()

    def metrics(self, inc):
        end = inc.closed_at or time.time()
        a = inc.attempts[-1] if inc.attempts else {}
        return {"detect_s": round(inc.opened_at - inc.first_signal_at, 1),
                "diagnose_s": round(sum(((x.get("brain") or {}).get("seconds") or 0) for x in inc.attempts), 1),
                "to_apply_s": round(inc.applied_at - inc.opened_at, 1) if inc.applied_at else None,
                "verify_s": (a.get("verify") or {}).get("seconds"), "mttr_s": round(end - inc.first_signal_at, 1)}

    def _persist(self, inc):
        rec = inc.record(); rec["metrics"] = self.metrics(inc)
        with open(os.path.join(self.state_dir, "incidents.jsonl"), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        for fn in self.listeners:
            fn(inc)

    def _to(self, inc, state, note=""):
        inc.state = state
        inc.timeline.append({"t": time.time(), "state": state, "note": note})
        self._say(inc.service, state, note, inc.id)
        self.publish(f"/wd/{inc.service}/incident", {"id": inc.id, "service": inc.service, "kind": inc.kind, "state": state,
                                                     "attempt": len(inc.attempts), "note": note[:300]})

    def on_tool(self, inc, name, args, out):
        brief = {k: v for k, v in (out or {}).items() if k in ("ok", "applied", "refused", "error", "violations", "changes")} if isinstance(out, dict) else {}
        inc.timeline.append({"t": time.time(), "tool": name, "args": args, "result": brief})
        self._say(inc.service, "tool", f"{name}({json.dumps(args)[:120]}) → {json.dumps(brief, default=str)[:200]}")
        self.publish("/wd/agent", {"service": inc.service, "incident": inc.id, "type": "tool", "text": f"{name} {json.dumps(args)[:200]}"})

    def on_thought(self, inc, kind, payload):
        if kind == "thought":
            inc.timeline.append({"t": time.time(), "thought": payload[:4000]})
            self._say(inc.service, "think", payload.replace("\n", " ")[:220])
            self.publish("/wd/agent", {"service": inc.service, "incident": inc.id, "type": "thought", "text": payload[:2000]})

    def _say(self, svc, state, note="", iid=None):
        if self.quiet:
            return
        col = C.get(STATE_COLOR.get(state, "dim"), "")
        print(f"{C['dim']}{time.strftime('%H:%M:%S')}{C['reset']} {C['bold']}{svc:<10}{C['reset']} {col}{state:<12}{C['reset']} {note}", flush=True)

    def _trigger_line(self, inc):
        d = inc.detail or {}
        if inc.kind == "latency":
            return f"TTFB p50 {d.get('p50_ms')} ms > {d.get('threshold_ms')} ms ({d.get('breaches')}/5 probes)"
        if inc.kind == "oom":
            return f"OOM kill (exit 137) on {d.get('container')}"
        if inc.kind == "crash":
            return f"crash loop: exit {d.get('exit_code')} ×{d.get('exits_in_window')}"
        return inc.kind

    def _evidence_line(self, inc):
        w = self.watch[inc.service]
        s = [x for t, x in w.summaries if inc.first_signal_at - 30 <= t <= inc.opened_at + 5]
        if not s:
            return "-"
        return f"cpu peak {max(x.get('cpu_pct', 0) for x in s):.0f}%, mem peak {max(x.get('mem_peak_mb', 0) for x in s):.0f}/{s[-1].get('mem_limit_mb')} MiB"

    @staticmethod
    def _change_line(ch):
        return ", ".join(f"{k} {a} → {b}" for k, (a, b) in ch.items())

    def _verify_line(self, inc):
        pol = self.policies[inc.service]
        if inc.kind == "latency":
            return f"waiting for the median of {pol['verify'].get('consecutive', 5)} probes < {pol['probe'].get('recover_ms', 500)} ms (≤ {pol['verify'].get('timeout_s', 60)} s; an improvement is never rolled back)"
        return f"watching for {pol['verify'].get('window_s') or '3× the crash interval (10–60)'} s with no crash or OOM"
