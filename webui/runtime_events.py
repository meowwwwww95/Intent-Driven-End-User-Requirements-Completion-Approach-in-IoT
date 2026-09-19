from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional
import threading


_REPORTER_STATE = threading.local()
_REPORTER_FALLBACK_LOCK = threading.RLock()
_REPORTER_FALLBACK: Any | None = None


def get_active_reporter() -> Any | None:
    reporter = getattr(_REPORTER_STATE, "reporter", None)
    if reporter is not None:
        return reporter
    with _REPORTER_FALLBACK_LOCK:
        return _REPORTER_FALLBACK


@contextmanager
def activate_reporter(reporter: Any | None) -> Iterator[None]:
    previous = get_active_reporter()
    global _REPORTER_FALLBACK
    _REPORTER_STATE.reporter = reporter
    with _REPORTER_FALLBACK_LOCK:
        _REPORTER_FALLBACK = reporter
    try:
        yield
    finally:
        _REPORTER_STATE.reporter = previous
        with _REPORTER_FALLBACK_LOCK:
            _REPORTER_FALLBACK = previous


def emit_event(
    event_type: str,
    title: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    agent: str | None = None,
    level: str = "info",
) -> None:
    reporter = get_active_reporter()
    if reporter is None:
        return
    reporter.emit(
        event_type=event_type,
        title=title,
        payload=payload or {},
        agent=agent,
        level=level,
    )


def emit_agent_status(
    agent_name: str,
    status: str,
    title: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    level: str = "info",
) -> None:
    normalized_status = (status or "").strip() or "progress"
    emit_event(
        f"agent.{normalized_status}",
        title,
        payload=payload,
        agent=agent_name,
        level=level,
    )


def emit_tree_snapshot(tree_payload: dict[str, Any], *, source: str = "", topic: str = "") -> None:
    reporter = get_active_reporter()
    if reporter is None:
        return
    reporter.emit_tree_snapshot(tree_payload, source=source, topic=topic)
