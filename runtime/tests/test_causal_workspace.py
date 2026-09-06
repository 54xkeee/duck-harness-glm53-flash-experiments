"""Portable host logic tests. These do not stand in for real isolation tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("causal_workspace", ROOT / "causal_workspace.py")
ccw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccw)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.set_frame(0)
        self.host = ccw.CausalWorkspace(self.path, True)
        self.host.begin()
        self.calls = 0

    def set_frame(self, col, step=0, level=1):
        grid = [[0] * 8]
        grid[0][col] = 1
        self.path.write_text(json.dumps({"current_frame": {"grid": grid, "step": step, "level": level}}))

    def move(self, request):
        self.calls += 1
        self.set_frame(self.calls, self.calls)
        return {"executed": True, "action_num": self.calls, "reward": 0}

    def noop(self, request):
        self.calls += 1
        return {"executed": True, "action_num": self.calls, "reward": 0}

    @staticmethod
    def rule():
        return {"subject": "color:1", "action": "RIGHT", "conditions": {},
                "effect": {"color_translation": {"1": [0, 1]}}}

    def test_option_runs_without_model_roundtrips(self):
        result = self.host.execute([{"action": "RIGHT", "expected": self.rule()["effect"]}] * 3, self.move)
        self.assertEqual(result["executed_count"], 3)
        self.assertEqual(self.host.receipt["executed"], 3)
        self.assertNotEqual(self.host.receipt["first_transition"], self.host.receipt["last_transition"])

    def test_surprise_stops_later_calls(self):
        item = {"action": "RIGHT", "expected": {"color_translation": {"1": [1, 0]}}}
        self.host.execute([item] * 3, self.move)
        self.host.execute([item], self.move)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.host.receipt["requested"], 4)
        self.assertEqual(self.host.receipt["stop_reason"], "unexpected_effect")

    def test_noop_suppression_and_explicit_retry(self):
        for _ in range(3):
            self.host.begin()
            self.host.execute([{"action": "LEFT"}], self.noop)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.host.receipt["executed"], 0)
        self.assertIsNone(self.host.receipt["last_transition"])
        self.host.begin()
        self.host.execute([{"action": "LEFT", "retry_reason": "test a delayed effect"}], self.noop)
        self.assertEqual(self.calls, 3)

    def test_changed_state_permits_retry(self):
        for _ in range(2):
            self.host.begin()
            self.host.execute([{"action": "LEFT"}], self.noop)
        self.set_frame(4)
        self.host.begin()
        self.host.execute([{"action": "LEFT"}], self.noop)
        self.assertEqual(self.calls, 3)

    def test_workspace_and_evidence_restart(self):
        self.host.update({"game": {"controls": "candidate"}})
        self.host.execute([{"action": "RIGHT"}], self.move)
        restored = ccw.CausalWorkspace(self.path, True)
        self.assertEqual(restored.digest(), self.host.digest())
        restored.begin()
        self.assertGreater(restored.receipt["request_id"], self.host.receipt["request_id"])

    def test_canonical_rule_identity_and_true_revision(self):
        r = self.rule()
        self.host.update({"rules": [r, dict(reversed(list(r.items())))]})
        self.assertEqual(len(self.host.workspace["rules"]), 1)
        r["conditions"] = {"has_key": True}
        self.host.update({"rules": [r]})
        self.assertEqual(len(self.host.workspace["rules"]), 2)
        r["name"] = "move_v11"
        with self.assertRaises(ValueError):
            self.host.update({"rules": [r]})

    def test_contradiction_survives_resubmission(self):
        self.host.update({"rules": [self.rule()]})
        rid = next(iter(self.host.workspace["rules"]))
        item = {"action": "RIGHT", "rule_id": rid, "expected": self.rule()["effect"]}
        self.host.execute([item], self.noop)
        self.host.update({"rules": [self.rule()]})
        self.host.begin()
        self.host.execute([item], self.move)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.host.workspace["rules"][rid]["status"], "contradicted")

    def test_scope_preserves_only_game_priors(self):
        self.host.update({"game": {"controls": "hypothesis"}, "level": {"map": 1},
                          "episode": {"plan": "right"}, "rules": [self.rule()]})
        self.host.execute([{"action": "RIGHT"}], self.move)
        self.set_frame(0, level=2)
        self.host.sync_scope()
        self.assertTrue(self.host.workspace["game"])
        self.assertFalse(self.host.workspace["level"])
        self.assertFalse(self.host.workspace["episode"])
        self.assertEqual(next(iter(self.host.workspace["rules"].values()))["status"], "prior")
        self.assertFalse(self.host.digest()["evidence"])

    def test_later_invalid_option_preserves_first_stop_reason(self):
        self.host.execute([{"action": "RIGHT"}], self.noop)
        self.host.execute([{"action": "RIGHT", "expected": {"bad": 1}}], self.move)
        self.assertEqual(self.host.receipt["stop_reason"], "no_effect")
        self.assertEqual(self.calls, 1)

    def test_rejected_action_is_not_a_transition(self):
        self.host.execute([{"action": "RIGHT"}], lambda _: {"executed": False, "stop_reason": "budget"})
        self.assertEqual(self.host.receipt["executed"], 0)
        self.assertFalse(self.host.recent)

    def test_stale_and_invalid_options_are_preflight_rejected(self):
        for item in ({"action": "RIGHT", "state_key": "stale"},
                     {"action": "RIGHT", "expected": {"made_up": 1}},
                     {"action": "RIGHT", "expected": {"color_translation": "bad"}}):
            self.host.begin()
            self.host.execute([item], self.move)
        self.assertEqual(self.calls, 0)

    def test_workspace_bounds_and_evidence_copies(self):
        with self.assertRaises(ValueError):
            self.host.update({"game": {"text": "x" * 6001}})
        self.host.execute([{"action": "RIGHT"}], self.move)
        digest = self.host.digest()
        digest["evidence"].clear()
        self.assertTrue(self.host.recent)

    def test_callback_error_stops_with_unknown_outcome(self):
        def broken(_):
            raise RuntimeError("engine error")
        with self.assertRaises(RuntimeError):
            self.host.execute([{"action": "RIGHT"}], broken)
        self.host.execute([{"action": "RIGHT"}], self.move)
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.host.receipt["stop_reason"], "host_callback_error_outcome_unknown")

    def test_reset_and_terminal_stop_options(self):
        self.host.execute([{"action": "RESET"}, {"action": "RIGHT"}], self.noop)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.host.receipt["stop_reason"], "scope_changed")
        self.host.begin()
        self.host.execute([{"action": "RIGHT"}] * 3, lambda _: {"executed": True, "done": True})
        self.assertEqual(self.host.receipt["executed"], 1)

    def test_translation_is_not_assumed_on_deformation(self):
        before = self.host.frame()
        after = {**before, "grid": [[0, 1, 1, 0]]}
        self.assertNotIn("1", ccw.events(before, after, {})["color_translation"])

    def test_receipt_has_priority_with_large_escaped_output(self):
        rendered = ccw.render_receipt(self.host.receipt, {"stdout": '\\"中文' * 10000}, 512)
        self.assertLessEqual(len(rendered), 512)
        self.assertEqual(json.loads(rendered)["HOST_RECEIPT"], self.host.receipt)

    def test_nonfinite_model_result_cannot_break_receipt(self):
        rendered = ccw.render_receipt(self.host.receipt, {"result": float("nan")}, 512)
        self.assertEqual(json.loads(rendered)["result"], "NaN")
        self.assertEqual(json.loads(rendered)["HOST_RECEIPT"], self.host.receipt)


if __name__ == "__main__":
    unittest.main()
