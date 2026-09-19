from concurrent.futures import ThreadPoolExecutor, as_completed
from doctest import debug
import json
import re
from typing import List, Dict, Any

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.ai_client import get_default_ai_client, simple_completion, create_system_message, create_user_message, create_assistant_message
from model.trace_tree_bus import get_available_devices
from tool.TAP_extraction import element_pick


# 旧路径保留如下，当前不再使用：
# DEFAULT_EXAMPLES = [
#     """
#     用户输入: IF 亮度 低于 10lux, THEN 升高 亮度
#     推理过程:
#     1. 提取意图实例中的要素：亮度
#     2. 匹配的触发条件和动作：
#        - 触发条件：光照感知设备可以读取环境亮度，支持判断亮度是否大于或小于指定亮度
#        - 动作：光照加亮设备可以打开来增加亮度
#     3. 生成的规则：
#        - IF 光照感知设备.获取读数() < 10 THEN 打开() 光照加亮设备
#     最终输出：
#     <result>
#     IF 光照感知设备.获取读数() < 10 THEN 打开() 光照加亮设备
#     </result>
#     """,
#     """
#     用户输入: IF 人在家 THEN 提升室内照明
#     推理过程:
#     1. 提取意图实例中的要素：人在家，亮度
#     2. 匹配的触发条件和动作：
#        - 触发条件：人体感知设备可以读取环境是否有人在家，支持判断是否有人在家
#        - 动作：光照加亮设备可以打开来增强室内照明
#     3. 生成的规则：
#        - IF 人体感知设备.获取读数() == 人进入 THEN 打开() 光照加亮设备
#     最终输出：
#     <result>
#     IF 人体感知设备.获取读数() == 人进入 THEN 打开() 光照加亮设备
#     </result>
#     """
# ]
#
#
# def _build_prompt_template(triggers_knowledge: str, actions_knowledge: str, examples: List[Dict[str, Any]] | None = None) -> str:
#     if examples is None:
#         examples = DEFAULT_EXAMPLES
#     examples_text = ""
#     for example in examples:
#         example_text = example.strip()
#         examples_text += example_text + "\n\n"
#     template = f"""
# # 任务
# 你是一个经验丰富的智能家居设备调度师。请根据例子的格式和实际语义来输出正确的意图转换结果，按照处理流程一步一步进行思考。
#
# # 处理流程
# 1. 提取意图中的要素（环境属性/效果/功能/人行为/设备状态动作，不含位置信息）
#    - 无相关上下文时用'null'表示
# 2. 根据给定的触发条件和动作，从用户感觉出发，选择能实现用户意图的出发条件或动作。动作可以是直接或是间接的，只要能让用户感觉他的意图能满足即可。
#    - 如果没有能匹配的触发条件或动作, 你需要根据要素抽取结果, 从可用的触发条件和动作中选择最相关的一个。如果都不相关，那么你得将其置为“null”。
# 3. 输出最终实现方案：
#    - 最终输出的结果用"<result>"和"</result>"包裹起来。
#
# # 限制
# - 要求生成的规则必须能够满足输入的用户意图。如果没有动作能满足用户意图，那么你得将动作部分置为“null”。
# - 输出格式应该严格按照：IF <触发设备>.<设备能力> <操作符> <触发值> THEN <设备能力> <动作设备> 的形式
# - 如果当前已经生成的规则中已经出现过相同的规则，那么你需要重新思考，从用户的角度出发，是否有其他的触发条件或动作组合能够通过间接方式以让用户感觉到相同的效果。如果没有，那么最终实现方案输出为null
# - 你必须使用给定的触发条件和动作。其中，
# 触发条件包括:
# {triggers_knowledge}
# 动作包括:
# {actions_knowledge}
#
# # 参考示例
# {examples_text}
# """
#     return template
#
#
# def _build_reflection_prompt_template(triggers_knowledge: str, actions_knowledge: str) -> str:
#     template = f"""
# # 任务
# 请根据给定的触发条件和动作，反思一下生成的规则中是否存在使用不在给定列表中的触发条件或动作的情况。如果有，将其进行修正。
#
# 触发条件包括：
# {triggers_knowledge}
# 动作包括：
# {actions_knowledge}
#
# # 限制
# - 如果动作部分为“null”，那么无需修改规则。
# - 规则应该严格按照：IF <触发设备>.<设备能力> <操作符> <触发值> THEN <设备能力> <动作设备> 的形式。其中，操作符必须为: '==', '!=', '<', '<=', '>', '>='；设备能力后应当包含括号'()'。
# - 规则中的触发值应当尽量参考触发条件列表中的描述，例如人体感知设备使用获取读数能力时，其触发值应当为'人进入'或'人离开'。
# - 规则中的触发值和动作值都应该为抽象能力名，最好翻译成中文，例如'打开'、'关闭'等。
# """
#     return template
#
# def _extract_final_result(response_text: str) -> str:
#     result_start_tag = "<result>"
#     result_end_tag = "</result>"
#     last_start_index = response_text.rfind(result_start_tag)
#     if last_start_index != -1:
#         last_end_index = response_text.find(result_end_tag, last_start_index)
#         if last_end_index != -1:
#             json_part = response_text[last_start_index + len(result_start_tag):last_end_index].strip()
#             if json_part:
#                 return json_part
#     stripped_response = response_text.strip()
#     if stripped_response:
#         return stripped_response
#     raise ValueError(f"无法从响应中提取有效的结果 : {response_text}")

