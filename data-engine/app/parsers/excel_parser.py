import datetime as dt
import io
import math
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from app import db
from app.config import settings


def sanitize_identifier(value: str, fallback: str = "col") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    return cleaned


def _detect_header_row(raw_df: pd.DataFrame, max_scan: int = 15) -> int:
    if raw_df.empty:
        return 0

    scan_limit = min(max_scan, len(raw_df))
    row_scores: list[tuple[int, int, int]] = []
    max_non_empty = 0

    for idx in range(scan_limit):
        vals = raw_df.iloc[idx].tolist()
        non_empty = 0
        str_like = 0
        for v in vals:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            s = str(v).strip()
            if not s or s.lower() == "nan":
                continue
            non_empty += 1
            if isinstance(v, str):
                str_like += 1
        max_non_empty = max(max_non_empty, non_empty)
        row_scores.append((idx, non_empty, str_like))

    if max_non_empty == 0:
        return 0

    threshold = max(2, math.ceil(max_non_empty * 0.7)) if max_non_empty >= 2 else 1
    for idx, non_empty, str_like in row_scores:
        if non_empty >= threshold and str_like >= max(1, non_empty // 2):
            return idx

    for idx, non_empty, _ in row_scores:
        if non_empty == max_non_empty:
            return idx
    return 0


def _normalize_columns(raw_headers: list[Any]) -> tuple[list[str], list[str]]:
    seen: dict[str, int] = {}
    clean_names: list[str] = []
    original_names: list[str] = []

    for idx, raw in enumerate(raw_headers):
        is_missing = raw is None or (isinstance(raw, float) and math.isnan(raw)) or not str(raw).strip()
        orig = f"Column {idx + 1}" if is_missing else str(raw).strip()
        base = sanitize_identifier(orig, fallback=f"col_{idx + 1}")
        if base in {"_source_file_id", "_ingested_at"}:
            base = f"src_{base.lstrip('_')}"

        count = seen.get(base, 0) + 1
        seen[base] = count
        unique_name = base if count == 1 else f"{base}_{count}"
        clean_names.append(unique_name)
        original_names.append(orig)

    return clean_names, original_names


def _to_json_safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (pd.Timestamp, dt.datetime, dt.date)):
        return val.isoformat()
    if hasattr(val, "item"):
        return _to_json_safe(val.item())
    return val


def _series_to_arrow_array(series: pd.Series) -> tuple[pa.Array, str]:
    non_null = [v for v in series.tolist() if v is not None and not (isinstance(v, float) and math.isnan(v)) and v is not pd.NaT]
    if not non_null:
        return pa.array([None] * len(series), type=pa.string()), "VARCHAR"

    if all(isinstance(v, bool) for v in non_null):
        vals = [None if pd.isna(v) else bool(v) for v in series.tolist()]
        return pa.array(vals, type=pa.bool_()), "BOOLEAN"

    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        is_all_int = all(isinstance(v, int) or (isinstance(v, float) and v.is_integer()) for v in non_null)
        if is_all_int:
            vals = [None if pd.isna(v) else int(v) for v in series.tolist()]
            return pa.array(vals, type=pa.int64()), "BIGINT"
        vals = [None if pd.isna(v) else float(v) for v in series.tolist()]
        return pa.array(vals, type=pa.float64()), "DOUBLE"

    if all(isinstance(v, (pd.Timestamp, dt.datetime, dt.date)) for v in non_null):
        vals = [None if pd.isna(v) else v.isoformat() for v in series.tolist()]
        return pa.array(vals, type=pa.string()), "VARCHAR"

    vals = [None if (v is None or pd.isna(v)) else str(v).strip() for v in series.tolist()]
    return pa.array(vals, type=pa.string()), "VARCHAR"


