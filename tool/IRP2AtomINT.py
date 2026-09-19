"""
IRP2AtomINT

将 SYS 图转换为原子 INT 规则的工具函数。

仅对外暴露一个入口函数：
- generate_atom_int_rule(SYS): 内部依次调用 _extract_env_value_trend_subgraph -> _compose_atom_int_rule。
  支持两种输入：
  - ContextGraph 实例（推荐）
  - 字典结构的 SYS（包含 nodes/edges/max_node_count）
"""

from collections import deque
from typing import Any, Deque, Dict, List, Set
from model.context_graph import ContextGraph
from tool.TAP_extraction import extract_tap_rule


def _ensure_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    基本结构校验：要求包含 nodes 与 edges，且字段完整。
    """
    if not isinstance(graph, dict):
        raise TypeError("SYS 必须是字典")
    if "nodes" not in graph or "edges" not in graph:
        raise ValueError("SYS 需包含 'nodes' 与 'edges'")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise TypeError("'nodes' 与 'edges' 必须是列表")
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n or "name" not in n or "type" not in n:
            raise ValueError("节点需包含 id、name、type")
    for e in edges:
        if not isinstance(e, dict) or "from" not in e or "to" not in e or "type" not in e:
            raise ValueError("边需包含 from、to、type")
    return {"nodes": nodes, "edges": edges}

def _extract_env_value_trend_subgraph(sys_graph_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    输入 SYS 全图，返回仅包含 type 为 environment、value、trend 的子图。
    遍历起点为所有 environment 节点，保留这些目标节点之间的边，边顺序与原图一致。
    """
    sys_graph = _ensure_graph(sys_graph_input)

    nodes_by_id: Dict[int, Dict[str, Any]] = {node["id"]: node for node in sys_graph["nodes"]}
    adjacency: Dict[int, List[Dict[str, Any]]] = {node["id"]: [] for node in sys_graph["nodes"]}

    for edge in sys_graph["edges"]:
        f = edge["from"]
        t = edge["to"]
        adjacency.setdefault(f, []).append({"node": t, "edge": edge})
        adjacency.setdefault(t, []).append({"node": f, "edge": edge})

    env_ids: List[int] = [node["id"] for node in sys_graph["nodes"] if node["type"] == "environment"]
    visited: Set[int] = set()
    target_ids: Set[int] = set()

    def bfs(start_id: int) -> None:
        q: Deque[int] = deque([start_id])
        visited.add(start_id)
        while q:
            current = q.popleft()
            info = nodes_by_id.get(current)
            if not info:
                continue
            if info["type"] in ("environment", "value", "trend"):
                target_ids.add(current)
            for nb in adjacency.get(current, []):
                nid = nb["node"]
                edge = nb["edge"]
                ninfo = nodes_by_id.get(nid)
                if not ninfo:
                    continue
                if ninfo["type"] in ("environment", "value", "trend"):
                    target_ids.add(nid)
                    if nid not in visited:
                        visited.add(nid)
                        q.append(nid)

    for env_id in env_ids:
        if env_id not in visited:
            bfs(env_id)

    filtered_nodes: List[Dict[str, Any]] = sorted([nodes_by_id[i] for i in target_ids], key=lambda n: n["id"])
    filtered_edges: List[Dict[str, Any]] = [
        edge
        for edge in sys_graph["edges"]
        if edge["from"] in target_ids and edge["to"] in target_ids
    ]

    return {"nodes": filtered_nodes, "edges": filtered_edges}

def _compose_atom_int_rule(subgraph: Dict[str, Any]) -> Dict[str, Any]:
    """
    输入 func1 的子图输出，生成规则字符串：
    - THEN 部分：所有 type 为“是”的边，格式“X Y”。
    - IF 部分：其余边，格式“X type Y”，以 AND 连接。
    """
    graph = _ensure_graph(subgraph)
    nodes_by_id: Dict[int, str] = {node["id"]: node["name"] for node in graph["nodes"]}

    then_edges: List[Dict[str, Any]] = []
    remaining_edges: List[Dict[str, Any]] = []
    for edge in graph["edges"]:
        if edge["type"] == "是":
            then_edges.append(edge)
        else:
            remaining_edges.append(edge)

    then_parts: List[str] = [f"{nodes_by_id[e['from']]} {nodes_by_id[e['to']]}" for e in then_edges]
    if_parts: List[str] = [f"{nodes_by_id[e['from']]} {e['type']} {nodes_by_id[e['to']]}" for e in remaining_edges]

    if_str = " AND ".join(if_parts)
    then_str = " AND ".join(then_parts)
    rule = f"IF {if_str} THEN {then_str}" if if_str and then_str else "无法构建规则：没有找到有效的边"

    return {"rule": rule, "if_parts": if_parts, "then_parts": then_parts}

def generate_atom_int_rule(sys_graph: Any) -> str:
    """
    工具入口：输入 ContextGraph 或字典 SYS，先提取 environment/value/trend 子图，再生成规则。
    """
    if isinstance(sys_graph, ContextGraph):
        graph_dict = sys_graph.to_dict()
    elif isinstance(sys_graph, dict):
        graph_dict = sys_graph
    else:
        raise TypeError("generate_atom_int_rule 仅支持 ContextGraph 或 dict(SYS) 输入")

    atom_int_rule = _compose_atom_int_rule(_extract_env_value_trend_subgraph(graph_dict)).get("rule", "无法构建规则")
    return atom_int_rule

def batch_tap_to_irp_and_atom_int(tap_rules: List[str]) -> List[Dict[str, Any]]:
    """
    批处理方法：输入 TAP 规则列表，输出每条 TAP 对应的 IRP 和 Atom_INT。

    参数：
        tap_rules: List[str] - 多条 TAP 规则字符串组成的列表。

    返回：
        List[Dict[str, Any]] - 每条规则对应的结果字典列表，包含：
            {
                "tap": 原始 TAP 字符串,
                "IRP": SR 图（字典结构：nodes/edges/max_node_count），若解析失败则为 None,
                "Atom_INT": 原子意图字符串，若解析失败或无法构建则为提示字符串,
                "success": 解析是否成功
            }
    """
    if not isinstance(tap_rules, list):
        raise TypeError("tap_rules 必须是字符串列表")

    results: List[Dict[str, Any]] = []
    for tap in tap_rules:
        if not isinstance(tap, str):
            results.append({
                "tap": tap,
                "IRP": None,
                "Atom_INT": "输入类型错误：TAP 必须为字符串",
                "success": False,
            })
            continue

        parsed = extract_tap_rule(tap)
        success = bool(parsed.get("success", False))
        sr_graph = parsed.get("SR") if success else None

        if success and sr_graph:
            try:
                atom_int = generate_atom_int_rule(sr_graph)
            except Exception as e:
                atom_int = f"无法构建规则：{e}"
        else:
            atom_int = "解析 TAP 失败，无法构建规则"

        results.append({
            "tap": tap,
            "IRP": sr_graph,
            "Atom_INT": atom_int,
            "success": success,
        })

    return results


__all__ = ["generate_atom_int_rule", "batch_tap_to_irp_and_atom_int"]