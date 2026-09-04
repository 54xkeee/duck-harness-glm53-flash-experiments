"""Precommitted, host-checked predictions; support is observational, not causal."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from inference.agent.runtime_state import Frame
from inference.utils.grid_utils import ARC_COLOR_CHARS


def _cells(value: Any, frame: Frame) -> list[list[Any]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("cells must be a list")
    cells, seen = [], set()
    for cell in value:
        if not isinstance(cell, (list, tuple)) or len(cell) != 3:
            raise ValueError("cell must be [row, col, ARCletter]")
        row, col, color = cell
        if (type(row) is not int or type(col) is not int or
                not 0 <= row < len(frame.grid) or not 0 <= col < len(frame.grid[row])):
            raise ValueError("cell coordinates outside current grid")
        if not isinstance(color, str) or len(color) != 1 or color not in ARC_COLOR_CHARS:
            raise ValueError("cell color must be an ARC letter")
        if (row, col) in seen:
            raise ValueError("duplicate cell coordinates")
        seen.add((row, col))
        cells.append([row, col, color])
    return cells


def _actual(frame: Frame, row: int, col: int) -> str | None:
    if row >= len(frame.grid) or col >= len(frame.grid[row]):
        return None
    value = frame.grid[row][col]
    return ARC_COLOR_CHARS[value] if 0 <= value < len(ARC_COLOR_CHARS) else None


@dataclass
class PredictionController:
    enabled: bool = False
    scope: int = 0
    level: int | None = None
    _serial: int = 0
    _rules: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _versions: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _recent: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _counterexamples: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _pending: dict[int, tuple[dict[str, Any], Frame]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls) -> "PredictionController":
        return cls(os.environ.get("DUCK_PREDICTION_CHECK", "").strip().lower() in {"1", "true", "yes", "on"})

    def snapshot(self) -> dict[str, Any]:
        fields = ("ticket_id", "evidence_id", "action", "step", "action_num", "scope", "rule_id", "version",
                  "status", "matched", "scope_ended", "stop_reason", "checked_cell_count",
                  "predicted_changed_cell_count", "observed_changed_cell_count", "observed_changed_cells_checked",
                  "observed_changed_cell_coverage", "stationary_cells_prediction")

        def compact(record: dict[str, Any], *, counterexample: bool = False) -> dict[str, Any]:
            result = {key: record[key] for key in fields}
            checks = record["checks"]
            mismatches = [check for check in checks if not check["matched"]]
            result["checks"] = mismatches[:8] if mismatches else checks[:2]
            result["check_count"] = len(checks)
            result["mismatch_count"] = len(mismatches)
            result["checks_truncated"] = len(result["checks"]) < len(checks)
            if counterexample:
                conditions = record["prediction"]["conditions"]
                result["conditions_sample"] = conditions[:8]
                result["condition_count"] = len(conditions)
            return result

        return deepcopy({
            "scope": {"id": self.scope, "level": self.level},
            "support_semantics": "predictive_observations_not_causal_verification",
            "record_detail": "Compact samples only; full predictions, conditions, and checks are preserved in action events.",
            "rules": sorted(self._rules.values(), key=lambda rule: rule["last_step"])[-8:],
            "rule_versions": self._versions[-8:],
            "recent_predictions": [compact(record) for record in self._recent[-12:]],
            "counterexamples": [compact(record, counterexample=True) for record in self._counterexamples[-8:]],
        })

    def _dependency_valid(self, dependency: dict[str, Any]) -> bool:
        rule = self._rules.get(dependency["rule_id"])
        return bool(rule and rule["version"] == dependency["version"] and
                    rule["scope"] == self.scope and rule["status"] == "predictively_supported")

    def _invalidate_dependents(self) -> list[str]:
        invalidated = []
        while True:
            changed = False
            for rule in self._rules.values():
                if rule["status"] in {"candidate", "predictively_supported"} and any(
                    not self._dependency_valid(dep) for dep in rule["depends_on"]
                ):
                    rule["status"] = "dependency_invalidated"
                    invalidated.append(rule["rule_id"])
                    changed = True
            if not changed:
                return invalidated

    def prepare(self, action: str, before: Frame, prediction: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
        if not self.enabled:
            return None, None
        if self.level is None:
            self.level = before.level
        rule, revision = None, None
        if prediction is not None:
            try:
                if not isinstance(prediction, dict) or set(prediction) - {"rule_id", "rule", "expected", "conditions", "depends_on"}:
                    raise ValueError("prediction fields: rule_id, rule, expected, conditions, depends_on")
                prediction = deepcopy(prediction)
                for key in ("rule_id", "rule"):
                    if not isinstance(prediction.get(key), str) or not prediction[key].strip():
                        raise ValueError(f"{key} must be nonempty text")
                    prediction[key] = prediction[key].strip()
                expected = prediction.get("expected")
                if not isinstance(expected, dict) or set(expected) - {"cells", "level", "board_changed"}:
                    raise ValueError("expected fields: cells, level, board_changed")
                expected["cells"] = _cells(expected.get("cells", []), before)
                if "level" in expected and (type(expected["level"]) is not int or expected["level"] < 1):
                    raise ValueError("expected level must be a positive integer")
                if not expected["cells"] and "level" not in expected:
                    raise ValueError("expected needs cells or level, not only board_changed")
                if not expected["cells"] and expected.get("level") == before.level:
                    raise ValueError("level-only prediction must predict a level transition")
                if "board_changed" in expected and type(expected["board_changed"]) is not bool:
                    raise ValueError("board_changed must be boolean")
                prediction["conditions"] = _cells(prediction.get("conditions", []), before)
                dependencies = prediction.setdefault("depends_on", [])
                if not isinstance(dependencies, list) or any(
                    not isinstance(dep, dict) or set(dep) != {"rule_id", "version"} or
                    not isinstance(dep["rule_id"], str) or type(dep["version"]) is not int or dep["version"] < 1 or
                    dep["rule_id"] == prediction["rule_id"] for dep in dependencies
                ):
                    raise ValueError("depends_on needs other rule_id/version pairs")
            except ValueError as exc:
                return None, f"prediction_invalid: {exc}"
            mismatches = []
            for row, col, color in prediction["conditions"]:
                actual = _actual(before, row, col)
                if actual != color:
                    mismatches.append([row, col, color, actual])
                    if len(mismatches) == 8:
                        break
            if mismatches:
                detail = {"columns": ["row", "col", "expected", "actual"], "mismatches": mismatches}
                return None, f"prediction_precondition_failed: {json.dumps(detail)}"
            old = self._rules.get(prediction["rule_id"])
            changed_rule = old is None or old["rule"] != prediction["rule"]
            if old and not changed_rule and old["status"] == "contested":
                return None, "prediction_rule_contested"
            dependencies = prediction["depends_on"] + ([] if changed_rule else old["depends_on"])
            if any(not self._dependency_valid(dep) for dep in dependencies):
                return None, "prediction_stale_dependency"
            ancestors = [dep["rule_id"] for dep in dependencies]
            visited = set()
            while ancestors:
                ancestor = ancestors.pop()
                if ancestor == prediction["rule_id"]:
                    return None, "prediction_stale_dependency"
                if ancestor not in visited:
                    visited.add(ancestor)
                    ancestors.extend(dep["rule_id"] for dep in self._rules[ancestor]["depends_on"])
            if changed_rule:
                revision = {"rule_id": prediction["rule_id"], "version": old["version"] + 1 if old else 1,
                            "rule": prediction["rule"], "created_scope": self.scope, "created_step": before.step}
                self._versions.append(deepcopy(revision))
                rule = {**revision, "scope": self.scope, "status": "candidate", "support_count": 0,
                        "depends_on": [], "evidence_ids": [], "last_step": before.step}
                self._rules[prediction["rule_id"]] = rule
            else:
                rule = old
            rule["scope"] = self.scope
            rule["last_step"] = before.step
            for dep in dependencies:
                if dep not in rule["depends_on"]:
                    rule["depends_on"].append(deepcopy(dep))
            self._invalidate_dependents()
        self._serial += 1
        ticket = {"ticket_id": self._serial, "action": action, "step": before.step, "scope": self.scope,
                  "rule_id": rule["rule_id"] if rule else None, "version": rule["version"] if rule else None,
                  "rule_revision": revision, "prediction": prediction, "conditions_match": prediction is not None}
        self._pending[self._serial] = (deepcopy(ticket), before)
        return deepcopy(ticket), None

    def observe(self, ticket: dict[str, Any] | None, action: str, before: Frame, after: Frame,
                payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if not self.enabled:
            return {}, None
        committed = self._pending.pop(ticket.get("ticket_id") if isinstance(ticket, dict) else None, None)
        if committed is None or committed[0]["action"] != action or committed[1] != before:
            return {"status": "invalid_commitment", "matched": None}, "prediction_missing_commitment"
        frozen = committed[0]
        prediction = frozen["prediction"]
        boundary = action == "RESET" or before.level != after.level or any(
            payload.get(key) for key in ("level_completed", "game_over", "run_complete")
        )
        checks = []
        if prediction is not None:
            expected = prediction["expected"]
            for row, col, color in expected["cells"]:
                actual = _actual(after, row, col)
                checks.append({"field": "cell", "row": row, "col": col, "expected": color,
                               "actual": actual, "matched": color == actual})
            for key, actual in (("level", after.level), ("board_changed", before.grid != after.grid)):
                if key in expected:
                    checks.append({"field": key, "expected": expected[key], "actual": actual, "matched": expected[key] == actual})
        matched = all(check["matched"] for check in checks) if prediction is not None else None
        observed_changed = {
            (row, col)
            for row in range(max(len(before.grid), len(after.grid)))
            for col in range(max(len(before.grid[row]) if row < len(before.grid) else 0,
                                 len(after.grid[row]) if row < len(after.grid) else 0))
            if _actual(before, row, col) != _actual(after, row, col)
        }
        cells = prediction["expected"]["cells"] if prediction else []
        checked_changed = len(observed_changed.intersection((row, col) for row, col, _ in cells))
        predicted_changed = sum(_actual(before, row, col) != color for row, col, color in cells)
        reason = "prediction_required" if prediction is None else "prediction_mismatch" if not matched else None
        evaluation = {**deepcopy(frozen), "evidence_id": f"{self.scope}:{after.step}", "action_num": after.step,
                      "status": "unpredicted" if prediction is None else "matched" if matched else "mismatched",
                      "matched": matched, "checks": checks, "scope_ended": boundary, "invalidated_rules": [],
                      "checked_cell_count": len(cells), "predicted_changed_cell_count": predicted_changed,
                      "observed_changed_cell_count": len(observed_changed),
                      "observed_changed_cells_checked": checked_changed,
                      "observed_changed_cell_coverage": checked_changed / len(observed_changed) if observed_changed else None,
                      "stationary_cells_prediction": bool(cells) and predicted_changed == 0}
        if prediction is not None:
            rule = self._rules[frozen["rule_id"]]
            rule["status"] = "predictively_supported" if matched else "contested"
            rule["support_count"] += int(matched)
            rule["last_step"] = after.step
            rule["evidence_ids"] = (rule["evidence_ids"] + [evaluation["evidence_id"]])[-8:]
            evaluation["invalidated_rules"] = self._invalidate_dependents()
        if boundary:
            self.scope += 1
            self.level = after.level
            self._pending.clear()
            for rule in self._rules.values():
                rule.update(scope=None, status="candidate", support_count=0, depends_on=[], evidence_ids=[])
            reason = reason or "prediction_scope_boundary"
        evaluation["stop_reason"] = reason
        self._recent = (self._recent + [deepcopy(evaluation)])[-12:]
        if matched is False:
            self._counterexamples = (self._counterexamples + [deepcopy(evaluation)])[-8:]
        return evaluation, reason
