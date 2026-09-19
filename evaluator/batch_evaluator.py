from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple
import time


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from evaluator.rule_set_metrics import evaluate_output_case_dir_detail


@dataclass(frozen=True)
class CaseItem:
    dataset: str
    case_name: str
    output_case_dir: str


def _parse_case_index(case_name: str) -> Optional[int]:
    s = (case_name or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"case(\d+)", s, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _strip_case_prefix(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    m = re.fullmatch(r"case(\d+)", t, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return t


def _parse_range_token(token: str) -> Optional[Tuple[int, int]]:
    t = _strip_case_prefix(token)
    if not t:
        return None
    if t.isdigit():
        n = int(t)
        return (n, n)
    if "-" not in t:
        return None
    a, b = t.split("-", 1)
    a = _strip_case_prefix(a)
    b = _strip_case_prefix(b)
    if not a.isdigit() or not b.isdigit():
        return None
    start = int(a)
    end = int(b)
    if start <= 0 or end <= 0:
        return None
    if start > end:
        start, end = end, start
    return (start, end)


def _match_tokens(tokens: Iterable[str], case_name: str) -> bool:
    name = (case_name or "").strip()
    if not name:
        return False

    norm = [t.strip() for t in tokens if isinstance(t, str) and t.strip()]
    if not norm:
        return False
    if name in set(norm):
        return True

    idx = _parse_case_index(name)
    if idx is None:
        return False
    for t in norm:
        rng = _parse_range_token(t)
        if rng is None:
            continue
        if rng[0] <= idx <= rng[1]:
            return True
    return False


def _compress_case_indices(indices: List[int]) -> List[Tuple[int, int]]:
    if not indices:
        return []
    uniq = sorted(set(i for i in indices if isinstance(i, int)))
    out: List[Tuple[int, int]] = []
    start = uniq[0]
    prev = uniq[0]
    for x in uniq[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append((start, prev))
        start = x
        prev = x
    out.append((start, prev))
    return out


def _format_dataset_cases(dataset: str, cases: List[CaseItem]) -> Optional[str]:
    indices: List[int] = []
    others: List[str] = []
    for c in cases:
        idx = _parse_case_index(c.case_name)
        if idx is None:
            others.append(c.case_name)
        else:
            indices.append(idx)

    parts: List[str] = []
    for a, b in _compress_case_indices(indices):
        if a == b:
            parts.append(f"case{a}")
        else:
            parts.append(f"case{a}-{b}")
    for s in sorted(set(x for x in others if isinstance(x, str) and x.strip())):
        parts.append(s.strip())

    if not parts:
        return None
    return f"{dataset}/" + ",".join(parts)


def _summarize_cases(cases: List[CaseItem]) -> List[str]:
    by_dataset: dict[str, List[CaseItem]] = {}
    for c in cases:
        by_dataset.setdefault(c.dataset, []).append(c)

    lines: List[str] = []
    for ds in sorted(by_dataset.keys()):
        s = _format_dataset_cases(ds, by_dataset.get(ds) or [])
        if s:
            lines.append(s)
    return lines


def _split_abs_path_parts(abs_path: str) -> Tuple[str, List[str]]:
    p = os.path.abspath(abs_path)
    drive, tail = os.path.splitdrive(os.path.normpath(p))
    parts = [x for x in tail.strip(os.sep).split(os.sep) if x]
    return drive, parts


def _rebuild_abs_path(drive: str, parts: List[str]) -> str:
    if drive:
        return os.path.join(drive + os.sep, *parts)
    return os.path.join(os.sep, *parts)


def _infer_output_mode_and_base(output_path: str) -> Tuple[str, str, str, str]:
    abs_target = os.path.abspath(output_path)
    drive, parts = _split_abs_path_parts(abs_target)

    if "output" in parts:
        i = parts.index("output")
        prefix = parts[:i]
        if i + 2 < len(parts):
            level = parts[i + 1]
            case_name = parts[i + 2]
            case_dir = _rebuild_abs_path(drive, prefix + ["output", level, case_name])
            if os.path.isdir(case_dir):
                return ("case", case_dir, level, case_name)
        if i + 1 < len(parts):
            level = parts[i + 1]
            level_dir = _rebuild_abs_path(drive, prefix + ["output", level])
            if os.path.isdir(level_dir):
                return ("level", level_dir, level, "")
        out_root = _rebuild_abs_path(drive, prefix + ["output"])
        if os.path.isdir(out_root):
            return ("output", out_root, "", "")

    if os.path.isdir(abs_target):
        base = os.path.basename(abs_target)
        parent = os.path.basename(os.path.dirname(abs_target))
        if base.lower().startswith("case"):
            return ("case", abs_target, parent, base)
        if base.upper().startswith("L"):
            return ("level", abs_target, base, "")
        return ("output", abs_target, "", "")

    raise FileNotFoundError(abs_target)


def _discover_output_cases(output_path: str) -> List[CaseItem]:
    mode, base_dir, inferred_level, inferred_case = _infer_output_mode_and_base(output_path)
    items: List[CaseItem] = []

    if mode == "case":
        items.append(CaseItem(dataset=inferred_level or "", case_name=inferred_case or os.path.basename(base_dir), output_case_dir=base_dir))
        return items

    if mode == "level":
        level = inferred_level or os.path.basename(base_dir)
        for case_dirname in sorted(os.listdir(base_dir)):
            case_dir = os.path.join(base_dir, case_dirname)
            if not os.path.isdir(case_dir):
                continue
            items.append(CaseItem(dataset=level, case_name=case_dirname, output_case_dir=case_dir))
        return items

    for level in sorted(os.listdir(base_dir)):
        level_dir = os.path.join(base_dir, level)
        if not os.path.isdir(level_dir):
            continue
        for case_dirname in sorted(os.listdir(level_dir)):
            case_dir = os.path.join(level_dir, case_dirname)
            if not os.path.isdir(case_dir):
                continue
            items.append(CaseItem(dataset=level, case_name=case_dirname, output_case_dir=case_dir))
    return items


def _parse_case_spec(case_spec: str) -> List[str]:
    s = (case_spec or "").strip()
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[,\s]+", s) if p and p.strip()]
    return parts


def _infer_input_case_dir_for_case(input_case_dir_arg: Optional[str], dataset: str, case_name: str) -> Optional[str]:
    if not input_case_dir_arg:
        return None
    base = os.path.abspath(str(input_case_dir_arg))
    if os.path.basename(base).lower().startswith("case"):
        return base
    if os.path.basename(base) == dataset:
        cand = os.path.join(base, case_name)
        return cand if os.path.isdir(cand) else base
    if os.path.basename(base) == "input":
        cand = os.path.join(base, dataset, case_name)
        return cand if os.path.isdir(cand) else base
    cand = os.path.join(base, dataset, case_name) if dataset else os.path.join(base, case_name)
    return cand if os.path.isdir(cand) else base


def main(argv: Optional[list[str]] = None) -> int:
    start_time = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output_case_dir",
        nargs="?",
        default=None,
        help="要评估的 output 目录（例如 output/L5/case1；也支持 output/L5 或 output）",
    )
    parser.add_argument("--all_levels", action="store_true", help="一键评估 output 下的 L1~L5 全部 case")
    parser.add_argument("--cases", default="", help="选择 case（例如 1-100 或 1,3,5-9 或 case2-case6）")
    parser.add_argument("--input_case_dir", default=None, help="可选：指定 input 目录")
    parser.add_argument("--evaluation_dir", default=None, help="可选：指定 evaluation 输出目录")
    parser.add_argument("--intent_threshold", type=float, default=1, help="意图覆盖阈值")
    parser.add_argument("--rule_threshold", type=float, default=0.95, help="规则相似阈值")
    parser.add_argument("--dry_run", action="store_true", help="仅打印要评估的case，不执行")
    parser.add_argument("--workers", type=int, default=0, help="并行度，0表示自动")
    parser.add_argument("--only_methods", default="", help="仅评估这些方法目录（逗号或空格分隔），例如 SingleLLM")
    args = parser.parse_args(argv)

    only_methods: list[str] = []
    if args.only_methods:
        tokens = [t.strip() for t in re.split(r"[,\s]+", str(args.only_methods)) if t and t.strip()]
        only_methods = tokens

    target_output_dir: Optional[str] = None
    if args.all_levels:
        target_output_dir = os.path.join(PROJECT_ROOT, "output")
    else:
        if not args.output_case_dir:
            parser.error("缺少 output_case_dir 参数，或使用 --all_levels 进行一键评估")
            return 2
        target_output_dir = str(args.output_case_dir)

    all_cases = _discover_output_cases(str(target_output_dir))
    if args.all_levels:
        allowed = {"L1", "L2", "L3", "L4", "L5"}
        all_cases = [c for c in all_cases if c.dataset.upper() in allowed]
    tokens = _parse_case_spec(str(args.cases))

    selected = all_cases
    if tokens:
        selected = [c for c in all_cases if _match_tokens(tokens, c.case_name)]

    planned_lines = _summarize_cases(selected)
    print(f"共发现 {len(all_cases)} 个case，已选择 {len(selected)} 个case")
    if planned_lines:
        print("将评估:")
        for s in planned_lines:
            print(f"- {s}")

    if args.dry_run:
        return 0

    if not selected:
        return 0

    cpu = os.cpu_count() or 1
    workers = int(args.workers) if int(args.workers) > 0 else max(1, min(cpu, len(selected)))

    ok: List[Tuple[CaseItem, List[str]]] = []
    failed: List[Tuple[CaseItem, str]] = []

    def _eval_one(c: CaseItem) -> List[str]:
        input_case_dir = _infer_input_case_dir_for_case(args.input_case_dir, c.dataset, c.case_name)
        evaluation_dir: Optional[str] = None
        if args.evaluation_dir:
            base_eval = os.path.abspath(str(args.evaluation_dir))
            evaluation_dir = (
                base_eval
                if len(selected) == 1
                else os.path.join(base_eval, c.dataset or "_", c.case_name)
            )
        return evaluate_output_case_dir_detail(
            output_case_dir=str(c.output_case_dir),
            input_case_dir=str(input_case_dir) if input_case_dir else None,
            evaluation_dir=str(evaluation_dir) if evaluation_dir else None,
            intent_threshold=float(args.intent_threshold),
            rule_threshold=float(args.rule_threshold),
            only_methods=only_methods if only_methods else None,
        )

    if workers <= 1:
        for c in selected:
            try:
                out_paths = _eval_one(c)
                ok.append((c, out_paths))
            except Exception as e:
                failed.append((c, str(e)))
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            fut_to_case: dict[concurrent.futures.Future[List[str]], CaseItem] = {}
            for c in selected:
                fut_to_case[ex.submit(_eval_one, c)] = c
            for fut in concurrent.futures.as_completed(fut_to_case):
                c = fut_to_case[fut]
                try:
                    out_paths = fut.result()
                    ok.append((c, out_paths))
                except Exception as e:
                    failed.append((c, str(e)))

    for _, out_paths in ok:
        for p in out_paths:
            print(p)

    elapsed_time = time.time() - start_time
    total_count = len(selected)
    success_count = len(ok)
    failed_count = len(failed)
    success_rate = (success_count / total_count * 100.0) if total_count else 0.0
    print("\n================ 统计信息 ================")
    print(f"总耗时: {elapsed_time:.2f} 秒")
    print(f"并行度: {workers}")
    print(f"总Case数: {total_count}")
    print(f"成功: {success_count}  失败: {failed_count}  成功率: {success_rate:.1f}%")
    ok_lines = _summarize_cases([c for c, _ in ok])
    failed_lines = _summarize_cases([c for c, _ in failed])
    if ok_lines:
        print("成功Case:")
        for s in ok_lines:
            print(f"- {s}")
    if failed_lines:
        print("失败Case:")
        for s in failed_lines:
            print(f"- {s}")
    print("评估已完成")
    print("==========================================")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
