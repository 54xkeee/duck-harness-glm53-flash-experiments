"""Host-observed motion evidence and stepwise batch control."""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from inference.agent.runtime_state import Frame
from inference.utils.grid_utils import ARC_COLOR_CHARS
from inference.utils.segmentation import segment_layer


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _observed_motion(before: Frame, after: Frame) -> tuple[dict[str, Any] | None, str]:
    """Match unique equal-shape components, then group their common translation."""
    if before.shape != after.shape or not before.grid:
        return None, "shape_changed"
    indexes = []
    for frame in (before, after):
        index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in segment_layer(frame.grid, ARC_COLOR_CHARS)["nodes"]:
            index[node["hash"]].append(node)
        indexes.append(index)
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for shape, old_nodes in indexes[0].items():
        new_nodes = indexes[1].get(shape, [])
        if len(old_nodes) != 1 or len(new_nodes) != 1:
            continue
        old, new = old_nodes[0], new_nodes[0]
        origin = tuple(min(point[axis] for point in old["boundary"]) for axis in (0, 1))
        target = tuple(min(point[axis] for point in new["boundary"]) for axis in (0, 1))
        delta = (target[0] - origin[0], target[1] - origin[1])
        if delta != (0, 0):
            groups[delta].append({"shape": shape, "color": old["color"], "pixels": old["pixels"], "origin": origin})
    if len(groups) != 1:
        return None, "competing_motion" if groups else "no_unique_motion"
    delta, components = next(iter(groups.items()))
    anchor = tuple(min(part["origin"][axis] for part in components) for axis in (0, 1))
    parts = sorted(
        [{"shape": part["shape"], "color": part["color"], "pixels": part["pixels"],
          "offset": [part["origin"][0] - anchor[0], part["origin"][1] - anchor[1]]}
         for part in components], key=lambda part: (part["shape"], part["offset"]),
    )
    return {"delta": list(delta), "components": parts}, "observed_motion"


@dataclass
class EvidenceController:
    probe_enabled: bool = False
    belief_enabled: bool = False
    scope: int = 0
    level: int | None = None
    recent_facts: list[dict[str, Any]] = field(default_factory=list)
    counterexamples: list[dict[str, Any]] = field(default_factory=list)
    _models: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls) -> "EvidenceController":
        return cls(_enabled("DUCK_VERIFIED_PROBE"), _enabled("DUCK_BELIEF_STORE"))

    @property
    def enabled(self) -> bool:
        return self.probe_enabled or self.belief_enabled

    def allows_batch(self, action: str) -> bool:
        model = self._models.get(action)
        return bool(model and len(model["contexts"]) >= 2)

    def snapshot(self) -> dict[str, Any]:
        return {
            "scope": {"id": self.scope, "level": self.level},
            "recent_facts": list(self.recent_facts),
            "action_models": [
                {"action": action, "motion": model["motion"], "support_count": len(model["contexts"]),
                 "status": "consistent_motion" if len(model["contexts"]) >= 2 else "observed_motion",
                 "evidence_ids": list(model["evidence_ids"])}
                for action, model in self._models.items()
            ],
            "counterexamples": list(self.counterexamples),
        }

    def observe(self, action: str, before: Frame, after: Frame, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if self.level is None:
            self.level = before.level
        boundary = action == "RESET" or before.level != after.level or any(
            payload.get(key) for key in ("level_completed", "game_over", "run_complete")
        )
        changed = before.grid != after.grid
        motion, motion_status = _observed_motion(before, after) if changed and not boundary else (None, "scope_boundary" if boundary else "no_change")
        fact = {
            "evidence_id": f"{self.scope}:{after.step}", "action_num": after.step, "action": action,
            "level": before.level, "next_level": after.level, "state": payload.get("state"),
            "score": payload.get("score"), "board_changed": changed,
            "changed_pixels": sum(a != b for old, new in zip(before.grid, after.grid) for a, b in zip(old, new)) if before.shape == after.shape else None,
            "motion": motion, "motion_status": motion_status, "scope_ended": boundary,
        }
        previous = self._models.get(action)
        mismatch = previous is not None and previous["motion"] != motion
        if mismatch and not boundary:
            self.counterexamples.append({"evidence_id": fact["evidence_id"], "action": action,
                                         "expected_motion": previous["motion"], "observed_motion": motion,
                                         "motion_status": motion_status})
            self.counterexamples = self.counterexamples[-8:]
        if motion is not None:
            if previous is None or mismatch:
                previous = {"motion": motion, "contexts": set(), "evidence_ids": []}
                self._models[action] = previous
            if len(previous["contexts"]) < 2:
                previous["contexts"].add(before.grid)
            previous["evidence_ids"] = (previous["evidence_ids"] + [fact["evidence_id"]])[-4:]
        else:
            self._models.pop(action, None)
        self.recent_facts = (self.recent_facts + [fact])[-8:]
        if boundary:
            self._models.clear()
            self.counterexamples.clear()
            self.scope += 1
            self.level = after.level
        stop_reason = "probe_no_change" if not changed else "probe_mismatch" if mismatch else None
        return fact, stop_reason
