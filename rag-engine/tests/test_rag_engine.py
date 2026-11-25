import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.tools.sql_query import execute_silver_sql, validate_readonly_sql
from app.tools.vector_search import search_documents


def test_sql_validator():
    assert "LIMIT 100" in validate_readonly_sql("SELECT * FROM my_table;")
    assert "LIMIT 100" in validate_readonly_sql("WITH cte AS (SELECT 1) SELECT * FROM cte")
    assert validate_readonly_sql("SELECT * FROM t LIMIT 15") == "SELECT * FROM t LIMIT 15"
    assert "LIMIT 100" in validate_readonly_sql("SELECT * FROM t LIMIT 500")

    # String literals containing SQL keywords should be allowed
    safe_str = validate_readonly_sql("SELECT * FROM t WHERE status = 'DELETE'")
    assert "WHERE status = 'DELETE'" in safe_str

    for bad_sql in [
        "",
        "DROP TABLE t",
        "DELETE FROM t WHERE id = 1",
        "INSERT INTO t VALUES (1)",
        "SELECT 1; DROP TABLE t",
        "ATTACH 'foo.db'",
        "SELECT * FROM delta_scan('s3://lakehouse/silver/tables/foo')",
    ]:
        with pytest.raises(ValueError):
            validate_readonly_sql(bad_sql)


def test_health_models_and_conversations_crud():
    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json() == {"status": "ok", "postgres": True, "duckdb": True}

        r_models = client.get("/models")
        assert r_models.status_code == 200
        models_data = r_models.json()
        assert len(models_data["models"]) >= 5

        r_create = client.post("/conversations", json={"title": "__phase5_test_conv"})
        assert r_create.status_code == 201
        conv_id = r_create.json()["id"]

        r_get = client.get(f"/conversations/{conv_id}")
        assert r_get.status_code == 200
        assert r_get.json()["title"] == "__phase5_test_conv"

        r_del = client.delete(f"/conversations/{conv_id}")
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True


def test_live_tools_and_streaming_chat():
    tables = db.list_silver_tables()
    if tables:
        tname = tables[0]["table_name"]
        sql_res = execute_silver_sql(f'SELECT COUNT(*) AS total_rows FROM "{tname}"', "Count rows")
        assert "error" not in sql_res, sql_res
        assert sql_res["rows"][0]["total_rows"] == tables[0]["row_count"]

    pdfs = db.list_ready_pdf_files()
    if pdfs:
        vec_res = search_documents("syarat permohonan atau ringkasan dokumen", top_k=3)
        assert "error" not in vec_res
        assert vec_res["count"] >= 1

    with TestClient(app) as client:
        models_resp = client.get("/models").json()
        configured_models = [m["id"] for m in models_resp["models"] if m["configured"]]
        if not configured_models:
            pytest.skip("No configured LLM API keys in .env")

        # Create a temporary conversation and test up to 2 configured models (e.g., DeepSeek + Gemini)
        conv_id = client.post("/conversations", json={"title": "New Conversation"}).json()["id"]
        try:
            for model_id in configured_models[:2]:
                prompt = (
                    f'How many rows are in table "{tables[0]["table_name"]}"?'
                    if tables
                    else "Summarize what documents are in the Lakehouse."
                )
                with client.stream(
                    "POST",
                    f"/conversations/{conv_id}/chat",
                    json={"message": prompt, "model_id": model_id},
                ) as resp:
                    assert resp.status_code == 200
                    body = resp.read().decode("utf-8")
                    assert "event: done" in body, f"Missing done event for {model_id}: {body}"
                    assert "event: error" not in body, f"Error event for {model_id}: {body}"

            conv_detail = client.get(f"/conversations/{conv_id}").json()
            assert len(conv_detail["messages"]) >= 2
            assert conv_detail["title"] != "New Conversation"
        finally:
            client.delete(f"/conversations/{conv_id}")
