import ast
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import arcengine
import pytest
import requests

from inference.agent.evidence_controller import EvidenceController
from inference.agent.prediction_controller import PredictionController
from inference.agent.runtime_state import Frame
from inference.agent.tool_agent import ToolAgent
from inference.framework.solver import _HarnessGameSession
from inference.utils.grid_utils import ARC_COLOR_CHARS
from inference.utils.viewer_artifacts import raw_events_jsonl_sidecar_path


def frame(col, step=0):
    grid = [[0] * 6 for _ in range(3)]
    grid[1][col] = 1
    return Frame(tuple(map(tuple, grid)), step, 1)


def prediction(col, target, **changes):
    value = {
        "rule_id": "right",
        "rule": "move right when the adjacent cell is empty",
        "conditions": [[1, col, ARC_COLOR_CHARS[1]]],
        "expected": {"cells": [[1, col, ARC_COLOR_CHARS[0]], [1, target, ARC_COLOR_CHARS[1]]]},
        "depends_on": [],
    }
    value.update(changes)
    return value


class FakeGame:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.game_run = SimpleNamespace(
            history=[], state="playing", game_id="fixture", levels_completed=0,
            number_of_levels=2, actions_per_level=[], final_score=None,
        )
        self.number_of_levels = 2
        self.current_state = self.state(next(self.frames))
        self.before_execute = None

    @staticmethod
    def state(value):
        return SimpleNamespace(
            frame=SimpleNamespace(data=value.grid), levels_completed=0, won=False,
            available_actions=[1, 2, 3, 4], just_won_level=False,
            raw=SimpleNamespace(state=arcengine.GameState.NOT_FINISHED),
        )

    def execute_action(self, action, **kwargs):
        if self.before_execute:
            self.before_execute()
        self.game_run.history.append(action)
        self.current_state = self.state(next(self.frames))
        return self.current_state


def session(tmp_path, frames, *, enabled=True):
    value = _HarnessGameSession(
        solver=SimpleNamespace(max_actions_per_game=None, max_runtime_s_per_game=None,
                               job_dir=tmp_path, label="fixture"),
        game=FakeGame(frames), analyzer=SimpleNamespace(generated_tokens=0),
        game_index=0, pass_index=0, state_path=tmp_path / "tool_runtime_state.json",
        transcript_path=tmp_path / "transcript.txt", analysis_html_relpath="analysis.html",
        stop_event=threading.Event(), viewer_data_path=tmp_path / "viewer.json",
        evidence_controller=EvidenceController(False, False),
        prediction_controller=PredictionController(enabled),
    )
    value.seed_initial_history()
    value.write_runtime_state()
    return value


def agent_for(value):
    agent = ToolAgent(model="glm-5.3-flash", provider="bigmodel",
                      base_url="https://example.invalid/v4", api_key="test-secret", timeout=3)
    agent._ensure_session(value.state_path)
    agent._step_env_callback = value.step_env
    agent._current_valid_actions = ["RIGHT"]
    return agent


def events(value):
    return [json.loads(line) for line in raw_events_jsonl_sidecar_path(value.viewer_data_path).read_text().splitlines()]


def test_commitment_persisted_before_engine_and_match_survives_full_stack(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)])
    pred = prediction(1, 2)

    def before_execute():
        recorded = events(value)
        assert recorded[-1]["type"] == "prediction_commitment"
        assert recorded[-1]["prediction_commitment"]["prediction"] == pred
        assert len(value.game.game_run.history) == 0

    value.game.before_execute = before_execute
    result = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"result = action({{'action':'RIGHT', 'prediction':{pred!r}}})",
    })
    assert result.step_executed, result.content
    assert len(value.game.game_run.history) == 1
    assert events(value)[-1]["prediction"]["status"] == "matched"
    assert events(value)[-1]["prediction"]["checked_cell_count"] == 2


def test_mismatch_stops_batch_and_later_calls_even_if_result_ignored(tmp_path):
    value = session(tmp_path, [frame(1), frame(1), frame(2), frame(3)])
    pred = prediction(1, 2)
    spec = {"action": "RIGHT", "prediction": pred}
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({[spec, spec]!r}); action('RIGHT'); action('RIGHT')",
    })
    assert output.step_executed, output.content
    assert len(value.game.game_run.history) == 1
    assert events(value)[-1]["prediction"]["status"] == "mismatched"
    assert events(value)[-1]["prediction_control"]["executed_count"] == 1


@pytest.mark.parametrize("pred,reason", [
    (prediction(2, 3), "prediction_precondition_failed"),
    ({"unexpected": True}, "prediction_invalid"),
    (prediction(1, 2, depends_on=[{"rule_id": "missing", "version": 1}]), "prediction_stale_dependency"),
])
def test_preexecution_rejection_stops_remaining_snippet(tmp_path, pred, reason):
    value = session(tmp_path, [frame(1), frame(2)])
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({{'action':'RIGHT','prediction':{pred!r}}}); action('RIGHT')",
    })
    assert not output.step_executed, output.content
    assert not value.game.game_run.history
    assert events(value)[-1]["type"] == "prediction_rejected"
    assert events(value)[-1]["stop_reason"] == reason


