"""追溯树共享总线（TraceTreeBus）

提供一个全局的需求双向追溯树 `BiReqTraceTree` 与轻量级发布/订阅机制。

"""
import logging
from typing import Callable, Dict, List, Any
import json
import os
import threading

from config.logging_config import CASE_DEBUG_LOGGER_NAME, CASE_RUN_LOGGER_NAME
from config.overhead_recorder import get_overhead_recorder
from model.biReq_trace_tree import BiReqTraceTree
from model.devices_profile import DevicesProfile
from webui.runtime_events import emit_event, emit_tree_snapshot

# 模块级调试开关，由入口 main.py 设置   
_BUS_DEBUG: bool = False
_MAX_REFINE_ITERATION: int = 3
_RUN_LOGGER = logging.getLogger(f"{CASE_RUN_LOGGER_NAME}.TraceTreeBus")
_DEBUG_LOGGER = logging.getLogger(f"{CASE_DEBUG_LOGGER_NAME}.TraceTreeBus")


def _log_summary(message: str) -> None:
    _RUN_LOGGER.info(message)
    _DEBUG_LOGGER.info(message)


def _log_debug(message: str) -> None:
    _DEBUG_LOGGER.debug(message)


def _log_error(message: str) -> None:
    _RUN_LOGGER.error(message)
    _DEBUG_LOGGER.error(message)


def set_bus_debug(flag: bool) -> None:
    global _BUS_DEBUG
    _BUS_DEBUG = bool(flag)


