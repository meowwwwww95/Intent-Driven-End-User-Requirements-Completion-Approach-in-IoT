from calendar import c
from doctest import debug
import os
import json
from re import template
import sys
from pathlib import Path
from dotenv import load_dotenv
# from pandas.core.dtypes.dtypes import pa
# from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from model.context_graph import ContextGraph, NodeType, EdgeType
from config.ai_client import get_default_ai_client, simple_completion, create_system_message, create_user_message

# Load environment variables
load_dotenv()

from model.trace_tree_bus import get_available_devices
import json


def _lookup_abstract_capability_context(device_type_name: str, capability_name: str) -> dict:
    """从抽象设备画像中获取设备/能力描述，供提示词补充语义上下文。"""
    device_type = (device_type_name or "").strip()
    capability = (capability_name or "").strip()
    if not device_type and not capability:
        return {"device_description": "", "capability_description": ""}

    try:
        abstract_devices = get_available_devices().get_abstract_devices()
    except Exception:
        abstract_devices = []

    device_description = ""
    capability_description = ""
    fallback_capability_description = ""

    for device in abstract_devices or []:
        if not isinstance(device, dict):
            continue
        dtype = (device.get("type") or "").strip()
        if dtype == device_type and not device_description:
            device_description = (device.get("description") or "").strip()
        for cap in (device.get("capabilities") or []):
            if not isinstance(cap, dict):
                continue
            cap_name = (cap.get("name") or "").strip()
            if cap_name != capability:
                continue
            cap_desc = (cap.get("description") or "").strip()
            if dtype == device_type:
                capability_description = cap_desc
                break
            if not fallback_capability_description:
                fallback_capability_description = cap_desc
        if capability_description and device_description:
            break

    if not capability_description:
        capability_description = fallback_capability_description

    return {
        "device_description": device_description,
        "capability_description": capability_description,
    }

def _extract_devices(SR: dict) -> list:
    """Extract device nodes from SR"""
    devices = [node for node in SR["nodes"] if node["type"] == "specific_device"]
    return devices

def _extract_capabilities(SR: dict) -> list:
    """Extract capability nodes from SR"""
    capabilities = [node for node in SR["nodes"] if node["type"] == "specific_capability"]
    return capabilities

def _extract_values(SR: dict) -> list:
    """Extract values from SR"""   
    values = [node["name"] for node in SR["nodes"] if node["type"] == "value"]
    return values

def _abstract_device_node(device_node: NodeType) -> dict:
    """用大模型将设备进行抽象，抽象为设备类型"""
    template = """
    # 任务
    仔细理解输入的具体设备名称，将其映射到抽象的设备类型上。

    # 示例
    输入:bedroom_fan_pro
    JSON输出：{{
        "device_type": "风扇"
    }}


    # 相关知识
    {knowledge}

    # 限制
    1. 直接给出输出结果，不要输出其他内容
    2. 只映射到一个最可能的设备类型上
    """

    knowledge = get_available_devices().get_all_device_knowledge_str()
    
    messages = [
        create_system_message(template.format(knowledge=knowledge)),
        create_user_message(f"这是需要理解的具体设备名称：{device_node['name']}")
    ]
    try:
        resp = simple_completion(
            messages_input=messages, 
            temperature=0.0, 
            use_json=True
        )
        # print("抽象设备类型响应:", resp)
        parsed_resp = json.loads(resp) if isinstance(resp, str) else resp
        new_device_type = (parsed_resp or {"device_type": ""}).get("device_type", "")
        original_node = device_node
        new_node = original_node.copy()
        new_node["name"] = new_device_type
        new_node["type"] = "device_type"
        ret = {
            "res": {
                "new_node": new_node,
                "original_node": original_node
            }
        }
        return ret
    except Exception:
        raise Exception(f"抽象设备类型时出错: {device_node}")

def _abstract_capability_node(capability_node: NodeType) -> dict:
    """将设备能力进行抽象，抽象为抽象能力类型"""

    template = """
    # 任务
    仔细理解输入的具体设备能力名称，将其映射到某个抽象的设备能力上。

    # 示例
    输入: reading
    JSON输出: {{
        "device_capability_type": "获取读数"
    }}

    # 相关知识
    {knowledge}

    # 限制
    1. 直接给出输出结果，不要输出其他内容
    2. 只映射到一个最可能的能力上
    """
    knowledge = get_available_devices().get_all_device_knowledge_str()

    messages = [
        create_system_message(template.format(knowledge=knowledge)),
        create_user_message(f"这是需要理解的具体能力函数名：{capability_node['name']}")
    ]
    try:
        resp = simple_completion(
            messages_input=messages, 
            temperature=0.0, 
            use_json=True
        )
        # print("抽象能力类型响应:", resp)
        parsed_resp = json.loads(resp) if isinstance(resp, str) else resp
        new_device_capability = (parsed_resp or {"device_capability_type": ""}).get("device_capability_type", "")
        original_node = capability_node
        new_node = original_node.copy()
        new_node["name"] = new_device_capability
        new_node["type"] = "device_capability_type"
        ret = {
            "res": {
                "new_node": new_node,
                "original_node": original_node
            }
        }
        return ret
    except Exception:
        raise Exception(f"抽象能力类型时出错: {capability_node}")

