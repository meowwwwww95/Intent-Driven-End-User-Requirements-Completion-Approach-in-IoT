import sys
import os
import re
import networkx as nx
import json

# Add the project root to the Python path to allow direct execution of this script
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from model.context_graph import ContextGraph, NodeType, EdgeType

def extract_tap_rule(TAP_rule: str, debug: bool = False) -> dict:
    """
    将字符串形式的TAP规则转换为图形表示和相关设备列表。

    Args:
        TAP_rule: 字符串形式的TAP规则。
        debug: 是否开启调试模式，默认为False。

    Returns:
        一个包含图、特定设备列表和成功状态的字典。
        {
            "SR": dict,  # 图形表示
            "specific_devices": list[str],
            "success": bool
        }
    """
    elements = element_pick(TAP_rule)
    if debug:
        print(f"提取的元素: {elements}")
    
    if not elements.get('success'):
        return {
            "SR": None,
            "specific_devices": [],
            "success": False
        }

    context_graph = assemble_graph(elements)
    specific_devices = extract_specfic_devices(elements)
    if debug:
        print(f"特定设备: {specific_devices}")
    
    return {
        "SR": context_graph,
        "specific_devices": specific_devices,
        "success": True
    }

def element_pick(TAP_rule: str) -> dict:
    """
    从TAP规则字符串中提取元素。

    Args:
        TAP_rule: 字符串形式的TAP规则。

    Returns:
        一个包含提取元素的字典，结构如下：
        {
            "success": bool,
            "conditions": [
                {
                    "device": str,
                    "capability": str,
                    "operator": str,
                    "value": str
                }
            ],
            "action": {
                "device": str,
                "capability": str
            }
        }
    """
    ret = {
        "success": False,
        "conditions": [],
        "action": None,
    }
    # 使用正则表达式匹配IF和THEN之间的内容以及THEN之后的内容
    match = re.search(r'IF (.*?) THEN (.*?)$', TAP_rule, re.IGNORECASE)
    if not match:   
        return ret

    trigger_str = match.group(1)
    action_str = match.group(2)

    # 解析Trigger部分 - 先以AND为关键字分割
    conditions = re.split(r'\s+AND\s+', trigger_str, flags=re.IGNORECASE)
    
    for condition in conditions:
        # 匹配 device.capability() op value
        # The value can be a number or a quoted string
        match = re.search(
            r'([\w_-]+)\.([\w_-]+)\s*\(\s*([^)]*)\s*\)\s*([<>=!]+)\s*("(?:\\"|[^"])*"|'
            + r"'(?:\\'|[^'])*'"
            + r'|[\w_.-]+)',
            condition.strip(),
        )
        
        if match:
            args_str = match.group(3).strip()
            capability = f"{match.group(2)}({args_str})"
            value = match.group(5).strip('"').strip("'")
            ret['conditions'].append({
                "device": match.group(1),
                "capability": capability,
                "operator": match.group(4),
                "value": value,
            })
        else:
            # If one condition fails, fail all.
            ret['success'] = False
            return ret
    
    # 解析Action部分，只接受新格式: capability() device
    action_part = action_str.strip()

    # 匹配新格式: capability() device
    match = re.search(r'^([\w_-]+)\s*\(\s*([^)]*)\s*\)\s+([\w_-]+)$', action_part)
    if match:
        args_str = match.group(2).strip()
        capability = f"{match.group(1)}({args_str})"
        ret['action'] = {
            "device": match.group(3),
            "capability": capability,
        }
    else:
        ret['success'] = False
        return ret
    
    ret['success'] = True
    return ret

def assemble_graph(elements: dict) -> dict:
    """
    从提取的元素组装上下文图。
    使用 ContextGraph 来构建图，然后将其序列化为字典。
    """
    if not elements.get('success'):
        return {
            "res": {
                "nodes": [],
                "edges": [],
                "max_node_count": -1
            }
        }

    graph = ContextGraph()

    # 映射关系
    operator_map = {
        ">": EdgeType.OPERATOR_GREATER_THAN,
        ">=": EdgeType.OPERATOR_HIGHER_AND_EQUAL,
        "<": EdgeType.OPERATOR_LOWER_THAN,
        "<=": EdgeType.OPERATOR_LOWER_AND_EQUAL,
        "==": EdgeType.OPERATOR_EQUALS,
        "!=": EdgeType.OPERATOR_NOT_EQUALS,
    }

    # 1. 处理执行器
    action = elements['action']
    actuator_name = action['device']
    actuator_capability = action['capability']

    actuator_device_id = graph.add_node(actuator_name, NodeType.SPECIFIC_DEVICE)
    actuator_capability_id = graph.add_node(actuator_capability, NodeType.SPECIFIC_CAPABILITY)
    graph.add_edge(actuator_device_id, actuator_capability_id, EdgeType.USE)

    # 2. 处理传感器条件
    for condition in elements['conditions']:
        sensor_name = condition['device']
        sensor_capability = condition['capability']
        value = str(condition['value'])
        operator = condition['operator']

        sensor_device_id = graph.add_node(sensor_name, NodeType.SPECIFIC_DEVICE)
        sensor_capability_id = graph.add_node(sensor_capability, NodeType.SPECIFIC_CAPABILITY)
        value_id = graph.add_node(value, NodeType.VALUE)

        graph.add_edge(sensor_device_id, sensor_capability_id, EdgeType.USE)
        
        # 添加从值到传感器能力的比较操作边
        if operator in operator_map:
            op_edge_type = operator_map[operator]
            graph.add_edge(value_id, sensor_capability_id, op_edge_type)

        # 添加从传感器能力到执行器能力的使能边
        graph.add_edge(sensor_capability_id, actuator_capability_id, EdgeType.ENABLE)

    # 3. 将 ContextGraph 转换为旧的字典格式以保持兼容性
    return graph.to_dict()

def extract_specfic_devices(elements: dict) -> list[str]:
    if not elements.get('success'):
        return []

    devices = set()

    # Extract devices from conditions
    for condition in elements.get('conditions', []):
        if 'device' in condition:
            devices.add(condition['device'])

    # Extract device from action
    action = elements.get('action')
    if action and 'device' in action:
        devices.add(action['device'])

    return list(devices)
    params = elements
    sensors = params['sensors']
    actuator = params["actuator"]
    
    res = []
    for item in sensors:
        res.append(item['sensorName'])
    
    res.append(actuator)

    # 构建输出对象
    ret: Output = {
        "specfic_devices": res
    }
    return ret

if __name__ == '__main__':
    # Example TAP rule for testing（新语法）
    test_rule = 'IF bedroom_temp_sensor_pro.reading() > 38 AND hallway_presence_sensor_pro.reading() == "home" THEN turn_cool_on() livingroom_ac_pro'
    
    print(f"正在测试: '{test_rule}'")
    
    # Test extract_tap_rule
    print("\n--- 测试 extract_tap_rule ---")
    result = extract_tap_rule(test_rule, debug=True)
    print(json.dumps(result, indent=4, ensure_ascii=False))
