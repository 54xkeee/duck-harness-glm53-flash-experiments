"""Host-owned evidence and bounded model hypotheses; no game-specific rules.

One instance per runtime state path. Evidence is append-only JSONL; workspace
is a separately persisted, bounded model proposal. Neither lives in the child.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:24]


def render_receipt(receipt, payload, limit):
    """Reserve the factual envelope before budgeting any untrusted output."""
    # The child may return NaN/Infinity; keep display data from breaking receipts.
    display = json.loads(json.dumps(payload, default=str), parse_constant=str)
    out = {"HOST_RECEIPT": deepcopy(receipt), **display}
    for field in ("result", "stdout", "error"):
        if len(canonical(out)) <= limit:
            break
        if field in out:
            value = out[field]
            value = value if isinstance(value, str) else canonical(value)
            out["truncated"] = True
            out[field] = ""
            available = max(0, limit - len(canonical(out)))
            # JSON escaping can use six characters per input character.
            out[field] = value[:available // 6]
    return canonical(out) if len(canonical(out)) <= limit else canonical({"HOST_RECEIPT": receipt, "truncated": True})


def state_key(frame):
    # Deliberately exact, including HUD. No guessed player/HUD masks.
    return fingerprint({"grid": frame["grid"], "level": frame["level"]})


def action_key(action):
    return {k: action[k] for k in ("action", "row", "col") if k in action}


def events(before, after, result):
    out = {"board_changed": before["grid"] != after["grid"],
           "level_changed": before["level"] != after["level"],
           "terminal": any(result.get(k) for k in ("done", "game_over", "run_complete")),
           "reward": result.get("reward", 0)}
    # A color is tracked only if its entire occupied set translates rigidly.
    # This is a color-mask event, NOT a claim of persistent object identity.
    def masks(frame):
        found = {}
        for r, row in enumerate(frame["grid"]):
            for c, color in enumerate(row):
                found.setdefault(str(color), set()).add((r, c))
        return found
    a, b = masks(before), masks(after)
    motion = {}
    for color in a.keys() & b.keys():
        old, new = a[color], b[color]
        if len(old) != len(new):
            continue
        p, q = min(old), min(new)
        dr, dc = q[0] - p[0], q[1] - p[1]
        if {(r + dr, c + dc) for r, c in old} == new:
            motion[color] = [dr, dc]
    out["color_translation"] = motion
    return out


def validate_effect(effect):
    if not isinstance(effect, dict) or not effect:
        raise ValueError("expected must be a nonempty semantic event object")
    if set(effect) - {"board_changed", "level_changed", "terminal", "reward", "color_translation"}:
        raise ValueError("unknown expected event")
    for key in ("board_changed", "level_changed", "terminal"):
        if key in effect and type(effect[key]) is not bool:
            raise ValueError("event flags must be boolean")
    if "reward" in effect and type(effect["reward"]) not in (int, float):
        raise ValueError("reward must be numeric")
    if "color_translation" in effect:
        motion = effect["color_translation"]
        if not isinstance(motion, dict) or not motion or any(
            not isinstance(k, str) or not isinstance(v, list) or len(v) != 2
            or any(type(x) is not int for x in v) for k, v in motion.items()
        ):
            raise ValueError("color_translation expects color -> [dr, dc]")
    canonical(effect)


def matches(expected, actual):
    return all(all(actual[k].get(c) == delta for c, delta in v.items())
               if k == "color_translation" else actual[k] == v
               for k, v in expected.items())


class CausalWorkspace:
    def __init__(self, state_path: Path, enabled=False):
        self.state_path = state_path
        self.enabled = enabled
        self.journal = state_path.with_suffix(".evidence.jsonl")
        self.workspace_path = state_path.with_suffix(".workspace.json")
        self.workspace = {"game": {}, "level": {}, "episode": {}, "rules": {}}
        self.dead = Counter()
        self.recent = []
        self.sequence = 0
        self.request = 0
        self.scope = None
        self.last_action = None
        if self.workspace_path.exists():
            self.workspace = json.loads(self.workspace_path.read_text(encoding="utf-8"))
        if self.journal.exists():
            with self.journal.open(encoding="utf-8") as stream:
                for line in stream:
                    self._ingest(json.loads(line))
        self.receipt = {}
        self.stopped = None

    def frame(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))["current_frame"]

    def _ingest(self, record):
        self.sequence = record["sequence"]
        self.request = max(self.request, record.get("request", 0))
        if record["type"] == "scope":
            self.scope = record["scope"]
            self.dead.clear()
            self.recent.clear()
        elif record["type"] == "transition":
            self.last_action = record["action"]
            self.recent = (self.recent + [record])[-4:]
            key = (record["before_key"], canonical(record["action"]))
            if record["no_effect"]:
                self.dead[key] += 1

    def append(self, kind, **fields):
        record = {"type": kind, "sequence": self.sequence + 1,
                  "request": self.request, **deepcopy(fields)}
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(canonical(record) + "\n")
        self._ingest(record)
        return record

    def sync_scope(self, reset=False):
        frame = self.frame()
        if self.scope != frame["level"] or reset:
            self.append("scope", scope=frame["level"])
            self.workspace["level"] = {}
            self.workspace["episode"] = {}
            # Cross-level rules remain hypotheses/priors, never auto-certified.
            for rule in self.workspace["rules"].values():
                rule["status"] = "prior"
            self.save()

    def save(self):
        temp = self.workspace_path.with_suffix(".tmp")
        temp.write_text(canonical(self.workspace) + "\n", encoding="utf-8")
        temp.replace(self.workspace_path)

    def update(self, proposal):
        if not isinstance(proposal, dict) or set(proposal) - {"game", "level", "episode", "rules"}:
            raise ValueError("workspace accepts game, level, episode and rules only")
        candidate = deepcopy(self.workspace)
        for scope in ("game", "level", "episode"):
            if scope in proposal:
                if not isinstance(proposal[scope], dict):
                    raise ValueError("workspace scopes must be objects")
                candidate[scope] = deepcopy(proposal[scope])
        rules = proposal.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("rules must be a list")
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"subject", "action", "conditions", "effect"}:
                raise ValueError("rule requires subject, action, conditions, effect; names are not identity")
            if not isinstance(rule["subject"], str) or not isinstance(rule["conditions"], dict):
                raise ValueError("subject must be a string; conditions a structured object")
            if rule["action"] not in {"UP", "DOWN", "LEFT", "RIGHT", "SPACE", "MOUSE", "RESET"}:
                raise ValueError("use a canonical action name")
            validate_effect(rule["effect"])
            rid = "R" + fingerprint(rule)
            candidate["rules"].setdefault(rid, {"spec": deepcopy(rule), "status": "hypothesis",
                                                "support": [], "counterexamples": []})
        if len(candidate["rules"]) > 16 or len(canonical(candidate)) > 6000:
            raise ValueError("workspace exceeds 16 rules or 6000 serialized characters")
        self.workspace = candidate
        self.save()

    def digest(self):
        return {"workspace": deepcopy(self.workspace), "evidence": [
            {k: r[k] for k in ("transition_id", "action", "events")} for r in self.recent],
            "state_key": state_key(self.frame()), "scope": self.scope}

    def begin(self, proposal=None):
        self.request += 1
        self.stopped = None
        self.receipt = {"request_id": self.request, "requested": 0, "executed": 0,
                        "first_transition": None, "last_transition": None, "stop_reason": None,
                        "current_step": self.frame()["step"], "last_real_action": self.last_action}
        self.append("request")
        self.sync_scope()
        if proposal is not None:
            if not self.enabled:
                raise ValueError("workspace treatment is disabled")
            self.update(proposal)

    def execute(self, actions, callback):
        self.receipt["requested"] += len(actions)
        executed, last, names = 0, {}, []
        # Validate the complete option before taking any real action.
        try:
            for item in actions:
                if "expected" in item:
                    validate_effect(item["expected"])
                if "rule_id" in item:
                    rule = self.workspace["rules"].get(item["rule_id"])
                    if not rule or rule["status"] == "contradicted" or item.get("expected") != rule["spec"]["effect"]:
                        raise ValueError("unknown/contradicted rule or effect differs from referenced rule")
                    if item["action"] != rule["spec"]["action"]:
                        raise ValueError("action differs from referenced rule")
        except (ValueError, TypeError):
            self.stopped = self.stopped or "invalid_option"
        for item in actions:
            if self.stopped:
                break
            before = self.frame()
            key = (state_key(before), canonical(action_key(item)))
            if self.enabled and item["action"] != "RESET" and self.dead[key] >= 2 and not item.get("retry_reason"):
                self.stopped = "dead_action_suppressed"
                break
            if "state_key" in item and item["state_key"] != state_key(before):
                self.stopped = "stale_option"
                break
            clean = {k: v for k, v in item.items() if k in {"action", "row", "col", "prediction"}}
            self.append("attempt", action=action_key(item), before_key=key[0],
                        expected=item.get("expected"), retry_reason=item.get("retry_reason"))
            try:
                last = callback({"actions": [clean]})
            except Exception:
                self.stopped = "host_callback_error_outcome_unknown"
                self.receipt["stop_reason"] = self.stopped
                self.append("error", reason=self.stopped)
                raise
            if not isinstance(last, dict):
                self.stopped = "host_callback_error_outcome_unknown"
                self.receipt["stop_reason"] = self.stopped
                self.append("error", reason=self.stopped)
                raise ValueError("host callback returned no result")
            after = self.frame()
            if not last.get("executed"):
                self.stopped = last.get("stop_reason") or "host_rejected"
                break
            event = events(before, after, last)
            tid = "T" + str(self.sequence + 1).zfill(8)
            record = self.append("transition", transition_id=tid, action=action_key(item),
                before=before, after=after, before_key=key[0], events=event,
                no_effect=not event["board_changed"] and not event["level_changed"]
                    and not event["terminal"] and not event["reward"],
                host_result={k: last.get(k) for k in ("executed", "action_num", "state", "reward", "stop_reason")})
            executed += 1
            names.append(item["action"])
            self.receipt["executed"] += 1
            self.receipt["first_transition"] = self.receipt["first_transition"] or tid
            self.receipt.update(last_transition=tid, current_step=after["step"], last_real_action=action_key(item))
            expected = item.get("expected") if self.enabled else None
            if expected and not matches(expected, event):
                self.stopped = "unexpected_effect"
            rid = item.get("rule_id")
            if self.enabled and rid:
                rule = self.workspace["rules"][rid]
                bucket = "counterexamples" if self.stopped else "support"
                rule[bucket] = (rule[bucket] + [record["transition_id"]])[-4:]
                rule["status"] = "contradicted" if self.stopped else "supported_local"
                self.save()
            if event["level_changed"] or item["action"] == "RESET":
                self.sync_scope(reset=True)
                self.stopped = "scope_changed"
            elif event["terminal"] or last.get("level_completed"):
                self.stopped = "terminal_or_level_complete"
            elif last.get("stop_reason"):
                self.stopped = last["stop_reason"]
            elif self.enabled and record["no_effect"]:
                self.stopped = "no_effect"
        self.receipt["stop_reason"] = self.stopped
        self.append("receipt", receipt=self.receipt)
        return {**last, "executed": bool(executed), "requested_count": len(actions),
                "executed_count": executed, "executed_actions": names,
                "stopped_early": executed < len(actions), "stop_reason": self.stopped}