def _validate_rules(rules: List[List[str]], debug: bool = False) -> List[List[str]]:
    if debug:
        print(f"原始规则组: {rules}")
    validated_rules: List[List[str]] = []
    for rule_group in rules:
        valid_group: List[str] = []
        for rule in rule_group:
            if not isinstance(rule, str) or not rule.strip():
                continue
            res = element_pick(rule.strip())
            if res.get("success"):
                valid_group.append(rule)
        if valid_group:
            validated_rules.append(valid_group)
    return validated_rules


def _semantic_filter_rules(
    intent: str,
    rules: List[List[str]],
    abstract_devices: List[Dict[str, Any]],
    debug: bool = False,
    max_workers: int = 4,
) -> List[List[str]]:
    def _format_selected_knowledge(entries: List[Dict[str, Any]], kind: str) -> str:
        lines: List[str] = []
        expected_kind = (kind or "").strip().lower()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            dtype = (entry.get("type") or "").strip()
            caps = [
                c for c in (entry.get("capabilities") or [])
                if isinstance(c, dict) and (c.get("capabilityType") or "").strip().lower() == expected_kind
            ]
            if not dtype or not caps:
                continue
            lines.append(f"{dtype}:")
            fields = ["name", "description"]
            lines.append(f"  capabilities[{len(caps)}]{{{','.join(fields)}}}:")
            for cap in caps:
                cap_name = (cap.get("name") or "").strip()
                cap_desc = (cap.get("description") or "").strip()
                lines.append(f"    {cap_name},{cap_desc}")
        return "\n".join(lines)

    def _knowledge_for_rule(rule_text: str) -> tuple[str, str]:
        parsed = element_pick(rule_text)
        if not parsed.get("success"):
            return "", ""

        trigger_pairs: set[tuple[str, str]] = set()
        action_pairs: set[tuple[str, str]] = set()
        for condition in (parsed.get("conditions") or []):
            if not isinstance(condition, dict):
                continue
            trigger_pairs.add(
                (
                    (condition.get("device") or "").strip(),
                    (condition.get("capability") or "").strip(),
                )
            )
        action = parsed.get("action") or {}
        if isinstance(action, dict):
            action_pairs.add(
                (
                    (action.get("device") or "").strip(),
                    (action.get("capability") or "").strip(),
                )
            )

        trigger_entries: List[Dict[str, Any]] = []
        action_entries: List[Dict[str, Any]] = []
        for device in abstract_devices:
            if not isinstance(device, dict):
                continue
            dtype = (device.get("type") or "").strip()
            if not dtype:
                continue
            trigger_caps: List[Dict[str, Any]] = []
            action_caps: List[Dict[str, Any]] = []
            for cap in (device.get("capabilities") or []):
                if not isinstance(cap, dict):
                    continue
                cap_name = (cap.get("name") or "").strip()
                cap_type = (cap.get("capabilityType") or "").strip().lower()
                pair = (dtype, cap_name)
                if cap_type == "trigger" and pair in trigger_pairs:
                    trigger_caps.append(cap)
                elif cap_type == "action" and pair in action_pairs:
                    action_caps.append(cap)
            if trigger_caps:
                trigger_entries.append({"type": dtype, "capabilities": trigger_caps})
            if action_caps:
                action_entries.append({"type": dtype, "capabilities": action_caps})

        return _format_selected_knowledge(trigger_entries, "trigger"), _format_selected_knowledge(action_entries, "action")

    flat_rules: list[str] = []
    pos_map: list[tuple[int, int]] = []
    for gi, group in enumerate(rules):
        if not isinstance(group, list):
            continue
        for ri, rule in enumerate(group):
            if not isinstance(rule, str):
                continue
            t = rule.strip()
            if not t:
                continue
            flat_rules.append(t)
            pos_map.append((gi, ri))

    if not flat_rules:
        return rules

    def _judge_one_rule(idx: int, rule_text: str) -> Dict[str, Any]:
        rule_triggers_knowledge, rule_actions_knowledge = _knowledge_for_rule(rule_text)
        filter_sys = f"""
# 任务
你是智能家居意图-规则的语义过滤器。给定一个用户意图与一条候选 IRP 规则，请判断该规则是否能从用户视角实现该意图。

# 判断标准
- keep=true：该规则的触发与动作组合，合理地促进/实现意图中的目标效果（允许间接实现，但必须有合理因果或常识关联）。
- keep=false：该规则与意图目标没有合理关联、无法促成目标、或明显相反/降低实现目标的可能性。
- 确保直接实现意图目标：如果规则直接触发/执行意图目标，保持该规则。如果规则间接触发/执行意图目标，则丢弃该规则。
- 不确定时偏保守：如果你无法确定该规则无关，keep=true。

# 约束
- 不要改写规则文本，只做保留/丢弃判断。
- 只输出 JSON：{{"keep":true,"reason":""}}。

# 示例
输入：{{
    "intent": 'IF 空气质量 < 60 THEN 升高 空气质量',
    "rule": 'IF 空气质量感知设备.获取读数(空气质量) < 60 THEN 打开() 空气质量调节设备'
}}
输出:
{{"keep":true,"reason":"打开空气质量调节设备有助于直接升高空气质量，故保留"}}

输入：{{
    "intent": 'IF 湿度 < 60 THEN 升高 湿度',
    "rule": 'IF 湿度感知设备.获取读数(湿度) < 60 THEN 打开() 空气质量调节设备'
}}
输出:
{{"keep":false,"reason":"空气质量调节设备不能直接影响环境湿度，故丢弃"}}


# 当前规则相关的触发条件知识
{rule_triggers_knowledge}

# 当前规则相关的动作知识
{rule_actions_knowledge}
""".strip()
        one_rule_user = json.dumps(
            {"intent": intent, "rule": rule_text},
            ensure_ascii=False,
            indent=2,
        )
        try:
            raw = simple_completion(
                messages_input=[create_system_message(filter_sys), create_user_message(one_rule_user)],
                use_json=True,
                temperature=0.0,
            )
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            parsed = {}

        keep = (parsed or {}).get("keep")
        reason = (parsed or {}).get("reason")
        if debug:
            print(f"判断规则 {idx}: {rule_text} -> {keep} ({reason})")
        return {
            "idx": idx,
            "rule": rule_text,
            "keep": keep,
            "reason": reason if isinstance(reason, str) else "",
            "rule_triggers_knowledge": rule_triggers_knowledge,
            "rule_actions_knowledge": rule_actions_knowledge,
        }

    keep_idx: set[int] = set(range(len(flat_rules)))
    decisions_debug: List[Dict[str, Any]] = []
    worker_count = max(1, min(int(max_workers or 1), len(flat_rules)))
    ai_client = get_default_ai_client()
    scope_snapshot = ai_client.get_scope_stack_snapshot()
    if worker_count == 1:
        decisions_debug = [_judge_one_rule(idx, rule_text) for idx, rule_text in enumerate(flat_rules)]
    else:
        def _judge_one_rule_with_scope(idx: int, rule_text: str) -> Dict[str, Any]:
            with ai_client.inherit_scope_stack(scope_snapshot):
                return _judge_one_rule(idx, rule_text)
        futures = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for idx, rule_text in enumerate(flat_rules):
                future = executor.submit(_judge_one_rule_with_scope, idx, rule_text)
                futures[future] = idx
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    decisions_debug.append(future.result())
                except Exception as e:
                    decisions_debug.append(
                        {
                            "idx": idx,
                            "rule": flat_rules[idx],
                            "keep": True,
                            "reason": f"并行判断异常，按保守策略保留: {e}",
                            "rule_triggers_knowledge": "",
                            "rule_actions_knowledge": "",
                        }
                    )

    decisions_debug.sort(key=lambda item: int(item.get("idx", -1)))
    for decision in decisions_debug:
        idx = decision.get("idx")
        keep = decision.get("keep")
        if isinstance(idx, int) and isinstance(keep, bool):
            if not keep and idx in keep_idx:
                keep_idx.remove(idx)

    # if debug:
    #     print("语义过滤模型返回(parsed):")
    #     print(json.dumps({"decisions": decisions_debug}, ensure_ascii=False, indent=2, sort_keys=True))

    idx_by_pos: dict[tuple[int, int], int] = {}
    for i, pos in enumerate(pos_map):
        idx_by_pos[pos] = i

    out: List[List[str]] = []
    for gi, group in enumerate(rules):
        if not isinstance(group, list):
            continue
        kept_group: list[str] = []
        for ri, rule in enumerate(group):
            if not isinstance(rule, str) or not rule.strip():
                continue
            idx = idx_by_pos.get((gi, ri))
            if idx is None:
                kept_group.append(rule)
                continue
            if idx in keep_idx:
                kept_group.append(rule)
        if kept_group:
            out.append(kept_group)

    if debug:
        removed = len(flat_rules) - sum(len(g) for g in out)
        print(f"语义过滤移除规则数: {removed} / {len(flat_rules)}")

    return out


