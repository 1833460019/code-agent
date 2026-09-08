from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .core.agent import AgentKernel
from .core.config import get_settings
from .core.model import create_model_adapter
from .core.schemas import ChatRequest, ChatResponse, SessionSummary

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

settings = get_settings()
kernel = AgentKernel(settings=settings, model=create_model_adapter(settings))

@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    worker = asyncio.create_task(kernel.serve_schedules(stop)) if settings.cron_enabled else None
    try:
        yield
    finally:
        stop.set()
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApprovalResponse(BaseModel):
    approved: bool


@app.post("/api/approvals/{request_id}")
async def resolve_approval(request_id: str, response: ApprovalResponse):
    try:
        kernel.resolve_approval(request_id, response.approved)
    except KeyError as exc:
        raise HTTPException(404, "Approval expired") from exc
    return {"status": "answered"}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.model_id, "workspace": str(settings.workspace_dir)}


@app.get("/api/schedules")
async def list_schedules():
    from repo_agent.scheduler import CronScheduler
    return CronScheduler(kernel.state_dir / "services").list()


@app.get("/api/sessions")
async def list_sessions() -> list[SessionSummary]:
    return [
        SessionSummary(session_id=session.session_id, message_count=len(session.messages), todos=session.todos, title=session.title, updated_at=session.updated_at)
        for session in kernel.list_sessions()
    ]


@app.get("/api/runs")
async def list_runs():
    return kernel.list_runs()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    try:
        return kernel.get_run(run_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, "Run not found") from exc


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> ChatResponse:
    session = kernel.get_session(session_id)
    return ChatResponse(session_id=session.session_id, messages=session.messages)


@app.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    session = kernel.get_session(request.session_id)
    async for _event in kernel.run_turn(session.session_id, request.message):
        pass
    return ChatResponse(session_id=session.session_id, messages=session.messages)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    async def event_stream():
        async for event in kernel.run_turn(request.session_id, request.message):
            yield f"data: {event.model_dump_json()}\n\n"
        yield "event: close\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


