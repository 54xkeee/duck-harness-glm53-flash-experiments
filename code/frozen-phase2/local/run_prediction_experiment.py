#!/usr/bin/env python3
"""Frozen four-arm LS20 prediction experiment; importing makes no model calls."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import threading
import time

from run_verified_experiment import (
    MODEL, SCORING, child_environment as verified_environment, collect_metrics,
    copy_log, read_jsonl, signal_group, utc_now, write_json,
)

ARMS = (("baseline", False, False), ("belief", False, True),
        ("prediction", True, False), ("combined", True, True))
TERMINAL = {"completed", "timeout", "process_error", "launch_error", "runner_error"}
STARTUP_SECONDS = 30


def schedule(seed, repeats=2):
    rng, slots = random.Random(seed), []
    for replicate in range(1, repeats + 1):
        arms = list(ARMS)
        rng.shuffle(arms)
        for order, (arm, prediction, belief) in enumerate(arms, 1):
            slots.append({"slot": f"r{replicate}-{order}-{arm}", "replicate": replicate,
                          "order": order, "arm": arm, "prediction": prediction,
                          "belief": belief, "seed": seed + replicate - 1, "status": "pending"})
    return slots


def child_environment(slot):
    env = verified_environment({**slot, "probe": False})
    env.update(DUCK_PREDICTION_CHECK=str(int(slot["prediction"])),
               DUCK_API_FAILURE_LIMIT="3",
               LOCAL_ANALYZER_BASE_URL="https://open.bigmodel.cn/api/paas/v4/")
    return env


def command_for(args, run_dir):
    # No experiment-level override: framework adds 600 s for InlineTarget,
    # which removes that same buffer. The external process deadline stays fixed.
    runtime = (args.per_run_seconds - args.cleanup_seconds - STARTUP_SECONDS) / 60
    command = [sys.executable, "-m", "inference.framework.run", "--game", "ls20",
            "--agent", "duck-harness", "--model", MODEL, "--live-arcade",
            "--experiment-dir", str(run_dir), "--max-actions", str(args.max_actions),
            "--max-runtime-minutes", str(runtime), "--n-passes", "1",
            "--concurrent-jobs", "1", "--deployment-target", "inline",
            "--deployment-wait", "--analyzer-timeout", "90", "--no-save-request-logs"]
    if runtime * 60 < 600:
        command += ["--max-experiment-runtime-minutes", str(runtime * 2)]
    return command


def run_slot(slot, args, command=None):
    started = time.monotonic()
    result = {**slot, "started_at": utc_now(), "status": "running"}
    run_dir = args.output / slot["slot"]
    run_dir.mkdir()
    result["run_dir"] = str(run_dir)
    write_json(run_dir / "slot.json", result)
    env, process, log_thread = child_environment(slot), None, None
    hard_deadline = started + args.per_run_seconds
    try:
        process = subprocess.Popen(command or command_for(args, run_dir), cwd=args.app_dir,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", start_new_session=True)
        result["pid"] = process.pid
        log_thread = threading.Thread(target=copy_log, args=(
            process.stdout, run_dir / "process.log", env.get("LOCAL_ANALYZER_API_KEY", "")), daemon=True)
        log_thread.start()
        try:
            process.wait(timeout=max(0.001, hard_deadline - args.cleanup_seconds - time.monotonic()))
            result["status"] = "completed" if process.returncode == 0 else "process_error"
        except subprocess.TimeoutExpired:
            result["status"] = "timeout"
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=min(5, args.cleanup_seconds / 3))
            except subprocess.TimeoutExpired:
                pass
        finally:
            signal_group(process, signal.SIGKILL)
            process.wait(timeout=max(0.001, min(args.cleanup_seconds / 3, hard_deadline - time.monotonic())))
        result["returncode"] = process.returncode
    except Exception as exc:
        result.update(status="launch_error" if process is None else "runner_error",
                      error_type=type(exc).__name__, returncode=process.returncode if process else None)
    finally:
        if process:
            signal_group(process, signal.SIGKILL)
        if log_thread:
            log_thread.join(timeout=max(0, min(args.cleanup_seconds / 3, hard_deadline - time.monotonic())))
    # The execution/cleanup clock is separate from subsequent offline analysis.
    result.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic() - started, 3))
    result["deadline_exceeded"] = result["elapsed_seconds"] > args.per_run_seconds
    result.update(collect_metrics(run_dir))
    write_json(run_dir / "slot.json", result)
    return result


class ApiCircuit:
    """Inspect completed slots only; stop queued work, retain running/failed slots."""

    def __init__(self):
        self.consecutive_errors = 0
        self.reason = None

    def observe(self, rows):
        terminals = {row.get("request_id"): row for row in rows
                     if row.get("event") in ("response", "error")}
        for row in terminals.values():
            if row["event"] == "response":
                self.consecutive_errors = 0
                continue
            self.consecutive_errors += 1
            code = str(row.get("provider_error_code", "")).lower()
            if row.get("http_status") in (401, 402, 403) or code in (
                    "insufficient_quota", "insufficient_balance", "credit_exhausted", "1113"):
                self.reason = "provider_credentials_or_quota"
        if self.reason is None and self.consecutive_errors >= 3:
            self.reason = "three_consecutive_errors_without_response"
        return self.reason


def slot_usage(run_dir):
    rows = []
    for path in sorted((run_dir / "artifacts").glob("*usage.jsonl")):
        rows.extend(read_jsonl(path, []))
    return rows


def report(output, config, slots, pause_reason=None):
    complete = len(slots) == 8 and all(slot["status"] in TERMINAL for slot in slots)
    write_json(output / "summary.json", {"config": config, "slots": slots, "complete": complete,
                                       "pause_reason": pause_reason, "updated_at": utc_now()})
    columns = ["slot", "arm", "replicate", "status", "elapsed_seconds", "actions",
               "levels_completed", "score_cap_1_15", "model_requests", "model_errors"]
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(slots)
    lines = ["# LS20 预测闭环实验运行状态", "",
             "八槽已结束；最终结果见 analysis.md。" if complete else "实验尚未完整结束，待运行槽位保持 pending。",
             f"队列暂停原因：{pause_reason or '无'}", "",
             "| 槽位 | 状态 | 动作 | 关卡 | 端到端秒 |", "|---|---|---:|---:|---:|"]
    for slot in slots:
        lines.append("| " + " | ".join(str(slot.get(key, "—")) for key in
                                         ("slot", "status", "actions", "levels_completed", "elapsed_seconds")) + " |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_schedule(slots, args, config, execute=run_slot):
    circuit = ApiCircuit()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for replicate in range(1, 3):
            queue = [i for i, slot in enumerate(slots)
                     if slot["replicate"] == replicate and slot["status"] == "pending"]
            active = {}
            while queue or active:
                while queue and len(active) < args.jobs and circuit.reason is None:
                    index = queue.pop(0)
                    slots[index]["status"] = "running"
                    active[pool.submit(execute, slots[index], args)] = index
                report(args.output, config, slots, circuit.reason)
                if not active:
                    break
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: active[item]):
                    index = active.pop(future)
                    try:
                        slots[index] = future.result()
                    except Exception as exc:
                        slots[index].update(status="runner_error", error_type=type(exc).__name__, finished_at=utc_now())
                    circuit.observe(slot_usage(args.output / slots[index]["slot"]))
                    print(json.dumps({key: slots[index].get(key) for key in
                                      ("slot", "status", "actions", "levels_completed", "elapsed_seconds")}), flush=True)
            if circuit.reason:
                break
    report(args.output, config, slots, circuit.reason)
    return circuit.reason


def snapshot_sources(args):
    snapshot = args.output / "source_snapshot"
    repo = args.app_dir.parent
    sources = list((args.app_dir / "inference").rglob("*.py"))
    sources += list((repo / "tufa-arc-agi-framework" / "src" / "taaf").rglob("*.py"))
    sources += [Path(__file__).parent / name for name in (
        "run_prediction_experiment.py", "analyze_prediction_experiment.py", "test_prediction_experiment.py",
        "run_verified_experiment.py", "analyze_verified_experiment.py", "PREDICTION_EXPERIMENT.md")]
    for source in sources:
        target = snapshot / source.relative_to(repo)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return [str(source.relative_to(repo)) for source in sources]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1] / "ARC3-Inference")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-actions", type=int, default=1200)
    parser.add_argument("--per-run-seconds", type=float, default=1800)
    parser.add_argument("--cleanup-seconds", type=float, default=30)
    parser.add_argument("--seed", type=int, default=20260905)
    args = parser.parse_args(argv)
    if args.max_actions < 1 or not 0 < args.cleanup_seconds < args.per_run_seconds - STARTUP_SECONDS <= 1770:
        parser.error("positive actions and 0 < cleanup < duration - 30 <= 1770 required")
    args.output, args.app_dir = args.output.resolve(), args.app_dir.resolve()
    return args


def main(argv=None):
    args = parse_args(argv)
    if os.name != "posix":
        raise SystemExit("Run under Linux/WSL for process-group deadlines.")
    if not os.environ.get("LOCAL_ANALYZER_API_KEY"):
        raise SystemExit("Required environment variable: LOCAL_ANALYZER_API_KEY")
    args.output.mkdir(parents=True, exist_ok=False)
    sources = snapshot_sources(args)
    slots = schedule(args.seed)
    config = {name: getattr(args, name) for name in
              ("jobs", "max_actions", "per_run_seconds", "cleanup_seconds", "seed")}
    effective = args.per_run_seconds - args.cleanup_seconds - STARTUP_SECONDS
    config.update(model=MODEL, game="ls20", repeats=2, scoring=SCORING, created_at=utc_now(),
                  effective_solver_seconds=effective, inline_deployment_seconds=effective + min(effective, 600),
                  startup_reserved_seconds=STARTUP_SECONDS,
                  api_failure_limit=3,
                  frozen_source_files=sources,
                  randomization="two replicate blocks; random.Random(seed).shuffle; no provider seed assumed")
    env = child_environment(slots[0])
    config["model_settings"] = {key: env[key] for key in (
        "LOCAL_ANALYZER_PROVIDER", "LOCAL_ANALYZER_MODEL_ID", "LOCAL_ANALYZER_CONTEXT_WINDOW",
        "LOCAL_ANALYZER_MAX_OUTPUT", "LOCAL_ANALYZER_TIMEOUT", "LOCAL_ANALYZER_TEMPERATURE",
        "LOCAL_ANALYZER_TOP_P", "LOCAL_ANALYZER_TOP_K", "LOCAL_ANALYZER_TOOL_STEPS",
        "LOCAL_ANALYZER_TOOL_TIMEOUT", "LOCAL_ANALYZER_TOOL_OUTPUT_TOKENS",
        "LOCAL_ANALYZER_ENABLE_THINKING", "LOCAL_ANALYZER_REASONING_EFFORT")}
    config["model_settings"].update(LOCAL_ANALYZER_BASE_URL=env["LOCAL_ANALYZER_BASE_URL"],
                                    MULTIMODAL_CONTEXT=env["MULTIMODAL_CONTEXT"], MULTIMODAL_UPSCALE=env["MULTIMODAL_UPSCALE"])
    paused = run_schedule(slots, args, config)
    if paused:
        print(json.dumps({"complete": False, "pause_reason": paused}), flush=True)
        return 2
    from analyze_prediction_experiment import save_analysis
    save_analysis(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
