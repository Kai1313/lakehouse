import datetime as dt
import io
import json
from typing import Any
from uuid import UUID, uuid4

import fitz  # PyMuPDF
import pdfplumber
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from app.config import settings


def _silver_documents_uri() -> str:
    return f"s3://{settings.minio_bucket}/silver/documents"


def _gold_chunks_uri() -> str:
    return f"s3://{settings.minio_bucket}/gold/document_chunks"


def _format_table_markdown(table: list[list[Any]]) -> str:
    cleaned_rows: list[list[str]] = []
    for row in table:
        if not row:
            continue
        cells = [str(c).replace("\n", " ").strip() if c is not None else "" for c in row]
        if any(cells):
            cleaned_rows.append(cells)

    if not cleaned_rows:
        return ""

    col_count = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (col_count - len(r)) for r in cleaned_rows]
    header = padded[0]
    sep = ["---"] * col_count
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def delete_pdf_delta_rows(source_file_id: UUID) -> None:
    predicate = f"source_file_id = '{source_file_id}'"
    for uri in (_silver_documents_uri(), _gold_chunks_uri()):
        try:
            dt_table = DeltaTable(uri, storage_options=settings.delta_storage_options)
            dt_table.delete(predicate=predicate)
        except TableNotFoundError:
            continue


def extract_pdf_pages(
    *,
    source_file_id: UUID,
    filename: str,
    raw_bytes: bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extracts page text + markdown tables via PyMuPDF and pdfplumber, flagging scanned pages."""
    fitz_doc = fitz.open(stream=raw_bytes, filetype="pdf")
    plumber_doc = pdfplumber.open(io.BytesIO(raw_bytes))

    pages: list[dict[str, Any]] = []
    scanned_pages: list[int] = []
    tables_found = 0
    ingested_at = dt.datetime.now(dt.timezone.utc).isoformat()

    try:
        num_pages = len(fitz_doc)
        for idx in range(num_pages):
            page_number = idx + 1
            fitz_page = fitz_doc[idx]
            raw_text = (fitz_page.get_text("text") or "").strip()

            md_tables: list[str] = []
            if idx < len(plumber_doc.pages):
                try:
                    extracted_tables = plumber_doc.pages[idx].extract_tables() or []
                    for tbl in extracted_tables:
                        md = _format_table_markdown(tbl)
                        if md:
                            md_tables.append(md)
                            tables_found += 1
                except Exception:
                    # Keep text extraction resilient even if table layout detection fails on a page
                    pass

            has_images = len(fitz_page.get_images(full=True)) > 0
            needs_ocr = len(raw_text) < 30 and has_images
            if needs_ocr:
                scanned_pages.append(page_number)

            combined = raw_text
            if md_tables:
                table_block = "\n\n### Extracted Tables\n\n" + "\n\n".join(md_tables)
                combined = (combined + table_block).strip()

            pages.append(
                {
                    "source_file_id": str(source_file_id),
                    "filename": filename,
                    "page_number": page_number,
                    "text": combined,
                    "has_tables": len(md_tables) > 0,
                    "needs_ocr": needs_ocr,
                    "ingested_at": ingested_at,
                }
            )
    finally:
        fitz_doc.close()
        plumber_doc.close()

    stats = {
        "page_count": len(pages),
        "tables_extracted": tables_found,
        "scanned_pages": scanned_pages,
        "needs_ocr": len(scanned_pages) > 0,
    }
    return pages, stats


def write_silver_documents(source_file_id: UUID, pages: list[dict[str, Any]]) -> None:
    if not pages:
        return

    uri = _silver_documents_uri()
    try:
        dt_table = DeltaTable(uri, storage_options=settings.delta_storage_options)
        dt_table.delete(predicate=f"source_file_id = '{source_file_id}'")
    except TableNotFoundError:
        pass

    pa_table = pa.Table.from_arrays(
        [
            pa.array([p["source_file_id"] for p in pages], type=pa.string()),
            pa.array([p["filename"] for p in pages], type=pa.string()),
            pa.array([p["page_number"] for p in pages], type=pa.int32()),
            pa.array([p["text"] for p in pages], type=pa.string()),
            pa.array([p["has_tables"] for p in pages], type=pa.bool_()),
            pa.array([p["needs_ocr"] for p in pages], type=pa.bool_()),
            pa.array([p["ingested_at"] for p in pages], type=pa.string()),
        ],
        names=["source_file_id", "filename", "page_number", "text", "has_tables", "needs_ocr", "ingested_at"],
    )
    write_deltalake(
        uri,
        pa_table,
        mode="append",
        storage_options=settings.delta_storage_options,
    )


def _split_text_overlapping(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    text_len = len(cleaned)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            # Prefer breaking on paragraph, newline, or space near the end of window
            for sep in ("\n\n", "\n", ". ", " "):
                pos = cleaned.rfind(sep, start + chunk_size // 2, end)
                if pos != -1:
                    end = pos + len(sep)
                    break

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= text_len:
            break
        start = max(end - overlap, start + 1)

    return chunks


def build_and_write_gold_chunks(
    *,
    source_file_id: UUID,
    pages: list[dict[str, Any]],
    chunk_size: int = 900,
    overlap: int = 150,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    global_idx = 0

    for page in pages:
        page_text = page["text"]
        if not page_text.strip():
            continue
        pieces = _split_text_overlapping(page_text, chunk_size=chunk_size, overlap=overlap)
        for piece in pieces:
            chunks.append(
                {
                    "id": str(uuid4()),
                    "source_file_id": str(source_file_id),
                    "filename": page["filename"],
                    "page_number": page["page_number"],
                    "chunk_index": global_idx,
                    "content": piece,
                    "metadata_json": {
                        "has_tables": page["has_tables"],
                        "needs_ocr": page["needs_ocr"],
                    },
                    "ingested_at": page["ingested_at"],
                }
            )
            global_idx += 1

    uri = _gold_chunks_uri()
    try:
        dt_table = DeltaTable(uri, storage_options=settings.delta_storage_options)
        dt_table.delete(predicate=f"source_file_id = '{source_file_id}'")
    except TableNotFoundError:
        pass

    if chunks:
        pa_table = pa.Table.from_arrays(
            [
                pa.array([c["id"] for c in chunks], type=pa.string()),
                pa.array([c["source_file_id"] for c in chunks], type=pa.string()),
                pa.array([c["filename"] for c in chunks], type=pa.string()),
                pa.array([c["page_number"] for c in chunks], type=pa.int32()),
                pa.array([c["chunk_index"] for c in chunks], type=pa.int32()),
                pa.array([c["content"] for c in chunks], type=pa.string()),
                pa.array([json.dumps(c["metadata_json"]) for c in chunks], type=pa.string()),
                pa.array([c["ingested_at"] for c in chunks], type=pa.string()),
            ],
            names=[
                "id",
                "source_file_id",
                "filename",
                "page_number",
                "chunk_index",
                "content",
                "metadata_json",
                "ingested_at",
            ],
        )
        write_deltalake(
            uri,
            pa_table,
            mode="append",
            storage_options=settings.delta_storage_options,
        )

    return chunks
