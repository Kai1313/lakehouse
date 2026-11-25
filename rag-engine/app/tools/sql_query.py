import datetime as dt
import decimal
import math
import re
from typing import Any

import duckdb

from app import db
from app.config import settings

FORBIDDEN_SQL_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "TRUNCATE",
    "ATTACH",
    "DETACH",
    "COPY",
    "EXPORT",
    "IMPORT",
    "PRAGMA",
    "CALL",
    "LOAD",
    "INSTALL",
    "SET",
    "USE",
    "VACUUM",
    "MERGE",
}

FORBIDDEN_IDENTIFIERS = (
    "delta_scan",
    "read_parquet",
    "read_csv",
    "read_json",
    "s3://",
    "http://",
    "https://",
    "file://",
)


def _strip_literals_and_comments(sql: str) -> str:
    # Remove block comments, line comments, and single-quoted literals so keyword scans ignore string data
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    no_strings = re.sub(r"'(?:''|[^'])*'", "''", no_line)
    return no_strings


def validate_readonly_sql(sql: str, max_limit: int = 100) -> str:
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("SQL query cannot be empty.")

    scanned = _strip_literals_and_comments(cleaned)
    if ";" in scanned:
        raise ValueError("Multiple SQL statements are not allowed.")

    lowered_scanned = scanned.lower()
    for banned in FORBIDDEN_IDENTIFIERS:
        if banned in lowered_scanned:
            raise ValueError(f"Direct external reader or URI '{banned}' is not allowed; query registered views only.")

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", scanned.upper())
    if not tokens:
        raise ValueError("Invalid SQL statement.")

    if tokens[0] not in {"SELECT", "WITH"}:
        raise ValueError("Only read-only SELECT or WITH queries are allowed.")

    for tok in tokens:
        if tok in FORBIDDEN_SQL_KEYWORDS:
            raise ValueError(f"Forbidden SQL keyword detected: {tok}")

    # Enforce maximum LIMIT 100
    limit_match = re.search(r"\bLIMIT\s+(\d+)\s*$", scanned, flags=re.IGNORECASE)
    if limit_match:
        requested = int(limit_match.group(1))
        if requested > max_limit:
            return f"SELECT * FROM ({cleaned}) AS _capped_result LIMIT {max_limit}"
        return cleaned

    return f"SELECT * FROM ({cleaned}) AS _limited_result LIMIT {max_limit}"


def _json_safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, (dt.datetime, dt.date)):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


def ping_duckdb() -> bool:
    try:
        con = duckdb.connect(":memory:")
        con.execute("SELECT 1").fetchone()
        con.close()
        return True
    except Exception:
        return False


def execute_silver_sql(sql: str, explanation: str = "") -> dict[str, Any]:
    """Executes a validated read-only SQL query against MinIO Silver Delta tables via DuckDB."""
    silver_tables = db.list_silver_tables()
    if not silver_tables:
        return {
            "error": "No Silver Excel tables are currently loaded in the Lakehouse.",
            "sql": sql,
            "explanation": explanation,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    try:
        safe_sql = validate_readonly_sql(sql, max_limit=100)
    except ValueError as exc:
        return {
            "error": str(exc),
            "sql": sql,
            "explanation": explanation,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL delta; LOAD delta;")
        endpoint_host = settings.minio_endpoint.replace("http://", "").replace("https://", "")
        use_ssl = "true" if settings.minio_endpoint.startswith("https://") else "false"
        con.execute(
            f"""
            CREATE SECRET minio_secret (
                TYPE S3,
                KEY_ID '{settings.minio_access_key}',
                SECRET '{settings.minio_secret_key}',
                REGION '{settings.aws_region}',
                ENDPOINT '{endpoint_host}',
                USE_SSL {use_ssl},
                URL_STYLE 'path'
            );
            """
        )

        for tbl in silver_tables:
            tname = tbl["table_name"].replace('"', '""')
            delta_uri = tbl["delta_uri"].replace("'", "''")
            con.execute(
                f'CREATE OR REPLACE TEMP VIEW "{tname}" AS '
                f"SELECT * EXCLUDE (_source_file_id, _ingested_at) FROM delta_scan('{delta_uri}')"
            )

        cursor = con.execute(safe_sql)
        columns = [desc[0] for desc in (cursor.description or [])]
        raw_rows = cursor.fetchall()
        rows = [
            {col: _json_safe(val) for col, val in zip(columns, row)}
            for row in raw_rows
        ]
        return {
            "sql": safe_sql,
            "explanation": explanation,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }
    except Exception as exc:
        return {
            "error": f"DuckDB SQL error: {exc}",
            "sql": safe_sql,
            "explanation": explanation,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    finally:
        con.close()
