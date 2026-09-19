from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union
from evaluator.rule_similarity import evaluate_tap_rule_similarity, parse_tap_rule


Mode = Literal["summary", "detailed"]


@dataclass(frozen=True)
class RuleMatchDetail:
    source_rule: str
    matched: bool
    best_target_rule: Optional[str]
    best_target_index: Optional[int]
    best_score: float


@dataclass(frozen=True)
class IntentCoverageDetail:
    intent: str
    covered: bool
    score: float
    rationale: Optional[str]
    matched_rule_indices: List[int] = field(default_factory=list)


def _coerce_tap_list(rules: Union[str, Sequence[str], None]) -> List[str]:
    if rules is None:
        return []
    if isinstance(rules, str):
        items = [s.strip() for s in rules.splitlines()]
        return [s for s in items if s]
    out: List[str] = []
    for r in rules:
        if isinstance(r, str):
            s = r.strip()
            if s:
                out.append(s)
    return out


def _coerce_intent_list(intents: Union[str, Sequence[str], None]) -> List[str]:
    if intents is None:
        return []
    if isinstance(intents, str):
        items = [s.strip() for s in intents.splitlines()]
        return [s for s in items if s]
    out: List[str] = []
    for it in intents:
        if isinstance(it, str):
            s = it.strip()
            if s:
                out.append(s)
    return out


def _cap_norm(s: str) -> str:
    t = (s or "").strip()
    return t[:-2] if t.endswith("()") else t


def _signature(tap: str) -> Optional[Tuple[str, str, Tuple[Tuple[str, str], ...]]]:
    parsed = parse_tap_rule(tap)
    if not parsed.get("success"):
        return None
    action = parsed.get("action") or {}
    act_dev = str(action.get("device", "")).strip()
    act_cap = _cap_norm(str(action.get("capability", "")).strip())
    cond_pairs: List[Tuple[str, str]] = []
    for c in parsed.get("conditions", []) or []:
        dev = str(c.get("device", "")).strip()
        cap = _cap_norm(str(c.get("capability", "")).strip())
        cond_pairs.append((dev, cap))
    cond_pairs_sorted = tuple(sorted(set(cond_pairs)))
    return act_dev, act_cap, cond_pairs_sorted


def _pair_similarity(a: str, b: str, device_profile: List[Dict[str, Any]]) -> float:
    if a.replace(" ", "").lower() == b.replace(" ", "").lower():
        return 1.0
    sa = _signature(a)
    sb = _signature(b)
    if sa is None or sb is None:
        return 0.0
    if sa != sb:
        return 0.0
    res = evaluate_tap_rule_similarity(a, b, device_profile=device_profile)
    return float(res.get("scores", {}).get("overall", 0.0))


def _build_index(rules: Sequence[str]) -> Dict[Tuple[str, str, Tuple[Tuple[str, str], ...]], List[int]]:
    idx: Dict[Tuple[str, str, Tuple[Tuple[str, str], ...]], List[int]] = {}
    for i, r in enumerate(rules):
        sig = _signature(r)
        if sig is None:
            continue
        idx.setdefault(sig, []).append(i)
    return idx


def _signature_set(rules: Sequence[str]) -> set[Tuple[str, str, Tuple[Tuple[str, str], ...]]]:
    out: set[Tuple[str, str, Tuple[Tuple[str, str], ...]]] = set()
    for r in rules:
        sig = _signature(r)
        if sig is not None:
            out.add(sig)
    return out


def _match_one_to_one(
    sources: Sequence[str],
    targets: Sequence[str],
    device_profile: List[Dict[str, Any]],
    threshold: float,
) -> Tuple[int, List[RuleMatchDetail]]:
    target_index = _build_index(targets)
    used_targets = set()
    matched = 0
    details: List[RuleMatchDetail] = []

    for s in sources:
        sig = _signature(s)
        candidate_ids = target_index.get(sig, []) if sig is not None else []

        best_score = 0.0
        best_rule: Optional[str] = None
        best_tid: Optional[int] = None

        for tid in candidate_ids:
            if tid in used_targets:
                continue
            t = targets[tid]
            score = _pair_similarity(s, t, device_profile=device_profile)
            if score > best_score:
                best_score = score
                best_rule = t
                best_tid = tid

        ok = best_score >= threshold and best_rule is not None
        if ok:
            matched += 1
            if best_tid is not None:
                used_targets.add(best_tid)
        details.append(
            RuleMatchDetail(
                source_rule=s,
                matched=ok,
                best_target_rule=best_rule,
                best_target_index=best_tid,
                best_score=best_score,
            )
        )

    return matched, details


