import networkx as nx
from enum import Enum

class NodeType(Enum):
    """
    图中的节点类型，根据元模型定义。
    """
    SPECIFIC_DEVICE = "specific_device"
    DEVICE_TYPE = "device_type"
    SPECIFIC_CAPABILITY = "specific_capability"
    DEVICE_CAPABILITY_TYPE = "device_capability_type"
    ENVIRONMENT = "environment"
    VALUE = "value"
    TREND = "trend"

class EdgeType(Enum):
    """
    图中的边类型，根据元模型定义。
    """
    USE = "使用"
    ENABLE = "使能"
    IS_ACQUIRED_BY = "被获取"
    CAUSES = "导致"
    IS = "是"
    IMPLEMENTS = "实现"
    
    HIGHER_THAN = "高于"
    HIGHER_AND_EQUAL = "高于等于"
    LOWER_THAN = "低于"
    LOWER_AND_EQUAL = "低于等于"
    EQUALS = "等于"
    NOT_EQUALS = "不等于"

    OPERATOR_GREATER_THAN = ">"
    OPERATOR_HIGHER_AND_EQUAL = ">="
    OPERATOR_LOWER_THAN = "<"
    OPERATOR_LOWER_AND_EQUAL = "<="
    OPERATOR_EQUALS = "=="
    OPERATOR_NOT_EQUALS = "!="
    

class ContextGraph:
    """
    基于元模型定义上下文图的数据结构。

    使用 `networkx.MultiDiGraph` 存储，以数字ID作为节点唯一标识；
    节点的显示名称存放在属性 `name` 中，类型在 `type` 中。
    """
    def __init__(self):
        self.graph = nx.MultiDiGraph()
        self._next_id = 0

    def add_node(self, name: str, node_type: NodeType, **kwargs) -> int:
        """
        添加一个新节点并返回其数字ID。

        不基于名称去重，允许同名节点并存。
        """
        node_id = self._next_id
        self._next_id += 1
        self.graph.add_node(node_id, name=name, type=node_type.value, **kwargs)
        return node_id

    def add_edge(self, source_id: int, target_id: int, edge_type: EdgeType, **kwargs):
        """
        添加一条边（按数字ID）。
        """
        if not self.graph.has_node(source_id):
            raise ValueError(f"源节点 {source_id} 不存在。")
        if not self.graph.has_node(target_id):
            raise ValueError(f"目标节点 {target_id} 不存在。")
        key = edge_type.value
        if self.graph.has_edge(source_id, target_id, key=key):
            return
        self.graph.add_edge(source_id, target_id, key=key, type=key, **kwargs)

    def __str__(self):
        """
        返回图的字符串表示形式。
        """
        return f"ContextGraph 包含 {self.graph.number_of_nodes()} 个节点和 {self.graph.number_of_edges()} 条边。"

    def to_dict(self) -> dict:
        nodes = []
        for node_id, node_data in self.graph.nodes(data=True):
            nodes.append({
                "id": node_id,
                "name": node_data.get('name'),
                "type": node_data.get('type')
            })

        edges = []
        for u, v, k, edge_data in self.graph.edges(keys=True, data=True):
            edges.append({
                "from": u,
                "to": v,
                "type": edge_data.get('type')
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "max_node_count": len(nodes)
        }
