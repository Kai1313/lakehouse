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


def ensure_schema_migrations() -> None:
    """Idempotent migrations for existing Postgres volumes created before Phase 8."""
    statements = [
        """
        ALTER TABLE data_engine.file_registry
            ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'needs_review'
        """,
        """
        ALTER TABLE data_engine.file_registry
            ADD COLUMN IF NOT EXISTS document_note TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE data_engine.silver_tables
            ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'needs_review'
        """,
        """
        ALTER TABLE data_engine.silver_tables
            ADD COLUMN IF NOT EXISTS header_rows_json JSONB NOT NULL DEFAULT '[]'::jsonb
        """,
        """
        ALTER TABLE data_engine.silver_tables
            ADD COLUMN IF NOT EXISTS mapping_override_json JSONB NOT NULL DEFAULT '{}'::jsonb
        """,
        """
        CREATE TABLE IF NOT EXISTS data_engine.silver_mapping_presets (
            filename TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            mapping_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (filename, sheet_name)
        )
        """,
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()


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
    review_status: str = "needs_review",
    document_note: str = "",
    metadata_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_engine.file_registry (
                    id, filename, file_type, file_hash, file_size_bytes,
                    bronze_uri, status, review_status, document_note, metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    review_status,
                    document_note,
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
    review_status: str | None = None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            sets = ["status = %s", "error_message = %s", "updated_at = now()"]
            params: list[Any] = [status, error_message]
            if metadata_json is not None:
                sets.append("metadata_json = %s")
                params.append(Jsonb(metadata_json))
            if review_status is not None:
                sets.append("review_status = %s")
                params.append(review_status)
            params.append(file_id)

            cur.execute(
                f"""
                UPDATE data_engine.file_registry
                SET {", ".join(sets)}
                WHERE id = %s
                RETURNING *
                """,
                params,
            )
            row = cur.fetchone()
        conn.commit()
    return row


def update_file_review(
    file_id: UUID,
    *,
    review_status: str,
    document_note: str | None = None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if document_note is not None:
                cur.execute(
                    """
                    UPDATE data_engine.file_registry
                    SET review_status = %s,
                        document_note = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (review_status, document_note, file_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE data_engine.file_registry
                    SET review_status = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (review_status, file_id),
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
    review_status: str = "needs_review",
    header_rows: list[int] | None = None,
    mapping_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_engine.silver_tables (
                    source_file_id, table_name, original_filename, sheet_name,
                    delta_uri, row_count, review_status, header_rows_json,
                    mapping_override_json, columns_json, sample_rows_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (table_name) DO UPDATE SET
                    source_file_id = EXCLUDED.source_file_id,
                    original_filename = EXCLUDED.original_filename,
                    sheet_name = EXCLUDED.sheet_name,
                    delta_uri = EXCLUDED.delta_uri,
                    row_count = EXCLUDED.row_count,
                    review_status = EXCLUDED.review_status,
                    header_rows_json = EXCLUDED.header_rows_json,
                    mapping_override_json = EXCLUDED.mapping_override_json,
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
                    review_status,
                    Jsonb(header_rows or []),
                    Jsonb(mapping_override or {}),
                    Jsonb(columns),
                    Jsonb(sample_rows),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def save_mapping_preset(filename: str, sheet_name: str, mapping: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_engine.silver_mapping_presets (filename, sheet_name, mapping_json, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (filename, sheet_name) DO UPDATE SET
                    mapping_json = EXCLUDED.mapping_json,
                    updated_at = now()
                """,
                (filename, sheet_name, Jsonb(mapping)),
            )
        conn.commit()


def get_mapping_presets_for_filename(filename: str) -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sheet_name, mapping_json
                FROM data_engine.silver_mapping_presets
                WHERE filename = %s
                """,
                (filename,),
            )
            return {row["sheet_name"]: row["mapping_json"] for row in cur.fetchall()}


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
                "SELECT * FROM data_engine.silver_tables WHERE source_file_id = %s ORDER BY sheet_name ASC",
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
