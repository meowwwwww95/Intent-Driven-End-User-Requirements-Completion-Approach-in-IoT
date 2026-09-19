"""
质量规划Agent
"""
import logging
import sys
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.ai_client import get_default_ai_client, create_system_message, create_user_message
from config.logging_config import CASE_DEBUG_LOGGER_NAME, CASE_RUN_LOGGER_NAME
from config.overhead_recorder import get_overhead_recorder
from model.trace_tree_bus import get_shared_trace_tree, get_shared_trace_bus, get_available_devices
from config.quality_template_config import get_quality_template_spec, print_quality_template
from tool.QT_search import prune_quality_template
from tool.IRP2TAP import generate_tap
from webui.runtime_events import emit_agent_status


# TODO 这里的可满足性检查，或许应该检查整棵树，因为会存在AND关系，同理，实现规划师那里也可能出现这个问题
# TODO “所有影音、照明、温控及厨电类电器”还要做一步模糊匹配，不然很多目标电器出不来

class QualityPlannerAgent:
    """封装质量规划流程与追溯树交互的逻辑。"""
    def __init__(self):
        """初始化质量规划器实例与当前运行状态。"""
        self.ai_client = get_default_ai_client()
        self._run_counter = 0
        self._current_run: Dict[str, str] = {}
        self.bus = get_shared_trace_bus()
        self.trace_tree = None
        self.run_logger = logging.getLogger(f"{CASE_RUN_LOGGER_NAME}.QualityPlanner")
        self.debug_logger = logging.getLogger(f"{CASE_DEBUG_LOGGER_NAME}.QualityPlanner")

    def _log_summary(self, message: str) -> None:
        self.run_logger.info(message)
        self.debug_logger.info(message)

    def _log_debug(self, message: str) -> None:
        self.debug_logger.debug(message)

    def _find_path_by_id(self, spec: Dict[str, Any], target_id: str) -> List[Dict[str, Any]]:
        """在质量模板树中查找目标 id 的路径（自根到该节点）。未找到时返回空列表。"""
        path: List[Dict[str, Any]] = []
        found = False
        def dfs(n: Dict[str, Any]) -> bool:
            nonlocal found
            if found:
                return True
            path.append(n)
            if n.get("id") == target_id:
                found = True
                return True
            for c in n.get("children", []) or []:
                if dfs(c):
                    return True
            path.pop()
            return False
        dfs(spec)
        return path if found else []

    def _begin_run(self, input_quality_template: object = None) -> None:
        """初始化一次运行的上下文：刷新追溯树、设置质量模板与可用设备。"""
        self._run_counter += 1
        self.trace_tree = get_shared_trace_tree() # 更新需求追踪树观察结果
        
        if not input_quality_template:
            input_quality_template = get_quality_template_spec()

        self._current_run = {
            "quality_template": input_quality_template,
            "available_devices": self.bus.get_available_devices(),
        }

    @staticmethod
    def _get_parallel_workers(item_count: int) -> int:
        if item_count <= 0:
            return 0
        return min(4, item_count)
    

    def quality_template_prune(self, quality_template: object, available_devices: [], debug: bool = False):
        """根据可用设备裁剪质量模板，返回可实现的质量模板 spec。"""
        spec = quality_template if isinstance(quality_template, dict) and quality_template else get_quality_template_spec()
        devices = available_devices
        if hasattr(available_devices, "get_available_device_names") and hasattr(available_devices, "get_abstract_devices"):
            devices = {
                "device_names": available_devices.get_available_device_names(),
                "abstract_devices": available_devices.get_abstract_devices(),
            }
        elif not isinstance(available_devices, (list, dict)):
            devices = [available_devices] if available_devices else []
        pruned = prune_quality_template(devices, template_spec=spec)
        return pruned.get("spec")

    def _prepare_quality_template(self, debug: bool = False):
        self._log_summary("--- Step 1: 质量模板检索 ---")

        quality_template = self._current_run.get('quality_template')
        device_profile = self._current_run.get('available_devices')
        available_devices_names = device_profile.get_available_device_names()

        spec_for_prune = quality_template if isinstance(quality_template, dict) else get_quality_template_spec()
        devices_list = available_devices_names if isinstance(available_devices_names, list) else ([available_devices_names] if available_devices_names else [])
        abstract_devices = device_profile.get_abstract_devices() if hasattr(device_profile, "get_abstract_devices") else []
        prune_input = {
            "device_names": devices_list,
            "abstract_devices": abstract_devices,
        }
        
        if debug:
            self._log_debug(f"输入质量模板: {json.dumps(spec_for_prune, ensure_ascii=False, indent=2)}")
            self._log_debug(f"输入设备名称: {devices_list}")
            self._log_debug(f"输入抽象设备类型: {[d.get('type') for d in abstract_devices if isinstance(d, dict) and d.get('type')]}")

        pruned_detail = prune_quality_template(prune_input, template_spec=spec_for_prune)
        realizable_quality_template = pruned_detail.get("spec")
        self._current_run["quality_template"] = realizable_quality_template
        if debug:
            self._log_debug(f"移除的IRP: {pruned_detail.get('removed_irp')}")
            self._log_debug(f"移除的INT: {pruned_detail.get('removed_int')}")
            print_quality_template(realizable_quality_template)
        return realizable_quality_template, pruned_detail

    def TAP_generation(self, realizable_quality_template: object, debug: bool = False):
        """遍历 IRP 节点生成 TAP 规则，返回 IRP 到 TAP 的映射。"""

        self._log_summary("--- Step 2: 实现逻辑生成 ---")

        spec = realizable_quality_template if isinstance(realizable_quality_template, dict) and realizable_quality_template else get_quality_template_spec()
        irp_ids: List[str] = []
        def walk(n: Dict[str, Any]):
            t = n.get("type")
            if t == "IRP":
                s = n.get("id")
                if isinstance(s, str) and s.strip():
                    irp_ids.append(s)
            for c in n.get("children", []) or []:
                walk(c)
        walk(spec)
        source_map: Dict[str, List[str]] = {}
        available_names: List[str] = []
        devices_from_bus = get_available_devices()
        if hasattr(devices_from_bus, "get_available_device_names"):
            try:
                available_names = devices_from_bus.get_available_device_names() or []
            except Exception:
                available_names = []
        elif isinstance(self._current_run.get("available_devices"), list):
            available_names = [x for x in self._current_run.get("available_devices") if isinstance(x, str)]
        scope_snapshot = self.ai_client.get_scope_stack_snapshot()
        overhead_snapshot = get_overhead_recorder().snapshot_context()
        indexed_results: List[Optional[tuple[str, List[str]]]] = [None] * len(irp_ids)
        max_workers = self._get_parallel_workers(len(irp_ids))

        if max_workers <= 1:
            for idx, irp in enumerate(irp_ids):
                indexed_results[idx] = self._generate_taps_for_irp(
                    irp,
                    available_names,
                    debug=debug,
                    scope_snapshot=scope_snapshot,
                    overhead_snapshot=overhead_snapshot,
                )
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="QualityPlannerTap") as executor:
                future_to_idx = {
                    executor.submit(
                        self._generate_taps_for_irp,
                        irp,
                        available_names,
                        debug,
                        scope_snapshot,
                        overhead_snapshot,
                    ): idx
                    for idx, irp in enumerate(irp_ids)
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    indexed_results[idx] = future.result()

        for item in indexed_results:
            if item is None:
                continue
            irp, uniq = item
            source_map[irp] = uniq
        if debug:
            self._log_debug(json.dumps(source_map, ensure_ascii=False, indent=2))

        return source_map

    def _generate_taps_for_irp(
        self,
        irp: str,
        available_names: List[str],
        debug: bool = False,
        scope_snapshot: Optional[List[Dict[str, Any]]] = None,
        overhead_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, List[str]]:
        with self.ai_client.inherit_scope_stack(scope_snapshot), get_overhead_recorder().inherit_context(overhead_snapshot):
            taps = generate_tap(irp, available_names, debug=debug)
            uniq: List[str] = []
            seen: set[str] = set()
            for tap in taps:
                if tap not in seen:
                    uniq.append(tap)
                    seen.add(tap)
            return irp, uniq

    def _extract_rule_references(self, rule_text: str) -> List[Dict[str, str]]:
        """从 IRP/TAP 规则文本中提取设备与能力引用。"""
        text = (rule_text or "").strip()
        if not text:
            return []

        refs: List[Dict[str, str]] = []
        match = re.search(r"^\s*IF\s+(?P<cond>.*?)\s+THEN\s+(?P<act>.*?)\s*$", text, re.IGNORECASE)
        if not match:
            return refs

        cond_text = match.group("cond")
        act_text = match.group("act").strip()

        for part in re.split(r"\s+AND\s+", cond_text, flags=re.IGNORECASE):
            ref_match = re.search(r"([^\.\s]+)\.([^\s]+)", part.strip())
            if not ref_match:
                continue
            refs.append(
                {
                    "kind": "trigger",
                    "device": ref_match.group(1).strip(),
                    "capability": ref_match.group(2).strip(),
                }
            )

        action_parts = act_text.split()
        if len(action_parts) >= 2:
            refs.append(
                {
                    "kind": "action",
                    "device": " ".join(action_parts[1:]).strip(),
                    "capability": action_parts[0].strip(),
                }
            )
        return refs

    def _build_device_context_for_satisfaction(self, chain: Dict[str, Any]) -> Dict[str, Any]:
        """检索质量链中涉及的抽象/具体设备与能力信息，供满足性检查使用。"""
        irp_text = (chain.get("irp") or "").strip()
        taps = chain.get("tap") or []
        tap_rules = taps if isinstance(taps, list) else ([str(taps)] if taps else [])

        irp_refs = self._extract_rule_references(irp_text)
        tap_refs: List[Dict[str, str]] = []
        for tap_rule in tap_rules:
            if isinstance(tap_rule, str) and tap_rule.strip():
                tap_refs.extend(self._extract_rule_references(tap_rule))

        available_profile = self._current_run.get("available_devices") or get_available_devices()
        concrete_usage: Dict[str, Dict[str, Any]] = {}
        for ref in tap_refs:
            device_name = (ref.get("device") or "").strip()
            capability_name = (ref.get("capability") or "").strip()
            if not device_name:
                continue
            entry = concrete_usage.setdefault(
                device_name,
                {
                    "name": device_name,
                    "used_capabilities": set(),
                    "used_as": set(),
                }
            )
            if capability_name:
                entry["used_capabilities"].add(capability_name)
            kind = (ref.get("kind") or "").strip()
            if kind:
                entry["used_as"].add(kind)

        concrete_device_context: List[Dict[str, Any]] = []
        for device_name in sorted(concrete_usage.keys()):
            usage = concrete_usage[device_name]
            profile = {}
            if hasattr(available_profile, "get_device_profile_context"):
                try:
                    profile = available_profile.get_device_profile_context(device_name) or {}
                except Exception:
                    profile = {}
            capabilities = profile.get("capabilities") if isinstance(profile, dict) else []
            if not isinstance(capabilities, list):
                capabilities = []
            used_caps = usage.get("used_capabilities") or set()
            matched_capabilities = []
            for cap in capabilities:
                if not isinstance(cap, dict):
                    continue
                cap_name = (cap.get("name") or "").strip()
                if used_caps and cap_name not in used_caps:
                    continue
                matched_capabilities.append(
                    {
                        "name": cap_name,
                        "description": cap.get("description", "") if isinstance(cap.get("description"), str) else "",
                        "capabilityType": cap.get("capabilityType", "") if isinstance(cap.get("capabilityType"), str) else "",
                    }
                )
            concrete_device_context.append(
                {
                    "name": device_name,
                    "type": profile.get("type", "") if isinstance(profile, dict) else "",
                    "used_as": sorted(list(usage.get("used_as") or [])),
                    "used_capability_names": sorted(list(used_caps)),
                    "matched_capabilities": matched_capabilities,
                }
            )

        return {
            "irp_rule_refs": irp_refs,
            "tap_rule_refs": tap_refs,
            "concrete_device_context": concrete_device_context,
        }

    def _satisfaction_check_one_chain(self, chain: Dict[str, Any], debug: bool = False) -> Dict[str, Any]:
        satisfaction_prompt = f"""
# 任务
你是一个质量模板满足性检查助手。你的任务是根据输入的质量意图链条（INT语句路径）、抽象的IRP规则、具体的TAP规则，以及规则中涉及的设备与能力画像，判断这些TAP是否能满足该质量意图。

# 示例1
输入IRP: IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 所有温控电器
输入TAP: IF motion_detector_a.reading() == 人离开 THEN turn_off() living_room_ac

输出: 是
理由: 空调属于温控类电器，那么关闭空调就是关闭目标电器集合的一种形式，因此输出“是”。这里并不需要考虑是否已经覆盖全部目标类别，因为并不考虑充分性。

# 示例2
输入IRP: IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 所有温控电器
输入TAP: IF motion_detector_a.motion_state() == false THEN turn_off() living_room_ac

输出: 是
理由: motion_detector_a也可以检测到人离开，可在此视作一种人体感知设备，因此输出“是”。

# 示例3
输入IRP: IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 所有温控电器
输入TAP: 
- IF motion_detector_a.motion_state() == true THEN turn_off() living_room_ac
- IF smoke_detector_b.reading() > 0.05 THEN turn_off() living_room_ac
输出: 否
理由: smoke_detector_b是烟雾传感器，并不是人体感知设备。虽然motion_detector_a也可以检测到人离开，可在此视作一种人体感知设备，但仍然需要输出“否”。

# 示例4
输入IRP: IF 运动感知设备.获取读数() == false THEN 关闭() 照明电器
输入TAP: IF motion_detector_a.motion_state() == false THEN light_off() smart_bulb_pro
补充设备画像:
- motion_detector_a: type=motion_sensor, used_capability_names=["motion_state()"], matched_capabilities 中说明“检测是否有人经过，如果有人则返回true，无人则返回false”
- smart_bulb_pro: type=brightness_sensor, used_capability_names=["light_off()"], matched_capabilities 中说明“关闭灯”

输出: 是
理由: 虽然smart_bulb_pro的设备type名字里包含brightness_sensor，但它当前在TAP里被用作action设备，且使用的能力是light_off()，能力描述明确是“关闭灯”，因此它在这条规则中应视作照明设备。motion_detector_a的motion_state()==false也可视作“人离开/无人”的一种实现，因此该TAP满足“关闭照明电器”的质量意图。

# 判断原则
1) 人体感知设备判断优先看是否与人体活动相关（如运动感知设备/人在感知设备/人员感知设备等），与烟雾、温度等环境感知无关的才可视为人体感知设备。
2) 如果给出了规则涉及的设备与能力画像，应优先利用其中的used_capability_names、matched_capabilities、capability description 做判断，而不是只看设备名表面语义。
3) 优先关注设备能力，而不是仅关注设备名，有些设备名字长得像传感器，但实际也有控制能力。
4) 对动作设备的类别判断，优先看“当前被使用的能力”及其description，而不是设备type字面值。只要某设备当前使用的action能力及其描述明确指向某类电器的操作，即使它的type字面值像另一类设备，也应按当前能力将其视作对应类别的设备。
5) 只要TAP中的具体动作设备属于IRP要求关闭/打开/控制的目标设备类别之一，就应判定为满足，不要求覆盖该类别中的全部设备。
6) 当IRP中的抽象设备属于某类电器（如照明设备/照明电器），而TAP动作设备当前使用的action能力及其description明确对应该类电器的操作时，应优先判定为匹配该类别语义。
7) 对触发条件的判断也优先看能力语义。若设备能力说明表明它检测是否有人移动、是否有人存在、是否无人等，那么即使具体值表达为true/false，也可以视作“人在/人离开/无人”的实现。

# 限制
1. 只需要返回"是"或"否"，不需要返回其他内容。
        """

        irp = chain.get("irp") or ""
        path = chain.get("path") or []
        taps = chain.get("tap") or []
        intents = [n.get("id") for n in path if n.get("type") in ["INT", "INT_TYPE"]]
        intent_text = ";".join([x for x in intents if isinstance(x, str)])
        irp_text = irp or ""
        tap_text = "\n".join(taps) if isinstance(taps, list) else str(taps)
        device_context = self._build_device_context_for_satisfaction(chain)

        if debug:
            self._log_debug("满足性检查设备上下文:")
            self._log_debug(json.dumps(device_context, ensure_ascii=False, indent=2))

        with self.ai_client.usage_scope("satisfaction_check", scope_type="step"):
            output = self.ai_client.simple_completion([
                create_system_message(satisfaction_prompt),
                create_user_message(
                    "这是用户输入：\n"
                    f"质量意图链条: {intent_text}\n"
                    f"抽象的规划方案(IRP): {irp_text}\n"
                    f"具体的TAP规则: {tap_text}\n"
                    f"规则涉及的设备与能力信息: {json.dumps(device_context, ensure_ascii=False, indent=2)}"
                )
            ]).strip()

        ok = ("是" in output)
        if not ok and ("否" not in output) and (not isinstance(taps, list) or len(taps) == 0):
            ok = False
            output = "否"

        return {"chain": chain, "satisfied": ok, "model_output": output}

    def satisfaction_check(self, forward_quality_chain: object, debug: bool = False) -> Dict[str, Any]:
        if not isinstance(forward_quality_chain, dict):
            return {"chain": forward_quality_chain, "satisfied": False, "model_output": "否"}
        return self._satisfaction_check_one_chain(forward_quality_chain, debug=debug)

    def constraint_attach(self, debug: bool = False):
        """识别追溯树中的约束 INT 节点并匹配到应约束的 IRP 节点，写回约束对象。"""
        constraint_nodes: List[str] = []
        for nid in self.trace_tree.get_int_nodes():
            try:
                node = self.trace_tree.get_node(nid)
            except Exception:
                continue
            data = node.get("data") or {}
            if data.get("isConstraint") is True:
                constraint_nodes.append(nid)
        irp_nodes = self.trace_tree.get_irp_nodes()
        result_map: Dict[str, List[str]] = {}
        for cid in constraint_nodes:
            try:
                cnode = self.trace_tree.get_node(cid)
            except Exception:
                continue
            ctext = cnode.get("id") or ""
            current_list = list[Any](cnode.get("data", {}).get("constraintObject") or [])
            updated_set = set[Any](current_list)
            for irp_id in irp_nodes:
                try:
                    inode = self.trace_tree.get_node(irp_id)
                except Exception:
                    continue
                irp_text = inode.get("data", {}).get("IRP") or inode.get("id") or ""
                prompt = """
# 任务
你是一个质量约束匹配助手。你的任务是判断给定的质量约束是否应当约束一条意图实现规划(IRP)。

# 限制
1. 如果该约束应当适用于这条IRP，返回"是"；否则返回"否"。
2. 只需要返回"是"或"否"，不需要返回其他内容。
                """
                with self.ai_client.usage_scope("constraint_attach", scope_type="step"):
                    output = self.ai_client.simple_completion([
                        create_system_message(prompt),
                        create_user_message(f"这是用户输入：\n质量约束: {ctext}\nIRP规则: {irp_text}")
                    ]).strip()
                if debug:
                    self._log_debug(f"约束 [{ctext}] 对 IRP [{irp_id}] 的判断结果: {output}")
                if "是" in output:
                    updated_set.add(irp_id)
            updated_list = list[Any](updated_set)
            self.trace_tree.update_node_data(cid, {"constraintObject": updated_list})
            result_map[cid] = updated_list
        if debug:
            self._log_debug(json.dumps({"constraint_objects": result_map}, ensure_ascii=False, indent=2))
        return {"constraint_objects": result_map}

    def _attach_taps_to_irp_nodes(self, spec: Dict[str, Any], tap_map: Dict[str, List[str]]) -> Dict[str, Any]:
        """将 TAP 规则作为子节点附加到 IRP 节点，返回更新后的质量模板副本。"""
        def deep_copy(x: Dict[str, Any]) -> Dict[str, Any]:
            return json.loads(json.dumps(x, ensure_ascii=False))
        def ensure_unique_id(base_id: str, used_ids: set[str]) -> str:
            if base_id not in used_ids:
                return base_id
            idx = 1
            nid = f"{base_id} (副本{idx})"
            while nid in used_ids:
                idx += 1
                nid = f"{base_id} (副本{idx})"
            return nid
        def collect_ids(n: Dict[str, Any], used_ids: set[str]) -> None:
            nid = n.get("id")
            if isinstance(nid, str) and nid:
                used_ids.add(nid)
            for c in n.get("children", []) or []:
                if isinstance(c, dict):
                    collect_ids(c, used_ids)
        def dfs(n: Dict[str, Any]):
            ch = n.get("children", []) or []
            if n.get("type") == "IRP":
                irp_id = n.get("id")
                taps = tap_map.get(irp_id) or []
                for tap in taps:
                    tap_id = ensure_unique_id(tap, used_ids)
                    if tap_id not in used_ids:
                        ch.append({"id": tap_id, "type": "DSL", "data": {"tap_rule": tap}})
                        used_ids.add(tap_id)
                n["children"] = ch
            for c in n.get("children", []) or []:
                dfs(c)
        copied = deep_copy(spec)
        used_ids: set[str] = set()
        collect_ids(copied, used_ids)
        dfs(copied)
        return copied

    def _build_forward_quality_chains(self, realizable_quality_template, device_scheduling_logic):
        chains: List[Dict[str, Any]] = []
        for irp_id, taps in device_scheduling_logic.items():
            up_path = self._find_path_by_id(realizable_quality_template, irp_id)
            chain = {
                "path": [{"id": n.get("id"), "type": n.get("type")} for n in up_path],
                "irp": irp_id,
                "tap": taps,
            }
            chains.append(chain)
        return chains

    def quality_chain_refinement(self, chain: object, debug: bool = False) -> Dict[str, Any]:
        if debug:
            self._log_debug("--- Step 4: 质量需求精化 ---")
        if not isinstance(chain, dict):
            return {"chain": chain, "satisfied": False, "model_output": "否"}

        taps = chain.get("tap") or []
        if not isinstance(taps, list):
            taps = [str(taps)] if taps else []

        refinement_prompt = """
# 任务
你是一个质量需求精化助手。你的任务是对一条质量链中的 TAP 规则做“一次精化”，使其更贴合抽象 IRP 的语义要求。

# 目标
1) TAP 触发设备/能力应与 IRP 中的抽象触发一致（例如 IRP 出现“人体感知设备”时，TAP 的触发设备应该是人体传感器，而不是烟雾传感器等不相关的设备）。
2) 尽量少改动：优先删除明显不相关的 TAP 规则，其次再做最小替换修正。
3) 输出只包含精化后的 TAP 列表，不要输出解释。

# 注意
1) 能力参数并不要求强制一致，例如IRP中的“人离开”可能在TAP中表达的是“true”这个布尔值。这些具体的能力参数以TAP规则中的结果为主

# 输出格式（严格 JSON）
{"tap": ["...","..."]}

# 示例
输入：{
    "path": ...,
    "irp": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 所有温控电器",
    "tap": [
      "IF motion_detector_a.reading() == 人离开 THEN turn_off() living_room_ac",
      "IF smoke_detector_b.reading() == 人离开 THEN turn_off() living_room_ac",
    ]
}
输出：
{
    "tap": [
      "IF motion_detector_a.reading() == 人离开 THEN turn_off() living_room_ac",
    ]
}
        """

        refined_taps = taps
        try:
            with self.ai_client.usage_scope("quality_chain_refinement", scope_type="step"):
                raw = self.ai_client.simple_completion(
                    [
                        create_system_message(refinement_prompt),
                        create_user_message(f"这是需要精化的质量链：\n{json.dumps(chain, ensure_ascii=False, indent=2)}"),
                    ],
                    use_json=True,
                    temperature=0.0,
                )
            self._log_debug(f"精化后的 TAP 列表: {raw}")
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            cand = (parsed or {}).get("tap")
            if isinstance(cand, list):
                refined_taps = [x for x in cand if isinstance(x, str) and x.strip()]
        except Exception:
            refined_taps = taps

        refined_chain = json.loads(json.dumps(chain, ensure_ascii=False))
        refined_chain["tap"] = refined_taps
        satisfied = self.satisfaction_check(refined_chain, debug=debug)
        satisfied["refined_from"] = {"tap": taps}
        return satisfied

    def _process_quality_chain(
        self,
        chain: Dict[str, Any],
        debug: bool = False,
        scope_snapshot: Optional[List[Dict[str, Any]]] = None,
        overhead_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.ai_client.inherit_scope_stack(scope_snapshot), get_overhead_recorder().inherit_context(overhead_snapshot):
            satisfied = self.satisfaction_check(chain, debug=debug)
            if debug:
                self._log_debug(f"检查链的满足性结果: {json.dumps(satisfied, ensure_ascii=False, indent=2)}")

            if satisfied.get("satisfied", False) is False:
                refined = self.quality_chain_refinement(chain, debug=debug)
                if refined.get("satisfied", False) is False:
                    if debug:
                        self._log_debug(f"精化后链 {json.dumps(refined, ensure_ascii=False, indent=2)} 一次精化后仍不满意，抛弃")
                    return None
                if debug:
                    self._log_debug(f"精化后链 {json.dumps(refined, ensure_ascii=False, indent=2)} 一次精化后满意，加入结果")
                return refined

            return satisfied



    def _run_satisfaction_stage(self, realizable_quality_template, device_scheduling_logic, debug: bool = False):
        chains = self._build_forward_quality_chains(realizable_quality_template, device_scheduling_logic)
        per_chain: List[Dict[str, Any]] = []
        scope_snapshot = self.ai_client.get_scope_stack_snapshot()
        overhead_snapshot = get_overhead_recorder().snapshot_context()
        indexed_results: List[Optional[Dict[str, Any]]] = [None] * len(chains)
        max_workers = self._get_parallel_workers(len(chains))

        if max_workers <= 1:
            for idx, ch in enumerate(chains):
                indexed_results[idx] = self._process_quality_chain(
                    ch,
                    debug=debug,
                    scope_snapshot=scope_snapshot,
                    overhead_snapshot=overhead_snapshot,
                )
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="QualityPlannerChain") as executor:
                future_to_idx = {
                    executor.submit(
                        self._process_quality_chain,
                        ch,
                        debug,
                        scope_snapshot,
                        overhead_snapshot,
                    ): idx
                    for idx, ch in enumerate(chains)
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    indexed_results[idx] = future.result()

        for item in indexed_results:
            if item is not None:
                per_chain.append(item)

        satisfied_irps: set[str] = set()
        for item in per_chain:
            irp_id = ((item or {}).get("chain") or {}).get("irp")
            if isinstance(irp_id, str) and irp_id.strip():
                satisfied_irps.add(irp_id)

        satisfaction_output = {
            "per_chain": per_chain,
            "all_satisfied": all(x.get("satisfied") for x in per_chain) if per_chain else False,
            "satisfied_irps": list(satisfied_irps),
        }
        self._log_summary("--- Step 3: 实现满足性检查 ---")
        if debug:
            self._log_debug(json.dumps(satisfaction_output, ensure_ascii=False, indent=2))
        return satisfaction_output

    def run_plan(self, quality_template: object, debug: bool = False, test_flag: bool = False) -> Dict[str, Any]:
        """执行完整质量规划流程：裁剪、生成、检查、同步追溯树，并返回各步骤的结构化输出。"""
        with self.ai_client.usage_scope("QualityPlanner", scope_type="agent"), get_overhead_recorder().agent_run("QualityPlanner"):
            emit_agent_status("QualityPlanner", "started", "质量规划Agent开始运行规划部分", {"mode": "plan"})
            self._log_summary("质量规划Agent: 开始运行规划部分")

            try:
                self._begin_run(quality_template)

                # 1. 质量模板检索
                emit_agent_status("QualityPlanner", "progress", "执行质量模板检索", {"step": "quality_template_search"})
                realizable_quality_template, pruned_detail = self._prepare_quality_template(debug=debug)

                # 2. 实现逻辑生成
                emit_agent_status("QualityPlanner", "progress", "执行实现逻辑生成", {"step": "tap_generation"})
                device_scheduling_logic = self.TAP_generation(realizable_quality_template, debug=debug)
                
                # 3. 实现满足性检查
                emit_agent_status("QualityPlanner", "progress", "执行实现满足性检查", {"step": "satisfaction_check"})
                satisfaction_output = self._run_satisfaction_stage(realizable_quality_template, device_scheduling_logic, debug=debug)

                final_result = {
                    "step1_output": {
                        "realizable_quality_template": realizable_quality_template,
                        "removed_irp": pruned_detail.get("removed_irp"),
                        "removed_int": pruned_detail.get("removed_int"),
                    },
                    "step2_output": {
                        "device_scheduling_logic": device_scheduling_logic,
                        "forward_quality_chains": satisfaction_output.get("per_chain", []),
                    },
                    "step3_output": {
                        "satisfaction_output": satisfaction_output,
                    }
                }

                successful_irps = set[str](satisfaction_output.get("satisfied_irps") or [])
                refined_logic: Dict[str, List[str]] = {}
                for item in satisfaction_output.get("per_chain", []):
                    if not isinstance(item, dict):
                        continue
                    ch = item.get("chain") or {}
                    irp_id = ch.get("irp")
                    taps = ch.get("tap") or []
                    if not isinstance(irp_id, str) or not irp_id.strip():
                        continue
                    uniq: List[str] = []
                    seen: set[str] = set()
                    for t in taps:
                        if isinstance(t, str) and t.strip() and t not in seen:
                            uniq.append(t)
                            seen.add(t)
                    if uniq:
                        refined_logic[irp_id] = uniq
                filtered_logic = {k: v for k, v in (refined_logic or {}).items() if k in successful_irps}

                if successful_irps:
                    final_spec = self._attach_taps_to_irp_nodes(realizable_quality_template, filtered_logic)
                    self.trace_tree.attach_quality_result(final_spec)
                    realizable_quality_template = final_spec
                
                if debug:
                    self._log_debug("内部需求双向追溯树已更新为:")
                    self.trace_tree.print_tree()

                # 更新结果到共享池中
                if not test_flag:
                    self.bus.publish("trace_tree.update_quality", {"trace_tree": self.trace_tree.to_dict(), "source": "QualityPlanner"})
                
                
                final_result["trace_tree"] = self.trace_tree.to_dict()
                emit_agent_status(
                    "QualityPlanner",
                    "finished",
                    "质量规划Agent规划部分完成",
                    {"mode": "plan", "node_count": len((final_result["trace_tree"] or {}).get("nodes", []))},
                )
                self._log_summary("质量规划Agent: 规划部分完成")
                return final_result
            except Exception as e:
                emit_agent_status(
                    "QualityPlanner",
                    "failed",
                    "质量规划Agent规划部分失败",
                    {"mode": "plan", "error": str(e)},
                    level="error",
                )
                self.debug_logger.exception("质量规划Agent: 规划部分失败")
                raise e

    def run_attach(self, debug: bool = False, test_flag: bool = False) -> Dict[str, Any]:
        with self.ai_client.usage_scope("QualityPlanner", scope_type="agent"), get_overhead_recorder().agent_run("QualityPlanner"):
            emit_agent_status("QualityPlanner", "started", "质量规划Agent开始运行约束添加部分", {"mode": "attach"})
            self._log_summary("质量规划Agent: 开始运行约束添加部分")

            self._begin_run()

            emit_agent_status("QualityPlanner", "progress", "执行质量约束添加", {"step": "constraint_attach"})
            constrained_functional_requirements = self.constraint_attach(debug=debug)

            if debug:
                self._log_debug("--- 质量约束添加 ---")
                self._log_debug(json.dumps(constrained_functional_requirements, ensure_ascii=False, indent=2))
                self.trace_tree.print_tree(show_constraints=True)

            if not test_flag:
                self.bus.publish("trace_tree.attach_constraints", {"trace_tree": self.trace_tree.to_dict(), "source": "QualityPlanner"})

            result = {
                "output": {
                    "constrained_functional_requirements": constrained_functional_requirements,
                },

                "trace_tree": self.trace_tree.to_dict()
            }

            emit_agent_status(
                "QualityPlanner",
                "finished",
                "质量规划Agent约束添加部分完成",
                {"mode": "attach", "node_count": len((result["trace_tree"] or {}).get("nodes", []))},
            )
            self._log_summary("质量规划Agent: 约束添加部分完成")
            return result
