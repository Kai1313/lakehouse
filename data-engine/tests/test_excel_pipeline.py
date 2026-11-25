import io
import time

import duckdb
import openpyxl
from fastapi.testclient import TestClient

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
