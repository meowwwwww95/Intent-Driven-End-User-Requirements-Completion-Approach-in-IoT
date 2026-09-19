import logging
import sys
import os
import threading
from typing import Dict, Any, List
import json
import re
import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.trace_tree_bus import get_shared_trace_bus, get_shared_trace_tree
from model.biReq_trace_tree import NodeKind
from config.ai_client import get_default_ai_client, create_system_message, create_user_message, simple_completion
from config.logging_config import CASE_DEBUG_LOGGER_NAME, CASE_RUN_LOGGER_NAME
from config.overhead_recorder import get_overhead_recorder
from webui.runtime_events import emit_agent_status

class QualityCheckerAgent:
    def __init__(self):
        self._run_counter = 0
        self._current_run: Dict[str, Any] = {}
        self.bus = get_shared_trace_bus()
        self.trace_tree = None
        self._env_sensor_cache: dict[str, str | None] = {}
        self._normalized_rule_cache: dict[str, str] = {}
        self._homeguard_conflict_cache: dict[tuple[str, ...], Dict[str, Any]] = {}
        self._homeguard_cache_lock = threading.Lock()
        self._homeguard_inflight: dict[tuple[str, ...], threading.Event] = {}
        self.run_logger = logging.getLogger(f"{CASE_RUN_LOGGER_NAME}.QualityChecker")
        self.debug_logger = logging.getLogger(f"{CASE_DEBUG_LOGGER_NAME}.QualityChecker")
        self._plan_debug_sample_limit = 3
        self._progress_log_target = 10

    @staticmethod
    def _get_homeguard_parallel_workers(item_count: int) -> int:
        if item_count <= 0:
            return 0
        if item_count == 1:
            return 1
        cpu_count = os.cpu_count() or 4
        # 默认启用并行，并限制到一个保守上限，避免在线程过多时产生额外调度开销。
        return min(item_count, max(2, min(cpu_count, 8)))
        
    def _log_summary(self, message: str) -> None:
        self.run_logger.info(message)
        self.debug_logger.info(message)

    def _log_debug(self, message: str) -> None:
        self.debug_logger.debug(message)

    def _log_error(self, message: str) -> None:
        self.run_logger.error(message)
        self.debug_logger.error(message)

    def _format_rule_sample(self, rules: List[str], limit: int = 2, width: int = 120) -> str:
        items: List[str] = []
        for rule in list(rules or [])[:limit]:
            text = str(rule or "").strip().replace("\n", " ")
            if len(text) > width:
                text = text[: width - 3] + "..."
            if text:
                items.append(text)
        if not items:
            return "(无规则)"
        return " | ".join(items)

    def _should_log_plan_progress(self, idx: int, total: int) -> bool:
        if total <= 0:
            return False
        if total <= self._progress_log_target:
            return True
        step = max(1, total // self._progress_log_target)
        return idx == 1 or idx == total or idx % step == 0

    def _strip_plan_results_for_logging(self, result: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        logged_result = dict(result)
        logged_result.pop("plan_results", None)
        return logged_result

    def _build_skipped_conflict_result(self, kind: NodeKind, reason: str, plan_count: int = 0) -> Dict[str, Any]:
        return {
            "summary": {
                "plan_count": int(plan_count),
                "ok_plan_count": 0,
                "failed_plan_count": 0,
                "skipped_plan_count": int(plan_count),
                "homeguard_checked_plan_count": 0,
                "any_plan_ok": False,
                "all_plans_ok": True,
                "pass_criterion": "skipped",
            },
            "mode": "skipped",
            "skipped": True,
            "reason": reason,
            "kind": kind.value,
        }

    def _node_has_descendant_kind(self, node_id: str, target_kind: NodeKind) -> bool:
        visited: set[str] = set()
        stack: List[str] = [str(node_id)]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            try:
                info = self.trace_tree.get_node(current)
            except Exception:
                continue
            if not isinstance(info, dict):
                continue
            child_ids = [cid for cid in (info.get("children") or []) if isinstance(cid, str)]
            for cid in child_ids:
                try:
                    child_info = self.trace_tree.get_node(cid)
                except Exception:
                    continue
                child_type = str((child_info or {}).get("type") or "").strip().upper()
                if child_type == target_kind.value:
                    return True
                stack.append(cid)
        return False

    def _extract_action_device_from_rule(self, rule_text: str) -> str | None:
        text = str(rule_text or "").strip()
        if not text:
            return None
        m = re.search(r'^\s*IF\s+.*?\s+THEN\s+(?P<act>.*?)\s*$', text, flags=re.IGNORECASE)
        if not m:
            return None
        action_text = re.sub(r'\s+', ' ', (m.group("act") or "").strip())
        if not action_text:
            return None
        m2 = re.search(r'([\w\u4e00-\u9fa5]+)\s*\([^)]*\)\s+([\w\u4e00-\u9fa5]+)\s*$', action_text)
        if not m2:
            m2 = re.search(r'([\w\u4e00-\u9fa5]+)\s+([\w\u4e00-\u9fa5]+)\s*$', action_text)
        if not m2:
            return None
        device = str(m2.group(2) or "").strip()
        return device or None

    def _collect_descendant_dsl_action_devices(self, node_id: str) -> set[str] | None:
        try:
            descendants = self.trace_tree.descendants(node_id)
        except Exception:
            return None
        devices: set[str] = set()
        found_dsl = False
        for did in descendants:
            try:
                info = self.trace_tree.get_node(did)
            except Exception:
                continue
            if str((info or {}).get("type") or "").strip().upper() != NodeKind.DSL.value:
                continue
            found_dsl = True
            rule_text = self._extract_rule_text_from_node(did, NodeKind.DSL)
            device = self._extract_action_device_from_rule(rule_text or "")
            if not device:
                return None
            devices.add(device)
        if not found_dsl or not devices:
            return None
        return devices

    def _cheap_checker_evaluate_plan(self, kind: NodeKind, plan: Dict[str, Any]) -> Dict[str, Any]:
        rules = [str(r or "").strip() for r in list(plan.get("rules") or []) if str(r or "").strip()]
        if len(rules) <= 1:
            return {"skip_homeguard": True, "reason": "single_rule_plan"}

        if kind == NodeKind.DSL:
            target_sets: List[set[str]] = []
            for rule in rules:
                device = self._extract_action_device_from_rule(rule)
                if not device:
                    return {"skip_homeguard": False, "reason": "dsl_action_device_unknown"}
                target_sets.append({device})
        elif kind == NodeKind.IRP:
            node_ids = [str(n or "").strip() for n in (plan.get("node_ids") or []) if str(n or "").strip()]
            if not node_ids:
                return {"skip_homeguard": False, "reason": "irp_node_ids_missing"}
            target_sets = []
            for nid in node_ids:
                devices = self._collect_descendant_dsl_action_devices(nid)
                if not devices:
                    return {"skip_homeguard": False, "reason": f"irp_descendant_action_devices_unknown:{nid}"}
                target_sets.append(devices)
        else:
            return {"skip_homeguard": False, "reason": "unsupported_kind"}

        for i in range(len(target_sets)):
            for j in range(i + 1, len(target_sets)):
                if target_sets[i] & target_sets[j]:
                    return {"skip_homeguard": False, "reason": "action_targets_overlap_possible"}
        target_sample = [sorted(list(s)) for s in target_sets[: self._plan_debug_sample_limit]]
        return {
            "skip_homeguard": True,
            "reason": "action_targets_pairwise_disjoint",
            "target_sample": target_sample,
        }

    def _begin_run(self):
        self._run_counter += 1
        self.trace_tree = get_shared_trace_tree()
        self._normalized_rule_cache = {}
        self._homeguard_conflict_cache = {}
        self._homeguard_inflight = {}

    # 该映射表覆盖当前支持的传感器类型命名模式，接入新传感器类型时需相应扩展
    def _fallback_env_sensor_canonical(self, device_name: str) -> str | None:
        s = (device_name or "").strip()
        if not s:
            return None
        ss = s.lower()

        if not ss.endswith("_sensor") and "_sensor" not in ss:
            return None

        if (
            "temperature" in ss
            or "_temp_sensor" in ss
            or ss.endswith("_temp")
            or "thermo" in ss
        ):
            return "temperature_sensor"

        if (
            "humidity" in ss
            or "_humid_sensor" in ss
        ):
            return "humidity_sensor"

        if (
            "brightness" in ss
            or "illuminance" in ss
            or "lux" in ss
            or "_light_sensor" in ss
        ):
            return "brightness_sensor"

        if (
            "pm25" in ss
            or "pm2_5" in ss
            or "pm2.5" in ss
            or "air_quality" in ss
            or "dust" in ss
            or "co2" in ss
            or "voc" in ss
        ):
            return "air_quality_sensor"

        if (
            "noise" in ss
            or "sound" in ss
        ):
            return "sound_sensor"

        if (
            "water_quality" in ss
            or "tds" in ss
            or "ph" in ss
            or "turbidity" in ss
        ):
            return "water_quality_sensor"

        if (
            "oil_fume" in ss
            or "oil_smoke" in ss
            or "fume" in ss
        ):
            return "oil_fume_sensor"

        if (
            "smoke" in ss
            or "gas" in ss
            or "ch4" in ss
        ):
            return "smoke_sensor"

        if (
            "human" in ss
            or "motion" in ss
            or "presence" in ss
            or "occupancy" in ss
            or "pir" in ss
        ):
            return "presence_sensor"

        return None

    # 该 canonical 枚举覆盖当前支持的传感器类型，接入新类型时需相应扩展
    def _llm_env_sensor_canonical(self, device_name: str, cap_name: str | None = None) -> str | None:
        allowed = [
            "brightness_sensor",
            "temperature_sensor",
            "air_quality_sensor",
            "sound_sensor",
            "humidity_sensor",
            "water_quality_sensor",
            "oil_fume_sensor",
            "smoke_sensor",
            "presence_sensor",
            "",
        ]
        template = """
你在做智能家居规则的传感器统一映射。
给定 `device_name`、可选的 `cap_name`，以及 `devices_profile_context`（该设备的画像信息，包括 type、capabilities 及 description），请判断“当前这次被调用的能力”实际在感知哪一类环境属性传感器，并返回 canonical 名称。

你的目标不是判断设备的大类名称，而是判断“这个能力调用在条件中代表的环境属性”。
很多设备是复合传感器，设备名可能强调 presence / motion / human 等，但它的某个具体能力实际读取的是亮度、温度、湿度、空气质量等。此时必须优先按能力语义映射，不能只按设备名映射。

可选 canonical（只能从下列中选择其一）:
- brightness_sensor（亮度）
- temperature_sensor（温度）
- air_quality_sensor（空气质量，如 PM2.5/CO2/VOC）
- sound_sensor（声音/噪声）
- humidity_sensor（湿度）
- water_quality_sensor（水质，如 pH/TDS/浊度）
- oil_fume_sensor（油烟浓度）
- smoke_sensor（烟雾/可燃气体）
- presence_sensor（人在：人体/运动/存在/占用）
- ""（如果不是环境属性传感器，返回空字符串）

判断优先级:
1. 优先看 `cap_name` 的语义。
2. 再看 `devices_profile_context.capabilities` 中与该能力对应的 description。
3. 再看 `devices_profile_context.type` 和设备整体 description。
4. 最后才参考 `device_name` 本身。

关键规则:
- 你要判断的是“当前能力在读取什么环境属性”，不是设备名更像什么。
- 如果 `cap_name` 或 capability description 明确表示 illuminance / brightness / lux / 光照 / 亮度，则返回 `brightness_sensor`。
- 如果 `cap_name` 或 capability description 明确表示 temperature / temp / 温度，则返回 `temperature_sensor`。
- 如果 `cap_name` 或 capability description 明确表示 humidity / 湿度，则返回 `humidity_sensor`。
- 如果 `cap_name` 或 capability description 明确表示 PM2.5 / CO2 / VOC / air quality / 空气质量，则返回 `air_quality_sensor`。
- 如果 `cap_name` 或 capability description 明确表示 motion / presence / occupancy / human / 有人无人 / 人体存在，则返回 `presence_sensor`。
- 对于 `reading(Temperature)`、`reading(Humidity)`、`reading(PM2.5)` 这类带参数的能力，必须按参数语义决定 canonical，而不是按设备名决定。
- 对于复合传感器，即使设备名里包含 `presence`、`human`、`motion` 等词，只要当前能力是在读取亮度/温度/湿度等属性，也必须映射到对应环境属性传感器。
- 只有在无法从 `cap_name`、capability description、devices_profile_context 中判断这是环境属性传感器时，才根据设备名做弱判断。
- 如果综合所有信息仍不确定，返回空字符串。
- 不要把动作设备误判为传感器；如 air_conditioner / heater / light 本身通常不是环境属性传感器。

示例:
输入:
{
  "device_name": "sensorhub_presence_unit",
  "cap_name": "measure_illuminance",
  "devices_profile_context": {
    "type": "presence_detector",
    "capabilities": [
      {"name": "measure_illuminance", "description": "测量当前环境光照强度"}
    ]
  }
}
输出:
{
  "canonical": "brightness_sensor"
}

输入:
{
  "device_name": "climatrack_air_sensor",
  "cap_name": "measure(Temperature)",
  "devices_profile_context": {
    "type": "climate_monitor",
    "capabilities": [
      {"name": "measure(Temperature)", "description": "测量当前环境温度"}
    ]
  }
}
输出:
{
  "canonical": "temperature_sensor"
}

输入:
{
  "device_name": "detectpro_motion_sensor",
  "cap_name": "detect()",
  "devices_profile_context": {
    "type": "motion_detector",
    "capabilities": [
      {"name": "detect()", "description": "判断当前是否有人存在"}
    ]
  }
}
输出:
{
  "canonical": "presence_sensor"
}

输出 JSON:
{
  "canonical": "temperature_sensor"
}
"""
        devices_profile_context: dict[str, Any] = {"device_name": (device_name or "").strip()}
        try:
            dp = self.bus.get_available_devices()
            if dp:
                devices_profile_context = dp.get_device_profile_context(device_name)
        except Exception:
            devices_profile_context = {"device_name": (device_name or "").strip()}
        messages = [
            create_system_message(template.strip()),
            create_user_message(
                json.dumps(
                    {
                        "device_name": device_name,
                        "cap_name": cap_name or "",
                        "devices_profile_context": devices_profile_context,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            with get_default_ai_client().usage_scope("env_sensor_canonicalize", scope_type="step"):
                resp = simple_completion(messages_input=messages, use_json=True, temperature=0.0)
            parsed = json.loads(resp) if isinstance(resp, str) else resp
            canonical = (parsed or {}).get("canonical")
            if not isinstance(canonical, str):
                return None
            canonical = canonical.strip()
            if canonical not in allowed:
                return None
            return canonical or None
        except Exception:
            return None

    def _env_sensor_canonical(self, device_name: str, cap_name: str | None = None) -> str | None:
        key = f"{device_name}::{cap_name or ''}"
        if key in self._env_sensor_cache:
            return self._env_sensor_cache[key]

        canonical = self._fallback_env_sensor_canonical(device_name)
        if canonical is None:
            canonical = self._llm_env_sensor_canonical(device_name, cap_name=cap_name)

        self._env_sensor_cache[key] = canonical
        return canonical

    def _normalize_rule_for_homeguard(self, rule_text: str) -> str:
        if not isinstance(rule_text, str) or not rule_text.strip():
            return rule_text

        m = re.search(r'^\s*IF\s+(?P<cond>.*?)\s+THEN\s+(?P<act>.*?)\s*$', rule_text)
        if not m:
            return rule_text

        cond_span = m.span("cond")
        cond_text = rule_text[cond_span[0]:cond_span[1]]

        def _canonical_from_reading_arg(arg: str | None) -> str | None:
            if not isinstance(arg, str):
                return None
            a = arg.strip()
            if not a:
                return None
            a = re.sub(r'^(["\'])|(["\'])$', '', a).strip()
            if not a:
                return None
            al = a.lower().replace(" ", "")

            if "temperature" in al or al in {"temp"}:
                return "temperature_sensor"
            if "humidity" in al or "relativehumidity" in al or al in {"rh"}:
                return "humidity_sensor"
            if (
                "air_quality" in al
                or "airquality" in al
                or "pm25" in al
                or "pm2_5" in al
                or "pm2.5" in al
                or "co2" in al
                or "voc" in al
                or "dust" in al
            ):
                return "air_quality_sensor"
            if "brightness" in al or "illuminance" in al or "lux" in al:
                return "brightness_sensor"
            if "sound" in al or "noise" in al:
                return "sound_sensor"
            if "water_quality" in al or "waterquality" in al or "tds" in al or al in {"ph"} or "turbidity" in al:
                return "water_quality_sensor"
            if "oil_fume" in al or "oilfume" in al or "oil_smoke" in al or "fume" in al:
                return "oil_fume_sensor"
            if "smoke" in al or "gas" in al or "ch4" in al:
                return "smoke_sensor"
            if "presence" in al or "occupancy" in al or "human" in al or "motion" in al or "pir" in al:
                return "presence_sensor"

            return None

        def _sub(match: re.Match) -> str:
            dev = match.group("dev")
            cap = match.group("cap")
            arg = match.group("arg")
            canonical = self._env_sensor_canonical(dev, cap_name=cap)
            arg_canonical = _canonical_from_reading_arg(arg)
            if arg_canonical:
                canonical = arg_canonical
            if canonical:
                return f"{canonical}.reading()"
            call = match.group("call") or ""
            return f"{dev}.{cap}{call}"

        new_cond = re.sub(
            r'(?P<dev>[A-Za-z0-9_\u4e00-\u9fa5]+)\s*\.\s*(?P<cap>[A-Za-z0-9_\u4e00-\u9fa5]+)(?P<call>\s*\(\s*(?P<arg>[^)]*)\s*\))?',
            _sub,
            cond_text,
        )
        if new_cond == cond_text:
            return rule_text
        return rule_text[:cond_span[0]] + new_cond + rule_text[cond_span[1]:]

    def _build_homeguard_input(self, rules: List[str]) -> tuple[List[str], dict[str, List[str]]]:
        normalized_to_originals: dict[str, List[str]] = {}
        for r in rules:
            raw_rule = str(r or "")
            nr = self._normalized_rule_cache.get(raw_rule)
            if nr is None:
                nr = self._normalize_rule_for_homeguard(raw_rule)
                self._normalized_rule_cache[raw_rule] = nr
            normalized_to_originals.setdefault(nr, []).append(r)
        return list(normalized_to_originals.keys()), normalized_to_originals

    def _make_homeguard_cache_key(self, normalized_rules: List[str]) -> tuple[str, ...]:
        return tuple(sorted(str(r or "").strip() for r in (normalized_rules or []) if str(r or "").strip()))

    def _restore_conflict_entry(self, entry: Any, normalized_to_originals: dict[str, List[str]]) -> List[Any]:
        if isinstance(entry, str):
            if " ,and, " in entry:
                parts = entry.split(" ,and, ")
                if len(parts) == 2:
                    left = parts[0]
                    right = parts[1]
                    lefts = normalized_to_originals.get(left, [left])
                    rights = normalized_to_originals.get(right, [right])
                    out: List[str] = []
                    for a in lefts:
                        for b in rights:
                            out.append(f"{a} ,and, {b}")
                    return out
            return normalized_to_originals.get(entry, [entry])

        if isinstance(entry, list):
            merged: List[Any] = []
            seen: set[str] = set()
            for x in entry:
                restored_list = self._restore_conflict_entry(x, normalized_to_originals)
                for r in restored_list:
                    key = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(r)
            return [merged]

        return [entry]

    def _restore_homeguard_conflicts(self, conflicts: Dict[str, Any], normalized_to_originals: dict[str, List[str]]) -> Dict[str, Any]:
        restored: Dict[str, Any] = {}
        for k, v in (conflicts or {}).items():
            if isinstance(v, list):
                new_list: List[Any] = []
                for item in v:
                    restored_items = self._restore_conflict_entry(item, normalized_to_originals)
                    if isinstance(item, list):
                        for ri in restored_items:
                            if isinstance(ri, list):
                                new_list.append(ri)
                            else:
                                new_list.append([ri])
                    else:
                        new_list.extend(restored_items)
                restored[k] = new_list
            else:
                restored[k] = v
        return restored

    def _default_child_relation(self, child_type: str) -> str:
        t = (child_type or "").strip().upper()
        if t in {NodeKind.INT.value, NodeKind.INT_TYPE.value}:
            return "AND"
        if t in {NodeKind.IRP.value, NodeKind.DSL.value}:
            return "OR"
        return "AND"

    def _get_child_relation(self, parent_info: Dict[str, Any], child_type: str) -> str:
        data = (parent_info or {}).get("data") or {}
        rels = data.get("child_relations") or {}
        if isinstance(rels, dict):
            rel = str(rels.get(child_type, "") or "").strip().upper()
            if rel in {"AND", "OR"}:
                return rel
        return self._default_child_relation(child_type)

    def _merge_plan_nodes(self, left: List[str], right: List[str]) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for item in list(left or []) + list(right or []):
            s = str(item or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _dedupe_plans(self, plans: List[List[str]]) -> List[List[str]]:
        out: List[List[str]] = []
        seen: set[tuple[str, ...]] = set()
        for plan in plans or []:
            ordered_items = [x for x in (plan or []) if isinstance(x, str) and x.strip()]
            norm_key = tuple(sorted(set(ordered_items)))
            if not norm_key or norm_key in seen:
                continue
            seen.add(norm_key)
            out.append(ordered_items)
        return out

    def _combine_plan_groups_and(self, groups: List[List[List[str]]]) -> List[List[str]]:
        acc: List[List[str]] = [[]]
        for group in groups:
            if not group:
                continue
            new_acc: List[List[str]] = []
            for left in acc:
                for right in group:
                    new_acc.append(self._merge_plan_nodes(left, right))
            acc = self._dedupe_plans(new_acc)
            if not acc:
                break
        return self._dedupe_plans(acc)

    def _enumerate_rule_plans_from_node(self, node_id: str, target_kind: NodeKind) -> List[List[str]]:
        try:
            info = self.trace_tree.get_node(node_id)
        except Exception:
            return []

        node_type = (info.get("type") or "").strip().upper()
        if node_type == target_kind.value:
            return [[node_id]]

        child_ids = sorted([cid for cid in (info.get("children") or []) if isinstance(cid, str)])
        if not child_ids:
            return []

        grouped_child_plan_sets: dict[str, List[List[List[str]]]] = defaultdict(list)
        for cid in child_ids:
            child_plans = self._enumerate_rule_plans_from_node(cid, target_kind)
            if not child_plans:
                continue
            try:
                child_info = self.trace_tree.get_node(cid)
            except Exception:
                continue
            child_type = (child_info.get("type") or "").strip().upper()
            if not child_type:
                continue
            grouped_child_plan_sets[child_type].append(child_plans)

        if not grouped_child_plan_sets:
            return []

        per_type_groups: List[List[List[str]]] = []
        for child_type in sorted(grouped_child_plan_sets.keys()):
            child_plan_sets = grouped_child_plan_sets[child_type]
            relation = self._get_child_relation(info, child_type)
            if relation == "OR":
                merged_group: List[List[str]] = []
                for child_plans in child_plan_sets:
                    merged_group.extend(child_plans)
                per_type_groups.append(self._dedupe_plans(merged_group))
            else:
                per_type_groups.append(self._combine_plan_groups_and(child_plan_sets))

        return self._combine_plan_groups_and(per_type_groups)

    def _enumerate_rule_plans(self, target_kind: NodeKind) -> List[List[str]]:
        root_id = getattr(self.trace_tree, "root_id", None)
        if not isinstance(root_id, str) or not root_id.strip():
            return []
        return self._dedupe_plans(self._enumerate_rule_plans_from_node(root_id, target_kind))

    def _extract_rule_text_from_node(self, node_id: str, kind: NodeKind) -> str | None:
        try:
            node_info = self.trace_tree.get_node(node_id)
        except Exception:
            return None
        if not node_info or not isinstance(node_info, dict):
            return None

        data = node_info.get("data", {}) or {}
        rule_text = None
        if kind == NodeKind.IRP:
            rule_text = data.get("IRP")
        elif kind == NodeKind.DSL:
            rule_text = data.get("tap_rule") or data.get("IRP")

        if not rule_text:
            node_id_text = node_info.get("id")
            if isinstance(node_id_text, str) and node_id_text.strip().upper().startswith("IF "):
                rule_text = node_id_text

        if isinstance(rule_text, str):
            text = rule_text.strip()
            if text and "副本" not in text:
                return text
        return None

    def _collect_all_rules_by_kind(self, kind: NodeKind) -> List[str]:
        if kind == NodeKind.IRP:
            node_ids = list[str](self.trace_tree.get_irp_nodes()) or list[str](self.trace_tree.find_by_type(NodeKind.IRP)) or []
        else:
            node_ids = list[str](self.trace_tree.find_by_type(NodeKind.DSL)) or []

        rules: List[str] = []
        seen: set[str] = set()
        for nid in node_ids:
            if kind == NodeKind.IRP and not self._node_has_descendant_kind(nid, NodeKind.DSL):
                continue
            rule_text = self._extract_rule_text_from_node(nid, kind)
            if not rule_text or rule_text in seen:
                continue
            seen.add(rule_text)
            rules.append(rule_text)
        return rules

    def _build_rule_plans_from_tree(self, kind: NodeKind) -> List[Dict[str, Any]]:
        plan_node_ids = self._enumerate_rule_plans(kind)
        out: List[Dict[str, Any]] = []
        seen_rule_sets: set[tuple[str, ...]] = set()
        prefix = "IRP" if kind == NodeKind.IRP else "DSL"

        for node_ids in plan_node_ids:
            rules: List[str] = []
            seen_rules: set[str] = set()
            filtered_node_ids: List[str] = []
            for nid in node_ids:
                if kind == NodeKind.IRP and not self._node_has_descendant_kind(nid, NodeKind.DSL):
                    continue
                rule_text = self._extract_rule_text_from_node(nid, kind)
                if not rule_text or rule_text in seen_rules:
                    continue
                seen_rules.add(rule_text)
                rules.append(rule_text)
                filtered_node_ids.append(nid)
            if not rules:
                continue
            rule_key = tuple(sorted(set(rules)))
            if rule_key in seen_rule_sets:
                continue
            seen_rule_sets.add(rule_key)
            out.append(
                {
                    "plan_id": f"{prefix}_PLAN_{len(out) + 1}",
                    "node_ids": filtered_node_ids,
                    "rules": rules,
                    "source": "relation_grouped",
                }
            )

        if out:
            return out

        legacy_rules = self._collect_all_rules_by_kind(kind)
        if not legacy_rules:
            return []
        return [
            {
                "plan_id": f"{prefix}_PLAN_1",
                "node_ids": [],
                "rules": legacy_rules,
                "source": "legacy_all_rules_fallback",
            }
        ]

    def _conflicts_has_issue(self, conflicts: Dict[str, Any]) -> bool:
        if not isinstance(conflicts, dict):
            return False
        for key, val in conflicts.items():
            if key == "error":
                if val:
                    return True
                continue
            if isinstance(val, list) and len(val) > 0:
                return True
        return False

    def _aggregate_plan_conflicts(self, plan_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        aggregated: Dict[str, Any] = {}
        list_seen: dict[str, set[str]] = defaultdict(set)
        errors: List[dict[str, Any]] = []
        for plan in plan_results or []:
            if not isinstance(plan, dict):
                continue
            plan_id = str(plan.get("plan_id") or "").strip()
            conflicts = plan.get("conflicts") or {}
            if not isinstance(conflicts, dict):
                continue
            err = conflicts.get("error")
            if err:
                errors.append({"plan_id": plan_id, "error": err})
            for key, val in conflicts.items():
                if not isinstance(val, list):
                    continue
                dst = aggregated.setdefault(key, [])
                seen = list_seen[key]
                for item in val:
                    token = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                    if token in seen:
                        continue
                    seen.add(token)
                    dst.append(item)
        if errors:
            aggregated["errors"] = errors
        return aggregated

    def _get_or_compute_homeguard_conflicts(
        self,
        cache_key: tuple[str, ...],
        normalized_rules: List[str],
        run_homeguard,
    ) -> tuple[Dict[str, Any], bool]:
        while True:
            with self._homeguard_cache_lock:
                cached_conflicts = self._homeguard_conflict_cache.get(cache_key)
                if cached_conflicts is not None:
                    return copy.deepcopy(cached_conflicts), True
                inflight = self._homeguard_inflight.get(cache_key)
                if inflight is None:
                    inflight = threading.Event()
                    self._homeguard_inflight[cache_key] = inflight
                    is_owner = True
                else:
                    is_owner = False
            if is_owner:
                try:
                    raw_conflicts = run_homeguard(normalized_rules)
                    with self._homeguard_cache_lock:
                        self._homeguard_conflict_cache[cache_key] = copy.deepcopy(raw_conflicts)
                    return raw_conflicts, False
                finally:
                    with self._homeguard_cache_lock:
                        done_event = self._homeguard_inflight.pop(cache_key, None)
                    if done_event is not None:
                        done_event.set()
            inflight.wait()

    def _evaluate_conflict_plan(
        self,
        kind: NodeKind,
        plan: Dict[str, Any],
        idx: int,
        total: int,
        debug: bool,
        run_homeguard,
    ) -> Dict[str, Any] | None:
        rules = list(plan.get("rules") or [])
        if not rules:
            return None

        if self._should_log_plan_progress(idx, total):
            self._log_summary(
                f"质量检查Agent: 正在检查 {kind.value} 方案 {idx}/{total} "
                f"({plan.get('plan_id')})，规则数={len(rules)}"
            )
        if debug and idx <= self._plan_debug_sample_limit:
            self._log_debug(
                "质量检查Agent: 方案规则样例 "
                + json.dumps(
                    {
                        "kind": kind.value,
                        "plan_id": plan.get("plan_id"),
                        "rule_sample": self._format_rule_sample(rules, limit=3),
                        "rule_count": len(rules),
                    },
                    ensure_ascii=False,
                )
            )

        checked_by = "homeguard"
        cheap_checker_skipped = 0
        homeguard_checked = 0
        try:
            cheap_eval = self._cheap_checker_evaluate_plan(kind, plan)
            if cheap_eval.get("skip_homeguard") is True:
                conflicts = {}
                checked_by = "cheap_checker"
                cheap_checker_skipped = 1
                if self._should_log_plan_progress(idx, total):
                    self._log_summary(
                        f"质量检查Agent: {kind.value} 方案 {plan.get('plan_id')} 命中 cheap checker，跳过 HomeGuard"
                    )
                if debug and idx <= self._plan_debug_sample_limit:
                    self._log_debug(
                        "质量检查Agent: cheap checker 判定 "
                        + json.dumps(
                            {
                                "kind": kind.value,
                                "plan_id": plan.get("plan_id"),
                                "reason": cheap_eval.get("reason"),
                                "target_sample": cheap_eval.get("target_sample") or [],
                            },
                            ensure_ascii=False,
                        )
                    )
            else:
                normalized_rules, normalized_to_originals = self._build_homeguard_input(rules)
                cache_key = self._make_homeguard_cache_key(normalized_rules)
                raw_conflicts, cache_hit = self._get_or_compute_homeguard_conflicts(
                    cache_key,
                    normalized_rules,
                    run_homeguard,
                )
                if debug and cache_hit and idx <= self._plan_debug_sample_limit:
                    self._log_debug(
                        f"质量检查Agent: HomeGuard 缓存命中，plan_id={plan.get('plan_id')} key_size={len(cache_key)}"
                    )
                conflicts = self._restore_homeguard_conflicts(raw_conflicts, normalized_to_originals)
                homeguard_checked = 1
        except Exception as e:
            if debug:
                self._log_debug(f"Error running HomeGuard for {plan.get('plan_id')}: {e}")
            conflicts = {"error": str(e)}
            checked_by = "homeguard_error"

        has_conflict = self._conflicts_has_issue(conflicts)
        if self._should_log_plan_progress(idx, total):
            self._log_summary(
                f"质量检查Agent: {kind.value} 方案 {plan.get('plan_id')} 检查完成，"
                f"结果={'通过' if not has_conflict else '失败'}，checked_by={checked_by}"
            )
        if debug and idx <= self._plan_debug_sample_limit:
            self._log_debug(
                "质量检查Agent: 方案检查结果 "
                + json.dumps(
                    {
                        "kind": kind.value,
                        "plan_id": plan.get("plan_id"),
                        "ok": not has_conflict,
                        "checked_by": checked_by,
                        "conflict_keys": sorted(list(conflicts.keys())) if isinstance(conflicts, dict) else [],
                        "conflict_count": len(conflicts or {}) if isinstance(conflicts, dict) else 0,
                    },
                    ensure_ascii=False,
                )
            )

        return {
            "index": idx,
            "cheap_checker_skipped": cheap_checker_skipped,
            "homeguard_checked": homeguard_checked,
            "plan_result": {
                "plan_id": plan.get("plan_id"),
                "source": plan.get("source"),
                "node_ids": list(plan.get("node_ids") or []),
                "rules": rules,
                "ok": not has_conflict,
                "checked_by": checked_by,
                "conflicts": conflicts,
            },
        }

    def _run_conflict_check_by_plans(self, kind: NodeKind, debug: bool = False) -> tuple[bool, Dict[str, Any]]:
        plans = self._build_rule_plans_from_tree(kind)
        if not plans:
            self._log_summary(f"质量检查Agent: {kind.value} 层未发现可检查方案，跳过静态检查")
            return True, {}

        self._log_summary(
            f"质量检查Agent: 开始检查 {kind.value} 层，共 {len(plans)} 组方案"
        )
        if debug:
            for plan in plans[: self._plan_debug_sample_limit]:
                self._log_debug(
                    "质量检查Agent: 方案已枚举 "
                    + json.dumps(
                        {
                            "kind": kind.value,
                            "plan_id": plan.get("plan_id"),
                            "source": plan.get("source"),
                            "node_ids": plan.get("node_ids") or [],
                            "rule_count": len(plan.get("rules") or []),
                            "rule_sample": self._format_rule_sample(plan.get("rules") or []),
                        },
                        ensure_ascii=False,
                    )
                )
            if len(plans) > self._plan_debug_sample_limit:
                self._log_debug(
                    f"质量检查Agent: 已省略其余 {len(plans) - self._plan_debug_sample_limit} 组 {kind.value} 方案枚举日志"
                )

        try:
            from tool.HomeGuard import run_homeguard
        except Exception as e:
            if debug:
                self._log_debug(f"Error importing HomeGuard: {e}")
            return True, {"error": str(e)}

        plan_results: List[Dict[str, Any]] = []
        cheap_checker_skipped_count = 0
        homeguard_checked_count = 0
        worker_count = self._get_homeguard_parallel_workers(len(plans))
        self._log_summary(
            f"质量检查Agent: {kind.value} 层使用默认并行模式，HomeGuard worker_count={worker_count}"
        )
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="QualityCheckerHomeGuard") as executor:
            future_to_index = {
                executor.submit(
                    self._evaluate_conflict_plan,
                    kind,
                    plan,
                    idx,
                    len(plans),
                    debug,
                    run_homeguard,
                ): idx
                for idx, plan in enumerate(plans, start=1)
            }
            ordered_results: list[Dict[str, Any]] = []
            for future in as_completed(future_to_index):
                worker_result = future.result()
                if not worker_result:
                    continue
                cheap_checker_skipped_count += int(worker_result.get("cheap_checker_skipped") or 0)
                homeguard_checked_count += int(worker_result.get("homeguard_checked") or 0)
                ordered_results.append(worker_result)

        ordered_results.sort(key=lambda item: int(item.get("index") or 0))
        plan_results = [item["plan_result"] for item in ordered_results]

        if not plan_results:
            return True, {}

        ok_plan_count = sum(1 for p in plan_results if p.get("ok") is True)
        any_plan_ok = ok_plan_count > 0
        all_plans_ok = ok_plan_count == len(plan_results)
        summary = {
            "plan_count": len(plan_results),
            "ok_plan_count": ok_plan_count,
            "failed_plan_count": len(plan_results) - ok_plan_count,
            "skipped_plan_count": cheap_checker_skipped_count,
            "homeguard_checked_plan_count": homeguard_checked_count,
            "any_plan_ok": any_plan_ok,
            "all_plans_ok": all_plans_ok,
            "pass_criterion": "all_plans_must_be_conflict_free",
        }
        aggregated_conflicts = self._aggregate_plan_conflicts(plan_results)
        result = dict(aggregated_conflicts)
        result["summary"] = summary
        result["plan_results"] = plan_results
        result["mode"] = "relation_grouped"
        self._log_summary(
            f"质量检查Agent: {kind.value} 层检查完成，共 {summary['plan_count']} 组方案，"
            f"通过 {summary['ok_plan_count']} 组，失败 {summary['failed_plan_count']} 组，"
            f"cheap checker 跳过 {summary['skipped_plan_count']} 组，"
            f"HomeGuard 实检 {summary['homeguard_checked_plan_count']} 组，"
            f"层级结果={'通过' if all_plans_ok else '失败'}"
        )
        return all_plans_ok, result

    def detect_impl_conflicts(self, SYS: object = None, IRP: str = None, device_types: List[str] = None, specific_devices: List[str] = None, debug: bool = False) -> tuple[bool, Dict[str, Any]]:
        return self._run_conflict_check_by_plans(NodeKind.DSL, debug=debug)

    def detect_planning_conflicts(self, debug: bool = False) -> tuple[bool, Dict[str, Any]]:
        plans = self._build_rule_plans_from_tree(NodeKind.IRP)
        reason = "暂时跳过 IRP 规划冲突检查，不送检 HomeGuard"
        self._log_summary(f"质量检查Agent: {reason}，可检查方案数={len(plans)}")
        return True, self._build_skipped_conflict_result(NodeKind.IRP, reason, plan_count=len(plans))

    def check_intent_satisfaction(self, debug: bool = False) -> tuple[bool, List[str]]:
        """
        检查所有INT节点是否满足其约束条件。
        """
        # 获取所有 INT 节点；若专用方法不可用，则回退为按类型检索
        int_nodes = list[str](self.trace_tree.get_int_nodes()) or list[str](self.trace_tree.find_by_type(NodeKind.INT)) or []

        # 收集所有 IRP 节点的 id 与文本，供后续约束引用比对
        irp_ids = set[Any]()
        irp_texts = set[Any]()
        irp_nodes = []
        try:
            irp_nodes = list[str](self.trace_tree.find_by_type(NodeKind.IRP))
        except Exception:
            irp_nodes = []
        for irp_id in irp_nodes:
            info = self.trace_tree.get_node(irp_id)
            if isinstance(info, dict):
                irp_ids.add(irp_id)
                irp_texts.add(info.get("id"))
                d = info.get("data") or {}
                s = d.get("IRP")
                if isinstance(s, str) and s.strip():
                    irp_texts.add(s)
        unsatisfied: List[str] = []
        for nid in int_nodes:
            ok = False
            try:
                info = self.trace_tree.get_node(nid)
            except Exception:
                unsatisfied.append(nid)
                continue
            children = (info or {}).get("children") or []
            # 规则1：若 INT 自身拥有至少一个子节点，则视为已满足
            ok = isinstance(children, list) and len(children) > 0
            # 规则2：若为约束 INT，且其约束对象引用了某个 IRP（id 或文本），也视为满足
            if not ok:
                data = (info or {}).get("data") or {}
                if data.get("isConstraint") is True:
                    ok = True
                    # TODO 暂时不考虑质量约束是否约束了某个功能需求，只要他是质量约束，就视作满足
                    objs = data.get("constraintObject") or []
                    if not isinstance(objs, list):
                        objs = [objs] if objs else []
                    for obj in objs:
                        if isinstance(obj, str) and obj.strip():
                            if (obj in irp_ids) or (obj in irp_texts):
                                ok = True
                                break
            if not ok:
                unsatisfied.append(nid)
        all_ok = len(unsatisfied) == 0
        return all_ok, unsatisfied

    def run(self, debug: bool = False, test_flag: bool = False) -> Dict[str, Any]:
        with get_default_ai_client().usage_scope("QualityChecker", scope_type="agent"), get_overhead_recorder().agent_run("QualityChecker"):
            emit_agent_status("QualityChecker", "started", "质量检查Agent开始检查双向需求追溯树")
            self._log_summary("质量检查Agent: 开始检查双向需求追溯树")

            # 初始化共享追踪树并开始一次检查流程
            self._begin_run()

            # 执行 INT 满足性检查（仅输出调试信息，留作后续扩展）
            self._log_summary("质量检查Agent: 开始执行 INT 满足性检查")
            emit_agent_status("QualityChecker", "progress", "执行 INT 满足性检查", {"step": "int_satisfaction"})
            all_satisfied, unsatisfied = self.check_intent_satisfaction(debug)
            self._log_summary(
                f"质量检查Agent: INT 满足性检查完成，结果={'通过' if all_satisfied else '失败'}，"
                f"未满足节点数={len(unsatisfied)}"
            )
            if debug and unsatisfied:
                self._log_debug(
                    f"质量检查Agent: 未满足 INT 节点: {json.dumps(unsatisfied, ensure_ascii=False)}"
                )
            
            # 执行 IRP 规划冲突检查（仅输出调试信息，留作后续扩展）
            self._log_summary("质量检查Agent: 开始执行 IRP 规划冲突检查")
            emit_agent_status("QualityChecker", "progress", "执行 IRP 规划冲突检查", {"step": "irp_conflict_check"})
            all_planning_ok, planning_result = self.detect_planning_conflicts(debug)

            # 执行 DSL 实现冲突检查（仅输出调试信息，留作后续扩展）
            self._log_summary("质量检查Agent: 开始执行 DSL 实现冲突检查")
            emit_agent_status("QualityChecker", "progress", "执行 DSL 实现冲突检查", {"step": "dsl_conflict_check"})
            all_impl_ok, impl_result = self.detect_impl_conflicts(debug=debug)

            result_payload = {
                "check_result": {
                "all_INT_satisfied": all_satisfied, 
                "unsatisfied_INTs": unsatisfied, 
                "all_IRP_ok": all_planning_ok, 
                "IRP_checking_result": planning_result, 
                "all_TAP_ok": all_impl_ok,
                "TAP_checking_result": impl_result
            }}

            # RQ3 开销埋点：记录 Checker 的 pass/fail 与问题类型（HomeGuard 冲突类别 + INT 未满足）
            checker_pass = bool(all_satisfied and all_planning_ok and all_impl_ok)
            feedback_types: List[str] = []
            for layer_result in (planning_result, impl_result):
                if not isinstance(layer_result, dict):
                    continue
                for key, value in layer_result.items():
                    if key in ("summary", "plan_results", "mode"):
                        continue
                    if isinstance(value, list) and value and key not in feedback_types:
                        feedback_types.append(key)
            if not all_satisfied and "INT未满足" not in feedback_types:
                feedback_types.append("INT未满足")
            get_overhead_recorder().set_checker_result("pass" if checker_pass else "fail", feedback_types)

            if debug:
                self._log_debug(
                    "质量检查结果: "
                    + json.dumps(
                        {
                            "all_INT_satisfied": all_satisfied,
                            "unsatisfied_INT_count": len(unsatisfied),
                            "all_IRP_ok": all_planning_ok,
                            "IRP_checking_result": self._strip_plan_results_for_logging(planning_result),
                            "all_TAP_ok": all_impl_ok,
                            "TAP_checking_result": self._strip_plan_results_for_logging(impl_result),
                        },
                        ensure_ascii=False,
                    )
                )

            if not test_flag:
                self.bus.publish("quality_checker.result", result_payload)

            self._log_summary(
                "质量检查Agent: 检查汇总 "
                + json.dumps(
                    {
                        "all_INT_satisfied": all_satisfied,
                        "all_IRP_ok": all_planning_ok,
                        "all_TAP_ok": all_impl_ok,
                        "irp_plan_count": ((planning_result or {}).get("summary") or {}).get("plan_count", 0),
                        "tap_plan_count": ((impl_result or {}).get("summary") or {}).get("plan_count", 0),
                    },
                    ensure_ascii=False,
                )
            )
            emit_agent_status(
                "QualityChecker",
                "finished",
                "质量检查Agent检查完成",
                {
                    "all_INT_satisfied": all_satisfied,
                    "all_IRP_ok": all_planning_ok,
                    "all_TAP_ok": all_impl_ok,
                },
            )
            self._log_summary("质量检查Agent: 检查完成")
            return result_payload
