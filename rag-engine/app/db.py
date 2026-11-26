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


def list_silver_tables() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, original_filename, sheet_name, delta_uri,
                       row_count, review_status, columns_json, sample_rows_json
                FROM data_engine.silver_tables
                ORDER BY table_name ASC
                """
            )
            return cur.fetchall()


def list_ready_pdf_files() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT filename, metadata_json, review_status, document_note
                FROM data_engine.file_registry
                WHERE file_type = 'pdf' AND status = 'ready'
                ORDER BY filename ASC
                """
            )
            return cur.fetchall()


def search_document_chunks(
    query_embedding: list[float],
    *,
    top_k: int = 5,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    vec_literal = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
    with get_conn() as conn:
        with conn.cursor() as cur:
            if filename:
                cur.execute(
                    """
                    SELECT id, source_file_id, filename, page_number, chunk_index, content,
                           metadata_json,
                           1 - (embedding <=> %s::vector) AS score
                    FROM rag.document_chunks
                    WHERE filename ILIKE %s
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT %s
                    """,
                    (vec_literal, f"%{filename}%", vec_literal, top_k),
                )
            else:
                cur.execute(
                    """
                    SELECT id, source_file_id, filename, page_number, chunk_index, content,
                           metadata_json,
                           1 - (embedding <=> %s::vector) AS score
                    FROM rag.document_chunks
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT %s
                    """,
                    (vec_literal, vec_literal, top_k),
                )
            return cur.fetchall()


def list_conversations() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, model_id, created_at, updated_at
                FROM rag.conversations
                ORDER BY updated_at DESC
                """
            )
            return cur.fetchall()


def create_conversation(title: str = "New Conversation", model_id: str | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag.conversations (title, model_id)
                VALUES (%s, %s)
                RETURNING id, title, model_id, created_at, updated_at
                """,
                (title, model_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def get_conversation(conversation_id: UUID) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, model_id, created_at, updated_at
                FROM rag.conversations
                WHERE id = %s
                """,
                (conversation_id,),
            )
            conv = cur.fetchone()
            if not conv:
                return None
            cur.execute(
                """
                SELECT id, conversation_id, role, content, model_id, citations_json, created_at
                FROM rag.messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )
            conv["messages"] = cur.fetchall()
            return conv


def update_conversation_meta(
    conversation_id: UUID,
    *,
    title: str | None = None,
    model_id: str | None = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if title is not None and model_id is not None:
                cur.execute(
                    """
                    UPDATE rag.conversations
                    SET title = %s, model_id = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (title, model_id, conversation_id),
                )
            elif title is not None:
                cur.execute(
                    """
                    UPDATE rag.conversations
                    SET title = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (title, conversation_id),
                )
            elif model_id is not None:
                cur.execute(
                    """
                    UPDATE rag.conversations
                    SET model_id = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (model_id, conversation_id),
                )
        conn.commit()


def delete_conversation(conversation_id: UUID) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag.conversations WHERE id = %s RETURNING id",
                (conversation_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def add_message(
    *,
    conversation_id: UUID,
    role: str,
    content: str,
    model_id: str | None = None,
    citations_json: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag.messages (conversation_id, role, content, model_id, citations_json)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, conversation_id, role, content, model_id, citations_json, created_at
                """,
                (conversation_id, role, content, model_id, Jsonb(citations_json if citations_json is not None else {})),
            )
            row = cur.fetchone()
            cur.execute(
                "UPDATE rag.conversations SET updated_at = now() WHERE id = %s",
                (conversation_id,),
            )
        conn.commit()
    return row
