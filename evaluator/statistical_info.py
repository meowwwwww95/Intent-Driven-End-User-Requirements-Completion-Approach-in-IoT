import argparse
import csv
import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_METHOD_ORDER = ["Manus", "metaGPT", "chatIoT", "our_tool"]
METRIC_NAMES = ["意图覆盖率", "实现多样率", "规则补全率", "规则幻觉率", "规则冲突率"]


def _get_nested(d: Any, keys: List[str]) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None
    return None


# 方法名归一化：chatdev 结果并入 metaGPT 统计
def _normalize_method(method_raw: str) -> str:
    m = (method_raw or "").strip()
    ml = m.lower()
    if ml == "manus":
        return "Manus"
    if ml in {"metagpt", "meta_gpt", "meta-gpt", "chatdev"}:
        return "metaGPT"
    if ml in {"chatiot", "chat_iot", "chat-iot", "chatiot ", "chatiot\t"}:
        return "chatIoT"
    if ml in {"our_tool", "ourtool", "our-tool"}:
        return "our_tool"
    return m or method_raw


def _extract_metrics(eval_obj: Dict[str, Any]) -> Dict[str, Optional[float]]:
    results = eval_obj.get("results") if isinstance(eval_obj, dict) else None
    if not isinstance(results, dict):
        results = {}

    intent_coverage_rate = _to_float(_get_nested(results, ["intent_coverage", "intent_coverage_rate"]))
    implementation_diversity_rate = _to_float(
        _get_nested(results, ["implementation_diversity_rate", "implementation_diversity_rate"])
    )

    completion_rate = _to_float(_get_nested(results, ["rule_completion", "completion_rate"]))

    hallucination_rate = _to_float(_get_nested(results, ["rule_hallucination", "hallucination_rate"]))
    conflict_rate = _to_float(_get_nested(results, ["rule_conflict_rate", "conflict_rate"]))

    return {
        "意图覆盖率": intent_coverage_rate,
        "实现多样率": implementation_diversity_rate,
        "规则补全率": completion_rate,
        "规则幻觉率": hallucination_rate,
        "规则冲突率": conflict_rate,
    }


def _sort_key_token(s: str) -> Tuple[str, int, str]:
    m = re.search(r"(\d+)", s or "")
    if not m:
        return (s or "", -1, s or "")
    return (s[: m.start()], int(m.group(1)), s[m.end() :])


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


def _discover_ground_truth(case_dir: str) -> Optional[str]:
    if not os.path.isdir(case_dir):
        return None
    cands = sorted(
        p
        for p in (os.path.join(case_dir, n) for n in os.listdir(case_dir))
        if p.endswith("_ground_truth.json") and os.path.isfile(p)
    )
    return cands[0] if cands else None


def _has_implementation_diversity(mapping_relationship: Any) -> bool:
    if not isinstance(mapping_relationship, list):
        return False
    for m in mapping_relationship:
        if not isinstance(m, dict):
            continue
        groups = _normalize_realization_groups(m.get("realization_taps"))
        if len(groups) > 1:
            return True
    return False


def scan_diversity_cases(input_root: str) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    root = os.path.abspath(input_root)
    if not os.path.isdir(root):
        return result
    for dataset in sorted(os.listdir(root)):
        dataset_dir = os.path.join(root, dataset)
        if not os.path.isdir(dataset_dir):
            continue
        cases: List[str] = []
        for case_name in sorted(os.listdir(dataset_dir), key=_sort_key_token):
            case_dir = os.path.join(dataset_dir, case_name)
            if not os.path.isdir(case_dir):
                continue
            gt_path = _discover_ground_truth(case_dir)
            if not gt_path:
                continue
            try:
                with open(gt_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                try:
                    with open(gt_path, "r", encoding="utf-8-sig") as f:
                        obj = json.load(f)
                except Exception:
                    continue
            mapping_relationship = (obj or {}).get("mapping_relationship")
            if _has_implementation_diversity(mapping_relationship):
                cases.append(case_name)
        if cases:
            result[dataset] = cases
    return result


def _write_diversity_output(out_path: str, data: Dict[str, List[str]]) -> None:
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if out_path.lower().endswith(".csv"):
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["数据集", "case"])
            for dataset in sorted(data.keys(), key=_sort_key_token):
                for case_name in data[dataset]:
                    w.writerow([dataset, case_name])
        return
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _count_standard_taps(obj: Any) -> int:
    taps = (obj or {}).get("standard_taps") if isinstance(obj, dict) else None
    if not isinstance(taps, list):
        return 0
    return len([t for t in taps if isinstance(t, str) and t.strip()])


