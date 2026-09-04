"""Offline prediction experiment checks: fixtures only, no model requests."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import analyze_prediction_experiment as analysis
import run_prediction_experiment as runner


def event_pair(ticket_id=1, *, matched=True, boundary=False, version=1, step=0, revision=True):
    rule_revision = {"rule_id": "move", "version": version, "rule": f"rule {version}", "created_step": step}
    ticket = {"ticket_id": ticket_id, "action": "UP", "step": step, "scope": 0,
              "rule_id": "move", "version": version,
              "rule_revision": rule_revision if revision else None,
              "prediction": {"expected": {"cells": [[1, 1, "G"]]}}}
    evaluation = {**ticket, "status": "matched" if matched else "mismatched", "matched": matched,
                  "scope_ended": boundary, "checked_cell_count": 1, "predicted_changed_cell_count": 1,
                  "observed_changed_cell_count": 2, "observed_changed_cells_checked": 1,
                  "stationary_cells_prediction": False,
                  "stop_reason": None if matched else "prediction_mismatch", "invalidated_rules": []}
    return [{"type": "prediction_commitment", "prediction_commitment": ticket},
            {"type": "action", "prediction": evaluation, "score": 0, "action_name": "ACTION1", "action_display": "UP"}]


class PredictionExperimentTests(unittest.TestCase):
    def test_balanced_reproducible_four_arms(self):
        slots = runner.schedule(20260905)
        self.assertEqual(slots, runner.schedule(20260905))
        self.assertEqual(len(slots), 8)
        for replicate in (1, 2):
            self.assertEqual({row["arm"] for row in slots if row["replicate"] == replicate},
                             {name for name, _, _ in runner.ARMS})

    def test_common_settings_override_environment(self):
        with patch.dict(runner.os.environ, {"DUCK_VERIFIED_PROBE": "1", "DUCK_PREDICTION_CHECK": "1",
                                            "LOCAL_ANALYZER_MAX_OUTPUT": "99999", "LOCAL_ANALYZER_BASE_URL": "fixture"}):
            for slot in runner.schedule(1):
                env = runner.child_environment(slot)
                self.assertEqual(env["DUCK_VERIFIED_PROBE"], "0")
                self.assertEqual(env["DUCK_API_FAILURE_LIMIT"], "3")
                self.assertEqual(env["DUCK_PREDICTION_CHECK"], str(int(slot["prediction"])))
                self.assertEqual(env["DUCK_BELIEF_STORE"], str(int(slot["belief"])))
                self.assertEqual(env["LOCAL_ANALYZER_MAX_OUTPUT"], "8192")
                self.assertEqual(env["LOCAL_ANALYZER_REASONING_EFFORT"], "low")
                self.assertEqual(env["MULTIMODAL_CONTEXT"], "current_grid")
                self.assertEqual(env["LOCAL_ANALYZER_BASE_URL"], "https://open.bigmodel.cn/api/paas/v4/")

    def test_runtime_keeps_solver_and_hard_budget_distinct(self):
        args = runner.parse_args(["--output", "/tmp/prediction-fixture"])
        command = runner.command_for(args, args.output)
        self.assertEqual(float(command[command.index("--max-runtime-minutes") + 1]), 29)
        self.assertNotIn("--max-experiment-runtime-minutes", command)
        self.assertEqual(args.max_actions, 1200)
        self.assertEqual(args.per_run_seconds - args.cleanup_seconds, 1770)
        inline_budget = 29 * 60 + 600
        self.assertEqual(inline_budget - min(600, inline_budget / 2), 1740)

    def test_invalid_budgets_and_parallelism(self):
        for extra in (["--per-run-seconds", "1801"], ["--jobs", "3"], ["--per-run-seconds", "60"]):
            with self.assertRaises(SystemExit):
                runner.parse_args(["--output", "/tmp/x", *extra])

    def test_short_fixture_budget_compensates_half_budget_clamp(self):
        args = runner.parse_args(["--output", "/tmp/x", "--per-run-seconds", "180"])
        command = runner.command_for(args, args.output)
        inline = float(command[command.index("--max-experiment-runtime-minutes") + 1]) * 60
        self.assertEqual(inline - min(600, inline / 2), 120)

    def test_api_circuit_quota_is_distinct_from_rate_limit(self):
        circuit = runner.ApiCircuit()
        self.assertIsNone(circuit.observe([{"event": "error", "request_id": 1, "http_status": 429}]))
        self.assertEqual(circuit.observe([{"event": "error", "request_id": 2, "http_status": 401}]),
                         "provider_credentials_or_quota")

    def test_api_circuit_consecutive_errors_and_response_reset(self):
        circuit = runner.ApiCircuit()
        self.assertIsNone(circuit.observe([{"event": "error", "request_id": 1}]))
        self.assertIsNone(circuit.observe([{"event": "error", "request_id": 1}, {"event": "response", "request_id": 2}]))
        self.assertEqual(circuit.consecutive_errors, 0)
        self.assertIsNone(circuit.observe([{"event": "error", "request_id": 1}, {"event": "error", "request_id": 2}]))
        self.assertEqual(circuit.observe([{"event": "error", "request_id": 1}]),
                         "three_consecutive_errors_without_response")

    def test_paused_queue_stays_pending_and_blocks_final_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = argparse.Namespace(output=root, jobs=1)
            slots = runner.schedule(1)

            def execute(slot, unused):
                path = root / slot["slot"] / "artifacts"
                path.mkdir(parents=True)
                (path / "usage.jsonl").write_text(json.dumps({"event": "error", "request_id": 1, "http_status": 401}))
                return {**slot, "status": "process_error"}

            reason = runner.run_schedule(slots, args, {}, execute)
            self.assertEqual(reason, "provider_credentials_or_quota")
            self.assertEqual(sum(row["status"] == "pending" for row in slots), 7)
            self.assertFalse(json.loads((root / "summary.json").read_text())["complete"])
            with self.assertRaises(ValueError):
                analysis.build_analysis(root)

    def test_all_eight_terminal_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner.report(root, {}, [{**row, "status": "completed"} for row in runner.schedule(1)[:4]])
            with self.assertRaises(ValueError):
                analysis.build_analysis(root)

    def test_resume_only_pending_slots_and_generate_complete_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slots, called = runner.schedule(1), []
            slots[0].update(status="launch_error", elapsed_seconds=1)
            config = {"model": runner.MODEL, "max_actions": 1200, "per_run_seconds": 1800, "effective_solver_seconds": 1740}

            def execute(slot, unused):
                called.append(slot["slot"])
                return {**slot, "status": "launch_error", "elapsed_seconds": 1}

            self.assertIsNone(runner.run_schedule(slots, argparse.Namespace(output=root, jobs=2), config, execute))
            self.assertEqual(len(called), 7)
            self.assertNotIn(slots[0]["slot"], called)
            data = analysis.save_analysis(root)
            self.assertTrue(data["complete"])
            self.assertEqual(data["arms_itt"]["baseline"]["assigned_n"], 2)
            self.assertEqual(data["arms_itt"]["baseline"]["mean_full_game_success"], 0)
            self.assertTrue((root / "analysis.md").exists())

    def test_prediction_ids_are_scoped_by_run(self):
        rows = [{"_key": (run, "usage.jsonl", 1), "event": "request", "prediction_injected": True,
                 "prediction_ids": [1]} for run in ("r1", "r2")]
        result = analysis.request_metrics(rows)
        self.assertEqual(result["prediction_state_injection_requests"], 2)
        self.assertEqual(result["distinct_injected_prediction_ids"], 2)

    def test_precommit_must_precede_action(self):
        commitment, action = event_pair()
        result = analysis.prediction_metrics([action, commitment])
        self.assertEqual(result["scored_predictions"], 0)
        self.assertEqual(result["invalid_commitment_actions"], 1)

    def test_precommit_must_equal_frozen_action_prediction(self):
        events = event_pair()
        events[-1]["prediction"] = {**events[-1]["prediction"], "prediction": {"expected": {"cells": []}}}
        result = analysis.prediction_metrics(events)
        self.assertEqual(result["scored_predictions"], 0)

    def test_prediction_boundary_coverage_and_stops(self):
        events = event_pair() + event_pair(2, matched=False, boundary=True, step=1)
        events += [{"type": "action", "score": 0}, {"type": "prediction_rejected", "prediction_error": "prediction_precondition_failed"}]
        result = analysis.prediction_metrics(events)
        self.assertEqual(result["scored_predictions"], 2)
        self.assertAlmostEqual(result["prediction_action_coverage"], 2 / 3)
        self.assertEqual(result["all_predictions"]["match_rate"], 0.5)
        self.assertEqual(result["within_scope_predictions"]["match_rate"], 1)
        self.assertEqual(result["boundary_predictions"]["mismatched"], 1)
        self.assertEqual(result["observed_changed_cell_coverage_micro"], 0.5)
        self.assertEqual(result["stop_interventions"], 1)
        self.assertEqual(result["rejection_reasons"], {"prediction_precondition_failed": 1})

    def test_stationary_matches_are_not_changed_cell_predictions(self):
        events = event_pair()
        events[-1]["prediction"].update(predicted_changed_cell_count=0, stationary_cells_prediction=True,
                                        observed_changed_cells_checked=0)
        result = analysis.prediction_metrics(events)
        self.assertEqual(result["stationary_cell_predictions"]["matched"], 1)
        self.assertEqual(result["changed_cell_predictions"]["scored"], 0)
        self.assertEqual(result["observed_changed_cell_coverage_micro"], 0)

    def test_revised_rule_needs_new_future_validation(self):
        events = event_pair(matched=False) + event_pair(2, version=2, step=1)
        self.assertEqual(analysis.prediction_metrics(events)["revised_after_mismatch_later_predictions"]["scored"], 0)
        events += event_pair(3, version=2, step=2, revision=False)
        result = analysis.prediction_metrics(events)
        self.assertEqual(result["revised_rule_versions"], 1)
        self.assertEqual(result["revised_after_mismatch_later_predictions"]["matched"], 1)

    def test_reset_costs_are_not_best_episode_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slot = {**runner.schedule(1)[0], "status": "completed", "elapsed_seconds": 1}
            run = root / slot["slot"]
            (run / "artifacts").mkdir(parents=True)
            events = [{"type": "initial", "score": 0}, {"type": "action", "score": 1},
                      {"type": "action", "action_name": "RESET", "score": 0},
                      {"type": "action", "score": 0}, {"type": "action", "score": 1}]
            (run / "artifacts" / "ls20_events.jsonl").write_text("\n".join(map(json.dumps, events)))
            (run / "benchmark.json").write_text(json.dumps({"game_runs": [{"number_of_levels": 2,
                "base_actions_per_level": [1, 2], "actions_per_level": [3, 1], "levels_completed": 1,
                "history": [{}, {}, {}, {}], "final_score": 100 / 27}]}))
            result = analysis.analyze_slot(root, slot, {"model": runner.MODEL, "max_actions": 1200,
                                          "per_run_seconds": 1800, "effective_solver_seconds": 1740})
            self.assertEqual(result["actions_per_level_all_resets"], [3, 1])
            self.assertAlmostEqual(result["score_cap_1_15"], 100 / 27)
            self.assertEqual(len(result["reset_segments"]), 2)
            self.assertTrue(result["reset_segments"][0]["reset_progress_drop"])
            self.assertTrue(result["benchmark_covers_all_events"])

    def test_launch_error_remains_terminal_zero_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(output=Path(temporary), app_dir=Path(temporary),
                                      per_run_seconds=3, cleanup_seconds=1)
            result = runner.run_slot(runner.schedule(1)[0], args, ["/missing-fixture-command"])
            self.assertEqual(result["status"], "launch_error")
            self.assertEqual(result["actions"], 0)
            self.assertLess(result["elapsed_seconds"], 3)

    def test_hard_timeout_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(runner.os.environ, {"LOCAL_ANALYZER_API_KEY": "fixture-secret"}):
            args = argparse.Namespace(output=Path(temporary), app_dir=Path(temporary),
                                      per_run_seconds=2, cleanup_seconds=0.6)
            code = "import signal,time; print('fixture-secret',flush=True); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(90)"
            result = runner.run_slot(runner.schedule(1)[0], args, [sys.executable, "-c", code])
            self.assertEqual(result["status"], "timeout")
            self.assertLess(result["elapsed_seconds"], 2)
            self.assertEqual(result["returncode"], -9)
            log = (Path(result["run_dir"]) / "process.log").read_text()
            self.assertNotIn("fixture-secret", log)
            self.assertIn("[REDACTED]", log)


if __name__ == "__main__":
    unittest.main()
