import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID

import httpx
import litellm
import yaml

from app import db
from app.config import settings
from app.prompts import build_system_prompt
from app.schemas import ModelOption
from app.tools.sql_query import execute_silver_sql
from app.tools.vector_search import search_documents

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_SQL_ERROR_RETRIES = 2

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Semantic vector search over extracted PDF document chunks in pgvector. "
                "Use for questions about policies, guidelines, explanations, or text inside PDFs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional partial or exact PDF filename filter.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of top chunks to return (1-12, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tabular_data",
            "description": (
                "Execute a read-only DuckDB SELECT/WITH SQL query against Silver Excel Delta tables "
                "listed in the system prompt. Always double-quote column or table names if needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "Read-only DuckDB SELECT or WITH query referencing registered Silver table names.",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Brief description of what this query checks.",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]


def _check_ollama_alive(base_url: str) -> bool:
    if not base_url:
        return False
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=0.3)
        return resp.status_code == 200
    except Exception:
        return False


def _is_env_key_configured(env_key: str) -> bool:
    if env_key == "OLLAMA_API_BASE":
        return _check_ollama_alive(settings.ollama_api_base)
    attr = env_key.lower()
    val = getattr(settings, attr, "")
    return bool(str(val).strip())


def load_models_registry() -> list[ModelOption]:
    yaml_path = Path(settings.models_yaml_path)
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    items = raw.get("models", [])
    options: list[ModelOption] = []
    for item in items:
        env_key = item.get("env_key", "")
        options.append(
            ModelOption(
                id=item["id"],
                label=item["label"],
                provider=item["provider"],
                configured=_is_env_key_configured(env_key),
                supports_tools=bool(item.get("supports_tools", True)),
            )
        )
    return options


