import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from app import db, storage
from app.config import settings
from app.models_registry import (
    FileChunksResponse,
    FileRecord,
    HealthResponse,
    SilverTableInfo,
    TablePreviewResponse,
    UploadItemResult,
    UploadResponse,
)
from app.parsers.excel_parser import read_delta_preview
from app.workers.ingest_pipeline import process_file, purge_file_silver_gold

EXCEL_EXTENSIONS = {".xlsx", ".xls"}
PDF_EXTENSIONS = {".pdf"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage.ensure_bucket_exists()
    yield


app = FastAPI(
    title="Lakehouse Data Management Engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in EXCEL_EXTENSIONS:
        return "excel"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported file extension for '{filename}'. Allowed: .xlsx, .xls, .pdf",
    )


def _content_type_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".xls":
        return "application/vnd.ms-excel"
    return "application/octet-stream"


@app.get("/health", response_model=HealthResponse)
def health_check():
    pg_ok = db.ping_db()
    minio_ok = storage.ping_storage()
    return HealthResponse(
        status="ok" if (pg_ok and minio_ok) else "degraded",
        postgres=pg_ok,
        minio=minio_ok,
    )


@app.post("/files/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    results: list[UploadItemResult] = []

    for upload in files:
        filename = Path(upload.filename or "unnamed").name
        file_type = _detect_file_type(filename)
        raw_bytes = await upload.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail=f"File '{filename}' is empty.")

        file_hash = hashlib.sha256(raw_bytes).hexdigest()

        # 1. Exact SHA-256 deduplication against already-ready files
        existing_hash = db.get_ready_file_by_hash(file_hash)
        if existing_hash:
            results.append(
                UploadItemResult(
                    file_id=existing_hash["id"],
                    filename=existing_hash["filename"],
                    file_type=existing_hash["file_type"],
                    file_hash=existing_hash["file_hash"],
                    status=existing_hash["status"],
                    action="skipped_duplicate",
                    bronze_uri=existing_hash["bronze_uri"],
                )
            )
            continue

        # 2. If same filename already exists with a different hash, purge & replace cleanly
        action = "queued"
        prior_same_name = db.get_files_by_filename(filename)
        if prior_same_name:
            action = "replaced"
            for old in prior_same_name:
                purge_file_silver_gold(old["id"])
                storage.delete_prefix(f"bronze/{old['id']}/")
                db.delete_file_record(old["id"])

        file_id = uuid4()
        bronze_key = f"bronze/{file_id}/{filename}"
        bronze_uri = storage.upload_bytes(
            bronze_key,
            raw_bytes,
            content_type=_content_type_for(filename),
        )

        record = db.create_file_record(
            file_id=file_id,
            filename=filename,
            file_type=file_type,
            file_hash=file_hash,
            file_size_bytes=len(raw_bytes),
            bronze_uri=bronze_uri,
            status="bronze",
        )

        background_tasks.add_task(process_file, file_id)

        results.append(
            UploadItemResult(
                file_id=record["id"],
                filename=record["filename"],
                file_type=record["file_type"],
                file_hash=record["file_hash"],
                status=record["status"],
                action=action,
                bronze_uri=record["bronze_uri"],
            )
        )

    return UploadResponse(items=results)


@app.get("/files", response_model=list[FileRecord])
def get_files():
    return db.list_files()


@app.get("/files/{file_id}", response_model=FileRecord)
def get_file(file_id: UUID):
    row = db.get_file_by_id(file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")
    tables = db.get_silver_tables_for_file(file_id)
    row["silver_tables"] = [t["table_name"] for t in tables]
    return row


@app.post("/files/{file_id}/reingest", response_model=FileRecord, status_code=status.HTTP_202_ACCEPTED)
def reingest_file(file_id: UUID, background_tasks: BackgroundTasks):
    row = db.get_file_by_id(file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")
    updated = db.update_file_status(file_id, "bronze", error_message=None)
    background_tasks.add_task(process_file, file_id)
    tables = db.get_silver_tables_for_file(file_id)
    updated["silver_tables"] = [t["table_name"] for t in tables]
    return updated


@app.delete("/files/{file_id}")
def delete_file(file_id: UUID):
    row = db.get_file_by_id(file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")

    purge_file_silver_gold(file_id)
    storage.delete_prefix(f"bronze/{file_id}/")
    db.delete_file_record(file_id)
    return {"deleted": True, "file_id": str(file_id), "filename": row["filename"]}


@app.get("/tables", response_model=list[SilverTableInfo])
def get_tables():
    return db.list_silver_tables()


@app.get("/tables/{table_name}/preview", response_model=TablePreviewResponse)
def preview_table(table_name: str, limit: int = Query(default=20, ge=1, le=200)):
    table_info = db.get_silver_table_by_name(table_name)
    if not table_info:
        raise HTTPException(status_code=404, detail=f"Silver table '{table_name}' not found.")

    rows = read_delta_preview(table_info["delta_uri"], limit=limit)
    return TablePreviewResponse(
        table_name=table_info["table_name"],
        row_count=table_info["row_count"],
        columns=table_info["columns_json"],
        rows=rows,
    )


@app.get("/files/{file_id}/chunks", response_model=FileChunksResponse)
def get_file_chunks(
    file_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    page: int | None = Query(default=None, ge=1),
):
    row = db.get_file_by_id(file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")
    total, chunks = db.get_document_chunks(file_id, limit=limit, page=page)
    return FileChunksResponse(
        file_id=row["id"],
        filename=row["filename"],
        total_chunks=total,
        chunks=chunks,
    )
