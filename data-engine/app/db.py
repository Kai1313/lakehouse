from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings


@contextmanager
def get_conn() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def ping_db() -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def get_ready_file_by_hash(file_hash: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM data_engine.file_registry
                WHERE file_hash = %s AND status = 'ready'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (file_hash,),
            )
            return cur.fetchone()


def get_files_by_filename(filename: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM data_engine.file_registry
                WHERE filename = %s
                ORDER BY created_at DESC
                """,
                (filename,),
            )
            return cur.fetchall()


def get_file_by_id(file_id: UUID) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM data_engine.file_registry WHERE id = %s",
                (file_id,),
            )
            return cur.fetchone()


def list_files() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.*,
                       COALESCE(
                           (SELECT array_agg(st.table_name ORDER BY st.table_name)
                            FROM data_engine.silver_tables st
                            WHERE st.source_file_id = f.id),
                           ARRAY[]::text[]
                       ) AS silver_tables
                FROM data_engine.file_registry f
                ORDER BY f.created_at DESC
                """
            )
            return cur.fetchall()


def create_file_record(
    *,
    file_id: UUID,
    filename: str,
    file_type: str,
    file_hash: str,
    file_size_bytes: int,
    bronze_uri: str,
    status: str = "bronze",
    metadata_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_engine.file_registry (
                    id, filename, file_type, file_hash, file_size_bytes,
                    bronze_uri, status, metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    file_id,
                    filename,
                    file_type,
                    file_hash,
                    file_size_bytes,
                    bronze_uri,
                    status,
                    Jsonb(metadata_json or {}),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def update_file_status(
    file_id: UUID,
    status: str,
    *,
    error_message: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if metadata_json is not None:
                cur.execute(
                    """
                    UPDATE data_engine.file_registry
                    SET status = %s,
                        error_message = %s,
                        metadata_json = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (status, error_message, Jsonb(metadata_json), file_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE data_engine.file_registry
                    SET status = %s,
                        error_message = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (status, error_message, file_id),
                )
            row = cur.fetchone()
        conn.commit()
    return row


def delete_file_record(file_id: UUID) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM data_engine.file_registry WHERE id = %s RETURNING *",
                (file_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def upsert_silver_table(
    *,
    source_file_id: UUID,
    table_name: str,
    original_filename: str,
    sheet_name: str,
    delta_uri: str,
    row_count: int,
    columns: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_engine.silver_tables (
                    source_file_id, table_name, original_filename, sheet_name,
                    delta_uri, row_count, columns_json, sample_rows_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (table_name) DO UPDATE SET
                    source_file_id = EXCLUDED.source_file_id,
                    original_filename = EXCLUDED.original_filename,
                    sheet_name = EXCLUDED.sheet_name,
                    delta_uri = EXCLUDED.delta_uri,
                    row_count = EXCLUDED.row_count,
                    columns_json = EXCLUDED.columns_json,
                    sample_rows_json = EXCLUDED.sample_rows_json,
                    created_at = now()
                RETURNING *
                """,
                (
                    source_file_id,
                    table_name,
                    original_filename,
                    sheet_name,
                    delta_uri,
                    row_count,
                    Jsonb(columns),
                    Jsonb(sample_rows),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def list_silver_tables() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM data_engine.silver_tables
                ORDER BY table_name ASC
                """
            )
            return cur.fetchall()


def get_silver_table_by_name(table_name: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM data_engine.silver_tables WHERE table_name = %s",
                (table_name,),
            )
            return cur.fetchone()


def get_silver_tables_for_file(file_id: UUID) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM data_engine.silver_tables WHERE source_file_id = %s",
                (file_id,),
            )
            return cur.fetchall()


def delete_silver_tables_for_file(file_id: UUID) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM data_engine.silver_tables WHERE source_file_id = %s RETURNING *",
                (file_id,),
            )
            rows = cur.fetchall()
        conn.commit()
    return rows


def delete_document_chunks_for_file(file_id: UUID) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag.document_chunks WHERE source_file_id = %s",
                (file_id,),
            )
            count = cur.rowcount
        conn.commit()
    return count


def insert_document_chunks(chunks: list[dict[str, Any]]) -> int:
    if not chunks:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for ch in chunks:
                vec_literal = "[" + ",".join(str(float(x)) for x in ch["embedding"]) + "]"
                cur.execute(
                    """
                    INSERT INTO rag.document_chunks (
                        id, source_file_id, filename, page_number,
                        chunk_index, content, metadata_json, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (
                        ch["id"],
                        ch["source_file_id"],
                        ch["filename"],
                        ch["page_number"],
                        ch["chunk_index"],
                        ch["content"],
                        Jsonb(ch.get("metadata_json") or {}),
                        vec_literal,
                    ),
                )
        conn.commit()
    return len(chunks)


def get_document_chunks(
    file_id: UUID,
    limit: int = 100,
    page: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if page is not None:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM rag.document_chunks WHERE source_file_id = %s AND page_number = %s",
                    (file_id, page),
                )
                total = cur.fetchone()["cnt"]
                cur.execute(
                    """
                    SELECT id, source_file_id, filename, page_number, chunk_index,
                           content, metadata_json, created_at
                    FROM rag.document_chunks
                    WHERE source_file_id = %s AND page_number = %s
                    ORDER BY page_number ASC NULLS LAST, chunk_index ASC
                    LIMIT %s
                    """,
                    (file_id, page, limit),
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM rag.document_chunks WHERE source_file_id = %s",
                    (file_id,),
                )
                total = cur.fetchone()["cnt"]
                cur.execute(
                    """
                    SELECT id, source_file_id, filename, page_number, chunk_index,
                           content, metadata_json, created_at
                    FROM rag.document_chunks
                    WHERE source_file_id = %s
                    ORDER BY page_number ASC NULLS LAST, chunk_index ASC
                    LIMIT %s
                    """,
                    (file_id, limit),
                )
            rows = cur.fetchall()
    return total, rows