# 前向环境推理
def _forward_environment_inference(
    device_capability_name,
    device_type_name,
    value,
    operator,
    device_description="",
    capability_description="",
) -> dict:
    """前向环境推理"""
    # print(f"正在进行前向环境推理，设备能力名称：{device_capability_name}，设备类型名称：{device_type_name}，值：{value}，操作符：{operator}")
    template = """
    # 任务
    需要根据输入的内容，仔细理解后推理出设备使用该能力是为了获取什么环境属性的值。

    # 判断优先级
    1. 优先关注“正在使用的能力/功能”及其描述，先判断这个能力到底在获取什么。
    2. 设备类型名称只作为次要参考，不能盖过能力语义。
    3. 如果设备类型名称与能力语义冲突，必须优先相信能力语义，因为设备可能是复合设备或设备类型标注不够细，此时更要依据能力名称和能力描述判断。

    # 示例
    输入：湿度传感器使用获取读数<40%是为了判断XXXYYY40%
    (XXX是占位符,表示需要推理的环境属性名;YYY是占位符,表示环境属性与阈值之间的比较关系)
    JSON输出:
    {{
        "targetEnv": "湿度",
        "targetOperation": "<"
    }}

    输入：温度感知设备使用获取湿度()<29，能力描述为“获取当前的湿度”，是为了判断XXXYYY29
    JSON输出:
    {{
        "targetEnv": "湿度",
        "targetOperation": "<"
    }}

    # 限制
    targetOperation必须为以下几个中选取:">","<","==","!="

    """
    messages = [
        create_system_message(template.format()),
        create_user_message(
            "输入内容为：\n"
            f"- 设备类型：{device_type_name}\n"
            f"- 设备类型描述：{device_description or '无'}\n"
            f"- 使用能力：{device_capability_name}\n"
            f"- 能力描述：{capability_description or '无'}\n"
            f"- 比较条件：{device_capability_name}{operator}{value}\n"
            f"- 需要推理：上述能力是为了判断XXX是否YYY{value}"
        )
    ]
    try:
        resp = simple_completion(
            messages_input=messages, 
            temperature=0.0, 
            use_json=True
        )
        # print("前向环境推理响应:", resp)
        parsed_resp = json.loads(resp) if isinstance(resp, str) else resp
        target_env = (parsed_resp or {"targetEnv": ""}).get("targetEnv", "")
        target_operation = (parsed_resp or {"targetOperation": ""}).get("targetOperation", "")
        if target_env and target_operation:
            return {
                "env_node":{
                    "name": f"{target_env}",
                    "type": "environment"
                },
                "operator": target_operation
            }
    except Exception:
        raise Exception(f"前向环境推理时出错: {device_capability_name}，设备类型名称：{device_type_name}，值：{value}，操作符：{operator}")

def _backward_environment_inference(
    device_capability_name,
    device_type_name,
    device_description="",
    capability_description="",
) -> dict:
    """后向环境推理"""
    # print(f"正在进行后向环境推理，设备能力名称：{device_capability_name}，设备类型名称：{device_type_name}")
    template = f"""
    # 任务
    需要根据输入的内容，仔细理解后推理出设备使用该能力是为了让什么环境属性有什么趋势。尽可能完整地推理出可能影响的环境属性, 返回列表

    # 判断优先级
    1. 优先关注“正在使用的能力/功能”及其描述，先判断该动作会直接改变什么环境属性。
    2. 设备类型名称只作为次要参考，不能盖过能力语义。
    3. 如果设备类型名称与能力语义冲突，必须优先相信能力语义，因为设备可能是复合设备或设备类型标注不够细，此时更要依据能力名称和能力描述判断。

    # 示例
    输入: 空调使用打开制热是为了让XXX能YYY
    (XXX是占位符, 表示需要推理的环境属性名; YYY也是占位符, 表示环境属性期望达成的变化趋势)
    JSON输出：
    {{
        "env_nodes": [
            {{
                "targetEnv": "温度",
                "targetTrend": "升高"
            }}
        ]
    }}

    输入: 温度控制设备使用切换除湿模式()，能力描述为“降低空气湿度”，是为了让XXX能YYY
    JSON输出：
    {{
        "env_nodes": [
            {{
                "targetEnv": "湿度",
                "targetTrend": "降低"
            }}
        ]
    }}

    # 限制
    1. 如果趋势是升高或降低的含义，分别使用"升高"和"降低"以统一化名称。
    以下词语可近似为升高含义："升高","上升","增加","提升","增强","提高","增大"
    以下词语可近似为降低含义："降低","下降","减少","下降","减弱","降低","减小"
    """
    messages = [
        create_system_message(template),
        create_user_message(
            "输入内容为：\n"
            f"- 设备类型：{device_type_name}\n"
            f"- 设备类型描述：{device_description or '无'}\n"
            f"- 使用能力：{device_capability_name}\n"
            f"- 能力描述：{capability_description or '无'}\n"
            f"- 需要推理：{device_type_name}使用{device_capability_name}是为了让XXX能YYY"
        )
    ]

    try:
        resp = simple_completion(
            messages_input=messages, 
            temperature=0.0, 
            use_json=True
        )
        # print("后向环境推理响应:", resp)
        parsed_resp = json.loads(resp) if isinstance(resp, str) else resp
        items = []
        if isinstance(parsed_resp, dict):
            items = parsed_resp.get("env_nodes") or []
        elif isinstance(parsed_resp, list):
            items = parsed_resp
        env_nodes = [
            {
                "environment": i.get("targetEnv", ""),
                "trend": i.get("targetTrend", "")
            }
            for i in items
            if i.get("targetEnv") and i.get("targetTrend")
        ]
        if not env_nodes and isinstance(parsed_resp, dict):
            env = parsed_resp.get("targetEnv")
            trend = parsed_resp.get("targetTrend")
            if env and trend:
                env_nodes = [{"environment": env, "trend": trend}]
        if env_nodes:
            return {"env_nodes": env_nodes}
    except Exception:
        raise Exception(f"后向环境推理时出错: {device_capability_name}，设备类型名称：{device_type_name}")