def _evaluate_rule_alignment(
    gold_tap_rules: Union[str, Sequence[str]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    threshold: float,
) -> Dict[str, Any]:
    gold = _coerce_tap_list(gold_tap_rules)
    pred = _coerce_tap_list(pred_tap_rules)

    gold_sig_set = _signature_set(gold)
    irrelevant_pred = 0
    invalid_pred = 0
    for r in pred:
        sig = _signature(r)
        if sig is None:
            invalid_pred += 1
            continue
        if sig not in gold_sig_set:
            irrelevant_pred += 1

    matched_gold, gold_details = _match_one_to_one(
        sources=gold,
        targets=pred,
        device_profile=device_profile,
        threshold=threshold,
    )
    matched_pred, pred_details = _match_one_to_one(
        sources=pred,
        targets=gold,
        device_profile=device_profile,
        threshold=threshold,
    )

    return {
        "gold": gold,
        "pred": pred,
        "matched_gold": matched_gold,
        "matched_pred": matched_pred,
        "irrelevant_pred": irrelevant_pred,
        "invalid_pred": invalid_pred,
        "gold_details": gold_details,
        "pred_details": pred_details,
    }


def evaluate_rule_completion(
    gold_tap_rules: Union[str, Sequence[str]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    threshold: float = 0.95,
    mode: Mode = "summary",
) -> Dict[str, Any]:
    aligned = _evaluate_rule_alignment(
        gold_tap_rules=gold_tap_rules,
        pred_tap_rules=pred_tap_rules,
        device_profile=device_profile,
        threshold=threshold,
    )

    gold_need = len(aligned["gold"])
    matched_gold = int(aligned["matched_gold"])
    completion_rate = 100.0 if gold_need == 0 else (matched_gold / gold_need) * 100.0

    if mode == "summary":
        return {
            "completion_rate": completion_rate,
            "threshold": threshold,
            "counts": {"gold_total": gold_need, "matched_gold": matched_gold},
        }

    return {
        "completion_rate": completion_rate,
        "threshold": threshold,
        "counts": {"gold_total": gold_need, "matched_gold": matched_gold},
        "details": {"gold_to_pred": [d.__dict__ for d in aligned["gold_details"]]},
    }


def evaluate_rule_hallucination(
    gold_tap_rules: Union[str, Sequence[str]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    threshold: float = 0.95,
    mode: Mode = "summary",
) -> Dict[str, Any]:
    aligned = _evaluate_rule_alignment(
        gold_tap_rules=gold_tap_rules,
        pred_tap_rules=pred_tap_rules,
        device_profile=device_profile,
        threshold=threshold,
    )

    pred_total = len(aligned["pred"])
    irrelevant_pred = int(aligned["irrelevant_pred"])
    invalid_pred = int(aligned["invalid_pred"])
    matched_pred = int(aligned["matched_pred"])

    hallucination_rate = 0.0 if pred_total == 0 else (irrelevant_pred / pred_total) * 100.0

    if mode == "summary":
        return {
            "hallucination_rate": hallucination_rate,
            "threshold": threshold,
            "counts": {
                "pred_total": pred_total,
                "matched_pred": matched_pred,
                "irrelevant_pred": irrelevant_pred,
                "invalid_pred": invalid_pred,
            },
        }

    return {
        "hallucination_rate": hallucination_rate,
        "threshold": threshold,
        "counts": {
            "pred_total": pred_total,
            "matched_pred": matched_pred,
            "irrelevant_pred": irrelevant_pred,
            "invalid_pred": invalid_pred,
        },
        "details": {"pred_to_gold": [d.__dict__ for d in aligned["pred_details"]]},
    }


def evaluate_rule_completion_and_hallucination(
    gold_tap_rules: Union[str, Sequence[str]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    threshold: float = 0.95,
    mode: Mode = "summary",
) -> Dict[str, Any]:
    aligned = _evaluate_rule_alignment(
        gold_tap_rules=gold_tap_rules,
        pred_tap_rules=pred_tap_rules,
        device_profile=device_profile,
        threshold=threshold,
    )

    gold_need = len(aligned["gold"])
    pred_total = len(aligned["pred"])
    matched_gold = int(aligned["matched_gold"])
    matched_pred = int(aligned["matched_pred"])
    irrelevant_pred = int(aligned["irrelevant_pred"])
    invalid_pred = int(aligned["invalid_pred"])

    completion_rate = 100.0 if gold_need == 0 else (matched_gold / gold_need) * 100.0
    hallucination_rate = 0.0 if pred_total == 0 else (irrelevant_pred / pred_total) * 100.0

    if mode == "summary":
        return {
            "completion_rate": completion_rate,
            "hallucination_rate": hallucination_rate,
            "threshold": threshold,
            "counts": {
                "gold_total": gold_need,
                "pred_total": pred_total,
                "matched_gold": matched_gold,
                "matched_pred": matched_pred,
                "irrelevant_pred": irrelevant_pred,
                "invalid_pred": invalid_pred,
            },
        }

    return {
        "completion_rate": completion_rate,
        "hallucination_rate": hallucination_rate,
        "threshold": threshold,
        "counts": {
            "gold_total": gold_need,
            "pred_total": pred_total,
            "matched_gold": matched_gold,
            "matched_pred": matched_pred,
            "irrelevant_pred": irrelevant_pred,
            "invalid_pred": invalid_pred,
        },
        "details": {
            "gold_to_pred": [d.__dict__ for d in aligned["gold_details"]],
            "pred_to_gold": [d.__dict__ for d in aligned["pred_details"]],
        },
    }


# 传感器类型归一化映射：覆盖当前支持的传感器类型，新增类型需扩展此映射
def _fallback_env_sensor_canonical(device_name: str) -> str | None:
    s = (device_name or "").strip()
    if not s:
        return None
    ss = s.lower()

    if not ss.endswith("_sensor") and "_sensor" not in ss:
        return None

    if "temperature" in ss or "_temp_sensor" in ss or ss.endswith("_temp") or "thermo" in ss:
        return "temperature_sensor"
    if "humidity" in ss or "_humid_sensor" in ss:
        return "humidity_sensor"
    if "brightness" in ss or "illuminance" in ss or "lux" in ss or "_light_sensor" in ss:
        return "brightness_sensor"
    if "pm25" in ss or "pm2_5" in ss or "pm2.5" in ss or "air_quality" in ss or "dust" in ss or "co2" in ss or "voc" in ss:
        return "air_quality_sensor"
    if "noise" in ss or "sound" in ss:
        return "sound_sensor"
    if "water_quality" in ss or "tds" in ss or "ph" in ss or "turbidity" in ss:
        return "water_quality_sensor"
    if "oil_fume" in ss or "oil_smoke" in ss or "fume" in ss:
        return "oil_fume_sensor"
    if "smoke" in ss or "gas" in ss or "ch4" in ss:
        return "smoke_sensor"
    if "human" in ss or "motion" in ss or "presence" in ss or "occupancy" in ss or "pir" in ss:
        return "presence_sensor"

    return None


def _canonical_from_reading_arg(arg: str | None) -> str | None:
    if not isinstance(arg, str):
        return None
    a = arg.strip()
    if not a:
        return None
    a = re.sub(r'^(["\'])|(["\'])$', "", a).strip()
    if not a:
        return None
    al = a.lower().replace(" ", "")

    if "temperature" in al or al in {"temp"}:
        return "temperature_sensor"
    if "humidity" in al or "relativehumidity" in al or al in {"rh"}:
        return "humidity_sensor"
    if "air_quality" in al or "airquality" in al or "pm25" in al or "pm2_5" in al or "pm2.5" in al or "co2" in al or "voc" in al or "dust" in al:
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


def _normalize_rule_for_homeguard(rule_text: str) -> str:
    if not isinstance(rule_text, str) or not rule_text.strip():
        return rule_text

    m = re.search(r"^\s*IF\s+(?P<cond>.*?)\s+THEN\s+(?P<act>.*?)\s*$", rule_text)
    if not m:
        return rule_text

    cond_span = m.span("cond")
    cond_text = rule_text[cond_span[0]:cond_span[1]]

    def _sub(match: re.Match) -> str:
        dev = match.group("dev")
        cap = match.group("cap")
        arg = match.group("arg")
        canonical = _fallback_env_sensor_canonical(dev)
        arg_canonical = _canonical_from_reading_arg(arg)
        if arg_canonical:
            canonical = arg_canonical
        if canonical:
            return f"{canonical}.reading()"
        call = match.group("call") or ""
        return f"{dev}.{cap}{call}"

    new_cond = re.sub(
        r"(?P<dev>[A-Za-z0-9_\u4e00-\u9fa5]+)\s*\.\s*(?P<cap>[A-Za-z0-9_\u4e00-\u9fa5]+)(?P<call>\s*\(\s*(?P<arg>[^)]*)\s*\))?",
        _sub,
        cond_text,
    )
    if new_cond == cond_text:
        return rule_text
    return rule_text[:cond_span[0]] + new_cond + rule_text[cond_span[1]:]


def _build_homeguard_input(rules: List[str]) -> tuple[List[str], dict[str, List[str]]]:
    normalized_to_originals: dict[str, List[str]] = {}
    for r in rules:
        nr = _normalize_rule_for_homeguard(r)
        normalized_to_originals.setdefault(nr, []).append(r)
    return list(normalized_to_originals.keys()), normalized_to_originals


def _restore_conflict_entry(entry: Any, normalized_to_originals: dict[str, List[str]]) -> List[Any]:
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
            restored_list = _restore_conflict_entry(x, normalized_to_originals)
            for r in restored_list:
                key = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(r)
        return [merged]

    return [entry]


def _restore_homeguard_conflicts(conflicts: Dict[str, Any], normalized_to_originals: dict[str, List[str]]) -> Dict[str, Any]:
    restored: Dict[str, Any] = {}
    for k, v in (conflicts or {}).items():
        if isinstance(v, list):
            new_list: List[Any] = []
            for item in v:
                restored_items = _restore_conflict_entry(item, normalized_to_originals)
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


def evaluate_rule_conflict_rate(pred_tap_rules: Union[str, Sequence[str]], mode: Mode = "summary") -> Dict[str, Any]:
    pred = _coerce_tap_list(pred_tap_rules)
    pred_total = len(pred)

    try:
        from tool.HomeGuard import run_homeguard

        normalized_rules, normalized_to_originals = _build_homeguard_input(pred)
        res = run_homeguard(normalized_rules)
        res = _restore_homeguard_conflicts(res, normalized_to_originals)
        # 冲突率仅统计多值冲突，其余冲突类型检出但不计入，属指标定义
        conflict_types = ["自冲突", "多值冲突", "冗余冲突", "并发冲突", "覆盖冲突"]
        rate_conflict_types = {"多值冲突"}
        conflicts_by_type: Dict[str, Any] = {}
        conflict_rule_texts: set[str] = set()

        for k in conflict_types:
            v = (res or {}).get(k)
            if not v:
                continue
            conflicts_by_type[k] = v
            if k not in rate_conflict_types:
                continue
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        parts = item.split(" ,and, ") if " ,and, " in item else [item]
                        for p in parts:
                            s = p.strip()
                            if s and s != "0":
                                conflict_rule_texts.add(s)
                    elif isinstance(item, list):
                        for p in item:
                            if isinstance(p, str):
                                s = p.strip()
                                if s and s != "0":
                                    conflict_rule_texts.add(s)
            elif isinstance(v, str):
                s = v.strip()
                if s and s != "0":
                    conflict_rule_texts.add(s)

        conflicting_pred = sum(1 for r in pred if r in conflict_rule_texts)
        conflict_rate = 0.0 if pred_total == 0 else (conflicting_pred / pred_total) * 100.0

        if mode == "summary":
            return {
                "success": True,
                "conflict_rate": conflict_rate,
                "counts": {"pred_total": pred_total, "conflicting_pred": conflicting_pred},
            }
        return {
            "success": True,
            "conflict_rate": conflict_rate,
            "counts": {"pred_total": pred_total, "conflicting_pred": conflicting_pred},
            "details": {"conflicts_by_type": conflicts_by_type},
        }
    except Exception as e:
        if mode == "summary":
            return {
                "success": False,
                "conflict_rate": None,
                "counts": {"pred_total": pred_total, "conflicting_pred": None},
                "error": f"homeguard_error:{e}",
            }
        return {
            "success": False,
            "conflict_rate": None,
            "counts": {"pred_total": pred_total, "conflicting_pred": None},
            "error": f"homeguard_error:{e}",
            "details": {"conflicts_by_type": {}},
        }


def _dedupe_keep_order(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _coerce_mapping_relationship(x: Any) -> List[Dict[str, Any]]:
    if not isinstance(x, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in x:
        if isinstance(it, dict):
            out.append(it)
    return out


def _normalize_realization_groups(rt: Any) -> List[List[str]]:
    if not isinstance(rt, list):
        return []
    has_list = any(isinstance(x, list) for x in rt)
    if not has_list:
        group_rules = [s.strip() for s in rt if isinstance(s, str) and s.strip()]
        return [group_rules] if group_rules else []
    groups: List[List[str]] = []
    for group in rt:
        if isinstance(group, list):
            group_rules = [s.strip() for s in group if isinstance(s, str) and s.strip()]
            if group_rules:
                groups.append(group_rules)
        elif isinstance(group, str) and group.strip():
            groups.append([group.strip()])
    return groups


def _derive_gold_from_mapping(mapping_relationship: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    intents: List[str] = []
    taps: List[str] = []
    for m in mapping_relationship:
        ui = m.get("user_intent")
        if isinstance(ui, str) and ui.strip():
            intents.append(ui.strip())
        rt = m.get("realization_taps")
        if not isinstance(rt, list):
            continue
        for group in rt:
            if isinstance(group, list):
                for r in group:
                    if isinstance(r, str) and r.strip():
                        taps.append(r.strip())
            elif isinstance(group, str) and group.strip():
                taps.append(group.strip())
    return _dedupe_keep_order(intents), _dedupe_keep_order(taps)


def _evaluate_intent_coverage_by_mapping(
    gold_user_intents: Union[str, Sequence[str]],
    mapping_relationship: List[Dict[str, Any]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    intent_threshold: float,
    rule_threshold: float,
    mode: Mode,
) -> Dict[str, Any]:
    intents = _coerce_intent_list(gold_user_intents)
    pred = _coerce_tap_list(pred_tap_rules)

    index: Dict[str, Dict[str, Any]] = {}
    for m in mapping_relationship:
        ui = m.get("user_intent")
        if isinstance(ui, str) and ui.strip():
            index[ui.strip()] = m

    total = len(intents)
    covered = 0
    details: List[IntentCoverageDetail] = []

    for it in intents:
        m = index.get(it)
        rt = (m or {}).get("realization_taps")
        groups = _normalize_realization_groups(rt)

        best_score = 0.0
        best_matched_indices: List[int] = []
        if groups:
            for group_rules in groups:
                if not group_rules:
                    score = 1.0
                    matched_indices = []
                else:
                    matched_cnt, match_details = _match_one_to_one(
                        sources=group_rules,
                        targets=pred,
                        device_profile=device_profile,
                        threshold=float(rule_threshold),
                    )
                    score = float(matched_cnt) / float(len(group_rules))
                    matched_indices = [
                        int(d.best_target_index)
                        for d in match_details
                        if d.matched and isinstance(d.best_target_index, int)
                    ]

                if score > best_score:
                    best_score = score
                    best_matched_indices = matched_indices

        ok = best_score >= float(intent_threshold)
        if ok:
            covered += 1
        details.append(
            IntentCoverageDetail(
                intent=it,
                covered=ok,
                score=best_score,
                rationale="mapping_relationship",
                matched_rule_indices=sorted(set(best_matched_indices)),
            )
        )

    coverage_rate = 100.0 if total == 0 else (covered / total) * 100.0
    if mode == "summary":
        return {
            "intent_coverage_rate": coverage_rate,
            "threshold": float(intent_threshold),
            "rule_threshold": float(rule_threshold),
            "counts": {"intent_total": total, "intent_covered": covered},
        }

    return {
        "intent_coverage_rate": coverage_rate,
        "threshold": float(intent_threshold),
        "rule_threshold": float(rule_threshold),
        "counts": {"intent_total": total, "intent_covered": covered},
        "details": {"intents": [d.__dict__ for d in details]},
    }


def evaluate_intent_coverage_by_mapping(
    gold_user_intents: Union[str, Sequence[str]],
    mapping_relationship: List[Dict[str, Any]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    intent_threshold: float = 1,
    rule_threshold: float = 0.95,
    mode: Mode = "summary",
) -> Dict[str, Any]:
    return _evaluate_intent_coverage_by_mapping(
        gold_user_intents=gold_user_intents,
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_tap_rules,
        device_profile=device_profile,
        intent_threshold=float(intent_threshold),
        rule_threshold=float(rule_threshold),
        mode=mode,
    )


def evaluate_implementation_diversity_rate(
    mapping_relationship: List[Dict[str, Any]],
    pred_tap_rules: Union[str, Sequence[str]],
    device_profile: List[Dict[str, Any]],
    rule_threshold: float = 0.80,
    mode: Mode = "summary",
) -> Dict[str, Any]:
    pred = _coerce_tap_list(pred_tap_rules)

    total_groups = 0
    covered_groups = 0
    multi_intents = 0
    detail_items: List[Dict[str, Any]] = []

    for m in mapping_relationship:
        ui = m.get("user_intent")
        intent = ui.strip() if isinstance(ui, str) else ""
        rt = m.get("realization_taps")
        groups = _normalize_realization_groups(rt)
        if len(groups) <= 1:
            continue

        multi_intents += 1
        intent_groups: List[Dict[str, Any]] = []

        for gi, group_rules in enumerate(groups):
            total_groups += 1

            if not group_rules:
                covered = True
                matched_pred_indices: List[int] = []
            else:
                matched_cnt, match_details = _match_one_to_one(
                    sources=group_rules,
                    targets=pred,
                    device_profile=device_profile,
                    threshold=float(rule_threshold),
                )
                covered = matched_cnt == len(group_rules)
                matched_pred_indices = [
                    int(d.best_target_index)
                    for d in match_details
                    if d.matched and isinstance(d.best_target_index, int)
                ]

            if covered:
                covered_groups += 1

            if mode == "detailed":
                intent_groups.append(
                    {
                        "group_index": gi,
                        "group_rules": group_rules,
                        "covered": covered,
                        "matched_rule_indices": sorted(set(matched_pred_indices)),
                    }
                )

        if mode == "detailed":
            detail_items.append({"user_intent": intent, "groups": intent_groups})

    diversity_rate = 0.0 if total_groups == 0 else (covered_groups / total_groups)
    out: Dict[str, Any] = {
        "implementation_diversity_rate": diversity_rate,
        "rule_threshold": float(rule_threshold),
        "counts": {
            "multi_intent_total": multi_intents,
            "implementation_total": total_groups,
            "implementation_covered": covered_groups,
        },
    }
    if mode == "detailed":
        out["details"] = {"intents": detail_items}
    return out


def evaluate_case_detail_to_file(
    ground_truth_json_path: str,
    pred_rules_json_path: str,
    output_dir: str,
    output_suffix: str = "_our_tool.json",
    output_path: Optional[str] = None,
    input_json_path: Optional[str] = None,
    intent_threshold: float = 1,
    rule_threshold: float = 0.95,
) -> str:
    with open(ground_truth_json_path, "r", encoding="utf-8") as f:
        gt = json.load(f)

    mapping_relationship = _coerce_mapping_relationship((gt or {}).get("mapping_relationship"))
    derived_intents, derived_taps = _derive_gold_from_mapping(mapping_relationship)

    gold_user_intents = (gt or {}).get("user_intent") or derived_intents or []
    gold_tap_rules = (gt or {}).get("standard_taps") or derived_taps or []

    with open(pred_rules_json_path, "r", encoding="utf-8") as f:
        pred_payload = json.load(f)
    if isinstance(pred_payload, dict):
        pred_tap_rules = pred_payload.get("tap") or pred_payload.get("rules") or pred_payload.get("pred_tap_rules") or []
    else:
        pred_tap_rules = pred_payload

    if input_json_path is None:
        if ground_truth_json_path.endswith("_ground_truth.json"):
            cand = ground_truth_json_path[: -len("_ground_truth.json")] + "_input.json"
            if os.path.exists(cand):
                input_json_path = cand
        if input_json_path is None:
            base_dir = os.path.dirname(ground_truth_json_path)
            stem = os.path.basename(ground_truth_json_path).replace("_ground_truth.json", "")
            cand = os.path.join(base_dir, f"{stem}_input.json")
            if os.path.exists(cand):
                input_json_path = cand
        if input_json_path is None:
            base_dir = os.path.dirname(ground_truth_json_path)
            cands = sorted(
                p
                for p in (os.path.join(base_dir, n) for n in os.listdir(base_dir))
                if p.endswith("_input.json") and os.path.isfile(p)
            )
            input_json_path = cands[0] if cands else None

    env_profile: List[Dict[str, Any]] = []
    if input_json_path and os.path.exists(input_json_path):
        with open(input_json_path, "r", encoding="utf-8") as f:
            inp = json.load(f)
        env_profile = inp.get("env_profile") or []
        if not isinstance(env_profile, list):
            env_profile = []
        env_profile = [d for d in env_profile if isinstance(d, dict)]

    intent_eval = _evaluate_intent_coverage_by_mapping(
        gold_user_intents=gold_user_intents,
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_tap_rules,
        device_profile=env_profile,
        intent_threshold=float(intent_threshold),
        rule_threshold=float(rule_threshold),
        mode="detailed",
    )

    impl_div_eval = evaluate_implementation_diversity_rate(
        mapping_relationship=mapping_relationship,
        pred_tap_rules=pred_tap_rules,
        device_profile=env_profile,
        rule_threshold=float(rule_threshold),
        mode="detailed",
    )

    rule_completion_eval = evaluate_rule_completion(
        gold_tap_rules,
        pred_tap_rules,
        device_profile=env_profile,
        threshold=float(rule_threshold),
        mode="detailed",
    )
    rule_hallucination_eval = evaluate_rule_hallucination(
        gold_tap_rules,
        pred_tap_rules,
        device_profile=env_profile,
        threshold=float(rule_threshold),
        mode="detailed",
    )
    conflict_eval = evaluate_rule_conflict_rate(pred_tap_rules, mode="detailed")

    if output_path is None:
        os.makedirs(output_dir, exist_ok=True)
        pred_base = os.path.splitext(os.path.basename(pred_rules_json_path))[0]
        if not output_suffix.endswith(".json"):
            output_suffix = output_suffix + ".json"
        out_path = os.path.join(output_dir, f"{pred_base}{output_suffix}")
    else:
        out_path = output_path
        os.makedirs(os.path.dirname(out_path) or output_dir, exist_ok=True)

    out = {
        "inputs": {
            "ground_truth_json_path": ground_truth_json_path,
            "pred_rules_json_path": pred_rules_json_path,
            "input_json_path": input_json_path,
            "output_dir": output_dir,
        },
        "data": {
            "gold_user_intents": _coerce_intent_list(gold_user_intents),
            "gold_tap_rules": _coerce_tap_list(gold_tap_rules),
            "pred_tap_rules": _coerce_tap_list(pred_tap_rules),
            "device_profile": env_profile,
        },
        "results": {
            "intent_coverage": intent_eval,
            "implementation_diversity_rate": impl_div_eval,
            "rule_completion": rule_completion_eval,
            "rule_hallucination": rule_hallucination_eval,
            "rule_conflict_rate": conflict_eval,
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    return out_path


def evaluate_case_dir_detail_to_dir(
    ground_truth_json_path: str,
    case_output_dir: str,
    evaluation_dir: str,
    input_json_path: Optional[str] = None,
    intent_threshold: float = 1,
    rule_threshold: float = 0.95,
    only_methods: Optional[Sequence[str]] = None,
) -> List[str]:
    pred_files: List[str] = []
    for root, _, files in os.walk(case_output_dir):
        for name in files:
            if name.endswith("_res_dsl.json"):
                pred_files.append(os.path.join(root, name))

    allowed: Optional[set[str]] = None
    if only_methods:
        allowed = {str(m).strip().lower() for m in only_methods if str(m).strip()}

    out_paths: List[str] = []
    for pred_path in sorted(set(pred_files)):
        parent_dir = os.path.basename(os.path.dirname(pred_path))
        method_name = parent_dir
        base = os.path.splitext(os.path.basename(pred_path))[0]
        case_name = base[:-len("_res_dsl")] if base.endswith("_res_dsl") else base

        # 规范化方法名：若直接位于 case 目录，则视为 our_tool
        effective_method = (
            "our_tool" if (method_name == os.path.basename(case_output_dir)) else method_name
        )

        if allowed is not None:
            if effective_method.lower() not in allowed:
                continue

        if method_name and method_name != os.path.basename(case_output_dir):
            if method_name == "our_tool":
                out_name = f"{case_name}_our_tool.json"
            else:
                out_name = f"{case_name}_{method_name}.json"
        else:
            out_name = f"{case_name}_our_tool.json"

        out_path = os.path.join(evaluation_dir, out_name)
        out_paths.append(
            evaluate_case_detail_to_file(
                ground_truth_json_path=ground_truth_json_path,
                pred_rules_json_path=pred_path,
                output_dir=evaluation_dir,
                output_path=out_path,
                input_json_path=input_json_path,
                intent_threshold=float(intent_threshold),
                rule_threshold=float(rule_threshold),
            )
        )

    return out_paths


def evaluate_output_case_dir_detail(
    output_case_dir: str,
    input_case_dir: Optional[str] = None,
    evaluation_dir: Optional[str] = None,
    intent_threshold: float = 1,
    rule_threshold: float = 0.95,
    only_methods: Optional[Sequence[str]] = None,
) -> List[str]:
    out_case_dir = os.path.abspath(output_case_dir)
    if evaluation_dir is None:
        evaluation_dir = os.path.join(out_case_dir, "evaluation")
    os.makedirs(evaluation_dir, exist_ok=True)

    project_root = os.path.abspath(os.path.join(out_case_dir, "..", "..", ".."))
    parts = os.path.normpath(out_case_dir).split(os.sep)
    level = ""
    case_name = ""
    if "output" in parts:
        i = parts.index("output")
        if i + 2 < len(parts):
            level = parts[i + 1]
            case_name = parts[i + 2]

    if input_case_dir is None:
        if level and case_name:
            input_case_dir = os.path.join(project_root, "input", level, case_name)
        else:
            input_case_dir = os.path.join(project_root, "input")

    input_case_dir = os.path.abspath(input_case_dir)

    ground_truth_json_path = os.path.join(input_case_dir, f"{case_name}_ground_truth.json") if case_name else ""
    input_json_path = os.path.join(input_case_dir, f"{case_name}_input.json") if case_name else ""

    if not ground_truth_json_path or not os.path.exists(ground_truth_json_path):
        cands = sorted(
            p
            for p in (os.path.join(input_case_dir, n) for n in os.listdir(input_case_dir))
            if p.endswith("_ground_truth.json") and os.path.isfile(p)
        )
        if not cands:
            raise FileNotFoundError(f"missing_ground_truth_in:{input_case_dir}")
        ground_truth_json_path = cands[0]

    if not input_json_path or not os.path.exists(input_json_path):
        cands = sorted(
            p
            for p in (os.path.join(input_case_dir, n) for n in os.listdir(input_case_dir))
            if p.endswith("_input.json") and os.path.isfile(p)
        )
        input_json_path = cands[0] if cands else None

    return evaluate_case_dir_detail_to_dir(
        ground_truth_json_path=ground_truth_json_path,
        case_output_dir=out_case_dir,
        evaluation_dir=evaluation_dir,
        input_json_path=input_json_path,
        intent_threshold=float(intent_threshold),
        rule_threshold=float(rule_threshold),
        only_methods=only_methods,
    )


__all__ = [
    "evaluate_rule_completion",
    "evaluate_rule_hallucination",
    "evaluate_rule_conflict_rate",
    "evaluate_intent_coverage_by_mapping",
    "evaluate_case_detail_to_file",
    "evaluate_case_dir_detail_to_dir",
    "evaluate_output_case_dir_detail",
]
