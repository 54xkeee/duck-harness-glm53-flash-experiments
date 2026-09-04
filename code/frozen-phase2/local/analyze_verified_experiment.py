#!/usr/bin/env python3
"""Offline supplement for the frozen LS20 experiment; never invokes the harness."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import statistics

from run_verified_experiment import ARMS, collect_metrics, read_jsonl, write_json


TOKEN_PATHS = {
    "prompt_tokens": ("prompt_tokens",),
    "completion_tokens_including_reasoning": ("completion_tokens",),
    "reasoning_tokens": ("completion_tokens_details", "reasoning_tokens"),
    "cached_input_tokens": ("prompt_tokens_details", "cached_tokens"),
    "total_tokens": ("total_tokens",),
}
HEADER = re.compile(
    r"(?m)^\[(SYSTEM PROMPT|USER PROMPT|MODEL RESPONSE META|THINKING|ASSISTANT|"
    r"ANALYZER STATUS|TOOL CALL: [^\]\n]+|TOOL RESULT: [^\]\n]+|PYTHON TOOL RESULT)\]\r?$"
    r"|^---[^\n]*tool-agent ---\r?$")
ERROR_TEXT = re.compile(r"(?m)^Traceback \(most recent call last\):|^error:\s*$|^\w*(?:Error|Exception):")
ERROR_CLASS = re.compile(r"(?m)^(\w*(?:Error|Exception)):")


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def complete_mean(values):
    return statistics.mean(values) if values and all(numeric(value) for value in values) else None


def percentile95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None


def token_values(response):
    values = {}
    for name, path in TOKEN_PATHS.items():
        value = response.get("usage")
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        values[name] = value if numeric(value) else None
    prompt, cached = values["prompt_tokens"], values["cached_input_tokens"]
    values["uncached_input_tokens"] = prompt - cached if prompt is not None and cached is not None and prompt >= cached else None
    return values


def usage_metrics(rows):
    requests = {row["_key"]: row for row in rows if row.get("event") == "request"}
    # A terminal request is counted once, even if a partial artifact was appended twice.
    terminal = {row["_key"]: row for row in rows if row.get("event") in ("response", "error")}
    responses = [row for row in terminal.values() if row.get("event") == "response"]
    errors = [row for row in terminal.values() if row.get("event") == "error"]
    latency = [row["elapsed_seconds"] for row in terminal.values() if numeric(row.get("elapsed_seconds"))]
    token_rows = [token_values(row) for row in responses]
    totals, observed, coverage = {}, {}, {}
    for field in (*TOKEN_PATHS, "uncached_input_tokens"):
        known = [row[field] for row in token_rows if row[field] is not None]
        totals[field] = sum(known) if token_rows and len(known) == len(token_rows) else None
        observed[field] = sum(known) if known else None
        coverage[field] = len(known)
    injected = [row for row in requests.values() if row.get("beliefs_injected") is True
                and bool(row.get("belief_transition_ids"))]
    return {
        "requests": len(requests), "responses": len(responses), "errors": len(errors),
        "unfinished_requests": len(requests.keys() - terminal.keys()),
        "provider_error_types": dict(Counter(row.get("error_type", "missing_type") for row in errors)),
        "provider_error_http_status": dict(Counter(str(row.get("http_status")) for row in errors)),
        "latency_completed_requests_n": len(latency),
        "latency_median_seconds": statistics.median(latency) if latency else None,
        "latency_p95_seconds": percentile95(latency),
        "latency_missing_terminal_records": len(terminal) - len(latency),
        "tokens_all_observed_responses": totals, "tokens_known_field_subtotal": observed,
        "token_field_response_coverage": coverage, "belief_fact_injection_requests": len(injected),
        "belief_fact_injection_rate": len(injected) / len(requests) if requests else None,
        "distinct_injected_evidence_ids": len({(row["_key"][:-1], fact) for row in injected for fact in row["belief_transition_ids"]}),
        "request_model_settings": [dict(zip(("model", "provider", "reasoning_effort", "max_output_tokens"), values))
                                   for values in sorted({tuple(row.get(key) for key in
                                        ("model", "provider", "reasoning_effort", "max_output_tokens"))
                                        for row in requests.values()}, key=str)],
    }


def transcript_metrics(paths):
    results = errors = 0
    classes = Counter()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        markers = list(HEADER.finditer(text))
        for index, marker in enumerate(markers):
            label = marker.group(1) or ""
            if not (label.startswith("TOOL RESULT:") or label == "PYTHON TOOL RESULT"):
                continue
            results += 1
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            body = text[marker.end():end]
            if ERROR_TEXT.search(body):
                errors += 1
                # Count result sections, not repeated lines or viewer transcript copies.
                names = ERROR_CLASS.findall(body)
                classes[names[-1] if names else "unclassified_error_text"] += 1
    return {"tool_result_sections": results, "tool_error_text_results": errors,
            "tool_error_text_classes": dict(classes), "transcript_files": len(paths)}


def load_usage(run_dir, warnings):
    rows = []
    for path in sorted((run_dir / "artifacts").glob("*usage.jsonl")):
        for row in read_jsonl(path, warnings):
            row["_key"] = (run_dir.name, path.name, row.get("request_id"))
            rows.append(row)
    return rows


def analyze_slot(root, slot, config):
    run_dir = root / slot["slot"]
    warnings = []
    actions, initials = [], 0
    for path in sorted((run_dir / "artifacts").glob("*_events.jsonl")):
        events = read_jsonl(path, warnings)
        actions.extend(row for row in events if row.get("type") == "action")
        initials += sum(row.get("type") == "initial" for row in events)
    metrics = collect_metrics(run_dir)
    benchmark = run_dir / "benchmark.json"
    game = json.loads(benchmark.read_text(encoding="utf-8")).get("game_runs", [{}])[0] if benchmark.exists() else {}
    game_outcome = game.get("state", metrics.get("game_status"))
    inner_budget = config["per_run_seconds"] - config.get("cleanup_seconds", 30)
    soft_budget = inner_budget - min(600, inner_budget / 2)
    soft_end = None
    stdout = run_dir / "stdout.log"
    if stdout.exists():
        with stdout.open(encoding="utf-8") as stream:
            for line in stream:
                match = re.search(r"deploy\.inline: soft_end_time\s*=\s*(\S+)", line)
                if match:
                    soft_end = match.group(1)
                    break
    usage = usage_metrics(load_usage(run_dir, warnings))
    controls = [row["probe_control"] for row in actions if row.get("probe_control", {}).get("enabled") is True]
    interventions = sum(control.get("intervened") is True for control in controls)
    belief_events = sum("beliefs" in row.get("evidence", {}) for row in actions)
    first_success = next((index for index, row in enumerate(actions, 1) if row.get("score", 0) >= 1), None)
    terminal = slot.get("status") not in ("pending", "running")
    expected = {"model": config["model"], "provider": "bigmodel", "reasoning_effort": "low", "max_output_tokens": 8192}
    model_match = bool(usage["request_model_settings"]) and all(value == expected for value in usage["request_model_settings"])
    probe_match = bool(controls) if slot["probe"] else not controls
    belief_match = (belief_events > 0 and usage["belief_fact_injection_requests"] > 0) if slot["belief"] else (belief_events == 0 and usage["belief_fact_injection_requests"] == 0)
    elapsed = slot.get("elapsed_seconds")
    reasons = []
    for satisfied, reason in (
        (slot.get("status") == "completed", "process_not_completed"),
        (0 < len(actions) <= config["max_actions"], "zero_or_over_budget_actions"),
        (numeric(elapsed) and elapsed <= config["per_run_seconds"], "end_to_end_time_missing_or_over_limit"),
        (initials == 1, "fresh_initial_event_not_exactly_one"), (model_match, "model_settings_missing_or_mismatched"),
        (probe_match, "probe_assignment_observation_mismatch"), (belief_match, "belief_assignment_observation_mismatch"),
        (not slot["probe"] or interventions > 0, "no_actual_probe_intervention"),
    ):
        if not satisfied:
            reasons.append(reason)
    return {
        "slot": slot["slot"], "arm": slot["arm"], "replicate": slot["replicate"], "status": slot["status"],
        "process_exit_status": slot["status"], "game_outcome": game_outcome,
        "budget_termination": slot["status"] == "timeout" or game_outcome == "cancelled",
        "budget_termination_kind": "outer_runner_timeout" if slot["status"] == "timeout" else "inner_soft_budget_cancelled" if game_outcome == "cancelled" else None,
        "inline_budget_seconds": inner_budget, "inline_soft_budget_seconds": soft_budget,
        "inline_soft_end_time_local": soft_end, "game_final_wallclock_seconds": game.get("final_wallclock_seconds"),
        "finished": terminal, "probe_assigned": slot["probe"], "belief_assigned": slot["belief"],
        "first_level_success": int(first_success is not None) if terminal else None,
        "first_level_actions_to_success": first_success,
        "level1_actions_spent": (metrics["actions_per_level_rebuilt"] or [0])[0],
        "levels_completed": metrics["levels_completed"], "score_cap_1_15": metrics["score_cap_1_15"],
        "raw_score": metrics["raw_score"], "actions": len(actions),
        "no_change_actions": metrics["no_change_actions"], "longest_no_change_streak": metrics["longest_no_change_streak"],
        "elapsed_seconds": elapsed, "time_limit_seconds": config["per_run_seconds"],
        "within_end_to_end_limit": elapsed <= config["per_run_seconds"] if numeric(elapsed) else None,
        "probe_enabled_calls_observed": len(controls), "probe_interventions": interventions,
        "belief_summary_action_events": belief_events, "probe_assignment_match": probe_match,
        "belief_assignment_match": belief_match, "protocol_qualified": not reasons,
        "protocol_exclusion_reasons": reasons, "usage": usage,
        "tools": transcript_metrics(sorted((run_dir / "transcripts").glob("*.txt"))),
        "artifact_warnings": warnings + metrics["artifact_warnings"],
    }


def arm_metrics(members):
    all_finished = bool(members) and all(row["finished"] for row in members)
    output = {"assigned_n": len(members), "finished_n": sum(row["finished"] for row in members),
              "protocol_qualified_n": sum(row["protocol_qualified"] for row in members)}
    for metric in ("first_level_success", "score_cap_1_15", "level1_actions_spent", "elapsed_seconds"):
        output["mean_" + metric] = complete_mean([row[metric] for row in members]) if all_finished else None
    successes = [row["first_level_actions_to_success"] for row in members if row["first_level_success"] == 1]
    output.update(first_level_successes=len(successes),
                  mean_first_level_actions_among_successes=complete_mean(successes) if all_finished else None,
                  max_elapsed_seconds=max((row["elapsed_seconds"] for row in members if numeric(row["elapsed_seconds"])), default=None),
                  within_time_limit_n=sum(row["within_end_to_end_limit"] is True for row in members),
                  budget_termination_n=sum(row.get("budget_termination", False) for row in members),
                  game_outcome_counts=dict(Counter(str(row.get("game_outcome")) for row in members if row["finished"])),
                  probe_interventions=sum(row["probe_interventions"] for row in members),
                  belief_fact_injection_requests=sum(row["usage"]["belief_fact_injection_requests"] for row in members))
    return output


def build_analysis(root):
    saved = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    slots = [analyze_slot(root, slot, saved["config"]) for slot in saved["slots"]]
    arms = {name: arm_metrics([slot for slot in slots if slot["arm"] == name]) for name, _, _ in ARMS}
    qualified = {name: arm_metrics([slot for slot in slots if slot["arm"] == name and slot["protocol_qualified"]]) for name, _, _ in ARMS}
    effects = {}
    for metric in ("mean_first_level_success", "mean_score_cap_1_15"):
        b, p, s, both = [arms[name][metric] for name in ("baseline", "probe", "belief", "combined")]
        effects[metric] = ({"probe_main": (p + both - b - s) / 2,
                            "belief_main": (s + both - b - p) / 2,
                            "interaction": both - p - s + b}
                           if all(numeric(value) for value in (b, p, s, both)) else None)
    warnings = []
    all_usage = [row for slot in saved["slots"] for row in load_usage(root / slot["slot"], warnings)]
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "complete": all(slot["finished"] for slot in slots),
            "source_summary_updated_at": saved.get("updated_at"), "slots": slots, "arms_itt": arms,
            "protocol_qualified_descriptive_subset": qualified, "descriptive_factorial_effects": effects,
            "all_usage": usage_metrics(all_usage), "read_warnings": warnings}


def markdown(data):
    def cell(value):
        return "—" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)

    lines = ["# LS20 四臂实验：离线补充分析", "", "全槽已结束。" if data["complete"] else "实验进行中；无最终分臂结论。", "",
             "## ITT：保留全部指定槽位", "", "| 臂 | 结束/分配 | 首关成功 | 平均 cap 分 | 成功局首关动作均值 | 首关投入动作均值 | 端到端均值/最大秒 | 时限内 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, arm in data["arms_itt"].items():
        values = [name, f"{arm['finished_n']}/{arm['assigned_n']}",
                  f"{arm['first_level_successes']}/{arm['assigned_n']}", arm["mean_score_cap_1_15"],
                  arm["mean_first_level_actions_among_successes"], arm["mean_level1_actions_spent"],
                  f"{cell(arm['mean_elapsed_seconds'])}/{cell(arm['max_elapsed_seconds'])}", arm["within_time_limit_n"]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "首关动作均值仅针对成功局；首关投入动作均值包含失败局。cap 分沿用冻结运行器、当前局基线与整局完成权重上限。", "",
              "## 进程退出、游戏结果与有效时间预算", "",
              "`completed` 仅表示进程正常退出，不表示游戏完成。冻结 TAAF InlineTarget 会从 1770 秒内层预算扣除 min(600,1770/2)=600 秒收尾缓冲，因此软预算为 1170 秒（19.5 分钟），游戏初始化还会消耗该窗口的一部分；外层进程含工件收尾仍受 1800 秒上限约束。四臂规则相同。`cancelled` 计入内层预算终止，并与运行器 timeout 一起统计。", "",
              "| 槽位 | 进程退出 | 游戏结果 | 预算终止类型 | 游戏 final_wallclock 秒 | 端到端秒 |",
              "|---|---|---|---|---:|---:|"]
    for slot in data["slots"]:
        values = [slot[key] for key in ("slot", "process_exit_status", "game_outcome", "budget_termination_kind", "game_final_wallclock_seconds", "elapsed_seconds")]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "预算公式来源：冻结 `tufa-arc-agi-framework/src/taaf/deploy_inline.py:108–110`；实际 soft_end_time 与 benchmark final_wallclock 已逐槽记录。", "",
              "## 处理实际摄取与 protocol-qualified 描述子集", "",
              "| 槽位 | probe 分配/介入 | belief 分配/事实注入请求 | 与分配匹配 P/B | qualified | 工具错误文本结果数 |",
              "|---|---:|---:|---|---|---:|"]
    for slot in data["slots"]:
        values = [slot["slot"], f"{int(slot['probe_assigned'])}/{slot['probe_interventions']}",
                  f"{int(slot['belief_assigned'])}/{slot['usage']['belief_fact_injection_requests']}",
                  f"{slot['probe_assignment_match']}/{slot['belief_assignment_match']}", slot["protocol_qualified"],
                  slot["tools"]["tool_error_text_results"]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "qualified 定义：进程正常结束、1–120 动作、端到端不超过指定时限、恰好一个初始化事件、实际请求模型配置匹配；开臂须观察到对应处理（probe 真介入、belief 真事实注入），关臂须无对应观测。排除原因逐槽存于 JSON。",
              "遵循指定时间预算而取消的游戏仍可符合该协议口径，并非成功游戏。该子集按处理后的观测筛选，仅作 protocol-qualified 描述，不是因果 per-protocol 估计。ITT 不因此删样本。", "",
              "```json", json.dumps(data["protocol_qualified_descriptive_subset"], ensure_ascii=False, indent=2), "```", "",
              "## 描述性 2×2 效应", "",
              "probe 主效应=(probe+combined−baseline−belief)/2；belief 主效应=(belief+combined−baseline−probe)/2；交互=combined−probe−belief+baseline。首关成功以比例计，分数以百分点计。每臂 n=2，不进行显著性检验。", "",
              "```json", json.dumps(data["descriptive_factorial_effects"], ensure_ascii=False, indent=2), "```", "",
              "## 请求耗时、provider 错误与 token", "",
              "| 槽位 | 请求/错误/未收尾 | 已结束请求耗时 median/p95 秒 | completion 含 reasoning | reasoning | cached input | uncached input |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for slot in data["slots"]:
        usage = slot["usage"]
        tokens = usage["tokens_all_observed_responses"]
        values = [slot["slot"], f"{usage['requests']}/{usage['errors']}/{usage['unfinished_requests']}",
                  f"{cell(usage['latency_median_seconds'])}/{cell(usage['latency_p95_seconds'])}",
                  *[tokens[field] for field in ("completion_tokens_including_reasoning", "reasoning_tokens", "cached_input_tokens", "uncached_input_tokens")]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "全轮 usage 汇总：", "", "```json", json.dumps(data["all_usage"], ensure_ascii=False, indent=2), "```", "",
              "耗时统计包括已结束 response/error 请求，p95 为 nearest-rank；未收尾请求单列，不赋造完成耗时。字段缺失保留 null，known-field subtotal 与覆盖数另存；completion 已含 reasoning，不二次相加。非缓存输入=prompt−cached。这些是已观测响应 token，不是账户总扣费，也不含失败请求可能发生的未报告消耗。", "",
              "工具 error-text 按原始 transcripts 的单个 [TOOL RESULT: …] / [PYTHON TOOL RESULT] 段落计数，匹配 Traceback、error 标签或异常类文本；忽略模型思考及 viewer 中重复转录，不按同段重复打印累加。显示文本已丢失原始结构化错误标记，故该数不是已认证的 Python crash 数。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Existing experiment directory")
    root = parser.parse_args().output.resolve()
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    if any(slot.get("status") in ("pending", "running") for slot in summary["slots"]):
        raise SystemExit("实验仍有待结束槽位；八槽结束后再生成补充报告。")
    data = build_analysis(root)
    write_json(root / "analysis.json", data)
    (root / "analysis.md").write_text(markdown(data), encoding="utf-8")
    print(json.dumps({"complete": data["complete"], "slots": len(data["slots"]), "output": str(root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
