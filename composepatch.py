"""Surgical docker-compose.yml edits: change only the bytes of the values that change.

ruamel.yaml is used only to locate values (line, column); the original text is patched in place,
so comments, list indentation and quoting elsewhere stay byte-identical and git diffs show one line.
Missing keys are inserted with the surrounding indentation. Flow-style maps fall back to a ruamel dump.
"""
from __future__ import annotations

import io, re

from ruamel.yaml import YAML

_SCALAR = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^\s,}\]#]+')


def _cpus(v) -> str:
    v = float(v)
    return f"{v:.1f}" if v == int(v) else f"{v:g}"


def _load(text):
    y = YAML(); y.preserve_quotes = True
    return y.load(text)


def _fmt(old_token: str | None, new: str) -> str:
    if old_token and old_token[0] in "\"'":
        return old_token[0] + new + old_token[0]
    return new


def _set_value(lines, m, key, new):
    ln, col = m.lc.value(key)
    tok = _SCALAR.match(lines[ln], col)
    old = tok.group(0) if tok else ""
    lines[ln] = lines[ln][:col] + _fmt(old, new) + lines[ln][col + len(old):]


def _child_indent(m, default):
    for k in m:
        return m.lc.key(k)[1]
    return default


def _insert(lines, parent_map, parent_key, path_keys, value, quote=False):
    """Insert path_keys (e.g. ['resources', 'limits', 'memory']) under parent_map[parent_key] (a block map)."""
    node = parent_map[parent_key]
    kln, kcol = parent_map.lc.key(parent_key)
    ind = _child_indent(node, kcol + 2) if node else kcol + 2
    block, cur = [], ind
    for i, k in enumerate(path_keys):
        last = i == len(path_keys) - 1
        block.append(" " * cur + f"{k}:" + (f' "{value}"' if last and quote else f" {value}" if last else ""))
        cur += 2
    lines[kln + 1:kln + 1] = block


def set_resources(text: str, service: str, replicas=None, memory=None, cpus=None) -> str:
    data = _load(text)
    s = data["services"][service]
    lines = text.split("\n")
    flow = lambda m: m is not None and getattr(m, "fa", None) is not None and m.fa.flow_style()
    dep = s.get("deploy")
    if flow(s) or flow(dep) or flow((dep or {}).get("resources")) or flow(((dep or {}).get("resources") or {}).get("limits")):
        return _dump_fallback(text, service, replicas, memory, cpus)
    ops = []      # (line, fn) applied bottom-up so line numbers stay valid
    if replicas is not None:
        if "scale" in s:
            ops.append((s.lc.value("scale")[0], lambda L: _set_value(L, s, "scale", str(replicas))))
        elif dep is not None and "replicas" in dep:
            ops.append((dep.lc.value("replicas")[0], lambda L: _set_value(L, dep, "replicas", str(replicas))))
        elif dep is not None:
            ops.append((s.lc.key("deploy")[0], lambda L: _insert(L, s, "deploy", ["replicas"], replicas)))
        else:
            return _dump_fallback(text, service, replicas, memory, cpus)
    for key, val, flat, quote in (("memory", memory, "mem_limit", False), ("cpus", None if cpus is None else _cpus(cpus), "cpus", True)):
        if val is None:
            continue
        res = (dep or {}).get("resources")
        lim = (res or {}).get("limits")
        if lim is not None and key in lim:
            ops.append((lim.lc.value(key)[0], lambda L, lim=lim, key=key, val=val: _set_value(L, lim, key, str(val))))
        elif flat in s and lim is None:
            ops.append((s.lc.value(flat)[0], lambda L, flat=flat, val=val: _set_value(L, s, flat, str(val))))
        elif lim is not None:
            ops.append((dep["resources"].lc.key("limits")[0], lambda L, key=key, val=val, q=quote: _insert(L, dep["resources"], "limits", [key], val, q)))
        elif res is not None:
            ops.append((dep.lc.key("resources")[0], lambda L, key=key, val=val, q=quote: _insert(L, dep, "resources", ["limits", key], val, q)))
        elif dep is not None:
            ops.append((s.lc.key("deploy")[0], lambda L, key=key, val=val, q=quote: _insert(L, s, "deploy", ["resources", "limits", key], val, q)))
        else:
            return _dump_fallback(text, service, replicas, memory, cpus)
    for _, fn in sorted(ops, key=lambda o: -o[0]):
        fn(lines)
    out = "\n".join(lines)
    _load(out)                                   # must still parse
    return out


def _dump_fallback(text, service, replicas, memory, cpus):
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ
    y = YAML(); y.preserve_quotes = True; y.width = 4096; y.indent(mapping=2, sequence=4, offset=2)
    data = y.load(text); s = data["services"][service]
    dep = s.setdefault("deploy", {})
    if replicas is not None:
        if "scale" in s:
            s["scale"] = replicas
        else:
            dep["replicas"] = replicas
    lim = None
    for key, val in (("memory", memory), ("cpus", None if cpus is None else DQ(_cpus(cpus)))):
        if val is None:
            continue
        flat = "mem_limit" if key == "memory" else "cpus"
        if flat in s and "resources" not in dep:
            s[flat] = val
        else:
            lim = dep.setdefault("resources", {}).setdefault("limits", {}); lim[key] = val
    buf = io.StringIO(); y.dump(data, buf)
    return buf.getvalue()
