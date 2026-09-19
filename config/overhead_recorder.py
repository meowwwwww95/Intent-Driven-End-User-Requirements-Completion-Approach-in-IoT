#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQ3 系统开销埋点记录器

按《RQ3 系统开销分析埋点需求文档》落三类原始记录（均写入 case 输出目录的 overhead.jsonl）：
- case_run：一次完整 Case 执行的端到端记录（case_id、difficulty_level、起止时间、final_status 等）
- agent_run：一次 Agent 运行的记录（含无 LLM 调用的 QualityChecker/z3 检测耗时，token 为内部 LLM 调用聚合）
- llm_call：一次 LLM 调用的记录（最小分析粒度，记 input/output tokens、model、status 等）

设计约定：
- 单进程单 case（与项目既有单例约定一致），模块级单例 + RLock 保证多线程写盘安全
- 线程本地栈维护 agent_run 嵌套关系，parent_call_id 取栈内上一层 agent_run；
  跨线程池传播通过 snapshot_context()/inherit_context()（与 ai_client 的 scope 快照机制平行）
- 每完成一条记录立即 append 并 flush，进程崩溃也保留已完成记录
- 未 begin_case 时所有 hook 静默空转，不影响 test/、evaluator/ 等其他入口
"""

import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

_OVERHEAD_FILE_NAME = "overhead.jsonl"


def _now_iso() -> str:
    """带时区的 ISO 8601 时间戳。"""
    return datetime.now().astimezone().isoformat()


def infer_case_info_from_path(path: str) -> tuple[Optional[str], Optional[str]]:
    """从输入/输出文件路径推断 (difficulty_level, case_name)。

    匹配 .../L1..L5/<case_dir>/... 结构；推断不出时返回 (None, None)。
    """
    if not path:
        return None, None
    normalized = str(path).replace("\\", "/")
    match = re.search(r"/(L[1-5])/([^/]+)/", normalized)
    if not match:
        return None, None
    return match.group(1), match.group(2)


class OverheadRecorder:
    """RQ3 开销埋点记录器（单例）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread_local = threading.local()
        self._reset_case_state()

    # -------------------- 内部状态 --------------------

    def _reset_case_state(self) -> None:
        self._case_id: Optional[str] = None
        self._difficulty_level: Optional[str] = None
        self._experiment_run_id: Optional[str] = None
        self._run_id: Optional[str] = None
        self._out_file: Optional[str] = None
        self._case_start_iso: Optional[str] = None
        self._call_seq = 0
        self._iteration_id = 1
        self._final_status: Optional[str] = None

    def _get_agent_stack(self) -> List[Dict[str, Any]]:
        stack = getattr(self._thread_local, "agent_stack", None)
        if stack is None:
            stack = []
            self._thread_local.agent_stack = stack
        return stack

    def _next_call_id(self) -> str:
        with self._lock:
            self._call_seq += 1
            return f"{self._case_id}_call_{self._call_seq:03d}"

    def _append_record(self, record: Dict[str, Any]) -> None:
        with self._lock:
            if not self._out_file:
                return
            try:
                with open(self._out_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                # 埋点失败不影响主流程
                pass

    # -------------------- Case 级 --------------------

    def begin_case(
        self,
        case_id: str,
        difficulty_level: Optional[str] = None,
        experiment_run_id: Optional[str] = None,
        out_dir: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """开始一次 Case 记录，写 case_start（case_end 在 end_case 时补）。"""
        with self._lock:
            self._reset_case_state()
            self._case_id = str(case_id or "unknown_case")
            self._difficulty_level = difficulty_level
            self._experiment_run_id = experiment_run_id
            self._run_id = run_id
            self._case_start_iso = _now_iso()
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
                self._out_file = os.path.join(out_dir, _OVERHEAD_FILE_NAME)

    def end_case(self, final_status: str = "success", error_type: Optional[str] = None) -> None:
        """结束 Case 记录并落盘 case_run。"""
        with self._lock:
            if not self._case_id:
                return
            record = {
                "record_type": "case_run",
                "case_id": self._case_id,
                "difficulty_level": self._difficulty_level,
                "case_start_time": self._case_start_iso,
                "case_end_time": _now_iso(),
                "final_status": final_status,
                "experiment_run_id": self._experiment_run_id,
                "run_id": self._run_id,
            }
            if final_status != "success" and error_type:
                record["error_type"] = error_type
            self._final_status = final_status
        self._append_record(record)

    # -------------------- 轮次与检查结果 --------------------

    def set_iteration(self, iteration_id: int) -> None:
        """设置当前迭代轮次（首轮为 1，由总线在 refinement 递增时更新）。"""
        with self._lock:
            self._iteration_id = max(1, int(iteration_id))

    def set_checker_result(
        self,
        checker_result: str,
        feedback_types: Optional[List[str]] = None,
    ) -> None:
        """把 pass/fail 与问题类型写到线程当前最内层的 agent_run 记录（QualityChecker 用）。"""
        stack = self._get_agent_stack()
        if not stack:
            return
        frame = stack[-1]
        frame["checker_result"] = checker_result
        if feedback_types:
            frame["feedback_type"] = list(feedback_types)

    # -------------------- Agent 运行级 --------------------

    @contextmanager
    def agent_run(self, agent_name: str, trigger_type: str = "initial") -> Iterator[None]:
        """记录一次 Agent 运行。未 begin_case 时静默空转。"""
        if not self._case_id:
            yield
            return
        stack = self._get_agent_stack()
        parent_call_id = stack[-1]["call_id"] if stack else None
        with self._lock:
            iteration_id = self._iteration_id
        frame: Dict[str, Any] = {
            "call_id": self._next_call_id(),
            "agent_name": agent_name,
            "iteration_id": iteration_id,
            "start_time": _now_iso(),
            "trigger_type": trigger_type,
            "parent_call_id": parent_call_id,
            "checker_result": None,
            "feedback_type": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        stack.append(frame)
        status = "success"
        error_type: Optional[str] = None
        try:
            yield
        except Exception as e:
            status = "error"
            error_type = type(e).__name__
            raise
        finally:
            if stack and stack[-1] is frame:
                stack.pop()
            elif frame in stack:
                stack.remove(frame)
            record = {
                "record_type": "agent_run",
                "case_id": self._case_id,
                "call_id": frame["call_id"],
                "agent_name": frame["agent_name"],
                "iteration_id": frame["iteration_id"],
                "start_time": frame["start_time"],
                "end_time": _now_iso(),
                "status": status,
                "input_tokens": int(frame["input_tokens"]),
                "output_tokens": int(frame["output_tokens"]),
                "trigger_type": frame["trigger_type"],
            }
            if frame.get("parent_call_id"):
                record["parent_call_id"] = frame["parent_call_id"]
            if frame.get("checker_result"):
                record["checker_result"] = frame["checker_result"]
            if frame.get("feedback_type"):
                record["feedback_type"] = frame["feedback_type"]
            if error_type:
                record["error_type"] = error_type
            self._append_record(record)

    # -------------------- LLM 调用级 --------------------

    @contextmanager
    def llm_call(self, model: Optional[str] = None, provider: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """记录一次 LLM 调用。

        yield 出一个 dict，调用方在拿到响应后回填：
        input_tokens / output_tokens / cached_input_tokens / reasoning_tokens / model_name。
        未 begin_case 时静默空转（yield 空 dict）。
        """
        if not self._case_id:
            yield {}
            return
        stack = self._get_agent_stack()
        parent_frame = stack[-1] if stack else None
        with self._lock:
            iteration_id = self._iteration_id
        trigger_type = "retry" if getattr(self._thread_local, "retry_mark", False) else "initial"
        record: Dict[str, Any] = {
            "record_type": "llm_call",
            "case_id": self._case_id,
            "call_id": self._next_call_id(),
            "agent_name": parent_frame["agent_name"] if parent_frame else None,
            "iteration_id": iteration_id,
            "start_time": _now_iso(),
            "model_name": model,
            "provider": provider,
            "trigger_type": trigger_type,
        }
        if parent_frame:
            record["parent_call_id"] = parent_frame["call_id"]
        detail: Dict[str, Any] = {}
        status = "success"
        error_type: Optional[str] = None
        try:
            yield detail
        except Exception as e:
            status = "error"
            error_type = type(e).__name__
            raise
        finally:
            record["end_time"] = _now_iso()
            record["status"] = status
            if detail.get("model_name"):
                record["model_name"] = detail["model_name"]
            record["input_tokens"] = int(detail.get("input_tokens", 0) or 0)
            record["output_tokens"] = int(detail.get("output_tokens", 0) or 0)
            if detail.get("cached_input_tokens") is not None:
                record["cached_input_tokens"] = int(detail["cached_input_tokens"])
            if detail.get("reasoning_tokens") is not None:
                record["reasoning_tokens"] = int(detail["reasoning_tokens"])
            if error_type:
                record["error_type"] = error_type
            # 聚合到调用链上所有 agent_run（与 ai_client 作用域统计一致：祖先帧"含子调用"）
            with self._lock:
                for frame in stack:
                    frame["input_tokens"] = int(frame.get("input_tokens", 0)) + record["input_tokens"]
                    frame["output_tokens"] = int(frame.get("output_tokens", 0)) + record["output_tokens"]
            self._append_record(record)

    @contextmanager
    def mark_retry(self) -> Iterator[None]:
        """把块内的 LLM 调用标记为 retry（completion_with_retry 第 2 次起的尝试用）。"""
        previous = getattr(self._thread_local, "retry_mark", False)
        self._thread_local.retry_mark = True
        try:
            yield
        finally:
            self._thread_local.retry_mark = previous

    # -------------------- 跨线程传播 --------------------

    def snapshot_context(self) -> List[Dict[str, Any]]:
        """获取当前线程的 agent_run 栈快照，供子线程继承。

        帧对象按引用共享（不拷贝），这样子线程内 llm_call 的 token 聚合
        能写回父线程的 agent_run 帧；各 Agent 线程池均在退出 agent_run 前
        join  worker，因此帧生命周期安全。
        """
        return list(self._get_agent_stack())

    @contextmanager
    def inherit_context(self, snapshot: Optional[List[Dict[str, Any]]] = None) -> Iterator[None]:
        """在当前线程临时继承父线程的 agent_run 栈。"""
        previous_stack = self._get_agent_stack()
        self._thread_local.agent_stack = list(snapshot or [])
        try:
            yield
        finally:
            self._thread_local.agent_stack = previous_stack


# ==================== 全局单例与便捷函数 ====================

_recorder = OverheadRecorder()


def get_overhead_recorder() -> OverheadRecorder:
    """获取全局开销记录器单例。"""
    return _recorder
