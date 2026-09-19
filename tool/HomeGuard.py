# -*- coding:utf-8 -*-
"""
统一接口：HomeGuard 冲突检测
将原 HomeGuard.py 与 config.py 的配置整合，提供对外唯一接口函数。
"""

import os
import sys
import time
import re
import json

def _get_value_code(value_tables: dict[str, dict[str, int]], ft: str, attr: str, raw_val: str) -> str:
    v = str(raw_val or '').strip()
    v = re.sub(r'^(["\'])|(["\'])$', '', v)
    # 数值直接返回
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', v):
        return v
    key = f"{ft}|{attr}"
    tbl = value_tables.setdefault(key, {})
    # 英文大小写不敏感，中文保持原样
    vk = v.upper()
    if vk in tbl:
        return str(tbl[vk])
    code = len(tbl) + 1
    tbl[vk] = code
    return str(code)

# 加载算法目录到路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'HomeGuard', 'algorithm'))  # 注入算法目录，供 mainCheck/connectAndTransfer 导入
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # 注入项目根，便于引入 config 下的模块

# 文本规则示例：IF 条件(可 AND 连接) THEN 动作能力() 功能设备
TEST_DATA  = [
    "IF bedroom_temp_sensor_pro.reading() > 38 THEN turn_cool_on() livingroom_ac_pro",
    "IF study_temp_sensor_pro.reading() > 38 THEN turn_cool_on() livingroom_ac_pro",
    "IF bedroom_temp_sensor_pro.reading() < 14 THEN turn_heat_on() livingroom_ac_pro"
]

# 直接内嵌测试数据（来自原 config.py）
# 注：下方为示例结构，run_homeguard 可直接使用文本规则或传入结构化数据源
# DEFAULT_TEST_DATA = {
#     'rules': [
#         # 规则数据格式：[ruleId, ruleName, conditionIds, actionIds, dayofweeks, starttime, endtime, userId, sceneId]
#         (120, 'If PM2.5 dust detector - the status of the living room equipment is equal to open THEN the electric fan - the status of the living room equipment is the second gear', '125', '150', '1,2,3,4,5,6,7', '00:00:00', '23:59:59', 5, 1),
#         (132, 'IF infrared human body sensor-human body detection in the living room is equal to someone, lighting-the state of the living room device is equal to on THEN the air conditioner-the state of the living room device is in cooling mode', '139,138', '164', '1,2', '20:30:00', '11:30:00', 12, 1),
#         (135, 'IF the temperature sensor - the living room temperature is greater than or equal to 30, the infrared human body sensor - the living room human body detection is equal to someone THEN the TV - the living room device status is turned on', '144,143', '167', '6,7', '20:00:00', '11:20:00', 15, 1),
#         (145, 'IF infrared human body sensor-office human detection is equal to someone THEN smart socket-office device status is on', '154', '178', '1,2,3,4,5,6,7', '00:00:00', '23:59:59', 15, 2),
#         (155, 'IF smoke detector-living room smoke detection is equal to smoke THEN air purifier-living room device status is on', '160', '187', '1,2,3,4,5,6,7', '00:00:00', '23:59:59', 15, 1),
#         # 这俩在检测上是一致的，如果检测效果不好，那就用原来的检测器去跑全时段检测即可
#         # 添加一个无时间约束的规则测试案例（使用None表示无时间约束）
#         (200, 'IF door opens THEN turn on security light', '200', '200', None, None, None, 20, 3),
#         # 添加一个明确全时间约束的规则（对比测试）
#         (201, 'IF window opens THEN turn on alarm', '201', '201', '1,2,3,4,5,6,7', '00:00:00', '23:59:59', 20, 3),
#     ],
#     'conditions': [
#         # 条件数据格式：[conditionId, deviceId, attribute, compareType, standardValue]
#         (125, 'PM2.5传感器_73', 'equipment status', '1', '1'),  # PM2.5检测器状态等于开启
#         (138, '红外传感器_80', 'Human detection', '1', '1'),   # 红外人体传感器检测到有人
#         (139, '照明_91', 'equipment status', '1', '1'),        # 照明设备状态等于开启
#         (143, '温度传感器_88', 'temperature', '4', '30'),      # 温度传感器温度大于等于30度
#         (144, '红外传感器_80', 'Human detection', '1', '1'),   # 红外人体传感器检测到有人
#         (154, '办公室红外_100', 'Human detection', '1', '1'),  # 办公室红外传感器检测到有人
#         (160, '烟雾传感器_89', 'smoke detection', '1', '1'),   # 烟雾检测器检测到烟雾
#         (200, '门_200', 'door status', '1', '1'),               # 门状态等于开启
#         (201, '窗_202', 'window status', '1', '1'),             # 窗户状态等于开启
#     ],
#     'actions': [
#         # 动作数据格式：[actionId, deviceId, attribute, newValue]
#         (150, '电风扇_76', 'equipment status', '2'),  # 电风扇设置为二档
#         (164, '空调_79', 'equipment status', '4'),    # 空调设置为制冷模式
#         (167, '电视_78', 'equipment status', '1'),    # 电视设备状态设为开启
#         (178, '智能插座_108', 'equipment status', '1'), # 智能插座设备状态设为开启
#         (187, '空气净化器_82', 'equipment status', '1'),  # 空气净化器设备状态设为开启
#         (200, '安全灯_201', 'light status', '1'),       # 安全灯设备状态设为开启
#         (201, '警报器_203', 'alarm status', '1'),       # 警报设备状态设为开启
#     ]
# }


