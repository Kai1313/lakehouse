from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import db
from app.config import settings
from app.llm_router import get_model_option, load_models_registry, stream_chat_turn
from app.schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    HealthResponse,
    ModelsResponse,
)
from app.tools.sql_query import ping_duckdb

app = FastAPI(
    title="Lakehouse Dynamic RAG Engine",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health_check():
    pg_ok = db.ping_db()
    duck_ok = ping_duckdb()
    return HealthResponse(
        status="ok" if (pg_ok and duck_ok) else "degraded",
        postgres=pg_ok,
        duckdb=duck_ok,
    )


@app.get("/models", response_model=ModelsResponse)
def get_models():
    models = load_models_registry()
    default_id = next((m.id for m in models if m.configured), None)
    return ModelsResponse(models=models, default_model_id=default_id)


@app.get("/conversations", response_model=list[ConversationSummary])
def list_conversations():
    return db.list_conversations()


@app.post("/conversations", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
def create_conversation(body: ConversationCreate | None = None):
    payload = body or ConversationCreate()
    return db.create_conversation(title=payload.title, model_id=payload.model_id)


@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: UUID):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: UUID):
    deleted = db.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True, "conversation_id": str(conversation_id)}


@app.post("/conversations/{conversation_id}/chat")
async def chat_stream(conversation_id: UUID, body: ChatRequest):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    model_opt = get_model_option(body.model_id)
    if not model_opt:
        raise HTTPException(status_code=400, detail=f"Unknown model_id '{body.model_id}'.")
    if not model_opt.configured:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{body.model_id}' is not configured in .env.",
        )

    return StreamingResponse(
        stream_chat_turn(
            conversation=conv,
            user_message=body.message,
            model_id=body.model_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
