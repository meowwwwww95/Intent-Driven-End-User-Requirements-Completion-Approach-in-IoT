#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import argparse
import sys
import os
from concurrent.futures import ThreadPoolExecutor
import uuid
from typing import Any, Optional
from Agents.IntentReader import IntentReaderAgent
from Agents.QualityPlanner import QualityPlannerAgent
from model.trace_tree_bus import set_bus_debug
from model.trace_tree_bus import set_available_devices
from model.trace_tree_bus import save_tree_to_json
from model.trace_tree_bus import get_shared_trace_bus
from config.ai_client import get_default_ai_client
import logging
import time
from config.logging_config import get_case_debug_logger
from config.logging_config import redirect_stdout_to_logger
from config.logging_config import setup_case_logging
from config.overhead_recorder import get_overhead_recorder
from config.overhead_recorder import infer_case_info_from_path
from webui.runtime_events import activate_reporter, emit_event

AGENT_STAT_ORDER = [
    "QualityPlanner",
    "IntentReader",
    "RealizationPlanner",
    "QualityChecker",
]

SCOPE_TYPE_LABELS = {
    "agent": "Agent",
    "tool": "工具",
    "step": "步骤",
    "scope": "作用域",
}


def _merge_intervals(intervals: list[list[float] | tuple[float, float]]) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            continue
        start_value = float(interval[0])
        end_value = float(interval[1])
        if end_value < start_value:
            start_value, end_value = end_value, start_value
        normalized.append((start_value, end_value))
    if not normalized:
        return []
    normalized.sort(key=lambda item: item[0])
    merged: list[tuple[float, float]] = [normalized[0]]
    for start_value, end_value in normalized[1:]:
        last_start, last_end = merged[-1]
        if start_value <= last_end:
            merged[-1] = (last_start, max(last_end, end_value))
        else:
            merged.append((start_value, end_value))
    return merged


def _intervals_duration(intervals: list[list[float] | tuple[float, float]]) -> float:
    return sum(end_value - start_value for start_value, end_value in _merge_intervals(intervals))


def _format_usage_line(prefix: str, usage: dict) -> str:
    scope_name = str(usage.get("scope_name") or "")
    scope_type = str(usage.get("scope_type") or "scope")
    scope_label = SCOPE_TYPE_LABELS.get(scope_type, "作用域")
    if scope_type == "agent":
        title = scope_name
    else:
        title = f"[{scope_label}] {scope_name}"
    return (
        f"{prefix}{title}: 墙钟耗时 {float(usage.get('wall_clock_seconds', 0.0) or 0.0):.2f} 秒, "
        f"独占耗时 {float(usage.get('exclusive_seconds', 0.0) or 0.0):.2f} 秒, "
        f"Token {int(usage.get('tokens', 0) or 0)}, "
        f"LLM调用 {int(usage.get('api_calls', 0) or 0)} 次, "
        f"执行 {int(usage.get('invocations', 0) or 0)} 次"
    )


def _sort_child_scope_keys(scope_keys: list[str], scope_usage: dict) -> list[str]:
    def _sort_key(scope_key: str) -> tuple[int, int, str]:
        usage = scope_usage.get(scope_key, {})
        scope_type = str(usage.get("scope_type") or "scope")
        scope_name = str(usage.get("scope_name") or "")
        type_order = {"tool": 0, "step": 1, "scope": 2, "agent": 3}
        return (
            type_order.get(scope_type, 9),
            -int(usage.get("tokens", 0) or 0),
            scope_name,
        )

    return sorted(scope_keys, key=_sort_key)


def _empty_usage(scope_name: str, scope_type: str) -> dict:
    return {
        "scope_name": scope_name,
        "scope_type": scope_type,
        "tokens": 0,
        "api_calls": 0,
        "invocations": 0,
        "wall_clock_seconds": 0.0,
        "exclusive_seconds": 0.0,
        "intervals": [],
    }


