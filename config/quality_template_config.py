from typing import Dict, Any
from model.biReq_trace_tree import BiReqTraceTree

QUALITY_TEMPLATE_SPEC: Dict[str, Any] = {
    "id": "质量意图",
    "type": "INT_TYPE",
    "children": [
        {
            "id": "生命安全",
            "type": "INT",
            "children": [
                {
                    "id": "检测并处理火灾",
                    "type": "INT",
                    "children": [
                        {
                            "id": "火灾发生时及时发出警报",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 烟雾感知设备.获取读数() > 0.05 THEN 打开报警() 报警设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["烟雾感知设备", "报警设备"]
                                    }
                                },
                                {
                                    "id": "IF 火灾感知设备.获取读数() == 发生火灾 THEN 打开报警() 报警设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["火灾感知设备", "报警设备"]
                                    }
                                }
                            ]
                        },
                        {
                            "id": "火灾发生时保持逃生通道畅通",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 烟雾感知设备.获取读数() > 0.05 THEN 打开() 出入控制设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["烟雾感知设备", "出入控制设备"]
                                    }
                                },
                                {
                                    "id": "IF 烟雾感知设备.获取读数() > 0.05 THEN 解锁() 门锁设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["烟雾感知设备", "门锁设备"]
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    "id": "检测并处理燃气泄漏",
                    "type": "INT",
                    "children": [
                        {
                            "id": "燃气泄漏时及时通风并告警",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 燃气感知设备.获取读数() == 燃气泄漏 THEN 打开() 窗户设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["燃气感知设备", "窗户设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                },
                                {
                                    "id": "IF 燃气感知设备.获取读数() == 燃气泄漏 THEN 打开报警() 报警设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["燃气感知设备", "报警设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    "id": "检测并处理水浸",
                    "type": "INT",
                    "children": [
                        {
                            "id": "水浸发生时及时告警",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 水浸感知设备.获取读数() == 水浸 THEN 打开报警() 报警设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["水浸感知设备", "报警设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    "id": "检测并处理入侵",
                    "type": "INT",
                    "children": [
                        {
                            "id": "检测到非法入侵时及时告警",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 门窗感知设备.获取读数() == 被打开 THEN 打开报警() 报警设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["门窗感知设备", "报警设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        {
            "id": "财产安全",
            "type": "INT",
            "children": [
                {
                    "id": "预防火灾",
                    "type": "INT",
                    "children": [
                        
                        {
                            "id": "无人时自动关闭非必要电器",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "无人时自动关闭影音类电器",
                                    "type": "INT",
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 影音电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "影音电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 影音电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "影音电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭照明类电器",
                                    "type": "INT",
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 照明电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "照明电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 照明电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "照明电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭温度控制类电器",
                                    "type": "INT", 
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 温度控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "温度控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 温度控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "温度控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭湿度控制类电器",
                                    "type": "INT", 
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 湿度控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "湿度控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 湿度控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "湿度控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭空气质量控制类电器",
                                    "type": "INT", 
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 空气质量控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "空气质量控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 空气质量控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "空气质量控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭气味控制类电器",
                                    "type": "INT", 
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 气味控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "气味控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 气味控制电器",  
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "气味控制电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                },
                                {
                                    "id": "无人时自动关闭厨房类电器",
                                    "type": "INT",
                                    "children": [
                                        {
                                            "id": "IF 人体感知设备.获取读数() == 人离开 THEN 关闭() 厨房电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["人体感知设备", "厨房电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        },
                                        {
                                            "id": "IF 运动感知设备.获取读数() == false THEN 关闭() 厨房电器",
                                            "type": "IRP",
                                            "data": {
                                                "devices": ["运动感知设备", "厨房电器"],
                                                "child_relations": {"DSL": "AND"}
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
                {
                    "id": "电涌保护",
                    "type": "INT",
                    "children": [
                        {
                            "id": "检测到电涌时断开敏感电器",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 功率监测设备.获取读数() == 电涌 THEN 断电() 智能插座设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["功率监测设备", "智能插座设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        {
            "id": "隐私与安全",
            "type": "INT",
            "children": [
                {
                    "id": "摄像头隐私",
                    "type": "INT",
                    "children": [
                        {
                            "id": "用户在家时关闭监控摄像头",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 人体感知设备.获取读数() == 人在家 THEN 关闭() 摄像头设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["人体感知设备", "摄像头设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    "id": "住宅安全",
                    "type": "INT",
                    "children": [
                        {
                            "id": "用户不在家时打开监控摄像头",
                            "type": "INT",
                            "children": [
                                {
                                    "id": "IF 人体感知设备.获取读数() == 人离开 THEN 打开() 摄像头设备",
                                    "type": "IRP",
                                    "data": {
                                        "devices": ["人体感知设备", "摄像头设备"],
                                        "child_relations": {"DSL": "AND"}
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        # 暂时停用性能约束分支，后续需要时再恢复。
        # ,
        # {
        #     "id": "性能约束",
        #     "type": "INT",
        #     "children": [
        #         {
        #             "id": "动作应该在两秒内执行",
        #             "type": "INT",
        #             "data": {
        #                 "isConstraint": True,
        #                 "constraintObject": []
        #             },
        #             "children": []
        #         }
        #     ]
        # }
    ]
}

def get_quality_template_spec() -> Dict[str, Any]:
    return QUALITY_TEMPLATE_SPEC

def attach_quality_template(tree: BiReqTraceTree, parent_id: str = "ROOT") -> None:
    tree.attach_subtree_from_hier(parent_id, QUALITY_TEMPLATE_SPEC)

def format_quality_template(spec: Dict[str, Any] = None) -> str:
    data = spec or QUALITY_TEMPLATE_SPEC
    def label(n: Dict[str, Any]) -> str:
        t = n.get("type", "")
        i = n.get("id", "")
        return f"[{t}] {i}"
    def walk(n: Dict[str, Any], prefix: str, is_last: bool) -> list:
        cur = f"{prefix}{'└─' if is_last else '├─'} {label(n)}"
        lines = [cur]
        ch = n.get("children", []) or []
        new_prefix = prefix + ("   " if is_last else "│  ")
        for idx, c in enumerate(ch):
            lines += walk(c, new_prefix, idx == len(ch) - 1)
        return lines
    lines = [label(data)]
    children = data.get("children", []) or []
    for idx, c in enumerate(children):
        lines += walk(c, "", idx == len(children) - 1)
    return "\n".join(lines)

def print_quality_template(spec: Dict[str, Any] = None) -> None:
    print(format_quality_template(spec))
