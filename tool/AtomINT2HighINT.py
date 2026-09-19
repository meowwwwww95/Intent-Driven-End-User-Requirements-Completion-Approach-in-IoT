#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原子意图到复杂意图的抽象工具

本模块提供将原子意图（AtomINT）列表抽象为高层意图（HighINT）的功能。
使用 AI 模型进行语义分析和抽象。
"""

import json
from typing import List, Dict, Any, Optional
import sys
import os

# 添加项目根目录到路径，以便导入 config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.ai_client import get_default_ai_client, create_system_message, create_user_message

def abstract_atom_ints(atom_ints: List[str], debug: bool = False) -> Dict[str, Any]:
    """
    将原子意图列表抽象为复杂意图。

    Args:
        atom_ints (List[str]): 原子意图列表，每个元素为 "IF ... THEN ..." 格式的字符串。
        debug (bool): 是否打印调试信息。

    Returns:
        Dict[str, Any]: 包含复杂意图和抽象关系的字典。
            格式示例:
            {
                "high_level_intents": [
                        {"id": "HI_1", "description": "保持室内光线充足"}
                ],
                "abstraction_map": {
                    "HI_1": ["IF 光照 < 100 THEN 开灯", ...]
                }
            }
    """
    if not atom_ints:
        return {"high_level_intents": [], "abstraction_map": {}}

    client = get_default_ai_client()

    # 高层意图限定为以下三种表述格式，属方法设计约定
    system_prompt = """
# 任务
你是一个智能家居意图抽象专家。你的任务是将一系列低层次的“原子意图”（格式为 IF trigger THEN action）抽象为更高层次、更复杂的意图。
原子意图通常描述了在某具体环境状态和人类行为下，期望执行的具体效果。
复杂意图应该描述用户想要达到的状态或目标。一条复杂意图可能涉及多个原子意图的组合。复杂意图必须为以下三种格式之一：
1. 期望 <环境属性> 处于 <具体值/范围>
2. <环境属性> 应 高于/低于 <具体值>
3. 如果 <环境属性/人类行为> (不)为 <具体值>，那么 <执行具体效果> 

请分析输入的原子意图列表，找出它们背后的共同目标或逻辑联系，将其归纳为若干个复杂意图。
对于某个环境属性，进行如下考量：
- 对于温度、湿度、亮度、声音这类刺激感官的环境属性，即使当前只看到单侧原子意图（例如 IF 温度 > 33 THEN 降低 温度），也仍然优先抽象为“期望 温度 处于 33”这类格式1，而不是“温度 应 低于 33”。
- 空气质量等涉及身体健康的环境属性，优先考虑格式2的意图，即 <环境属性> 应 高于/低于 <具体值>。
对于其他情况，根据具体场景和用户需求，灵活选择合适的意图格式。例如，涉及人类行为的，优先考虑格式3的意图。

# 输出格式
输出必须是严格的 JSON 格式，包含以下字段：
1. `high_level_intents`: 一个列表，每个元素包含：
    - `id`: 复杂意图的唯一标识符（如 "HI_1", "HI_2"）。
    - `description`: 复杂意图的自然语言描述。
2. `abstraction_map`: 一个字典，键是复杂意图的 `id`，值是该意图所包含的原子意图列表（必须完全匹配输入中的字符串）。

# 案例1
- 输入: ["IF 温度 < 15 THEN 升高 温度"]
- 输出:
    {
        "high_level_intents": [
            {"id": "HI_1", "description": "期望 温度 处于 15"}
        ],
        "abstraction_map": {
            "HI_1": ["IF 温度 < 15 THEN 升高 温度"]
        }
    }

# 案例2
- 输入: ["IF 温度 < 20 THEN 升高 温度", "IF 温度 > 24 THEN 降低 温度"]
- 输出:
    {
        "high_level_intents": [
            {"id": "HI_1", "description": "期望 温度 处于 20-24"}
        ],
        "abstraction_map": {
            "HI_1": ["IF 温度 < 20 THEN 升高 温度", "IF 温度 > 24 THEN 降低 温度"]
        }
    }

