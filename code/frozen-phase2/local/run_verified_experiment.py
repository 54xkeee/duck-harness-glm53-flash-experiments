#!/usr/bin/env python3
"""Run the LS20 2x2 experiment from Linux/WSL; no model calls on import."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Any


ARMS = (("baseline", False, False), ("probe", True, False),
        ("belief", False, True), ("combined", True, True))
MODEL = "glm-5.3-flash"
SCORING = {
    "checked_on": "2026-09-04", "cap": 1.15,
    "formula": "s_i = completed_i ? min(1.15, (h_i/a_i)^2) : 0; game = 100 * min(sum(i*s_i)/sum(i), sum(i*completed_i)/sum(i)), i=1..L",
    "source": "https://docs.arcprize.org/methodology",
    "changelog": "https://docs.arcprize.org/toolkit/overview (0.9.7)",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def schedule(seed: int, repeats: int = 2) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    slots = []
    for replicate in range(1, repeats + 1):
        arms = list(ARMS)
        rng.shuffle(arms)
        for order, (arm, probe, belief) in enumerate(arms, 1):
            slots.append({"slot": f"r{replicate}-{order}-{arm}", "replicate": replicate,
                          "order": order, "arm": arm, "probe": probe, "belief": belief,
                          "seed": seed + replicate - 1, "status": "pending"})
    return slots


def read_jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                warnings.append(f"{path.name}:{number}: incomplete or invalid JSON")
    return rows


def sum_usage(responses: list[dict[str, Any]], field: str) -> int | None:
    values = [(row.get("usage") or {}).get(field) for row in responses]
    if not values or any(not isinstance(value, (int, float)) for value in values):
        return None
    return int(sum(values))


def recompute_score(actions: list[int], completed: int,
                    baselines: list[int] | None, levels: int | None) -> float | None:
    if completed == 0:
        return 0.0
    if not levels or not baselines or len(baselines) != levels or len(actions) < completed:
        return None
    if any(value <= 0 for value in actions[:completed]):
        return None
    weights = levels * (levels + 1) / 2
    earned = sum((index + 1) * min(1.15, (baselines[index] / actions[index]) ** 2)
                 for index in range(completed))
    completion_cap = completed * (completed + 1) / 2
    return 100 * min(earned, completion_cap) / weights


def collect_metrics(run_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    event_paths = sorted((run_dir / "artifacts").glob("*_events.jsonl"))
    events = [row for path in event_paths for row in read_jsonl(path, warnings)]
    actions = [row for row in events if row.get("type") == "action"]
    no_change = [row.get("board_changed") is False for row in actions]
    longest = streak = 0
    for unchanged in no_change:
        streak = streak + 1 if unchanged else 0
        longest = max(longest, streak)
    levels = max((int(row.get("score", 0)) for row in actions), default=0)
    # Charge the completion action to the level just completed, not the next level.
    per_level: list[int] = []
    completed = int(next((row.get("score", 0) for row in events if row.get("type") == "initial"), 0))
    for row in actions:
        while len(per_level) <= completed:
            per_level.append(0)
        per_level[completed] += 1
        completed = int(row.get("score", 0))
    viewer_paths = sorted((run_dir / "artifacts").glob("*_viewer_data.json"))
    viewer = {}
    if len(viewer_paths) == 1:
        try:
            viewer = json.loads(viewer_paths[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            warnings.append("viewer JSON incomplete")
    if len(event_paths) > 1 or len(viewer_paths) > 1:
        warnings.append("multiple games in single slot; inspect artifact scope")
    game_run = {}
    benchmark_path = run_dir / "benchmark.json"
    if benchmark_path.exists():
        try:
            game_runs = json.loads(benchmark_path.read_text(encoding="utf-8")).get("game_runs", [])
            if len(game_runs) == 1:
                game_run = game_runs[0]
        except json.JSONDecodeError:
            warnings.append("benchmark JSON incomplete")
    baselines = game_run.get("base_actions_per_level")
    total_levels = game_run.get("number_of_levels", viewer.get("total_levels"))
    score = recompute_score(per_level, levels, baselines, total_levels)
    if levels and score is None:
        warnings.append("cap score missing: actual game baselines or level count unavailable")
    recorded_actions = game_run.get("actions_per_level")
    if recorded_actions is not None and (per_level + [0] * max(0, len(recorded_actions) - len(per_level))) != recorded_actions:
        warnings.append("rebuilt actions_per_level differs from benchmark snapshot")
    usage_paths = sorted((run_dir / "artifacts").glob("*usage.jsonl"))
    usage = [row for path in usage_paths for row in read_jsonl(path, warnings)]
    requests = [row for row in usage if row.get("event") == "request"]
    responses = [row for row in usage if row.get("event") == "response"]
    errors = [row for row in usage if row.get("event") == "error"]
    request_ids = {row.get("request_id") for row in requests}
    finished_ids = {row.get("request_id") for row in responses + errors}
    uptake = [row for row in requests if row.get("beliefs_injected") is True
              and bool(row.get("belief_transition_ids"))]
    interventions = [row["probe_control"] for row in actions
                     if (row.get("probe_control") or {}).get("intervened") is True]
    return {
        "actions": len(actions), "zero_action": not actions, "levels_completed": levels,
        "actions_per_level_rebuilt": per_level, "no_change_actions": sum(no_change),
        "no_change_rate": sum(no_change) / len(actions) if actions else None,
        "longest_no_change_streak": longest,
        "unknown_change_actions": sum("board_changed" not in row for row in actions),
        "raw_score": game_run.get("final_score", viewer.get("final_score")), "score_cap_1_15": score,
        "total_levels": total_levels, "game_status": viewer.get("status"),
        "game_id": game_run.get("game_id", viewer.get("game_id")),
        "base_actions_per_level": baselines,
        "baseline_source": "benchmark.json game_runs[0]" if baselines else None,
        "probe_actions": len(interventions), "probe_interventions": len(interventions),
        "probe_truncated_planned_actions": sum(max(0, row["requested_count"] - row["executed_count"])
                                               for row in interventions),
        "evidence_actions": sum(bool(row.get("evidence", {}).get("fact")) for row in actions),
        "belief_uptake_requests": len(uptake),
        "model_requests": len(requests), "model_responses": len(responses),
        "model_errors": len(errors), "unfinished_requests": len(request_ids - finished_ids),
        "prompt_tokens": sum_usage(responses, "prompt_tokens"),
        "completion_tokens": sum_usage(responses, "completion_tokens"),
        "total_tokens": sum_usage(responses, "total_tokens"),
        "usage_complete": bool(requests) and len(request_ids - finished_ids) == 0
                          and not errors and sum_usage(responses, "total_tokens") is not None,
        "artifact_warnings": warnings,
    }


def child_environment(slot: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DUCK_VERIFIED_PROBE": str(int(slot["probe"])),
        "DUCK_BELIEF_STORE": str(int(slot["belief"])),
        "PYTHONHASHSEED": str(slot["seed"]),
        "LOCAL_ANALYZER_MODEL_ID": MODEL,
    })
    # Values are recorded separately through this explicit, non-secret allowlist.
    defaults = {"LOCAL_ANALYZER_PROVIDER": "bigmodel", "LOCAL_ANALYZER_CONTEXT_WINDOW": "65536",
                "LOCAL_ANALYZER_MAX_OUTPUT": "8192", "LOCAL_ANALYZER_TIMEOUT": "90",
                "LOCAL_ANALYZER_TEMPERATURE": "0.6", "LOCAL_ANALYZER_TOP_P": "0.95",
                "LOCAL_ANALYZER_TOP_K": "20", "LOCAL_ANALYZER_TOOL_STEPS": "0",
                "LOCAL_ANALYZER_TOOL_TIMEOUT": "30", "LOCAL_ANALYZER_TOOL_OUTPUT_TOKENS": "1024",
                "LOCAL_ANALYZER_ENABLE_THINKING": "true", "LOCAL_ANALYZER_REASONING_EFFORT": "low",
                "MULTIMODAL_CONTEXT": "current_grid", "MULTIMODAL_UPSCALE": "16"}
    env.update(defaults)
    return env


def command_for(args: argparse.Namespace, run_dir: Path) -> list[str]:
    runtime = (args.per_run_seconds - args.cleanup_seconds) / 60
    return [sys.executable, "-m", "inference.framework.run", "--game", "ls20",
            "--agent", "duck-harness", "--model", MODEL, "--live-arcade",
            "--experiment-dir", str(run_dir), "--max-actions", str(args.max_actions),
            "--max-runtime-minutes", str(runtime), "--max-experiment-runtime-minutes", str(runtime),
            "--n-passes", "1", "--concurrent-jobs", "1", "--deployment-target", "inline",
            "--deployment-wait", "--analyzer-timeout", "90", "--no-save-request-logs"]


def signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def copy_log(pipe: Any, path: Path, secret: str) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for line in iter(pipe.readline, ""):
            stream.write(line.replace(secret, "[REDACTED]") if secret else line)
            stream.flush()
    pipe.close()


def run_slot(slot: dict[str, Any], args: argparse.Namespace,
             command: list[str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    result = {**slot, "started_at": utc_now(), "status": "running"}
    run_dir = args.output / slot["slot"]
    run_dir.mkdir()
    result["run_dir"] = str(run_dir)
    write_json(run_dir / "slot.json", result)
    env = child_environment(slot)
    process = None
    log_thread = None
    try:
        process = subprocess.Popen(command or command_for(args, run_dir), cwd=args.app_dir,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", start_new_session=True)
        result["pid"] = process.pid
        log_thread = threading.Thread(target=copy_log,
                                      args=(process.stdout, run_dir / "process.log",
                                            env.get("LOCAL_ANALYZER_API_KEY", "")), daemon=True)
        log_thread.start()
        execution_deadline = started + args.per_run_seconds - args.cleanup_seconds
        try:
            process.wait(timeout=max(0.001, execution_deadline - time.monotonic()))
            result["status"] = "completed" if process.returncode == 0 else "process_error"
        except subprocess.TimeoutExpired:
            result["status"] = "timeout"
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=min(5, args.cleanup_seconds / 3))
            except subprocess.TimeoutExpired:
                pass
        finally:
            # Also stop descendants left behind by an already exited leader.
            signal_group(process, signal.SIGKILL)
            process.wait(timeout=max(0.1, args.cleanup_seconds / 3))
        result["returncode"] = process.returncode
    except Exception as exc:
        result.update(status="launch_error" if process is None else "runner_error",
                      error_type=type(exc).__name__, returncode=process.returncode if process else None)
    finally:
        if process:
            signal_group(process, signal.SIGKILL)
        if log_thread:
            log_thread.join(timeout=max(0.1, args.cleanup_seconds / 3))
    result.update(collect_metrics(run_dir))
    result["finished_at"] = utc_now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["deadline_exceeded"] = result["elapsed_seconds"] > args.per_run_seconds
    write_json(run_dir / "slot.json", result)
    return result


def report(output: Path, config: dict[str, Any], slots: list[dict[str, Any]]) -> None:
    arms = []
    for arm, _, _ in ARMS:
        members = [slot for slot in slots if slot["arm"] == arm]
        finished = [slot for slot in members if slot.get("status") not in ("pending", "running")]
        entry = {"arm": arm, "assigned": len(members), "finished": len(finished),
                 "zero_actions": sum(bool(slot.get("zero_action")) for slot in finished),
                 "timeouts": sum(slot.get("status") == "timeout" for slot in finished)}
        for metric in ("levels_completed", "score_cap_1_15", "actions", "no_change_actions"):
            values = [slot.get(metric) for slot in finished]
            entry["mean_" + metric] = (sum(values) / len(members)
                                            if len(finished) == len(members) and all(value is not None for value in values)
                                            else None)
        arms.append(entry)
    write_json(output / "summary.json", {"config": config, "slots": slots, "arms": arms, "updated_at": utc_now()})
    columns = ["slot", "replicate", "order", "arm", "status", "elapsed_seconds", "deadline_exceeded",
               "actions", "zero_action", "levels_completed", "raw_score", "score_cap_1_15",
               "no_change_actions", "no_change_rate", "longest_no_change_streak", "probe_actions",
               "belief_uptake_requests", "model_requests", "model_errors", "unfinished_requests",
               "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete"]
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(slots)
    lines = ["# GLM-5.3-Flash / LS20 四臂实验报告", "",
             f"模型：`{MODEL}`；每臂 {config['repeats']} 次；每次硬上限 {config['per_run_seconds']} 秒（含收尾）。",
             f"动作上限 {config['max_actions']}；并行 {config['jobs']}；顺序随机种子 {config['seed']}。",
             "每局独立进程、全新模型上下文与游戏；顺序在每个 replicate 内随机化。",
             "零动作、启动失败与超时均保留在指定实验臂分母中；进行中槽位不视为已完成。", "",
             "| 槽位 | 状态 | 关卡 | 动作 | 无变化 | 最长串 | 原始分 | cap 1.15 | probe | 事实注入请求 | 秒 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    fields = ["slot", "status", "levels_completed", "actions", "no_change_actions",
              "longest_no_change_streak", "raw_score", "score_cap_1_15", "probe_actions",
              "belief_uptake_requests", "elapsed_seconds"]
    for slot in slots:
        lines.append("| " + " | ".join("—" if slot.get(key) is None else str(slot[key]) for key in fields) + " |")
    lines += ["", "## 分臂结果（ITT）", "", "| 臂 | 完成/分配 | 零动作 | 超时 | 平均关卡 | 平均 cap 分 |",
              "|---|---:|---:|---:|---:|---:|"]
    for arm in arms:
        fields = [arm["arm"], f"{arm['finished']}/{arm['assigned']}", arm["zero_actions"],
                  arm["timeouts"], arm["mean_levels_completed"], arm["mean_score_cap_1_15"]]
        lines.append("| " + " | ".join("—" if value is None else str(value) for value in fields) + " |")
    lines += ["", "## 请求与 token 记录", "",
              "| 槽位 | 请求 | 错误 | 未收尾请求 | 输入 token | 输出 token | 总 token | 完整 |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for slot in slots:
        fields = [slot.get(key) for key in ("slot", "model_requests", "model_errors", "unfinished_requests",
                                             "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete")]
        lines.append("| " + " | ".join("—" if value is None else str(value) for value in fields) + " |")
    lines += ["", "## 统计口径", "",
              "- 关卡、动作、无变化及最长连续无变化按逐动作事件重建，初始化 RESET 不计作真实动作。",
              "- 事实摄取 = 请求中实际注入至少一个 transition ID；非写入次数。",
              "- probe = 实际触发 probe_required / probe_no_change / probe_mismatch 的调用数；单步介入也计数，非声称节省动作。",
              "- token 缺失保留 null；不补零。usage_complete 仅在全部请求结束且 token 完整时为 true。",
              f"- 评分核对日期：{SCORING['checked_on']}；公式：`{SCORING['formula']}`。",
              f"- 评分来源：[官方方法]({SCORING['source']})；[工具包变更记录]({SCORING['changelog'].split(' ')[0]})。",
              "- 人类基线只从当前局 benchmark.json 读取；缺失且已过关时重算值留空。整局仍受已完成关卡权重上限约束。",
              "- 这是每臂 2 次的探索性对照；随机性、请求延迟和接口失败均影响结果。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1] / "ARC3-Inference")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-actions", type=int, default=120)
    parser.add_argument("--per-run-seconds", type=float, default=1800)
    parser.add_argument("--cleanup-seconds", type=float, default=30)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args(argv)
    if min(args.jobs, args.repeats, args.max_actions) < 1:
        parser.error("jobs, repeats and max-actions must be positive")
    if not 0 < args.cleanup_seconds < args.per_run_seconds <= 1800:
        parser.error("require 0 < cleanup-seconds < per-run-seconds <= 1800")
    args.output = args.output.resolve()
    args.app_dir = args.app_dir.resolve()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.summarize_only:
        saved = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
        for slot in saved["slots"]:
            if slot.get("run_dir"):
                slot.update(collect_metrics(Path(slot["run_dir"])))
        report(args.output, saved["config"], saved["slots"])
        return 0
    if os.name != "posix":
        raise SystemExit("Run under Linux/WSL to enforce process-group deadlines.")
    for name in ("LOCAL_ANALYZER_API_KEY", "LOCAL_ANALYZER_BASE_URL"):
        if not os.environ.get(name):
            raise SystemExit(f"Required environment variable: {name}")
    args.output.mkdir(parents=True, exist_ok=False)
    snapshot = args.output / "source_snapshot"
    for source in sorted((args.app_dir / "inference").rglob("*.py")):
        target = snapshot / source.relative_to(args.app_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(Path(__file__), snapshot / Path(__file__).name)
    slots = schedule(args.seed, args.repeats)
    config = {name: getattr(args, name) for name in
              ("jobs", "repeats", "max_actions", "per_run_seconds", "cleanup_seconds", "seed")}
    config.update(model=MODEL, game="ls20", scoring=SCORING, created_at=utc_now(),
                  randomization="random.Random(seed), shuffle four arms within each replicate; no provider seed support assumed")
    env = child_environment(slots[0])
    config["model_settings"] = {key: env.get(key) for key in (
        "LOCAL_ANALYZER_BASE_URL", "LOCAL_ANALYZER_PROVIDER", "LOCAL_ANALYZER_MODEL_ID",
        "LOCAL_ANALYZER_CONTEXT_WINDOW", "LOCAL_ANALYZER_MAX_OUTPUT", "LOCAL_ANALYZER_TIMEOUT",
        "LOCAL_ANALYZER_TEMPERATURE", "LOCAL_ANALYZER_TOP_P", "LOCAL_ANALYZER_TOP_K",
        "LOCAL_ANALYZER_TOOL_STEPS", "LOCAL_ANALYZER_ENABLE_THINKING", "LOCAL_ANALYZER_REASONING_EFFORT",
        "MULTIMODAL_CONTEXT", "MULTIMODAL_UPSCALE")}
    report(args.output, config, slots)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for replicate in range(1, args.repeats + 1):
            indexes = [i for i, slot in enumerate(slots) if slot["replicate"] == replicate]
            futures = {pool.submit(run_slot, slots[i], args): i for i in indexes}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    slots[i] = future.result()
                except Exception as exc:
                    # Preserve this assigned slot without aborting the other seven.
                    slots[i].update(status="runner_error", error_type=type(exc).__name__,
                                    run_dir=str(args.output / slots[i]["slot"]), finished_at=utc_now())
                    write_json(Path(slots[i]["run_dir"]) / "slot.json", slots[i])
                report(args.output, config, slots)
                print(json.dumps({key: slots[i].get(key) for key in
                                  ("slot", "status", "actions", "levels_completed", "elapsed_seconds")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
