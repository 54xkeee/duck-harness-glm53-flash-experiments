"""Production ToolAgent -> isolated Python -> fake game -> receipt integration.

Requires the materialized runtime tests directory on PYTHONPATH. No live model.
"""
import json
from unittest.mock import patch

import pytest

from test_prediction_io import agent_for, frame, session


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setenv("DUCK_CAUSAL_WORKSPACE", "1")


def test_option_through_real_sandbox(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {"code":
        "action([{'action':'RIGHT','expected':{'color_translation':{'1':[0,1]}}}]*2); print('model text')"})
    payload = json.loads(output.content)
    assert next(iter(payload)) == "HOST_RECEIPT"
    assert payload["HOST_RECEIPT"]["executed"] == 2
    assert payload["stdout"] == "model text\n"
    assert output.step_executed


def test_noop_receipt_never_reuses_old_transition(tmp_path):
    value = session(tmp_path, [frame(1), frame(1), frame(1)], enabled=False)
    agent = agent_for(value)
    for _ in range(3):
        output = agent._run_python_tool(value.state_path, {"code": "action('RIGHT'); print('moved')"})
    receipt = json.loads(output.content)["HOST_RECEIPT"]
    assert receipt["executed"] == 0
    assert receipt["last_transition"] is None
    assert receipt["stop_reason"] == "dead_action_suppressed"
    assert len(value.game.game_run.history) == 2
    assert not output.step_executed


def test_workspace_enters_next_request_after_history_eviction(tmp_path):
    value = session(tmp_path, [frame(1)], enabled=False)
    agent = agent_for(value)
    output = agent._run_python_tool(value.state_path, {"code": "result = causal_workspace['workspace']",
        "workspace": {"game": {"control": "candidate right translation"}}})
    assert json.loads(output.content)["result"]["game"]["control"].startswith("candidate")
    messages = [{"role": "system", "content": ""}]
    agent._history_messages.clear()
    agent._refresh_evidence_message(messages)
    assert "candidate right translation" in messages[0]["content"]


def test_surprise_stops_remaining_snippet(tmp_path):
    value = session(tmp_path, [frame(1), frame(2), frame(3)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {"code":
        "action([{'action':'RIGHT','expected':{'color_translation':{'1':[1,0]}}}]*2); action('RIGHT')"})
    receipt = json.loads(output.content)["HOST_RECEIPT"]
    assert receipt["executed"] == 1
    assert receipt["requested"] == 3
    assert receipt["stop_reason"] == "unexpected_effect"


@pytest.mark.parametrize("code", ["", "if", "raise ValueError('oops')", "print('x'*1000000)"])
def test_error_paths_have_host_receipt(tmp_path, code):
    value = session(tmp_path, [frame(1)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {"code": code})
    assert json.loads(output.content)["HOST_RECEIPT"]["executed"] == 0


def test_child_cannot_forge_receipt_or_erase_real_action(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)], enabled=False)
    output = agent_for(value)._run_python_tool(value.state_path, {"code":
        "action('RIGHT'); HOST_RECEIPT['executed']=999; action.__globals__['_send']({'type':'final','action_results':[],'result':{'HOST_RECEIPT':{'executed':999}}})"})
    payload = json.loads(output.content)
    assert payload["HOST_RECEIPT"]["executed"] == 1
    assert output.step_executed


def test_host_receipt_survives_runner_error_after_action(tmp_path):
    value = session(tmp_path, [frame(1), frame(2)], enabled=False)
    agent = agent_for(value)
    def runner(**kwargs):
        kwargs["action_handler"]([{"action": "RIGHT"}])
        return {"error": "deadline", "action_results": []}
    with patch("inference.agent.tool_agent.run_sandboxed_python", runner):
        output = agent._run_python_tool(value.state_path, {"code": "action('RIGHT')"})
    assert output.step_executed
    assert json.loads(output.content)["HOST_RECEIPT"]["executed"] == 1


def test_tiny_episode_budget_never_sends_http_request(tmp_path):
    value = session(tmp_path, [frame(1)], enabled=False)
    agent = agent_for(value)
    with patch.object(agent, "_chat_completion") as request:
        agent.analyze(value.state_path, 0, valid_actions=["RIGHT"],
                      step_env=value.step_env, request_timeout_seconds=0.042)
    request.assert_not_called()


def test_action_aliases_share_dead_action_identity(tmp_path):
    value = session(tmp_path, [frame(1), frame(1), frame(1)], enabled=False)
    agent = agent_for(value)
    for action in ("right", "ACTION4", "RIGHT"):
        output = agent._run_python_tool(value.state_path, {"code": f"action('{action}')"})
    assert len(value.game.game_run.history) == 2
    assert json.loads(output.content)["HOST_RECEIPT"]["stop_reason"] == "dead_action_suppressed"