def scan_standard_taps_counts(input_root: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    root = os.path.abspath(input_root)
    if not os.path.isdir(root):
        return result
    for dataset in sorted(os.listdir(root)):
        dataset_dir = os.path.join(root, dataset)
        if not os.path.isdir(dataset_dir):
            continue
        cases: Dict[str, int] = {}
        total = 0
        for case_name in sorted(os.listdir(dataset_dir), key=_sort_key_token):
            case_dir = os.path.join(dataset_dir, case_name)
            if not os.path.isdir(case_dir):
                continue
            gt_path = _discover_ground_truth(case_dir)
            if not gt_path:
                continue
            try:
                with open(gt_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                try:
                    with open(gt_path, "r", encoding="utf-8-sig") as f:
                        obj = json.load(f)
                except Exception:
                    continue
            cnt = _count_standard_taps(obj)
            cases[case_name] = cnt
            total += cnt
        if cases:
            result[dataset] = {"total": total, "cases": cases}
    return result


def _write_standard_taps_output(out_path: str, data: Dict[str, Dict[str, Any]]) -> None:
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if out_path.lower().endswith(".csv"):
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["数据集", "case", "standard_taps_count"])
            for dataset in sorted(data.keys(), key=_sort_key_token):
                cases = (data[dataset] or {}).get("cases") or {}
                for case_name in sorted(cases.keys(), key=_sort_key_token):
                    w.writerow([dataset, case_name, cases.get(case_name, 0)])
        return
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def scan_output(output_root: str) -> Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]]:
    collected: Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]] = defaultdict(dict)

    for dirpath, _, filenames in os.walk(output_root):
        if os.path.basename(dirpath).lower() != "evaluation":
            continue

        case_dir = os.path.dirname(dirpath)
        dataset_dir = os.path.dirname(case_dir)
        case_name = os.path.basename(case_dir)
        dataset_name = os.path.basename(dataset_dir)

        for fn in filenames:
            if not fn.lower().endswith(".json"):
                continue

            m = re.match(r"^(?P<case>.+?)_(?P<method>[^.]+)\.json$", fn)
            if not m:
                continue

            method = _normalize_method(m.group("method"))
            fp = os.path.join(dirpath, fn)

            try:
                with open(fp, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                try:
                    with open(fp, "r", encoding="utf-8-sig") as f:
                        obj = json.load(f)
                except Exception:
                    continue

            metrics = _extract_metrics(obj)
            collected[(dataset_name, case_name)][method] = metrics

    return dict(collected)


def _build_method_list(
    collected: Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]]
) -> List[str]:
    discovered: List[str] = []
    seen = set()
    for per_case in collected.values():
        for m in per_case.keys():
            if m not in seen:
                discovered.append(m)
                seen.add(m)

    ordered: List[str] = []
    for m in DEFAULT_METHOD_ORDER:
        if m in seen:
            ordered.append(m)
            seen.remove(m)
    for m in sorted(seen, key=_sort_key_token):
        ordered.append(m)
    return ordered


def build_metric_table(
    collected: Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]],
    metric_name: str,
    methods: List[str],
) -> Tuple[List[str], List[List[Any]]]:
    headers = ["数据集", "case", *methods]
    rows: List[List[Any]] = []
    keys = sorted(collected.keys(), key=lambda x: (_sort_key_token(x[0]), _sort_key_token(x[1])))

    for dataset_name, case_name in keys:
        row: List[Any] = [dataset_name, case_name]
        per_case = collected.get((dataset_name, case_name), {})
        for method in methods:
            mm = per_case.get(method, {})
            row.append(mm.get(metric_name))
        rows.append(row)

    return headers, rows


def build_wide_table_for_csv(
    collected: Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]],
    methods: List[str],
) -> Tuple[List[str], List[List[Any]]]:
    headers = ["数据集", "case"]
    for method in methods:
        for metric_name in METRIC_NAMES:
            headers.append(f"{method}_{metric_name}")

    rows: List[List[Any]] = []
    keys = sorted(collected.keys(), key=lambda x: (_sort_key_token(x[0]), _sort_key_token(x[1])))

    for dataset_name, case_name in keys:
        row: List[Any] = [dataset_name, case_name]
        per_case = collected.get((dataset_name, case_name), {})
        for method in methods:
            mm = per_case.get(method, {})
            for metric_name in METRIC_NAMES:
                row.append(mm.get(metric_name))
        rows.append(row)

    return headers, rows


def write_xlsx_openpyxl(
    out_xlsx: str,
    collected: Dict[Tuple[str, str], Dict[str, Dict[str, Optional[float]]]],
    methods: List[str],
) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws0 = wb.active
    ws0.title = METRIC_NAMES[0]
    headers0, rows0 = build_metric_table(collected, METRIC_NAMES[0], methods)
    ws0.append(headers0)
    for r in rows0:
        ws0.append(r)

    for metric_name in METRIC_NAMES[1:]:
        ws = wb.create_sheet(title=metric_name)
        headers, rows = build_metric_table(collected, metric_name, methods)
        ws.append(headers)
        for r in rows:
            ws.append(r)

    wb.save(out_xlsx)


