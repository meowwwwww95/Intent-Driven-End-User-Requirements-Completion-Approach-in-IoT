#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能意图推断Agent

"""

import logging
import sys
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

from dotenv.main import rewrite

# 将项目根目录添加到sys.path，以便导入其他模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.ai_client import get_default_ai_client, create_system_message, create_user_message
from config.logging_config import CASE_DEBUG_LOGGER_NAME, CASE_RUN_LOGGER_NAME
from config.overhead_recorder import get_overhead_recorder
from tool.TAP_extraction import extract_tap_rule
from tool.SR2CG import SR2CG
from tool.IRP2AtomINT import generate_atom_int_rule
from tool.SYS2IRP import convert_specific_devices_to_function_types
from tool.AtomINT2HighINT import abstract_atom_ints
from model.biReq_trace_tree import NodeKind
from model.trace_tree_bus import get_shared_trace_tree, get_shared_trace_bus, get_available_devices, set_available_devices
from webui.runtime_events import emit_agent_status

class IntentReaderAgent:
    """
    功能意图推断Agent
    """
    
    def __init__(self):
        """
        初始化意图读取Agent
        """
        self.ai_client = get_default_ai_client()
        self._run_counter = 0
        self._current_run: Dict[str, str] = {}
        self.bus = get_shared_trace_bus()
        self.trace_tree = None
        self.run_logger = logging.getLogger(f"{CASE_RUN_LOGGER_NAME}.IntentReader")
        self.debug_logger = logging.getLogger(f"{CASE_DEBUG_LOGGER_NAME}.IntentReader")
        self.reflection_prompt = """
# 任务
你是一个功能意图推断助手。你的任务是根据输入的TAP规则和功能意图，判断TAP规则是否能满足该功能意图。
# 判断依据
1. 优先判断 TAP 规则表达的功能效果，而不是把设备动作字面值当成唯一目标。例如开启加湿器可以满足升高湿度，开启空气净化器可以降低二氧化碳浓度。
2. 当功能意图已经是环境效果表达时，不要因为 TAP 使用了具体设备动作（如 power_on、turn_on）就误判为不满足。
3. 如果功能意图里出现 XXX 这类占位符，应优先结合 TAP 规则语义恢复为具体环境属性；不能因为占位符存在就把环境效果意图改写成设备动作意图。

# 案例
输入 TAP rule: IF motion_detector_pro.reading() == 有人活动 THEN turn_on() living_room_lamp, 
输入 Atom_INT: IF 有人活动 THEN 升高 亮度
输出: 是
原因: TAP规则想表达的是检测到有人活动时才打开灯，而功能意图是有人活动时升高亮度，两者是一致的。

# 案例
输入 TAP rule: IF bedroom_humidity_sensor.reading() < 42.5 THEN power_on() humidifier_unit,
输入 Atom_INT: IF 湿度 < 42.5 THEN 升高 湿度
输出: 是
原因: 打开加湿器的目的就是在湿度较低时加湿空气，因此该设备动作可以满足“升高湿度”的功能意图。

# 限制
1. 如果TAP规则能满足该功能意图，返回"是"；否则返回"否"。
2. 只需要返回"是"或"否"，不需要返回其他内容。
3. THEN之后不允许出现AND，即只能有一个动作。如果出现AND，则直接返回"否"
        """
        self.rewrite_prompt = """
# 任务
你是一个功能意图重写助手。你的任务是根据输入的功能意图和TAP规则，现在已知该TAP规则不满足功能意图，你需要重写功能意图，使其能被TAP规则满足。

# 案例
输入 TAP rule: IF study_temp_probe.reading() > 34 THEN turn_cool_on() split_ac_unit_pro,
输入 Atom_INT: IF 温度 > 34 THEN 升高 温度 AND 降低 温度
输出重写意图: IF 温度 > 34 THEN 降低 温度

# 案例
输入 TAP rule: IF motion_detector_pro.reading() == 有人活动 THEN turn_on() living_room_lamp, 
输入 Atom_INT: IF 有人活动 == 有人活动 THEN 升高 亮度
输出重写意图: IF 有人活动 THEN 升高 亮度

# 案例
输入 TAP rule: IF bedroom_humidity_sensor.reading() < 42.5 THEN power_on() humidifier_unit,
输入 Atom_INT: IF XXX < 42.5 THEN 升高 湿度
输出重写意图: IF 湿度 < 42.5 THEN 升高 湿度

# 案例
输入 TAP rule: IF hallway_co2_monitor.reading() > 900 THEN power_on() air_purifier_x1,
输入 Atom_INT: IF CO2浓度 > 900 THEN 降低 CO2浓度
输出重写意图: IF CO2浓度 > 900 THEN 降低 CO2浓度

