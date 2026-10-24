// WatchDock Lab: results of the scenario suite (dockersim + real edge agent + real Claude). Data: ./runs, ./index, ./findings
import * as echarts from 'echarts@5';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
function pal(isLight) {
  return isLight ? { bg: '#ffffff', band: '#f7f7f8', stroke: '#d4d4d8', hair: '#e4e4e7', ink: '#18181b', muted: '#52525b', faint: '#8a8a93',
    acc: '#E8654A', accText: '#B8452D', accSoft: 'rgba(232,101,74,0.10)', amberSoft: 'rgba(217,160,40,0.13)', redSoft: 'rgba(200,60,60,0.10)',
    ok: '#2f7d4f', okSoft: 'rgba(47,125,79,0.10)', blue: '#3b6fb6', load: '#9aa3b2' }
  : { bg: '#18181b', band: '#121214', stroke: '#3f3f46', hair: '#27272a', ink: '#f4f4f5', muted: '#a1a1aa', faint: '#71717a',
    acc: '#F07860', accText: '#F59A86', accSoft: 'rgba(240,120,96,0.13)', amberSoft: 'rgba(230,180,70,0.12)', redSoft: 'rgba(230,90,90,0.12)',
    ok: '#6cc28a', okSoft: 'rgba(108,194,138,0.12)', blue: '#7fa8e0', load: '#6b7280' };
}
function cls(isLight) {
  return isLight ? { text: 'text-zinc-900', body: 'text-zinc-700', muted: 'text-zinc-500', faint: 'text-zinc-400', hair: 'border-zinc-200', soft: 'border-zinc-100',
    row: 'hover:bg-zinc-50', rowOn: 'bg-zinc-100', pillOn: 'bg-zinc-900 text-white', pillOff: 'text-zinc-600 hover:bg-zinc-100', code: 'bg-zinc-50' }
  : { text: 'text-zinc-100', body: 'text-zinc-300', muted: 'text-zinc-400', faint: 'text-zinc-500', hair: 'border-zinc-800', soft: 'border-zinc-800/70',
    row: 'hover:bg-white/[0.03]', rowOn: 'bg-white/[0.06]', pillOn: 'bg-zinc-100 text-zinc-900', pillOff: 'text-zinc-400 hover:bg-white/[0.06]', code: 'bg-zinc-900/60' };
}
const OUT = { RESOLVED: 'ok', ESCALATED: 'acc' };
const roll = (pts, w) => pts.map((pt, i) => { const win = pts.slice(Math.max(0, i - w + 1), i + 1).map((x) => x[1]).sort((a, b) => a - b); return [pt[0], win[Math.floor(win.length / 2)]]; });
const fmt = (s) => (s == null ? '–' : s < 10 ? s.toFixed(1) + ' s' : Math.round(s) + ' s');

function Chip({ text, tone, p }) {
  const c = tone === 'ok' ? [p.ok, p.okSoft] : tone === 'acc' ? [p.accText, p.accSoft] : [p.muted, 'transparent'];
  return <span className="inline-flex items-center h-5 px-2 rounded-full text-[10.5px] font-medium whitespace-nowrap"
    style={{ fontFamily: MONO, color: c[0], background: c[1], boxShadow: tone ? 'none' : 'inset 0 0 0 1px ' + p.stroke }}>{text}</span>;
}

function changeOf(sc) {
  const out = [];
  (sc.incidents || []).forEach((i) => (i.attempts || []).forEach((a) => {
    if (a.applied && a.applied.changes) out.push(Object.entries(a.applied.changes).map(([k, v]) => `${k} ${v[0]}→${v[1]}`).join(', ') + (a.rollback ? ' ↺' : ''));
  }));
  return out.join(' · ') || '—';
}

