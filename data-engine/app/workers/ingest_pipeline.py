import logging
from uuid import UUID

from app import db, storage
from app.parsers.embedder import embed_texts
from app.parsers.excel_parser import ingest_excel_bytes
from app.parsers.pdf_parser import (
    build_and_write_gold_chunks,
    delete_pdf_delta_rows,
    extract_pdf_pages,
    write_silver_documents,
)

logger = logging.getLogger(__name__)


def purge_file_silver_gold(file_id: UUID) -> None:
    """Deletes Silver/Gold Delta data and pgvector chunks for a source_file_id."""
    silver_tables = db.delete_silver_tables_for_file(file_id)
    for st in silver_tables:
        _, prefix = storage.parse_s3_uri(st["delta_uri"])
        storage.delete_prefix(prefix.rstrip("/") + "/")

    delete_pdf_delta_rows(file_id)
    db.delete_document_chunks_for_file(file_id)


def process_file(file_id: UUID) -> None:
    """Background ingestion pipeline: Bronze -> Silver -> Gold."""
    record = db.get_file_by_id(file_id)
    if not record:
        logger.warning("process_file skipped: file_id=%s not found", file_id)
        return

    try:
        db.update_file_status(file_id, "silver", error_message=None)
        raw_bytes = storage.download_bytes(record["bronze_uri"])
        file_type = record["file_type"]
        filename = record["filename"]

        if file_type == "excel":
            purge_file_silver_gold(file_id)
            presets = db.get_mapping_presets_for_filename(filename)
            review_state = "verified" if presets else "needs_review"

            tables = ingest_excel_bytes(
                source_file_id=file_id,
                filename=filename,
                raw_bytes=raw_bytes,
                sheet_overrides=presets or None,
                review_status=review_state,
            )
            if not tables:
                raise ValueError("No non-empty sheets found in Excel workbook.")

            all_verified = all(t.get("review_status") == "verified" for t in tables)
            metadata = {
                "sheets_count": len(tables),
                "total_rows": sum(t["row_count"] for t in tables),
                "tables": [t["table_name"] for t in tables],
            }
            db.update_file_status(
                file_id,
                "ready",
                error_message=None,
                metadata_json=metadata,
                review_status="verified" if all_verified else "needs_review",
            )

        elif file_type == "pdf":
            purge_file_silver_gold(file_id)
            pages, pdf_stats = extract_pdf_pages(
                source_file_id=file_id,
                filename=filename,
                raw_bytes=raw_bytes,
            )
            if not pages:
                raise ValueError("PDF has 0 pages.")

            write_silver_documents(file_id, pages)
            db.update_file_status(file_id, "gold", error_message=None, metadata_json=pdf_stats)

            chunks = build_and_write_gold_chunks(source_file_id=file_id, pages=pages)
            if chunks:
                embeddings = embed_texts([c["content"] for c in chunks])
                for chunk, vec in zip(chunks, embeddings):
                    chunk["embedding"] = vec
                db.insert_document_chunks(chunks)

            metadata = {
                **pdf_stats,
                "chunk_count": len(chunks),
            }
            db.update_file_status(file_id, "ready", error_message=None, metadata_json=metadata)

        else:
            db.update_file_status(
                file_id,
                "failed",
                error_message=f"Unsupported file type: {file_type}",
            )
    except Exception as exc:
        logger.exception("Ingestion failed for file_id=%s", file_id)
        db.update_file_status(file_id, "failed", error_message=str(exc))
