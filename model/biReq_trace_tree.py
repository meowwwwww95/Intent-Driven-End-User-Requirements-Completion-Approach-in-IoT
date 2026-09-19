"""
需求双向追溯树模型

该数据结构用于表达 DSL→IRP→INT→INT_TYPE→ROOT 的层级约束，并支持：
- 自下而上的回溯（祖先、路径到根）
- 自上而下的展开（后代、叶子）
- 增删改查（节点ID、类型、数据）

约束解释：
- “X只能作为Y的叶子节点出现”在实现中理解为“X只能作为Y的直接子节点出现”，以确保父子类型合法。
- 支持 INT→INT 的层层细化；完整链路可为 ROOT→INT_TYPE→INT→IRP→DSL。
"""
from enum import Enum
from typing import Dict, Optional, List, Set, Any
import json
import os
import threading


class NodeKind(Enum):
    """
    节点类型枚举。

    - DSL: 领域特定语言的最底层表示，只能挂在 IRP 下
    - IRP: 中间过程表示，只能挂在 INT 下
    - INT: 原子/组合意图，既可挂在 INT（细化）也可挂在 INT_TYPE 下
    - INT_TYPE: 意图的类型层级，只能挂在 ROOT 下
    - ROOT: 根节点，唯一存在
    """
    DSL = "DSL"
    IRP = "IRP"
    INT = "INT"
    INT_TYPE = "INT_TYPE"
    ROOT = "ROOT"


class TreeNode:
    """
    树节点结构。

    维护：
    - id: 节点唯一标识
    - kind: 节点类型（NodeKind）
    - data: 附加数据
    - parent: 父节点ID（最多一个）
    - children: 子节点ID集合
    """
    def __init__(self, node_id: str, kind: NodeKind, data: Optional[Dict[str, Any]] = None):
        self.id = node_id
        self.kind = kind
        self.data: Dict[str, Any] = data or {}
        self.parent: Optional[str] = None
        self.children: Set[str] = set()


