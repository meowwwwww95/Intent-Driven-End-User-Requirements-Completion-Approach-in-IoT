import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from evaluator.rule_set_metrics import (
    evaluate_intent_coverage_by_mapping,
    evaluate_rule_completion,
    evaluate_rule_conflict_rate,
    evaluate_rule_hallucination,
    evaluate_implementation_diversity_rate,
)


DEVICE_PROFILE = [
    {
        "name": "cooling_unit_a",
        "type": "air_conditioner",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到“制冷模式”、“制热模式”或是“已关闭”",
                "capabilityType": "trigger",
            },
            {"name": "turn_cool_on()", "description": "", "capabilityType": "action"},
            {"name": "turn_heat_on()", "description": "", "capabilityType": "action"},
            {"name": "turn_off()", "description": "", "capabilityType": "action"},
        ],
    },
        {
        "name": "cooling_unit_b",
        "type": "air_conditioner",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到“制冷模式”、“制热模式”或是“已关闭”",
                "capabilityType": "trigger",
            },
            {"name": "turn_cool_on()", "description": "", "capabilityType": "action"},
            {"name": "turn_heat_on()", "description": "", "capabilityType": "action"},
            {"name": "turn_off()", "description": "", "capabilityType": "action"},
        ],
    },
    {
        "name": "weather_sensor_a",
        "type": "temperature_sensor",
        "capabilities": [
            {"name": "reading()", "description": "可以获取到当前的温度", "capabilityType": "trigger"}
        ],
    },
    {
        "name": "motion_detector_pro",
        "type": "human_sensor",
        "capabilities": [
            {
                "name": "reading()",
                "description": '可以获取到当前是否有人，若有人，则返回"occupied"，否则返回"vacant"',
                "capabilityType": "trigger",
            }
        ],
    },
    {
        "name": "smoke_detector_x",
        "type": "smoke_sensor",
        "capabilities": [
            {
                "name": "reading()",
                "description": "可以获取到当前的烟雾浓度",
                "capabilityType": "trigger",
            }
        ],
    },
    {
        "name": "siren_unit_y",
        "type": "alarm",
        "capabilities": [
            {"name": "turn_on()", "description": "开始报警", "capabilityType": "action"}
        ],
    },
]


def main() -> None:
    gold_user_intents = [
        "Preferred temperature is 30",
        "IF smoke is detected THEN activate the siren",
    ]
    mapping_relationship = [
        {
            "user_intent": "Preferred temperature is 30",
            "realization_taps": [
                [
                    "IF weather_sensor_a.reading() > 30 THEN turn_cool_on() cooling_unit_a",
                    "IF weather_sensor_a.reading() < 30 THEN turn_heat_on() cooling_unit_a"
                ],
                [
                    "IF weather_sensor_a.reading() > 30 THEN turn_cool_on() cooling_unit_b",
                    "IF weather_sensor_a.reading() < 30 THEN turn_heat_on() cooling_unit_b"
                ],
            ],
        },
        {
            "user_intent": "IF smoke is detected THEN activate the siren",
            "realization_taps": [
                ["IF smoke_detector_x.reading()>0.35 THEN turn_on() siren_unit_y"],
            ],
        },
    ]
    gold_rules = [
        "IF weather_sensor_a.reading() > 30 THEN turn_cool_on() cooling_unit_a",
        "IF weather_sensor_a.reading() < 30 THEN turn_heat_on() cooling_unit_a",
        "IF weather_sensor_a.reading() > 30 THEN turn_cool_on() cooling_unit_b",
        "IF weather_sensor_a.reading() < 30 THEN turn_heat_on() cooling_unit_b",
        "IF smoke_detector_x.reading() > 0.35 THEN turn_on() siren_unit_y",    
    ]
    pred_rules = [
        "IF weather_sensor_a.reading() > 30 THEN turn_cool_on() cooling_unit_a",
        "IF weather_sensor_a.reading() < 30 THEN turn_heat_on() cooling_unit_a",
        "IF smoke_detector_x.reading() > 0.35 THEN turn_on() siren_unit_y",
    ]

    intent_summary = evaluate_intent_coverage_by_mapping(
        gold_user_intents=gold_user_intents,
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_rules,
        device_profile=DEVICE_PROFILE,
        intent_threshold=1,
        rule_threshold=0.95,
        mode="summary",
    )
    intent_detailed = evaluate_intent_coverage_by_mapping(
        gold_user_intents=gold_user_intents,
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_rules,
        device_profile=DEVICE_PROFILE,
        intent_threshold=1,
        rule_threshold=0.95,
        mode="detailed",
    )

    completion_summary = evaluate_rule_completion(
        gold_rules,
        pred_rules,
        device_profile=DEVICE_PROFILE,
        threshold=0.95,
        mode="summary",
    )
    completion_detailed = evaluate_rule_completion(
        gold_rules,
        pred_rules,
        device_profile=DEVICE_PROFILE,
        threshold=0.95,
        mode="detailed",
    )
    hallucination_summary = evaluate_rule_hallucination(
        gold_rules,
        pred_rules,
        device_profile=DEVICE_PROFILE,
        threshold=0.95,
        mode="summary",
    )
    hallucination_detailed = evaluate_rule_hallucination(
        gold_rules,
        pred_rules,
        device_profile=DEVICE_PROFILE,
        threshold=0.95,
        mode="detailed",
    )
    impl_div_summary = evaluate_implementation_diversity_rate(
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_rules,
        device_profile=DEVICE_PROFILE,
        rule_threshold=0.95,
        mode="summary",
    )
    impl_div_detailed = evaluate_implementation_diversity_rate(
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_rules,
        device_profile=DEVICE_PROFILE,
        rule_threshold=0.95,
        mode="detailed",
    )
    conflict_summary = evaluate_rule_conflict_rate(pred_rules, mode="summary")
    conflict_detailed = evaluate_rule_conflict_rate(pred_rules, mode="detailed")

    print("INTENT_SUMMARY")
    print(json.dumps(intent_summary, ensure_ascii=False, indent=2))
    print("INTENT_DETAILED")
    print(json.dumps(intent_detailed, ensure_ascii=False, indent=2))
    print("COMPLETION_SUMMARY")
    print(json.dumps(completion_summary, ensure_ascii=False, indent=2))
    print("COMPLETION_DETAILED")
    print(json.dumps(completion_detailed, ensure_ascii=False, indent=2))
    print("HALLUCINATION_SUMMARY")
    print(json.dumps(hallucination_summary, ensure_ascii=False, indent=2))
    print("HALLUCINATION_DETAILED")
    print(json.dumps(hallucination_detailed, ensure_ascii=False, indent=2))
    print("IMPLEMENTATION_DIVERSITY_SUMMARY")
    print(json.dumps(impl_div_summary, ensure_ascii=False, indent=2))
    print("IMPLEMENTATION_DIVERSITY_DETAILED")
    print(json.dumps(impl_div_detailed, ensure_ascii=False, indent=2))
    print("CONFLICT_SUMMARY")
    print(json.dumps(conflict_summary, ensure_ascii=False, indent=2))
    print("CONFLICT_DETAILED")
    print(json.dumps(conflict_detailed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
