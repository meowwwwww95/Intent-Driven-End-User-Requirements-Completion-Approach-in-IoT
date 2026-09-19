"""
质量模板可满足性判断工具

仅使用大模型判断输入设备列表能否满足质量模板中的某个 IRP 节点。
不做任何失败兜底或本地匹配逻辑，若模型不可用或返回格式不合规则抛出异常。

"""

from typing import List, Dict, Any
import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.ai_client import get_default_ai_client, simple_completion, create_system_message, create_user_message
from config.quality_template_config import get_quality_template_spec, print_quality_template

# TODO 这里的检索模板满足性，应该要放到功能设备上做对齐，仅在这篇论文里看的话

def _find_node_by_id(spec: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    """
    在层级字典模板中按 id 递归查找节点。
    """
    if spec.get("id") == node_id:
        return spec
    for child in spec.get("children", []) or []:
        r = _find_node_by_id(child, node_id)
        if r:
            return r
    return {}

def _get_irp_devices(irp_node: Dict[str, Any]) -> List[str]:
    """
    仅使用 IRP 自身的 data.devices 作为所需设备列表。
    若缺少该字段或不是列表，抛出异常。
    """
    data = irp_node.get("data", {}) or {}
    devices = data.get("devices")
    if not isinstance(devices, list) or len(devices) == 0:
        raise ValueError("IRP节点未提供设备列表(data.devices)")
    return devices


def _normalize_abstract_devices(abstract_devices: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(abstract_devices, list):
        return normalized

    for device in abstract_devices:
        if not isinstance(device, dict):
            continue
        dtype = (device.get("type") or "").strip()
        if not dtype:
            continue

        capabilities: List[Dict[str, Any]] = []
        for cap in (device.get("capabilities") or []):
            if not isinstance(cap, dict):
                continue
            name = (cap.get("name") or "").strip()
            cap_type = (cap.get("capabilityType") or "").strip().lower()
            if not name or cap_type not in ("trigger", "action"):
                continue
            capabilities.append(
                {
                    "name": name,
                    "description": cap.get("description", "") if isinstance(cap.get("description"), str) else "",
                    "capabilityType": cap_type,
                }
            )

        normalized.append(
            {
                "type": dtype,
                "description": device.get("description", "") if isinstance(device.get("description"), str) else "",
                "capabilities": capabilities,
            }
        )
    return normalized


def _normalize_prune_input(devices: Any) -> Dict[str, Any]:
    device_names: List[str] = []
    abstract_devices: List[Dict[str, Any]] = []

    if isinstance(devices, dict):
        raw_names = devices.get("device_names")
        if not isinstance(raw_names, list):
            raw_names = devices.get("devices")
        if isinstance(raw_names, list):
            for item in raw_names:
                if isinstance(item, str):
                    name = item.strip()
                    if name and name not in device_names:
                        device_names.append(name)
        abstract_devices = _normalize_abstract_devices(devices.get("abstract_devices"))
    elif isinstance(devices, list):
        for item in devices:
            if isinstance(item, str):
                name = item.strip()
                if name and name not in device_names:
                    device_names.append(name)

    return {
        "device_names": device_names,
        "abstract_devices": abstract_devices,
    }


def _extract_irp_capability_requirements(irp_id: str) -> List[Dict[str, str]]:
    text = (irp_id or "").strip()
    if not text.startswith("IF "):
        return []

    condition_text, sep, action_text = text[3:].partition(" THEN ")
    if not sep:
        return []

    requirements: List[Dict[str, str]] = []

    trigger_device, dot, trigger_rest = condition_text.partition(".")
    trigger_capability = trigger_rest.split(" ", 1)[0].strip() if dot else ""
    trigger_device = trigger_device.strip()
    if trigger_device and trigger_capability:
        requirements.append(
            {
                "device": trigger_device,
                "capability": trigger_capability,
                "capabilityType": "trigger",
            }
        )

    action_capability, action_sep, action_device = action_text.strip().partition(" ")
    action_device = action_device.strip()
    if action_sep and action_capability and action_device:
        requirements.append(
            {
                "device": action_device,
                "capability": action_capability.strip(),
                "capabilityType": "action",
            }
        )

    return requirements


def _check_irp_satisfiable(devices: Any, irp_id: str, template_spec: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    判断给定设备列表是否可满足质量模板中的某个 IRP 节点（仅比较 IRP.data.devices）。

    逻辑：
    1) 读取模板并定位 IRP；提取其 data.devices 为所需设备列表
    2) 调用大模型进行语义判断（只返回 JSON）

    Args:
        devices: 输入设备上下文，可为设备名列表，或包含 device_names/abstract_devices 的字典
        irp_id: 质量模板中 IRP 节点的 id（规则字符串）
        template_spec: 可选的质量模板字典；不传则使用默认模板

    Returns:
        Dict[str, Any]: {satisfiable, missing_devices, reason}
    """
    spec = template_spec or get_quality_template_spec()
    irp = _find_node_by_id(spec, irp_id)
    if not irp or irp.get("type") != "IRP":
        return {"satisfiable": False, "missing_devices": ["IRP节点不存在或类型错误"], "reason": "找不到指定IRP"}
    required_devices = _get_irp_devices(irp)
    prune_input = _normalize_prune_input(devices)

    payload = {
        "irp_id": irp.get("id"),
        "devices_provided": prune_input.get("device_names") or [],
        "abstract_devices_provided": prune_input.get("abstract_devices") or [],
        "devices_required": required_devices,
        "required_capabilities": _extract_irp_capability_requirements(irp.get("id", "")),
    }
    sys = create_system_message(
        """
你是智能家居质量模板校验助手。比较 devices_provided 与 abstract_devices_provided 是否能满足 devices_required。
请把 abstract_devices_provided 当作主要的设备抽象知识来源，优先参考其中的抽象设备 type、description、capabilities 以及 capabilityType 做语义判断。
required_capabilities 给出了从 IRP 中解析出的触发/动作能力需求，你需要结合 abstract_devices_provided 中的 capabilities 判断这些能力是否已经被当前环境覆盖。
即使 devices_provided 里的具体设备名看不出语义，只要 abstract_devices_provided 已经提供足够的抽象知识，也应判定为满足。
如果 devices_required 要求的是一个通用类别（如“所有影音、照明、温控及厨电类电器”），而 devices_provided 中包含了该类别下的部分具体设备，则认为该类别要求被部分满足，应当通过。判断时应以设备的能力描述为准，而不是仅看设备名。
但是，如果缺少关键的设备，例如 devices_required 需要 door作为一个出入控制设备， 而 devices_provided 中没有提供，则认为该类别要求完全未被满足，应当失败。
        """
        "考虑同义词与中英文别名。只返回JSON，字段为satisfiable(boolean), missing_devices(list[str]), reason(string)。"
    )
    usr = create_user_message(json.dumps(payload, ensure_ascii=False))
    ans = simple_completion([sys, usr], use_json=True, temperature=0.0)
    obj = json.loads(ans)
    # print(f"剪枝结果: {json.dumps(obj, ensure_ascii=False, indent=2)}")
    if not isinstance(obj, dict) or not {"satisfiable", "missing_devices", "reason"}.issubset(obj.keys()):
        raise ValueError("模型未返回有效JSON：必须包含satisfiable/missing_devices/reason")
    return obj



def prune_quality_template(devices: Any, template_spec: Dict[str, Any] = None) -> Dict[str, Any]:#
    """
    迭代剪除不可满足的 IRP 以及因此成为叶子的 INT。

    流程：
    - 扫描所有 IRP，调用模型判断不可满足则剪除
    - 之后剪除所有成为叶子的 INT（children 为空）
    - 循环直到没有任何节点被剪除

    Returns:
        Dict[str, Any]: {spec, removed_irp, removed_int}
    """
    with get_default_ai_client().usage_scope("QT_search.prune_quality_template", scope_type="tool"):
        base = template_spec or get_quality_template_spec()
        spec = json.loads(json.dumps(base, ensure_ascii=False))
        removed_irp: List[str] = []
        removed_int: List[str] = []

        def prune_irp(node: Dict[str, Any]) -> bool:
            changed = False
            ch = node.get("children", []) or []
            new_children: List[Dict[str, Any]] = []
            for c in ch:
                if c.get("type") == "IRP":
                    res = _check_irp_satisfiable(devices, c.get("id"), template_spec=spec)
                    if not res.get("satisfiable", False):
                        removed_irp.append(c.get("id"))
                        changed = True
                        continue
                if prune_irp(c):
                    changed = True
                new_children.append(c)
            node["children"] = new_children
            return changed

        def prune_int_leaves(node: Dict[str, Any]) -> bool:
            changed = False
            for c in node.get("children", []) or []:
                if prune_int_leaves(c):
                    changed = True
            ch = node.get("children", []) or []
            new_children: List[Dict[str, Any]] = []
            for c in ch:
                if c.get("type") == "INT":
                    is_leaf = len(c.get("children", []) or []) == 0
                    data = c.get("data", {}) or {}
                    is_constraint = data.get("isConstraint")
                    if is_leaf and (is_constraint is None or is_constraint is False):
                        removed_int.append(c.get("id"))
                        changed = True
                        continue
                new_children.append(c)
            node["children"] = new_children
            return changed

        while True:
            c1 = prune_irp(spec)
            c2 = prune_int_leaves(spec)
            if not (c1 or c2):
                break

        return {"spec": spec, "removed_irp": removed_irp, "removed_int": removed_int}

__all__ = ["prune_quality_template"]

def main():
    # cases = [
    # ]
    # for devices, irp_id in cases:
    #     try:
    #         res = _check_irp_satisfiable(devices, irp_id)
    #         print(json.dumps({"irp_id": irp_id, "devices": devices, "result": res}, ensure_ascii=False))
    #     except Exception as e:
    #         print(json.dumps({"irp_id": irp_id, "devices": devices, "error": str(e)}, ensure_ascii=False))

    # 剪枝测试：根据设备能力对质量模板进行剪枝，并输出剪枝后的模板
    devices_for_prune = [
    "bedroom_temp_sensor_pro",
    "livingroom_ac_pro",
    "hallway_presence_sensor_pro",
    "bedroom_curtain_motor_pro",
    "kitchen_smoke_detector_pro",
    "entryway_siren_pro",
    "livingroom_ceiling_light_pro",
    "robot_vacuum_pro",
    "tower_fan_pro"
  ]
    pruned = prune_quality_template(devices_for_prune)
    print(json.dumps({
        "devices": devices_for_prune,
        "removed_irp": pruned["removed_irp"],
        "removed_int": pruned["removed_int"]
    }, ensure_ascii=False, indent=2))

    print(print_quality_template(pruned["spec"]))

if __name__ == "__main__":
    main()