def _build_environment_extension(SR: dict, device_mapping: dict, device_capability_mapping: dict) -> dict:
    """通过环境推理，构建SR的扩展部分（新版本仅使用设备与能力两类映射）"""
    # print("正在构建环境扩展")
    id_to_node = {n["id"]: n for n in SR.get("nodes", [])}
    start_id = SR.get("max_node_count", len(SR.get("nodes", [])))
    # print(f"原始节点数量: {len(SR.get('nodes', []))}, 起始扩展ID: {start_id}")

    # 将输入的映射结构归一化为：{ 原始名称: {"name": 抽象名称, "type": 抽象类型 } }
    # 这样可统一支持两种输入形式：
    # - {"bedroom_fan_pro": {"name": "风扇", "type": "device_type"}}
    # - {"bedroom_fan_pro": "风扇"}（此时使用 default_type 作为类型）
    def normalize_mapping(mapping, default_type: str) -> dict:
        ret = {}
        if isinstance(mapping, list):
            for item in mapping:
                if not isinstance(item, dict):
                    continue
                original_node = item.get("original") or (item.get("res") or {}).get("original_node")
                abstract_node = item.get("abstract") or (item.get("res") or {}).get("new_node")
                orig_id = original_node.get("id") if isinstance(original_node, dict) else None
                if isinstance(abstract_node, dict):
                    new_name = abstract_node.get("name") or abstract_node.get("device_type") or abstract_node.get("device_capability_type")
                    new_type = abstract_node.get("type", default_type)
                else:
                    new_name = abstract_node
                    new_type = default_type
                if orig_id is not None and new_name:
                    ret[orig_id] = {"name": new_name, "type": new_type}
        elif isinstance(mapping, dict):
            for k, v in mapping.items():
                # 解析候选原始ID集，优先取显式id；否则通过名称在SR中匹配
                candidate_ids = []
                if isinstance(k, dict):
                    if k.get("id") is not None:
                        candidate_ids = [k.get("id")]
                    else:
                        name = k.get("name")
                        candidate_ids = [nid for nid, node in id_to_node.items() if node.get("name") == name]
                else:
                    name = k
                    candidate_ids = [nid for nid, node in id_to_node.items() if node.get("name") == name]

                if isinstance(v, dict):
                    new_name = v.get("name") or v.get("device_type") or v.get("device_capability_type")
                    new_type = v.get("type", default_type)
                else:
                    new_name = v
                    new_type = default_type

                for orig_id in candidate_ids:
                    if orig_id is not None and new_name:
                        ret[orig_id] = {"name": new_name, "type": new_type}
        return ret

    # 构建两类抽象映射：设备类型映射与能力类型映射
    # 仅保留设备映射(device_mapping)与能力映射(device_capability_mapping)两类输入
    device_type_map = normalize_mapping(device_mapping, NodeType.DEVICE_TYPE.value)
    capability_type_map = normalize_mapping(device_capability_mapping, NodeType.DEVICE_CAPABILITY_TYPE.value)
    # print(f"设备类型映射: {device_type_map}")
    # print(f"能力类型映射: {capability_type_map}")

    ext_nodes = []
    ext_node_cache = {}

    # 统一为扩展节点分配连续ID（从原SR的 max_node_count 起），避免与原SR发生ID冲突
    def get_or_add_ext_node(name: str, t: str, scope: str = None) -> int:
        key = (name, t, scope)
        if key in ext_node_cache:
            return ext_node_cache[key]
        node = {"id": start_id + len(ext_nodes), "name": name, "type": t}
        ext_nodes.append(node)
        ext_node_cache[key] = node["id"]
        return node["id"]

    # 扩展子图的边集合
    ext_edges = []

    # 1) 处理“使用”关系：抽象设备类型 使用 抽象能力类型
    usage_edges = [e for e in SR.get("edges", []) if e.get("type") == EdgeType.USE.value]
    # print(f"使用关系数量: {len(usage_edges)}")
    for e in usage_edges:
        src = id_to_node.get(e.get("from"))
        dst = id_to_node.get(e.get("to"))
        if not src or not dst:
            continue
        # 支持 device -> capability 或 capability -> device 两种方向
        if src.get("type") == NodeType.SPECIFIC_DEVICE.value and dst.get("type") == NodeType.SPECIFIC_CAPABILITY.value:
            dev_node = src
            cap_node = dst
        elif src.get("type") == NodeType.SPECIFIC_CAPABILITY.value and dst.get("type") == NodeType.SPECIFIC_DEVICE.value:
            dev_node = dst
            cap_node = src
        else:
            continue
        # 如果映射缺失，回退为原名称对应的抽象类型（类型使用默认值），以ID为键
        dev_abstract = device_type_map.get(dev_node.get("id"), {"name": dev_node.get("name"), "type": NodeType.DEVICE_TYPE.value})
        cap_abstract = capability_type_map.get(cap_node.get("id"), {"name": cap_node.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})
        dev_id = get_or_add_ext_node(dev_abstract["name"], dev_abstract["type"])
        cap_id = get_or_add_ext_node(cap_abstract["name"], cap_abstract["type"], scope=dev_abstract["name"])  # 能力节点按设备类型区分
        ext_edges.append({"from": dev_id, "to": cap_id, "type": EdgeType.USE.value})
        # print(f"使用关系扩展: {src.get('name')}→{dst.get('name')} 映射为 {dev_abstract['name']}({dev_id}) 使用 {cap_abstract['name']}({cap_id})")

    # print(f"使用关系扩展后: 扩展节点数={len(ext_nodes)}, 扩展边数={len(ext_edges)}")

    # 2) 处理“使能”关系：抽象能力A 使能 抽象能力B（仅考虑能力到能力的边）
    enable_edges = [e for e in SR.get("edges", []) if e.get("type") == EdgeType.ENABLE.value]
    # print(f"使能关系数量: {len(enable_edges)}")
    for e in enable_edges:
        src = id_to_node.get(e.get("from"))
        dst = id_to_node.get(e.get("to"))
        if not src or not dst:
            continue
        if src.get("type") != NodeType.SPECIFIC_CAPABILITY.value or dst.get("type") != NodeType.SPECIFIC_CAPABILITY.value:
            continue
        # 找到与两个能力相关联的设备类型集合
        src_related_devs = []
        dst_related_devs = []
        for ue in SR.get("edges", []):
            u_src = id_to_node.get(ue.get("from"))
            u_dst = id_to_node.get(ue.get("to"))
            if u_src and u_dst and ue.get("type") == EdgeType.USE.value:
                if u_src.get("type") == NodeType.SPECIFIC_DEVICE.value and u_dst.get("id") == src.get("id"):
                    src_related_devs.append(u_src)
                elif u_dst.get("type") == NodeType.SPECIFIC_DEVICE.value and u_src.get("id") == src.get("id"):
                    src_related_devs.append(u_dst)
                if u_src.get("type") == NodeType.SPECIFIC_DEVICE.value and u_dst.get("id") == dst.get("id"):
                    dst_related_devs.append(u_src)
                elif u_dst.get("type") == NodeType.SPECIFIC_DEVICE.value and u_src.get("id") == dst.get("id"):
                    dst_related_devs.append(u_dst)

        # 映射到抽象设备类型名
        src_dev_types = [device_type_map.get(d.get("id"), {"name": d.get("name"), "type": NodeType.DEVICE_TYPE.value}).get("name") for d in src_related_devs]
        dst_dev_types = [device_type_map.get(d.get("id"), {"name": d.get("name"), "type": NodeType.DEVICE_TYPE.value}).get("name") for d in dst_related_devs]
        # 去重
        src_dev_types = list(dict.fromkeys([n for n in src_dev_types if n]))
        dst_dev_types = list(dict.fromkeys([n for n in dst_dev_types if n]))

        cap_a_info = capability_type_map.get(src.get("id"), {"name": src.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})
        cap_b_info = capability_type_map.get(dst.get("id"), {"name": dst.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})

        # 为每个设备类型创建/复用各自的能力节点，并建立使能关系
        for sa in (src_dev_types or [None]):
            a_id = get_or_add_ext_node(cap_a_info["name"], cap_a_info["type"], scope=sa)
            for sb in (dst_dev_types or [None]):
                b_id = get_or_add_ext_node(cap_b_info["name"], cap_b_info["type"], scope=sb)
                ext_edges.append({"from": a_id, "to": b_id, "type": EdgeType.ENABLE.value})
                # print(f"使能关系扩展: {src.get('name')}({sa}) 使能 {dst.get('name')}({sb}) 映射为 {cap_a_info['name']}({a_id}) 使能 {cap_b_info['name']}({b_id})")

    # print(f"使能关系扩展后: 扩展节点数={len(ext_nodes)}, 扩展边数={len(ext_edges)}")

    # 3) 如果原有的具体能力有 value 类型的节点指向它
    #    那么仅新增边：原始 value 节点 -> 抽象能力类型，边类型为 "被获取"
    #    注意：不在扩展子图中复制/新增 value 节点，保持引用原 SR 的节点
    for e in SR.get("edges", []):
        src = id_to_node.get(e.get("from"))
        dst = id_to_node.get(e.get("to"))
        if not src or not dst:
            continue
        if src.get("type") == NodeType.VALUE.value and dst.get("type") == NodeType.SPECIFIC_CAPABILITY.value:
            val_name = src.get("name")
            cap_abstract = capability_type_map.get(dst.get("id"), {"name": dst.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})
            # 将值连接到与该具体能力相关联的每个设备类型下的能力节点
            # 找出该具体能力所关联的设备类型
            related_devs = []
            for ue in SR.get("edges", []):
                u_src = id_to_node.get(ue.get("from"))
                u_dst = id_to_node.get(ue.get("to"))
                if u_src and u_dst and ue.get("type") == EdgeType.USE.value:
                    if u_src.get("type") == NodeType.SPECIFIC_DEVICE.value and u_dst.get("id") == dst.get("id"):
                        related_devs.append(u_src)
                    elif u_dst.get("type") == NodeType.SPECIFIC_DEVICE.value and u_src.get("id") == dst.get("id"):
                        related_devs.append(u_dst)
            dev_types = [device_type_map.get(d.get("id"), {"name": d.get("name"), "type": NodeType.DEVICE_TYPE.value}).get("name") for d in related_devs]
            dev_types = list(dict.fromkeys([n for n in dev_types if n]))
            if not dev_types:
                # 无设备类型关联时，退化为无作用域的能力节点
                cap_id = get_or_add_ext_node(cap_abstract["name"], cap_abstract["type"])
                ext_edges.append({"from": src.get("id"), "to": cap_id, "type": EdgeType.IS_ACQUIRED_BY.value})
                # print(f"值到能力扩展: 值 {val_name}({src.get('id')}) 被获取→ 抽象能力 {cap_abstract['name']}({cap_id}) [无设备作用域]")
            else:
                for dt in dev_types:
                    cap_id = get_or_add_ext_node(cap_abstract["name"], cap_abstract["type"], scope=dt)
                    ext_edges.append({"from": src.get("id"), "to": cap_id, "type": EdgeType.IS_ACQUIRED_BY.value})
                    # print(f"值到能力扩展: 值 {val_name}({src.get('id')}) 被获取→ 抽象能力 {cap_abstract['name']}({cap_id}) @设备类型={dt}")

    # print(f"值到能力扩展后: 扩展节点数={len(ext_nodes)}, 扩展边数={len(ext_edges)}")

    # 4) 对每条使能关系的前件（src 具体能力）做前向环境推理
    #    找到与该能力相关联的具体设备（使用关系），由设备类型推导环境名（如“温度传感器”→“温度”）
    #    再将推导出的环境节点与该能力所关联的 value 节点相连，边类型使用原 SR 的运算符（如 ">", "=="）
    for e in enable_edges:
        src_cap = id_to_node.get(e.get("from"))
        if not src_cap or src_cap.get("type") != NodeType.SPECIFIC_CAPABILITY.value:
            continue
        # 与前件能力相关联的 value→capability 边及其运算符
        related_value_edges = [ve for ve in SR.get("edges", []) if id_to_node.get(ve.get("from"), {}).get("type") == NodeType.VALUE.value and ve.get("to") == src_cap.get("id")]
        if not related_value_edges:
            continue
        # 找到与该能力相关联的抽象设备和抽象能力（device 使用 capability）
        related_devices = []
        for ue in SR.get("edges", []):
            u_src = id_to_node.get(ue.get("from"))
            u_dst = id_to_node.get(ue.get("to"))
            if u_src and u_dst and ue.get("type") == EdgeType.USE.value:
                if u_src.get("type") == NodeType.SPECIFIC_DEVICE.value and u_dst.get("id") == src_cap.get("id"):
                    related_devices.append(u_src)
                elif u_dst.get("type") == NodeType.SPECIFIC_DEVICE.value and u_src.get("id") == src_cap.get("id"):
                    related_devices.append(u_dst)

        if not related_devices:
            continue

        # 使用前向环境推理函数，输入抽象设备类型与抽象能力类型、原始 value 与 SR 运算符
        for dev in related_devices:
            dev_abs = device_type_map.get(dev.get("id"))
            if not dev_abs:
                continue
            for ve in related_value_edges:
                val_node = id_to_node.get(ve.get("from"))
                if not val_node:
                    continue
                cap_abs = capability_type_map.get(src_cap.get("id"), {"name": src_cap.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})
                context = _lookup_abstract_capability_context(dev_abs.get("name"), cap_abs.get("name"))
                try:
                    inf = _forward_environment_inference(
                        device_capability_name=cap_abs.get("name"),
                        device_type_name=dev_abs.get("name"),
                        value=val_node.get("name"),
                        operator=ve.get("type"),
                        device_description=context.get("device_description", ""),
                        capability_description=context.get("capability_description", ""),
                    )
                    env_node = inf.get("env_node", {})
                    ret_op = inf.get("operator")
                    if env_node and env_node.get("name") and ret_op:
                        env_id = get_or_add_ext_node(env_node.get("name"), NodeType.ENVIRONMENT.value)
                        ext_edges.append({"from": env_id, "to": val_node.get("id"), "type": ret_op})
                        # print(f"前向环境扩展: 环境 {env_node.get('name')}({env_id}) {ret_op} 值 {val_node.get('name')}({val_node.get('id')})")
                except Exception:
                    # 推理失败时忽略该条前向推理连接
                    # print(f"前向环境推理失败：设备类型={dev_abs.get('name')}，能力类型={cap_abs.get('name')}，值={val_node.get('name')}，运算符={ve.get('type')}")
                    pass
      
    # print("进行后向环境推理")
    # print(f"前向环境扩展后: 扩展节点数={len(ext_nodes)}, 扩展边数={len(ext_edges)}")
    processed_pairs = set()
    # 对每个使能关系的后件节点进行后向环境推理
    # 然后把返回的结果连接到后件节点上，连接方式如下：
    # 1. 先把返回的trend新建为一个trend类型节点，建立一条从后件指向trend的边，边类型为"导致"
    # 2. 再把返回的environment新建为一个environment类型节点，建立一条从trend指向environment的边，边类型为"是"
    for e in enable_edges:
        dst_cap = id_to_node.get(e.get("to"))
        if not dst_cap or dst_cap.get("type") != NodeType.SPECIFIC_CAPABILITY.value:
            continue
        related_devices = []
        for ue in SR.get("edges", []):
            u_src = id_to_node.get(ue.get("from"))
            u_dst = id_to_node.get(ue.get("to"))
            if u_src and u_dst and ue.get("type") == EdgeType.USE.value:
                if u_src.get("type") == NodeType.SPECIFIC_DEVICE.value and u_dst.get("id") == dst_cap.get("id"):
                    related_devices.append(u_src)
                elif u_dst.get("type") == NodeType.SPECIFIC_DEVICE.value and u_src.get("id") == dst_cap.get("id"):
                    related_devices.append(u_dst)

        if not related_devices:
            continue

        for dev in related_devices:
            dev_abs = device_type_map.get(dev.get("id"))
            if not dev_abs:
                continue
            cap_abs = capability_type_map.get(dst_cap.get("id"), {"name": dst_cap.get("name"), "type": NodeType.DEVICE_CAPABILITY_TYPE.value})
            pair_key = (dev_abs.get("name"), cap_abs.get("name"))
            if pair_key in processed_pairs:
                # print(f"后向环境推理跳过重复组合: {pair_key}")
                continue
            processed_pairs.add(pair_key)
            context = _lookup_abstract_capability_context(dev_abs.get("name"), cap_abs.get("name"))
            try:
                inf = _backward_environment_inference(
                    device_capability_name=cap_abs.get("name"),
                    device_type_name=dev_abs.get("name"),
                    device_description=context.get("device_description", ""),
                    capability_description=context.get("capability_description", ""),
                )
            except Exception:
                # 推理失败时忽略该条后向推理连接
                # print(f"后向环境推理失败：设备类型={dev_abs.get('name')}，能力类型={cap_abs.get('name')}")
                inf = None

            if isinstance(inf, dict) and inf.get("env_nodes"):
                for item in inf.get("env_nodes") or []:
                    env_name = item.get("environment")
                    trend_name = item.get("trend")
                    if not env_name or not trend_name:
                        continue
                    cap_id = get_or_add_ext_node(cap_abs.get("name"), NodeType.DEVICE_CAPABILITY_TYPE.value, scope=dev_abs.get("name"))
                    trend_id = get_or_add_ext_node(trend_name, NodeType.TREND.value)
                    env_id = get_or_add_ext_node(env_name, NodeType.ENVIRONMENT.value)
                    ext_edges.append({"from": cap_id, "to": trend_id, "type": EdgeType.CAUSES.value})
                    ext_edges.append({"from": trend_id, "to": env_id, "type": EdgeType.IS.value})
                    # print(f"后向环境扩展: 能力 {cap_abs.get('name')}({cap_id}) 导致 趋势 {trend_name}({trend_id}) 是 环境 {env_name}({env_id})")
            elif isinstance(inf, dict):
                env_node = inf.get("env_node", {})
                trend_node = inf.get("trend_node", {})
                env_name = env_node.get("name") if isinstance(env_node, dict) else None
                trend_name = trend_node.get("name") if isinstance(trend_node, dict) else None
                if env_name and trend_name:
                    cap_id = get_or_add_ext_node(cap_abs.get("name"), NodeType.DEVICE_CAPABILITY_TYPE.value, scope=dev_abs.get("name"))
                    trend_id = get_or_add_ext_node(trend_name, NodeType.TREND.value)
                    env_id = get_or_add_ext_node(env_name, NodeType.ENVIRONMENT.value)
                    ext_edges.append({"from": cap_id, "to": trend_id, "type": EdgeType.CAUSES.value})
                    ext_edges.append({"from": trend_id, "to": env_id, "type": EdgeType.IS.value})
                    # print(f"后向环境扩展: 能力 {cap_abs.get('name')}({cap_id}) 导致 趋势 {trend_name}({trend_id}) 是 环境 {env_name}({env_id})")

    # print(f"后向环境扩展后: 扩展节点数={len(ext_nodes)}, 扩展边数={len(ext_edges)}")

    # 汇总扩展子图：包含新增的抽象节点与关系，并更新最大节点计数
    extension = {
        "nodes": ext_nodes,
        "edges": ext_edges,
        "max_node_count": start_id + len(ext_nodes)
    }
    # print("环境扩展构建完成")
    # print(f"环境扩展节点总数: {len(ext_nodes)}")
    # print(f"环境扩展边总数: {len(ext_edges)}")
    return extension