def _op_to_code(op: str) -> str:
    # 文本操作符 → HomeGuard 比较码：1(==),2(>),3(<),4(>=),5(<=),6(!=)
    s = op.strip()
    if s in ['==', '=', '等于']:
        return '1'
    if s in ['>', '高于']:
        return '2'
    if s in ['<', '低于']:
        return '3'
    if s in ['>=', '大于等于']:
        return '4'
    if s in ['<=', '小于等于']:
        return '5'
    if s in ['!=', '不等于']:
        return '6'
    return None

def _norm_value(value_tables: dict[str, dict[str, int]], ft: str, attr: str, val: str) -> str:
    # 动态值规范化：按功能设备与属性维护枚举码表；遇新值则追加
    return _get_value_code(value_tables, ft, attr, val)

def _parse_condition_text(text: str, value_tables: dict[str, dict[str, int]], cid: int | None = None) -> dict | None:
    # 解析单个条件片段：功能设备.能力() 操作符 值；返回标准字段供结构化构建
    s = re.sub(r'\s+', ' ', text).strip()
    m = re.search(r'([\w\u4e00-\u9fa5]+)\.([\w\u4e00-\u9fa5]+)\s*\(\s*\)\s*([<>!=]=|[<>]|=|等于|高于|低于|大于等于|小于等于|不等于)\s*(".*?"|\'.*?\'|[\w\u4e00-\u9fa5.-]+)', s)
    if not m:
        m = re.search(r'([\w\u4e00-\u9fa5]+)\.([\w\u4e00-\u9fa5]+)\s*([<>!=]=|[<>]|=|等于|高于|低于|大于等于|小于等于|不等于)\s*(".*?"|\'.*?\'|[\w\u4e00-\u9fa5.-]+)', s)
    if not m:
        return None
    ft = m.group(1)
    cap = m.group(2)
    op = m.group(3)
    val = m.group(4)

    if _op_to_code(op) is None:
        return None
    return {
        'deviceId': ft,
        'attribute': cap,
        'compareType': _op_to_code(op),
        'standardValue': _norm_value(value_tables, ft, cap, val),
    }

