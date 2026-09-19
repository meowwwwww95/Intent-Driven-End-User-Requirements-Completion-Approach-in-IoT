import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from config.ai_client import create_system_message, create_user_message, simple_completion

weights_matrix = {
    "syntax": 0.2,
    "entity": 0.3,
    "intent": 0.5
}

@dataclass(frozen=True)
class TapCondition:
    device: str
    capability: str
    operator: str
    value: str


@dataclass(frozen=True)
class TapAction:
    capability: str
    device: str


def _normalize_capability_name(name: str) -> str:
    s = (name or "").strip()
    if s.endswith("()"):
        s = s[:-2]
    return s


def parse_tap_rule(tap: str) -> Dict[str, Any]:
    """
    解析 TAP 规则为结构化结果。

    支持格式：
    - IF <cond1> AND <cond2> THEN <action> <device>
    - 条件：device.capability <op> value
    - 动作：<capability> <device>
    """
    ret: Dict[str, Any] = {"success": False, "conditions": [], "action": None, "error": None}
    if not isinstance(tap, str) or not tap.strip():
        ret["error"] = "empty_input"
        return ret

    m = re.search(r"^\s*IF\s*(.*?)\s*THEN\s*(.*?)\s*$", tap.strip(), flags=re.IGNORECASE)
    if not m:
        ret["error"] = "missing_if_then"
        return ret

    trigger_str = m.group(1).strip()
    action_str = m.group(2).strip()
    if not trigger_str or not action_str:
        ret["error"] = "empty_trigger_or_action"
        return ret

    parts = re.split(r"\s+AND\s+", trigger_str, flags=re.IGNORECASE)
    conds: List[TapCondition] = []
    for part in parts:
        part = part.strip()
        mm = re.search(
            r"^([^\.\s]+)\.([^\s]+)\s*(==|=|!=|<=|>=|<|>)\s*(\"(?:\\\"|[^\"])*\"|'(?:\\'|[^'])*'|[^\s]+)\s*$",
            part,
        )
        if not mm:
            ret["error"] = f"bad_condition:{part}"
            return ret

        value = mm.group(4)
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        conds.append(
            TapCondition(
                device=mm.group(1),
                capability=mm.group(2),
                operator=mm.group(3),
                value=value,
            )
        )

    action_parts = action_str.split()
    if len(action_parts) < 2:
        ret["error"] = "bad_action"
        return ret

    action = TapAction(capability=action_parts[0], device=" ".join(action_parts[1:]))
    ret["conditions"] = [c.__dict__ for c in conds]
    ret["action"] = action.__dict__
    ret["success"] = True
    return ret


