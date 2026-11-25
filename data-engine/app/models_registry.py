from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    minio: bool


class FileRecord(BaseModel):
    id: UUID
    filename: str
    file_type: str
    file_hash: str
    file_size_bytes: int
    bronze_uri: str
    status: str
    error_message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    silver_tables: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UploadItemResult(BaseModel):
    file_id: UUID
    filename: str
    file_type: str
    file_hash: str
    status: str
    action: str  # 'queued' | 'replaced' | 'skipped_duplicate'
    bronze_uri: str


class UploadResponse(BaseModel):
    items: list[UploadItemResult]


class SilverColumnInfo(BaseModel):
    name: str
    original_name: str
    type: str


class SilverTableInfo(BaseModel):
    id: UUID
    source_file_id: UUID
    table_name: str
    original_filename: str
    sheet_name: str
    delta_uri: str
    row_count: int
    columns_json: list[SilverColumnInfo]
    sample_rows_json: list[dict[str, Any]]
    created_at: datetime


class TablePreviewResponse(BaseModel):
    table_name: str
    row_count: int
    columns: list[SilverColumnInfo]
    rows: list[dict[str, Any]]


class DocumentChunkInfo(BaseModel):
    id: UUID
    source_file_id: UUID
    filename: str
    page_number: int | None = None
    chunk_index: int
    content: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class FileChunksResponse(BaseModel):
    file_id: UUID
    filename: str
    total_chunks: int
    chunks: list[DocumentChunkInfo]
