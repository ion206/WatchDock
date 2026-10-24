// WatchDock design doc · Draft 3 (built, tested, measured). Live data: ../lab/app (runs, findings), ./files (code).
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const SECTIONS = [['status', 'Status'], ['arch', 'Architecture'], ['controller', 'Controller'], ['verify', 'Verify & rollback'], ['tools', 'Agent surface'],
  ['threats', 'Threat model'], ['contracts', 'Contracts'], ['lab', 'Test matrix'], ['findings', 'Findings'], ['demo', 'Demo'], ['runbook', 'Runbook'], ['code', 'Code'], ['left', 'What’s left']];
function palette(isLight) {
  return isLight ? { node: '#ffffff', band: '#f7f7f8', stroke: '#d4d4d8', hair: '#e4e4e7', ink: '#18181b', muted: '#52525b', faint: '#8a8a93',
    acc: '#E8654A', accText: '#B8452D', accSoft: 'rgba(232,101,74,0.09)', ok: '#2f7d4f', okSoft: 'rgba(47,125,79,0.10)' }
  : { node: '#18181b', band: '#121214', stroke: '#3f3f46', hair: '#27272a', ink: '#f4f4f5', muted: '#a1a1aa', faint: '#71717a',
    acc: '#F07860', accText: '#F59A86', accSoft: 'rgba(240,120,96,0.12)', ok: '#6cc28a', okSoft: 'rgba(108,194,138,0.12)' };
}
function classes(isLight) {
  return isLight ? { text: 'text-zinc-900', body: 'text-zinc-700', muted: 'text-zinc-500', faint: 'text-zinc-400', hair: 'border-zinc-200', soft: 'border-zinc-100',
    codeBg: 'bg-zinc-50', btn: 'border-zinc-200 text-zinc-700 hover:bg-zinc-50', primary: 'bg-zinc-900 text-white hover:bg-zinc-700',
    navOff: 'text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100', tabOn: 'bg-zinc-900 text-white', tabOff: 'text-zinc-600 hover:bg-zinc-100' }
  : { text: 'text-zinc-100', body: 'text-zinc-300', muted: 'text-zinc-400', faint: 'text-zinc-500', hair: 'border-zinc-800', soft: 'border-zinc-800/70',
    codeBg: 'bg-zinc-900/60', btn: 'border-zinc-800 text-zinc-200 hover:bg-white/[0.04]', primary: 'bg-zinc-100 text-zinc-900 hover:bg-white',
    navOff: 'text-zinc-400 hover:text-zinc-100 hover:bg-white/[0.06]', tabOn: 'bg-zinc-100 text-zinc-900', tabOff: 'text-zinc-400 hover:bg-white/[0.06]' };
}
function Box({ x, y, w, h, title, lines = [], p, acc, dashed }) {
  return (<g>
    <rect x={x} y={y} width={w} height={h} rx="10" fill={p.node} stroke={acc ? p.acc : p.stroke} strokeWidth={acc ? 1.5 : 1} strokeDasharray={dashed ? '4 3' : undefined} />
    <text x={x + 12} y={y + 24} fontSize="14" fontWeight="600" fill={p.ink}>{title}</text>
    {lines.map((l, i) => <text key={i} x={x + 12} y={y + 43 + i * 16} fontSize="11" fill={p.muted} fontFamily={MONO}>{l}</text>)}
  </g>);
}
const LOOP = 'M160,235 L160,107 L700,107 L700,367 L300,367 L300,235 L160,235';
function ArchDiagram({ p, motion }) {
  const E = (d) => <path d={d} fill="none" stroke={p.faint} strokeWidth="1.2" markerEnd="url(#w3-ar)" />;
  const A = (d) => <path d={d} fill="none" stroke={p.acc} strokeWidth="1.8" markerEnd="url(#w3-ar2)" />;
  const L = (x, y, t, a) => <text x={x} y={y} fontSize="10.5" fill={p.faint} fontFamily={MONO} textAnchor={a || 'start'}>{t}</text>;
  return (
    <svg viewBox="0 0 1000 460" className="w-full h-auto block" role="img" aria-label="WatchDock architecture: edge agent, Docker or dockersim, bridge, controller, brain, toolbox and guardrails, deployments repo, Foxglove. The coral loop is the heal path.">
      <defs>
        <marker id="w3-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1,1 L9,5 L1,9" fill="none" stroke={p.faint} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></marker>
        <marker id="w3-ar2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1,1 L9,5 L1,9" fill="none" stroke={p.acc} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></marker>
      </defs>
      <rect x="20" y="20" width="330" height="420" rx="14" fill={p.band} stroke={p.hair} />
      <rect x="380" y="20" width="600" height="420" rx="14" fill={p.band} stroke={p.hair} />
      <text x="36" y="44" fontSize="10.5" fill={p.faint} fontFamily={MONO} letterSpacing="0.06em">EDGE · PI OR VM (LAB: DOCKERSIM)</text>
      <text x="396" y="44" fontSize="10.5" fill={p.faint} fontFamily={MONO} letterSpacing="0.06em">MAC · CONTROL PLANE</text>
      {A('M160,198 L160,146')}{A('M330,107 L398,107')}{A('M570,107 L618,107')}{A('M700,144 L700,328')}{A('M620,367 L572,367')}{A('M400,367 L332,367')}{A('M300,330 L300,272')}
      <Box x={40} y={70} w={290} h={74} title="edge.py" lines={['telemetry · prober (TTFB / 2 s)', 'forwarder: spool when offline']} p={p} acc />
      <Box x={40} y={200} w={80} h={70} title="proxy" lines={[':8080']} p={p} />
      <Box x={130} y={200} w={60} h={70} title="web-1" lines={['.5 cpu']} p={p} />
      <Box x={200} y={200} w={60} h={70} title="web-2" lines={['new']} p={p} dashed />
      <Box x={270} y={200} w={60} h={70} title="web-3" lines={['new']} p={p} dashed />
      <Box x={40} y={330} w={290} h={74} title="Docker Engine" lines={['compose project · events · stats', 'lab: dockersim, same API']} p={p} />
      <Box x={400} y={70} w={170} h={74} title="bridge.py" lines={['WS :8766 → :8765', '+ MCAP recording']} p={p} />
      <Box x={620} y={70} w={160} h={74} title="controller.py" lines={['state machine', 'verify · rollback']} p={p} acc />
      <Box x={810} y={70} w={150} h={74} title="ledger + git" lines={['watchdock.md', 'commit per change']} p={p} />
      <Box x={400} y={200} w={170} h={70} title="Foxglove" lines={['ws://localhost:8765']} p={p} />
      <Box x={810} y={200} w={150} h={70} title="playbook" lines={['if the model fails', 'deterministic']} p={p} dashed />
      <Box x={400} y={330} w={170} h={74} title="toolbox" lines={['6 tools, no shell', 'guardrails · 1 deploy']} p={p} acc />
      <Box x={620} y={330} w={160} h={74} title="brain" lines={['Claude · Droid', 'evidence → tools']} p={p} />
      {E('M122,235 L128,235')}{E('M485,144 L485,198')}{E('M780,107 L808,107')}{E('M780,350 L885,350 L885,272')}
      {L(364, 99, 'WS JSON', 'middle')}{L(594, 99, 'live', 'middle')}{L(708, 240, 'evidence bundle')}{L(596, 359, 'tools', 'middle')}
      {L(366, 359, 'SSH', 'middle')}{L(306, 305, 'scale')}{L(168, 178, 'TTFB · stats')}{L(792, 342, 'fallback')}
      {motion ? (<g><circle r="11" fill={p.acc} opacity="0.18"><animateMotion dur="7s" repeatCount="indefinite" path={LOOP} /></circle>
        <circle r="4.5" fill={p.acc}><animateMotion dur="7s" repeatCount="indefinite" path={LOOP} /></circle></g>) : null}
    </svg>
  );
}
const SM_ROW = ['WATCHING', 'OPEN', 'COLLECTING', 'DIAGNOSING', 'APPLYING', 'VERIFYING', 'RESOLVED'];
function StateMachine({ p }) {
  const X = (i) => 14 + i * 140, W = 122, Y = 70;
  const pill = (x, y, label, kind, w = W) => (
    <g key={label + x}>
      <rect x={x} y={y} width={w} height="32" rx="16" fill={kind === 'ok' ? p.okSoft : kind === 'bad' ? p.accSoft : p.node} stroke={kind === 'ok' ? p.ok : kind === 'bad' ? p.acc : p.stroke} strokeDasharray={kind === 'dim' ? '3 3' : undefined} />
      <text x={x + w / 2} y={y + 20} fontSize="10.5" fontFamily={MONO} textAnchor="middle" fill={kind === 'ok' ? p.ok : kind === 'bad' ? p.accText : p.ink} letterSpacing="0.04em">{label}</text>
    </g>);
  const E = (d) => <path d={d} fill="none" stroke={p.faint} strokeWidth="1.2" markerEnd="url(#w3-sm)" />;
  const L = (x, y, t, a) => <text x={x} y={y} fontSize="10" fill={p.faint} fontFamily={MONO} textAnchor={a || 'middle'}>{t}</text>;
  const cx = (i) => X(i) + W / 2;
  return (
    <svg viewBox="0 0 1000 250" className="w-full h-auto block" role="img" aria-label="Incident state machine">
      <defs><marker id="w3-sm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1,1 L9,5 L1,9" fill="none" stroke={p.faint} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></marker></defs>
      {E(`M${cx(6)},${Y} V28 H${cx(0)} V${Y - 2}`)}{L(500, 22, 'cooldown_s (60) · then re-arm')}
      {SM_ROW.slice(0, -1).map((_, i) => <g key={i}>{E(`M${X(i) + W},${Y + 16} L${X(i + 1) - 2},${Y + 16}`)}</g>)}
      {SM_ROW.map((s, i) => pill(X(i), Y, s, s === 'RESOLVED' ? 'ok' : s === 'OPEN' ? 'bad' : null))}
      {pill(X(1) + 40, 128, 'QUEUED', 'dim', 100)}{E(`M${cx(1)},${Y + 32} L${cx(1) + 10},126`)}{E(`M${X(1) + 140},144 L${X(2) + 20},${Y + 34}`)}
      {L(cx(1) + 20, 176, 'same compose file busy')}
      {pill(X(3), 186, 'ESCALATED', 'bad')}{E(`M${cx(3)},${Y + 32} L${cx(3)},184`)}{L(cx(3) + 56, 150, 'escalate · no fix · budget', 'start')}
      {pill(X(5), 186, 'ROLLING_BACK', 'bad')}{E(`M${cx(5)},${Y + 32} L${cx(5)},184`)}{L(cx(5) + 6, 150, 'verification fails', 'start')}
      {E(`M${X(5) - 2},202 L${X(3) + W + 2},202`)}{L((X(5) + X(3) + W) / 2, 196, 'attempt 2 failed too')}
      {E(`M${cx(5)},218 V240 H${cx(3) + 30} V${Y + 34}`)}{L(cx(4), 236, 'attempt 2, with what failed')}
      {E(`M${X(3)},202 H${X(0) + 60} V${Y + 34}`)}{L(X(1) + 60, 216, 'hold 300 s, then re-arm')}
    </svg>
  );
}
function CodeBlock({ code, label, c, maxH }) {
  const [ok, setOk] = React.useState(false);
  return (<div className={'relative rounded-lg border min-w-0 ' + c.hair + ' ' + c.codeBg}>
    <div className={'flex items-center gap-2 px-3 pt-2 text-[11px] ' + c.faint} style={{ fontFamily: MONO }}>
      <span className="truncate">{label}</span>
      <button onClick={async () => { const r = await copyText(code); setOk(r !== 'failed'); setTimeout(() => setOk(false), 1400); }} className={'ml-auto h-6 px-2.5 rounded-full border text-[11px] shrink-0 ' + c.btn}>{ok ? 'Copied' : 'Copy'}</button>
    </div>
    <pre className={'px-3 pt-2 pb-3 overflow-auto text-[12px] leading-[18px] ' + c.body} style={{ fontFamily: MONO, maxHeight: maxH }}><code>{code}</code></pre>
  </div>);
}
function Table({ cols, rows, c, widths, mono = [] }) {
  const tpl = widths || cols.map(() => 'minmax(0,1fr)').join(' ');
  return (<div className="overflow-x-auto"><div className="min-w-[680px]">
    <div className={'grid gap-3 py-2 text-[11px] tracking-[0.06em] border-b ' + c.soft + ' ' + c.faint} style={{ gridTemplateColumns: tpl, fontFamily: MONO }}>{cols.map((h) => <span key={h}>{h}</span>)}</div>
    {rows.map((r, i) => <div key={i} className={'grid gap-3 py-2 border-b text-[12.5px] leading-5 ' + c.soft} style={{ gridTemplateColumns: tpl }}>
      {r.map((v, k) => <span key={k} className={k === 0 ? c.text : c.body} style={mono.includes(k) ? { fontFamily: MONO, fontSize: 12 } : undefined}>{v}</span>)}</div>)}
  </div></div>);
}

