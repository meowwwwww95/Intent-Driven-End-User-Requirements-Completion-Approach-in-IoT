from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webui.run_manager import RunManager


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "webui" / "static"

app = FastAPI(title="BRT Demo UI", version="0.1.0")
manager = RunManager(str(PROJECT_ROOT))


class StartRunRequest(BaseModel):
    case_path: str
    debug: bool = True


@app.get("/api/cases")
def list_cases() -> dict[str, Any]:
    return {"cases": manager.list_cases()}


@app.post("/api/runs")
def start_run(payload: StartRunRequest) -> dict[str, Any]:
    try:
        record = manager.start_run(
            case_path=payload.case_path,
            debug=payload.debug,
        )
        return manager.get_run_state(record.run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return manager.get_run_state(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc


@app.get("/api/runs/{run_id}/events")
def stream_run_events(run_id: str, last_seq: int = 0) -> StreamingResponse:
    try:
        manager.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
    return StreamingResponse(
        manager.stream_events_as_sse(run_id, last_seq=last_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