def test_exploration_one_action_then_next_snippet_may_explore(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)])
    agent = agent_for(value)
    for count in (1, 2):
        output = agent._run_python_tool(value.state_path, {"code": "action(['RIGHT','RIGHT']); action('RIGHT')"})
        assert output.step_executed
        assert len(value.game.game_run.history) == count
    assert events(value)[-1]["prediction"]["status"] == "unpredicted"


def test_disabled_baseline_ignores_prediction_and_preserves_batches(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": "action([{'action':'RIGHT','prediction':{'invalid':True}}, 'RIGHT'])",
    })
    assert output.step_executed
    assert len(value.game.game_run.history) == 2
    assert "prediction_state" not in json.loads(value.state_path.read_text())
    assert all("prediction" not in event for event in events(value))


def test_host_control_survives_stdout(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)])
    spec = {"action": "RIGHT", "prediction": prediction(1, 2)}
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({spec!r}); print('visible stdout')",
    })
    payload = json.loads(output.content)
    assert payload["stdout"] == "visible stdout\n"
    assert payload["host_control"] == {
        "executed_count": 1, "stop_reason": None, "error": None,
        "rule_id": "right", "version": 1, "mismatched_checks": [],
    }


def test_host_control_long_stdout_stays_within_total_json_budget(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)])
    agent = agent_for(value)
    agent._tool_output_chars = 512
    spec = {"action": "RIGHT", "prediction": prediction(1, 2)}
    output = agent._run_python_tool(value.state_path, {
        "code": f"action({spec!r}); print('\\\"\\\\中文' * 10000)",
    })
    payload = json.loads(output.content)
    assert len(output.content) <= agent._tool_output_chars
    assert payload["truncated"] is True
    assert payload["stdout"]
    assert payload["host_control"]["executed_count"] == 1
    assert payload["host_control"]["rule_id"] == "right"
    assert payload["host_control"]["version"] == 1


def test_host_control_precondition_rejection_reports_current_error(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)])
    spec = {"action": "RIGHT", "prediction": prediction(2, 3)}
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({spec!r}); action('RIGHT'); print('still visible')",
    })
    control = json.loads(output.content)["host_control"]
    assert not output.step_executed
    assert not value.game.game_run.history
    assert control == {
        "executed_count": 0, "stop_reason": "prediction_precondition_failed",
        "error": "prediction_precondition_failed", "rule_id": "right", "version": None,
        "mismatched_checks": [{"row": 1, "col": 2, "expected": ARC_COLOR_CHARS[1],
                               "actual": ARC_COLOR_CHARS[0]}],
    }


def test_host_control_mismatch_reports_actual_checks_and_stops(tmp_path):
    value = session(tmp_path, [frame(1), frame(1), frame(2)])
    spec = {"action": "RIGHT", "prediction": prediction(1, 2)}
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({[spec, spec]!r}); action('RIGHT'); print('ignored result')",
    })
    control = json.loads(output.content)["host_control"]
    assert output.step_executed
    assert len(value.game.game_run.history) == 1
    assert control["executed_count"] == 1
    assert control["stop_reason"] == "prediction_mismatch"
    assert control["rule_id"] == "right"
    assert control["version"] == 1
    assert control["mismatched_checks"] == [
        {"field": "cell", "row": 1, "col": 1, "expected": ARC_COLOR_CHARS[0],
         "actual": ARC_COLOR_CHARS[1], "matched": False},
        {"field": "cell", "row": 1, "col": 2, "expected": ARC_COLOR_CHARS[1],
         "actual": ARC_COLOR_CHARS[0], "matched": False},
    ]


def test_host_control_partial_batch_preserves_count_and_rejected_rule(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)])
    specs = [
        {"action": "RIGHT", "prediction": prediction(1, 2)},
        {"action": "RIGHT", "prediction": prediction(1, 2, rule_id="rejected")},
    ]
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": f"action({specs!r}); action('RIGHT'); print('partial batch')",
    })
    control = json.loads(output.content)["host_control"]
    assert output.step_executed
    assert len(value.game.game_run.history) == 1
    assert control["executed_count"] == 1
    assert control["stop_reason"] == "prediction_precondition_failed"
    assert control["error"] == "prediction_precondition_failed"
    assert control["rule_id"] == "rejected"
    assert control["version"] is None
    assert control["mismatched_checks"] == [
        {"row": 1, "col": 1, "expected": ARC_COLOR_CHARS[1], "actual": ARC_COLOR_CHARS[0]},
    ]


def test_host_control_print_only_does_not_reuse_previous_transition(tmp_path):
    value = session(tmp_path, [frame(1), frame(1)])
    agent = agent_for(value)
    spec = {"action": "RIGHT", "prediction": prediction(1, 2)}
    previous = agent._run_python_tool(value.state_path, {"code": f"action({spec!r})"})
    assert json.loads(previous.content)["host_control"]["mismatched_checks"]
    output = agent._run_python_tool(value.state_path, {"code": "print('analysis only')"})
    payload = json.loads(output.content)
    assert not output.step_executed
    assert len(value.game.game_run.history) == 1
    assert payload["stdout"] == "analysis only\n"
    assert payload["host_control"] == {
        "executed_count": 0, "stop_reason": None, "error": None,
        "rule_id": None, "version": None, "mismatched_checks": [],
    }
    assert "result" not in payload


