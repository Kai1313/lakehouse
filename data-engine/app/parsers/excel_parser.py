import datetime as dt
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from openpyxl.utils import range_boundaries

from app import db
from app.config import settings

_PERCENT_RE = re.compile(r"\(%\)")
_INT_STR_RE = re.compile(r"^-?\d+$")
_US_GROUPED_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_ID_GROUPED_RE = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
_ID_DECIMAL_RE = re.compile(r"^-?\d+,\d{1,4}$")
_PLAIN_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")


def sanitize_identifier(value: str, fallback: str = "col") -> str:
    text = _PERCENT_RE.sub(" pct", str(value))
    text = text.replace("%", " pct")
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    return cleaned


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


def _extract_xlsx_horizontal_merges(raw_bytes: bytes, max_scan_rows: int = 20) -> dict[str, list[tuple[int, int, int]]]:
    """Reads <mergeCell> horizontal ranges from .xlsx XML without loading full workbooks."""
    merges_by_sheet: dict[str, list[tuple[int, int, int]]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as z:
            if "xl/workbook.xml" not in z.namelist() or "xl/_rels/workbook.xml.rels" not in z.namelist():
                return merges_by_sheet

            wb_root = ET.fromstring(z.read("xl/workbook.xml"))
            rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))

            ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels_root}

            for sheet_el in wb_root.findall(f".//{{{ns_main}}}sheet"):
                name = sheet_el.attrib.get("name", "")
                rid = sheet_el.attrib.get(f"{{{ns_rel}}}id")
                target = rel_map.get(rid or "", "")
                if not target:
                    continue
                if not target.startswith("xl/"):
                    target = f"xl/{target.lstrip('/')}"
                if target not in z.namelist():
                    continue

                ws_root = ET.fromstring(z.read(target))
                spans: list[tuple[int, int, int]] = []
                for mc in ws_root.findall(f".//{{{ns_main}}}mergeCell"):
                    ref = mc.attrib.get("ref", "")
                    if ":" not in ref:
                        continue
                    min_c, min_r, max_c, max_r = range_boundaries(ref)
                    if min_r <= max_scan_rows and max_c > min_c:
                        # Store 0-based (row_idx, start_col_idx, end_col_idx)
                        spans.append((min_r - 1, min_c - 1, max_c - 1))
                merges_by_sheet[name] = spans
    except Exception:
        return {}
    return merges_by_sheet


def _is_cell_non_empty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    s = str(v).strip()
    return bool(s) and s.lower() != "nan"


def _expand_row_with_merges(
    row_vals: list[Any],
    row_idx: int,
    horiz_merges: list[tuple[int, int, int]],
) -> list[Any]:
    out = list(row_vals)
    for m_row, c_start, c_end in horiz_merges:
        if m_row == row_idx and 0 <= c_start < len(out):
            anchor_val = out[c_start]
            if _is_cell_non_empty(anchor_val):
                for c in range(c_start + 1, min(c_end + 1, len(out))):
                    if not _is_cell_non_empty(out[c]):
                        out[c] = anchor_val
    return out