function phases(inc) {
  if (!inc) return [];
  const st = (inc.timeline || []).filter((x) => x.state);
  const segs = [{ k: 'detect', a: inc.first_signal_at, b: inc.opened_at }];
  for (let i = 0; i < st.length; i++) {
    const s = st[i]; const nxt = st[i + 1];
    if (['RESOLVED', 'ESCALATED', 'SUPPRESSED'].includes(s.state)) break;
    const end = nxt ? nxt.t : inc.closed_at;
    const k = { OPEN: 'open', COLLECTING: 'collect', QUEUED: 'queued', DIAGNOSING: 'diagnose', APPLYING: 'apply', VERIFYING: 'verify', ROLLING_BACK: 'rollback' }[s.state];
    if (k && k !== 'open' && end != null) segs.push({ k, a: s.t, b: end });
  }
  return segs.filter((s) => s.b > s.a);
}
const PH = { detect: ['detect', 'faint'], queued: ['queued', 'faint'], collect: ['collect', 'faint'], diagnose: ['diagnose', 'acc'], apply: ['apply', 'ink'], verify: ['verify', 'ok'], rollback: ['roll back', 'red'] };

function PhaseBar({ inc, p, c }) {
  const segs = phases(inc);
  if (!segs.length) return null;
  const total = segs.reduce((s, x) => s + (x.b - x.a), 0);
  const color = (k) => ({ faint: p.stroke, acc: p.acc, ink: p.ink, ok: p.ok, red: '#c24b4b' })[PH[k][1]];
  return (
    <div>
      <div className="flex w-full h-2.5 rounded-full overflow-hidden gap-[2px]">
        {segs.map((s, i) => <div key={i} title={`${PH[s.k][0]} ${fmt(s.b - s.a)}`} style={{ flex: Math.max(s.b - s.a, total * 0.015) + ' 1 0', background: color(s.k), opacity: s.k === 'apply' ? 0.8 : 1 }} />)}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segs.map((s, i) => (
          <span key={i} className={'text-[11.5px] inline-flex items-center gap-1.5 ' + c.muted}>
            <span className="w-2 h-2 rounded-full" style={{ background: color(s.k) }} />{PH[s.k][0]} <span className={c.text} style={{ fontFamily: MONO }}>{fmt(s.b - s.a)}</span>
          </span>))}
        <span className={'text-[11.5px] ' + c.muted}>· total <span className={c.text} style={{ fontFamily: MONO }}>{fmt(total)}</span></span>
      </div>
    </div>
  );
}