def _merge_usage_entry(target: dict, source: dict) -> None:
    target["tokens"] = int(target.get("tokens", 0) or 0) + int(source.get("tokens", 0) or 0)
    target["api_calls"] = int(target.get("api_calls", 0) or 0) + int(source.get("api_calls", 0) or 0)
    target["invocations"] = int(target.get("invocations", 0) or 0) + int(source.get("invocations", 0) or 0)
    target["exclusive_seconds"] = float(target.get("exclusive_seconds", 0.0) or 0.0) + float(source.get("exclusive_seconds", 0.0) or 0.0)
    target_intervals = list(target.get("intervals") or [])
    target_intervals.extend(list(source.get("intervals") or []))
    merged_intervals = _merge_intervals(target_intervals)
    target["intervals"] = [[start_value, end_value] for start_value, end_value in merged_intervals]
    target["wall_clock_seconds"] = _intervals_duration(merged_intervals)


def _find_owner_agent_name(scope_key: str, scope_usage: dict) -> str:
    current_key = scope_key
    while current_key:
        usage = scope_usage.get(current_key, {})
        if str(usage.get("scope_type") or "scope") == "agent":
            return str(usage.get("scope_name") or "")
        parent_key = usage.get("parent_key")
        current_key = parent_key if isinstance(parent_key, str) and parent_key else ""
    return ""


def _build_merged_agent_usage(scope_usage: dict) -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    agent_totals: dict[str, dict] = {}
    agent_children: dict[str, dict[str, dict]] = {}

    for scope_key, usage in scope_usage.items():
        scope_type = str(usage.get("scope_type") or "scope")
        scope_name = str(usage.get("scope_name") or "")
        if not scope_name:
            continue

        if scope_type == "agent":
            merged_agent = agent_totals.setdefault(scope_name, _empty_usage(scope_name, "agent"))
            _merge_usage_entry(merged_agent, usage)
            continue

        owner_agent_name = _find_owner_agent_name(scope_key, scope_usage)
        if not owner_agent_name:
            continue
        child_group = agent_children.setdefault(owner_agent_name, {})
        child_key = f"{scope_type}:{scope_name}"
        merged_child = child_group.setdefault(child_key, _empty_usage(scope_name, scope_type))
        _merge_usage_entry(merged_child, usage)

    return agent_totals, agent_children


def _sort_merged_usage_list(items: list[dict]) -> list[dict]:
    def _sort_key(usage: dict) -> tuple[int, int, str]:
        scope_type = str(usage.get("scope_type") or "scope")
        scope_name = str(usage.get("scope_name") or "")
        type_order = {"tool": 0, "step": 1, "scope": 2, "agent": 3}
        return (
            type_order.get(scope_type, 9),
            -int(usage.get("tokens", 0) or 0),
            scope_name,
        )

    return sorted(items, key=_sort_key)

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='intent_reader_entry', description='从JSON文件输入一个case（包含TAP集合与环境设备列表）并交由IntentReader处理')
    parser.add_argument('--file', dest='file', required=True, help='case JSON 文件路径，包含 user_input 与 env_profile 字段')
    parser.add_argument('--out', dest='out', required=True, help='输出追溯树 JSON 文件路径（同时会生成 *_dsl.json）')
    parser.add_argument('--debug', dest='debug', action='store_true', help='启用调试输出')
    parser.add_argument('--case-id', dest='case_id', default=None, help='RQ3 开销埋点用的 case 唯一标识（缺省从 --file/--out 路径推断）')
    parser.add_argument('--difficulty', dest='difficulty', default=None, help='RQ3 开销埋点用的难度等级 L1-L5（缺省从 --file 路径推断）')
    parser.add_argument('--experiment-run-id', dest='experiment_run_id', default=None, help='RQ3 开销埋点用的实验批次标识')
    return parser.parse_args()


