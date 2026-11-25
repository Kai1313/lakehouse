import time

import fitz
from deltalake import DeltaTable
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app


def _make_sample_pdf() -> bytes:
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        (
            "Lakehouse Architecture Technical Policy\n\n"
            "Section 1: Overview\n"
            "All incoming building permit documents must be stored in the Bronze layer "
            "before parsing into Silver Delta tables and Gold vector chunks."
        ),
    )
    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        (
            "Section 2: Processing Rules\n"
            "PDF documents are extracted page by page and embedded with Gemini embeddings "
            "into PostgreSQL pgvector for fast semantic retrieval."
        ),
    )
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_pdf_lakehouse_e2e():
    pdf_bytes = _make_sample_pdf()
    filename = "__phase3_smoke_test.pdf"

    with TestClient(app) as client:
        r_up = client.post(
            "/files/upload",
            files=[("files", (filename, pdf_bytes, "application/pdf"))],
        )
        assert r_up.status_code == 202, r_up.text
        item = r_up.json()["items"][0]
        file_id = item["file_id"]

        detail = None
        for _ in range(40):
            r_get = client.get(f"/files/{file_id}")
            assert r_get.status_code == 200
            detail = r_get.json()
            if detail["status"] in {"ready", "failed"}:
                break
            time.sleep(0.5)

        assert detail["status"] == "ready", f"PDF ingestion failed: {detail}"
        assert detail["metadata_json"]["page_count"] == 2
        assert detail["metadata_json"]["chunk_count"] >= 2

        # Verify chunks inspector endpoint (all + page filter up to 500)
        r_chunks = client.get(f"/files/{file_id}/chunks?limit=500")
        assert r_chunks.status_code == 200
        chunks_data = r_chunks.json()
        assert chunks_data["total_chunks"] >= 2
        assert any("Bronze layer" in c["content"] for c in chunks_data["chunks"])

        r_page2 = client.get(f"/files/{file_id}/chunks?page=2&limit=500")
        assert r_page2.status_code == 200
        page2_data = r_page2.json()
        assert page2_data["total_chunks"] >= 1
        assert all(c["page_number"] == 2 for c in page2_data["chunks"])

        # Verify pgvector stored exact 768-dim vectors
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vector_dims(embedding) AS dims FROM rag.document_chunks WHERE source_file_id = %s LIMIT 1",
                    (file_id,),
                )
                row = cur.fetchone()
                assert row is not None
                assert row["dims"] == settings.embedding_dim

        # Verify Silver & Gold Delta tables in MinIO contain rows for this file_id
        silver_dt = DeltaTable(
            f"s3://{settings.minio_bucket}/silver/documents",
            storage_options=settings.delta_storage_options,
        )
        silver_rows = silver_dt.to_pyarrow_table(
            filters=[("source_file_id", "=", file_id)]
        ).to_pylist()
        assert len(silver_rows) == 2

        # Clean delete & verify cascade cleanup across Postgres + Delta
        r_del = client.delete(f"/files/{file_id}")
        assert r_del.status_code == 200

        total_after, _ = db.get_document_chunks(file_id)
        assert total_after == 0

        silver_dt_after = DeltaTable(
            f"s3://{settings.minio_bucket}/silver/documents",
            storage_options=settings.delta_storage_options,
        )
        silver_rows_after = silver_dt_after.to_pyarrow_table(
            filters=[("source_file_id", "=", file_id)]
        ).to_pylist()
        assert len(silver_rows_after) == 0