const TRIGGERS = [
  ['latency', 'prober on the device: 3 of the last 5 probes over threshold_ms (timeouts and 5xx count)', 'edge re-arms only when 5/5 < recover_ms; prober cooldown 60 s', 's01 s04 s07 s15'],
  ['oom', '`oom` event, or `die` with State.OOMKilled', 'opens immediately', 's02 s03 s08 s11 s13'],
  ['crash loop', '≥ 2 non-clean exits (not 0 / 143) within 60 s', 'one stray exit is ignored', 's05'],
  ['unhealthy', '`health_status: unhealthy` event', 'opens immediately', '(design)'],
];
const SUPPRESS = [
  ['incident already open', 'the signal is attached to the incident and used by its verification', 'every scenario'],
  ['cooldown after RESOLVED', 'cooldown_s (60 s) suppresses tuning noise, never confirmed OOM/crash/unhealthy recurrence: relapse escalates immediately', 's13 s16'],
  ['hold after ESCALATED', '300 s, so a human gets the incident without the agent thrashing', 's03 s05'],
  ['heal budget', 'max_heals_per_hour counts every applied change, including rolled-back ones; escalates without calling the model', 's13'],
  ['same compose file busy', 'one incident per deployment directory (one writer per file); others wait in QUEUED', 'design'],
  ['global concurrency', 'at most 2 brains at once (semaphore)', 's09'],
];
const STATES = [
  ['OPEN', 'a trigger passes suppression', 'records first-signal time (first breaching probe or crash event)'],
  ['QUEUED', 'another incident holds this directory', 'waits for the per-directory lock'],
  ['COLLECTING', 'lock acquired, budget ok', '0.5 s for latency, 1.5 s for crashes (crash logs land); snapshots compose + manifest as the baseline'],
  ['DIAGNOSING', 'bundle built', 'brain runs with a 150 s timeout; on error or timeout the playbook runs instead'],
  ['APPLYING', 'apply_fix passed guardrails', '`docker compose up -d --no-build`; failure → restore + redeploy; success → commit'],
  ['VERIFYING', 'the deploy succeeded (starts immediately, in parallel with the model’s summary)', 'policy by incident kind, see Verify & rollback'],
  ['ROLLING_BACK', 'verification failed', 'restore baseline compose, redeploy, commit, feed what failed into attempt 2'],
  ['RESOLVED', 'verification passed', 'ledger row + commit, cooldown'],
  ['ESCALATED', 'escalate, no fix, budget, 2 failed attempts, or an error', 'unapplied edits reverted, ledger row + commit, hold'],
];
const VERIFY = [
  ['latency', 'after apply + 3 s grace: median of 5 fresh probes under recover_ms (500), at most 1 failed probe', 'timeout_s (60) passes, or any crash/OOM', 'rolled back; attempt 2 gets the p50 it saw'],
  ['oom / crash / unhealthy', 'after apply + 3 s grace: explicit window_s, or adaptive 3× crash interval + 5 s (10–60 s); no OOM/non-clean exit, replicas running', 'any trigger event inside the window, or a replica down at the end', 'rolled back; attempt 2 gets “OOM again N s after the change”'],
];
const TOOLS = [
  ['get_status', '—', 'read-only Docker query for the incident service', 'replicas, restarts, OOM flags, last 5 probes, CPU, memory'],
  ['read_file', 'path', 'resolved inside the deployment dir; .env refused; symlinks resolved first', 'file text (≤ 20 KB)'],
  ['set_resources', 'replicas · memory · cpus', 'type checks; only the incident service; surgical patch', 'one-line diff + guardrail dry run'],
  ['edit_compose', 'old · new (text mode)', 'compose file only; exactly one match', 'diff + dry run'],
  ['validate', '—', 'guardrails vs the pre-incident snapshot', 'ok · changes · violations'],
  ['apply_fix', 'summary', 'guardrails (whole stack) + heal budget again; once per attempt; one fixed deploy command', 'applied + commit, or refused with reasons'],
  ['escalate', 'reason', 'refused once a fix is applied', 'hands off to a human'],
];
const THREATS = [
  ['Model raises limits past the guardrails', 'apply_fix re-validates against the snapshot taken before the incident', 's10 · safety tests'],
  ['Model edits the guardrails', 'watchdock.md is not writable by any tool; front-matter change is itself a violation', 'safety tests'],
  ['Model edits another service, image, env, ports, volumes', 'whole-stack validation: only replicas/memory/cpus of the incident service may move', 's10 · safety tests'],
  ['Destructive or arbitrary commands', 'no shell tool; Droid runs with Execute disabled; one fixed deploy command', 'design · s10'],
  ['Secret exfiltration', '.env unreadable; logs redacted; bundle never includes .env', 'safety tests · s10'],
  ['Path traversal, symlink escape', 'resolve, then require containment in the deployment dir', 'safety tests'],
  ['Prompt injection in logs', 'system prompt treats logs as data; guardrails hold regardless of what the model wants', 's08'],
  ['A fix that makes things worse', 'verification policy + automatic rollback + commit', 's12'],
  ['Heal loops and thrash', 'cooldown, heal budget, escalation hold; confirmed post-fix relapse bypasses cooldown to escalate', 's13 s16'],
  ['Model outage or hang', '150 s timeout, then the deterministic playbook', 's11'],
  ['Model stops without acting', 'continue on max_tokens, one nudge, unapplied edits reverted', 'run 2 finding'],
  ['Two writers on one compose file', 'per-directory lock (QUEUED)', 'design'],
  ['Transient noise heals for nothing', '3-of-5 rule + hysteresis', 's06'],
];
const BUNDLE = [
  ['trigger', 'controller', 'kind, seconds since the first signal, prober report or crash event'],
  ['current', 'effective compose', 'replicas, memory, cpus right now'],
  ['policy', 'watchdock.md front matter', 'guardrails, probe thresholds, stateful, verify policy'],
  ['device', 'docker info', 'NCPU, MemTotal: is there room to scale?'],
  ['probe', 'edge prober', 'p50 of recent probes, errors, ≤ 30-point series around the incident'],
  ['stats', 'edge summaries', 'CPU per replica vs its limit (the key latency signal), memory peak vs limit'],
  ['events, exit_codes', 'docker events', 'last 15, relative to the incident'],
  ['replicas_now, logs', 'docker API', 'status, restarts, OOM flag; last 40 log lines per replica, redacted'],
  ['watchdock_md, docker_compose_yml', 'files', 'purpose, notes, ledger history; the file the fix edits'],
  ['previous_attempt', 'controller', 'on attempt 2: what was changed and how verification failed'],
];