# 限制
1. 重写后的意图必须是 IF ... THEN ... 格式。
2. 重写后的意图必须是能被输入的TAP规则满足的。
3. 只需要返回重写后的意图，不需要返回其他内容。
4. 意图中THEN之后不允许出现AND，即只能有一个动作。
5. 优先保留“环境属性变化”的功能意图，不要把它重写成具体设备动作意图。例如不能把“IF CO2浓度 > 900 THEN 降低 CO2浓度”改写成“IF CO2浓度 > 900 THEN power_on() air_purifier_x1”。
6. 如果原意图中出现 XXX 这类占位符，应结合 TAP 规则恢复为具体环境属性，例如湿度传感器的 reading() 低于阈值通常对应“湿度”。
        """

    def _log_summary(self, message: str) -> None:
        self.run_logger.info(message)
        self.debug_logger.info(message)

    def _log_debug(self, message: str) -> None:
        self.debug_logger.debug(message)

    def _log_error(self, message: str) -> None:
        self.run_logger.error(message)
        self.debug_logger.error(message)


    def _begin_run(self, tap_rule: str) -> None:
        self._run_counter += 1
        self.trace_tree = get_shared_trace_tree()
        self._current_run = {
            "tap_rule": tap_rule,
            "max_rewrite_count": 3
        }
        self.bus.add_user_input_rules([tap_rule])

    @staticmethod
    def _normalize_text(text: object) -> str:
        return " ".join(str(text or "").strip().split())

    def tap_function_abstraction(self, tap_rule: str, debug: bool = False) -> (object, list[str], str, list[str]):
        """
        第一步：TAP功能抽象
        输入字符串类型的TAP规则，输出对象SYS、字符串数组specfic_devices、字符串IRP和字符串数组device_types
        """
        if debug:
            self._log_debug("--- Step 1: TAP 功能抽象 ---")
            self._log_debug(f"输入 TAP rule: {tap_rule}")
        
        
        result = extract_tap_rule(tap_rule)
        SR = result.get("SR", None)
        specific_devices = result.get("specific_devices", [])
        if debug:
            pass
            # print(f"输出 SR: {SR}")

        SYS, IRP, device_types = SR2CG(SR)
        if len(IRP) == 1: # TODO 一个补丁，现在让每个TAP都单独进行抽象
            IRP = IRP[0]
        
        if debug:
            self._log_debug(f"输出 转化前IRP: {IRP}")

        IRP = convert_specific_devices_to_function_types(IRP)
        
        if debug:
            # print(f"输出 SYS: {SYS}")
            self._log_debug(f"输出 specific_devices: {specific_devices}")
            self._log_debug(f"输出 转化后IRP: {IRP}")
            self._log_debug(f"输出 device_types: {device_types}")

        return SYS, specific_devices, IRP, device_types

    def intent_recognition(self, SYS: object, debug: bool = False) -> Dict[str, Any]:
        """
        第二步：意图识别
        输入对象SYS，输出字符串Atom_INT
        """
        if debug:
            self._log_debug("--- Step 2: 意图识别 ---")
            self._log_debug(f"输入 SYS: {SYS}")
            
        Atom_INT = generate_atom_int_rule(SYS)
        if debug:
            self._log_debug(f"输出 Atom_INT: {Atom_INT}")

        return Atom_INT

    def reflection(self, tap_rule: str, Atom_INT: str, debug: bool = False)-> bool:
        """
        第三步：反思
        输入字符串Atom_INT，输出布尔值，True表示TAP规则能满足该功能意图，False表示不能。
        """
        if debug:
            self._log_debug("--- Step 3: 抽象与识别反思 ---")
            self._log_debug(f"输入 TAP rule: {tap_rule}")
            self._log_debug(f"输入 Atom_INT: {Atom_INT}")
        
        # 使用 AIClient 的简单完成接口获取字符串结果
        with self.ai_client.usage_scope("reflection", scope_type="step"):
            reflection_output = self.ai_client.simple_completion(
                messages_input=[
                    create_system_message(self.reflection_prompt),
                    create_user_message(f"这是用户输入：\nTAP规则: {tap_rule}\n功能意图: {Atom_INT}")
                ]
            ).strip()
        
        if debug:
            self._log_debug(f"输出: {reflection_output}")

        if "是" in reflection_output:
            return True
        elif "否" in reflection_output:
            return False
        else:
            raise ValueError(f"未知的反思输出: {reflection_output}")
        # 当前没有返回值

    def rewrite_intent(self, tap_rule: str, Atom_INT: str, debug: bool = False) -> str:
        """
        第四步：重写意图
        输入字符串Atom_INT，输出字符串，为重写后的功能意图。
        """
        if debug:
            self._log_debug("--- Step 4: 重写意图 ---")
            self._log_debug(f"输入 TAP rule: {tap_rule}, 输入 Atom_INT: {Atom_INT}")
        
        # 使用 AIClient 的简单完成接口获取字符串结果
        with self.ai_client.usage_scope("rewrite_intent", scope_type="step"):
            rewrite_output = self.ai_client.simple_completion(
                messages_input=[
                    create_system_message(self.rewrite_prompt),
                    create_user_message(f"这是用户输入：\n功能意图: {Atom_INT}, TAP规则: {tap_rule}")
                ]
            ).strip()
        
        if debug:
            self._log_debug(f"输出重写意图: {rewrite_output}")

        return rewrite_output

    def _process_single_tap_for_batch(
        self,
        tap_rule: str,
        debug: bool = False,
        scope_snapshot: Optional[List[Dict[str, Any]]] = None,
        overhead_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        批处理场景下单条 TAP 的纯计算阶段。
        不触碰共享 trace_tree / bus，仅返回可串行合并的结果。
        """
        with self.ai_client.inherit_scope_stack(scope_snapshot), get_overhead_recorder().inherit_context(overhead_snapshot):
            max_rewrite_count = 3
            SYS, specific_devices, IRP, device_types = self.tap_function_abstraction(tap_rule, debug)
            sys_serializable = SYS.to_dict() if hasattr(SYS, "to_dict") else SYS
            Atom_INT = self.intent_recognition(SYS, debug)

            is_satisfied = self.reflection(tap_rule, Atom_INT, debug)
            rewrite_count = 0
            while not is_satisfied and rewrite_count < max_rewrite_count:
                Atom_INT = self.rewrite_intent(tap_rule, Atom_INT, debug)
                rewrite_count += 1
                is_satisfied = self.reflection(tap_rule, Atom_INT, debug)
                if is_satisfied:
                    break

            return {
                "tap_rule": tap_rule,
                "IRP": IRP,
                "Atom_INT": Atom_INT,
                "device_types": device_types,
                "specific_devices": specific_devices,
                "step1_output": {
                    "SYS": sys_serializable,
                    "specific_devices": specific_devices,
                    "IRP": IRP,
                },
                "step2_output": {
                    "device_types": device_types,
                    "Atom_INT": Atom_INT,
                },
                "step3_output": {
                    "reflection_output": is_satisfied
                }
            }

    def run(self, tap_rule: str, debug: bool = False, test_flag: bool = False) -> Dict[str, Any]:
        """
        运行Agent，按顺序执行三个步骤
        
        Args:
            tap_rule (str): 用户输入的TAP规则
            debug (bool): 是否启用调试模式
            test_flag (bool): 是否为测试模式，如果是，则不发布消息到总线
            
        Returns:
            Dict[str, Any]: 最终的处理结果
        """
        emit_agent_status("IntentReader", "started", "意图识别Agent开始处理单条TAP", {"mode": "single"})
        self._log_summary("意图识别Agent: 开始处理单条 TAP")
        if debug:
            self._log_debug(f"接收到 TAP rule: {tap_rule}")

        with self.ai_client.usage_scope("IntentReader", scope_type="agent"), get_overhead_recorder().agent_run("IntentReader"):
            try:
                self._begin_run(tap_rule)
                
                # 1. TAP功能抽象
                SYS, specific_devices, IRP, device_types = self.tap_function_abstraction(tap_rule, debug)
                 # 将 SYS 转为可 JSON 序列化的结构（如果对象支持 to_dict）
                sys_serializable = SYS.to_dict() if hasattr(SYS, "to_dict") else SYS

                # 2. 意图识别
                Atom_INT = self.intent_recognition(SYS, debug)

                # 3. 意图反思
                is_satisfied = self.reflection(tap_rule, Atom_INT, debug)
                
                rewrite_count = 0
                while not is_satisfied and rewrite_count < self._current_run["max_rewrite_count"]:
                    # 4. 重写意图
                    Atom_INT = self.rewrite_intent(tap_rule, Atom_INT, debug)
                    rewrite_count += 1
                    is_satisfied = self.reflection(tap_rule, Atom_INT, debug)
                    if is_satisfied:
                        break
                    
                # 整合最终结果
                final_result = {
                    "step1_output": {
                        "SYS": sys_serializable,
                        "specific_devices": specific_devices,
                        "IRP": IRP,
                    },
                    "step2_output": {
                        "device_types": device_types,
                        "Atom_INT": Atom_INT,
                    },
                    "step3_output": {
                        "reflection_output": is_satisfied
                    }
                }

                self.trace_tree.attach_inference_result(Atom_INT, IRP, tap_rule)
                if not test_flag:
                    self.bus.publish("trace_tree.add_new_intent", {"trace_tree": self.trace_tree.to_dict(), "source": "IntentReader"})

                final_result["trace_tree"] = self.trace_tree.to_dict()
                emit_agent_status(
                    "IntentReader",
                    "finished",
                    "意图识别Agent单条TAP处理完成",
                    {"mode": "single", "node_count": len((final_result["trace_tree"] or {}).get("nodes", []))},
                )
                self._log_summary("意图识别Agent: 单条 TAP 处理完成")
                return final_result

            except Exception as e:
                emit_agent_status(
                    "IntentReader",
                    "failed",
                    "意图识别Agent单条TAP处理失败",
                    {"mode": "single", "error": str(e)},
                    level="error",
                )
                self.debug_logger.exception("意图识别Agent: 单条 TAP 处理失败")
                raise e

    def run_batch(self, tap_rules: List[str], debug: bool = False, test_flag: bool = False) -> List[Dict[str, Any]]:
        with self.ai_client.usage_scope("IntentReader", scope_type="agent"), get_overhead_recorder().agent_run("IntentReader"):
            emit_agent_status(
                "IntentReader",
                "started",
                "意图识别Agent开始批处理",
                {"mode": "batch", "tap_count": len(tap_rules)},
            )
            self._log_summary(f"意图识别Agent: 开始批处理，共 {len(tap_rules)} 条 TAP")
            self.bus.current_TAPs = list(tap_rules)
            self.trace_tree = get_shared_trace_tree()
            self.bus.add_user_input_rules(tap_rules)
            emit_agent_status("IntentReader", "progress", "执行 TAP 批处理解析", {"step": "tap_batch_processing", "tap_count": len(tap_rules)})
            emit_agent_status("IntentReader", "progress", "执行意图提取", {"step": "atom_int_extraction", "tap_count": len(tap_rules)})

            indexed_results: List[Optional[Dict[str, Any]]] = [None] * len(tap_rules)
            scope_snapshot = self.ai_client.get_scope_stack_snapshot()
            overhead_snapshot = get_overhead_recorder().snapshot_context()
            max_workers = min(4, len(tap_rules)) if tap_rules else 0

            if max_workers <= 1:
                for idx, tap_rule in enumerate(tap_rules):
                    try:
                        if debug:
                            self._log_debug(f"批处理接收到 TAP rule: {tap_rule}")
                        indexed_results[idx] = self._process_single_tap_for_batch(
                            tap_rule,
                            debug=debug,
                            scope_snapshot=scope_snapshot,
                            overhead_snapshot=overhead_snapshot,
                        )
                    except Exception as e:
                        self._log_error(f"意图识别Agent: 处理 TAP rule 失败: {tap_rule}")
                        self.debug_logger.exception("意图识别Agent: 批处理单条 TAP 失败")
                        indexed_results[idx] = {
                            "tap_rule": tap_rule,
                            "error": str(e)
                        }
            else:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="IntentReaderBatch") as executor:
                    future_to_meta = {}
                    for idx, tap_rule in enumerate(tap_rules):
                        if debug:
                            self._log_debug(f"批处理接收到 TAP rule: {tap_rule}")
                        future = executor.submit(
                            self._process_single_tap_for_batch,
                            tap_rule,
                            debug,
                            scope_snapshot,
                            overhead_snapshot,
                        )
                        future_to_meta[future] = (idx, tap_rule)

                    for future in as_completed(future_to_meta):
                        idx, tap_rule = future_to_meta[future]
                        try:
                            indexed_results[idx] = future.result()
                        except Exception as e:
                            self._log_error(f"意图识别Agent: 处理 TAP rule 失败: {tap_rule}")
                            self.debug_logger.exception("意图识别Agent: 并行批处理单条 TAP 失败")
                            indexed_results[idx] = {
                                "tap_rule": tap_rule,
                                "error": str(e)
                            }

            results: List[Dict[str, Any]] = []
            for item in indexed_results:
                if item is None:
                    continue

                if "error" not in item:
                    self.trace_tree.attach_inference_result(item["Atom_INT"], item["IRP"], item["tap_rule"])
                    item["trace_tree"] = self.trace_tree.to_dict()

                results.append(item)

                if not test_flag:
                    self.bus.publish("trace_tree.add_new_intent", {"trace_tree": self.trace_tree.to_dict(), "source": "IntentReader"})
            
            # 4. 意图抽象
            try:
                emit_agent_status("IntentReader", "progress", "执行意图抽象阶段", {"step": "intent_abstraction"})
                # 1. 获取所有原子意图（直接挂在功能意图下的，或者所有是原子意图定义的）
                # 这里我们定义原子意图为：类型为INT，且拥有IRP类型子节点的节点
                all_int_nodes = self.trace_tree.get_int_nodes()
                atom_int_candidates = []

                def _get_int_type_ancestor(node_id: str) -> Optional[str]:
                    cur = node_id
                    while True:
                        parent = self.trace_tree.get_parent(cur)
                        if parent is None:
                            return None
                        parent_node = self.trace_tree.nodes.get(parent)
                        if parent_node is None:
                            return None
                        if parent_node.kind == NodeKind.INT_TYPE:
                            return parent
                        cur = parent

                for nid in all_int_nodes:
                    int_type = _get_int_type_ancestor(nid)
                    if int_type != "功能意图":
                        continue
                    # 检查是否有 IRP 子节点
                    children = self.trace_tree.get_children(nid)
                    is_atom = False
                    for cid in children:
                        if self.trace_tree.nodes[cid].kind == NodeKind.IRP:
                            is_atom = True
                            break
                    if is_atom:
                        atom_int_candidates.append(nid)
                
                if atom_int_candidates:
                    if debug:
                        self._log_summary(f"意图识别Agent: 开始意图抽象，原子意图数量 {len(atom_int_candidates)}")
                    
                    abstraction_res = abstract_atom_ints(atom_int_candidates, debug=debug)
                    
                    high_level_intents = abstraction_res.get("high_level_intents", [])
                    abstraction_map = abstraction_res.get("abstraction_map", {})
                    
                    self.trace_tree.ensure_int_type_node('功能意图')
                    
                    for hi_intent in high_level_intents:
                        hi_desc = hi_intent.get("description")
                        hi_id = hi_intent.get("id")
                        
                        if not hi_desc:
                            continue
                            
                        # 确保高层意图节点存在
                        # 如果不存在则创建，默认挂在“功能意图”下
                        if not self.trace_tree.has_node(hi_desc):
                            try:
                                self.trace_tree.add_child('功能意图', hi_desc, NodeKind.INT, data={"isHighLevel": True, "isNew": True})
                            except ValueError:
                                # 如果添加失败（例如已存在但不在当前父节点下，或者名字冲突），尝试获取已存在的
                                pass
                        
                        # 移动原子意图到高层意图下
                        mapped_atoms = abstraction_map.get(hi_id, [])
                        for atom_id in mapped_atoms:
                            if not self.trace_tree.has_node(atom_id):
                                continue
                                
                            current_parent = self.trace_tree.get_parent(atom_id)
                            
                            # 如果已经是该高层意图的子节点，跳过
                            if current_parent == hi_desc:
                                continue
                                
                            # 执行移动操作
                            # 1. 从旧父节点移除
                            if current_parent:
                                self.trace_tree.nodes[current_parent].children.discard(atom_id)
                            
                            # 2. 绑定新父节点
                            self.trace_tree.nodes[atom_id].parent = hi_desc
                            self.trace_tree.nodes[hi_desc].children.add(atom_id)
                            try:
                                self.trace_tree.update_node_data(atom_id, {"isNew": False})
                            except Exception:
                                pass
                    
                    if debug:
                        self._log_summary("意图识别Agent: 意图抽象完成并更新 Trace Tree")
                        self.trace_tree.print_tree()
                    
                    # 发布更新事件
                    if not test_flag:
                        self.bus.publish("trace_tree.abstract_intent", {"trace_tree": self.trace_tree.to_dict(), "source": "IntentReader"})

            except Exception as e:
                self._log_error(f"意图识别Agent: 意图抽象阶段发生错误: {e}")
                self.debug_logger.exception("意图识别Agent: 意图抽象阶段异常")

            emit_agent_status(
                "IntentReader",
                "progress",
                "执行意图可满足性检查",
                {
                    "step": "intent_satisfaction_check",
                    "satisfied_count": sum(
                        1
                        for item in results
                        if bool(((item.get("step3_output") or {}).get("reflection_output")))
                    ),
                    "result_count": len(results),
                },
            )

            emit_agent_status(
                "IntentReader",
                "finished",
                "意图识别Agent批处理完成",
                {"mode": "batch", "tap_count": len(tap_rules), "result_count": len(results)},
            )
            self._log_summary("意图识别Agent: 批处理完成")
            return results
