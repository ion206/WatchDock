"""The only things a WatchDock brain can do. Every tool enforces policy in code.

There is no shell. A brain can look (get_status, read_file), propose (set_resources, or
edit_compose in text mode), check (validate), act once (apply_fix) or hand off (escalate).
apply_fix is the single path to production: guardrails (whole stack, from the pre-incident
snapshot) + heal budget + one exact deploy command.
"""
from __future__ import annotations

import difflib, io, json, os, re
from pathlib import Path

import guardrails

SPECS_COMMON = [
    {"name": "get_status", "description": "Live state of the service right now: replicas and their status, restarts, OOM kills, latest probe latency, CPU and memory.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Read a file in the deployment directory (docker-compose.yml, watchdock.md). .env is never readable.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "validate", "description": "Dry-run the guardrail check on the current docker-compose.yml against the pre-incident baseline. Changes nothing.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "apply_fix", "description": "Apply the edited docker-compose.yml (validated again first). Allowed once per attempt. WatchDock then verifies the fix and rolls back automatically if it does not work.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string", "description": "One sentence: what you changed and why."}}, "required": ["summary"]}},
    {"name": "escalate", "description": "Hand the incident to a human with no change, when no change inside the guardrails would fix the root cause (config or code bug, external dependency, need beyond the limits).",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]
SPEC_SET = {"name": "set_resources", "description": "Edit the incident service's limits in docker-compose.yml. Only replicas, memory (e.g. '512m') and cpus (e.g. 1.0) can change; omit what you keep. Returns the diff and a dry-run guardrail verdict.",
            "input_schema": {"type": "object", "properties": {"replicas": {"type": "integer"}, "memory": {"type": "string"}, "cpus": {"type": "number"}}}}
SPEC_EDIT = {"name": "edit_compose", "description": "Replace one exact occurrence of old_string with new_string in docker-compose.yml. Returns the diff and a dry-run guardrail verdict.",
             "input_schema": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}


class Toolbox:
    def __init__(self, incident, controller, edit_mode: str = "structured"):
        self.inc, self.ctl, self.edit_mode = incident, controller, edit_mode
        self.dir = Path(incident.dir).resolve()
        self.compose = self.dir / "docker-compose.yml"
        self.applied: dict | None = None
        self.applied_event = __import__("asyncio").Event()     # lets the controller start verifying at deploy time
        self.escalated: str | None = None
        self.calls: list[dict] = []

    def specs(self) -> list[dict]:
        return SPECS_COMMON[:2] + [SPEC_SET if self.edit_mode == "structured" else SPEC_EDIT] + SPECS_COMMON[2:]

    async def call(self, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        try:
            out = await fn(**(args or {})) if fn else {"error": f"unknown tool {name}"}
        except TypeError as e:
            out = {"error": f"bad arguments: {e}"}
        except guardrails.Invalid as e:
            out = {"error": str(e)}
        self.calls.append({"tool": name, "args": args, "result": out})
        self.ctl.on_tool(self.inc, name, args, out)
        return json.dumps(out, default=str)

    # ---- tools --------------------------------------------------------------
    async def t_get_status(self):
        return await self.ctl.status(self.inc)

    async def t_read_file(self, path: str):
        p = (self.dir / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        if p != self.dir and self.dir not in p.parents:
            return {"error": "outside the deployment directory"}
        if p.name == ".env":
            return {"error": ".env holds secrets and is never readable"}
        if not p.is_file():
            return {"error": f"no such file: {p.name}"}
        return {"path": p.name, "content": p.read_text()[:20000]}

    def _dry(self):
        v = guardrails.validate(self.inc.baseline, str(self.dir), self.inc.service, self.ctl.recent_heals(self.inc.service))
        return {"ok": v["ok"], "changes": v["changes"], "violations": v["violations"]}

    def _diff(self, before: str, after: str) -> str:
        return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "before", "after", n=1))[:1500]

    async def t_set_resources(self, replicas=None, memory=None, cpus=None):
        if self.applied:
            return {"error": "a fix was already applied in this attempt"}
        from composepatch import set_resources
        if memory is not None:
            guardrails.to_bytes(memory)                      # raises on nonsense
        if replicas is not None and (not isinstance(replicas, int) or replicas < 0):
            return {"error": "replicas must be a non-negative integer"}
        if cpus is not None and (not isinstance(cpus, (int, float)) or cpus <= 0):
            return {"error": "cpus must be a positive number"}
        before = self.compose.read_text()
        after = set_resources(before, self.inc.service, replicas=replicas, memory=memory, cpus=cpus)
        self.compose.write_text(after)
        return {"diff": self._diff(before, after), "dry_run": self._dry()}

    async def t_edit_compose(self, old_string: str, new_string: str):
        if self.applied:
            return {"error": "a fix was already applied in this attempt"}
        before = self.compose.read_text()
        n = before.count(old_string)
        if n != 1:
            return {"error": f"old_string must match exactly once (found {n})"}
        after = before.replace(old_string, new_string)
        self.compose.write_text(after)
        return {"diff": self._diff(before, after), "dry_run": self._dry()}

    async def t_validate(self):
        return self._dry()

    async def t_apply_fix(self, summary: str = ""):
        if self.applied:
            return {"error": "a fix was already applied in this attempt; wait for verification"}
        if self.escalated:
            return {"error": "already escalated"}
        res = await self.ctl.apply(self.inc, summary)
        if res.get("applied"):
            self.applied = res
            self.applied_event.set()
        return res

    async def t_escalate(self, reason: str):
        if self.applied:
            return {"error": "a fix is already applied; WatchDock will verify it and roll back if it fails"}
        self.escalated = reason
        return {"ok": True, "note": "handed to a human; no change made"}
