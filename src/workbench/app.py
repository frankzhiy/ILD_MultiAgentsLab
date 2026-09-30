from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from src.utils.config import load_yaml
from src.workbench.catalog import RunCatalog
from src.workbench.events import EventStore
from src.workbench.presentation import present
from src.workbench.runner import AGENTS, RunOrchestrator


ROOT = Path(__file__).resolve().parents[2]
catalog = RunCatalog(ROOT)
events = EventStore(ROOT / "outputs/workbench.sqlite3")
orchestrator = RunOrchestrator(ROOT, catalog, events)


class AgentOverride(BaseModel):
    model: str | None = None
    reasoning_effort: str | None = None

    @field_validator("model")
    @classmethod
    def nonempty_model(cls, value):
        if value is not None and not value.strip():
            raise ValueError("模型名称不能为空。")
        return value.strip() if value is not None else None


class CreateRunRequest(BaseModel):
    source: str = "library"
    case_id: str
    raw_text: str | None = None
    parent_run_id: str | None = None
    max_concurrency: int = Field(default=6, ge=1, le=16)
    agents: dict[str, AgentOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source not in {"library", "paste"}:
            raise ValueError("source must be library or paste")
        if self.source == "paste" and not (self.raw_text or "").strip():
            raise ValueError("raw_text is required when source=paste")
        return self


class CreateBatchRequest(BaseModel):
    kind: Literal["run", "discussion"]
    source: str = "library"
    case_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=6, ge=1, le=16)
    max_case_concurrency: int = Field(default=2, ge=1, le=16)
    max_request_concurrency: int = Field(default=6, ge=1, le=64)
    agents: dict[str, AgentOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_batch(self):
        if self.kind == "run":
            if self.source != "library":
                raise ValueError("批量完整运行目前仅支持病例库输入。")
            if not self.case_ids:
                raise ValueError("请至少选择一个病例。")
        elif not self.run_ids:
            raise ValueError("请至少选择一个运行。")
        return self

app = FastAPI(title="ILD Multi-Agent Research Workbench", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def not_found(error: FileNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def discussion_result(run_id: str, *, accepted: bool = False) -> dict:
    result = catalog.discussion(run_id)
    if accepted or orchestrator.discussion_running(run_id):
        stop = orchestrator.workflow.stop_events.get(run_id)
        if stop is not None and stop.is_set():
            result["status"] = "stopping"
            result["error"] = None
        elif accepted or catalog.run_summary(catalog.run_dir(run_id))["status"] == "running":
            result["status"] = "running"
            result["error"] = None
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cases")
def list_cases() -> list[dict]:
    return catalog.list_cases()


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, str]:
    try:
        return {"case_id": case_id, "text": catalog.case_text(case_id)}
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/models")
def models() -> dict:
    values = []
    for agent_id in AGENTS:
        path = ROOT / "configs/agents" / agent_id / "agent.yaml"
        config = load_yaml(path)
        values.append(
            {
                "agent_id": agent_id,
                "provider": config.get("provider"),
                "model": config.get("model"),
                "reasoning_effort": config.get("request_options", {}).get(
                    "reasoning_effort", "none"
                ),
                "supports_json_schema": config.get("supports_json_schema", False),
                "max_tokens": config.get("max_tokens"),
            }
        )
    return {"agents": values}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return catalog.list_runs()


@app.post("/api/runs", status_code=202)
async def create_run(request: CreateRunRequest) -> dict:
    try:
        run_id, input_path = orchestrator.prepare(request.model_dump(exclude_none=True))
        orchestrator.start(run_id, input_path)
        return catalog.run_summary(catalog.run_dir(run_id))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/batches")
def list_batches() -> list[dict]:
    return orchestrator.list_batches()


@app.post("/api/batches", status_code=202)
async def create_batch(request: CreateBatchRequest) -> dict:
    try:
        return orchestrator.create_batch(request.model_dump(exclude_none=True))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    try:
        return orchestrator.batch(batch_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/batches/{batch_id}/retry", status_code=202)
async def retry_batch(batch_id: str) -> dict:
    try:
        return orchestrator.retry_batch(batch_id)
    except FileNotFoundError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/batches/{batch_id}/export")
def export_batch(batch_id: str) -> Response:
    batch = get_batch(batch_id)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["batch_id", "case_id", "run_id", "status", "stage", "round_number",
                     "elapsed_seconds", "error", "case_version_id", "agents", "final_report"])
    for item in batch["items"]:
        progress = item["progress"]
        row = [batch_id, item["case_id"], item["run_id"], item["status"], progress["stage"],
               progress["round_number"], item["elapsed_seconds"], item.get("error"),
               item.get("case_version_id"), json.dumps(batch.get("agents", {}), ensure_ascii=False),
               f"/api/runs/{quote(item['run_id'], safe='')}/artifacts/{quote(item['case_id'], safe='')}_mdt_final_report.json"
               if item["status"] == "completed" else ""]
        # Spreadsheet exports must keep model/error text from becoming formulas.
        writer.writerow(["'" + cell if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@"))
                         else cell for cell in row])
    return Response(content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{batch_id}.csv"'})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return catalog.run_summary(catalog.run_dir(run_id))
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/semantic")
def semantic(run_id: str) -> dict:
    try:
        return catalog.semantic(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/routing")
def routing(run_id: str) -> dict:
    try:
        return catalog.routing(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/specialties")
def specialties(run_id: str) -> dict:
    try:
        return catalog.specialties(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/specialties/presented")
def presented_specialties(run_id: str) -> dict:
    try:
        orchestrator.workflow._load_env()
        return present(ROOT, catalog.run_dir(run_id), "specialties", catalog.specialties(run_id))
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/chair")
def chair(run_id: str) -> dict:
    try:
        result = catalog.chair(run_id)
        if orchestrator.chair_running(run_id):
            result["status"] = "running"
        return result
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/chair/presented")
def presented_chair(run_id: str) -> dict:
    try:
        orchestrator.workflow._load_env()
        result = catalog.chair(run_id)
        if orchestrator.chair_running(run_id):
            result["status"] = "running"
        return present(ROOT, catalog.run_dir(run_id), "chair", result)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/chair", status_code=202)
async def run_chair(run_id: str) -> dict:
    try:
        orchestrator.start_chair(run_id)
        result = catalog.chair(run_id)
        result["status"] = "running"
        return result
    except FileNotFoundError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/runs/{run_id}/discussion")
def discussion(run_id: str) -> dict:
    try:
        return discussion_result(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/discussion/presented")
def presented_discussion(run_id: str) -> dict:
    try:
        orchestrator.workflow._load_env()
        return present(ROOT, catalog.run_dir(run_id), "discussion", discussion_result(run_id))
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/report/presented")
def presented_report(run_id: str) -> dict:
    try:
        orchestrator.workflow._load_env()
        report = catalog.report(run_id)
        if run_id in orchestrator.active_reports:
            report["status"] = report["report_status"] = "running"
        report["runnable"] = report["runnable"] and not orchestrator._busy(run_id)
        return present(ROOT, catalog.run_dir(run_id), "report", report)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.post("/api/runs/{run_id}/discussion", status_code=202)
async def run_discussion(run_id: str) -> dict:
    try:
        orchestrator.start_discussion(run_id)
        return discussion_result(run_id, accepted=True)
    except FileNotFoundError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/runs/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict:
    try:
        orchestrator.stop_run(run_id)
        return catalog.run_summary(catalog.run_dir(run_id))
    except FileNotFoundError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/runs/{run_id}/report", status_code=202)
async def run_report(run_id: str) -> dict:
    try:
        orchestrator.start_report(run_id)
        return {**catalog.report(run_id), "status": "running", "report_status": "running"}
    except FileNotFoundError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/runs/{run_id}/artifacts")
def artifacts(run_id: str) -> list[dict]:
    try:
        return catalog.artifacts(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/errors")
def run_errors(run_id: str) -> list[dict]:
    try:
        return catalog.errors(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/artifacts/{relative_path:path}")
def artifact(run_id: str, relative_path: str) -> FileResponse:
    try:
        return FileResponse(catalog.artifact_path(run_id, relative_path))
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error


@app.get("/api/runs/{run_id}/events")
def event_history(run_id: str, after: int = 0) -> list[dict]:
    try:
        catalog.run_dir(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error
    return events.list(run_id, after)


@app.get("/api/runs/{run_id}/stream")
def event_stream(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
    try:
        catalog.run_dir(run_id)
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error

    last_event_id = request.headers.get("last-event-id")
    cursor = max(after, int(last_event_id)) if last_event_id and last_event_id.isdigit() else after

    async def generator():
        async for chunk in events.stream(run_id, cursor):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/api/guidelines")
def guidelines() -> list[dict]:
    return catalog.guidelines()


@app.get("/api/guidelines/{filename}")
def guideline(filename: str) -> FileResponse:
    try:
        return FileResponse(catalog.guideline_path(filename), media_type="application/pdf")
    except (FileNotFoundError, ValueError) as error:
        raise not_found(FileNotFoundError(str(error))) from error