class BiReqTraceTree:
    """
    需求双向追溯树。

    功能：
    - 严格的父子类型约束
    - 上下双向遍历（祖先/后代）
    - 增删改查操作（ID/类型/数据）
    - 序列化为字典结构
    """
    def __init__(self):
        self.nodes: Dict[str, TreeNode] = {}
        self.root_id: Optional[str] = None
        self._lock = threading.RLock()
        # 约束：key 为子类型，value 为允许的父类型集合
        self.allowed_parents: Dict[NodeKind, Set[NodeKind]] = {
            NodeKind.DSL: {NodeKind.IRP},
            NodeKind.IRP: {NodeKind.INT},
            NodeKind.INT: {NodeKind.INT, NodeKind.INT_TYPE},
            NodeKind.INT_TYPE: {NodeKind.ROOT},
            NodeKind.ROOT: set(),
        }

    def _default_child_relation_by_kind(self, child_kind: NodeKind) -> Optional[str]:
        """
        返回父节点面对某类子节点时的默认组合关系。

        当前版本采用简化假设：
        - INT / INT_TYPE 子节点按 AND 组合
        - IRP / DSL 子节点按 OR 组合

        TODO: 后续需要支持更细粒度的可配置关系定义，不能只按子节点类型做全局假设。
        """
        if child_kind in {NodeKind.INT, NodeKind.INT_TYPE}:
            return "AND"
        if child_kind in {NodeKind.IRP, NodeKind.DSL}:
            return "OR"
        return None

    def _normalize_child_relations(self, value: Any) -> Dict[str, str]:
        rels: Dict[str, str] = {}
        if not isinstance(value, dict):
            return rels
        for k, v in value.items():
            key = str(k or "").strip()
            val = str(v or "").strip().upper()
            if not key or val not in {"AND", "OR"}:
                continue
            rels[key] = val
        return rels

    def _set_child_relation(self, parent_id: str, child_kind: NodeKind) -> None:
        if parent_id not in self.nodes:
            return
        relation = self._default_child_relation_by_kind(child_kind)
        if not relation:
            return
        parent = self.nodes[parent_id]
        rels = self._normalize_child_relations(parent.data.get("child_relations"))
        if child_kind.value not in rels:
            rels[child_kind.value] = relation
        parent.data["child_relations"] = rels

    def _infer_and_backfill_child_relations(self, node_id: str) -> Dict[str, str]:
        if node_id not in self.nodes:
            return {}
        node = self.nodes[node_id]
        rels = self._normalize_child_relations(node.data.get("child_relations"))
        for cid in list(node.children):
            child = self.nodes.get(cid)
            if not child:
                continue
            default_rel = self._default_child_relation_by_kind(child.kind)
            if default_rel and child.kind.value not in rels:
                rels[child.kind.value] = default_rel
        if rels:
            node.data["child_relations"] = rels
        return rels

    def add_root(self, node_id: str = "ROOT", data: Optional[Dict[str, Any]] = None) -> None:
        """
        添加唯一根节点。

        Args:
            node_id: 根节点ID，默认 "ROOT"
            data: 根节点的附加数据

        Raises:
            ValueError: 根已存在或ID冲突
        """
        with self._lock:
            if self.root_id is not None:
                raise ValueError("根节点已存在")
            if node_id in self.nodes:
                raise ValueError("节点ID已存在")
            node = TreeNode(node_id, NodeKind.ROOT, data)
            self.nodes[node_id] = node
            self.root_id = node_id

    def add_child(self, parent_id: str, child_id: str, child_kind: NodeKind, data: Optional[Dict[str, Any]] = None) -> None:
        """
        在父节点下添加子节点，校验父子类型合法性。

        Args:
            parent_id: 父节点ID（必须已存在）
            child_id: 子节点ID（可复用未绑定节点）
            child_kind: 子节点类型
            data: 子节点附加数据

        Raises:
            ValueError: 父不存在、子已绑定、类型不合法
        """
        with self._lock:
            if parent_id not in self.nodes:
                raise ValueError("父节点不存在")
            if child_id in self.nodes and self.nodes[child_id].parent is not None:
                raise ValueError("子节点已绑定父节点")
            parent_kind = self.nodes[parent_id].kind
            # 校验：子类型的允许父集合中必须包含当前父类型
            if child_kind not in self.allowed_parents or parent_kind not in self.allowed_parents[child_kind]:
                raise ValueError("不允许的父子类型关系")
            if child_id not in self.nodes:
                self.nodes[child_id] = TreeNode(child_id, child_kind, data)
            child = self.nodes[child_id]
            child.kind = child_kind
            child.data.update(data or {})
            child.parent = parent_id
            self.nodes[parent_id].children.add(child_id)
            self._set_child_relation(parent_id, child_kind)

    def can_add_child(self, parent_id: str, child_kind: NodeKind) -> bool:
        """
        检查是否可在指定父节点下添加该类型的子节点。
        """
        with self._lock:
            if parent_id not in self.nodes:
                return False
            parent_kind = self.nodes[parent_id].kind
            return child_kind in self.allowed_parents and parent_kind in self.allowed_parents[child_kind]

    def has_node(self, node_id: str) -> bool:
        """
        检查节点是否存在。
        """
        with self._lock:
            return node_id in self.nodes

    def ensure_root_node(self, root_name: str = 'IoT系统') -> str:
        """
        确保根节点存在。如果不存在则创建。
        尝试兼容 'ROOT' 和 指定的 root_name。
        """
        with self._lock:
            if self.has_node(root_name):
                return root_name
            
            if self.has_node('ROOT'):
                return 'ROOT'
                
            try:
                self.add_root(root_name)
                return root_name
            except ValueError:
                if self.has_node(root_name):
                    return root_name
                if self.has_node('ROOT'):
                    return 'ROOT'
                raise

    def ensure_int_type_node(self, type_id: str) -> str:
        """
        确保意图类型节点（如 '功能意图', '质量意图'）存在。
        """
        with self._lock:
            if self.has_node(type_id):
                return type_id
                
            root_id = self.ensure_root_node()
            try:
                self.add_child(root_id, type_id, NodeKind.INT_TYPE)
            except ValueError:
                pass
            return type_id

    def ensure_original_intent_node(self, type_id: str, original_intent: str) -> str:
        """
        确保原始意图节点存在于指定的意图类型节点下。
        """
        with self._lock:
            if self.has_node(original_intent):
                return original_intent
                
            self.ensure_int_type_node(type_id)
            try:
                self.add_child(type_id, original_intent, NodeKind.INT, {"isNew": False})
            except ValueError:
                pass
            return original_intent

    def ensure_unique_id(self, base_id: str) -> str:
        """
        确保 ID 唯一，如果存在则添加 (副本N) 后缀。
        """
        with self._lock:
            s = base_id or ""
            if not self.has_node(s):
                return s
            idx = 1
            nid = f"{s} (副本{idx})"
            while self.has_node(nid):
                idx += 1
                nid = f"{s} (副本{idx})"
            return nid

    def attach_realization_result(self, original_id: str, atomic_user_intent: str, irp_text: str, taps: List[str], is_refined: bool) -> None:
        """
        将生成的 IRP 和 TAP 挂载到 trace tree 上。
        用于 RealizationPlanner。
        """
        with self._lock:
            # 1. 尝试合并逻辑：如果意图没有被精化，且 IRP 节点已经存在于 original_id 下
            if not is_refined and self.has_node(irp_text):
                try:
                     parent = self.get_parent(irp_text)
                     if parent == original_id:
                        existing_children = set(self.get_children(irp_text))
                        for t in (taps or []):
                            if t in existing_children:
                                continue
                            
                            did = t
                            if self.has_node(did):
                                 p = self.get_parent(did)
                                 if p != irp_text:
                                     did = self.ensure_unique_id(did)
                            
                            try:
                                self.add_child(irp_text, did, NodeKind.DSL)
                            except ValueError:
                                pass
                        return
                except ValueError:
                    pass

            taps_nodes = [{"id": t, "type": "DSL"} for t in (taps or [])]
            
            if is_refined:
                subtree = {
                    "id": atomic_user_intent,
                    "type": "INT",
                    "data": {"isNew": False},
                    "children": [
                        {
                            "id": irp_text,
                            "type": "IRP",
                            "children": taps_nodes
                        }
                    ]
                }
            else:
                subtree = {
                    "id": irp_text,
                    "type": "IRP",
                    "children": taps_nodes
                }

            try:
                self.attach_subtree_from_hier(original_id, subtree)
            except Exception:
                self._manual_attach_fallback(original_id, atomic_user_intent, irp_text, taps, is_refined)

    def _manual_attach_fallback(self, original_id: str, atomic_user_intent: str, irp_text: str, taps: List[str], is_refined: bool):
        """挂载失败后的手动回退处理"""
        with self._lock:
            current_parent = original_id
            
            if is_refined:
                if not self.has_node(atomic_user_intent):
                    try:
                        self.add_child(current_parent, atomic_user_intent, NodeKind.INT, {"isNew": False})
                    except ValueError:
                        pass
                current_parent = atomic_user_intent

            if not self.has_node(irp_text):
                try:
                    self.add_child(current_parent, irp_text, NodeKind.IRP)
                except ValueError:
                    pass
            
            for tap in (taps or []):
                tap_id = tap
                if self.has_node(tap_id):
                    parent = self.get_parent(tap_id)
                    if parent != irp_text:
                        tap_id = self.ensure_unique_id(tap_id)
                
                try:
                    self.add_child(irp_text, tap_id, NodeKind.DSL)
                except ValueError:
                    pass

    def attach_inference_result(self, atom_int: str, irp: str, tap_rule: str) -> None:
        """
        将推断结果（Atom_INT -> IRP -> DSL）挂载到 Trace Tree。
        用于 IntentReader。
        """
        with self._lock:
            self.ensure_root_node('IoT系统')
            self.ensure_int_type_node('功能意图')
            
            subtree = {
                "id": atom_int,
                "type": "INT",
                "data": {
                    "isNew": True,
                },
                "children": [
                    {
                        "id": irp,
                        "type": "IRP",
                        "children": [
                            {
                                "id": tap_rule,
                                "type": "DSL"
                            }
                        ]
                    }
                ]
            }
            
            try:
                self.attach_subtree_from_hier('功能意图', subtree)
            except Exception:
                prefix = "(重复)"
                subtree_alt = {
                    "id": f"{prefix}{atom_int}",
                    "type": "INT",
                    "children": [
                        {
                            "id": f"{prefix}{irp}",
                            "type": "IRP",
                            "children": [
                                {
                                    "id": f"{prefix}{tap_rule}",
                                    "type": "DSL"
                                }
                            ]
                        }
                    ]
                }
                try:
                    self.attach_subtree_from_hier('功能意图', subtree_alt)
                except Exception:
                    pass

    def attach_quality_result(self, quality_spec: Dict[str, Any]) -> None:
        """
        将质量规划生成的子树挂载到 Trace Tree。
        用于 QualityPlanner。
        """
        with self._lock:
            self.ensure_root_node('IoT系统')
            quality_root_id = quality_spec.get("id") if isinstance(quality_spec, dict) else None
            
            if quality_root_id and self.has_node(quality_root_id):
                self.remove_node(quality_root_id)
            
            try:
                self.attach_subtree_from_hier('IoT系统', quality_spec)
            except Exception as e:
                conflict_ids: list[str] = []
                if isinstance(quality_spec, dict):
                    stack = [quality_spec]
                    while stack:
                        n = stack.pop()
                        nid = n.get("id")
                        if isinstance(nid, str) and nid in self.nodes:
                            try:
                                if self.nodes[nid].parent is not None:
                                    conflict_ids.append(nid)
                            except Exception:
                                pass
                        ch = n.get("children", []) or []
                        for c in ch:
                            if isinstance(c, dict):
                                stack.append(c)
                conflict_str = ", ".join(conflict_ids[:10])
                if len(conflict_ids) > 10:
                    conflict_str = conflict_str + f"...(+{len(conflict_ids)-10})"
                print(f"挂载质量子树失败: {e}")
                print(f"根节点: {self.root_id}")
                print(f"质量子树根: {quality_root_id}")
                if conflict_ids:
                    print(f"已绑定的冲突节点: {conflict_str}")

    def merge_duplicate_nodes(self) -> None:
        """
        合并标记为 (重复) 的节点。
        """
        with self._lock:
            def _strip(s: str) -> tuple[bool, str]:
                p = "(重复)"
                return (s.startswith(p), s[len(p):]) if isinstance(s, str) else (False, s)

            kinds = [NodeKind.INT, NodeKind.IRP, NodeKind.DSL]
            for k in kinds:
                nids = self.find_by_type(k)
                for nid in nids:
                    is_dup, base = _strip(nid)
                    if not is_dup:
                        continue
                    
                    if self.has_node(base):
                        try:
                            base_children = set(self.get_children(base))
                            dup_children = list(self.get_children(nid))
                            
                            for cid in dup_children:
                                if cid in base_children:
                                    self.nodes[nid].children.discard(cid)
                                else:
                                    self.nodes[nid].children.discard(cid)
                                    self.nodes[base].children.add(cid)
                                    self.nodes[cid].parent = base
                            
                            dup_node = self.get_node(nid)
                            if dup_node.get("data"):
                                self.update_node_data(base, dup_node["data"])
                            
                            self.remove_node(nid)
                        except Exception:
                            pass
                    else:
                        try:
                            self.update_node_id(nid, base)
                        except ValueError:
                            pass

    def get_node(self, node_id: str) -> Dict[str, Any]:
        """
        获取节点详情（类型、父、子、数据）。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            n = self.nodes[node_id]
            self._infer_and_backfill_child_relations(node_id)
            return {
                "id": n.id,
                "type": n.kind.value,
                "parent": n.parent,
                "children": list(n.children),
                "data": dict(n.data),
            }

    def get_children(self, node_id: str) -> List[str]:
        """
        获取指定节点的子节点ID列表。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            return list(self.nodes[node_id].children)

    def get_parent(self, node_id: str) -> Optional[str]:
        """
        获取指定节点的父节点ID；根节点返回 None。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            return self.nodes[node_id].parent

    def find_by_type(self, kind: NodeKind) -> List[str]:
        """
        按节点类型查找所有匹配的节点ID。
        """
        with self._lock:
            return [nid for nid, n in self.nodes.items() if n.kind == kind]

    def get_int_nodes(self) -> List[str]:
        return self.find_by_type(NodeKind.INT)

    def get_irp_nodes(self) -> List[str]:
        return self.find_by_type(NodeKind.IRP)

    def _get_new_intent_node(self) -> list[str]:
        with self._lock:
            res: list[str] = []
            if not self.has_node("功能意图"):
                return res
            for nid in self.get_children("功能意图"):
                info = self.get_node(nid)
                if info.get("type") != NodeKind.INT.value:
                    continue
                data = info.get("data") or {}
                if data.get("isNew") is True:
                    res.append(nid)
            return res

    def path_to_root(self, node_id: str) -> List[str]:
        """
        返回从指定节点到根的路径（包含自身和根）。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            path: List[str] = []
            cur = node_id
            while cur is not None:
                path.append(cur)
                cur = self.nodes[cur].parent
            return path

    def ancestors(self, node_id: str) -> List[str]:
        """
        返回所有祖先节点（不包含自身）。
        """
        p = self.path_to_root(node_id)
        return p[1:]

    def descendants(self, node_id: str) -> List[str]:
        """
        返回所有后代节点（子孙），深度优先顺序。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            res: List[str] = []
            stack: List[str] = [node_id]
            while stack:
                cur = stack.pop()
                for c in self.nodes[cur].children:
                    res.append(c)
                    stack.append(c)
            return res

    def leaves(self, node_id: Optional[str] = None) -> List[str]:
        """
        获取树或指定节点集合中的叶子节点ID。
        """
        with self._lock:
            ids = [node_id] if node_id else list(self.nodes.keys())
            res: List[str] = []
            for nid in ids:
                if nid in self.nodes and len(self.nodes[nid].children) == 0:
                    res.append(nid)
            return res

    def update_node_id(self, old_id: str, new_id: str) -> None:
        """
        更新节点ID，并维护父子引用一致性。

        Raises:
            ValueError: 旧ID不存在或新ID冲突
        """
        with self._lock:
            if old_id not in self.nodes:
                raise ValueError("节点不存在")
            if new_id in self.nodes:
                raise ValueError("新ID已存在")
            node = self.nodes.pop(old_id)
            node.id = new_id
            self.nodes[new_id] = node
            if node.parent is not None:
                parent = self.nodes[node.parent]
                parent.children.remove(old_id)
                parent.children.add(new_id)
            for cid in list(node.children):
                self.nodes[cid].parent = new_id
            if self.root_id == old_id:
                self.root_id = new_id

    def update_node_type(self, node_id: str, new_kind: NodeKind) -> None:
        """
        更新节点类型，并校验与当前父/子类型的兼容性。

        Raises:
            ValueError: 根类型非法、与父或子类型不兼容
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            node = self.nodes[node_id]
            if node.parent is None and new_kind is not NodeKind.ROOT:
                raise ValueError("根节点类型必须为ROOT")
            if node.parent is not None:
                parent_kind = self.nodes[node.parent].kind
                if new_kind not in self.allowed_parents or parent_kind not in self.allowed_parents[new_kind]:
                    raise ValueError("不允许的父子类型关系")
            for cid in node.children:
                child_kind = self.nodes[cid].kind
                if child_kind not in self.allowed_parents or new_kind not in self.allowed_parents[child_kind]:
                    raise ValueError("与子节点类型不兼容")
            node.kind = new_kind

    def update_node_data(self, node_id: str, data: Dict[str, Any]) -> None:
        """
        合并更新节点的附加数据字典。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            self.nodes[node_id].data.update(data)

    def remove_node(self, node_id: str) -> None:
        """
        级联删除指定节点及其子树。
        """
        with self._lock:
            if node_id not in self.nodes:
                raise ValueError("节点不存在")
            ids = [node_id] + self.descendants(node_id)
            parent_id = self.nodes[node_id].parent
            for nid in ids:
                for cid in list(self.nodes[nid].children):
                    self.nodes[cid].parent = None
                self.nodes.pop(nid, None)
            if parent_id is not None and parent_id in self.nodes:
                self.nodes[parent_id].children.discard(node_id)
            if self.root_id == node_id:
                self.root_id = None

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            nodes = []
            edges = []
            id_to_idx: Dict[str, int] = {nid: i for i, nid in enumerate(self.nodes.keys())}
            for nid, n in self.nodes.items():
                self._infer_and_backfill_child_relations(nid)
                nodes.append({"id": id_to_idx[nid], "name": nid, "type": n.kind.value, "data": dict(n.data)})
            for nid, n in self.nodes.items():
                for cid in n.children:
                    edges.append({"from": id_to_idx[nid], "to": id_to_idx[cid], "type": "parent_child"})
            return {"nodes": nodes, "edges": edges, "root": self.root_id}

    def save_to_json(self, file_path: str) -> None:
        with self._lock:
            payload = self.to_dict()
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

    def export_dsl_rules_to_json(self, file_path: str) -> None:
        with self._lock:
            dsl_node_ids = sorted({
                node_id
                for node_id in self.find_by_type(NodeKind.DSL)
                if "副本" not in node_id
            })
            dir_path = os.path.dirname(file_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(dsl_node_ids, f, ensure_ascii=False, indent=2)


    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BiReqTraceTree":
        nodes_list = payload.get("nodes", [])
        edges_list = payload.get("edges", [])
        root_name = payload.get("root")
        idx_to_node: Dict[int, Dict[str, Any]] = {}
        name_to_idx: Dict[str, int] = {}
        for item in nodes_list:
            idx = item.get("id")
            name = item.get("name")
            kind_str = item.get("type")
            data = item.get("data") or {}
            idx_to_node[idx] = {"name": name, "kind": NodeKind(kind_str), "data": data}
            name_to_idx[name] = idx
        g = cls()
        if root_name is None:
            raise ValueError("缺少root标识")
        root_idx = name_to_idx.get(root_name)
        if root_idx is None:
            raise ValueError("root节点未在nodes中定义")
        root_info = idx_to_node[root_idx]
        g.add_root(root_info["name"], root_info["data"])
        adj: Dict[int, List[int]] = {}
        for e in edges_list:
            if e.get("type") != "parent_child":
                continue
            u = e.get("from")
            v = e.get("to")
            adj.setdefault(u, []).append(v)
        stack: List[int] = [root_idx]
        visited: Set[int] = set([root_idx])
        while stack:
            u = stack.pop()
            for v in adj.get(u, []):
                if v in visited:
                    continue
                p = idx_to_node[u]
                c = idx_to_node[v]
                g.add_child(p["name"], c["name"], c["kind"], data=c["data"])
                visited.add(v)
                stack.append(v)
        return g

    @classmethod
    def from_json_file(cls, file_path: str) -> "BiReqTraceTree":
        with open(file_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return cls.from_dict(payload)

    def attach_subtree_from_hier(self, parent_id: str, node_spec: Dict[str, Any]) -> None:
        with self._lock:
            if parent_id not in self.nodes:
                raise ValueError("父节点不存在")
            def _precheck(p_kind: NodeKind, spec: Dict[str, Any]) -> None:
                cid = spec.get("id")
                ct = spec.get("type")
                if not cid or not ct:
                    raise ValueError("层级节点缺少id或type")
                ck = NodeKind(ct)
                if ck not in self.allowed_parents or p_kind not in self.allowed_parents[ck]:
                    raise ValueError("不允许的父子类型关系")
                if cid in self.nodes and self.nodes[cid].parent is not None:
                    raise ValueError("子节点已绑定父节点")
                for ch in spec.get("children", []) or []:
                    _precheck(ck, ch)
            _precheck(self.nodes[parent_id].kind, node_spec)
            created: List[str] = []
            attached: List[tuple[str, str]] = []
            def _attach(pid: str, spec: Dict[str, Any]) -> None:
                cid = spec.get("id")
                ck = NodeKind(spec.get("type"))
                dd = spec.get("data") or {}
                existed = cid in self.nodes
                self.add_child(pid, cid, ck, data=dd)
                attached.append((pid, cid))
                if not existed:
                    created.append(cid)
                for ch in spec.get("children", []) or []:
                    _attach(cid, ch)
            try:
                _attach(parent_id, node_spec)
            except Exception as e:
                for pid, cid in reversed(attached):
                    if cid in self.nodes and self.nodes[cid].parent == pid:
                        self.nodes[pid].children.discard(cid)
                        self.nodes[cid].parent = None
                for cid in reversed(created):
                    if cid in self.nodes:
                        self.remove_node(cid)
                raise e

    @classmethod
    def from_hier_dict(cls, spec: Dict[str, Any]) -> "BiReqTraceTree":
        rid = spec.get("id")
        t = spec.get("type")
        d = spec.get("data") or {}
        if not rid or not t:
            raise ValueError("层级根缺少id或type")
        k = NodeKind(t)
        if k is not NodeKind.ROOT:
            raise ValueError("层级根类型必须为ROOT")
        g = cls()
        g.add_root(rid, d)
        for child in spec.get("children", []) or []:
            g.attach_subtree_from_hier(rid, child)
        return g

    @classmethod
    def from_hier_json_file(cls, file_path: str) -> "BiReqTraceTree":
        with open(file_path, 'r', encoding='utf-8') as f:
            spec = json.load(f)
        return cls.from_hier_dict(spec)

    def to_str(self, use_ascii: bool = False, show_constraints: bool = False, show_relations: bool = False) -> str:
        with self._lock:
            if self.root_id is None:
                return "(空树)\n"

            def fmt(nid: str) -> str:
                n = self.nodes[nid]
                base = f"[{n.kind.value}] {n.id}"
                extras: List[str] = []
                if show_constraints:
                    d = n.data or {}
                    if d.get("isConstraint") is True or ("constraintObject" in d):
                        obj = d.get("constraintObject")
                        if isinstance(obj, list):
                            obj_str = "[" + ", ".join(str(x) for x in obj) + "]"
                        else:
                            obj_str = str(obj)
                        extras.append(f"constraintObject={obj_str}")
                if extras:
                    return f"{base} {{{'; '.join(extras)}}}"
                return base

            bar = "|  " if use_ascii else "│  "
            spc = "   "

            lines: List[str] = []

            def edge_conn(parent_id: str, child_id: str, is_last: bool) -> str:
                base = "\\-" if (use_ascii and is_last) else (
                    "+-" if use_ascii else ("└─" if is_last else "├─")
                )
                if not show_relations:
                    return base + " "
                child = self.nodes.get(child_id)
                if child is None:
                    return base + " "
                rels = self._infer_and_backfill_child_relations(parent_id)
                rel = rels.get(child.kind.value, "")
                if rel:
                    return f"{base[:-1]}{{{rel}}}{base[-1]} "
                return base + " "

            def _walk(nid: str, prefix: str, is_root: bool, is_last: bool) -> None:
                if is_root:
                    lines.append(fmt(nid))
                    new_prefix = ""
                else:
                    parent_id = self.nodes[nid].parent or ""
                    conn = edge_conn(parent_id, nid, is_last)
                    lines.append(prefix + conn + fmt(nid))
                    new_prefix = prefix + (spc if is_last else bar)

                children = sorted(self.nodes[nid].children)
                for i, cid in enumerate(children):
                    _walk(cid, new_prefix, False, i == len(children) - 1)

            _walk(self.root_id, "", True, True)
            return "\n".join(lines) + "\n"

    def print_tree(self, use_ascii: bool = False, show_constraints: bool = False, show_relations: bool = True) -> None:
        with self._lock:
            print(
                self.to_str(
                    use_ascii=use_ascii,
                    show_constraints=show_constraints,
                    show_relations=show_relations,
                ),
                end="",
            )