def _chunk_items_by_size(items: List[Dict[str, Any]], max_chars: int = 12000) -> List[List[Dict[str, Any]]]:
    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_size = 0
    for item in items:
        item_size = len(json.dumps(item, ensure_ascii=False))
        if current_batch and current_size + item_size > max_chars:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(item)
        current_size += item_size
    if current_batch:
        batches.append(current_batch)
    return batches


def _collect_abstract_trigger_action_candidates(abstract_devices: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    triggers: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    seen_triggers: set[tuple[str, str]] = set()
    seen_actions: set[tuple[str, str]] = set()

    for device in abstract_devices:
        if not isinstance(device, dict):
            continue
        device_type = (device.get("type") or "").strip()
        device_desc = (device.get("description") or "").strip()
        if not device_type:
            continue
        for capability in (device.get("capabilities") or []):
            if not isinstance(capability, dict):
                continue
            capability_name = (capability.get("name") or "").strip()
            capability_desc = (capability.get("description") or "").strip()
            capability_type = (capability.get("capabilityType") or "").strip().lower()
            if not capability_name or capability_type not in ("trigger", "action"):
                continue

            item = {
                "device": device_type,
                "device_description": device_desc,
                "capability": capability_name,
                "capability_description": capability_desc,
                "kind": capability_type,
            }
            key = (device_type, capability_name)
            if capability_type == "trigger" and key not in seen_triggers:
                seen_triggers.add(key)
                triggers.append(item)
            elif capability_type == "action" and key not in seen_actions:
                seen_actions.add(key)
                actions.append(item)

    return triggers, actions


def _normalize_trigger_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        text = text[1:-1].strip()
    return text


def _complete_trigger_candidates(intent: str, triggers: List[Dict[str, Any]], debug: bool = False) -> List[Dict[str, Any]]:
    if not triggers:
        return []

    system_prompt = """
# 任务
你是智能家居 IRP 触发条件补全助手。给定原子用户意图与一组抽象 Trigger 候选，请为每个 Trigger 判断是否能够补全为:
<触发设备>.<设备能力> <操作符> <触发值>

# 处理要求
1. 你只能使用输入中已有的触发设备与触发能力，不得改写设备名或能力名。
2. 操作符只能是: ==, !=, <, <=, >, >=
3. 触发值必须由设备能力描述和原子意图共同支持，且要尽量具体、简洁。
4. 如果无法确定合适的操作符或触发值，就将该 Trigger 丢弃。
5. 数值阈值优先从原子意图中提取；枚举值优先使用设备知识里更贴切的中文表达，例如“人进入”“人离开”。

# 输出格式
只输出 JSON:
{
  "results": [
    {
      "idx": 0,
      "keep": true,
      "operator": "==",
      "value": "人进入",
      "reason": "..."
    }
  ]
}

# 示例
原子意图: IF 人在家 THEN 提升室内照明
输入 Trigger: 人体感知设备 / 获取读数() / 可以获取到当前是否有人在家
输出: {"idx":0,"keep":true,"operator":"==","value":"人进入","reason":"人体感知设备可用人进入表达人在家场景"}

原子意图: IF 室内温度高于 28 度 THEN 降低室内温度
输入 Trigger: 温度感知设备 / 获取读数() / 可以获取到当前温度
输出: {"idx":0,"keep":true,"operator":">","value":"28","reason":"意图中给出了明确阈值"}
""".strip()

    completed: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for batch in _chunk_items_by_size(triggers):
        user_payload = json.dumps({"intent": intent, "triggers": batch}, ensure_ascii=False, indent=2)
        try:
            raw = simple_completion(
                messages_input=[create_system_message(system_prompt), create_user_message(user_payload)],
                use_json=True,
                temperature=0.0,
            )
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            parsed = {}

        if debug:
            print("Trigger 补全结果(parsed):")
            print(json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True))

        results = (parsed or {}).get("results") or []
        index_map = {idx: item for idx, item in enumerate(batch)}
        processed_idx: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                continue
            idx = result.get("idx")
            keep = result.get("keep")
            if not isinstance(idx, int) or idx not in index_map:
                continue
            processed_idx.add(idx)
            trigger = dict(index_map[idx])
            reason = (result.get("reason") or "").strip()
            if keep is not True:
                trigger["drop_reason"] = reason or "模型判定无法为该 Trigger 补全操作符和值"
                dropped.append(trigger)
                continue
            operator = (result.get("operator") or "").strip()
            value = _normalize_trigger_value(result.get("value"))
            if operator not in {"==", "!=", "<", "<=", ">", ">="}:
                trigger["drop_reason"] = reason or f"模型返回了非法操作符: {operator}"
                dropped.append(trigger)
                continue
            if not value or value.lower() in {"null", "none", "true", "false"}:
                trigger["drop_reason"] = reason or f"模型返回了不可用的触发值: {value}"
                dropped.append(trigger)
                continue

            trigger["operator"] = operator
            trigger["value"] = value
            trigger["keep_reason"] = reason
            completed.append(trigger)

        for idx, trigger in index_map.items():
            if idx in processed_idx:
                continue
            dropped_trigger = dict(trigger)
            dropped_trigger["drop_reason"] = "模型未返回该 Trigger 的补全结果"
            dropped.append(dropped_trigger)

    if debug:
        print("保留的 Trigger 候选:")
        print(json.dumps(completed, ensure_ascii=False, indent=2))
        print("丢弃的 Trigger 候选:")
        print(json.dumps(dropped, ensure_ascii=False, indent=2))

    return completed