def detect_header_rows(
    raw_df: pd.DataFrame,
    horiz_merges: list[tuple[int, int, int]] | None = None,
    max_scan: int = 15,
) -> list[int]:
    """Detects 1 or 2 consecutive header rows (supporting multi-row merged group headers)."""
    if raw_df.empty:
        return [0]

    merges = horiz_merges or []
    scan_limit = min(max_scan, len(raw_df))
    stats: list[dict[str, Any]] = []
    max_expanded = 0

    for idx in range(scan_limit):
        vals = raw_df.iloc[idx].tolist()
        expanded = _expand_row_with_merges(vals, idx, merges)

        non_empty = 0
        expanded_non_empty = 0
        str_count = 0
        num_or_bool_count = 0

        for v, ev in zip(vals, expanded):
            if _is_cell_non_empty(ev):
                expanded_non_empty += 1
            if not _is_cell_non_empty(v):
                continue
            non_empty += 1
            if isinstance(v, (bool, int, float)) and not (isinstance(v, float) and math.isnan(v)):
                num_or_bool_count += 1
            elif isinstance(v, str):
                s = v.strip()
                if _parse_locale_number(s) is not None:
                    num_or_bool_count += 1
                else:
                    str_count += 1

        max_expanded = max(max_expanded, expanded_non_empty)
        is_header_candidate = (
            non_empty >= 2
            and str_count >= 2
            and num_or_bool_count == 0
        )
        stats.append(
            {
                "idx": idx,
                "vals": vals,
                "expanded": expanded,
                "non_empty": non_empty,
                "expanded_non_empty": expanded_non_empty,
                "str_count": str_count,
                "num_count": num_or_bool_count,
                "is_header_candidate": is_header_candidate,
            }
        )

    if max_expanded == 0:
        return [0]

    # Pick dense header candidates (>= 40% of max expanded columns, minimum 2 cells)
    dense_threshold = max(2, math.ceil(max_expanded * 0.4))
    candidates = [s for s in stats if s["is_header_candidate"] and s["expanded_non_empty"] >= dense_threshold]

    if not candidates:
        # Fallback: first row matching max non-empty count
        best = max(stats, key=lambda s: s["expanded_non_empty"])
        return [best["idx"]]

    # Group contiguous header candidate rows starting from the first dense candidate
    first = candidates[0]
    group = [first["idx"]]
    for next_idx in range(first["idx"] + 1, min(first["idx"] + 3, scan_limit)):
        row_st = stats[next_idx]
        if row_st["is_header_candidate"] and row_st["expanded_non_empty"] >= dense_threshold:
            # Confirm it's genuinely a sub-header row (previous row has merges OR blanks in this row)
            prev_vals = stats[next_idx - 1]["vals"]
            cur_vals = row_st["vals"]
            has_complement_blanks = any(
                _is_cell_non_empty(pv) and not _is_cell_non_empty(cv)
                for pv, cv in zip(prev_vals, cur_vals)
            )
            has_parent_merge = any(m_row == next_idx - 1 for m_row, _, _ in merges)
            if has_complement_blanks or has_parent_merge:
                group.append(next_idx)
                continue
        break

    return group


def _build_coalesced_headers(
    raw_df: pd.DataFrame,
    header_rows: list[int],
    horiz_merges: list[tuple[int, int, int]] | None = None,
) -> tuple[list[str], list[str]]:
    merges = horiz_merges or []
    num_cols = raw_df.shape[1]
    row_matrices: list[list[Any]] = []

    for r_idx in header_rows:
        if 0 <= r_idx < len(raw_df):
            vals = raw_df.iloc[r_idx].tolist()
            expanded = _expand_row_with_merges(vals, r_idx, merges)
            row_matrices.append(expanded)

    # Heuristic fallback when .xls or unmerged cells have a parent group header without <mergeCell>
    if len(row_matrices) >= 2 and not merges:
        top_row = row_matrices[0]
        bottom_row = row_matrices[-1]
        active_group: Any = None
        for c in range(num_cols):
            if _is_cell_non_empty(top_row[c]):
                active_group = top_row[c] if _is_cell_non_empty(bottom_row[c]) else None
            elif _is_cell_non_empty(bottom_row[c]) and active_group is not None:
                top_row[c] = active_group

    seen: dict[str, int] = {}
    clean_names: list[str] = []
    original_names: list[str] = []

    for c in range(num_cols):
        parts: list[str] = []
        for rm in row_matrices:
            cell = rm[c] if c < len(rm) else None
            if _is_cell_non_empty(cell):
                text = " ".join(str(cell).split())
                if not parts or parts[-1].lower() != text.lower():
                    parts.append(text)

        orig = " — ".join(parts) if parts else f"Column {c + 1}"
        base = sanitize_identifier(" ".join(parts) if parts else "", fallback=f"col_{c + 1}")
        if base in {"_source_file_id", "_ingested_at"}:
            base = f"src_{base.lstrip('_')}"

        count = seen.get(base, 0) + 1
        seen[base] = count
        unique_name = base if count == 1 else f"{base}_{count}"
        clean_names.append(unique_name)
        original_names.append(orig)

    return clean_names, original_names


