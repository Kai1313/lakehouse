from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    duckdb: bool


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str
    configured: bool
    supports_tools: bool = True


class ModelsResponse(BaseModel):
    models: list[ModelOption]
    default_model_id: str | None = None


class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    model_id: str | None = None


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    model_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageRecord(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    model_id: str | None = None
    citations_json: dict[str, Any] | list[Any] = Field(default_factory=dict)
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageRecord] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