def _build_candidate_rules(
    completed_triggers: List[Dict[str, Any]],
    actions: List[Dict[str, Any]],
) -> List[List[str]]:
    rules: List[List[str]] = []
    seen: set[str] = set()
    for trigger in completed_triggers:
        trigger_device = (trigger.get("device") or "").strip()
        trigger_capability = (trigger.get("capability") or "").strip()
        operator = (trigger.get("operator") or "").strip()
        value = _normalize_trigger_value(trigger.get("value"))
        if not trigger_device or not trigger_capability or not operator or not value:
            continue
        trigger_text = f"{trigger_device}.{trigger_capability} {operator} {value}"
        for action in actions:
            action_device = (action.get("device") or "").strip()
            action_capability = (action.get("capability") or "").strip()
            if not action_device or not action_capability:
                continue
            rule = f"IF {trigger_text} THEN {action_capability} {action_device}"
            if rule in seen:
                continue
            seen.add(rule)
            rules.append([rule])
    return rules


def _semantic_filter_rules_in_batches(
    intent: str,
    rules: List[List[str]],
    abstract_devices: List[Dict[str, Any]],
    debug: bool = False,
    batch_size: int = 40,
    max_workers: int = 4,
) -> List[List[str]]:
    if len(rules) <= batch_size:
        return _semantic_filter_rules(intent, rules, abstract_devices, debug, max_workers=max_workers)

    merged: List[List[str]] = []
    seen: set[str] = set[str]()
    for start in range(0, len(rules), batch_size):
        batch = rules[start:start + batch_size]
        filtered = _semantic_filter_rules(intent, batch, abstract_devices, debug, max_workers=max_workers)
        for group in filtered:
            if not isinstance(group, list):
                continue
            kept_group: List[str] = []
            for rule in group:
                if not isinstance(rule, str):
                    continue
                text = rule.strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                kept_group.append(text)
            if kept_group:
                merged.append(kept_group)
    return merged


