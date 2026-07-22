from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from src.utils.config import load_yaml
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.runner import RunOrchestrator


ROOT = Path(__file__).resolve().parents[2]
catalog = RunCatalog(ROOT)
events = EventStore(ROOT / "outputs/workbench.sqlite3")
orchestrator = RunOrchestrator(ROOT, catalog, events)


class AgentOverride(BaseModel):
    model: str | None = None
    reasoning_effort: str | None = None


class CreateRunRequest(BaseModel):
    source: str = "library"
    case_id: str
    raw_text: str | None = None
    max_concurrency: int = Field(default=6, ge=1, le=16)
    agents: dict[str, AgentOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source not in {"library", "paste"}:
            raise ValueError("source must be library or paste")
        if self.source == "paste" and not (self.raw_text or "").strip():
            raise ValueError("raw_text is required when source=paste")
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
    agents = ["semantic_graphing", *SPECIALTIES]
    values = []
    for agent_id in agents:
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
