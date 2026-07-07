from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .core.agent import AgentKernel
from .core.config import get_settings
from .core.model import create_model_adapter
from .core.schemas import ChatRequest, ChatResponse, SessionSummary

load_dotenv(override=True)

settings = get_settings()
kernel = AgentKernel(settings=settings, model=create_model_adapter(settings))

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.model_id, "workspace": str(settings.workspace_dir)}


@app.get("/api/sessions")
async def list_sessions() -> list[SessionSummary]:
    return [
        SessionSummary(session_id=session.session_id, message_count=len(session.messages), todos=session.todos)
        for session in kernel.list_sessions()
    ]


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