def _parse_action_text(text: str, value_tables: dict[str, dict[str, int]]) -> dict | None:
    # 解析动作片段：动作能力() 功能设备；兼容“打开 所有影音、照明、温控及厨电类电器”这类简写
    s = re.sub(r'\s+', ' ', text).strip()
    m = re.search(r'([\w\u4e00-\u9fa5]+)\s*\(\s*\)\s+([\w\u4e00-\u9fa5]+)', s)
    if not m:
        m = re.search(r'([\w\u4e00-\u9fa5]+)\s+([\w\u4e00-\u9fa5]+)', s)
    if not m:
        return None

    act = m.group(1)
    ft = m.group(2)
    
    # 统一映射：属性统一为 status，不同的动作名映射为不同的值
    attribute = 'status'
    # 使用 _get_value_code 将动作动词(act) 映射为该设备 status 属性下的唯一数值
    val_code = _get_value_code(value_tables, ft, attribute, act)
    try:
        newValue = int(float(val_code))
    except ValueError:
        newValue = 1

    return {
        'deviceId': ft,
        'attribute': attribute,
        'newValue': newValue,
    }

def build_structured_from_text(text_rules: list[str]) -> dict:
    # 文本规则列表 → 结构化数据（rules/conditions/actions）；无条件时将 conditionIds 置为 ''
    value_tables: dict[str, dict[str, int]] = {}
    rules = []
    conditions = []
    actions = []
    rid = 1000
    cid = 3000
    aid = 4000
    cond_index: dict[tuple[str, str, str, str], int] = {}
    action_index: dict[tuple[str, str, int], int] = {}
    for raw in text_rules:
        m = re.search(r'^\s*IF\s+(.*?)\s+THEN\s+(.*?)\s*$', raw)
        if not m:
            continue
        cond_str = m.group(1)
        act_str = m.group(2)
        cond_parts = re.split(r'\s+AND\s+', cond_str)
        cond_ids = []
        for ctext in cond_parts:
            c = _parse_condition_text(ctext, value_tables, cid)
            if not c:
                continue
            key = (c['deviceId'], c['attribute'], c['compareType'], c['standardValue'])
            if key in cond_index:
                cond_ids.append(str(cond_index[key]))
            else:
                conditions.append((cid, c['deviceId'], c['attribute'], c['compareType'], c['standardValue']))
                cond_index[key] = cid
                cond_ids.append(str(cid))
                cid += 1
        a = _parse_action_text(act_str, value_tables)
        if not a:
            continue
        akey = (a['deviceId'], a['attribute'], a['newValue'])
        if akey in action_index:
            action_id = action_index[akey]
        else:
            actions.append((aid, a['deviceId'], a['attribute'], a['newValue']))
            action_index[akey] = aid
            action_id = aid
            aid += 1
        action_ids = [str(action_id)]
        seen = set()
        ordered_cond_ids = []
        for x in cond_ids:
            if x not in seen:
                ordered_cond_ids.append(x)
                seen.add(x)
        rules.append((rid, raw, ','.join(ordered_cond_ids) if ordered_cond_ids else '', ','.join(action_ids), '1,2,3,4,5,6,7', '00:00:00', '23:59:59', 20, 1))
        rid += 1
    return {'rules': rules, 'conditions': conditions, 'actions': actions}

__all__ = ["run_homeguard"]

def run_homeguard(test_data=None):
    """
    执行 HomeGuard 冲突检测并返回结果。

    参数:
    - test_data: 可选，需要测试的数据

    返回:
    - dict 格式的检测结果
    """
    import importlib

    cat = importlib.import_module('connectAndTransfer')
    mainCheck = importlib.import_module('mainCheck')
    structured_data = build_structured_from_text(test_data) if isinstance(test_data, list) else None

    start_time = time.time()
    try:
        cat.set_runtime_state(structured_data)
        result = mainCheck.check()
    finally:
        cat.clear_runtime_state()
    _elapsed = time.time() - start_time  # 供调用方需要时参考
    # print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

if __name__ == '__main__':
    # 作为脚本运行时，调用统一接口并输出简要信息
    res = run_homeguard(TEST_DATA)  # 演示：直接使用文本规则进行检测
    try:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except TypeError:
        print(res)
