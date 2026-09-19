from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generator
import json
import logging
import threading
import time
import uuid

from main import run_case


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")


@dataclass
class RunRecord:
    run_id: str
    case_file: str
    case_label: str
    out_file: str
    status: str = "pending"
    started_at: str = field(default_factory=_utc_now)
    ended_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    latest_tree: dict[str, Any] | None = None
    latest_check_result: dict[str, Any] | None = None
    agent_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary_lines: list[str] = field(default_factory=list)
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    thread: threading.Thread | None = None


class RunEventReporter:
    def __init__(self, manager: "RunManager", run_id: str):
        self.manager = manager
        self.run_id = run_id
        self._last_node_names: set[str] = set()

    def emit(
        self,
        *,
        event_type: str,
        title: str,
        payload: dict[str, Any],
        agent: str | None = None,
        level: str = "info",
    ) -> None:
        self.manager.append_event(
            self.run_id,
            {
                "timestamp": _utc_now(),
                "event_type": event_type,
                "title": title,
                "payload": payload,
                "agent": agent,
                "level": level,
            },
        )

    def emit_tree_snapshot(self, tree_payload: dict[str, Any], *, source: str = "", topic: str = "") -> None:
        nodes = list(tree_payload.get("nodes") or [])
        node_names = {
            str(item.get("name") or "")
            for item in nodes
            if isinstance(item, dict) and str(item.get("name") or "")
        }
        added_node_names = sorted(node_names - self._last_node_names)
        self._last_node_names = node_names
        type_counts: dict[str, int] = {}
        for item in nodes:
            node_type = str(item.get("type") or "UNKNOWN")
            type_counts[node_type] = type_counts.get(node_type, 0) + 1
        self.emit(
            event_type="tree.snapshot",
            title="追溯树已更新",
            payload={
                "source": source,
                "topic": topic,
                "tree": tree_payload,
                "summary": {
                    "node_count": len(nodes),
                    "edge_count": len(list(tree_payload.get("edges") or [])),
                    "added_node_names": added_node_names,
                    "type_counts": type_counts,
                },
            },
            agent=source or "TraceTreeBus",
        )

    def build_run_log_handler(self) -> logging.Handler:
        reporter = self

        class RunSummaryHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    logger_name = str(record.name or "")
                    agent = None
                    if logger_name.startswith("intent2sys.case.run."):
                        agent = logger_name.split(".")[-1]
                    reporter.emit(
                        event_type="log.summary",
                        title=record.getMessage(),
                        payload={
                            "logger_name": logger_name,
                            "level_name": str(record.levelname or "INFO"),
                        },
                        agent=agent,
                        level=str(record.levelname or "INFO").lower(),
                    )
                except Exception:
                    return

        return RunSummaryHandler(level=logging.INFO)