def test_host_control_disabled_preserves_baseline_stdout_payload(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {
        "code": "action(['RIGHT', 'RIGHT']); print('baseline')",
    })
    assert output.step_executed
    assert len(value.game.game_run.history) == 2
    assert json.loads(output.content) == {"tool": "python", "returncode": 0, "stdout": "baseline\n"}


def test_stationary_prediction_is_success_not_automatic_counterexample(tmp_path):
    value = session(tmp_path, [frame(1), frame(1), frame(1)])
    pred = prediction(1, 2, rule="remain still when blocked", expected={
        "cells": [[1, 1, ARC_COLOR_CHARS[1]]], "board_changed": False,
    })
    result = value.step_env({"actions": [{"action": "RIGHT", "prediction": pred}] * 2})
    assert result["executed_count"] == 2
    assert result["prediction"]["status"] == "matched"
    assert not result["prediction_control"]["intervened"]


def test_second_precondition_failure_records_already_executed_count(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)])
    spec = {"action": "RIGHT", "prediction": prediction(1, 2)}
    result = value.step_env({"actions": [spec, spec]})
    assert result["executed_count"] == 1
    assert result["stop_reason"] == "prediction_precondition_failed"
    assert events(value)[-1]["prediction_control"]["executed_count"] == 1


def test_prediction_only_state_refreshed_and_usage_logs_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCK_PREDICTION_CHECK", "1")
    value = session(tmp_path, [frame(1), frame(2)])
    agent = agent_for(value)
    messages = [{"role": "system", "content": "old"}]
    agent._refresh_evidence_message(messages)
    assert "HOST PREDICTION STATE" in messages[0]["content"]
    assert "HOST OBSERVATIONS" not in messages[0]["content"]
    assert "board_changed:false" in messages[0]["content"]
    value.step_env({"action": "RIGHT", "prediction": prediction(1, 2)})
    agent._refresh_evidence_message(messages)
    assert '"ticket_id": 1' in messages[0]["content"]
    agent._record_usage("request")
    usage = json.loads((tmp_path / "artifacts" / "usage.jsonl").read_text().splitlines()[-1])
    assert usage["prediction_injected"] is True
    assert usage["prediction_ids"] == [1]
    assert usage["beliefs_injected"] is False
    output = agent._run_python_tool(value.state_path, {
        "code": "prediction_state['rules'][0]['status']='forged'; result=prediction_state['scope']",
    })
    assert not output.step_executed
    assert value.prediction_controller.snapshot()["rules"][0]["status"] == "predictively_supported"


def test_prediction_prompt_example_uses_only_valid_arc_colors(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCK_PREDICTION_CHECK", "1")
    agent = agent_for(session(tmp_path, [frame(1)]))
    messages = [{"role": "system", "content": ""}]
    agent._refresh_evidence_message(messages)
    assert "rows = current_frame.ascii.splitlines(); rows[row][col]" in messages[0]["content"]
    assert "rather than repeatedly omitting predictions" in messages[0]["content"]
    assert "counterexample about the game, not an interface failure" in messages[0]["content"]
    example = messages[0]["content"].split("inside that action object: action(", 1)[1].split("). Coordinates", 1)[0]
    pred = ast.literal_eval(example)["prediction"]
    cells = pred["conditions"] + pred["expected"]["cells"]
    assert cells
    assert all(len(color) == 1 and color in ARC_COLOR_CHARS for _, _, color in cells)


def test_api_error_code_is_sanitized_and_three_consecutive_failures_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCK_API_FAILURE_LIMIT", "3")
    value = session(tmp_path, [frame(1)], enabled=False)
    agent = agent_for(value)
    value.analyzer = agent
    response = requests.Response()
    response.status_code = 429
    response._content = b'{"error":{"code":"1113","message":"test-secret"}}'
    with patch("inference.agent.tool_agent.requests.post", return_value=response):
        for count in range(1, 4):
            with pytest.raises(requests.RequestException):
                agent._chat_completion([], tools=None)
            assert agent.api_failure_limit_reached is (count == 3)
    assert value.should_stop()
    path = tmp_path / "artifacts" / "usage.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[-1]["provider_error_code"] == "1113"
    assert records[-1]["consecutive_api_failures"] == 3
    assert "test-secret" not in path.read_text()
    success = Mock(status_code=200)
    success.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    with patch("inference.agent.tool_agent.requests.post", return_value=success):
        agent._chat_completion([], tools=None)
    assert not agent.api_failure_limit_reached


def test_api_failure_limit_default_off(tmp_path):
    value = session(tmp_path, [frame(1)], enabled=False)
    agent = agent_for(value)
    for _ in range(4):
        agent._record_usage("error", error_type="ReadTimeout")
    assert not agent.api_failure_limit_reached