def _resolve_unique_table_name(filename: str, sheet_name: str, source_file_id: UUID) -> str:
    stem = sanitize_identifier(Path(filename).stem, fallback="excel")
    sheet = sanitize_identifier(sheet_name, fallback="sheet")
    base = f"{stem}__{sheet}"[:56].strip("_")

    existing = db.get_silver_table_by_name(base)
    if existing and str(existing["source_file_id"]) != str(source_file_id):
        suffix = str(source_file_id).replace("-", "")[:6]
        return f"{base[:49]}_{suffix}"
    return base


def ingest_excel_bytes(
    *,
    source_file_id: UUID,
    filename: str,
    raw_bytes: bytes,
) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"

    excel_buf = io.BytesIO(raw_bytes)
    xl = pd.ExcelFile(excel_buf, engine=engine)

    ingested_tables: list[dict[str, Any]] = []
    ingested_at = dt.datetime.now(dt.timezone.utc).isoformat()

    for sheet_name in xl.sheet_names:
        raw_df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
        raw_df = raw_df.dropna(how="all").dropna(axis=1, how="all")
        if raw_df.empty:
            continue

        raw_df = raw_df.reset_index(drop=True)
        header_idx = _detect_header_row(raw_df)
        raw_headers = raw_df.iloc[header_idx].tolist()
        data_df = raw_df.iloc[header_idx + 1 :].copy()
        data_df = data_df.dropna(how="all").reset_index(drop=True)

        clean_names, original_names = _normalize_columns(raw_headers)
        data_df.columns = clean_names

        # Drop completely empty unnamed columns after header detection
        keep_cols: list[tuple[str, str]] = []
        for cname, oname in zip(clean_names, original_names):
            if oname.startswith("Column ") and data_df[cname].isna().all():
                continue
            keep_cols.append((cname, oname))

        if not keep_cols:
            continue

        data_df = data_df[[c for c, _ in keep_cols]]

        arrow_arrays: list[pa.Array] = []
        arrow_names: list[str] = []
        columns_meta: list[dict[str, str]] = []

        for cname, oname in keep_cols:
            arr, sql_type = _series_to_arrow_array(data_df[cname])
            arrow_arrays.append(arr)
            arrow_names.append(cname)
            columns_meta.append({"name": cname, "original_name": oname, "type": sql_type})

        row_count = len(data_df)
        arrow_arrays.append(pa.array([str(source_file_id)] * row_count, type=pa.string()))
        arrow_names.append("_source_file_id")
        arrow_arrays.append(pa.array([ingested_at] * row_count, type=pa.string()))
        arrow_names.append("_ingested_at")

        pa_table = pa.Table.from_arrays(arrow_arrays, names=arrow_names)

        table_name = _resolve_unique_table_name(filename, sheet_name, source_file_id)
        delta_uri = f"s3://{settings.minio_bucket}/silver/tables/{table_name}"

        write_deltalake(
            delta_uri,
            pa_table,
            mode="overwrite",
            schema_mode="overwrite",
            storage_options=settings.delta_storage_options,
        )

        sample_rows: list[dict[str, Any]] = []
        for rec in data_df.head(3).to_dict(orient="records"):
            sample_rows.append({k: _to_json_safe(v) for k, v in rec.items()})

        record = db.upsert_silver_table(
            source_file_id=source_file_id,
            table_name=table_name,
            original_filename=filename,
            sheet_name=sheet_name,
            delta_uri=delta_uri,
            row_count=row_count,
            columns=columns_meta,
            sample_rows=sample_rows,
        )
        ingested_tables.append(record)

    return ingested_tables


def read_delta_preview(delta_uri: str, limit: int = 20) -> list[dict[str, Any]]:
    dt_table = DeltaTable(delta_uri, storage_options=settings.delta_storage_options)
    pa_head = dt_table.to_pyarrow_dataset().head(limit)
    rows = pa_head.to_pylist()
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                k: _to_json_safe(v)
                for k, v in row.items()
                if k not in {"_source_file_id", "_ingested_at"}
            }
        )
    return cleaned