const ENVELOPE = `{ "v": 1, "id": "pi-lab:1792815173:42",      // host:run:seq, used for dedupe on backfill
  "ts_ns": 1792815201123456789,               // measured on the device (backfill keeps it)
  "host": "pi-lab", "kind": "probe",          // probe | stats | summary | event | trigger | recovered
  "service": "web", "topic": "/wd/web/probe",
  "data": { "ttfb_ms": 1030.1, "ok": true, "status": 200, "breach": true, "state": "degraded", ... } }`;
const MANIFEST = `---
project: web
services:
  web:
    guardrails:
      min_replicas: 1
      max_replicas: 3
      max_memory: 256m          # per container
      max_total_memory: 768m    # replicas × memory, must fit the device
      max_cpus: 1.0
      allowed_actions: [scale]  # scale · set_memory · set_cpus
      max_heals_per_hour: 6
      cooldown_s: 60
    probe:  {threshold_ms: 800, recover_ms: 500}
    verify: {consecutive: 5, timeout_s: 60}
  db:
    stateful: true              # pins max_replicas to 1, whatever else says
    guardrails: {max_memory: 1g, allowed_actions: [set_memory]}
    verify: {window_s: 30}
---
# web
## Purpose · ## Notes for the agent · ## Incident ledger (rows appended by code)`;
const INCIDENT = `{ "id": "inc-1023-211319-web", "service": "web", "kind": "latency",
  "first_signal_at": …, "opened_at": …, "applied_at": …, "closed_at": …,
  "outcome": "RESOLVED" | "ESCALATED", "reason": "…",
  "timeline": [{ "t", "state", "note" } | { "t", "tool", "args", "result" } | { "t", "thought" }],
  "attempts": [{ "n", "brain": { "brain", "turns", "usage", "seconds", "stops" }, "tool_calls",
                 "applied": { "changes", "commit" }, "verify": { "reason", "seconds" }, "rollback" }],
  "metrics": { "detect_s", "diagnose_s", "to_apply_s", "verify_s", "mttr_s" } }`;
