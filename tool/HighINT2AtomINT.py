#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复杂意图到原子意图的精化工具

本模块提供将高层意图（HighINT）精化为原子意图（AtomINT）的功能。
输入支持：
1) 高层意图描述字符串列表（建议遵循 AtomINT2HighINT.py 中定义的三种格式）
2) AtomINT2HighINT.py 的输出字典（包含 high_level_intents）
输出为严格 JSON 可解析结构，原子意图使用 "IF ... THEN ..." 字符串列表表示。
"""

import json
from typing import Any, Dict, List, Optional
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.ai_client import get_default_ai_client, create_system_message, create_user_message


def _is_atom_int(s: str) -> bool:
    ss = s.strip()
    if not ss.startswith("IF "):
        return False
    if " THEN " not in ss:
        return False
    parts = ss.split(" THEN ", 1)
    return len(parts) == 2 and parts[0].strip() != "IF" and bool(parts[1].strip())


def _coerce_atom_int_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [x.strip() for x in value.splitlines() if x.strip()]
        return [c for c in candidates if _is_atom_int(c)]
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            if v is None:
                continue
            s = str(v).strip()
            if _is_atom_int(s):
                out.append(s)
        return out
    return []


def _normalize_existing_atom_ints(existing_atom_ints: Optional[List[str]]) -> List[str]:
    if existing_atom_ints is None:
        return []
    if not isinstance(existing_atom_ints, list):
        raise TypeError("existing_atom_ints 必须是 List[str]")

    candidates: List[str] = []
    for v in existing_atom_ints:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            candidates.append(s)

    invalid = [s for s in candidates if not _is_atom_int(s)]
    if invalid:
        raise ValueError(f"existing_atom_ints 存在非法原子意图: {invalid}")

    out: List[str] = []
    seen = set()
    for s in candidates:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def refine_high_ints_to_atom_ints(
    high_int_input: str,
    existing_atom_ints: Optional[List[str]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    将高层意图精化为原子意图。

    Args:
        high_int_input: 高层意图字符串（单条）
        existing_atom_ints: 已有原子意图（List[str]，不传则视为空）
        debug: 是否打印调试信息

    Returns:
        Dict[str, Any]:
            {
                "atom_intents": ["IF ... THEN ...", ...],
                "refinement_map": {"<高层意图原文>": ["IF ... THEN ...", ...], ...}
            }
    """
    high_int = high_int_input.strip() if isinstance(high_int_input, str) else ""
    if not high_int:
        return {"atom_intents": [], "refinement_map": {}}

    existing_atom_int_list = _normalize_existing_atom_ints(existing_atom_ints)

    client = get_default_ai_client()

    # 高层意图限定为以下三种表述格式，属方法设计约定
    system_prompt = """
# 任务
你是一个智能家居意图精化专家。你的任务是将“高层意图（复杂意图）”精化为一个或多个“原子意图”规则。

# 输入
输入是一条高层意图字符串。高层意图通常为以下三种格式之一：
1. 期望 <环境属性> 处于 <具体值/范围>
2. <环境属性> 应 高于/低于 <具体值>
3. 如果 <环境属性/人类行为> (不)为 <具体值>，那么 <执行具体效果>

# 输出
输出必须是严格 JSON，包含字段：
1) atom_intents: 原子意图字符串列表
2) refinement_map: 字典，键为高层意图原始字符串（必须与输入中的字符串完全一致），值为该高层意图对应的原子意图列表

# 额外约束（必须遵守）
- 输入还会提供 existing_atom_intents（已有的原子意图列表）
- 你生成的结果必须保留 existing_atom_intents 中的每一条原子意图，并且原样出现在输出中（不允许改写、删减）
- refinement_map 中该高层意图对应的列表必须包含这些已有原子意图

# 原子意图格式要求
- 每条原子意图必须严格为单行字符串，且匹配：IF <触发条件> THEN <执行动作>
- 触发条件与动作使用自然语言短语即可，但必须具体且可执行（例如：打开/关闭/升高/降低/切换模式/播放/停止）
- 数值必须量化（包含阈值或范围边界），不能使用“适当/舒适/较高”等模糊词

# 精化原则
- 每条高层意图必须至少生成 1 条原子意图
- 原子意图尽量与环境属性（如温度、湿度、光照等）相关，而不要涉及到具体的控制设备
- 如果高层意图为“期望 X 处于 a-b”，通常生成两条边界控制规则：
  - IF X < a THEN 升高 X（环境属性 X 低于 a 时，触发升高 X 的动作）
  - IF X > b THEN 降低 X（环境属性 X 高于 b 时，触发降低 X 的动作）
  - 特别地，如果 a = b = v 时，也应该生成两条边界控制规则：
  - IF X < v THEN 升高 X（环境属性 X 低于 v 时，触发升高 X 的动作）
  - IF X > v THEN 降低 X（环境属性 X 高于 v 时，触发降低 X 的动作）
- 如果高层意图为“X 应 高于 v / 低于 v”，生成一条对应方向的规则
- 如果高层意图已经包含“如果...那么...”，则将其转换为 IF-THEN 并保持语义一致
- 同一条原子意图只能归属到一个高层意图
- atom_intents 必须等于 refinement_map 中所有列表的去重合并结果（顺序以 refinement_map 中出现顺序为准）
- 不能生成过于类似的重复原子意图，例如 IF 空气质量 < 1 THEN 升高 空气质量 和 IF 空气质量 < 1 THEN 升高 空气质量 到 3，这两条意图太过类似，只能保留一条，优先保留现有的原子意图。

# 示例
输入：
高层需求："期望 温度 处于 20-24"
现有原子需求：["IF 温度 < 20 THEN 升高 温度"]

输出：
{
  "atom_intents":[
    "IF 温度 < 20 THEN 升高 温度",
    "IF 温度 > 24 THEN 降低 温度"
  ],
  "refinement_map":{
    "期望 温度 处于 20-24":[
      "IF 温度 < 20 THEN 升高 温度",
      "IF 温度 > 24 THEN 降低 温度"
    ]
  }
}
"""

    user_prompt = (
        "请将以下高层意图精化为原子意图，并考虑已存在的原子意图：\n"
        f"{json.dumps({'high_intent': high_int, 'existing_atom_intents': existing_atom_int_list}, ensure_ascii=False, indent=2)}"
    )
    messages = [create_system_message(system_prompt), create_user_message(user_prompt)]

    if debug:
        print(f"正在请求 AI 进行意图精化，共 1 条高层意图，已有原子意图 {len(existing_atom_int_list)} 条...")

    try:
        with client.usage_scope("HighINT2AtomINT.refine_high_ints_to_atom_ints", scope_type="tool"):
            answer = client.simple_completion(messages_input=messages, use_json=True)
            raw = json.loads(answer)

            refinement_map_raw = raw.get("refinement_map", {})
            if not isinstance(refinement_map_raw, dict):
                refinement_map_raw = {}

            refinement_map: Dict[str, List[str]] = {}
            refinement_map_ai = _coerce_atom_int_list(refinement_map_raw.get(high_int, []))
            refinement_merged: List[str] = []
            seen_ref = set()
            for s in existing_atom_int_list + refinement_map_ai:
                if s not in seen_ref:
                    refinement_merged.append(s)
                    seen_ref.add(s)
            refinement_map[high_int] = refinement_merged

            atom_intents_raw = _coerce_atom_int_list(raw.get("atom_intents", []))

            merged: List[str] = []
            seen = set()
            for s in existing_atom_int_list:
                if s not in seen:
                    merged.append(s)
                    seen.add(s)
            for s in refinement_map.get(high_int, []):
                if s not in seen:
                    merged.append(s)
                    seen.add(s)
            for s in atom_intents_raw:
                if s not in seen:
                    merged.append(s)
                    seen.add(s)

            if not any(refinement_map.values()):
                if debug:
                    print("AI 输出未提供可用 refinement_map，尝试仅使用 atom_intents。")
                if merged:
                    refinement_map = {high_int: merged}
                else:
                    refinement_map = {}

            if debug:
                print("AI 响应解析成功。")

            return {"atom_intents": merged, "refinement_map": refinement_map}

    except Exception as e:
        print(f"意图精化过程中发生错误: {e}")
        return {"atom_intents": [], "refinement_map": {}, "error": str(e)}


if __name__ == "__main__":
    print("开始测试 refine_high_ints_to_atom_ints...")
    test_high_int =  "期望 光照强度 处于 100-500"
    current_atom_int = ["IF 光照强度 < 100 THEN 升高 光照强度"]
    result = refine_high_ints_to_atom_ints(test_high_int, existing_atom_ints=current_atom_int, debug=True)
    print("\n精化结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
