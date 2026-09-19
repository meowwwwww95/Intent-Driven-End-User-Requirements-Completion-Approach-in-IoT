#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


INPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input")
OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_METHOD_DIRNAME = "our_tool"
MAIN_ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

RUN_SPEC: Dict[str, Any] = {
    "L1": ["6", "7", "11-13"],
    "L2": ["6", "7", "11-13"],
    "L3": ["6", "7", "11-13"],
    "L4": ["6", "7", "11-13"],
    "L5": ["6", "7", "11-13"],
}

EXCLUDE_SPEC: Dict[str, Any] = {}


@dataclass(frozen=True)
class CaseItem:
    dataset: str
    case_name: str
    input_json_path: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="intent_reader_batch",
        description="批量遍历输入目录中的 case，并运行需求补全流程生成输出",
    )
    parser.add_argument("--input_root", default=INPUT_ROOT, help="input 根目录")
    parser.add_argument("--output_root", default=OUTPUT_ROOT, help="output 根目录")
    parser.add_argument("--debug", action="store_true", help="启用调试输出")
    parser.add_argument("--dry_run", action="store_true", help="仅打印要跑的case，不执行")
    parser.add_argument("--workers", type=int, default=0, help="并行度，0表示自动")
    return parser.parse_args()


def _as_set(x: Any) -> set[str]:
    if x is None:
        return set()
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return set()
        return {s}
    if isinstance(x, (list, tuple, set)):
        out: set[str] = set()
        for v in x:
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
        return out
    return set()


def _parse_case_index(case_name: str) -> Optional[int]:
    s = (case_name or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"case(\d+)", s, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_range_token(token: str) -> Optional[Tuple[int, int]]:
    t = (token or "").strip()
    if not t:
        return None
    if t.isdigit():
        n = int(t)
        return (n, n)
    if "-" not in t:
        return None
    a, b = t.split("-", 1)
    a = a.strip()
    b = b.strip()
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
    by_dataset: Dict[str, List[CaseItem]] = {}
    for c in cases:
        by_dataset.setdefault(c.dataset, []).append(c)

    lines: List[str] = []
    for ds in sorted(by_dataset.keys()):
        s = _format_dataset_cases(ds, by_dataset.get(ds) or [])
        if s:
            lines.append(s)
    return lines


class _StatusBoard:
    def __init__(self, cases: List[CaseItem]):
        self._cases = list(cases or [])
        self._n = len(self._cases)
        self._lock = threading.Lock()
        self._enabled = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._printed = False
        self._index: Dict[str, int] = {}
        for i, c in enumerate(self._cases):
            self._index[self._key(c)] = i

    @staticmethod
    def _key(c: CaseItem) -> str:
        return f"{c.dataset}/{c.case_name}"

    def start(self) -> None:
        with self._lock:
            if not self._enabled:
                return
            print("运行进度:", flush=True)
            for c in self._cases:
                print(f"{self._key(c)} 等待中", flush=True)
            self._printed = True

    def update(self, c: CaseItem, status: str) -> None:
        key = self._key(c)
        line = f"{key} {status}"
        with self._lock:
            if not self._enabled or not self._printed or self._n <= 0:
                print(line, flush=True)
                return
            idx = self._index.get(key)
            if idx is None:
                print(line, flush=True)
                return

            up = self._n - idx
            sys.stdout.write(f"\x1b[{up}A")
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.write(line)
            sys.stdout.write(f"\x1b[{up}B")
            sys.stdout.write("\r")
            sys.stdout.flush()


def _match_spec_value(spec_value: Any, name: str) -> bool:
    if spec_value is None:
        return False
    if isinstance(spec_value, str):
        v = spec_value.strip()
        if v in ("*", "ALL", "all"):
            return True
        return _match_tokens([v], name)
    if isinstance(spec_value, (list, tuple, set)):
        return _match_tokens(list(_as_set(spec_value)), name)
    if isinstance(spec_value, dict):
        if name in spec_value:
            return True
        keys = [k for k in spec_value.keys() if isinstance(k, str)]
        return _match_tokens(keys, name)
    return False


def _should_run(dataset: str, case_name: str) -> bool:
    if RUN_SPEC is None:
        allow = True
    elif isinstance(RUN_SPEC, dict):
        if dataset not in RUN_SPEC:
            allow = False
        else:
            allow = _match_spec_value(RUN_SPEC.get(dataset), case_name)
    else:
        allow = False

    if not allow:
        return False

    if EXCLUDE_SPEC is None:
        return True
    if isinstance(EXCLUDE_SPEC, dict) and dataset in EXCLUDE_SPEC:
        if _match_spec_value(EXCLUDE_SPEC.get(dataset), case_name):
            return False
    return True


def _discover_cases(input_root: str) -> List[CaseItem]:
    root = os.path.abspath(input_root)
    items: List[CaseItem] = []
    if not os.path.isdir(root):
        return items

    for dataset in sorted(os.listdir(root)):
        dataset_dir = os.path.join(root, dataset)
        if not os.path.isdir(dataset_dir):
            continue

        for case_dirname in sorted(os.listdir(dataset_dir)):
            case_dir = os.path.join(dataset_dir, case_dirname)
            if not os.path.isdir(case_dir):
                continue

            input_json_candidates = sorted(
                p
                for p in (os.path.join(case_dir, n) for n in os.listdir(case_dir))
                if p.endswith("_input.json") and os.path.isfile(p)
            )
            if not input_json_candidates:
                continue

            input_json_path = input_json_candidates[0]
            stem = os.path.basename(input_json_path)[: -len("_input.json")]
            case_name = stem or case_dirname
            if _parse_case_index(case_name) is None and _parse_case_index(case_dirname) is not None:
                case_name = case_dirname

            items.append(CaseItem(dataset=dataset, case_name=case_name, input_json_path=input_json_path))

    return items


def _run_one_case_subprocess(case: CaseItem, output_root: str, debug: bool, experiment_run_id: Optional[str] = None) -> str:
    out_dir = os.path.join(os.path.abspath(output_root), case.dataset, case.case_name, OUTPUT_METHOD_DIRNAME)
    out_path = os.path.join(out_dir, f"{case.case_name}_res.json")
    os.makedirs(out_dir, exist_ok=True)

    cmd = [sys.executable, "-X", "utf8", MAIN_ENTRY, "--file", case.input_json_path, "--out", out_path]
    # RQ3 开销埋点：显式传入 case 标识与实验批次
    cmd += ["--case-id", f"{case.dataset}/{case.case_name}", "--difficulty", case.dataset]
    if experiment_run_id:
        cmd += ["--experiment-run-id", experiment_run_id]
    if debug:
        cmd.append("--debug")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, env=env)

    return out_path