#构建完整上下文图
def _build_complete_context_graph(SR: dict, extension: dict, device_mappings, capability_mappings) -> dict:
    # print("正在构建完整上下文图")
    nodes = []
    edges = []
    if isinstance(SR, dict):
        nodes.extend(SR.get("nodes", []))
        sr_edges = SR.get("edges", [])
        edges.extend(sr_edges)
    else:
        sr_edges = []
    if isinstance(extension, dict):
        ext_nodes = extension.get("nodes", [])
        ext_edges = extension.get("edges", [])
        nodes.extend(ext_nodes)
        edges.extend(ext_edges)
    else:
        ext_nodes = []
        ext_edges = []

    # 构建索引
    id_to_node = {n.get("id"): n for n in nodes}
    # 扩展图中的 使用 边：设备类型ID -> 能力类型ID
    ext_use_edges = [(e.get("from"), e.get("to")) for e in ext_edges if e.get("type") == EdgeType.USE.value]
    devtype_to_captype_ids = {}
    for frm, to in ext_use_edges:
        devtype_to_captype_ids.setdefault(frm, []).append(to)
    # 名称与类型到ID（可能多个）
    name_type_to_ids = {}
    for n in nodes:
        key = (n.get("name"), n.get("type"))
        name_type_to_ids.setdefault(key, []).append(n.get("id"))

    # original 设备ID -> 抽象设备类型名称
    device_id_to_devtype_name = {}
    for item in (device_mappings or []):
        orig = item.get("original") or (item.get("res") or {}).get("original_node")
        absn = item.get("abstract") or (item.get("res") or {}).get("new_node")
        if isinstance(orig, dict) and isinstance(absn, dict):
            device_id_to_devtype_name[orig.get("id")] = absn.get("name")

    # 构建设备类型名称 -> 设备类型节点ID（扩展图）
    devtype_name_to_id = {}
    for n in ext_nodes:
        if n.get("type") == NodeType.DEVICE_TYPE.value:
            devtype_name_to_id[n.get("name")] = n.get("id")

    # 添加设备实现关系（抽象设备类型 -> 具体设备）
    for orig_id, devtype_name in device_id_to_devtype_name.items():
        spec_ids = name_type_to_ids.get((id_to_node.get(orig_id, {}).get("name"), NodeType.SPECIFIC_DEVICE.value)) or [orig_id]
        abs_ids = name_type_to_ids.get((devtype_name, NodeType.DEVICE_TYPE.value)) or []
        if abs_ids and spec_ids:
            edges.append({"from": abs_ids[0], "to": spec_ids[0], "type": "实现"})

    # 添加能力实现关系（按设备类型作用域）
    for item in (capability_mappings or []):
        if not isinstance(item, dict):
            continue
        orig = item.get("original") or (item.get("res") or {}).get("original_node")
        absn = item.get("abstract") or (item.get("res") or {}).get("new_node")
        if not isinstance(orig, dict) or not isinstance(absn, dict):
            continue
        orig_cap_id = orig.get("id")
        orig_cap_name = orig.get("name")
        abs_cap_name = absn.get("name")

        # 找到该具体能力关联的具体设备（SR使用边）
        related_device_ids = []
        for e in sr_edges:
            if e.get("type") == EdgeType.USE.value:
                frm = id_to_node.get(e.get("from"))
                to = id_to_node.get(e.get("to"))
                if not frm or not to:
                    continue
                if frm.get("type") == NodeType.SPECIFIC_DEVICE.value and to.get("id") == orig_cap_id:
                    related_device_ids.append(frm.get("id"))
                elif to.get("type") == NodeType.SPECIFIC_DEVICE.value and frm.get("id") == orig_cap_id:
                    related_device_ids.append(to.get("id"))

        if not related_device_ids:
            # 无设备关联，退化为名字匹配的第一个抽象能力
            abs_ids = name_type_to_ids.get((abs_cap_name, NodeType.DEVICE_CAPABILITY_TYPE.value)) or []
            if abs_ids:
                edges.append({"from": abs_ids[0], "to": orig_cap_id, "type": "实现"})
            continue

        # 依据设备类型作用域选择对应的抽象能力节点
        for dev_id in dict.fromkeys(related_device_ids):
            devtype_name = device_id_to_devtype_name.get(dev_id)
            devtype_id = devtype_name_to_id.get(devtype_name)
            if devtype_id is None:
                # 找不到设备类型节点则跳过
                continue
            cand_cap_ids = [cid for cid in devtype_to_captype_ids.get(devtype_id, []) if id_to_node.get(cid, {}).get("type") == NodeType.DEVICE_CAPABILITY_TYPE.value and id_to_node.get(cid, {}).get("name") == abs_cap_name]
            if not cand_cap_ids:
                # 退化到名字匹配的第一个抽象能力
                cand_cap_ids = name_type_to_ids.get((abs_cap_name, NodeType.DEVICE_CAPABILITY_TYPE.value)) or []
            if cand_cap_ids:
                edges.append({"from": cand_cap_ids[0], "to": orig_cap_id, "type": "实现"})

    max_cnt = max(SR.get("max_node_count", 0), extension.get("max_node_count", 0))
    return {"nodes": nodes, "edges": edges, "max_node_count": max_cnt}
    
    # return {"nodes": nodes, "edges": edges, "max_node_count": max_cnt}