# 案例3
- 输入: ["IF 亮度 < 100 THEN 升高 亮度", "IF 亮度 > 180 THEN 降低 亮度", "IF 人回家 THEN 升高 亮度"]
- 输出:
    {
        "high_level_intents": [
            {"id": "HI_1", "description": "期望 亮度 处于 100-180"}
            {"id": "HI_2", "description": "如果 人回家 THEN 升高 亮度到合适的水平"}
        ],
        "abstraction_map": {
            "HI_1": ["IF 亮度 < 100 THEN 升高 亮度", "IF 亮度 > 180 THEN 降低 亮度"]
            "HI_2": ["IF 人回家 THEN 升高 亮度"]
        }
    }

# 案例4
- 输入: ["IF 温度 > 33 THEN 降低 温度"]
- 输出:
    {
        "high_level_intents": [
            {"id": "HI_1", "description": "期望 温度 处于 33"}
        ],
        "abstraction_map": {
            "HI_1": ["IF 温度 > 33 THEN 降低 温度"]
        }
    }

# 案例5
- 输入: ["IF 臭氧浓度 > 0.18 THEN 降低 臭氧浓度"]
- 输出:
    {
        "high_level_intents": [
            {"id": "HI_1", "description": "臭氧浓度 应 低于 0.18"}
        ],
        "abstraction_map": {
            "HI_1": ["IF 臭氧浓度 > 0.18 THEN 降低 臭氧浓度"]
        }
    }

# 限制
- 确保每个输入的原子意图都被映射到有且仅有一个复杂意图（如果无法合并，可以单独形成一个复杂意图，但绝对不允许遗漏任何输入原子意图）。
- `abstraction_map` 必须覆盖所有输入原子意图；禁止丢弃、合并后遗漏、或只保留其中一部分。
- 如果某条原子意图和其它意图难以合并，也必须单独保留一个复杂意图来承接它。
- 描述应简洁明了，体现用户的真实需求，结果应该量化到具体值，不能是模糊的描述。
- 复杂意图格式只能是上方提到的三种之一，不允许出现其它格式
- 不允许出现 '期望 XX 处于 低于 60' 这样的意图，应根据环境属性类型改为 '期望 XX 处于 60'(刺激感官的环境属性) 或 'XX 应 低于 60'(涉及健康的环境属性)。
"""

    user_prompt = f"请对以下原子意图进行抽象：\n{json.dumps(atom_ints, ensure_ascii=False, indent=2)}"

    messages = [
        create_system_message(system_prompt),
        create_user_message(user_prompt)
    ]

    if debug:
        print(f"正在请求 AI 进行意图抽象，共 {len(atom_ints)} 条原子意图...")

    try:
        with client.usage_scope("AtomINT2HighINT.abstract_atom_ints", scope_type="tool"):
            # 使用 retry 机制调用 AI，并要求 JSON 输出
            answer = client.simple_completion(
                messages_input=messages,
                use_json=True
            )
            
            # 解析 JSON 结果
            result = json.loads(answer)
            
            if debug:
                print("AI 响应解析成功。")
                
            return result

    except Exception as e:
        print(f"意图抽象过程中发生错误: {e}")
        # 出错时返回空结果或根据需求处理
        return {
            "high_level_intents": [],
            "abstraction_map": {},
            "error": str(e)
        }

if __name__ == "__main__":
    # 简单的测试代码
    test_atom_ints = [
        "IF 光照强度 < 100 THEN 打开 客厅灯",
        "IF 人体存在 = 人回家 THEN 打开 客厅灯",
        "IF 光照强度 > 500 THEN 关闭 客厅灯",
        "IF 温度 > 33 THEN 打开制冷 空调",
        "IF 温度 < 15 THEN 打开制热 空调"
    ]
    
    print("开始测试 abstract_atom_ints...")
    result = abstract_atom_ints(test_atom_ints, debug=True)
    print("\n抽象结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
