import re
import os
import sys
import json
import traceback
from typing import Any, List
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from model.trace_tree_bus import get_available_devices

_UNIT_TOKEN_HEAD = r"%°℃℉A-Za-z\u4e00-\u9fa5μµΩ"
_UNIT_TOKEN_BODY = r"%°℃℉A-Za-z0-9\u4e00-\u9fa5μµΩ/._-"
_NUMERIC_VALUE_WITH_UNIT_RE = re.compile(
    rf"^\s*(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>[{_UNIT_TOKEN_HEAD}][{_UNIT_TOKEN_BODY}]*(?:\s+[{_UNIT_TOKEN_HEAD}][{_UNIT_TOKEN_BODY}]*)*)\s*$",
    re.IGNORECASE,
)
_CONDITION_NUMERIC_UNIT_RE = re.compile(
    rf"(?P<prefix>(?:[<>]=?|==|!=)\s*)(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>[{_UNIT_TOKEN_HEAD}][{_UNIT_TOKEN_BODY}]*(?:\s+[{_UNIT_TOKEN_HEAD}][{_UNIT_TOKEN_BODY}]*)*)(?=\s+(?:AND|OR)\s+|\s*$)",
    re.IGNORECASE,
)


def _sanitize_condition_value(value: Any) -> str:
    """
    清理触发条件中的数值单位。

    例如：
    - 40 dB -> 40
    - 25 摄氏度 -> 25
    - 60% -> 60
    - 5 mg/m3 -> 5
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return text

    match = _NUMERIC_VALUE_WITH_UNIT_RE.fullmatch(text)
    if match:
        return match.group("num")

    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sanitize_tap_rule(rule_text: str) -> str:
    """
    对最终生成的 TAP 规则做兜底清洗，避免数值单位漏出到 IF 条件中。
    """
    if not isinstance(rule_text, str) or not rule_text.strip():
        return rule_text

    match = re.search(r"^\s*IF\s+(?P<cond>.*?)\s+THEN\s+(?P<act>.*?)\s*$", rule_text, re.IGNORECASE)
    if not match:
        return rule_text

    cond_text = _CONDITION_NUMERIC_UNIT_RE.sub(
        lambda m: f"{m.group('prefix')}{m.group('num')}",
        match.group("cond"),
    )
    cond_text = re.sub(r"\s+", " ", cond_text).strip()
    act_text = match.group("act").strip()
    return f"IF {cond_text} THEN {act_text}"

def _parse_irp(irp: str) -> dict:
    """
    把一条 IRP 文本解析为结构化 dict。

    输入示例：
    - IF Light.brightness > 50 AND Sensor.motion == true THEN turnOn Light

    输出字段：
    - success: 是否解析成功
    - conditions: [{device, capability, operator, value}, ...]
    - action: {capability, device}
    """
    ret = {"success": False, "conditions": [], "action": None}
    m = re.search(r"IF\s*(.*?)\s*THEN\s*(.*?)$", irp.strip(), re.IGNORECASE)
    if not m:
        return ret
    trigger_str = m.group(1)
    action_str = m.group(2)
    parts = re.split(r"\s+AND\s+", trigger_str, flags=re.IGNORECASE)
    for part in parts:
        # 条件支持：device.capability <op> value，其中 value 允许用引号包裹字符串
        mm = re.search(r"([^\.\s]+)\.([^\s]+)\s*([<>!=]+)\s*(\"(?:\\\"|[^\"])*\"|[^\s]+)", part.strip())
        if not mm:
            return ret
        value = mm.group(4)
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        ret["conditions"].append({
            "device": mm.group(1),
            "capability": mm.group(2),
            "operator": mm.group(3),
            "value": value,
        })
    parts = action_str.strip().split()
    if len(parts) < 2:
        return ret
    # 动作部分简化为：<capability> <device/type/instance...>
    ret["action"] = {"capability": parts[0], "device": " ".join(parts[1:])}
    ret["success"] = True
    return ret

def _norm_token(x: str) -> str:
    """
    token 归一化：去掉内部空白、并 strip。
    这里刻意“把空白全部删掉”，用于适配 IRP 中可能出现的多余空格。
    """
    return re.sub(r"\s+", "", (x or "")).strip()

def _ensure_call_syntax(x: str) -> str:
    """
    能力名统一成“调用形式”：
    - 已经是 xxx()：保持
    - 已包含括号：保持（例如 xxx(1)）
    - 否则补成 xxx()
    """
    s = (x or "").strip()
    if not s:
        return s
    if s.endswith("()"):
        return s
    if "(" in s and ")" in s:
        return s
    return f"{s}()"

def generate_tap(irp: str, available_devices: List[str] | None = None, debug: bool = False) -> List[str]:
    """
    主入口：把一条 IRP 文本生成 TAP 列表。

    参数：
    - irp：单条 IRP 字符串（"null"/空串会被忽略）
    - available_devices：可用实例白名单（None 表示不过滤）
    - debug：打印解析/过滤/展开过程，方便定位 profile 映射问题

    返回：
    - TAP 字符串列表
    """

    irp_text = irp.strip()
    if not irp_text or "null" in irp_text.lower():
        if debug:
            print(f"[IRP2TAP] 跳过IRP: irp_text={repr(irp_text)}")
        return []

    def _sample_list(xs: List[str] | None, n: int = 10) -> str:
        if xs is None:
            return "None"
        if not xs:
            return "[]"
        head = xs[:n]
        tail = "" if len(xs) <= n else f"...(+{len(xs) - n})"
        return f"{head}{tail}"

    if debug:
        avail_list = list(available_devices or []) if available_devices is not None else None
        print(f"[IRP2TAP] generate_tap.start: count={len(avail_list or [])}")
        print(f"[IRP2TAP] generate_tap.start: available_devices_sample={_sample_list(avail_list)}")
        print(f"[IRP2TAP] 需要处理的IRP: {irp_text}")

    try:

        parsed = _parse_irp(irp_text)
        if not parsed.get("success"):
            if debug:
                print(f"[IRP2TAP] IRP解析失败: parsed={json.dumps(parsed, ensure_ascii=False)}")
            return []

        if debug:
            print(f"[IRP2TAP] 解析结果: {json.dumps(parsed, ensure_ascii=False)}")

        cond_options: List[List[dict]] = []

        conds_any = parsed.get("conditions") if isinstance(parsed, dict) else None
        if not isinstance(conds_any, list):
            if debug:
                print(f"[IRP2TAP] conditions类型异常: type={type(conds_any)} value={repr(conds_any)}")
            return []

        for idx, cond in enumerate[Any](conds_any):
            if not isinstance(cond, dict):
                if debug:
                    print(f"[IRP2TAP] 条件{idx}: 非dict，跳过该IRP: {repr(cond)}")
                return []
            if debug:
                print(f"[IRP2TAP] 条件{idx}: 原始={json.dumps(cond, ensure_ascii=False)}")

            token = _norm_token(cond.get("device", ""))
            cap_key = _ensure_call_syntax(_norm_token(cond.get("capability", "")))

            inst_filter = list[str](available_devices or []) if available_devices is not None else None
        
            try:
                maps_any = get_available_devices().map_abstract_device_and_capability(
                    device=token,
                    capability=cap_key,
                    kind="trigger",
                    instances=inst_filter,
                    value=cond.get("value"),
                    operator=cond.get("operator"),
                )
            except Exception as e:
                if debug:
                    print(f"[IRP2TAP] 条件{idx}: 映射异常: device_token={token} capability_token={cap_key}")
                    print(traceback.format_exc().rstrip())
                raise e

            if not isinstance(maps_any, list):
                maps_any = []
            maps = [
                m for m in maps_any
                if isinstance(m, dict)
                and (m.get("device_name") or "").strip()
                and (m.get("capability_name") or "").strip()
            ]

            if debug:
                print(f"[IRP2TAP] 条件{idx}: device_token={token} capability_token={cap_key}")
                print(f"[IRP2TAP] 条件{idx}: 过滤后候选数={len(maps)}")
                print(f"[IRP2TAP] 条件{idx}: 候选sample={[(m.get('device_name'), m.get('capability_name')) for m in maps[:10]]}{'...'+str(len(maps)-10) if len(maps)>10 else ''}")

            if not maps:
                if debug:
                    print(f"[IRP2TAP] 条件{idx}: 映射后无可用实例，跳过该IRP")
                return []
            cond_options.append(maps)

        act_any = parsed.get("action") if isinstance(parsed, dict) else None
        if not isinstance(act_any, dict):
            if debug:
                print(f"[IRP2TAP] action类型异常: type={type(act_any)} value={repr(act_any)}")
            return []

        action_token = _norm_token(act_any.get("device", ""))
        action_cap_key = _ensure_call_syntax(_norm_token(act_any.get("capability", "")))
        inst_filter = list[str](available_devices or []) if available_devices is not None else None
        if debug:
            print(f"[IRP2TAP] 动作: inst_filter_is_none={inst_filter is None} count={len(inst_filter or [])}")

        try:
            action_maps_any = get_available_devices().map_abstract_device_and_capability(
                device=action_token,
                capability=action_cap_key,
                kind="action",
                instances=inst_filter,
            )
        except Exception as e:
            if debug:
                print(f"[IRP2TAP] 动作: 映射异常: device_token={action_token} capability_token={action_cap_key}")
                print(traceback.format_exc().rstrip())
            raise e

        if not isinstance(action_maps_any, list):
            action_maps_any = []
        action_maps = [
            m for m in action_maps_any
            if isinstance(m, dict)
            and (m.get("device_name") or "").strip()
            and (m.get("capability_name") or "").strip()
        ]

        if debug:
            print(f"[IRP2TAP] 动作: device_token={action_token} capability_token={action_cap_key}")
            print(f"[IRP2TAP] 动作: 过滤后候选数={len(action_maps)}")
            print(f"[IRP2TAP] 动作: 候选sample={[(m.get('device_name'), m.get('capability_name')) for m in action_maps[:10]]}{'...'+str(len(action_maps)-10) if len(action_maps)>10 else ''}")

        if not action_maps or not cond_options:
            if debug:
                print(f"[IRP2TAP] 条件或动作实例为空，跳过该IRP: cond_options_count={len(cond_options)} action_maps_count={len(action_maps)}")
            return []

        res: List[str] = []

        def build_conditions(choice: List[dict]) -> str:
            parts: List[str] = []
            for idx, picked in enumerate(choice):
                c = conds_any[idx]
                inst = (picked.get("device_name") or "").strip()
                capn = _ensure_call_syntax((picked.get("capability_name") or "").strip())
                picked_value = picked.get("value")
                val = c.get("value")
                if isinstance(picked_value, str) and picked_value.strip():
                    val = picked_value.strip()
                elif picked_value is not None:
                    val = str(picked_value).strip()
                if isinstance(val, str):
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                val = _sanitize_condition_value(val)
                parts.append(f"{inst}.{capn} {c['operator']} {val}")
            return " AND ".join(parts)

        def backtrack(i: int, cur: List[dict]):
            if i == len(cond_options):
                cond_text = build_conditions(cur)
                for am in action_maps:
                    a_inst = (am.get("device_name") or "").strip()
                    a_cap = _ensure_call_syntax((am.get("capability_name") or "").strip())
                    if a_inst and a_cap:
                        res.append(_sanitize_tap_rule(f"IF {cond_text} THEN {a_cap} {a_inst}"))
                return
            for picked in cond_options[i]:
                cur.append(picked)
                backtrack(i + 1, cur)
                cur.pop()

        if debug:
            sizes = [len(x) for x in cond_options]
            total_combo = 1
            for s in sizes:
                total_combo *= max(1, s)
            print(f"[IRP2TAP] 展开准备: conditions={len(cond_options)} sizes={sizes} estimated_combos={total_combo} action_candidates={len(action_maps)}")

        try:
            backtrack(0, [])
        except Exception:
            if debug:
                print("[IRP2TAP] 回溯展开异常")
                print(traceback.format_exc().rstrip())
            raise

        if debug:
            print(f"[IRP2TAP] 该IRP生成TAP数={len(res)}")
            if res:
                print(f"[IRP2TAP] TAP sample={res[:10]}{'...'+str(len(res)-10) if len(res)>10 else ''}")
        return res
    except Exception as e:
        if debug:
            print("[IRP2TAP] generate_tap.fatal")
            print(traceback.format_exc().rstrip())
        raise e