const RUNBOOK = [
  ['Mac: install', `python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python tests/test_safety.py
python tests/test_release.py                # 46 checks across two suites`],
  ['Mac: run WatchDock', `export ANTHROPIC_API_KEY=...                  # or --brain droid / --brain fallback
python watchdock.py run --deployments deployments \\
    --docker-host ssh://pi@10.0.0.42 --brain claude
# WatchDock watching web (web), worker (worker) · edge ingest ws://0.0.0.0:8766 · Foxglove ws://127.0.0.1:8765`],
  ['Device: edge agent', `pip install docker websockets
WATCHDOCK_HOST=pi-01 python edge.py --ws ws://MAC_IP:8766 \\
    --probe web=http://localhost:8080/ --threshold 800 --recover 500`],
  ['First live Droid test', `droid                                      # log in once (or export FACTORY_API_KEY)
python watchdock.py run --deployments deployments --brain droid --docker-host ssh://pi@10.0.0.42
# check in the terminal: Droid reads watchdock.md, edits docker-compose.yml, calls the watchdock MCP apply_fix tool
# if Droid cannot see the MCP tools, fall back to --brain claude (same toolbox, tested)`],
  ['Before the demo', `python watchdock.py check deployments/web --service web   # guardrail dry run vs git HEAD
ab -n 100000 -c 20 http://pi:8080/     # or: python lab/loadgen.py http://pi:8080/ --concurrency 60 --think 1`],
];
const LEFT = [
  ['Must', 'Live Droid run', 'DroidBrain is written against droid-sdk 0.5 (Execute disabled, WatchDock tools as an in-process MCP server, edits gated to docker-compose.yml) and its MCP client/server round trip passed, but no Factory model call ran here: there is no Factory key in this workspace. First thing to run on your Mac.'],
  ['Must', 'Real Docker on the Pi', 'Everything ran against dockersim, which speaks the Docker API and enforces limits with real processes. ComposeDeployer and the SSH path are untested here. Check memory cgroups on the Pi (`docker info`).'],
  ['Should', 'Terminal UI and Foxglove layout', 'The controller prints a coloured state stream and publishes /wd/<svc>/incident and /wd/agent; the Textual panes and a saved layout.json are the demo polish left.'],
  ['Should', 'Scale back in', 'After a quiet period, step replicas down to min_replicas through the same toolbox.'],
  ['Nice', 'Skip the model on repeat incidents', 'Match a new incident to a resolved ledger row and apply the recorded fix (about 10 s instead of 30).'],
];

