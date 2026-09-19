import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os
import json
import re
from typing import Dict, Any, List, Set, Tuple, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.ai_client import get_default_ai_client, create_system_message, create_user_message
from config.logging_config import CASE_DEBUG_LOGGER_NAME, CASE_RUN_LOGGER_NAME
from config.overhead_recorder import get_overhead_recorder
from model.biReq_trace_tree import NodeKind, BiReqTraceTree
from model.trace_tree_bus import get_shared_trace_tree, get_shared_trace_bus, get_available_devices
from tool.AtomINT2IRP import generate_irp_rules
from tool.IRP2TAP import generate_tap
from tool.HighINT2AtomINT import refine_high_ints_to_atom_ints
from webui.runtime_events import emit_agent_status

# 规划这里会有两个问题：
# 1. 规划不全，会漏方案
# 2. 怎么根据检查结果进行反思

class RealizationPlannerAgent:
    def __init__(self):
        self.ai_client = get_default_ai_client()
        self._run_counter = 0
        self._current_run: Dict[str, Any] = {}
        self.bus = get_shared_trace_bus()
        self.trace_tree = None
        self.run_logger = logging.getLogger(f"{CASE_RUN_LOGGER_NAME}.RealizationPlanner")
        self.debug_logger = logging.getLogger(f"{CASE_DEBUG_LOGGER_NAME}.RealizationPlanner")
        self.satisfaction_prompt = """
# 任务
你是一个意图实现规划助手。你的任务是根据输入的功能意图、抽象的规划方案、具体的TAP规则，以及规则中涉及的设备与能力画像，判断这些TAP规则是否能满足该功能意图。

# 判断原则
1. 结合规则中涉及的设备与能力画像进行判断，优先参考其中的设备used_capability_names、matched_capabilities 及 capability description 做判断，而不是只看设备名表面语义。
2. 优先关注设备能力，而不是仅关注设备名，有些设备名字长得像传感器，但实际也有控制能力。
3. 触发条件和动作语义都要匹配。若 TAP 里使用了不相关的触发设备、错误的能力或不匹配的动作设备，则应判断为“否”。
4. 如果 atomic_intent_metadata 中 derived_balance_completion 为 true，表示当前原子意图是系统为实现“期望某环境属性处于目标值/目标区间”而自动补全出的反界闭环控制规则。此时允许该原子意图不直接出现在用户原始规则中；只要它与目标环境属性一致、阈值边界互补、动作方向正确，并且 TAP/IRP 没有偏离该高层目标，就可以判断为“是”。
5. 除上述 derived_balance_completion 情况外，必须追溯并对照与当前功能意图关联的用户原始输入规则，确保当前功能意图、IRP 和 TAP 没有脱离这些原始规则的语义边界。
6. 对环境属性要保持精确，不能把用户原始规则中的具体环境指标过度泛化成更宽泛的环境属性；若出现这类泛化，应判断为“否”。

# 限制
1. 如果TAP规则能满足该功能意图，返回"是"；否则返回"否"。
2. 只需要返回"是"或"否"，不需要返回其他内容。
        """
        self.repair_prompt = """
# 任务
你是一个意图实现修正助手。当当前 INT / IRP / TAP 没有通过满足性检查时，你需要基于原始用户规则、当前失败链路和可用设备能力，重写出一个更贴近用户语义边界的原子意图(INT)和抽象规划规则(IRP)。

# 修正规则
1. 必须严格参考“追溯到的用户原始输入规则”，不能偏离用户原始规则的触发指标、阈值方向和目标效果。
2. 原始规则中的具体环境指标必须保留，不能过度泛化。例如 PM2.5、CO2、VOC 等具体指标不能被改写成宽泛的“空气质量”。
3. 输出的 revised_int 必须严格为 `IF ... THEN ...` 格式，且 THEN 后只能有一个动作。
4. 输出的 revised_irp 也必须严格为 `IF ... THEN ...` 格式，且 THEN 后只能有一个动作设备。
5. revised_irp 必须优先使用设备能力画像中确实存在、且与用户原始规则语义一致的抽象设备/动作。
6. 如果当前失败链路已经足够接近用户原始规则，优先做最小修改，不要无故重写成完全不同的意图。
7. 如果确实无法修复，返回 null。

# 示例1
用户原始规则:
["IF bedroom_humidity_sensor.get_humidity() < 0.35 THEN power_on() humidifier_unit"]
失败链路:
- 当前 INT: IF 空气质量指数 < 0.35 THEN 提高 空气质量
- 当前 IRP: IF 空气质量感知设备.获取读数() < 0.35 THEN 打开() 空气净化设备
- 当前 TAP: ["IF bedroom_humidity_sensor.get_humidity() < 0.35 THEN turn_on() air_purifier_pro"]
修正输出:
{
  "revised_int": "IF 湿度 < 0.35 THEN 提高 湿度",
  "revised_irp": "IF 湿度感知设备.获取读数() < 0.35 THEN 打开() 加湿设备",
  "reason": "原始规则关注的是湿度与加湿器，不能泛化成空气质量或空气净化器。"
}

# 示例2
用户原始规则:
["IF living_room_temp_sensor.get_temperature() > 30 THEN start_cooling() cool_air_conditioner"]
失败链路:
- 当前 INT: IF 温度 > 30 THEN 升高 风速
- 当前 IRP: IF 温度感知设备.获取读数(温度) > 30 THEN 拉开() 窗帘设备
修正输出:
{
  "revised_int": "IF 温度 > 30 THEN 降低 温度",
  "revised_irp": "IF 温度感知设备.获取读数(温度) > 30 THEN 打开制冷() 空调设备",
  "reason": "窗帘设备与温度调节无关，应改回与空调制冷降温相关的实现。"
}

# 输出格式
只输出 JSON:
{
  "revised_int": "IF ... THEN ..." 或 null,
  "revised_irp": "IF ... THEN ..." 或 null,
  "reason": "..."
}
        """

    def _log_summary(self, message: str) -> None:
        self.run_logger.info(message)
        self.debug_logger.info(message)

    def _log_debug(self, message: str) -> None:
        self.debug_logger.debug(message)

    def _log_error(self, message: str) -> None:
        self.run_logger.error(message)
        self.debug_logger.error(message)

    def _begin_run(self, final_user_intent: str, trace_tree: Optional[BiReqTraceTree] = None) -> None:
        """初始化运行环境，获取 trace tree 和可用设备"""
        self._run_counter += 1
        self.trace_tree = trace_tree if trace_tree is not None else get_shared_trace_tree()
        self._current_run = {
            "final_user_intent": final_user_intent,
            "available_devices": self.bus.get_available_devices(),
            "device_knowledge": self.bus.get_available_devices().get_all_device_knowledge_str(),
        }



    @staticmethod
    def _is_atom_int(intent_text: str) -> bool:
        s = intent_text.strip() if isinstance(intent_text, str) else ""
        if not s.startswith("IF "):
            return False
        if " THEN " not in s:
            return False
        parts = s.split(" THEN ", 1)
        return len(parts) == 2 and parts[0].strip() != "IF" and bool(parts[1].strip())

    @staticmethod
    def _normalize_text(text: object) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @classmethod
    def _parse_numeric_literal(cls, text: object) -> Optional[float]:
        normalized = cls._normalize_text(text)
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
            return None
        try:
            return float(normalized)
        except Exception:
            return None

    @classmethod
    def _same_numeric_value(cls, left: object, right: object, tolerance: float = 1e-9) -> bool:
        left_num = cls._parse_numeric_literal(left)
        right_num = cls._parse_numeric_literal(right)
        if left_num is None or right_num is None:
            return False
        return abs(left_num - right_num) <= tolerance

    @classmethod
    def _parse_target_range_intent(cls, intent_text: str) -> Optional[Dict[str, Any]]:
        normalized = cls._normalize_text(intent_text)
        match = re.match(r"^期望\s+(?P<attr>.+?)\s+处于\s+(?P<target>.+?)\s*$", normalized)
        if not match:
            return None

        attr = cls._normalize_text(match.group("attr"))
        target = cls._normalize_text(match.group("target"))
        range_match = re.match(
            r"^(?P<lower>[-+]?\d+(?:\.\d+)?)\s*(?:-|~|到|至|—|–)\s*(?P<upper>[-+]?\d+(?:\.\d+)?)$",
            target,
        )
        if range_match:
            lower_text = range_match.group("lower")
            upper_text = range_match.group("upper")
            lower_value = cls._parse_numeric_literal(lower_text)
            upper_value = cls._parse_numeric_literal(upper_text)
            if lower_value is None or upper_value is None:
                return None
            return {
                "attribute": attr,
                "target_text": target,
                "lower_text": lower_text,
                "upper_text": upper_text,
                "lower_value": lower_value,
                "upper_value": upper_value,
                "mode": "range",
            }

        exact_value = cls._parse_numeric_literal(target)
        if exact_value is None:
            return None
        return {
            "attribute": attr,
            "target_text": target,
            "lower_text": target,
            "upper_text": target,
            "lower_value": exact_value,
            "upper_value": exact_value,
            "mode": "point",
        }

    @classmethod
    def _parse_atom_intent_structure(cls, atom_intent: str) -> Optional[Dict[str, str]]:
        normalized = cls._normalize_text(atom_intent)
        match = re.match(
            r"^IF\s+(?P<attr>.+?)\s*(?P<op><=|>=|<|>|==)\s*(?P<value>.+?)\s+THEN\s+(?P<verb>\S+)\s+(?P<target>.+?)\s*$",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            return None
        return {
            "attribute": cls._normalize_text(match.group("attr")),
            "operator": match.group("op").strip(),
            "value": cls._normalize_text(match.group("value")),
            "action_verb": cls._normalize_text(match.group("verb")),
            "action_target": cls._normalize_text(match.group("target")),
        }

    @classmethod
    def _classify_balance_completion(cls, final_user_intent: str, atom_intent: str) -> Optional[Dict[str, Any]]:
        target_info = cls._parse_target_range_intent(final_user_intent)
        atom_info = cls._parse_atom_intent_structure(atom_intent)
        if not target_info or not atom_info:
            return None

        attribute = target_info["attribute"]
        if atom_info["attribute"] != attribute or atom_info["action_target"] != attribute:
            return None

        action_verb = atom_info["action_verb"]
        lower_verbs = {"升高", "增加", "提高", "调高"}
        upper_verbs = {"降低", "减少", "调低"}
        operator = atom_info["operator"]
        value = atom_info["value"]

        if (
            operator in {"<", "<="}
            and action_verb in lower_verbs
            and cls._same_numeric_value(value, target_info["lower_text"])
        ):
            return {
                "refinement_kind": "balance_completion",
                "balance_side": "lower",
                "target_attribute": attribute,
                "target_value": target_info["target_text"],
            }

        if (
            operator in {">", ">="}
            and action_verb in upper_verbs
            and cls._same_numeric_value(value, target_info["upper_text"])
        ):
            return {
                "refinement_kind": "balance_completion",
                "balance_side": "upper",
                "target_attribute": attribute,
                "target_value": target_info["target_text"],
            }

        return None

    @classmethod
    def _build_atomic_intent_metadata(
        cls,
        final_user_intent: str,
        atom_intents: List[str],
        existing_atom_intents: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        existing_set = {cls._normalize_text(x) for x in (existing_atom_intents or []) if isinstance(x, str) and x.strip()}
        metadata_map: Dict[str, Dict[str, Any]] = {}

        for atom_int in atom_intents:
            if not isinstance(atom_int, str):
                continue
            atom_text = cls._normalize_text(atom_int)
            if not atom_text:
                continue

            is_existing = atom_text in existing_set or atom_text == cls._normalize_text(final_user_intent)
            metadata: Dict[str, Any] = {
                "source": "existing" if is_existing else "refined",
                "derived_balance_completion": False,
            }

            balance_info = cls._classify_balance_completion(final_user_intent, atom_text)
            if balance_info:
                metadata.update(balance_info)
                if not is_existing:
                    metadata["derived_balance_completion"] = True
                    metadata["source"] = "derived_balance_completion"

            metadata_map[atom_text] = metadata

        return metadata_map

    def _get_atomic_intent_metadata(self, atomic_user_intent: str) -> Dict[str, Any]:
        metadata_map = self._current_run.get("atomic_intent_metadata") or {}
        if not isinstance(metadata_map, dict):
            return {}
        metadata = metadata_map.get(self._normalize_text(atomic_user_intent)) or {}
        return metadata if isinstance(metadata, dict) else {}

    def intent_refinement(self, final_user_intent: str, debug: bool = False) -> List[str]:
        if debug:
            self._log_debug("--- Step 1: 意图精化 ---")
            self._log_debug(f"意图精化输入: {final_user_intent}")

        original_id = self.trace_tree.ensure_original_intent_node('功能意图', final_user_intent)
        try:
            self.trace_tree.update_node_data(original_id, {"isNew": False})
        except Exception as e:
            raise e

        existing_atom_intents: List[str] = []
        if self._is_atom_int(final_user_intent):
            atom_intents = [final_user_intent]
        else:
            seen: Set[str] = set[str]()
            try:
                for nid in self.trace_tree.descendants(original_id):
                    try:
                        info = self.trace_tree.get_node(nid)
                    except Exception as e:
                        self._log_debug(f"获取节点信息出错: {e}")
                        continue
                    if info.get("type") != NodeKind.INT.value:
                        continue
                    if not self._is_atom_int(nid):
                        continue
                    if nid not in seen:
                        existing_atom_intents.append(nid)
                        seen.add(nid)
            except Exception as e:
                self._log_debug(f"遍历子节点出错: {e}")
            
            if debug:
                self._log_debug(f"已存在的原子意图: {existing_atom_intents}")
                self._log_debug(f"待精化的意图: {final_user_intent}")

            refined = refine_high_ints_to_atom_ints(
                final_user_intent,
                existing_atom_ints=existing_atom_intents,
                debug=debug,
            )
            atom_intents = refined.get("atom_intents") or []
            if not isinstance(atom_intents, list):
                atom_intents = []
            atom_intents = [str(x).strip() for x in atom_intents if isinstance(x, str) and x.strip()]

        atomic_intent_metadata = self._build_atomic_intent_metadata(
            final_user_intent,
            atom_intents,
            existing_atom_intents=existing_atom_intents,
        )
        self._current_run["atomic_intent_metadata"] = atomic_intent_metadata

        try:
            existing_children = set(self.trace_tree.get_children(original_id))
        except Exception as e:
            self._log_debug(f"获取原始意图节点的子节点出错: {e}")
            existing_children = set()
        for atom_int in atom_intents:
            if atom_int in existing_children:
                continue
            try:
                self.trace_tree.add_child(original_id, atom_int, NodeKind.INT, {"isNew": False})
                existing_children.add(atom_int)
            except Exception as e:
                self._log_debug(f"添加原子意图节点出错: {e}")

        if debug:
            self._log_debug(f"意图精化输出: {atom_intents}")
            self._log_debug(f"原子意图元数据: {json.dumps(atomic_intent_metadata, ensure_ascii=False, indent=2)}")
        return atom_intents

    def IRP_plan(self, atomic_user_intent: object, debug: bool = False):
        if debug:
            self._log_debug("--- Step 2: 意图实现规划 ---")
            self._log_debug(f"意图实现规划输入: {atomic_user_intent}")

        IRP = generate_irp_rules(atomic_user_intent, debug)
        
        if debug:
            self._log_debug(f"意图实现规划输出: {IRP}")
        return IRP

    def TAP_generation(self, plan: str, debug: bool = False) -> List[str]:
        if debug:
            self._log_debug("--- Step 3: TAP规则生成 ---")
            self._log_debug(f"TAP规则生成输入: {plan}")

        plan_text = plan.strip()
        if not plan_text or plan_text.lower() == "null":
            return []

        available = self._current_run.get("available_devices")
        available_names = None
        if hasattr(available, "get_available_device_names"):
            try:
                available_names = available.get_available_device_names()
            except Exception:
                available_names = None
        elif isinstance(available, list):
            available_names = [x for x in available if isinstance(x, str)]

        TAP = generate_tap(plan_text, available_names)
        
        if debug:
            self._log_debug(f"TAP规则生成输出: {TAP}")
        return TAP

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

    def _build_device_context_for_satisfaction(self, forward_chain: Dict[str, Any]) -> Dict[str, Any]:
        """检索功能链中涉及的抽象/具体设备与能力信息，供满足性检查使用。"""
        irp_text = (forward_chain.get("IRP") or "").strip()
        taps = forward_chain.get("TAP") or []
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

    def _collect_user_rules_for_intent(self, intent_text: str) -> List[str]:
        """从追溯树回捞与当前功能意图直接相关的用户原始输入规则。"""
        try:
            all_user_rules = [
                rule.strip()
                for rule in (self.bus.get_user_input_rules() or [])
                if isinstance(rule, str) and rule.strip()
            ]
        except Exception:
            all_user_rules = []

        if not all_user_rules or not self.trace_tree:
            return []

        user_rule_set = set(all_user_rules)
        candidate_nodes: List[str] = []
        seen_nodes: Set[str] = set()

        def _append_node(node_id: object) -> None:
            if not isinstance(node_id, str):
                return
            node_text = node_id.strip()
            if not node_text or node_text in seen_nodes:
                return
            if not self.trace_tree.has_node(node_text):
                return
            candidate_nodes.append(node_text)
            seen_nodes.add(node_text)

        _append_node(self._current_run.get("final_user_intent"))
        _append_node(intent_text)

        for node_id in list(candidate_nodes):
            try:
                for anc in self.trace_tree.ancestors(node_id):
                    try:
                        anc_info = self.trace_tree.get_node(anc)
                    except Exception:
                        anc_info = {}
                    if anc_info.get("type") != NodeKind.INT.value:
                        continue
                    _append_node(anc)
            except Exception:
                continue

        traced_rules: List[str] = []
        seen_rules: Set[str] = set()
        for node_id in candidate_nodes:
            subtree_nodes = [node_id]
            try:
                subtree_nodes.extend(self.trace_tree.descendants(node_id))
            except Exception:
                pass
            for sub_node_id in subtree_nodes:
                if sub_node_id in user_rule_set and sub_node_id not in seen_rules:
                    traced_rules.append(sub_node_id)
                    seen_rules.add(sub_node_id)

        if traced_rules:
            return traced_rules
        if len(all_user_rules) == 1:
            return all_user_rules
        return []

    def satisfaction_check(self, forward_chain: object, debug: bool = False):
        # 如果不通过满足性检查，那么将直接丢弃该条链
        if debug:
            self._log_debug("--- Step 4: 满足性检查 ---")
            self._log_debug(f"输入 TAP: {forward_chain}")

        intent_text = forward_chain.get("INT") or ""
        irp_text = forward_chain.get("IRP") or ""
        tap_text = forward_chain.get("TAP") or ""
        atomic_intent_metadata = forward_chain.get("atomic_intent_metadata") or {}
        device_context = self._build_device_context_for_satisfaction(forward_chain)
        traced_user_rules = self._collect_user_rules_for_intent(intent_text)

        if debug:
            self._log_debug("满足性检查原子意图元数据:")
            self._log_debug(json.dumps(atomic_intent_metadata, ensure_ascii=False, indent=2))
            self._log_debug("满足性检查设备上下文:")
            self._log_debug(json.dumps(device_context, ensure_ascii=False, indent=2))
            self._log_debug("满足性检查追溯到的用户原始规则:")
            self._log_debug(json.dumps(traced_user_rules, ensure_ascii=False, indent=2))
        
        with self.ai_client.usage_scope("satisfaction_check", scope_type="step"):
            output = self.ai_client.simple_completion(
                messages_input=[
                    create_system_message(self.satisfaction_prompt),
                    create_user_message(
                        "这是用户输入：\n"
                        f"需要实现的功能意图: {intent_text}\n"
                        f"抽象的规划方案: {irp_text}\n"
                        f"具体的TAP规则: {tap_text}\n"
                        f"当前原子意图元数据: {json.dumps(atomic_intent_metadata, ensure_ascii=False, indent=2)}\n"
                        f"规则涉及的设备与能力信息: {json.dumps(device_context, ensure_ascii=False, indent=2)}\n"
                        f"这条功能意图追溯到的用户原始输入规则: {json.dumps(traced_user_rules, ensure_ascii=False, indent=2)}"
                    )
                ]
            ).strip()

        if debug:
            self._log_debug(f"满足性检查结果: {output}")

        if "是" in output:
            return True
        elif "否" in output:
            return False
        else:
            raise ValueError(f"未知的满足性检查输出: {output}")

    @staticmethod
    def _is_irp_rule(rule_text: object) -> bool:
        text = str(rule_text or "").strip()
        return bool(text.startswith("IF ") and " THEN " in text)

    def _build_temp_result(
        self,
        final_user_intent: str,
        atomic_user_intent: str,
        atomic_intent_metadata: Dict[str, Any],
        irp_text: str,
        tap: object,
        ok: bool,
        reflection_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        temp_res = {
            "original_intent": final_user_intent,
            "step1_output": {
                "atomic_user_intent": atomic_user_intent,
                "is_refined": atomic_user_intent != final_user_intent,
                "atomic_intent_metadata": atomic_intent_metadata,
            },
            "step2_output": {
                "IRP": irp_text
            },
            "step3_output": {
                "TAP": tap,
            },
            "step4_output": {
                "satisfaction_output": ok
            }
        }
        if reflection_info:
            temp_res["step4_output"]["reflection"] = reflection_info
        return temp_res

    def _reflect_failed_chain(
        self,
        final_user_intent: str,
        forward_chain: Dict[str, Any],
        debug: bool = False,
    ) -> Optional[Dict[str, Any]]:
        intent_text = forward_chain.get("INT") or ""
        irp_text = forward_chain.get("IRP") or ""
        tap_text = forward_chain.get("TAP") or ""
        device_context = self._build_device_context_for_satisfaction(forward_chain)
        traced_user_rules = self._collect_user_rules_for_intent(intent_text)

        if debug:
            self._log_debug("--- Step 4.1: 满足性失败反思修正 ---")
            self._log_debug(f"反思输入 INT: {intent_text}")
            self._log_debug(f"反思输入 IRP: {irp_text}")
            self._log_debug(f"反思输入 TAP: {tap_text}")
            self._log_debug(f"反思追溯到的用户原始规则: {json.dumps(traced_user_rules, ensure_ascii=False, indent=2)}")

        try:
            with self.ai_client.usage_scope("repair_failed_chain", scope_type="step"):
                raw = self.ai_client.simple_completion(
                    messages_input=[
                        create_system_message(self.repair_prompt),
                        create_user_message(
                            "这是用户输入：\n"
                            f"最终功能意图: {final_user_intent}\n"
                            f"当前失败的原子意图(INT): {intent_text}\n"
                            f"当前失败的规划方案(IRP): {irp_text}\n"
                            f"当前失败的TAP规则: {tap_text}\n"
                            f"规则涉及的设备与能力信息: {json.dumps(device_context, ensure_ascii=False, indent=2)}\n"
                            f"这条功能意图追溯到的用户原始输入规则: {json.dumps(traced_user_rules, ensure_ascii=False, indent=2)}"
                        )
                    ],
                    use_json=True,
                    temperature=0.0,
                )
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as e:
            if debug:
                self._log_debug(f"反思修正出错: {e}")
            return None

        if debug:
            self._log_debug(f"反思修正输出: {json.dumps(parsed, ensure_ascii=False, indent=2)}")

        revised_int = (parsed.get("revised_int") or "").strip() if isinstance(parsed, dict) else ""
        revised_irp = (parsed.get("revised_irp") or "").strip() if isinstance(parsed, dict) else ""
        reason = (parsed.get("reason") or "").strip() if isinstance(parsed, dict) else ""

        if not self._is_atom_int(revised_int):
            return None
        if not self._is_irp_rule(revised_irp):
            return None
        if self._normalize_text(revised_int) == self._normalize_text(intent_text) and self._normalize_text(revised_irp) == self._normalize_text(irp_text):
            return None

        revised_metadata_map = self._build_atomic_intent_metadata(
            final_user_intent,
            [revised_int],
            existing_atom_intents=[intent_text],
        )
        revised_metadata = revised_metadata_map.get(self._normalize_text(revised_int), {})
        revised_tap = self.TAP_generation(revised_irp, debug) if revised_irp else []
        repaired_chain = {
            "INT": revised_int,
            "IRP": revised_irp,
            "TAP": revised_tap,
            "atomic_intent_metadata": revised_metadata,
        }

        try:
            repaired_ok = self.satisfaction_check(repaired_chain, debug)
        except Exception as e:
            if debug:
                self._log_debug(f"反思后满足性检查出错: {e}")
            repaired_ok = False

        return self._build_temp_result(
            final_user_intent=final_user_intent,
            atomic_user_intent=revised_int,
            atomic_intent_metadata=revised_metadata,
            irp_text=revised_irp,
            tap=revised_tap,
            ok=repaired_ok,
            reflection_info={
                "attempted": True,
                "reason": reason,
                "reflected_from": {
                    "INT": intent_text,
                    "IRP": irp_text,
                    "TAP": tap_text,
                },
            },
        )

    def _evaluate_chain_with_reflection(
        self,
        final_user_intent: str,
        atomic_user_intent: str,
        atomic_intent_metadata: Dict[str, Any],
        irp_text: str,
        debug: bool = False,
    ) -> List[Dict[str, Any]]:
        tap = self.TAP_generation(irp_text, debug) if irp_text else []
        forward_chain = {
            "INT": atomic_user_intent,
            "IRP": irp_text,
            "TAP": tap,
            "atomic_intent_metadata": atomic_intent_metadata,
        }

        try:
            ok = self.satisfaction_check(forward_chain, debug)
        except Exception as e:
            self._log_debug(f"满足性检查出错: {e}")
            ok = False

        results = [
            self._build_temp_result(
                final_user_intent=final_user_intent,
                atomic_user_intent=atomic_user_intent,
                atomic_intent_metadata=atomic_intent_metadata,
                irp_text=irp_text,
                tap=tap,
                ok=ok,
            )
        ]
        if ok:
            return results

        repaired = self._reflect_failed_chain(final_user_intent, forward_chain, debug)
        if repaired is not None:
            results.append(repaired)
        return results

    @staticmethod
    def _flatten_irp_texts(irp_value: object) -> List[str]:
        texts: List[str] = []

        def _walk(value: object) -> None:
            if isinstance(value, str):
                text = value.strip()
                if text and "null" not in text.lower():
                    texts.append(text)
                return
            if isinstance(value, list):
                for item in value:
                    _walk(item)

        _walk(irp_value)
        return texts

    def _process_atomic_intent(
        self,
        final_user_intent: str,
        atomic_user_intent: str,
        debug: bool = False,
        scope_snapshot: Optional[List[Dict[str, Any]]] = None,
        overhead_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        with self.ai_client.inherit_scope_stack(scope_snapshot), get_overhead_recorder().inherit_context(overhead_snapshot):
            temp_results: List[Dict[str, Any]] = []
            atomic_intent_metadata = self._get_atomic_intent_metadata(atomic_user_intent)
            IRP = self.IRP_plan(atomic_user_intent, debug)

            irp_texts = self._flatten_irp_texts(IRP)
            if not irp_texts:
                if debug:
                    self._log_debug("当前原子意图未生成可用 IRP，尝试直接进入反思修正")
                irp_texts = [""]

            for irp_text in irp_texts:
                if debug:
                    self._log_debug(f"当前IRP: {irp_text}")
                temp_results.extend(
                    self._evaluate_chain_with_reflection(
                        final_user_intent=final_user_intent,
                        atomic_user_intent=atomic_user_intent,
                        atomic_intent_metadata=atomic_intent_metadata,
                        irp_text=irp_text,
                        debug=debug,
                    )
                )

            return temp_results

    def _sync_atomic_intents_to_tree(self, final_user_intent: str, atomic_user_intents: List[str]) -> Optional[str]:
        try:
            original_id = self.trace_tree.ensure_original_intent_node('功能意图', final_user_intent)
            self.trace_tree.update_node_data(original_id, {"isNew": False})
        except Exception as e:
            self._log_debug(f"准备原始意图节点出错: {e}")
            return None

        try:
            existing_children = set(self.trace_tree.get_children(original_id))
        except Exception as e:
            self._log_debug(f"获取原始意图节点的子节点出错: {e}")
            existing_children = set()

        for atom_int in atomic_user_intents:
            if not isinstance(atom_int, str):
                continue
            atom_text = atom_int.strip()
            if not atom_text or atom_text == final_user_intent or atom_text in existing_children:
                continue
            try:
                self.trace_tree.add_child(original_id, atom_text, NodeKind.INT, {"isNew": False})
                existing_children.add(atom_text)
            except Exception as e:
                self._log_debug(f"添加原子意图节点出错: {e}")

        return original_id

    def _merge_temp_results_to_tree(
        self,
        final_user_intent: str,
        atomic_user_intents: List[str],
        temp_results: List[Dict[str, Any]],
        debug: bool = False,
    ) -> List[Dict[str, Any]]:
        forward_chains: List[Dict[str, Any]] = []
        original_id = self._sync_atomic_intents_to_tree(final_user_intent, atomic_user_intents)

        for temp_res in temp_results:
            ok = bool(temp_res.get("step4_output", {}).get("satisfaction_output"))
            atomic_user_intent = temp_res.get("step1_output", {}).get("atomic_user_intent", "")
            irp_text = temp_res.get("step2_output", {}).get("IRP", "")
            taps = temp_res.get("step3_output", {}).get("TAP", [])

            if ok:
                forward_chains.append(temp_res)
                if debug:
                    self._log_debug(f"当前结果: {temp_res}")
                if original_id is not None:
                    try:
                        self.trace_tree.attach_realization_result(
                            original_id,
                            atomic_user_intent,
                            irp_text,
                            taps if isinstance(taps, list) else ([taps] if taps else []),
                            temp_res["step1_output"]["is_refined"]
                        )
                    except Exception as e:
                        self._log_debug(f"添加实现结果到跟踪树出错: {e}")
            else:
                if debug:
                    self._log_debug("满足性检查未通过，已丢弃")
                    self._log_debug(f"当前结果(已丢弃): {temp_res}")

        return forward_chains

    @classmethod
    def _run_single_intent_worker(
        cls,
        final_user_intent: str,
        base_tree_payload: Dict[str, Any],
        scope_snapshot: Optional[List[Dict[str, Any]]] = None,
        debug: bool = False,
        overhead_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        worker = cls()
        isolated_tree = BiReqTraceTree.from_dict(base_tree_payload)
        with worker.ai_client.inherit_scope_stack(scope_snapshot), get_overhead_recorder().inherit_context(overhead_snapshot):
            worker._begin_run(final_user_intent, trace_tree=isolated_tree)
            atomic_user_intents = worker.intent_refinement(final_user_intent, debug)
            atomic_results: List[tuple[int, List[Dict[str, Any]]]] = []
            worker_count = max(1, min(4, len(atomic_user_intents)))

            if worker_count == 1:
                for idx, atomic_user_intent in enumerate(atomic_user_intents):
                    atomic_results.append(
                        (idx, worker._process_atomic_intent(final_user_intent, atomic_user_intent, debug))
                    )
            else:
                future_to_idx = {}
                nested_scope_snapshot = worker.ai_client.get_scope_stack_snapshot()
                nested_overhead_snapshot = get_overhead_recorder().snapshot_context()
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    for idx, atomic_user_intent in enumerate(atomic_user_intents):
                        future = executor.submit(
                            worker._process_atomic_intent,
                            final_user_intent,
                            atomic_user_intent,
                            debug,
                            nested_scope_snapshot,
                            nested_overhead_snapshot,
                        )
                        future_to_idx[future] = idx
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        try:
                            atomic_results.append((idx, future.result()))
                        except Exception as e:
                            worker._log_debug(f"并行处理原子意图出错: {e}")
                            atomic_results.append((idx, []))

            atomic_results.sort(key=lambda item: item[0])
            temp_results: List[Dict[str, Any]] = []
            for _, atomic_temp_results in atomic_results:
                temp_results.extend(atomic_temp_results)

            return {
                "final_user_intent": final_user_intent,
                "atomic_user_intents": atomic_user_intents,
                "temp_results": temp_results,
            }



    def _conflict_refine(self, current_tree: str, check_result: List[str], device_knowledge: str) -> List[Dict]:
        """根据错误检测报告，精化需求规约。"""


        refinement_prompt = """
# 任务
你是一个需求精化师，目的是根据错误检测报告，修改并精化现有的需求规约，最终输出一个列表，包含源项和精化结果。
错误检测报告会检测出多值错误，指的是同一时间，两条需求可能会同时发生，使得某个设备接收到两个不同的信号，从而引起混淆。
修复建议为：
1. 对功能意图下的需求添加约束条件，使得不同需求间保持互斥性。在添加互斥条件时，需要注意不能引起自冲突，例如：IF hallway_light_sensor.reading() < 120 AND hallway_light_sensor.reading() > 120 THEN turn_on() desk_lamp_pro是不被允许的。此时可以查询设备知识，看看能否引入其它互斥条件(如设备状态)。
2. 删除功能意图下的低优先级DSL需求，例如为了节能而“关闭”设备的需求优先级就低于为了正常工作而“打开”设备的需求，但用户输入的规则禁止删除。优先级一般如下: 涉及调高/调低设备状态的需求 > 涉及打开/关闭设备的需求 > 其他需求
需要注意的是，优先考虑添加修改需求，而不是删除。除非是示例中的典型情况，否则不建议删除。

以下为示例：
# 示例1
功能意图下存在DSL需求1:IF study_room_thermometer.reading() < 16 THEN turn_heat_on() heater_device
质量意图下存在DSL需求2:IF motion_detector_pro.reading() == 人离开 THEN turn_off() heater_device
两者存在冲突，那么将功能意图下的DSL需求1修改为：IF study_room_thermometer.reading() < 16 AND IF motion_detector_pro.reading() != 人离开 THEN turn_heat_on() heater_device

# 示例2
功能意图下存在DSL需求1:IF hallway_light_sensor.get_brightness() < 45 THEN turn_light_up() hallway_lamp
质量意图下存在DSL需求2:IF hallway_light_sensor.get_brightness() < 45 THEN turn_light_on() hallway_lamp
两者存在冲突，那么将功能意图下的DSL需求1修改为：IF hallway_light_sensor.get_brightness() < 45 AND hallway_lamp.get_state() == on THEN turn_light_up() hallway_lamp

# 示例3
功能意图下存在DSL需求1:IF hallway_light_sensor.get_brightness() < 45 THEN turn_light_up() desk_lamp_pro
质量意图下存在DSL需求2:IF hallway_light_sensor.get_brightness() < 45 THEN turn_light_on() desk_lamp_pro
两者存在冲突，但由于desk_lamp_pro设备中不存在get_state()方法用于查询当前灯的状态，优先保留更具有功能特性的升亮需求，那么将功能意图下的开关需求DSL需求2给舍弃。

# 示例4
功能意图下存在DSL需求1:IF study_room_thermometer.reading() < 16 THEN turn_heat_on() heater_device
功能意图下存在DSL需求2:IF study_room_thermometer.reading() < 16 THEN turn_off() heater_device
两者存在冲突，那么删去功能意图下的DSL需求2，因为此时为了正常工作，“打开”设备的需求优先级要高于为了节能而“关闭”设备的需求。

# 示例5
功能意图下存在DSL需求1:IF xx_temperature_sensor.reading() < 18 THEN turn_heat_on() xx_air_conditioner
功能意图下存在DSL需求2:IF xx_temperature_sensor.reading() < 18 THEN turn_swing_mode() xx_air_conditioner
用户输入：IF xx_temperature_sensor.reading() < 18 THEN turn_heat_on() xx_air_conditioner
两者存在冲突，由于用户输入需求1，需求1的优先级高于需求2，那么删去功能意图下的DSL需求2，同时，此时为了正常工作，打开制热模式的需求优先级要高于调整为 Swing 模式的需求。

# 输出格式（json）
{
    "results": [{
        "src": {
            "orignal_name": "...",
            "type": "DSL" OR "IRP"
        },
        "refine_result": {
            "operation": "update" OR "delete",
            "new_name": "..."
        }
    }]
}

# 限制
1. 尽量少改动：优先功能意图下的规则做最小替换修正。
2. 输出只包含精化结果，不要输出解释。
3. 不允许修改或删除任何质量意图下的DSL规则，不管是IRP还是DSL。
4. 用户输入的规则禁止删除，只允许修改。
5. 你只能使用“设备知识”中明确存在的设备能力(capability)。如果设备知识里没有某个 capability，就绝对不允许在 refine_result.new_name 中生成它。
6. 严禁臆造设备状态查询能力。例如只有当设备知识中明确存在 get_state()、query_state() 等状态查询 capability 时，才允许添加类似 device.get_state() == on/off 的条件；否则必须选择别的修复方式，或按规则删除较低优先级的非用户输入 DSL。
7. 在生成任何包含 device.capability() 的新增条件前，先核对该 capability 是否真的出现在设备知识里；若不存在，必须判定该修改方案无效，不得输出。
8. src.orignal_name 必须严格来自“错误检测报告”中出现的原始冲突规则文本，禁止选择错误检测报告中未直接出现的其它规则。
"""
        try:
            user_rules = self.bus.get_user_input_rules()
            with self.ai_client.usage_scope("conflict_refine", scope_type="step"):
                raw = self.ai_client.simple_completion(
                    messages_input=[
                        create_system_message(refinement_prompt),
                        create_user_message(f"这是需要精化的需求规约：\n{current_tree}"),
                        create_user_message(f"这是错误检测报告：\n{chr(10).join([str(x) for x in (check_result or [])])}"),
                        create_user_message(f"这是设备知识：\n{device_knowledge}"),
                        create_user_message(f"这是用户输入的规则列表（禁止删除）：\n{json.dumps(user_rules, ensure_ascii=False, indent=2)}"),
                    ],
                    use_json=True,
                    temperature=0.0,
                )
            # print(f"响应结果为:{json.dumps(raw, ensure_ascii=False, indent=2)}")
            parsed_obj = json.loads(raw) if isinstance(raw, str) else (raw or {})
            self._log_debug(f"精化结果为:{json.dumps(parsed_obj, ensure_ascii=False, indent=2)}")
            results = parsed_obj.get("results") if isinstance(parsed_obj, dict) else None
            if isinstance(results, list):
                return results
        except Exception as e:
            self._log_debug(f"精化需求规约时发生错误: {e}")
            return []
        return []

    def _is_under_int_type(self, node_id: str, int_type_id: str) -> bool:
        if not self.trace_tree or not isinstance(node_id, str) or not node_id.strip():
            return False
        if not self.trace_tree.has_node(node_id):
            return False
        try:
            for anc in self.trace_tree.ancestors(node_id):
                if anc == int_type_id:
                    return True
        except Exception:
            return False
        return False

    def _apply_refinement_results(self, results: List[Dict[str, Any]], debug: bool = False) -> Dict[str, int]:
        stats = {"total": 0, "applied": 0, "skipped": 0, "updated": 0, "deleted": 0}
        if not self.trace_tree:
            return stats
        user_rules = set()
        try:
            user_rules = set(self.bus.get_user_input_rules() or [])
        except Exception:
            user_rules = set()

        for item in results:
            stats["total"] += 1
            if not isinstance(item, dict):
                stats["skipped"] += 1
                continue

            src = item.get("src") or {}
            refine_result = item.get("refine_result") or {}
            if not isinstance(src, dict) or not isinstance(refine_result, dict):
                stats["skipped"] += 1
                continue

            original_name = src.get("original_name") or src.get("orignal_name")
            src_type = src.get("type")
            operation = refine_result.get("operation")
            new_name = refine_result.get("new_name")

            if not isinstance(original_name, str) or not original_name.strip():
                stats["skipped"] += 1
                continue
            if not isinstance(operation, str) or not operation.strip():
                stats["skipped"] += 1
                continue

            original_name = original_name.strip()
            operation = operation.strip().lower()
            if isinstance(src_type, str):
                src_type = src_type.strip()
            else:
                src_type = None

            if operation == "delete" and original_name in user_rules:
                stats["skipped"] += 1
                continue

            if not self.trace_tree.has_node(original_name):
                stats["skipped"] += 1
                continue

            if not self._is_under_int_type(original_name, "功能意图"):
                stats["skipped"] += 1
                continue

            try:
                node_info = self.trace_tree.get_node(original_name)
            except Exception:
                stats["skipped"] += 1
                continue

            if src_type and node_info.get("type") != src_type:
                stats["skipped"] += 1
                continue

            if operation == "update":
                if not isinstance(new_name, str) or not new_name.strip():
                    stats["skipped"] += 1
                    continue
                new_name = new_name.strip()
                if new_name == original_name:
                    stats["skipped"] += 1
                    continue

                try:
                    if not self.trace_tree.has_node(new_name):
                        self.trace_tree.update_node_id(original_name, new_name)
                        stats["applied"] += 1
                        stats["updated"] += 1
                        continue

                    try:
                        new_info = self.trace_tree.get_node(new_name)
                    except Exception:
                        new_info = None

                    if not isinstance(new_info, dict) or new_info.get("type") != node_info.get("type"):
                        unique = self.trace_tree.ensure_unique_id(new_name)
                        self.trace_tree.update_node_id(original_name, unique)
                        stats["applied"] += 1
                        stats["updated"] += 1
                        continue

                    old_parent = node_info.get("parent")
                    new_parent = new_info.get("parent")

                    if old_parent and new_parent and old_parent != new_parent:
                        unique = self.trace_tree.ensure_unique_id(new_name)
                        self.trace_tree.update_node_id(original_name, unique)
                        stats["applied"] += 1
                        stats["updated"] += 1
                        continue

                    if old_parent and not new_parent:
                        try:
                            self.trace_tree.add_child(old_parent, new_name, NodeKind(new_info.get("type")))
                        except Exception:
                            pass

                    old_children = list(self.trace_tree.nodes[original_name].children)
                    for cid in old_children:
                        self.trace_tree.nodes[original_name].children.discard(cid)
                        if cid not in self.trace_tree.nodes[new_name].children:
                            self.trace_tree.nodes[new_name].children.add(cid)
                        self.trace_tree.nodes[cid].parent = new_name

                    try:
                        self.trace_tree.update_node_data(new_name, node_info.get("data") or {})
                    except Exception:
                        pass

                    try:
                        self.trace_tree.remove_node(original_name)
                    except Exception:
                        pass

                    stats["applied"] += 1
                    stats["updated"] += 1
                except Exception as e:
                    if debug:
                        self._log_debug(f"更新节点失败: {e}")
                    stats["skipped"] += 1
                continue

            if operation == "delete":
                try:
                    self.trace_tree.remove_node(original_name)
                    stats["applied"] += 1
                    stats["deleted"] += 1
                except Exception as e:
                    if debug:
                        self._log_debug(f"删除节点失败: {e}")
                    stats["skipped"] += 1
                continue

            stats["skipped"] += 1

        return stats

    # TODO 目前只有对DSL的精化操作，还缺少对IRP的精化操作
    def run_refinement(self, check_result, debug: bool = False, test_flag: bool = False):
        emit_agent_status("RealizationPlanner", "started", "规划实现Agent开始处理精化请求", {"mode": "refinement"})
        self._log_summary("规划实现Agent: 开始处理错误报告")
        if debug:
            self._log_debug(f"接收到错误报告: {json.dumps(check_result, ensure_ascii=False, indent=2)}")

        with self.ai_client.usage_scope("RealizationPlanner", scope_type="agent"), get_overhead_recorder().agent_run("RealizationPlanner", trigger_type="refinement"):
            self._begin_run("refine requirements")
            result: List[Dict[str, Any]] = []
            if not check_result.get("all_TAP_ok", False):
                tap_checking_result = check_result.get("TAP_checking_result", {})
                if debug:
                    self._log_debug("发现TAP存在错误...")
                
                if  len(tap_checking_result.get("多值冲突", [])) > 0:
                    if debug:
                        self._log_debug("发现存在多值冲突...")
                    result = self._conflict_refine(self.trace_tree.to_str(), tap_checking_result.get("多值冲突", []), self._current_run.get("device_knowledge", ""))
                    if debug:
                        self._log_debug(f"多值冲突精化结果: {result}")
                elif  len(tap_checking_result.get("冗余冲突", [])) > 0:
                    if debug:
                        self._log_debug("发现存在冗余冲突...")
                    pass
                else:
                    pass
            # 在这里对双向需求追溯树进行修改：
            stats = self._apply_refinement_results(result, debug=debug)
            if debug:
                self._log_debug(f"追溯树精化修改统计: {stats}")
                try:
                    self.trace_tree.print_tree()
                except Exception:
                    pass

            if not test_flag:
                self.bus.publish(
                    "trace_tree.update_refinement",
                    {"trace_tree": self.trace_tree.to_dict(), "source": "RealizationPlanner"},
                )

            emit_agent_status(
                "RealizationPlanner",
                "finished",
                "规划实现Agent精化处理完成",
                {"mode": "refinement", "node_count": len((self.trace_tree.to_dict() or {}).get("nodes", []))},
            )
            self._log_summary("规划实现Agent: 错误报告处理完成")
            return {"refine_results": result, "refine_stats": stats, "trace_tree": self.trace_tree.to_dict()}

        



    def run_batch(self, final_user_intents: List[str], debug: bool = False, test_flag: bool = False) -> List[Dict[str, Any]]:
        with self.ai_client.usage_scope("RealizationPlanner", scope_type="agent"), get_overhead_recorder().agent_run("RealizationPlanner"):
            emit_agent_status(
                "RealizationPlanner",
                "started",
                "规划实现Agent开始批处理",
                {"mode": "batch", "intent_count": len(final_user_intents)},
            )
            self._log_summary(f"规划实现Agent: 开始批处理，共 {len(final_user_intents)} 条最终用户意图")
            results: List[Dict[str, Any]] = []
            try:
                self.trace_tree = get_shared_trace_tree()
                base_tree_payload = self.trace_tree.to_dict()
                intent_results: List[tuple[int, Dict[str, Any]]] = []
                worker_count = max(1, min(4, len(final_user_intents)))
                scope_snapshot = self.ai_client.get_scope_stack_snapshot()
                overhead_snapshot = get_overhead_recorder().snapshot_context()
                emit_agent_status(
                    "RealizationPlanner",
                    "progress",
                    "执行意图分解、规划实现与规则生成",
                    {"step": "intent_planning", "intent_count": len(final_user_intents)},
                )

                if worker_count == 1:
                    for idx, final_user_intent in enumerate(final_user_intents):
                        if debug:
                            self._log_debug(f"感知到最终用户意图: {final_user_intent}")
                        intent_results.append(
                            (idx, self._run_single_intent_worker(final_user_intent, base_tree_payload, scope_snapshot, debug, overhead_snapshot))
                        )
                else:
                    future_to_idx = {}
                    with ThreadPoolExecutor(max_workers=worker_count) as executor:
                        for idx, final_user_intent in enumerate(final_user_intents):
                            if debug:
                                self._log_debug(f"感知到最终用户意图: {final_user_intent}")
                            future = executor.submit(
                                self._run_single_intent_worker,
                                final_user_intent,
                                base_tree_payload,
                                scope_snapshot,
                                debug,
                                overhead_snapshot,
                            )
                            future_to_idx[future] = idx
                        for future in as_completed(future_to_idx):
                            idx = future_to_idx[future]
                            try:
                                intent_results.append((idx, future.result()))
                            except Exception as e:
                                self._log_error(f"并行处理最终用户意图出错: {e}")
                                self.debug_logger.exception("规划实现Agent: 并行处理最终用户意图失败")
                                intent_results.append(
                                    (
                                        idx,
                                        {
                                            "final_user_intent": final_user_intents[idx],
                                            "atomic_user_intents": [],
                                            "temp_results": [],
                                        },
                                    )
                                )

                intent_results.sort(key=lambda item: item[0])
                emit_agent_status(
                    "RealizationPlanner",
                    "progress",
                    "执行实现可满足性检查",
                    {"step": "realization_satisfaction_check", "intent_count": len(final_user_intents)},
                )
                for _, intent_result in intent_results:
                    final_user_intent = intent_result.get("final_user_intent", "")
                    atomic_user_intents = intent_result.get("atomic_user_intents", [])
                    temp_results = intent_result.get("temp_results", [])
                    forward_chains = self._merge_temp_results_to_tree(
                        final_user_intent,
                        atomic_user_intents if isinstance(atomic_user_intents, list) else [],
                        temp_results if isinstance(temp_results, list) else [],
                        debug=debug,
                    )

                    try:
                        self.trace_tree.merge_duplicate_nodes()
                    except Exception as e:
                        self._log_debug(f"合并重复节点出错: {e}")

                    if debug:
                        self.trace_tree.print_tree()

                    results.append({
                        "forward_chains": forward_chains,
                        "trace_tree": self.trace_tree.to_dict()
                    })

                if not test_flag:
                    self.bus.publish('trace_tree.update_realization', {'trace_tree': self.trace_tree.to_dict(), "source": "RealizationPlanner"})
                if debug:
                    self.trace_tree.print_tree()
                emit_agent_status(
                    "RealizationPlanner",
                    "finished",
                    "规划实现Agent批处理完成",
                    {"mode": "batch", "intent_count": len(final_user_intents), "result_count": len(results)},
                )
                self._log_summary("规划实现Agent: 批处理完成")
                return results
            except Exception as e:
                emit_agent_status(
                    "RealizationPlanner",
                    "failed",
                    "规划实现Agent批处理失败",
                    {"mode": "batch", "error": str(e)},
                    level="error",
                )
                self._log_error(f"规划实现Agent运行出错: {e}")
                self.debug_logger.exception("规划实现Agent: 批处理异常")
                return [{"error": str(e)}]

    def run(self, final_user_intent: str, debug: bool = False, test_flag: bool = False) -> Dict[str, Any]:
        with self.ai_client.usage_scope("RealizationPlanner", scope_type="agent"), get_overhead_recorder().agent_run("RealizationPlanner"):
            emit_agent_status("RealizationPlanner", "started", "规划实现Agent开始处理单条最终用户意图", {"mode": "single"})
            self._log_summary("规划实现Agent: 开始处理单条最终用户意图")
            if debug:
                self._log_debug(f"感知到最终用户意图: {final_user_intent}")
            
            try:
                self._begin_run(final_user_intent)

                atomic_user_intents = self.intent_refinement(final_user_intent, debug)
                temp_results: List[Dict[str, Any]] = []
                for atomic_user_intent in atomic_user_intents:
                    temp_results.extend(self._process_atomic_intent(final_user_intent, atomic_user_intent, debug))

                final_res = self._merge_temp_results_to_tree(
                    final_user_intent,
                    atomic_user_intents,
                    temp_results,
                    debug=debug,
                )

                try:
                    self.trace_tree.merge_duplicate_nodes()
                except Exception as e:
                    self._log_debug(f"合并重复节点出错: {e}")

                if not test_flag:
                    self.bus.publish('trace_tree.update_realization', {'trace_tree': self.trace_tree.to_dict(), 'source': 'RealizationPlanner'})
                
                if debug:
                    try:
                        self.trace_tree.print_tree()
                    except Exception as e:
                        self._log_debug(f"打印跟踪树出错: {e}")

                final_result = {
                    "forward_chains": final_res,
                    "trace_tree": self.trace_tree.to_dict()
                }
                emit_agent_status(
                    "RealizationPlanner",
                    "finished",
                    "规划实现Agent单条最终用户意图处理完成",
                    {"mode": "single", "node_count": len((final_result["trace_tree"] or {}).get("nodes", []))},
                )
                self._log_summary("规划实现Agent: 单条最终用户意图处理完成")
                return final_result

            except Exception as e:
                emit_agent_status(
                    "RealizationPlanner",
                    "failed",
                    "规划实现Agent单条最终用户意图处理失败",
                    {"mode": "single", "error": str(e)},
                    level="error",
                )
                self.debug_logger.exception("规划实现Agent: 单条最终用户意图处理失败")
                raise e
