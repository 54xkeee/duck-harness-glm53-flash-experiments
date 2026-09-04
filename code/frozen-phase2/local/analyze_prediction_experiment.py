#!/usr/bin/env python3
"""Offline analysis of the new prediction experiment; no provider calls."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from analyze_verified_experiment import complete_mean, load_usage, numeric, transcript_metrics, usage_metrics
from run_verified_experiment import collect_metrics, read_jsonl, recompute_score, utc_now, write_json
from run_prediction_experiment import ARMS, TERMINAL


def prediction_metrics(events):
    commitments, evaluated, revisions = {}, set(), {}
    actions = [row for row in events if row.get("type") == "action"]
    scored, rejected, stops = [], Counter(), Counter()
    boundary_count = unpredicted = invalid_commitments = invalidated = 0
    mismatched_versions, repair_later = {}, []
    for row in events:
        if row.get("type") == "prediction_commitment":
            ticket = row.get("prediction_commitment", {})
            commitments[ticket.get("ticket_id")] = ticket
            revision = ticket.get("rule_revision")
            if revision:
                revisions[(revision["rule_id"], revision["version"])] = revision
        elif row.get("type") == "prediction_rejected":
            rejected[str(row.get("prediction_error", "unknown")).split(":", 1)[0]] += 1
        elif row.get("type") == "action" and isinstance(row.get("prediction"), dict):
            evaluation = row["prediction"]
            ticket_id = evaluation.get("ticket_id")
            ticket = commitments.get(ticket_id)
            valid = (ticket is not None and ticket_id not in evaluated
                     and ticket.get("prediction") == evaluation.get("prediction")
                     and ticket.get("action") == evaluation.get("action")
                     and ticket.get("action") == row.get("action_display")
                     and ticket.get("step") == evaluation.get("step"))
            evaluated.add(ticket_id)
            reason = evaluation.get("stop_reason") or row.get("prediction_stop_reason")
            if reason:
                stops[reason] += 1
            boundary_count += evaluation.get("scope_ended") is True
            invalidated += len(evaluation.get("invalidated_rules", []))
            if not valid:
                invalid_commitments += 1
            elif evaluation.get("status") == "unpredicted":
                unpredicted += 1
            elif evaluation.get("status") in ("matched", "mismatched") and ticket.get("prediction"):
                scored.append(evaluation)
                rule_id, version = evaluation.get("rule_id"), evaluation.get("version")
                revision = revisions.get((rule_id, version), {})
                if (rule_id in mismatched_versions and version > mismatched_versions[rule_id]
                        and evaluation.get("step", -1) > revision.get("created_step", float("inf"))):
                    repair_later.append(evaluation)
                if evaluation["status"] == "mismatched":
                    mismatched_versions[rule_id] = version
    within = [row for row in scored if row.get("scope_ended") is False]
    boundary = [row for row in scored if row.get("scope_ended") is True]
    changing = [row for row in scored if row.get("predicted_changed_cell_count", 0) > 0]
    stationary = [row for row in scored if row.get("stationary_cells_prediction") is True]

    def counts(rows):
        matched = sum(row.get("matched") is True for row in rows)
        return {"scored": len(rows), "matched": matched, "mismatched": len(rows) - matched,
                "match_rate": matched / len(rows) if rows else None}

    def total(field):
        values = [row.get(field) for row in scored]
        return sum(values) if values and all(numeric(value) for value in values) else None

    changed, checked = total("observed_changed_cell_count"), total("observed_changed_cells_checked")
    declarations = [ticket for ticket in commitments.values() if ticket.get("prediction")]
    return {"actual_actions": len(actions), "committed_action_tickets": len(commitments),
            "precommitted_predictions": len(declarations), "scored_predictions": len(scored),
            "prediction_action_coverage": len(scored) / len(actions) if actions else None,
            "predicted_commitments_without_action": sum(ticket.get("prediction") is not None and key not in evaluated
                                                        for key, ticket in commitments.items()),
            "unpredicted_actions": unpredicted, "invalid_commitment_actions": invalid_commitments,
            "all_predictions": counts(scored), "within_scope_predictions": counts(within),
            "boundary_predictions": counts(boundary), "all_boundary_actions": boundary_count,
            "changed_cell_predictions": counts(changing), "stationary_cell_predictions": counts(stationary),
            "checked_cell_count": total("checked_cell_count"),
            "predicted_changed_cell_count": total("predicted_changed_cell_count"),
            "observed_changed_cell_count_on_scored_actions": changed,
            "observed_changed_cells_checked": checked,
            "observed_changed_cell_coverage_micro": checked / changed if changed and checked is not None else None,
            "stop_interventions": sum(stops.values()), "stop_reasons": dict(stops),
            "rejected_attempts": sum(rejected.values()), "rejection_reasons": dict(rejected),
            "dependency_invalidations": invalidated, "declared_rule_versions": len(revisions),
            "revised_rule_versions": sum(version > 1 for _, version in revisions),
            "revised_after_mismatch_later_predictions": counts(repair_later)}


def reset_segments(events):
    """Descriptive segments, never used to choose a best episode or discard cost."""
    completed = int(next((row.get("score", 0) for row in events if row.get("type") == "initial"), 0))
    segments, current = [], {"start_action": 1, "actions": 0, "max_levels": completed}
    for index, row in enumerate((row for row in events if row.get("type") == "action"), 1):
        after = int(row.get("score", 0))
        current["actions"] += 1
        current["max_levels"] = max(current["max_levels"], after)
        # RESET is charged to the level before reset, like Game.execute_action.
        if row.get("action_name") == "RESET":
            current.update(end_action=index, reset_action=index, reset_progress_drop=after < completed,
                           end_levels=after)
            segments.append(current)
            current = {"start_action": index + 1, "actions": 0, "max_levels": after}
        completed = after
    if current["actions"] or not segments:
        current.update(end_action=current["start_action"] + current["actions"] - 1, end_levels=completed)
        segments.append(current)
    return segments


def request_metrics(rows):
    metrics = usage_metrics(rows)
    requests = {row["_key"]: row for row in rows if row.get("event") == "request"}
    errors = {row["_key"]: row for row in rows if row.get("event") == "error"}
    injected = [row for row in requests.values() if row.get("prediction_injected") is True]
    metrics.update(prediction_state_injection_requests=len(injected),
                   distinct_injected_prediction_ids=len({(row["_key"][:-1], ticket)
                                                         for row in injected for ticket in row.get("prediction_ids", [])}),
                   provider_error_codes=dict(Counter(str(row.get("provider_error_code")) for row in errors.values())),
                   api_failure_limit_hits=sum(row.get("api_failure_limit_reached") is True for row in errors.values()))
    return metrics


def analyze_slot(root, slot, config):
    run_dir, warnings, events = root / slot["slot"], [], []
    paths = sorted((run_dir / "artifacts").glob("*_events.jsonl"))
    for path in paths:
        events.extend(read_jsonl(path, warnings))
    metrics = collect_metrics(run_dir)
    game = {}
    path = run_dir / "benchmark.json"
    if path.exists():
        try:
            games = json.loads(path.read_text(encoding="utf-8")).get("game_runs", [])
            if len(games) == 1:
                game = games[0]
        except json.JSONDecodeError:
            warnings.append("benchmark JSON incomplete")
    actions = [row for row in events if row.get("type") == "action"]
    score = metrics["score_cap_1_15"]
    rebuilt = metrics["actions_per_level_rebuilt"]
    benchmark_complete = len(game.get("history", [])) == len(actions) and game.get("final_score") is not None
    recorded_score = recompute_score(game.get("actions_per_level", []), game.get("levels_completed", 0),
                                     game.get("base_actions_per_level"), game.get("number_of_levels")) if game else None
    if benchmark_complete and score is not None and recorded_score is not None and abs(score - recorded_score) > 1e-8:
        warnings.append("event/benchmark score mismatch")
    usage = request_metrics(load_usage(run_dir, warnings))
    prediction = prediction_metrics(events)
    first = next((i for i, row in enumerate(actions, 1) if row.get("score", 0) >= 1), None)
    full_game = any(row.get("run_complete") is True or row.get("state") == "WIN" for row in actions)
    full_game = full_game or game.get("state") == "won"
    elapsed = slot.get("elapsed_seconds")
    reasons = []
    expected_model = {"model": config["model"], "provider": "bigmodel", "reasoning_effort": "low", "max_output_tokens": 8192}
    prediction_observed = prediction["committed_action_tickets"] > 0
    belief_observed = usage["belief_fact_injection_requests"] > 0
    for satisfied, reason in (
        (slot["status"] in TERMINAL, "process_not_terminal"),
        (len(paths) == 1 and sum(row.get("type") == "initial" for row in events) == 1, "fresh_initial_missing_or_multiple"),
        (0 < len(actions) <= config["max_actions"], "action_budget_or_zero_actions"),
        (numeric(elapsed) and elapsed <= config["per_run_seconds"], "wallclock_budget_or_missing"),
        (bool(usage["request_model_settings"]) and all(value == expected_model for value in usage["request_model_settings"]), "model_settings"),
        (prediction_observed == slot["prediction"], "prediction_assignment"),
        (belief_observed == slot["belief"], "belief_assignment"),
        (not any(row.get("probe_control", {}).get("enabled") for row in actions), "old_probe_enabled"),
    ):
        if not satisfied:
            reasons.append(reason)
    return {"slot": slot["slot"], "arm": slot["arm"], "replicate": slot["replicate"], "status": slot["status"],
            "finished": slot["status"] in TERMINAL, "first_level_success": int(first is not None),
            "first_level_actions_to_success": first, "full_game_success": int(full_game),
            "levels_completed": metrics["levels_completed"], "score_cap_1_15": score,
            "raw_benchmark_score": game.get("final_score"), "benchmark_recomputed_score": recorded_score,
            "benchmark_covers_all_events": benchmark_complete,
            "actions_per_level_all_resets": rebuilt, "reset_segments": reset_segments(events),
            "actions": len(actions), "elapsed_seconds": elapsed, "game_outcome": game.get("state", metrics.get("game_status")),
            "within_time_limit": elapsed <= config["per_run_seconds"] if numeric(elapsed) else None,
            "no_change_actions": metrics["no_change_actions"], "longest_no_change_streak": metrics["longest_no_change_streak"],
            "effective_solver_seconds": config["effective_solver_seconds"],
            "prediction": prediction, "usage": usage,
            "tools": transcript_metrics(sorted((run_dir / "transcripts").glob("*.txt"))),
            "assignment_observations_match": not reasons, "protocol_observation_flags": reasons,
            "artifact_warnings": warnings + metrics["artifact_warnings"]}


def arm_metrics(slots):
    finished = all(row["finished"] for row in slots)
    result = {"assigned_n": len(slots), "finished_n": sum(row["finished"] for row in slots)}
    for field in ("levels_completed", "full_game_success", "first_level_success", "score_cap_1_15", "actions", "elapsed_seconds"):
        result["mean_" + field] = complete_mean([row[field] for row in slots]) if finished else None
    for field in ("scored_predictions", "precommitted_predictions", "stop_interventions", "rejected_attempts"):
        result[field] = sum(row["prediction"][field] for row in slots)
    scored = result["scored_predictions"]
    result["prediction_match_rate_micro"] = sum(row["prediction"]["all_predictions"]["matched"] for row in slots) / scored if scored else None
    result["provider_errors"] = sum(row["usage"]["errors"] for row in slots)
    return result


def build_analysis(root):
    saved = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    expected = {(replicate, name) for replicate in (1, 2) for name, _, _ in ARMS}
    actual = {(slot["replicate"], slot["arm"]) for slot in saved["slots"]}
    if len(saved["slots"]) != 8 or actual != expected or any(slot["status"] not in TERMINAL for slot in saved["slots"]):
        raise ValueError("八个指定槽位尚未全部结束；保留 pending，暂不生成最终结论。")
    slots = [analyze_slot(root, slot, saved["config"]) for slot in saved["slots"]]
    arms = {name: arm_metrics([slot for slot in slots if slot["arm"] == name]) for name, _, _ in ARMS}
    effects = {}
    for metric in ("mean_levels_completed", "mean_full_game_success", "mean_score_cap_1_15"):
        base, belief, prediction, combined = [arms[name][metric] for name in ("baseline", "belief", "prediction", "combined")]
        effects[metric] = ({"prediction_main": (prediction + combined - base - belief) / 2,
                            "belief_main": (belief + combined - base - prediction) / 2,
                            "interaction": combined - prediction - belief + base}
                           if all(numeric(value) for value in (base, belief, prediction, combined)) else None)
    warnings = []
    usage = [row for slot in saved["slots"] for row in load_usage(root / slot["slot"], warnings)]
    return {"complete": True, "generated_at": utc_now(), "config": saved["config"], "slots": slots,
            "arms_itt": arms, "descriptive_factorial_effects": effects, "all_usage": request_metrics(usage),
            "warnings": warnings}


def markdown(data):
    def cell(value):
        return "—" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)

    lines = ["# GLM-5.3-Flash / LS20 预测闭环实验", "", "八个指定槽位均已结束；全部保留在 ITT 分母。", "",
             "## 主要游戏结果", "", "| 臂 | n | 平均关卡 | 全游戏成功率 | 首关成功率 | 平均评分 / 100 | 平均动作 | 平均秒 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in data["arms_itt"].items():
        lines.append("| " + " | ".join(map(cell, [name, row["assigned_n"], *[row["mean_" + field] for field in
                         ("levels_completed", "full_game_success", "first_level_success", "score_cap_1_15", "actions", "elapsed_seconds")]])) + " |")
    lines += ["", "| 槽位 | 进程 | 游戏状态 | 关卡 | 首关动作 | 总动作 | 评分 | 秒 |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in data["slots"]:
        lines.append("| " + " | ".join(cell(row[key]) for key in ("slot", "status", "game_outcome", "levels_completed",
                                                                   "first_level_actions_to_success", "actions", "score_cap_1_15", "elapsed_seconds")) + " |")
    lines += ["", "评分按全部真实动作重建：动作计入执行前所在关卡，RESET 本身及重置后的重玩成本均保留，完成关卡取全局高水位，与 TAAF GameRun 口径一致。reset_segments 仅解释重置，不挑最好一段。缺失评分依据时保留 null。进程 completed 与全游戏成功分开。", "",
              "## 预测是否真正发生、验证与覆盖", "",
              "| 槽位 | 行动前预测 | 已评分 | 动作覆盖率 | 全预测匹配率 | 预测变化格匹配率 | 仅静止格匹配率 | 实际变化格覆盖率 | 停止 / 拒绝 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in data["slots"]:
        pred = row["prediction"]
        values = [row["slot"], pred["precommitted_predictions"], pred["scored_predictions"], pred["prediction_action_coverage"],
                  pred["all_predictions"]["match_rate"], pred["changed_cell_predictions"]["match_rate"],
                  pred["stationary_cell_predictions"]["match_rate"], pred["observed_changed_cell_coverage_micro"],
                  f"{pred['stop_interventions']}/{pred['rejected_attempts']}"]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "只将动作前已落盘且与实际动作记录一致的承诺计入精度。全预测包含明确预告的过关等边界预测；JSON 另列同作用域与边界两组。动作覆盖率分母为全部真实动作。实际变化格覆盖率分母为被评分动作中真正变化的格子，而非整屏背景。停止次数并非节省动作数，规则改版并非修复成功。修订后后续预测表现要求旧版已有反例且新版本首个验证动作之后仍有新的预提交验证。", "",
              "## 请求故障与消耗", "", "| 槽位 | 请求 / 响应 / 错误 / 未收尾 | 延迟 median / p95 秒 | 输入 token | 输出 token（含推理） |",
              "|---|---:|---:|---:|---:|"]
    for row in data["slots"]:
        usage, tokens = row["usage"], row["usage"]["tokens_all_observed_responses"]
        values = [row["slot"], "/".join(str(usage[key]) for key in ("requests", "responses", "errors", "unfinished_requests")),
                  f"{cell(usage['latency_median_seconds'])}/{cell(usage['latency_p95_seconds'])}",
                  tokens["prompt_tokens"], tokens["completion_tokens_including_reasoning"]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "请求异常按 HTTP 状态及异常类型在 JSON 单列。token 为已返回 usage 的观测量，缺失保留 null；推理 token 已包含在输出 token 内。运行失败照常进入主要 ITT，接口问题也单独解释，避免把故障等同于算法效果。", "",
              "## 时间与比较范围", "",
              f"每槽外层硬上限 {data['config']['per_run_seconds']} 秒，求解软预算 {data['config']['effective_solver_seconds']} 秒，另留启动 30 秒和进程组清理 30 秒。新 launcher 使用独立每游戏预算，避开旧轮额外扣除 600 秒的问题；启动仍占外层窗口。",
              "本轮每臂 2 次、单一 LS20，效应只作描述性对照。动作预算从旧轮 120 提升到本轮 1200，仅本轮四臂共享条件下的比较具备对照意义。事实注入、预测精度与通关分别报告，不以某一项替代整个游戏理解。", "",
              "## 描述性 2×2 效应", "", "```json", json.dumps(data["descriptive_factorial_effects"], ensure_ascii=False, indent=2), "```", "",
              "## 全轮请求汇总", "", "```json", json.dumps(data["all_usage"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def save_analysis(root):
    data = build_analysis(root)
    write_json(root / "analysis.json", data)
    (root / "analysis.md").write_text(markdown(data), encoding="utf-8")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = save_analysis(args.output.resolve())
    print(json.dumps({"complete": data["complete"], "slots": len(data["slots"])}))


if __name__ == "__main__":
    main()
