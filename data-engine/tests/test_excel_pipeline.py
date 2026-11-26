import io
import time

import duckdb
import openpyxl
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app


def _make_sample_workbook() -> bytes:
    wb = openpyxl.Workbook()

    # Sheet 1: has a title banner row + empty row before actual headers
    ws1 = wb.active
    ws1.title = "Permit Summary"
    ws1.append(["MONTHLY REPORT 2026"])
    ws1.append([])
    ws1.append(["Permit ID", "Area Name", "Fee (IDR)", "Approved?"])
    ws1.append(["PBG-001", "Area A", 1500000, True])
    ws1.append(["PBG-002", "Area B", 2750000, False])

    # Sheet 2: clean header at row 0
    ws2 = wb.create_sheet("Zone Rules")
    ws2.append(["Zone Code", "Max Floors"])
    ws2.append(["R-1", 3])
    ws2.append(["C-2", 8])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_lakehouse_e2e():
    excel_bytes = _make_sample_workbook()
    filename = "__phase2_smoke_test.xlsx"

    with TestClient(app) as client:
        # 1. Health check
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json() == {"status": "ok", "postgres": True, "minio": True}

        # 2. Upload Excel -> 202 Accepted
        r_up = client.post(
            "/files/upload",
            files=[("files", (filename, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        assert r_up.status_code == 202, r_up.text
        item = r_up.json()["items"][0]
        file_id = item["file_id"]
        assert item["action"] in {"queued", "replaced"}

        # 3. Poll until ready
        status_val = item["status"]
        detail = None
        for _ in range(30):
            r_get = client.get(f"/files/{file_id}")
            assert r_get.status_code == 200
            detail = r_get.json()
            status_val = detail["status"]
            if status_val in {"ready", "failed"}:
                break
            time.sleep(0.25)

        assert status_val == "ready", f"Unexpected status: {detail}"
        assert len(detail["silver_tables"]) == 2

        # 4. Preview Silver tables & verify DuckDB delta_scan
        r_tables = client.get("/tables")
        assert r_tables.status_code == 200
        table_map = {t["table_name"]: t for t in r_tables.json() if t["source_file_id"] == file_id}
        assert len(table_map) == 2

        first_table_name = detail["silver_tables"][0]
        r_prev = client.get(f"/tables/{first_table_name}/preview?limit=10")
        assert r_prev.status_code == 200
        prev_data = r_prev.json()
        assert prev_data["row_count"] == 2
        assert len(prev_data["rows"]) == 2

        # Verify DuckDB can read the Delta table directly from MinIO
        delta_uri = table_map[first_table_name]["delta_uri"]
        con = duckdb.connect()
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
        duck_count = con.execute(f"SELECT COUNT(*) FROM delta_scan('{delta_uri}')").fetchone()[0]
        assert duck_count == 2
        con.close()

        # 5. SHA-256 deduplication check
        r_dup = client.post(
            "/files/upload",
            files=[("files", (filename, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        assert r_dup.status_code == 202
        dup_item = r_dup.json()["items"][0]
        assert dup_item["action"] == "skipped_duplicate"
        assert dup_item["file_id"] == file_id

        # 6. Clean delete & verify removal
        r_del = client.delete(f"/files/{file_id}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True

        r_gone = client.get(f"/files/{file_id}")
        assert r_gone.status_code == 404


def _make_merged_header_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monitoring"
    ws.append(["Laporan Penyerapan 2026"])
    ws.append([])
    ws.append(["Perangkat Daerah", "Nama Pekerjaan", "Rencana", None, "Realisasi", None])
    ws.append([None, None, "Alokasi (Rp)", "Fisik (%)", "Progress (%)", "Keterangan"])
    ws.merge_cells("A3:A4")
    ws.merge_cells("B3:B4")
    ws.merge_cells("C3:D3")
    ws.merge_cells("E3:F3")

    ws.append(["RSUD Eka Candrarini", "Belanja Obat", 50000000, "14,042.62", "102,72", "99.46 (239.63)"])
    ws.append(["Dinas Kesehatan", "Vaksin", 120000000, "95.5", "88,10", "normal"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cleanup_test_presets(filename: str) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM data_engine.silver_mapping_presets WHERE filename = %s",
                (filename,),
            )
        conn.commit()


def test_merged_header_and_silver_confirm_flow():
    excel_bytes = _make_merged_header_workbook()
    filename = "__phase8_merged_test.xlsx"
    _cleanup_test_presets(filename)

    with TestClient(app) as client:
        r_up = client.post(
            "/files/upload",
            files=[("files", (filename, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        assert r_up.status_code == 202
        file_id = r_up.json()["items"][0]["file_id"]

        try:
            detail = None
            for _ in range(30):
                detail = client.get(f"/files/{file_id}").json()
                if detail["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.25)

            assert detail["status"] == "ready", detail
            assert detail["review_status"] == "needs_review"
            table_name = detail["silver_tables"][0]

            # Verify merged headers coalesced cleanly and locale numbers parsed to DOUBLE
            prev = client.get(f"/tables/{table_name}/preview").json()
            col_map = {c["name"]: c["type"] for c in prev["columns"]}
            assert "perangkat_daerah" in col_map
            assert "nama_pekerjaan" in col_map
            assert "rencana_alokasi_rp" in col_map
            assert col_map["rencana_fisik_pct"] == "DOUBLE"
            assert col_map["realisasi_progress_pct"] == "DOUBLE"
            assert col_map["realisasi_keterangan"] == "VARCHAR"
            assert abs(prev["rows"][0]["rencana_fisik_pct"] - 14042.62) < 1e-3
            assert abs(prev["rows"][0]["realisasi_progress_pct"] - 102.72) < 1e-3

            # Raw preview check
            raw = client.get(f"/files/{file_id}/raw-preview").json()
            assert raw["sheets"][0]["detected_header_rows"] == [2, 3]
            assert len(raw["sheets"][0]["rows"]) == 6

            # Confirm with column rename & drop
            cols_payload = [
                {
                    "index": c["index"],
                    "name": "skpd_name" if c["name"] == "perangkat_daerah" else c["name"],
                    "type": c["type"],
                    "keep": c["name"] != "realisasi_keterangan",
                }
                for c in prev["columns"]
            ]
            r_conf = client.post(
                f"/files/{file_id}/confirm-silver",
                json={
                    "sheet_overrides": [
                        {
                            "sheet_name": "Monitoring",
                            "header_rows": [2, 3],
                            "columns": cols_payload,
                        }
                    ]
                },
            )
            assert r_conf.status_code == 200, r_conf.text
            assert r_conf.json()["review_status"] == "verified"

            prev_after = client.get(f"/tables/{table_name}/preview").json()
            names_after = [c["name"] for c in prev_after["columns"]]
            assert "skpd_name" in names_after
            assert "realisasi_keterangan" not in names_after
            assert prev_after["review_status"] == "verified"

            # Re-ingest should auto-apply saved preset and keep verified
            r_re = client.post(f"/files/{file_id}/reingest")
            assert r_re.status_code == 202
            for _ in range(30):
                d2 = client.get(f"/files/{file_id}").json()
                if d2["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.25)
            assert d2["review_status"] == "verified"
            prev_re = client.get(f"/tables/{table_name}/preview").json()
            assert any(c["name"] == "skpd_name" for c in prev_re["columns"])
        finally:
            client.delete(f"/files/{file_id}")
            _cleanup_test_presets(filename)