def write_csv(out_csv: str, headers: List[str], rows: List[List[Any]]) -> None:
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow(r)


def main() -> int:
    parser = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)

    parser.add_argument(
        "--output_root",
        default=os.path.join(project_root, "output"),
        help="项目 output 的路径",
    )
    parser.add_argument(
        "--out",
        default="",
        help="输出文件路径（默认输出到 output_root 目录；优先 xlsx，缺依赖则 csv）",
    )
    parser.add_argument(
        "--list_diversity_cases",
        action="store_true",
        help="统计实现多样性分母非零的 case 列表",
    )
    parser.add_argument(
        "--sum_standard_taps",
        action="store_true",
        help="统计每个数据集每个 case 的 standard_taps 数量及总和",
    )
    parser.add_argument(
        "--input_root",
        default=os.path.join(project_root, "input"),
        help="项目 input 的路径",
    )

    args = parser.parse_args()
    if args.list_diversity_cases:
        input_root = os.path.abspath(args.input_root)
        data = scan_diversity_cases(input_root)
        out_path = (args.out or "").strip()
        if out_path:
            _write_diversity_output(out_path, data)
            print(f"[OK] 已输出: {os.path.abspath(out_path)}")
        total_cases = sum(len(v) for v in data.values())
        print(f"[INFO] 共 {len(data)} 个数据集，{total_cases} 个 case 满足分母非零")
        for dataset in sorted(data.keys(), key=_sort_key_token):
            cases = data[dataset]
            print(f"{dataset}: {len(cases)}")
            for c in cases:
                print(f"  - {c}")
        return 0
    if args.sum_standard_taps:
        input_root = os.path.abspath(args.input_root)
        data = scan_standard_taps_counts(input_root)
        out_path = (args.out or "").strip()
        if out_path:
            _write_standard_taps_output(out_path, data)
            print(f"[OK] 已输出: {os.path.abspath(out_path)}")
        dataset_total = len(data)
        grand_total = sum(int((v or {}).get("total") or 0) for v in data.values())
        print(f"[INFO] 共 {dataset_total} 个数据集，standard_taps 总数 {grand_total}")
        for dataset in sorted(data.keys(), key=_sort_key_token):
            ds = data[dataset] or {}
            total = int(ds.get("total") or 0)
            cases = ds.get("cases") or {}
            print(f"{dataset}: {total}")
            for case_name in sorted(cases.keys(), key=_sort_key_token):
                print(f"  - {case_name}: {cases.get(case_name, 0)}")
        return 0
    output_root = os.path.abspath(args.output_root)
    if not os.path.isdir(output_root):
        raise FileNotFoundError(output_root)

    collected = scan_output(output_root)
    methods = _build_method_list(collected)

    out_path = (args.out or "").strip()
    if not out_path:
        out_path = os.path.join(output_root, "statistics.xlsx")

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    want_xlsx = out_path.lower().endswith(".xlsx") or out_path.lower().endswith(".xlsm")
    if not want_xlsx:
        headers, rows = build_wide_table_for_csv(collected, methods)
        write_csv(out_path, headers, rows)
        print(f"[OK] 已输出: {out_path}")
        print(f"[INFO] 共 {len(rows)} 行（按 数据集/case 汇总）")
        return 0

    try:
        write_xlsx_openpyxl(out_path, collected, methods)
        print(f"[OK] 已输出: {out_path}")
        print(f"[INFO] 共 {len(collected)} 行（按 数据集/case 汇总）")
        return 0
    except PermissionError:
        print(f"[ERROR] 无法写入（文件可能正在被 Excel 占用）：{out_path}")
        return 1
    except Exception as e:
        base = re.sub(r"\.xlsx$", "", out_path, flags=re.IGNORECASE)
        name_map = {
            "意图覆盖率": "intent_coverage",
            "实现多样率": "implementation_diversity_rate",
            "规则补全率": "rule_completion",
            "规则幻觉率": "rule_hallucination",
            "规则冲突率": "rule_conflict",
        }
        row_count = len(collected)
        for metric_name in METRIC_NAMES:
            headers, rows = build_metric_table(collected, metric_name, methods)
            suffix = name_map.get(metric_name, metric_name)
            csv_path = f"{base}.{suffix}.csv"
            write_csv(csv_path, headers, rows)

        print(f"[WARN] 写入 xlsx 失败（可能缺 openpyxl）：{e}")
        print(f"[OK] 已改为输出 4 个 CSV（Excel 可直接打开）：{base}.*.csv")
        print(f"[INFO] 共 {row_count} 行（按 数据集/case 汇总）")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