function Timeline({ sc, svc, p, isLight }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!ref.current || !sc) return;
    const chart = echarts.init(ref.current, null, { renderer: 'svg' });
    const tl = (sc.timeline || {})[svc] || { probe: [], summary: [], events: [] };
    const inc = (sc.incidents || []).filter((i) => i.service === svc);
    const latency = tl.probe && tl.probe.length > 0;
    const proj = Object.keys(sc.load || {}).find((k) => true);
    const loadRows = (sc.load || {})[(inc[0] && inc[0].project) || proj] || [];
    const tmax = Math.max(10, ...tl.summary.map((r) => r[0]), ...tl.probe.map((r) => r[0]));
    const areas = [], lines = [];
    inc.forEach((i) => {
      const st = (i.timeline || []).filter((x) => x.state);
      st.forEach((s, k) => {
        const nxt = st[k + 1]; const end = nxt ? nxt.t : (i.closed_at || tmax);
        const color = { DIAGNOSING: p.accSoft, VERIFYING: p.okSoft, ROLLING_BACK: p.redSoft, APPLYING: p.amberSoft, COLLECTING: p.amberSoft }[s.state];
        if (color && end > s.t) areas.push([{ xAxis: s.t, itemStyle: { color }, name: s.state === 'DIAGNOSING' ? 'diagnose' : s.state === 'VERIFYING' ? 'verify' : s.state === 'ROLLING_BACK' ? 'roll back' : '' }, { xAxis: end }]);
        if (['OPEN', 'RESOLVED', 'ESCALATED'].includes(s.state) || (s.state === 'APPLYING')) lines.push({ xAxis: s.t, name: s.state === 'APPLYING' ? (s.note || 'apply').replace(/ MiB/g, '') : s.state.toLowerCase(),
          lineStyle: { color: s.state === 'RESOLVED' ? p.ok : s.state === 'ESCALATED' || s.state === 'OPEN' ? p.acc : p.ink, type: s.state === 'APPLYING' ? 'solid' : 'dashed', width: 1.2 } });
      });
    });
    (sc.load_start || []).slice(0, 1).forEach((t) => lines.push({ xAxis: t, name: 'load on', lineStyle: { color: p.faint, type: 'dotted' } }));
    const memLimit = tl.summary.map((r) => [r[0], r[4]]);
    const ooms = tl.events.filter((e) => e[1] === 'oom').map((e) => {
      const near = tl.summary.reduce((b, r) => (Math.abs(r[0] - e[0]) < Math.abs(b[0] - e[0]) ? r : b), tl.summary[0] || [0, 0, 0, 0, 0]);
      return [e[0], near[4] || 0];
    });
    const top = latency ? [
      { name: 'probe TTFB', type: 'line', data: tl.probe.map((r) => [r[0], r[1]]), symbol: 'circle', symbolSize: 4, showSymbol: true, lineStyle: { width: 1.6, color: p.ink }, itemStyle: { color: p.ink }, z: 4,
        markArea: { silent: true, label: { color: p.faint, fontSize: 10, fontFamily: MONO, position: 'insideTop' }, data: areas },
        markLine: { silent: true, symbol: 'none', label: { color: p.muted, fontSize: 10, fontFamily: MONO, formatter: '{b}', position: 'insideEndTop' }, data: [...lines,
          { yAxis: 800, name: 'trigger 800', lineStyle: { color: p.acc, type: [5, 4] }, label: { position: 'insideEndTop', formatter: 'trigger 800 ms', color: p.accText } },
          { yAxis: 500, name: 're-arm 500', lineStyle: { color: p.faint, type: [2, 4] }, label: { position: 'insideEndBottom', formatter: 're-arm 500 ms' } }] } },
      { name: 'user latency p50', type: 'line', data: roll(loadRows.map((r) => [r[0] + 0.5, r[2]]), 3), symbol: 'none', lineStyle: { width: 1.3, color: p.blue, opacity: 0.85 }, z: 3 },
    ] : [
      { name: 'memory (MiB)', type: 'line', data: tl.summary.map((r) => [r[0], r[5] || r[3]]), symbol: 'none', lineStyle: { width: 1.6, color: p.ink }, z: 4, areaStyle: { color: p.ink, opacity: 0.04 },
        markArea: { silent: true, label: { color: p.faint, fontSize: 10, fontFamily: MONO, position: 'insideTop' }, data: areas },
        markLine: { silent: true, symbol: 'none', label: { color: p.muted, fontSize: 10, fontFamily: MONO, formatter: '{b}', position: 'insideEndTop' }, data: lines } },
      { name: 'memory limit', type: 'line', step: 'end', data: memLimit, symbol: 'none', lineStyle: { width: 1.2, color: p.acc, type: [5, 4] }, z: 3 },
      { name: 'OOM kill', type: 'scatter', data: ooms, symbol: 'triangle', symbolSize: 9, itemStyle: { color: p.acc }, z: 5 },
    ];
    const bottom = [
      { name: 'replicas', type: 'line', step: 'end', xAxisIndex: 1, yAxisIndex: 1, data: tl.summary.map((r) => [r[0], r[1]]), symbol: 'none', lineStyle: { width: 1.6, color: p.ink }, areaStyle: { color: p.ink, opacity: 0.05 } },
      { name: 'CPU %', type: 'line', xAxisIndex: 1, yAxisIndex: 2, data: tl.summary.map((r) => [r[0], r[2]]), symbol: 'none', lineStyle: { width: 1, color: p.acc, opacity: 0.75 } },
    ];
    const axisLine = { lineStyle: { color: p.stroke } }, axisLabel = { color: p.faint, fontSize: 10, fontFamily: MONO };
    chart.setOption({
      animation: false, backgroundColor: 'transparent',
      grid: [{ left: 52, right: 44, top: 30, height: '56%' }, { left: 52, right: 44, top: '73%', height: '17%' }],
      legend: { top: 0, right: 8, itemWidth: 14, itemHeight: 8, textStyle: { color: p.muted, fontSize: 11 }, data: latency ? ['probe TTFB', 'user latency p50', 'replicas', 'CPU %'] : ['memory (MiB)', 'memory limit', 'OOM kill', 'replicas', 'CPU %'] },
      tooltip: { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: p.stroke } }, backgroundColor: p.bg, borderColor: p.hair, textStyle: { color: p.ink, fontSize: 11 },
        valueFormatter: (v) => (v == null ? '–' : Math.round(v * 10) / 10) },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      xAxis: [{ type: 'value', min: 0, max: Math.ceil(tmax), axisLine, axisLabel: { show: false }, splitLine: { show: false }, axisTick: { show: false } },
              { type: 'value', gridIndex: 1, min: 0, max: Math.ceil(tmax), axisLine, axisLabel: { ...axisLabel, formatter: '{value}s' }, splitLine: { show: false } }],
      yAxis: [{ type: 'value', name: latency ? 'ms' : 'MiB', nameTextStyle: axisLabel, axisLabel, splitLine: { lineStyle: { color: p.hair } }, max: latency ? (v) => Math.max(1300, Math.ceil(v.max / 200) * 200) : (v) => Math.ceil(v.max / 64) * 64 },
              { type: 'value', gridIndex: 1, name: 'replicas', nameTextStyle: axisLabel, minInterval: 1, min: 0, max: (v) => Math.max(3, v.max), axisLabel, splitLine: { lineStyle: { color: p.hair } } },
              { type: 'value', gridIndex: 1, name: 'CPU %', nameTextStyle: axisLabel, min: 0, axisLabel, splitLine: { show: false }, position: 'right' }],
      series: [...top, ...bottom],
    });
    const ro = new ResizeObserver(() => chart.resize()); ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [sc, svc, isLight]);
  return <div ref={ref} className="w-full" style={{ height: 360 }} />;
}