def _get_SYS_rule(cg: ContextGraph):
    """从上下文图中提取SYS_rule"""
    # 1. 根据使能关系获取所有的抽象设备能力
    # 前件作为Trigger
    # 后件作为Action
    # 注意：如果是指向同一个节点，那么要做去重

    # 2. 对于Trigger，获取到使用它的抽象设备类型，被获取的值和值与环境之间的边类型，以<抽象设备类型>.<抽象设备能力> <值与环境之间的边类型> <值>作为模板进行填充
    # 多个Trigger之间使用 AND 进行连接

    # 3. 对于Action，获取到使用它的抽象设备类型，以<抽象设备能力> <抽象设备类型>作为模板进行填充

    # 4. 以 IF <Trigger> THEN <Action> 的形式组装，形成sys_rule并返回
    G = cg.graph
    node_types = {nid: data.get("type") for nid, data in G.nodes(data=True)}
    node_names = {nid: data.get("name") for nid, data in G.nodes(data=True)}
    devtype = NodeType.DEVICE_TYPE.value
    captype = NodeType.DEVICE_CAPABILITY_TYPE.value
    value_t = NodeType.VALUE.value
    env_t = NodeType.ENVIRONMENT.value
    use_t = EdgeType.USE.value
    enable_t = EdgeType.ENABLE.value
    acquired_t = EdgeType.IS_ACQUIRED_BY.value
    env_ops = {
        EdgeType.HIGHER_THAN.value,
        EdgeType.HIGHER_AND_EQUAL.value,
        EdgeType.LOWER_THAN.value,
        EdgeType.LOWER_AND_EQUAL.value,
        EdgeType.EQUALS.value,
        EdgeType.NOT_EQUALS.value,
        EdgeType.OPERATOR_GREATER_THAN.value,
        EdgeType.OPERATOR_HIGHER_AND_EQUAL.value,
        EdgeType.OPERATOR_LOWER_THAN.value,
        EdgeType.OPERATOR_LOWER_AND_EQUAL.value,
        EdgeType.OPERATOR_EQUALS.value,
        EdgeType.OPERATOR_NOT_EQUALS.value
    }
    edges = list(G.edges(keys=True, data=True))
    cap_to_devtypes = {}
    for u, v, k, d in edges:
        if d.get("type") == use_t and node_types.get(u) == devtype and node_types.get(v) == captype:
            cap_to_devtypes.setdefault(v, set()).add(u)
    cap_to_values = {}
    for u, v, k, d in edges:
        if d.get("type") == acquired_t and node_types.get(u) == value_t and node_types.get(v) == captype:
            cap_to_values.setdefault(v, set()).add(u)
    value_to_envop = {}
    for u, v, k, d in edges:
        if d.get("type") in env_ops and node_types.get(u) == env_t and node_types.get(v) == value_t:
            value_to_envop.setdefault(v, []).append((u, d.get("type")))
    rules_by_action = {}
    for u, v, k, d in edges:
        if d.get("type") != enable_t:
            continue
        if node_types.get(u) != captype or node_types.get(v) != captype:
            continue
        triggers = set()
        devs = cap_to_devtypes.get(u, set())
        vals = cap_to_values.get(u, set())
        for dd in devs:
            dn = node_names.get(dd)
            for vv in vals:
                vn = node_names.get(vv)
                ops = value_to_envop.get(vv) or []
                opn = ops[0][1] if ops else None
                if dn and node_names.get(u) and vn and opn:
                    triggers.add(f"{dn}.{node_names.get(u)} {opn} {vn}")
        actions = set()
        devs_b = cap_to_devtypes.get(v, set())
        for dd in devs_b:
            dn = node_names.get(dd)
            if dn and node_names.get(v):
                actions.add(f"{node_names.get(v)} {dn}")
        if triggers and actions:
            act_key = tuple(sorted(actions))
            rb = rules_by_action.setdefault(act_key, set())
            rb.update(triggers)
    sys_rules = []
    for act_key, trig_set in rules_by_action.items():
        trig_str = " AND ".join(sorted(trig_set))
        act_str = " AND ".join(list(act_key))
        if trig_str and act_str:
            sys_rules.append(f"IF {trig_str} THEN {act_str}")
    if not sys_rules:
        return None
    return sys_rules