def get_model_option(model_id: str) -> ModelOption | None:
    for m in load_models_registry():
        if m.id == model_id:
            return m
    return None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_chat_turn(
    *,
    conversation: dict[str, Any],
    user_message: str,
    model_id: str,
) -> AsyncIterator[str]:
    conv_id: UUID = conversation["id"]

    # Auto-title new conversations on first message
    new_title: str | None = None
    if conversation["title"] == "New Conversation" and not conversation.get("messages"):
        trimmed = " ".join(user_message.strip().split())
        new_title = trimmed[:56] + ("..." if len(trimmed) > 56 else "")

    db.update_conversation_meta(conv_id, title=new_title, model_id=model_id)
    db.add_message(conversation_id=conv_id, role="user", content=user_message, model_id=model_id)

    system_prompt = build_system_prompt()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for hist in (conversation.get("messages") or [])[-20:]:
        messages.append({"role": hist["role"], "content": hist["content"]})
    messages.append({"role": "user", "content": user_message})

    pdf_citations_by_id: dict[str, dict[str, Any]] = {}
    sql_citations: list[dict[str, Any]] = []
    final_answer_parts: list[str] = []
    sql_error_count = 0

    try:
        for _round in range(MAX_TOOL_ROUNDS + 1):
            yield _sse("status", {"message": "Thinking..."})

            extra_kwargs: dict[str, Any] = {}
            if model_id.startswith("ollama/") and settings.ollama_api_base:
                extra_kwargs["api_base"] = settings.ollama_api_base

            round_text_parts: list[str] = []
            tool_calls_acc: dict[int, dict[str, str]] = {}

            for attempt in range(3):
                try:
                    stream = await litellm.acompletion(
                        model=model_id,
                        messages=messages,
                        tools=TOOL_DEFINITIONS,
                        tool_choice="auto",
                        stream=True,
                        **extra_kwargs,
                    )

                    async for chunk in stream:
                        choices = getattr(chunk, "choices", None) or []
                        if not choices:
                            continue
                        delta = choices[0].delta

                        delta_content = getattr(delta, "content", None)
                        if delta_content:
                            round_text_parts.append(delta_content)
                            final_answer_parts.append(delta_content)
                            yield _sse("token", {"delta": delta_content})

                        delta_tools = getattr(delta, "tool_calls", None) or []
                        for tc in delta_tools:
                            idx = getattr(tc, "index", 0) or 0
                            entry = tool_calls_acc.setdefault(
                                idx,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            if getattr(tc, "id", None):
                                entry["id"] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn:
                                if getattr(fn, "name", None):
                                    entry["name"] += fn.name
                                if getattr(fn, "arguments", None):
                                    entry["arguments"] += fn.arguments
                    break
                except (litellm.ServiceUnavailableError, litellm.RateLimitError, litellm.MidStreamFallbackError) as retry_exc:
                    # Only retry cleanly if no partial tokens or tool calls were emitted yet
                    if attempt < 2 and not round_text_parts and not tool_calls_acc:
                        yield _sse("status", {"message": f"Provider busy, retrying ({attempt + 1}/2)..."})
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise retry_exc

            round_text = "".join(round_text_parts)
            ordered_tools = [tool_calls_acc[k] for k in sorted(tool_calls_acc.keys())]

            if not ordered_tools:
                break

            assistant_tool_calls = [
                {
                    "id": tc["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                }
                for i, tc in enumerate(ordered_tools)
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": round_text or None,
                    "tool_calls": assistant_tool_calls,
                }
            )

            for tc_payload in assistant_tool_calls:
                tc_id = tc_payload["id"]
                fn_name = tc_payload["function"]["name"]
                raw_args = tc_payload["function"]["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}

                yield _sse("tool_call", {"id": tc_id, "name": fn_name, "arguments": args})

                if fn_name == "search_documents":
                    yield _sse("status", {"message": "Searching PDF vector index..."})
                    result = search_documents(
                        query=args.get("query", user_message),
                        filename=args.get("filename"),
                        top_k=args.get("top_k", 5),
                    )
                    for item in result.get("results", []):
                        pdf_citations_by_id[item["chunk_id"]] = item
                    summary = f"Found {result.get('count', 0)} relevant PDF chunk(s)."
                elif fn_name == "query_tabular_data":
                    yield _sse("status", {"message": "Running DuckDB SQL on Silver tables..."})
                    result = execute_silver_sql(
                        sql=args.get("sql", ""),
                        explanation=args.get("explanation", ""),
                    )
                    if "error" in result:
                        sql_error_count += 1
                        summary = result["error"]
                        if sql_error_count > MAX_SQL_ERROR_RETRIES:
                            result["note"] = (
                                "Maximum SQL retry limit reached. Do not call query_tabular_data again; "
                                "explain what failed clearly to the user."
                            )
                    else:
                        sql_citations.append(result)
                        summary = f"Returned {result.get('row_count', 0)} row(s)."
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}
                    summary = result["error"]

                yield _sse("tool_result", {"id": tc_id, "name": fn_name, "summary": summary})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        citations_payload = {
            "pdf_citations": list(pdf_citations_by_id.values()),
            "sql_citations": sql_citations,
        }
        full_answer = "".join(final_answer_parts).strip() or "I could not generate a response."

        saved = db.add_message(
            conversation_id=conv_id,
            role="assistant",
            content=full_answer,
            model_id=model_id,
            citations_json=citations_payload,
        )

        yield _sse("citations", citations_payload)
        yield _sse(
            "done",
            {
                "message_id": str(saved["id"]),
                "conversation_id": str(conv_id),
                "title": new_title or conversation["title"],
            },
        )
    except Exception as exc:
        logger.exception("Chat streaming error for conversation_id=%s", conv_id)
        yield _sse("error", {"detail": str(exc)})


async def suggest_silver_schema(
    *,
    model_id: str,
    filename: str,
    sheet_name: str,
    raw_rows: list[list[Any]],
    detected_header_rows: list[int],
    current_columns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Asks the selected LiteLLM model to suggest header_rows, SQL column names, and types."""
    numbered_rows = [{"row_index": i, "cells": row} for i, row in enumerate(raw_rows[:15])]
    prompt_payload = {
        "filename": filename,
        "sheet_name": sheet_name,
        "detected_header_rows": detected_header_rows,
        "current_columns": current_columns,
        "raw_rows_preview": numbered_rows,
    }
    system_msg = (
        "You are a Data Engineer reviewing a raw Excel sheet for Silver Delta Lake ingestion.\n"
        "Inspect the 0-based `raw_rows_preview` (note multi-row merged headers or title banners at top) "
        "and `current_columns`.\n"
        "Return ONLY valid JSON with this exact shape:\n"
        '{"header_rows": [int, ...], "columns": [{"index": int, "name": "snake_case_sql_name", '
        '"type": "VARCHAR|BIGINT|DOUBLE|BOOLEAN", "keep": true, "reason": "short note"}]}\n'
        "Rules:\n"
        "- `header_rows` are the 0-based row indices forming the column headers (e.g. [3, 4] for 2-row merged headers).\n"
        "- Include every column index from `current_columns` (or matching max width of data rows).\n"
        "- Use clean snake_case ASCII SQL names (incorporating parent group names like `rencana_alokasi_rp`, `realisasi_total_rp`).\n"
        "- Allowed types strictly: VARCHAR, BIGINT, DOUBLE, BOOLEAN."
    )

    extra_kwargs: dict[str, Any] = {}
    if model_id.startswith("ollama/") and settings.ollama_api_base:
        extra_kwargs["api_base"] = settings.ollama_api_base

    resp = await litellm.acompletion(
        model=model_id,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        **extra_kwargs,
    )
    raw_text = (resp.choices[0].message.content or "").strip()
    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
    parsed = json.loads(raw_text)

    allowed_types = {"VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN"}
    cleaned_cols: list[dict[str, Any]] = []
    for col in parsed.get("columns", []):
        col_type = str(col.get("type", "VARCHAR")).upper()
        if col_type not in allowed_types:
            col_type = "VARCHAR"
        clean_name = re.sub(r"[^a-z0-9]+", "_", str(col.get("name", "")).lower()).strip("_")
        if not clean_name:
            clean_name = f"col_{int(col.get('index', 0)) + 1}"
        if clean_name[0].isdigit():
            clean_name = f"col_{clean_name}"
        cleaned_cols.append(
            {
                "index": int(col.get("index", len(cleaned_cols))),
                "name": clean_name,
                "type": col_type,
                "keep": bool(col.get("keep", True)),
                "reason": str(col.get("reason", ""))[:120],
            }
        )

    header_rows = [int(r) for r in parsed.get("header_rows", detected_header_rows) if isinstance(r, (int, float))]
    if not header_rows:
        header_rows = detected_header_rows or [0]

    return {
        "header_rows": sorted(set(header_rows)),
        "columns": cleaned_cols,
    }
