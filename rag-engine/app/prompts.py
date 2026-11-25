import json
from typing import Any

from app import db
from app.config import settings

BASE_SYSTEM_PROMPT = """You are a grounded Lakehouse AI Assistant that answers questions strictly using data ingested into the user's Data Lakehouse.

You have access to two retrieval tools:
1. `search_documents(query, filename?, top_k?)`: Semantic vector search over PDF documents (policies, manuals, reports, notes). Always use this for text, definitions, rules, or qualitative PDF questions.
2. `query_tabular_data(sql, explanation)`: Read-only DuckDB SQL over Silver Delta tables extracted from Excel workbooks. Always use this for counts, sums, averages, filtering, comparisons, or exact spreadsheet lookups—never guess or calculate spreadsheet math in your head.

Rules:
- Only state facts, figures, and rules observed in tool results. If the Lakehouse does not contain the answer, say so clearly.
- If a SQL query returns an error, inspect the error and column names carefully and retry with corrected SQL.
- Cite your sources clearly: mention the PDF filename and page number for document answers, and summarize the queried Silver table / key figures for tabular answers.
- Reply in the same language as the user's question (for example, Indonesian or English).
"""


def _format_silver_catalog(tables: list[dict[str, Any]]) -> str:
    if not tables:
        return "Silver Excel Tables: (none loaded yet)"

    lines = ["### Available Silver Excel Tables (for `query_tabular_data` DuckDB SQL)"]
    for tbl in tables:
        cols_desc = ", ".join(
            f"{c['name']} ({c['type']}; header: '{c['original_name']}')"
            for c in (tbl.get("columns_json") or [])
        )
        samples = json.dumps(tbl.get("sample_rows_json") or [], ensure_ascii=False)
        lines.append(
            f"- Table `{tbl['table_name']}` (from `{tbl['original_filename']}`, sheet `{tbl['sheet_name']}`, {tbl['row_count']} rows)\n"
            f"  Columns: {cols_desc}\n"
            f"  Sample rows: {samples}"
        )
    return "\n".join(lines)


def _format_pdf_catalog(pdfs: list[dict[str, Any]]) -> str:
    if not pdfs:
        return "Ready PDF Documents: (none loaded yet)"

    lines = ["### Available PDF Documents (for `search_documents` vector search)"]
    for pdf in pdfs:
        meta = pdf.get("metadata_json") or {}
        pages = meta.get("page_count", "?")
        chunks = meta.get("chunk_count", "?")
        lines.append(f"- `{pdf['filename']}` ({pages} pages, {chunks} chunks)")
    return "\n".join(lines)


def build_system_prompt() -> str:
    base = settings.default_system_prompt.strip() or BASE_SYSTEM_PROMPT.strip()
    tables = db.list_silver_tables()
    pdfs = db.list_ready_pdf_files()
    return f"{base}\n\n{_format_silver_catalog(tables)}\n\n{_format_pdf_catalog(pdfs)}"