def SR2CG(SR: dict, debug: bool = False):
    with get_default_ai_client().usage_scope("SR2CG", scope_type="tool"):
        if SR is None:
            raise ValueError("SR is None")
        # 1. 解析软件层模型，获取具体的设备，能力和值
        devices = _extract_devices(SR)
        capabilities = _extract_capabilities(SR)
        values = _extract_values(SR)

        if debug:
            print(f"提取到的设备: {devices}")
            print(f"提取到的能力: {capabilities}")
            print(f"提取到的值: {values}")
        
        
        # 2. 对设备，能力进行抽象
        abstract_devices = [_abstract_device_node(device) for device in devices]
        abstract_capabilities = [_abstract_capability_node(capability) for capability in capabilities]

        if debug:
            print(f"抽象后的设备: {abstract_devices}")
            print(f"抽象后的能力: {abstract_capabilities}")
        

        # 3. 建立设备，能力的映射关系
        device_mappings = [{"original": d, "abstract": a["res"]["new_node"]} for d, a in zip(devices, abstract_devices)]
        capability_mappings = [{"original": c, "abstract": a["res"]["new_node"]} for c, a in zip(capabilities, abstract_capabilities)]
        if debug:
            print(f"设备映射关系: {device_mappings}")
            print(f"能力映射关系: {capability_mappings}")
        
        # 3. 基于抽象后的设备和能力，获取扩展部分（新版本签名仅两参）
        environment_extension = _build_environment_extension(SR, device_mappings, capability_mappings)
        if debug:
            print(f"环境扩展: {environment_extension}")
        
        # 4. 组装原有SR和扩展部分，构建完整上下文图
        complete_dict = _build_complete_context_graph(SR, environment_extension, device_mappings, capability_mappings)
        if debug:
            print(f"完整上下文图: {complete_dict}")
        
        # 5. 从完整上下文图中获取SYS_rule
        cg = json_to_context_graph(complete_dict)
        sys_rule = _get_SYS_rule(cg)
        device_types = [a["res"]["new_node"]["name"] for a in abstract_devices if isinstance(a, dict) and a.get("res")]
        return cg, sys_rule, device_types