def _parse_locale_number(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return float(val)

    s = str(val).strip()
    if not s or any(ch.isalpha() for ch in s) or "(" in s or ")" in s or " " in s:
        return None

    if _INT_STR_RE.match(s) or _PLAIN_FLOAT_RE.match(s):
        # Check for Indonesian thousand grouping like 50.000 or 1.500.000
        if _ID_GROUPED_RE.match(s) and s.count(".") >= 1 and "," not in s:
            # Ambiguous single dot with 3 trailing digits like 1.500 -> treat >=2 dots strictly as thousand sep
            if s.count(".") >= 2:
                return float(s.replace(".", ""))
        return float(s)

    if _US_GROUPED_RE.match(s):
        return float(s.replace(",", ""))

    if _ID_GROUPED_RE.match(s):
        return float(s.replace(".", "").replace(",", "."))

    if _ID_DECIMAL_RE.match(s):
        return float(s.replace(",", "."))

    return None


def _series_to_arrow_array(
    series: pd.Series,
    forced_type: str | None = None,
) -> tuple[pa.Array, str]:
    raw_list = series.tolist()

    if forced_type == "VARCHAR":
        vals = [None if (v is None or pd.isna(v)) else str(v).strip() for v in raw_list]
        return pa.array(vals, type=pa.string()), "VARCHAR"

    if forced_type == "BOOLEAN":
        vals: list[bool | None] = []
        for v in raw_list:
            if v is None or pd.isna(v):
                vals.append(None)
            elif isinstance(v, bool):
                vals.append(v)
            elif str(v).strip().lower() in {"true", "1", "yes", "ya"}:
                vals.append(True)
            elif str(v).strip().lower() in {"false", "0", "no", "tidak"}:
                vals.append(False)
            else:
                vals.append(None)
        return pa.array(vals, type=pa.bool_()), "BOOLEAN"

    if forced_type in {"BIGINT", "DOUBLE"}:
        parsed = [_parse_locale_number(None if pd.isna(v) else v) for v in raw_list]
        if forced_type == "BIGINT":
            int_vals = [None if num is None else int(round(num)) for num in parsed]
            return pa.array(int_vals, type=pa.int64()), "BIGINT"
        return pa.array(parsed, type=pa.float64()), "DOUBLE"

    # Auto type inference
    non_null = [v for v in raw_list if v is not None and not (isinstance(v, float) and math.isnan(v)) and v is not pd.NaT]
    if not non_null:
        return pa.array([None] * len(raw_list), type=pa.string()), "VARCHAR"

    if all(isinstance(v, bool) for v in non_null):
        vals = [None if pd.isna(v) else bool(v) for v in raw_list]
        return pa.array(vals, type=pa.bool_()), "BOOLEAN"

    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        is_all_int = all(isinstance(v, int) or (isinstance(v, float) and v.is_integer()) for v in non_null)
        if is_all_int:
            vals = [None if pd.isna(v) else int(v) for v in raw_list]
            return pa.array(vals, type=pa.int64()), "BIGINT"
        vals = [None if pd.isna(v) else float(v) for v in raw_list]
        return pa.array(vals, type=pa.float64()), "DOUBLE"

    if all(isinstance(v, (pd.Timestamp, dt.datetime, dt.date)) for v in non_null):
        vals = [None if pd.isna(v) else v.isoformat() for v in raw_list]
        return pa.array(vals, type=pa.string()), "VARCHAR"

    # Check if string column is >= 90% clean locale-formatted numbers (e.g., 14,042.62 or 102,72)
    parsed_non_null = [_parse_locale_number(v) for v in non_null]
    numeric_hits = [p for p in parsed_non_null if p is not None]
    if len(numeric_hits) >= max(1, math.ceil(len(non_null) * 0.9)):
        all_parsed = [_parse_locale_number(None if pd.isna(v) else v) for v in raw_list]
        is_all_int = all(p.is_integer() for p in numeric_hits)
        if is_all_int:
            int_vals = [None if p is None else int(p) for p in all_parsed]
            return pa.array(int_vals, type=pa.int64()), "BIGINT"
        return pa.array(all_parsed, type=pa.float64()), "DOUBLE"

    vals = [None if (v is None or pd.isna(v)) else str(v).strip() for v in raw_list]
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


def extract_raw_excel_preview(filename: str, raw_bytes: bytes, max_rows: int = 15) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    merges_by_sheet = _extract_xlsx_horizontal_merges(raw_bytes) if ext == ".xlsx" else {}

    xl = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
    previews: list[dict[str, Any]] = []

    for sheet_name in xl.sheet_names:
        raw_df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
        if raw_df.empty:
            continue
        raw_df = raw_df.dropna(axis=1, how="all")
        horiz_merges = merges_by_sheet.get(sheet_name, [])
        detected_raw = detect_header_rows(raw_df, horiz_merges=horiz_merges)

        top_rows: list[list[Any]] = []
        for idx in range(min(max_rows, len(raw_df))):
            expanded = _expand_row_with_merges(raw_df.iloc[idx].tolist(), idx, horiz_merges)
            top_rows.append([_to_json_safe(v) for v in expanded])

        previews.append(
            {
                "sheet_name": sheet_name,
                "total_raw_rows": len(raw_df),
                "rows": top_rows,
                "detected_header_rows": detected_raw,
            }
        )
    return previews


def ingest_excel_bytes(
    *,
    source_file_id: UUID,
    filename: str,
    raw_bytes: bytes,
    sheet_overrides: dict[str, dict[str, Any]] | None = None,
    review_status: str = "needs_review",
) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    merges_by_sheet = _extract_xlsx_horizontal_merges(raw_bytes) if ext == ".xlsx" else {}
    overrides_map = sheet_overrides or {}

    xl = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
    ingested_tables: list[dict[str, Any]] = []
    ingested_at = dt.datetime.now(dt.timezone.utc).isoformat()

    for sheet_name in xl.sheet_names:
        raw_df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
        raw_df = raw_df.dropna(axis=1, how="all")
        if raw_df.dropna(how="all").empty:
            continue

        horiz_merges = merges_by_sheet.get(sheet_name, [])
        override = overrides_map.get(sheet_name)

        if override and override.get("header_rows"):
            header_rows = sorted({int(r) for r in override["header_rows"] if 0 <= int(r) < len(raw_df)})
            if not header_rows:
                header_rows = detect_header_rows(raw_df, horiz_merges=horiz_merges)
        else:
            header_rows = detect_header_rows(raw_df, horiz_merges=horiz_merges)

        last_header_row = max(header_rows)
        data_df = raw_df.iloc[last_header_row + 1 :].copy()
        data_df = data_df.dropna(how="all").reset_index(drop=True)

        auto_names, original_names = _build_coalesced_headers(raw_df, header_rows, horiz_merges=horiz_merges)
        col_override_by_idx: dict[int, dict[str, Any]] = {}
        if override and override.get("columns"):
            for col_ov in override["columns"]:
                col_override_by_idx[int(col_ov["index"])] = col_ov

        # Build final column plan (respecting keep flags, user renames, and unique SQL names)
        seen_names: dict[str, int] = {}
        selected_cols: list[tuple[int, str, str, str | None]] = []

        for col_idx in range(raw_df.shape[1]):
            auto_name = auto_names[col_idx]
            orig_name = original_names[col_idx]
            ov = col_override_by_idx.get(col_idx)

            if ov is not None:
                if not ov.get("keep", True):
                    continue
                chosen_base = sanitize_identifier(ov.get("name") or auto_name, fallback=f"col_{col_idx + 1}")
                forced_type = ov.get("type")
            else:
                if orig_name.startswith("Column ") and data_df.iloc[:, col_idx].isna().all():
                    continue
                chosen_base = auto_name
                forced_type = None

            if chosen_base in {"_source_file_id", "_ingested_at"}:
                chosen_base = f"src_{chosen_base.lstrip('_')}"
            count = seen_names.get(chosen_base, 0) + 1
            seen_names[chosen_base] = count
            final_name = chosen_base if count == 1 else f"{chosen_base}_{count}"
            selected_cols.append((col_idx, final_name, orig_name, forced_type))

        if not selected_cols:
            continue

        arrow_arrays: list[pa.Array] = []
        arrow_names: list[str] = []
        columns_meta: list[dict[str, Any]] = []
        preview_df_dict: dict[str, list[Any]] = {}

        row_count = len(data_df)
        for col_idx, final_name, orig_name, forced_type in selected_cols:
            series = data_df.iloc[:, col_idx]
            arr, sql_type = _series_to_arrow_array(series, forced_type=forced_type)
            arrow_arrays.append(arr)
            arrow_names.append(final_name)
            columns_meta.append(
                {
                    "index": col_idx,
                    "name": final_name,
                    "original_name": orig_name,
                    "type": sql_type,
                    "keep": True,
                }
            )
            preview_df_dict[final_name] = arr.slice(0, 3).to_pylist()

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
        sample_len = min(3, row_count)
        for r_i in range(sample_len):
            sample_rows.append({k: _to_json_safe(preview_df_dict[k][r_i]) for k in preview_df_dict})

        sheet_review_status = "verified" if override else review_status
        record = db.upsert_silver_table(
            source_file_id=source_file_id,
            table_name=table_name,
            original_filename=filename,
            sheet_name=sheet_name,
            delta_uri=delta_uri,
            row_count=row_count,
            columns=columns_meta,
            sample_rows=sample_rows,
            review_status=sheet_review_status,
            header_rows=header_rows,
            mapping_override=override or {},
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