class RunManager:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.RLock()

    def list_cases(self) -> list[dict[str, str]]:
        excluded_parts = {"output", "webui", "__pycache__", "node_modules", ".git", ".venv", "venv"}
        results: list[dict[str, str]] = []
        for path in self.project_root.rglob("case_input.json"):
            rel_path = path.relative_to(self.project_root)
            if any(part in excluded_parts for part in rel_path.parts):
                continue
            results.append(
                {
                    "id": _normalize_path(rel_path),
                    "label": _normalize_path(rel_path.parent),
                    "path": _normalize_path(rel_path),
                }
            )
        results.sort(key=lambda item: item["path"])
        return results

    def _resolve_case_file(self, case_path: str) -> Path:
        if not case_path:
            raise ValueError("case_path is required")
        candidate = (self.project_root / case_path).resolve()
        if self.project_root not in candidate.parents and candidate != self.project_root:
            raise ValueError("case_path is outside project root")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Case file not found: {case_path}")
        return candidate

    def _build_output_path(self, case_file: Path, run_id: str) -> Path:
        case_name = case_file.parent.name or case_file.stem
        out_dir = self.project_root / "output" / "webui" / case_name / run_id[:8]
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / "case_result.json"

    def start_run(
        self,
        *,
        case_path: str,
        debug: bool = True,
    ) -> RunRecord:
        case_file = self._resolve_case_file(case_path)
        with self._lock:
            for run in self._runs.values():
                if run.status in {"pending", "running"}:
                    raise RuntimeError("已有运行中的 case，请等待当前运行结束后再启动下一次。")
            run_id = uuid.uuid4().hex
            out_file = self._build_output_path(case_file, run_id)
            record = RunRecord(
                run_id=run_id,
                case_file=_normalize_path(case_file.relative_to(self.project_root)),
                case_label=_normalize_path(case_file.parent.relative_to(self.project_root)),
                out_file=_normalize_path(out_file.relative_to(self.project_root)),
                status="running",
            )
            self._runs[run_id] = record

        thread = threading.Thread(
            target=self._run_case_thread,
            args=(record, debug),
            daemon=True,
            name=f"WebUIRun-{run_id[:8]}",
        )
        record.thread = thread
        thread.start()
        return record

    def _run_case_thread(self, record: RunRecord, debug: bool) -> None:
        reporter = RunEventReporter(self, record.run_id)
        try:
            result = run_case(
                str(self.project_root / record.case_file),
                str(self.project_root / record.out_file),
                debug=debug,
                reporter=reporter,
                redirect_stdio=False,
            )
            with record.condition:
                record.status = "finished"
                record.ended_at = _utc_now()
                record.elapsed_seconds = float(result.get("elapsed_seconds", 0.0) or 0.0)
                record.total_tokens = int(result.get("total_tokens", 0) or 0)
                record.summary_lines = list(result.get("summary_lines") or [])
                record.condition.notify_all()
        except Exception as exc:
            self.append_event(
                record.run_id,
                {
                    "timestamp": _utc_now(),
                    "event_type": "run.failed",
                    "title": "运行线程异常退出",
                    "payload": {"error": str(exc)},
                    "agent": None,
                    "level": "error",
                },
            )
            with record.condition:
                record.status = "failed"
                record.ended_at = _utc_now()
                record.condition.notify_all()

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        record = self.get_run(run_id)
        with record.condition:
            seq = len(record.events) + 1
            event = dict(event)
            event["seq"] = seq
            event["run_id"] = run_id
            record.events.append(event)

            event_type = str(event.get("event_type") or "")
            payload = event.get("payload") or {}
            agent = event.get("agent")
            timestamp = str(event.get("timestamp") or _utc_now())

            if event_type == "run.started":
                record.status = "running"
                record.started_at = timestamp
            elif event_type == "run.failed":
                record.status = "failed"
                record.ended_at = timestamp
            elif event_type == "run.finished":
                if record.status != "failed":
                    record.status = "finished"
                record.ended_at = timestamp
                record.summary_lines = list(payload.get("summary_lines") or record.summary_lines)
                record.total_tokens = int(payload.get("total_tokens", record.total_tokens) or 0)
                record.elapsed_seconds = float(payload.get("elapsed_seconds", record.elapsed_seconds) or 0.0)
            elif event_type == "tree.snapshot":
                record.latest_tree = payload.get("tree")
            elif event_type == "check.result":
                record.latest_check_result = payload.get("result")

            if isinstance(agent, str) and agent:
                agent_state = dict(record.agent_states.get(agent) or {})
                if event_type == "agent.started":
                    agent_state["status"] = "running"
                    agent_state["started_at"] = timestamp
                    agent_state.setdefault("first_started_at", timestamp)
                elif event_type == "agent.finished":
                    agent_state["status"] = "finished"
                    agent_state["ended_at"] = timestamp
                elif event_type == "agent.failed":
                    agent_state["status"] = "failed"
                    agent_state["ended_at"] = timestamp
                agent_state["title"] = str(event.get("title") or agent_state.get("title") or "")
                agent_state["last_event_type"] = event_type
                agent_state["last_payload"] = payload
                agent_state["last_timestamp"] = timestamp
                record.agent_states[agent] = agent_state

            record.condition.notify_all()

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    def get_run_state(self, run_id: str) -> dict[str, Any]:
        record = self.get_run(run_id)
        with record.condition:
            return {
                "run_id": record.run_id,
                "case_file": record.case_file,
                "case_label": record.case_label,
                "out_file": record.out_file,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "elapsed_seconds": record.elapsed_seconds,
                "total_tokens": record.total_tokens,
                "summary_lines": list(record.summary_lines),
                "agent_states": dict(record.agent_states),
                "latest_tree": record.latest_tree,
                "latest_check_result": record.latest_check_result,
                "event_count": len(record.events),
            }

    def stream_events(self, run_id: str, last_seq: int = 0) -> Generator[dict[str, Any], None, None]:
        record = self.get_run(run_id)
        cursor = max(0, int(last_seq or 0))
        while True:
            with record.condition:
                pending = [event for event in record.events if int(event.get("seq", 0) or 0) > cursor]
                if pending:
                    for event in pending:
                        cursor = int(event.get("seq", cursor) or cursor)
                        yield event
                    continue
                if record.status in {"finished", "failed"}:
                    break
                record.condition.wait(timeout=1.0)

    def stream_events_as_sse(self, run_id: str, last_seq: int = 0) -> Generator[str, None, None]:
        yield "event: hello\ndata: " + json.dumps({"run_id": run_id, "ts": _utc_now()}, ensure_ascii=False) + "\n\n"
        for event in self.stream_events(run_id, last_seq=last_seq):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        yield "event: done\ndata: " + json.dumps({"run_id": run_id, "ts": _utc_now()}, ensure_ascii=False) + "\n\n"

