import json
import re
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


INPUT1 = "IF weather_sensor_a.reading()>30 THEN turn_cool_on() cooling_unit_b"
INPUT2 = "IF weather_sensor_a.reading()>32 AND motion_detector_pro.reading() != occupied THEN turn_cool_on() cooling_unit_b"

GROUND_TRUTH = "IF weather_sensor_a.reading()>30 AND motion_detector_pro.reading() != occupied THEN turn_cool_on() cooling_unit_b"

DEVICE_PROFILE = [
     {
        "name": "cooling_unit_b",
        "type": "air_conditioner",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到“制冷模式”、“制热模式”或是“已关闭”",
                "capabilityType": "trigger"
            },
            {
                "name": "turn_cool_on()",
                "description": "",
                "capabilityType": "action"
            },
            {
                "name": "turn_heat_on()",
                "description": "",
                "capabilityType": "action"
            },
            {
                "name": "turn_off()",
                "description": "",
                "capabilityType": "action"  
            }
        ]
    },
    {
        "name": "weather_sensor_a",
        "type": "temperature_sensor",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到当前的温度",
                "capabilityType": "trigger"
            }
        ]
    },
    {
        "name": "motion_detector_pro",
        "type": "human_sensor",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到当前是否有人，若有人，则返回\"occupied\"，否则返回\"vacant\"",
                "capabilityType": "trigger"
            }
        ]
    }
]

def _parse_TAP(tap: str) -> dict:
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
    m = re.search(r"IF\s*(.*?)\s*THEN\s*(.*?)$", tap.strip(), re.IGNORECASE)
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


def main() -> None:
    from evaluator.rule_similarity import evaluate_tap_rule_similarity

    cases = [
        {
            "name": "缺少安全",
            "pred": INPUT1,
            "gold": GROUND_TRUTH,
        },
        {
            "name": "完全一致",
            "pred": INPUT2,
            "gold": GROUND_TRUTH,
        }
    ]

    for c in cases:
        pred = c["pred"]
        gold = c["gold"]
        res = evaluate_tap_rule_similarity(pred, gold, device_profile=DEVICE_PROFILE)
        print("=" * 80)
        print(c["name"])
        print("- pred:", pred)
        print("- gold:", gold)
        print("- temp._parse_TAP(pred):", json.dumps(_parse_TAP(pred), ensure_ascii=False))
        print("- scores:", json.dumps(res["scores"], ensure_ascii=False))
        if res.get("details", {}).get("entity_errors"):
            print("- entity_errors:", json.dumps(res["details"]["entity_errors"], ensure_ascii=False))


if __name__ == "__main__":
    main()