function Section({ id, n, title, lead, c, children }) {
  return (<section id={'w3-' + id} className={'pt-10 mt-10 border-t ' + c.hair} style={{ scrollMarginTop: 'calc(var(--mana-top-inset, 64px) + 12px)' }}>
    <div className={'text-[11px] tracking-[0.08em] ' + c.faint} style={{ fontFamily: MONO }}>{String(n).padStart(2, '0')}</div>
    <h2 className={'mt-1 text-[21px] leading-7 font-semibold tracking-[-0.01em] ' + c.text}>{title}</h2>
    {lead ? <p className={'mt-2 text-[14.5px] leading-6 max-w-[74ch] ' + c.body}>{lead}</p> : null}
    <div className="mt-5">{children}</div>
  </section>);
}
function Tag({ t, p }) {
  const strong = t === 'Must';
  return <span className="inline-flex items-center h-5 px-2 rounded-full text-[10.5px] font-medium shrink-0" style={{ fontFamily: MONO, color: strong ? p.accText : p.muted, background: strong ? p.accSoft : 'transparent', boxShadow: strong ? 'none' : 'inset 0 0 0 1px ' + p.stroke }}>{t}</span>;
}
const fmtS = (s) => (s == null ? '–' : Math.round(s) + ' s');

function UserDiv() {
  const { theme } = useTheme();
  const isLight = theme !== 'dark';
  const p = palette(isLight), c = classes(isLight);
  const motion = React.useMemo(() => { try { return !window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return true; } }, []);
  const [files, setFiles] = React.useState([]);
  const [tab, setTab] = React.useState(0);
  const [index, setIndex] = React.useState([]);
  const [latest, setLatest] = React.useState(null);
  const [findings, setFindings] = React.useState(null);
  const [zip, setZip] = React.useState(null);
  React.useEffect(() => FS.onValue(FS.ref(FS.db, relPathAddress('files')), (s) => setFiles(Object.values(s.val() || {}).sort((a, b) => (a.order || 0) - (b.order || 0)))), []);
  React.useEffect(() => FS.onValue(FS.ref(FS.db, relPathAddress('..', 'lab', 'app', 'index')), (s) => setIndex(Object.values(s.val() || {}).sort((a, b) => a.started_ms - b.started_ms))), []);
  React.useEffect(() => FS.onValue(FS.ref(FS.db, relPathAddress('..', 'lab', 'app', 'findings')), (s) => { try { setFindings(s.val() ? JSON.parse(s.val()) : null); } catch (e) {} }), []);
  const best = index.length ? (index.find((m) => m.featured) || index[index.length - 1]) : null;
  React.useEffect(() => {
    if (!best) return;
    return FS.onValue(FS.ref(FS.db, relPathAddress('..', 'lab', 'app', 'runs', best.id, 'scenarios')), (s) => {
      const out = {}; Object.entries(s.val() || {}).forEach(([k, j]) => { try { out[k] = JSON.parse(j); } catch (e) {} }); setLatest(out);
    });
  }, [best && best.id]);
  React.useEffect(() => { let live = true; FS.getDownloadURL(FS.storageRef(FS.storage, ownerId + '/watchdock/watchdock.zip')).then((u) => live && setZip(u)).catch(() => {}); return () => { live = false; }; }, []);
  const go = (id) => { const el = document.getElementById('w3-' + id); if (el) el.scrollIntoView({ behavior: motion ? 'smooth' : 'auto', block: 'start' }); };
  const openLab = () => window.history.pushState({}, '', `/home/${ownerId}/${[...pathSegments.slice(0, -1), 'lab'].map(encodeURIComponent).join('/')}`);
  const totals = index.reduce((a, m) => ({ runs: a.runs + 1, sc: a.sc + m.total, inc: a.inc + m.incidents }), { runs: 0, sc: 0, inc: 0 });
  const P = ({ children }) => <p className={'mt-2 text-[13.5px] leading-[21px] max-w-[78ch] ' + c.body}>{children}</p>;
  const H3 = ({ children }) => <div className={'mt-7 mb-2 text-[13px] font-semibold ' + c.text}>{children}</div>;

  return (
    <div className="w-full"><div className="max-w-[1000px] mx-auto px-4 sm:px-6 pb-24">
      <header className="pt-2">
        <div className={'text-[11px] tracking-[0.08em] ' + c.faint} style={{ fontFamily: MONO }}>DESIGN DOC · DRAFT 3 · BUILT, TESTED, MEASURED · OCT 23, 2026</div>
        <h1 className={'mt-2 text-[34px] leading-[40px] font-semibold tracking-[-0.02em] ' + c.text}>WatchDock</h1>
        <p className={'mt-2 text-[17px] leading-7 max-w-[64ch] ' + c.body}>Autonomous healing for Docker on edge devices. It watches containers from your Mac, notices crashes and slowdowns, and changes the compose file only within limits the model cannot move. Every fix is verified, and a fix that doesn’t hold is rolled back.</p>
        <nav className="mt-5 flex flex-wrap gap-1">{SECTIONS.map(([id, l]) => <button key={id} onClick={() => go(id)} className={'h-7 px-3 rounded-full text-[12.5px] ' + c.navOff}>{l}</button>)}</nav>
      </header>

      <Section id="status" n={1} title="Status" c={c} lead="The loop is wired, hardened and tested end to end. The edge agent sends telemetry over WebSocket to the bridge, which feeds the controller; the controller builds the evidence bundle, the brain proposes a fix, the guardrails check it, the deployer applies it, the controller verifies it and rolls back if needed, and the result is written to the ledger and git. Docker containers could not run in this workspace or the tested compute box, so tests use dockersim: the Docker Engine API on top of real processes, real memory and CPU limits, real OOM kills and restarts.">
        <div className={'mb-5 rounded-lg border p-4 text-[13px] leading-5 ' + c.hair + ' ' + c.body}>
<strong>Validation boundary:</strong> 15/15 scenario assertions passed on a simulated Docker runtime, with live model calls. This is not a real Docker, Traefik, SSH or Pi integration pass. Use one controller and one daemon endpoint with unique service names. Host-wide aggregate admission and recovery of in-flight incidents after controller restart are not implemented. Configure conservative caps and explicit longer verification windows for slow jobs.
</div>
<div className="grid grid-cols-2 sm:grid-cols-4 gap-y-4">
          {[[totals.runs || '–', 'lab runs'], [totals.sc || '–', 'scenario executions'], [totals.inc || '–', 'incidents handled'], ['46 / 46', 'safety tests pass']].map(([v, l]) =>
            <div key={l}><div className={'text-[24px] leading-8 font-semibold ' + c.text}>{v}</div><div className={'text-[12px] ' + c.muted}>{l}</div></div>)}
        </div>
        <div className={'mt-6 border-t ' + c.hair}>
          {[['1', 'Wire the loop', 'controller.py: live envelopes → per-service state machine → evidence bundle → brain → toolbox → deployer. `watchdock.py run` starts it with the bridge.', 'done'],
            ['2', 'Test model + Docker', 'Claude (Sonnet 5, Haiku 4.5) tested live through the full loop. Droid brain written and config-checked; live Droid and real Docker still to run on your Mac.', 'partly'],
            ['3', 'Verification + rollback', 'A policy per incident kind; a failed check restores the baseline, redeploys, commits, and retries with feedback.', 'done'],
            ['4', 'Harden guardrails', 'No shell; six tools; whole-stack validation; one exact deploy command; persistent budgets; per-directory lock; surgical edits.', 'done'],
            ['5', 'Traffic-spike demo', 'Calibrated load: 60 users with 1 s think time against 0.5 CPU replicas. Heals by scaling out in about 30 s.', 'done'],
          ].map(([n, h, d, st]) => (
            <div key={n} className={'grid grid-cols-[24px_minmax(0,1fr)_60px] sm:grid-cols-[24px_minmax(0,180px)_minmax(0,1fr)_60px] gap-x-3 py-3 border-b text-[13px] leading-5 ' + c.soft}>
              <span className={c.faint} style={{ fontFamily: MONO }}>{n}</span><span className={'font-medium ' + c.text}>{h}</span><span className={'col-start-2 col-span-2 sm:col-auto sm:col-span-1 ' + c.body}>{d}</span>
              <span className="text-right" style={{ fontFamily: MONO, fontSize: 12, color: st === 'done' ? p.ok : p.accText }}>{st}</span>
            </div>))}
        </div>
        <button onClick={openLab} className={'mt-5 h-9 px-4 rounded-full text-[13px] font-medium ' + c.primary}>Open the Lab results</button>
      </Section>

      <Section id="arch" n={2} title="Architecture" c={c} lead="Production and the lab run the same code. Only the deployer and the Docker endpoint change: ComposeDeployer over SSH in production, dockersim in the lab.">
        <div className={'rounded-xl border p-3 sm:p-4 ' + c.hair}><ArchDiagram p={p} motion={motion} /></div>
      </Section>

      <Section id="controller" n={3} title="Controller spec" c={c} lead="Each service has one state machine. Signals arriving while an incident is open are attached to it and used for verification; they never open a second incident.">
        <div className={'rounded-xl border p-3 sm:p-4 ' + c.hair}><StateMachine p={p} /></div>
        <H3>States</H3><Table c={c} cols={['STATE', 'ENTERED WHEN', 'DOES']} widths="120px minmax(0,1fr) minmax(0,1.6fr)" rows={STATES} mono={[0]} />
        <H3>Triggers</H3><Table c={c} cols={['KIND', 'RULE', 'NOTES', 'TESTED IN']} widths="90px minmax(0,1.6fr) minmax(0,1.2fr) 110px" rows={TRIGGERS} mono={[0, 3]} />
        <H3>Suppression and limits</H3><Table c={c} cols={['RULE', 'BEHAVIOUR', 'TESTED IN']} widths="minmax(0,1fr) minmax(0,2fr) 90px" rows={SUPPRESS} mono={[2]} />
      </Section>

      <Section id="verify" n={4} title="Verification and rollback" c={c} lead="Verification starts at deploy completion, in parallel with the model summary. Median latency recovery is not a tail-latency guarantee. Samples must be fresh. If latency improves by at least half and falls below the alert threshold but misses recovery, keep the fix and escalate as partial, never report full recovery.">
        <Table c={c} cols={['INCIDENT', 'PASSES WHEN', 'FAILS WHEN', 'THEN']} widths="110px minmax(0,1.5fr) minmax(0,1.2fr) minmax(0,1fr)" rows={VERIFY} mono={[0]} />
        <P>Rollback copies the snapshot of docker-compose.yml back, redeploys and commits <span style={{ fontFamily: MONO }}>roll back attempt N: reason</span>. Attempt 2 receives <span style={{ fontFamily: MONO }}>previous_attempt</span> with the change and the observed result. If attempt 2 also fails, the incident escalates with the original file restored only if rollback succeeds. A failed runtime rollback escalates explicitly with runtime state unknown and stops retries. Scale-out normally preserves existing replicas; resource-limit changes can recreate them and cause downtime.</P>
      </Section>

      <Section id="tools" n={5} title="Hardened agent surface" c={c} lead="The brain cannot run commands. It has six tools, and apply_fix is the only path to production. The shell allowlist from Draft 2 was not a security boundary, so it is gone.">
        <Table c={c} cols={['TOOL', 'INPUT', 'ENFORCED', 'RETURNS']} widths="110px 140px minmax(0,1.6fr) minmax(0,1.2fr)" rows={TOOLS} mono={[0, 1]} />
        <P>Droid integration remains experimental: the MCP transport is verified, but a live Factory session and built-in read-tool restrictions are not yet validated. The permission gate is not an OS sandbox. Droid uses the same deployment surface. DroidBrain disables Execute, exposes get_status, validate, apply_fix and escalate as an in-process MCP server named <span style={{ fontFamily: MONO }}>watchdock</span>, and its permission handler lets Droid’s own Edit tool touch docker-compose.yml and nothing else. Edits are surgical: only the bytes of the values that change move, so the git diff is one line.</P>
      </Section>

      <Section id="threats" n={6} title="Threat model" c={c}>
        <Table c={c} cols={['THREAT', 'CONTROL', 'PROVEN BY']} widths="minmax(0,1.1fr) minmax(0,1.8fr) 120px" rows={THREATS} mono={[2]} />
      </Section>

      <Section id="contracts" n={7} title="Data contracts" c={c}>
        <H3>Envelope (edge → bridge → controller, and every line of events.jsonl)</H3><CodeBlock c={c} code={ENVELOPE} label="envelope v1" />
        <H3>Evidence bundle (what the brain sees)</H3><Table c={c} cols={['FIELD', 'SOURCE', 'WHY']} widths="190px 150px minmax(0,1fr)" rows={BUNDLE} mono={[0]} />
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <div><H3>watchdock.md (multi-service)</H3><CodeBlock c={c} code={MANIFEST} label="front matter" maxH={420} /></div>
          <div><H3>Incident record (.watchdock/incidents.jsonl)</H3><CodeBlock c={c} code={INCIDENT} label="incident" maxH={420} /></div>
        </div>
      </Section>

      <Section id="lab" n={8} title="Test matrix" c={c} lead={best ? `Featured run: ${best.label} · ${best.model} · ${best.pass}/${best.total} pass · median recovery ${fmtS(best.median_mttr_s)}. Every row is a real execution through the full production code path; the Lab page has the timeline, transcript and git log for each.` : 'Loading lab results…'}>
        {latest ? (
          <div className="overflow-x-auto"><div className="min-w-[720px]">
            <div className={'grid grid-cols-[44px_minmax(0,1.6fr)_minmax(0,1.4fr)_110px_70px] gap-3 py-2 text-[11px] tracking-[0.06em] border-b ' + c.soft + ' ' + c.faint} style={{ fontFamily: MONO }}>
              <span>ID</span><span>SCENARIO</span><span>WHAT HAPPENED</span><span>OUTCOME</span><span>VERDICT</span></div>
            {Object.values(latest).sort((a, b) => a.id.localeCompare(b.id)).map((s) => (
              <div key={s.id} className={'grid grid-cols-[44px_minmax(0,1.6fr)_minmax(0,1.4fr)_110px_70px] gap-3 py-2.5 border-b text-[12.5px] leading-5 ' + c.soft}>
                <span className={c.faint} style={{ fontFamily: MONO }}>{s.id}</span>
                <span><span className={c.text}>{s.title}</span><span className={'block text-[12px] ' + c.muted}>{s.story}</span></span>
                <span className={c.body}>{s.grade.summary}</span>
                <span style={{ fontFamily: MONO, fontSize: 11.5, color: (s.incidents || []).some((i) => i.outcome === 'RESOLVED') ? p.ok : (s.incidents || []).length ? p.accText : p.muted }}>{(s.incidents || []).map((i) => i.outcome.toLowerCase()).join(' + ') || 'no incident'}</span>
                <span style={{ color: s.grade.verdict === 'pass' ? p.ok : p.accText }}>{s.grade.verdict}</span>
              </div>))}
          </div></div>) : <P>Loading…</P>}
        <P>How faithful is dockersim? It is real: processes, HTTP, the proxy, load, the docker SDK and telemetry.py, events, stats, logs, the OOM kill on RSS over the limit, the emulated CPU quota (100 ms period, stop and continue), restart backoff, and compose reconcile semantics. These are not kernel cgroups, real Traefik, or complete Compose semantics. It is emulated: namespaces, the image build, and the cgroup accounting granularity (10 ms CPU ticks, RSS rather than page cache).</P>
      </Section>

      <Section id="findings" n={9} title="What testing changed" c={c} lead="Each item was found by running the scenarios, fixed, and confirmed in a later run.">
        <div className={'border-t ' + c.hair}>
          {(findings || []).map((f, k) => (
            <div key={k} className={'py-3.5 border-b grid gap-x-4 sm:grid-cols-[28px_minmax(0,1fr)_150px] ' + c.hair}>
              <span className={'text-[12px] pt-[2px] ' + c.faint} style={{ fontFamily: MONO }}>{String(k + 1).padStart(2, '0')}</span>
              <div className="min-w-0"><div className={'text-[14px] font-semibold ' + c.text}>{f.title}</div><p className={'mt-1 text-[13px] leading-5 ' + c.body}>{f.body}</p></div>
              <div className={'text-[12px] sm:text-right ' + c.muted} style={{ fontFamily: MONO }}>{f.metric || ''}</div>
            </div>))}
        </div>
      </Section>

      <Section id="demo" n={10} title="Demo · the traffic spike" c={c} lead="Calibrated in the lab. One web replica at 0.5 CPU serves about 25 req/s (20 ms of CPU per request). Sixty users with a 1 s think time ask for about 57 req/s, so latency climbs past a second. Two replicas bring it to about 0.2 s and three to about 40 ms. Zero think time (plain `ab -c`) keeps every replica saturated, and the leftover tail latency makes verification slow.">
        <Table c={c} cols={['T', 'BEAT', 'TERMINAL', 'FOXGLOVE']} widths="48px 110px minmax(0,1.5fr) minmax(0,1.2fr)" rows={[
          ['0:00', 'Baseline', 'web · 1 replica · TTFB 22 ms · link live', 'flat TTFB, replicas 1'],
          ['0:20', 'Chaos', '60 users start (loadgen.py --concurrency 60 --think 1)', 'TTFB past 800 ms within about 4 s'],
          ['0:25', 'Detect', 'OPEN: TTFB p50 1489 ms > 800 ms (3/5 probes)', 'coral dots above the trigger line'],
          ['0:27', 'Diagnose', 'Claude: CPU at its 50% limit, stateless, scale allowed → set_resources(replicas 3)', '/wd/agent log scrolls'],
          ['0:36', 'Apply', 'guardrails ✓ replicas 1 → 3 · commit · docker compose up -d', 'replicas steps 1 → 3'],
          ['0:45', 'Recover', 'RESOLVED · MTTR about 30 s · ledger row written', 'TTFB back to tens of ms under the same load'],
        ]} mono={[0]} />
      </Section>

      <Section id="runbook" n={11} title="Runbook" c={c}>
        <div className="grid gap-4">{RUNBOOK.map(([h, code]) => <CodeBlock key={h} c={c} code={code} label={h} />)}</div>
      </Section>

      <Section id="code" n={12} title="Code" c={c} lead="Everything in this section ran in this workspace: the controller, brains, toolbox, guardrails, patcher, deployer, edge agent, bridge, dockersim, the lab runner and the safety tests.">
        <div className="flex flex-wrap items-center gap-1">
          {files.map((f, i) => <button key={f.name} onClick={() => setTab(i)} className={'h-7 px-3 rounded-full text-[12px] ' + (i === tab ? c.tabOn : c.tabOff)} style={{ fontFamily: MONO }}>{f.name}</button>)}
          {zip ? <a href={zip} download="watchdock.zip" className={'ml-auto h-8 px-4 rounded-full text-[12.5px] font-medium inline-flex items-center ' + c.primary}>Download all (.zip)</a> : null}
        </div>
        {files[tab] ? <div className="mt-3"><CodeBlock code={files[tab].code} label={files[tab].name + ' · ' + files[tab].note} c={c} maxH={560} /></div> : null}
      </Section>

      <Section id="left" n={13} title="What’s left" c={c}>
        <div className={'border-t ' + c.hair}>{LEFT.map(([t, h, body], i) => (
          <div key={h} className={'py-4 border-b grid gap-x-4 gap-y-1 sm:grid-cols-[28px_minmax(0,1fr)] ' + c.hair}>
            <span className={'text-[12px] pt-[3px] ' + c.faint} style={{ fontFamily: MONO }}>{String(i + 1).padStart(2, '0')}</span>
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className={'text-[14.5px] font-semibold ' + c.text}>{h}</span><Tag t={t} p={p} /></div>
              <p className={'mt-1 text-[13.5px] leading-[21px] max-w-[78ch] ' + c.body}>{body}</p></div>
          </div>))}</div>
      </Section>
    </div></div>
  );
}