def generate_irp_rules(intent: str, debug: bool = False) -> List[List[str]]:
    with get_default_ai_client().usage_scope("AtomINT2IRP.generate_irp_rules", scope_type="tool"):
        devices_profile = get_available_devices()
        abstract_devices = devices_profile.get_abstract_devices() if hasattr(devices_profile, "get_abstract_devices") else []

        trigger_candidates, action_candidates = _collect_abstract_trigger_action_candidates(abstract_devices)
        completed_triggers = _complete_trigger_candidates(intent, trigger_candidates, debug=debug)
        all_rules = _build_candidate_rules(completed_triggers, action_candidates)
        validated_rules = _validate_rules(all_rules, debug)
        validated_rules = _semantic_filter_rules_in_batches(
            intent=intent,
            rules=validated_rules,
            abstract_devices=abstract_devices,
            debug=debug,
        )

        if debug:
            # print("Trigger 候选详情:")
            # print(json.dumps(trigger_candidates, ensure_ascii=False, indent=2))
            # print("Action 候选详情:")
            # print(json.dumps(action_candidates, ensure_ascii=False, indent=2))
            # print("候选规则:")
            # print(json.dumps(all_rules, ensure_ascii=False, indent=2))
            # print(f"抽象设备总数: {len(abstract_devices)}")
            # print(f"Trigger 候选数: {len(trigger_candidates)}")
            print(f"Action 候选数: {len(action_candidates)}")
            print(f"Trigger 候选数: {len(completed_triggers)}")
            print(f"候选规则数: {len(all_rules)}")
            print(f"通过校验与语义过滤的规则数: {len(validated_rules)}")
            print(f"最终规则: {validated_rules}")

    # 旧路径保留如下，当前不再使用：
    # current_actions_knowledge = devices_profile.get_abstract_device_knowledge_str_by_capability_kind("action")
    # all_rules: List[List[str]] = []
    # iteration = 0
    # max_iterations = 10
    # current_rules: List[List[str]] = []
    # last_triggers_knowledge = ""
    # while iteration < max_iterations:
    #     iteration += 1
    #     triggers_knowledge = devices_profile.get_abstract_device_knowledge_str_by_capability_kind("trigger")
    #     last_triggers_knowledge = triggers_knowledge
    #     current_prompt_template = _build_prompt_template(triggers_knowledge, current_actions_knowledge)
    #     full_prompt = [
    #         create_system_message(current_prompt_template),
    #         create_user_message(f"当前待处理的意图实例：{intent}，当前已生成的规则：{current_rules}")
    #     ]
    #     ans = simple_completion(full_prompt)
    #     current_reflection_template = _build_reflection_prompt_template(triggers_knowledge, current_actions_knowledge)
    #     full_prompt.extend([
    #         create_assistant_message(f"{ans}"),
    #         create_system_message(current_reflection_template)
    #     ])
    #     reflection_ans = simple_completion(full_prompt)
    #     try:
    #         final_ans = _extract_final_result(reflection_ans)
    #         if "null" in final_ans:
    #             break
    #         current_rules.append([final_ans])
    #     except (json.JSONDecodeError, ValueError):
    #         break
    # all_rules.extend(current_rules)
    # validated_rules = _validate_rules(all_rules, debug)
    # validated_rules = _semantic_filter_rules(
    #     intent=intent,
    #     rules=validated_rules,
    #     triggers_knowledge=last_triggers_knowledge,
    #     actions_knowledge=current_actions_knowledge,
    #     debug=debug,
    # )
    # return validated_rules

        return validated_rules

__all__ = ["generate_irp_rules"]