def json_to_context_graph(json_data) -> ContextGraph:
    """将 JSON 上下文图转换为 ContextGraph（适配新版以数字ID为主）。"""
    cg = ContextGraph()

    # 兼容嵌套结构：如果有 output，则取其中的 nodes/edges
    if isinstance(json_data, dict) and "output" in json_data and isinstance(json_data["output"], dict):
        json_data = json_data["output"]

    # 原始ID到内部ID的映射
    id_map = {}

    # 添加节点（返回内部数字ID），保留原始 id 作为属性
    if "nodes" in json_data:
        for node in json_data["nodes"]:
            try:
                node_type = NodeType(node["type"])
            except ValueError:
                # 跳过未知类型的节点
                print(f"Warning: Unknown node type '{node.get('type')}' ignored.")
                continue
            internal_id = cg.add_node(node.get("name"), node_type, original_id=node.get("id"))
            id_map[node.get("id")] = internal_id

    # 添加边（使用内部数字ID）
    if "edges" in json_data:
        for edge in json_data["edges"]:
            src_internal = id_map.get(edge.get("from"))
            dst_internal = id_map.get(edge.get("to"))
            if src_internal is None or dst_internal is None:
                print(f"Warning: Edge skipped due to missing nodes from={edge.get('from')} to={edge.get('to')}")
                continue
            try:
                edge_type = EdgeType(edge.get("type"))
                cg.add_edge(src_internal, dst_internal, edge_type)
            except ValueError:
                operator_map = {
                    ">": EdgeType.OPERATOR_GREATER_THAN,
                    ">=": EdgeType.OPERATOR_HIGHER_AND_EQUAL,
                    "<": EdgeType.OPERATOR_LOWER_THAN,
                    "<=": EdgeType.OPERATOR_LOWER_AND_EQUAL,
                    "==": EdgeType.OPERATOR_EQUALS,
                    "!=": EdgeType.OPERATOR_NOT_EQUALS
                }
                t = edge.get("type")
                if t in operator_map:
                    cg.add_edge(src_internal, dst_internal, operator_map[t])
                else:
                    print(f"Warning: Unknown edge type '{t}' ignored.")

    return cg

