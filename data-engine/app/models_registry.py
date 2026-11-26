from datetime import datetime
from typing import Any, Literal
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
    review_status: str = "needs_review"
    document_note: str = ""
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
    index: int | None = None
    name: str
    original_name: str
    type: str
    keep: bool = True


class SilverTableInfo(BaseModel):
    id: UUID
    source_file_id: UUID
    table_name: str
    original_filename: str
    sheet_name: str
    delta_uri: str
    row_count: int
    review_status: str = "needs_review"
    header_rows_json: list[int] = Field(default_factory=list)
    mapping_override_json: dict[str, Any] = Field(default_factory=dict)
    columns_json: list[SilverColumnInfo]
    sample_rows_json: list[dict[str, Any]]
    created_at: datetime


class TablePreviewResponse(BaseModel):
    table_name: str
    row_count: int
    review_status: str = "needs_review"
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


class RawSheetPreview(BaseModel):
    sheet_name: str
    total_raw_rows: int
    rows: list[list[Any]]
    detected_header_rows: list[int]
    table_name: str | None = None
    review_status: str = "needs_review"
    columns: list[SilverColumnInfo] = Field(default_factory=list)


class FileRawPreviewResponse(BaseModel):
    file_id: UUID
    filename: str
    file_type: str
    review_status: str
    document_note: str = ""
    sheets: list[RawSheetPreview] = Field(default_factory=list)


class ColumnMappingOverride(BaseModel):
    index: int = Field(ge=0)
    name: str = Field(min_length=1)
    type: Literal["VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN"] = "VARCHAR"
    keep: bool = True


class SheetMappingOverride(BaseModel):
    sheet_name: str
    header_rows: list[int] = Field(min_length=1)
    columns: list[ColumnMappingOverride] = Field(default_factory=list)


class ConfirmSilverRequest(BaseModel):
    document_note: str | None = None
    sheet_overrides: list[SheetMappingOverride] = Field(default_factory=list)