function Transcript({ inc, p, c }) {
  if (!inc) return null;
  const rows = (inc.timeline || []).filter((x) => x.state || x.tool || x.thought);
  return (
    <div className={'border-t ' + c.hair}>
      {rows.map((r, i) => (
        <div key={i} className={'grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 py-2 border-b text-[12.5px] leading-5 ' + c.soft}>
          <span className={c.faint} style={{ fontFamily: MONO }}>{r.t.toFixed(1)}s</span>
          {r.state ? (
            <div className="flex flex-wrap items-baseline gap-2 min-w-0">
              <Chip text={r.state} tone={r.state === 'RESOLVED' ? 'ok' : ['OPEN', 'ESCALATED', 'ROLLING_BACK'].includes(r.state) ? 'acc' : null} p={p} />
              <span className={'min-w-0 ' + c.body}>{r.note}</span>
            </div>
          ) : r.tool ? (
            <div className="min-w-0" style={{ fontFamily: MONO, fontSize: 12 }}>
              <span style={{ color: p.accText }}>{r.tool}</span><span className={c.muted}>({JSON.stringify(r.args).slice(1, -1).slice(0, 160)})</span>
              {r.result && Object.keys(r.result).length ? <div className={c.muted + ' truncate'}>→ {JSON.stringify(r.result).slice(0, 220)}</div> : null}
            </div>
          ) : (
            <div className={'min-w-0 whitespace-pre-wrap ' + c.text}>{r.thought}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function CompareChart({ runs, p }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!ref.current || runs.length < 2) return;
    const chart = echarts.init(ref.current, null, { renderer: 'svg' });
    const ids = Array.from(new Set(runs.flatMap((r) => Object.keys(r.scenarios)))).sort();
    const val = (r, id) => { const s = r.scenarios[id]; if (!s) return null; const res = (s.incidents || []).filter((i) => i.outcome === 'RESOLVED'); return res.length ? res[0].metrics.mttr_s : null; };
    const keep = ids.filter((id) => runs.some((r) => val(r, id) != null));
    const colors = [p.stroke, p.ink, p.acc, p.blue];
    chart.setOption({ animation: false, grid: { left: 44, right: 12, top: 30, bottom: 26 }, legend: { top: 0, textStyle: { color: p.muted, fontSize: 11 } },
      tooltip: { trigger: 'axis', backgroundColor: p.bg, borderColor: p.hair, textStyle: { color: p.ink, fontSize: 11 } },
      xAxis: { type: 'category', data: keep, axisLabel: { color: p.faint, fontFamily: MONO, fontSize: 10 }, axisLine: { lineStyle: { color: p.stroke } } },
      yAxis: { type: 'value', name: 'MTTR s', nameTextStyle: { color: p.faint, fontSize: 10 }, axisLabel: { color: p.faint, fontSize: 10 }, splitLine: { lineStyle: { color: p.hair } } },
      series: runs.map((r, k) => ({ name: r.meta.label, type: 'bar', barMaxWidth: 16, itemStyle: { color: colors[k % colors.length], borderRadius: [3, 3, 0, 0] }, data: keep.map((id) => val(r, id)) })) });
    const ro = new ResizeObserver(() => chart.resize()); ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [runs, p]);
  return <div ref={ref} className="w-full" style={{ height: 240 }} />;
}

function UserDiv() {
  const { theme } = useTheme();
  const isLight = theme !== 'dark';
  const p = pal(isLight), c = cls(isLight);
  const [index, setIndex] = React.useState(null);
  const [runId, setRunId] = React.useState(null);
  const [runs, setRuns] = React.useState({});
  const [sel, setSel] = React.useState('s01');
  const [svcSel, setSvcSel] = React.useState(null);
  const [findings, setFindings] = React.useState(null);

  React.useEffect(() => {
    const addr = relPathAddress('index'); if (!addr) return;
    return FS.onValue(FS.ref(FS.db, addr), (s) => {
      const v = s.val() || {}; const list = Object.values(v).sort((a, b) => a.started_ms - b.started_ms);
      setIndex(list); setRunId((cur) => cur || (list.find((m) => m.featured)?.id || (list.length ? list[list.length - 1].id : null)));
    });
  }, []);
  React.useEffect(() => {
    const addr = relPathAddress('findings'); if (!addr) return;
    return FS.onValue(FS.ref(FS.db, addr), (s) => { try { setFindings(s.val() ? JSON.parse(s.val()) : null); } catch (e) { setFindings(null); } });
  }, []);
  React.useEffect(() => {
    if (!index) return;
    const unsubs = index.map((m) => FS.onValue(FS.ref(FS.db, relPathAddress('runs', m.id)), (s) => {
      const v = s.val(); if (!v) return;
      const scenarios = {}; Object.entries(v.scenarios || {}).forEach(([k, j]) => { try { scenarios[k] = JSON.parse(j); } catch (e) {} });
      setRuns((r) => ({ ...r, [m.id]: { meta: v.meta, scenarios } }));
    }));
    return () => unsubs.forEach((u) => u());
  }, [index && index.map((m) => m.id).join(',')]);

  const run = runs[runId];
  const ids = run ? Object.keys(run.scenarios).sort() : [];
  const sc = run && (run.scenarios[sel] || run.scenarios[ids[0]]);
  const svcs = sc ? Object.keys(sc.timeline || {}) : [];
  const svc = svcSel && svcs.includes(svcSel) ? svcSel : svcs[0];
  const inc = sc ? (sc.incidents || []).filter((i) => i.service === svc) : [];
  const allRuns = (index || []).map((m) => runs[m.id]).filter(Boolean);

  if (!index) return <div className={'w-full py-16 text-center text-[13px] ' + c.muted}>Loading lab results…</div>;
  if (!index.length) return <div className={'w-full py-16 text-center text-[13px] ' + c.muted}>No runs yet.</div>;
  const m = run ? run.meta : index[index.length - 1];

  return (
    <div className="w-full">
      <div className="max-w-[1100px] mx-auto px-4 sm:px-6 pb-24">
        <header className="pt-2">
          <div className={'text-[11px] tracking-[0.08em] ' + c.faint} style={{ fontFamily: MONO }}>WATCHDOCK LAB · SCENARIO SUITE</div>
          <h1 className={'mt-2 text-[30px] leading-[36px] font-semibold tracking-[-0.02em] ' + c.text}>Does it actually heal?</h1>
          <p className={'mt-2 text-[15px] leading-6 max-w-[74ch] ' + c.body}>Each scenario breaks a real deployment on purpose and lets WatchDock handle it end to end: the edge agent sends telemetry over WebSocket to the bridge, which feeds the controller; the controller hands the evidence to Claude, guardrails are checked, the fix is applied and verified. Containers run on dockersim, a Docker-compatible runtime with real processes, memory and CPU limits, OOM kills and restarts.</p>
          <div className="mt-5 flex flex-wrap items-center gap-1">
            {index.map((r) => <button key={r.id} onClick={() => setRunId(r.id)} className={'h-7 px-3 rounded-full text-[12px] ' + (r.id === runId ? c.pillOn : c.pillOff)}>{r.label}</button>)}
          </div>
          <div className="mt-6 grid grid-cols-2 sm:grid-cols-5 gap-y-4">
            {[[`${m.pass}/${m.total}`, 'scenarios pass'], [m.incidents, 'incidents handled'], [fmt(m.median_mttr_s), 'median MTTR (healed)'], [fmt(m.median_brain_s), 'median model time'], [m.model, m.blind ? 'model · hidden test config' : 'model · test config visible']].map(([v, l]) => (
              <div key={l} className="min-w-0"><div className={'break-words text-[22px] leading-7 font-semibold tracking-[-0.01em] ' + c.text} style={{ fontFamily: typeof v === 'string' && v.includes('claude') ? undefined : undefined }}>{v}</div><div className={'text-[12px] ' + c.muted}>{l}</div></div>
            ))}
          </div>
          {m.note ? <p className={'mt-3 text-[12.5px] ' + c.muted}>{m.note}</p> : null}
        </header>

        <section className={'mt-10 border-t pt-6 ' + c.hair}>
          <h2 className={'text-[18px] font-semibold ' + c.text}>Scenario matrix</h2>
          <div className="mt-3 overflow-x-auto">
            <div className="min-w-[760px]">
              <div className={'grid grid-cols-[44px_minmax(0,2.2fr)_120px_110px_74px_minmax(0,1.6fr)] gap-3 py-2 text-[11px] tracking-[0.06em] border-b ' + c.soft + ' ' + c.faint} style={{ fontFamily: MONO }}>
                <span>ID</span><span>SCENARIO</span><span>OUTCOME</span><span>VERDICT</span><span>MTTR</span><span>CHANGE</span>
              </div>
              {ids.map((id) => { const s = run.scenarios[id]; const outs = (s.incidents || []).map((i) => i.outcome);
                return (
                  <button key={id} onClick={() => { setSel(id); setSvcSel(null); }} className={'w-full text-left grid grid-cols-[44px_minmax(0,2.2fr)_120px_110px_74px_minmax(0,1.6fr)] gap-3 py-2.5 border-b items-center text-[13px] ' + c.soft + ' ' + (id === sel ? c.rowOn : c.row)}>
                    <span className={c.faint} style={{ fontFamily: MONO }}>{id}</span>
                    <span className={'truncate ' + c.text}>{s.title}</span>
                    <span className="flex flex-wrap gap-1">{outs.length ? outs.map((o, k) => <Chip key={k} text={o.toLowerCase()} tone={OUT[o]} p={p} />) : <Chip text="no incident" p={p} />}</span>
                    <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full" style={{ background: s.grade.verdict === 'pass' ? p.ok : p.acc }} /><span className={c.body}>{s.grade.verdict}</span>{s.grade.quality && s.grade.quality !== 'ideal' ? <span className={c.faint} title={s.grade.quality}>*</span> : null}</span>
                    <span className={c.body} style={{ fontFamily: MONO }}>{(s.incidents || []).filter((i) => i.outcome === 'RESOLVED').map((i) => fmt(i.metrics.mttr_s)).join(' · ') || '–'}</span>
                    <span className={'truncate ' + c.muted} style={{ fontFamily: MONO, fontSize: 12 }}>{changeOf(s)}</span>
                  </button>);
              })}
              {Object.values(m.errors || {}).map((id) => <div key={id} className={'grid grid-cols-[44px_minmax(0,1fr)] gap-3 py-2.5 border-b text-[13px] ' + c.soft}><span className={c.faint} style={{ fontFamily: MONO }}>{id}</span><span className={c.muted}>lab error in this run (fixed and covered in a later run)</span></div>)}
            </div>
          </div>
        </section>

        {sc ? (
          <section className={'mt-10 border-t pt-6 ' + c.hair}>
            <div className="flex flex-wrap items-baseline gap-3">
              <span className={'text-[12px] ' + c.faint} style={{ fontFamily: MONO }}>{sc.id}</span>
              <h2 className={'text-[20px] font-semibold tracking-[-0.01em] ' + c.text}>{sc.title}</h2>
              <span className="ml-auto flex gap-1">{svcs.length > 1 ? svcs.map((s) => <button key={s} onClick={() => setSvcSel(s)} className={'h-6 px-2.5 rounded-full text-[11.5px] ' + (s === svc ? c.pillOn : c.pillOff)} style={{ fontFamily: MONO }}>{s}</button>) : null}</span>
            </div>
            <p className={'mt-1.5 text-[14px] leading-6 max-w-[80ch] ' + c.body}>{sc.story}</p>
            <div className={'mt-2 text-[12.5px] ' + c.muted}>Verdict: <span className={c.text}>{sc.grade.verdict}</span>{sc.grade.quality ? ' · ' + sc.grade.quality : ''} · {sc.grade.note}</div>
            {sc.review ? <p className={'mt-3 text-[13px] leading-5 ' + c.body}><strong>Review:</strong> {sc.review}</p> : null}
            <div className={'mt-4 rounded-xl border p-2 sm:p-3 ' + c.hair}><Timeline sc={sc} svc={svc} p={p} isLight={isLight} /></div>
            {inc.map((i) => (
              <div key={i.id} className="mt-6">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip text={i.outcome || i.state} tone={OUT[i.outcome]} p={p} />
                  <span className={'text-[13px] ' + c.text} style={{ fontFamily: MONO }}>{i.id}</span>
                  <span className={'text-[12.5px] ' + c.muted}>{i.kind} · {i.attempts.length} attempt{i.attempts.length === 1 ? '' : 's'} · {i.attempts.map((a) => (a.brain || {}).brain || 'none').join(', ')}</span>
                </div>
                <div className="mt-3"><PhaseBar inc={i} p={p} c={c} /></div>
                {i.reason ? <p className={'mt-3 text-[13px] leading-5 max-w-[86ch] ' + c.body}><span className={c.muted}>Escalation: </span>{i.reason}</p> : null}
                <div className="mt-4"><Transcript inc={i} p={p} c={c} /></div>
              </div>
            ))}
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <div>
                <div className={'text-[12px] mb-1.5 ' + c.faint} style={{ fontFamily: MONO }}>FINAL STATE</div>
                {Object.entries(sc.final || {}).map(([s, f]) => <div key={s} className={'text-[12.5px] ' + c.body} style={{ fontFamily: MONO }}>{s}: replicas {f.replicas} · memory {f.memory_mb} MiB · cpus {f.cpus ?? '–'} · running {f.running}</div>)}
                <div className={'text-[12px] mt-4 mb-1.5 ' + c.faint} style={{ fontFamily: MONO }}>LEDGER (written by code)</div>
                {Object.values(sc.ledger || {}).flat().map((l, k) => <div key={k} className={'text-[11.5px] leading-5 ' + c.muted} style={{ fontFamily: MONO }}>{l}</div>)}
              </div>
              <div>
                <div className={'text-[12px] mb-1.5 ' + c.faint} style={{ fontFamily: MONO }}>GIT LOG</div>
                <pre className={'text-[11.5px] leading-5 rounded-lg p-3 overflow-x-auto ' + c.code + ' ' + c.muted} style={{ fontFamily: MONO }}>{Object.values(sc.git || {}).flat().join('\n')}</pre>
              </div>
            </div>
          </section>
        ) : null}

        {allRuns.length > 1 ? (
          <section className={'mt-10 border-t pt-6 ' + c.hair}>
            <h2 className={'text-[18px] font-semibold ' + c.text}>Runs compared</h2>
            <p className={'mt-1 text-[13px] ' + c.muted}>Time to recovery for healed incidents, per scenario and run.</p>
            <div className="mt-3"><CompareChart runs={allRuns} p={p} /></div>
            <div className="mt-3 overflow-x-auto"><div className="min-w-[640px]">
              <div className={'grid grid-cols-[minmax(0,1.6fr)_repeat(5,minmax(0,1fr))] gap-3 py-2 text-[11px] tracking-[0.06em] border-b ' + c.soft + ' ' + c.faint} style={{ fontFamily: MONO }}>
                <span>RUN</span><span>PASS</span><span>MEDIAN MTTR</span><span>MEDIAN MODEL</span><span>MEDIAN TOKENS</span><span>MODE</span></div>
              {allRuns.map((r) => <div key={r.meta.id} className={'grid grid-cols-[minmax(0,1.6fr)_repeat(5,minmax(0,1fr))] gap-3 py-2 border-b text-[12.5px] ' + c.soft}>
                <span className={c.text}>{r.meta.label}</span><span className={c.body}>{r.meta.pass}/{r.meta.total}</span><span className={c.body}>{fmt(r.meta.median_mttr_s)}</span>
                <span className={c.body}>{fmt(r.meta.median_brain_s)}</span><span className={c.body}>{r.meta.median_tokens ? (r.meta.median_tokens / 1000).toFixed(1) + 'k' : '–'}</span><span className={c.muted}>{r.meta.blind ? 'blind' : 'knobs visible'}</span></div>)}
            </div></div>
          </section>
        ) : null}

        {findings ? (
          <section className={'mt-10 border-t pt-6 ' + c.hair}>
            <h2 className={'text-[18px] font-semibold ' + c.text}>What we learned and changed</h2>
            <div className={'mt-3 border-t ' + c.hair}>
              {findings.map((f, k) => (
                <div key={k} className={'py-3.5 border-b grid gap-x-4 sm:grid-cols-[28px_minmax(0,1fr)_minmax(0,140px)] ' + c.hair}>
                  <span className={'text-[12px] pt-[2px] ' + c.faint} style={{ fontFamily: MONO }}>{String(k + 1).padStart(2, '0')}</span>
                  <div className="min-w-0"><div className={'text-[14px] font-semibold ' + c.text}>{f.title}</div><p className={'mt-1 text-[13px] leading-5 ' + c.body}>{f.body}</p></div>
                  <div className={'text-[12px] sm:text-right ' + c.muted} style={{ fontFamily: MONO }}>{f.metric || ''}</div>
                </div>))}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