def _merge_overhead_files(
    selected: List[CaseItem],
    failed: List[Tuple[CaseItem, str]],
    output_root: str,
    experiment_run_id: Optional[str],
) -> Optional[str]:
    """RQ3 开销埋点：合并各 case 输出目录的 overhead.jsonl 到 output_root/overhead_merged.jsonl。

    只做原始记录合并，不做派生统计；子进程失败且无 case_run 记录的，
    补一条 final_status=error 的 case_run，保证每个选中 case 都可追踪。
    """
    merged_lines: List[str] = []
    recorded_case_ids: set[str] = set()
    for c in selected:
        overhead_path = os.path.join(
            os.path.abspath(output_root), c.dataset, c.case_name, OUTPUT_METHOD_DIRNAME, "overhead.jsonl"
        )
        if not os.path.isfile(overhead_path):
            continue
        try:
            with open(overhead_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    merged_lines.append(line)
                    if '"record_type": "case_run"' in line:
                        recorded_case_ids.add(f"{c.dataset}/{c.case_name}")
        except Exception as e:
            print(f"合并 overhead 文件失败 {overhead_path}: {e}")

    failed_keys = {f"{c.dataset}/{c.case_name}" for c, _ in failed}
    for c, err in failed:
        case_key = f"{c.dataset}/{c.case_name}"
        if case_key in recorded_case_ids:
            continue
        merged_lines.append(json.dumps({
            "record_type": "case_run",
            "case_id": case_key,
            "difficulty_level": c.dataset,
            "case_start_time": None,
            "case_end_time": None,
            "final_status": "error",
            "error_type": f"subprocess_failed: {err}",
            "experiment_run_id": experiment_run_id,
        }, ensure_ascii=False))

    if not merged_lines and not failed_keys:
        return None
    merged_path = os.path.join(os.path.abspath(output_root), "overhead_merged.jsonl")
    with open(merged_path, "w", encoding="utf-8") as f:
        for line in merged_lines:
            f.write(line + "\n")
    return merged_path


def main() -> int:
    args = _parse_args()
    start_time = time.time()
    # RQ3 开销埋点：本批次实验标识，透传给每个子进程
    experiment_run_id = "batch_" + time.strftime("%Y%m%d_%H%M%S")

    cases = _discover_cases(str(args.input_root))
    selected = [c for c in cases if _should_run(c.dataset, c.case_name)]

    planned_lines = _summarize_cases(selected)
    print(f"共发现 {len(cases)} 个case，已选择 {len(selected)} 个case")
    if planned_lines:
        print("将运行:")
        for s in planned_lines:
            print(f"- {s}")

    if args.dry_run:
        return 0

    ok: List[Tuple[CaseItem, str]] = []
    failed: List[Tuple[CaseItem, str]] = []

    cpu = os.cpu_count() or 1
    workers = int(args.workers) if int(args.workers) > 0 else max(1, min(cpu, len(selected) or 1))

    board = _StatusBoard(selected)
    board.start()

    def _job(c: CaseItem) -> str:
        board.update(c, "正在运行中")
        return _run_one_case_subprocess(
            c,
            output_root=str(args.output_root),
            debug=bool(args.debug),
            experiment_run_id=experiment_run_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        fut_to_case: Dict[concurrent.futures.Future[str], CaseItem] = {}
        for c in selected:
            fut_to_case[ex.submit(_job, c)] = c

        for fut in concurrent.futures.as_completed(fut_to_case):
            c = fut_to_case[fut]
            try:
                out_path = fut.result()
                board.update(c, "已运行完成")
                ok.append((c, out_path))
            except Exception as e:
                board.update(c, f"已运行完成（失败）: {str(e)}")
                failed.append((c, str(e)))

    # RQ3 开销埋点：合并各 case 的 overhead.jsonl
    merged_overhead_path = _merge_overhead_files(selected, failed, str(args.output_root), experiment_run_id)
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
    print("运行已完成")
    print("==========================================")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