class TraceTreeBus:
    """共享追溯树的发布/订阅总线。

    内部持有一个 `BiReqTraceTree` 实例，并提供简单的主题订阅与消息发布。
    """
    def __init__(self, root_name: str = 'ROOT'):
        """初始化总线并创建共享追溯树（含根 'ROOT'）。"""
        self._root_name = root_name
        self.current_TAPs = []
        self.available_devices = DevicesProfile()
        self.check_result = {}
        self.user_input_rules: List[str] = []
        self.tree = BiReqTraceTree()
        self.tree.add_root(root_name)
        self._subs: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self.refine_iteration = 0
        # RQ3 开销埋点：refinement 达上限且最终检查仍未通过时置位，供 main.py 判定 final_status=max_iteration
        self.refine_capped_unresolved = False
        self._pipeline_lock = threading.RLock()
        self._active_parallel_run_id: str = ""
        self._quality_ready = False
        self._realization_ready = False
        self._initial_check_completed = False
        # 默认订阅以接收并应用追溯树更新
        self.subscribe('trace_tree.add_new_intent', self._add_new_intent_handler)
        self.subscribe('trace_tree.update_realization', self._update_realization_tree)
        self.subscribe('trace_tree.update_quality', self._update_quality_tree)
        self.subscribe('trace_tree.update_refinement', self._update_refinement_tree)
        self.subscribe('trace_tree.attach_constraints', self._attach_constraints_handler)
        self.subscribe("quality_checker.result", self._update_check_result)
        self.subscribe('trace_tree.abstract_intent', self._abstract_intent_handler)

    def reset_run_state(self) -> None:
        """为一次新的运行重置共享状态，但保留订阅关系。"""
        with self._pipeline_lock:
            self.current_TAPs = []
            self.available_devices = DevicesProfile()
            self.check_result = {}
            self.user_input_rules = []
            self.tree = BiReqTraceTree()
            self.tree.add_root(self._root_name)
            self.refine_iteration = 0
            self.refine_capped_unresolved = False
            self._active_parallel_run_id = ""
            self._quality_ready = False
            self._realization_ready = False
            self._initial_check_completed = False

    def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """订阅主题。

        参数：
        - topic：字符串主题名
        - handler：处理函数，签名为 `handler(payload: dict) -> None`
        """
        self._subs.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """发布消息到主题。

        会调用该主题下的所有处理函数；单个处理函数抛出的异常会被吞掉，以确保其他订阅者继续执行。
        """
        source = str(payload.get("source") or "") if isinstance(payload, dict) else ""
        emit_event(
            "bus.topic",
            f"总线事件: {topic}",
            {
                "topic": topic,
                "source": source,
            },
            agent=source or "TraceTreeBus",
        )
        trace_tree = payload.get("trace_tree") if isinstance(payload, dict) else None
        if isinstance(trace_tree, dict):
            emit_tree_snapshot(trace_tree, source=source, topic=topic)
        if topic == "quality_checker.result":
            check_result = payload.get("check_result", {}) if isinstance(payload, dict) else {}
            emit_event(
                "check.result",
                "质量检查结果已更新",
                {"result": check_result, "topic": topic},
                agent=source or "QualityChecker",
            )
        for h in self._subs.get(topic, []):
            try:
                h(payload)
            except Exception as e:
                _log_error(f"处理主题 {topic} 时出错: {e}")

    def get_tree(self) -> BiReqTraceTree:
        """返回共享的 `BiReqTraceTree` 实例。"""
        return self.tree

    def start_parallel_planning_run(self, run_id: str) -> None:
        """初始化一次并行规划运行的屏障状态。"""
        with self._pipeline_lock:
            self._active_parallel_run_id = str(run_id or "").strip()
            self._quality_ready = False
            self._realization_ready = False
            self._initial_check_completed = False
            self.refine_iteration = 0
            self.refine_capped_unresolved = False

    def _has_active_parallel_run(self) -> bool:
        with self._pipeline_lock:
            return bool(self._active_parallel_run_id)

    def _mark_quality_ready(self) -> None:
        with self._pipeline_lock:
            if self._active_parallel_run_id:
                self._quality_ready = True

    def _mark_realization_ready(self) -> None:
        with self._pipeline_lock:
            if self._active_parallel_run_id:
                self._realization_ready = True

    def _should_run_initial_attach_and_check(self) -> bool:
        with self._pipeline_lock:
            if not self._active_parallel_run_id:
                return False
            if self._initial_check_completed:
                return False
            if not (self._quality_ready and self._realization_ready):
                return False
            self._initial_check_completed = True
            return True

    def _run_attach_and_check(self) -> None:
        # 质量约束附加阶段暂时停用，先只保留质量检查链路。
        # from Agents.QualityPlanner import QualityPlannerAgent
        # quality_planner = QualityPlannerAgent()
        # quality_planner.run_attach(debug=_BUS_DEBUG)
        from Agents.QualityChecker import QualityCheckerAgent
        quality_checker = QualityCheckerAgent()
        quality_checker.run(debug=_BUS_DEBUG)


    def _add_new_intent_handler(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                # IntentReader 已直接写入共享追溯树，这里只保留事件语义，避免并行时用旧快照覆盖新状态。
                pass
            except Exception as e:
                raise e

    
    def _update_realization_tree(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                self._mark_realization_ready()
                if self._has_active_parallel_run():
                    if self._should_run_initial_attach_and_check():
                        self._run_attach_and_check()
                else:
                    self._run_attach_and_check()
            except Exception as e:
                raise e
    
    def _update_quality_tree(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                self._mark_quality_ready()
                if self._should_run_initial_attach_and_check():
                    self._run_attach_and_check()
            except Exception as e:
                raise e
    
    def _update_refinement_tree(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                self.refine_iteration += 1
                # RQ3 开销埋点：第 n 次 refinement 后的检查/精化属于第 n+1 轮
                get_overhead_recorder().set_iteration(self.refine_iteration + 1)
                self._run_attach_and_check()
            except Exception as e:
                raise e

    def _abstract_intent_handler(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                from Agents.RealizationPlanner import RealizationPlannerAgent
                planner = RealizationPlannerAgent()
                new_intent_nodes = self.tree._get_new_intent_node()
                if new_intent_nodes:
                    planner.run_batch(new_intent_nodes, debug=_BUS_DEBUG)
            except Exception as e:
                raise e
            
    def _attach_constraints_handler(self, payload: Dict[str, Any]) -> None:
        d = payload.get('trace_tree')
        if isinstance(d, dict):
            try:
                # QualityPlanner 已直接写入共享追溯树，这里无需再次回放快照。
                pass
            except Exception as e:
                raise e
    
    def _update_check_result(self, payload: Dict[str, Any]) -> None:
        d = payload.get("check_result", {})
        if self.refine_iteration >= _MAX_REFINE_ITERATION:
            if isinstance(d, dict) and not d.get("all_TAP_ok", True):
                # RQ3 开销埋点：达到最大精化轮次仍未通过，标记 final_status=max_iteration
                self.refine_capped_unresolved = True
            _log_summary(f"已达到最大精化迭代次数 {_MAX_REFINE_ITERATION}，不再进行精化")
            return
        if isinstance(d, dict):
            try:
                self.check_result = d
                # TODO IRP的精化还没加进去
                if not self.check_result.get("all_TAP_ok", False):
                    from Agents.RealizationPlanner import RealizationPlannerAgent
                    planner = RealizationPlannerAgent()
                    planner.run_refinement(self.check_result, debug=_BUS_DEBUG)
            except Exception as e:
                raise e

    def update_tree(self, new_tree: BiReqTraceTree) -> None:
        # 就地更新以保持对象引用稳定
        self.tree.nodes = new_tree.nodes
        self.tree.root_id = new_tree.root_id

    def _update_tree_from_dict(self, tree_dict: Dict[str, Any]) -> None:
        new_tree = BiReqTraceTree.from_dict(tree_dict)
        self.update_tree(new_tree)

    def get_tree_dict(self) -> Dict[str, Any]:
        return self.tree.to_dict()

    def set_available_devices(self, devices: list[Any]) -> None:
        """设置可用设备列表。"""
        self.available_devices.set_env_profile(list(devices or []), debug=_BUS_DEBUG)

    def get_available_devices(self) -> DevicesProfile:
        """获取可用设备列表。"""
        return self.available_devices

    def set_user_input_rules(self, rules: list[Any]) -> None:
        if not isinstance(rules, list):
            self.user_input_rules = []
            return
        merged: List[str] = []
        seen = set()
        for r in rules:
            if isinstance(r, str):
                s = r.strip()
                if s and s not in seen:
                    merged.append(s)
                    seen.add(s)
        self.user_input_rules = merged

    def add_user_input_rules(self, rules: list[Any]) -> None:
        if not isinstance(rules, list):
            return
        seen = set(self.user_input_rules)
        for r in rules:
            if isinstance(r, str):
                s = r.strip()
                if s and s not in seen:
                    self.user_input_rules.append(s)
                    seen.add(s)

    def get_user_input_rules(self) -> List[str]:
        return list(self.user_input_rules)

    def save_tree_to_json(self, file_path: str) -> None:
        """将当前追溯树保存为JSON文件。"""
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        
        try:
            self.get_tree().save_to_json(file_path)
            self.get_tree().export_dsl_rules_to_json(file_path.replace('.json', '_dsl.json'))
            device_base_path = os.path.join(dir_path or ".", "device_base.json")
            with open(device_base_path, "w", encoding="utf-8") as f:
                json.dump(self.get_available_devices().get_abstract_devices(), f, ensure_ascii=False, indent=2)
            _log_summary(f"追溯树已保存到 {file_path}")
            _log_summary(f"DSL规则已保存到 {file_path.replace('.json', '_dsl.json')}")
            _log_summary(f"设备库已保存到 {device_base_path}")
        except Exception as e:
            raise e
        
        

_shared_bus = TraceTreeBus("IoT系统")


def get_shared_trace_bus() -> TraceTreeBus:
    """获取全局唯一的追溯树总线。"""
    return _shared_bus


def get_shared_trace_tree() -> BiReqTraceTree:
    """获取总线内的共享追溯树实例。"""
    return _shared_bus.get_tree()

def set_available_devices(devices: list[Any]) -> None:
    """设置可用设备列表。"""
    _shared_bus.set_available_devices(devices)


def get_available_devices() -> DevicesProfile:
    """获取可用设备列表。"""
    return _shared_bus.get_available_devices()

def set_user_input_rules(rules: list[Any]) -> None:
    _shared_bus.set_user_input_rules(rules)

def add_user_input_rules(rules: list[Any]) -> None:
    _shared_bus.add_user_input_rules(rules)

def get_user_input_rules() -> List[str]:
    return _shared_bus.get_user_input_rules()

def save_tree_to_json(file_path: str) -> None:
    """将当前追溯树保存为JSON文件。"""
    _shared_bus.save_tree_to_json(file_path)