if __name__ == "__main__":
    input_data = {
        "SR": {
            "nodes": [
                {
                    "id": 0,
                    "name": "livingroom_ac_pro",
                    "type": "specific_device"
                },
                {
                    "id": 1,
                    "name": "turn_cool_on()",
                    "type": "specific_capability"
                },
                {
                    "id": 2,
                    "name": "bedroom_temp_sensor_pro",
                    "type": "specific_device"
                },
                {
                    "id": 3,
                    "name": "reading()",
                    "type": "specific_capability"
                },
                {
                    "id": 4,
                    "name": "38",
                    "type": "value"
                },
                {
                    "id": 5,
                    "name": "hallway_presence_sensor_pro",
                    "type": "specific_device"
                },
                {
                    "id": 6,
                    "name": "reading()",
                    "type": "specific_capability"
                },
                {
                    "id": 7,
                    "name": "home",
                    "type": "value"
                }
            ],
            "edges": [
                {
                    "from": 0,
                    "to": 1,
                    "type": "使用"
                },
                {
                    "from": 2,
                    "to": 3,
                    "type": "使用"
                },
                {
                    "from": 3,
                    "to": 1,
                    "type": "使能"
                },
                {
                    "from": 4,
                    "to": 3,
                    "type": ">"
                },
                {
                    "from": 5,
                    "to": 6,
                    "type": "使用"
                },
                {
                    "from": 6,
                    "to": 1,
                    "type": "使能"
                },
                {
                    "from": 7,
                    "to": 6,
                    "type": "=="
                }
            ],
            "max_node_count": 8
        },
        "specific_devices": [
            "bedroom_temp_sensor_pro",
            "hallway_presence_sensor_pro",
            "livingroom_ac_pro"
        ],
        "success": True
    }
    SR = input_data["SR"]
    cg, sys_rule, device_types = SR2CG(SR, debug=True)
    # 打印可序列化的字典结构
    print(json.dumps(cg.to_dict(), indent=2, ensure_ascii=False))
    print(json.dumps({"SYS_rule": sys_rule, "device_types": device_types}, indent=2, ensure_ascii=False))