def _build_device_capability_index(device_profile: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    从设备画像构建索引：
    - idx[device_name][capability_name] = capabilityType(trigger/action)
    """
    idx: Dict[str, Dict[str, str]] = {}
    for d in device_profile or []:
        device_name = str(d.get("name", "")).strip()
        if not device_name:
            continue
        cap_map: Dict[str, str] = {}
        for c in d.get("capabilities", []) or []:
            cap_name = str(c.get("name", "")).strip()
            cap_type = str(c.get("capabilityType", "")).strip()
            if cap_name:
                cap_map[_normalize_capability_name(cap_name)] = cap_type
        idx[device_name] = cap_map
    return idx


def _entity_consistency_score(parsed: Dict[str, Any], device_profile: List[Dict[str, Any]]) -> Tuple[float, List[str]]:
    """
    实体一致性：检查 TAP 里出现的 device/capability 是否存在于设备画像中，且：
    - 条件中的 capabilityType 必须是 trigger
    - 动作中的 capabilityType 必须是 action

    返回：(score, errors)
    - score：通过项 / 总项（条件数 + 1个动作）
    - errors：不一致的原因列表
    """
    if not parsed.get("success"):
        return 0.0, ["tap_parse_failed"]

    idx = _build_device_capability_index(device_profile)
    total = 0
    ok = 0
    errors: List[str] = []

    for c in parsed.get("conditions", []) or []:
        total += 1
        dev = str(c.get("device", "")).strip()
        cap_raw = str(c.get("capability", "")).strip()
        cap = _normalize_capability_name(cap_raw)
        if dev not in idx:
            errors.append(f"unknown_device_in_condition:{dev}")
            continue
        if cap not in idx[dev]:
            errors.append(f"unknown_capability_in_condition:{dev}.{cap_raw}")
            continue
        if idx[dev][cap] and idx[dev][cap] != "trigger":
            errors.append(f"non_trigger_capability_in_condition:{dev}.{cap_raw}")
            continue
        ok += 1

    action = parsed.get("action") or {}
    total += 1
    act_dev = str(action.get("device", "")).strip()
    act_cap_raw = str(action.get("capability", "")).strip()
    act_cap = _normalize_capability_name(act_cap_raw)
    if act_dev not in idx:
        errors.append(f"unknown_device_in_action:{act_dev}")
    elif act_cap not in idx[act_dev]:
        errors.append(f"unknown_capability_in_action:{act_dev}.{act_cap_raw}")
    elif idx[act_dev][act_cap] and idx[act_dev][act_cap] != "action":
        errors.append(f"non_action_capability_in_action:{act_dev}.{act_cap_raw}")
    else:
        ok += 1

    if total <= 0:
        return 0.0, ["no_entities"]
    return ok / total, errors


def _intent_consistency_by_llm(pred_tap: str, gold_tap: str) -> Dict[str, Any]:
    """
    意图一致性：用大模型对“生成规则”与“标准答案”是否表达同一意图做判别。

    约定：
    - 只要设备/能力/阈值/比较关系/动作之一发生关键差异，应判不一致
    - 输出 JSON，尽量给出 0~1 的连续分数（同一意图≈1，明显不一致≈0）
    """
    system_prompt = """
# 任务
你是一个严格的TAP规则意图一致性评估器。你需要判断“生成规则”是否与“标准答案规则”表达同一意图。
意图包括：触发条件(设备/能力/阈值/比较关系)与动作(设备/能力)。
请忽略无关的空格大小写差异；从意图角度判断是否一致。
例如：
标准答案为：IF weather_sensor_a.reading()>30 THEN turn_cool_on() cooling_unit_b
它的意图就是温度太高时，自动开启空调降温。

那么对于
生成规则为：IF weather_sensor_a.reading()>32 THEN turn_cool_on() cooling_unit_b
它的意图也是温度太高时，自动开启空调降温。
即使阈值不同，但意图是一致的。因此，判断这两条规则的意图是一致的。

但是对于
生成规则为：IF weather_sensor_a.reading()<28 THEN turn_heat_on() cooling_unit_b
它的意图是温度太低时，自动开启空调升温。
他们想表达的意图不同。因此，判断这两条规则的意图是不一致的。

# 输出格式
只输出JSON对象，格式为：
{"same_intent": true/false, "score": 0到1之间的小数, "rationale": "一句话理由"}。
"""
    
    user_prompt = json.dumps(
        {"gold_rule": gold_tap, "pred_rule": pred_tap},
        ensure_ascii=False,
        indent=2,
    )
    messages = [create_system_message(system_prompt), create_user_message(user_prompt)]
    raw = simple_completion(messages_input=messages, use_json=True)
    data = json.loads(raw)
    score: Optional[float] = None
    if isinstance(data.get("score"), (int, float)):
        score = float(data["score"])
    elif isinstance(data.get("same_intent"), bool):
        score = 1.0 if data["same_intent"] else 0.0
    if score is None:
        score = 0.0
        if "rationale" not in data or data.get("rationale") in (None, ""):
            data["rationale"] = "missing_score_or_same_intent"
    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "rationale": data.get("rationale"),
        "raw": data,
    }


def evaluate_tap_rule_similarity(
    pred_tap: str,
    gold_tap: str,
    device_profile: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    评估两条 TAP 规则的相似程度（0~1）。

    三个维度：
    - syntax：语法正确性（pred 能否被 parse_tap_rule 成功解析）
    - entity：实体一致性（pred 是否正确使用 device_profile 的设备与能力）
    - intent：意图一致性（用大模型判断 pred 与 gold 是否同一意图）

    overall 为三者加权平均
    device_profile 必须由调用方显式传入。
    """
    weights = weights or weights_matrix
    w_syntax = float(weights.get("syntax", 0.25))
    w_entity = float(weights.get("entity", 0.3))
    w_intent = float(weights.get("intent", 0.5))
    w_sum = w_syntax + w_entity + w_intent
    if w_sum <= 0:
        w_syntax, w_entity, w_intent, w_sum = 0.2, 0.3, 0.5, 1.0

    parsed_pred = parse_tap_rule(pred_tap)
    parsed_gold = parse_tap_rule(gold_tap)

    syntax_score = 1.0 if parsed_pred.get("success") else 0.0

    entity_score, entity_errors = _entity_consistency_score(parsed_pred, device_profile)

    intent_detail: Dict[str, Any]
    if not parsed_pred.get("success") or not parsed_gold.get("success"):
        intent_detail = {"score": 0.0, "rationale": "tap_parse_failed", "raw": None}
    else:
        try:
            intent_detail = _intent_consistency_by_llm(pred_tap, gold_tap)
        except Exception as e:
            intent_detail = {"score": 0.0, "rationale": f"llm_error:{e}", "raw": None}

    overall = (w_syntax * syntax_score + w_entity * entity_score + w_intent * float(intent_detail["score"])) / w_sum

    return {
        "pred": {"tap": pred_tap, "parse": parsed_pred},
        "gold": {"tap": gold_tap, "parse": parsed_gold},
        "scores": {
            "syntax": syntax_score,
            "entity": entity_score,
            "intent": float(intent_detail["score"]),
            "overall": overall,
            "weights": {"syntax": w_syntax, "entity": w_entity, "intent": w_intent},
        },
        "details": {
            "entity_errors": entity_errors,
            "intent": intent_detail,
        },
    }


__all__ = ["parse_tap_rule", "evaluate_tap_rule_similarity"]
