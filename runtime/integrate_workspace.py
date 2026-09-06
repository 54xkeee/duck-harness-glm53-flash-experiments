"""Small checked source transform over the byte-verified feedback-fixed agent.

Fail on drift rather than silently omitting an integration hook. Frozen sources
are never edited; this runs only while materializing a new runtime directory.
"""

GUIDE = '''
Compact Causal Workspace is active. HOST_RECEIPT precedes untrusted Python
output and is the only action accounting authority. Workspace fields are model
hypotheses, not environment facts. Set python tool argument workspace to an
object containing game/level/episode objects and optionally a list of rules.
Each rule has exactly subject (string), action (UP/DOWN/LEFT/RIGHT/SPACE/MOUSE/RESET),
conditions (structured object), effect (semantic event object). Host fingerprints
the structure; labels are not accepted. Arbitrary natural-language synonyms are
not canonicalized. Conditions are descriptive, not a verified simulator.
Python exposes causal_workspace with state_key, workspace and recent evidence.
Use action([{'action':'RIGHT','expected':{'color_translation':{'1':[0,1]}}}, ...])
for short options. Color translation means an entire rigid color mask, not player
identity. Other expected fields: board_changed, level_changed, terminal, reward.
Use state_key on a step for an exact precondition; rule_id ties an expected effect
to a stored rule. Contradictions invalidate that rule. No prediction is required
for ordinary exploration. Host stops on surprise, no effect, level/reset or end.
Repeated exact-state no-effect actions are suppressed after two observations;
retry_reason permits a deliberate additional probe and is retained in evidence.
Game hypotheses survive levels as priors; level/episode layout and plans expire.
Keep the complete workspace under 6000 serialized characters and 16 rules.
'''


def integrate(source):
    def replace(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError(f"Workspace integration anchor drift: {old[:90]!r}")
        source = source.replace(old, new)

    replace('from __future__ import annotations',
            'from __future__ import annotations\nfrom inference.agent.causal_workspace import CausalWorkspace, render_receipt')
    replace('            self._session_runtime_dir = runtime_dir',
            '            self._session_runtime_dir = runtime_dir\n'
            '            self._ccw = CausalWorkspace(state_path, True) if _get_env_bool("DUCK_CAUSAL_WORKSPACE", False) else None')
    replace('        messages[0] = {"role": "system", "content": content}',
            '        if self._ccw is not None:\n'
            '            self._ccw.sync_scope()\n'
            f'            content += {GUIDE!r} + json.dumps(self._ccw.digest(), ensure_ascii=True)\n'
            '        messages[0] = {"role": "system", "content": content}')
    replace('                            "code": {',
            '                            "workspace": {"type": "object", "description": '
            '"Optional bounded game/level/episode hypotheses and structured rules; requires DUCK_CAUSAL_WORKSPACE."},\n'
            '                            "code": {')
    replace('                normalized.append(entry)',
            '                for key in ("expected", "state_key", "rule_id", "retry_reason"):\n'
            '                    if key in item:\n'
            '                        entry[key] = item[key]\n'
            '                normalized.append(entry)')
    replace('        code = str(arguments.get("code", "")).rstrip()',
            '        if self._ccw is not None:\n'
            '            try:\n'
            '                self._ccw.begin(arguments.get("workspace"))\n'
            '            except (ValueError, TypeError) as exc:\n'
            '                self._ccw.receipt["stop_reason"] = "invalid_workspace"\n'
            '                return _ToolDispatchResult(json.dumps({"HOST_RECEIPT": self._ccw.receipt, "error": str(exc)}))\n'
            '        code = str(arguments.get("code", "")).rstrip()')
    replace('return _ToolDispatchResult(json.dumps({"error": "python requires a non-empty `code` string."}, indent=2))',
            'return _ToolDispatchResult(json.dumps({**({"HOST_RECEIPT": self._ccw.receipt} if self._ccw else {}), "error": "python requires a non-empty `code` string."}, indent=2))')
    replace('return _ToolDispatchResult(json.dumps({"error": f"Python syntax error: {exc}"}, indent=2))',
            'return _ToolDispatchResult(json.dumps({**({"HOST_RECEIPT": self._ccw.receipt} if self._ccw else {}), "error": f"Python syntax error: {exc}"}, indent=2))')
    replace('                "current_frame": current_frame_payload,',
            '                "causal_workspace": self._ccw.digest() if self._ccw else {},\n'
            '                "HOST_RECEIPT": dict(self._ccw.receipt) if self._ccw else {},\n'
            '                "current_frame": current_frame_payload,')
    replace('            if terminal_action_result is not None:',
            '            if terminal_action_result is not None and self._ccw is None:')
    replace('            normalized_actions = self._normalize_python_actions(actions)',
            '            normalized_actions = self._normalize_python_actions(actions)\n'
            '            if self._ccw:\n'
            '                for item in normalized_actions:\n'
            '                    item["action"] = to_model_action(item["action"])')
    replace('            raw_payload = self._step_env_callback({"actions": normalized_actions})',
            '            raw_payload = (self._ccw.execute(normalized_actions, self._step_env_callback)\n'
            '                           if self._ccw else self._step_env_callback({"actions": normalized_actions}))')
    replace('        step_executed = any(bool(item.get("executed")) for item in action_results)',
            '        step_executed = (bool(self._ccw.receipt["executed"]) if self._ccw else\n'
            '                         any(bool(item.get("executed")) for item in action_results))')
    replace('        return _ToolDispatchResult(\n            self._render_prediction_payload(payload, host_control) if prediction_feedback else',
            '        if self._ccw is not None:\n'
            '            if rendered_error and not self._ccw.receipt["stop_reason"]:\n'
            '                self._ccw.receipt["stop_reason"] = "python_error"\n'
            '            self._ccw.append("tool_receipt", receipt=self._ccw.receipt)\n'
            '            return _ToolDispatchResult(render_receipt(self._ccw.receipt, payload, self._tool_output_chars), step_executed=step_executed)\n'
            '        return _ToolDispatchResult(\n            self._render_prediction_payload(payload, host_control) if prediction_feedback else')
    replace('                        if remaining <= 0:',
            '                        if remaining < min(self._timeout or 20.0, _get_env_float("DUCK_MIN_REQUEST_SECONDS", 20.0)):')
    compile(source, "tool_agent.py", "exec")
    return source