def _load_case(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    taps: list[str] = []
    devices: list[str] = []
    try:
        with open(args.file, 'r', encoding='utf-8') as f:
            obj = json.load(f)
            
            # 仅支持新的 user_input 字段
            arr = obj.get('user_input') or []
            if isinstance(arr, list):
                for x in arr:
                    if isinstance(x, str) and x.strip():
                        taps.append(x.strip())
            
            # 仅支持新的 env_profile 字段
            env_profile = obj.get('env_profile') or []
            if isinstance(env_profile, list):
                for d in env_profile:
                    devices.append(d)
    except Exception as e:
        raise Exception(f"加载文件 {args.file} 失败: {str(e)}")

    return taps, devices


def _log_summary(run_logger: logging.Logger, debug_logger: logging.Logger, message: str) -> None:
    run_logger.info(message)
    debug_logger.info(message)

def run_case(
    case_file: str,
    out_file: str,
    *,
    debug: bool = False,
    reporter: Optional[Any] = None,
    redirect_stdio: bool = True,
    case_id: Optional[str] = None,
    difficulty: Optional[str] = None,
    experiment_run_id: Optional[str] = None,
) -> dict[str, Any]:
    effective_debug = bool(debug) or (not bool(getattr(sys.__stdout__, "isatty", lambda: False)()))
    out_dir = os.path.dirname(os.path.abspath(out_file))
    os.makedirs(out_dir, exist_ok=True)
    run_logger, debug_logger = setup_case_logging(out_dir, overwrite=True)
    if redirect_stdio:
        redirect_stdout_to_logger(debug_logger)

    run_log_handler = reporter.build_run_log_handler() if reporter else None
    if run_log_handler is not None:
        run_logger.addHandler(run_log_handler)

    ai_client = get_default_ai_client()
    ai_client.reset_usage_stats()
    start_time = time.time()
    run_id = uuid.uuid4().hex

    # RQ3 开销埋点：Case 级记录开始（case_id/difficulty 缺省从输入或输出路径推断）
    inferred_difficulty, inferred_case_name = infer_case_info_from_path(case_file)
    if not inferred_difficulty or not inferred_case_name:
        out_difficulty, out_case_name = infer_case_info_from_path(out_file)
        inferred_difficulty = inferred_difficulty or out_difficulty
        inferred_case_name = inferred_case_name or out_case_name
    effective_difficulty = difficulty or inferred_difficulty
    effective_case_id = case_id or (
        f"{effective_difficulty}/{inferred_case_name}" if effective_difficulty and inferred_case_name
        else os.path.splitext(os.path.basename(out_file))[0]
    )
    recorder = get_overhead_recorder()
    recorder.begin_case(
        case_id=effective_case_id,
        difficulty_level=effective_difficulty,
        experiment_run_id=experiment_run_id,
        out_dir=out_dir,
        run_id=run_id,
    )
    run_error_type: Optional[str] = None
    bus = None

    _log_summary(run_logger, debug_logger, f"任务开始: run_id={run_id}")
    debug_logger.debug(f"输入文件: {os.path.abspath(case_file)}")
    debug_logger.debug(f"输出文件: {os.path.abspath(out_file)}")
    debug_logger.debug(f"调试模式: {effective_debug}")
    summary_lines: list[str] = []
    scope_usage: dict[str, dict] = {}

    class _ArgsProxy:
        def __init__(self, file_path: str):
            self.file = file_path

    with activate_reporter(reporter):
        try:
            emit_event(
                "run.started",
                "Case 运行已启动",
                {
                    "run_id": run_id,
                    "case_file": os.path.abspath(case_file),
                    "out_file": os.path.abspath(out_file),
                },
            )
            set_bus_debug(effective_debug)
            bus = get_shared_trace_bus()
            bus.reset_run_state()
            taps, devices = _load_case(_ArgsProxy(case_file))

            set_available_devices(devices)
            bus.start_parallel_planning_run(run_id)

            intentReader = IntentReaderAgent()
            qualityPlanner = QualityPlannerAgent()

            emit_event(
                "run.progress",
                "并行阶段即将开始",
                {
                    "stage": "parallel_planning",
                    "agents": ["IntentReader", "QualityPlanner"],
                    "tap_count": len(taps),
                    "device_count": len(devices),
                },
            )
            # _log_summary(run_logger, debug_logger, "并行阶段开始: IntentReader + QualityPlanner")
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(intentReader.run_batch, taps, effective_debug),
                    executor.submit(qualityPlanner.run_plan, None, effective_debug),
                ]
                for future in futures:
                    future.result()
            save_tree_to_json(out_file)
            emit_event(
                "run.progress",
                "主流程完成，结果已写出",
                {"stage": "completed", "out_file": os.path.abspath(out_file)},
            )
            _log_summary(run_logger, debug_logger, "主流程完成，已写出追溯树结果")
        except Exception as e:
            run_error_type = type(e).__name__
            run_logger.error(f"运行失败: {str(e)}")
            get_case_debug_logger().exception("运行失败")
            emit_event(
                "run.failed",
                "Case 运行失败",
                {"run_id": run_id, "error": str(e)},
                level="error",
            )
            raise Exception(f"运行失败: {str(e)}")
        finally:
            end_time = time.time()
            elapsed_time = end_time - start_time
            # RQ3 开销埋点：Case 级记录结束（refinement 达上限且仍未通过记为 max_iteration）
            if run_error_type:
                recorder.end_case(final_status="error", error_type=run_error_type)
            elif bool(getattr(bus, "refine_capped_unresolved", False)):
                recorder.end_case(final_status="max_iteration")
            else:
                recorder.end_case(final_status="success")
            total_tokens = ai_client.total_tokens
            scope_usage = ai_client.get_scope_usage_summary()
            summary_lines = [
                "================ 统计信息 ================",
                f"总墙钟耗时: {elapsed_time:.2f} 秒",
                f"总Token消耗: {total_tokens}",
            ]
            agent_totals, agent_children = _build_merged_agent_usage(scope_usage)
            ordered_agent_names = [name for name in AGENT_STAT_ORDER if name in agent_totals]
            for usage in _sort_merged_usage_list(list(agent_totals.values())):
                agent_name = str(usage.get("scope_name") or "")
                if agent_name and agent_name not in ordered_agent_names:
                    ordered_agent_names.append(agent_name)
            if ordered_agent_names:
                summary_lines.append("各Agent统计（含工具/步骤明细）:")
                summary_lines.append("说明: 墙钟耗时含子调用；独占耗时排除同步调用的子Agent，保留本Agent内工具和步骤耗时。")
                for agent_name in ordered_agent_names:
                    usage = agent_totals.get(agent_name, {})
                    summary_lines.append(_format_usage_line("- ", usage))
                    for child_usage in _sort_merged_usage_list(list((agent_children.get(agent_name) or {}).values())):
                        summary_lines.append(_format_usage_line("  - ", child_usage))
            summary_lines.append("==========================================")
            for line in summary_lines:
                _log_summary(run_logger, debug_logger, line)
            emit_event(
                "run.finished",
                "Case 运行结束",
                {
                    "run_id": run_id,
                    "elapsed_seconds": elapsed_time,
                    "total_tokens": total_tokens,
                    "summary_lines": summary_lines,
                    "scope_usage": scope_usage,
                    "out_file": os.path.abspath(out_file),
                },
            )
            if run_log_handler is not None:
                run_logger.removeHandler(run_log_handler)
                try:
                    run_log_handler.close()
                except Exception:
                    pass

    return {
        "run_id": run_id,
        "elapsed_seconds": elapsed_time,
        "total_tokens": total_tokens,
        "scope_usage": scope_usage,
        "summary_lines": summary_lines,
        "out_file": os.path.abspath(out_file),
    }


def main():
    args = _parse_args()
    run_case(
        args.file,
        args.out,
        debug=bool(args.debug),
        redirect_stdio=True,
        case_id=args.case_id,
        difficulty=args.difficulty,
        experiment_run_id=args.experiment_run_id,
    )

if __name__ == '__main__':
    main()
