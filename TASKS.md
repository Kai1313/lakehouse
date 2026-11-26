# Implementation Phases & Task Checklist

> **Companion document to [`SPECS.md`](./SPECS.md)**.
> Work **one phase at a time**. At the end of each phase, stop for the **Test & Review Checkpoint** so the user can test with real data (the 2 Excel + 5 PDF starter files) and request revisions before proceeding.

---

## Phase 1 — Infrastructure & Lakehouse Foundation
**Goal**: Stand up MinIO (S3 object storage), PostgreSQL (`pgvector`), Nginx routing config, and environment templates.

### Tasks
- [x] **1.1** Create `.env.example` (and local `.env` template instructions) covering Postgres, MinIO, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, and all dynamic Chat LLM keys (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `OLLAMA_API_BASE`).
- [x] **1.2** Create `infra/postgres/init.sql` to enable `vector` (`pgvector`) extension and create `data_engine` and `rag` schemas with all tables (`file_registry`, `silver_tables`, `document_chunks` + HNSW index, `conversations`, `messages`).
- [x] **1.3** Create `infra/nginx/default.conf` routing `/` $\rightarrow$ `ui`, `/api/data/*` $\rightarrow$ `data-engine:8001`, and `/api/rag/*` $\rightarrow$ `rag-engine:8002` (with SSE proxy buffering disabled).
- [x] **1.4** Create `docker-compose.yml` supporting both:
  - Infra-only local dev (`docker compose up minio postgres minio-init`)
  - Full containerized stack (`docker compose --profile full up --build`).
- [x] **1.5** Add MinIO bucket auto-creation (`minio-init` service creating `lakehouse` bucket).

### Phase 1 Test & Review Checkpoint
- Run `docker compose up -d minio postgres minio-init`.
- Verify MinIO Console (`http://localhost:9001`) shows the `lakehouse` bucket.
- Verify Postgres has `pgvector` enabled and both `data_engine` and `rag` tables created.
- **Revise if needed**: Confirm port bindings (`9000`, `9001`, `5432`, `3000`), embedding dimension (`768` vs `1536`), and `.env` variable names.

---

## Phase 2 — Data Management Engine (`data-engine/`): Core & Excel Lakehouse
**Goal**: Build the standalone FastAPI + `uv` Data Management service with Bronze uploads, SHA-256 deduplication, async background jobs, and Excel $\rightarrow$ Silver Delta Lake ingestion.

### Tasks
- [x] **2.1** Initialize `data-engine/` (`pyproject.toml` with `uv`, FastAPI, `deltalake`, `boto3`, `psycopg`, `pandas`, `openpyxl`, `duckdb`, `pydantic-settings`).
- [x] **2.2** Implement `app/config.py`, `app/db.py`, and `app/storage.py` (MinIO S3 upload/delete + `delta-rs` S3 storage options).
- [x] **2.3** Implement `POST /files/upload` with SHA-256 hash calculation, duplicate detection (skip if `ready` with same hash; replace cleanly if same filename + new hash), Bronze upload (`s3://lakehouse/bronze/<file_id>/<filename>`), and `202 Accepted` async background processing.
- [x] **2.4** Implement `app/parsers/excel_parser.py`:
  - Multi-sheet reading (`.xlsx`/`.xls`).
  - Auto header-row detection (skips title banner rows).
  - SQL-safe `snake_case` column normalization + original name preservation.
  - Writes each sheet as a Delta Lake table to `s3://lakehouse/silver/tables/<table_name>/`.
  - Records schema + row count + 3 sample rows in `data_engine.silver_tables`.
- [x] **2.5** Implement management endpoints: `GET /health`, `GET /files`, `GET /files/{file_id}`, `GET /tables`, `GET /tables/{table_name}/preview`, `POST /files/{file_id}/reingest`, `DELETE /files/{file_id}` (cascades Bronze, Silver Delta files, and Postgres rows).

### Phase 2 Test & Review Checkpoint
- Run `cd data-engine && uv run uvicorn app.main:app --reload --port 8001`.
- Open `http://localhost:8001/docs` and upload your **2 starter Excel files**.
- Poll `GET /files` until status is `ready`.
- Call `GET /tables` and `GET /tables/{table_name}/preview` to inspect how sheets and columns were normalized.
- Check MinIO Console to confirm `bronze/` raw files and `silver/tables/<name>/_delta_log/` + `.parquet` exist.
- Test re-uploading the same Excel file (verify hash skip) and deleting/re-ingesting a test file.
- **Revise if needed**: Adjust header detection heuristics, column naming rules, or table naming before adding PDFs.

---

## Phase 3 — Data Management Engine (`data-engine/`): PDF Parsing & External Embeddings
**Goal**: Extend `data-engine/` to parse PDFs into Silver/Gold Delta tables and sync vector embeddings to `pgvector` using external AI APIs via LiteLLM.

### Tasks
- [x] **3.1** Add `pymupdf`, `pdfplumber`, and `litellm` to `data-engine/pyproject.toml`.
- [x] **3.2** Implement `app/parsers/pdf_parser.py`:
  - Page-by-page text extraction (`PyMuPDF`) + embedded table extraction (`pdfplumber` formatted as Markdown tables).
  - Blank-page / scan detection hook (`needs_ocr: true` warning in `metadata_json` if a page has images with `< 30` chars of text).
  - Write extracted pages to `s3://lakehouse/silver/documents/` Delta table (replacing prior rows for `source_file_id` on re-ingest).
- [x] **3.3** Implement chunking (overlapping page-aware chunks) and write to `s3://lakehouse/gold/document_chunks/` Delta table.
- [x] **3.4** Implement `app/parsers/embedder.py` using `litellm.embedding(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIM, ...)` with batching and retry/backoff; insert vectors into `rag.document_chunks` (`pgvector`).
- [x] **3.5** Wire PDF branch into `ingest_pipeline.py` and add `GET /files/{file_id}/chunks` inspector endpoint (plus cleanup of `silver/documents`, `gold/document_chunks`, and `rag.document_chunks` in `DELETE /files/{file_id}`).

### Phase 3 Test & Review Checkpoint
- Upload your **5 starter PDF files** via `http://localhost:8001/docs`.
- Inspect `GET /files` for `ready` status and check `metadata_json` for page counts, chunk counts, or any `needs_ocr` scan warnings.
- Call `GET /files/{file_id}/chunks` to verify text quality, table formatting, and page numbers.
- Confirm `rag.document_chunks` in Postgres has populated vector rows.
- **Revise if needed**: Tune chunk size/overlap, table extraction formatting, or handle any scanned PDFs if warnings appeared.

---

## Phase 4 — Frontend UI (`ui/`): Scaffold & Lakehouse Data Management
**Goal**: Build the decoupled Vite + React + TypeScript + Tailwind SPA shell (`Data Lakehouse` active + `AI Chat` placeholder tab) and full Lakehouse Data Management & Inspector UI so you can visually upload and verify your **2 Excel + 5 PDF starter files** before building `rag-engine/`.

### Tasks
- [x] **4.1** Bump `data-engine` `GET /files/{file_id}/chunks` to support optional `page` filter and `limit <= 500` (with test coverage).
- [x] **4.2** Scaffold `ui/` (`Vite` + `React` + `TypeScript` + `Tailwind CSS` + `lucide-react`), configure `vite.config.ts` dev proxies (`/api/data` $\rightarrow$ `:8001`, `/api/rag` $\rightarrow$ `:8002`), and create typed `src/api/dataClient.ts`.
- [x] **4.3** Build dual-tab application shell (`Data Lakehouse` active + `AI Chat` "Coming in Phase 6" placeholder).
- [x] **4.4** Build **Lakehouse Data Management & Medallion Inspector**:
  - Top KPI summary bar (Files Ready/Processing/Failed, Silver Tables, Total PDF Chunks, Scan/OCR Warnings).
  - Drag-and-drop multi-file uploader (`.xlsx`, `.xls`, `.pdf`) + auto-polling file registry (`queued -> bronze -> silver -> gold -> ready / failed`), SHA-256 prefix, `Re-ingest`, and `Delete` with confirmation dialog.
  - **Silver Tables (Excel) Inspector**: sheet table selector, column schema badges (`name`, `original_name`, `type`), and scrollable data preview table (`20 / 50 / 100` rows).
  - **PDF Chunks Inspector**: page filter, chunk search filter, `has_tables` / `needs_ocr` badges, and full chunk content viewer.

### Phase 4 Test & Review Checkpoint
- Run `data-engine` (`uv run uvicorn app.main:app --reload --port 8001`) and `ui` (`npm run dev`).
- Open `http://localhost:5173` and upload your **2 Excel + 5 PDF starter files** via drag-and-drop.
- Watch live status transitions to `ready`, preview every normalized Silver Excel table, and inspect PDF chunks & scan warnings.
- **Revise if needed**: Tweak Excel header/column normalization, PDF chunking, or inspector UX before moving to `rag-engine/`.

---

## Phase 5 — RAG & Dynamic AI Engine (`rag-engine/`)
**Goal**: Build the standalone FastAPI + `uv` RAG engine with LiteLLM dynamic model switching, Hybrid RAG + DuckDB Text-to-SQL tool calling, SSE streaming, and persisted conversations.

### Tasks
- [x] **5.1** Initialize `rag-engine/` (`pyproject.toml` with `uv`, FastAPI, `litellm`, `duckdb`, `psycopg`, `pydantic-settings`, `pyyaml`).
- [x] **5.2** Create `models.yaml` registry (Claude, DeepSeek, Google Gemini, OpenAI, Groq, Ollama) and `GET /models` inspecting `.env` API keys to report configured vs. unconfigured models.
- [x] **5.3** Implement Postgres persistence endpoints: `GET /conversations`, `POST /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}`.
- [x] **5.4** Implement `app/tools/vector_search.py` (`search_documents` using `litellm.embedding` + `pgvector` cosine similarity returning filename, page, score, content).
- [x] **5.5** Implement `app/tools/sql_query.py` (`query_tabular_data` using read-only DuckDB + `delta` + `httpfs` against MinIO Silver tables, registering views from `data_engine.silver_tables`):
  - Strict SQL validator (`SELECT` / `WITH` only, block DDL/DML/multiple statements, enforce `LIMIT 100`).
- [x] **5.6** Implement `app/prompts.py` (domain-agnostic grounded system prompt + live Silver schema/sample-row catalog injection) and `app/llm_router.py` (LiteLLM async streaming + tool-calling loop with max 2 SQL self-correction retries).
- [x] **5.7** Implement `POST /conversations/{id}/chat` SSE endpoint emitting `status`, `tool_call`, `tool_result`, `token`, `citations`, `done`, and `error` events, saving assistant messages and `citations_json` to Postgres.

### Phase 5 Test & Review Checkpoint
- Run `cd rag-engine && uv run uvicorn app.main:app --reload --port 8002`.
- Verify `GET /models` accurately detects your configured `.env` keys (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, etc.).
- Test 3 question types against your 2 Excel + 5 PDF files (via Swagger / `curl -N`):
  1. **PDF-only semantic question** (verify `search_documents` + filename/page citations).
  2. **Excel calculation/aggregation question** (verify `query_tabular_data` + DuckDB SQL + accurate numbers).
  3. **Cross-source question** (uses both PDF context and Excel SQL).
- Switch `model_id` across at least **2 providers** (e.g., DeepSeek $\leftrightarrow$ Gemini) in the same conversation and verify tool calling & SSE streaming work consistently.
- **Revise if needed**: Tune system prompt, SQL retry behavior, or citation payload shape.

---

## Phase 6 — Frontend UI (`ui/`): Chat Workspace
**Goal**: Replace the `AI Chat` placeholder tab with the full streaming Chat Workspace connected to `rag-engine/`.

### Tasks
- [x] **6.1** Create typed `src/api/ragClient.ts` for conversations, models, and SSE chat streaming.
- [x] **6.2** Build **Chat Workspace**:
  - Conversation sidebar (create, switch, delete threads).
  - Top bar **AI Model Switcher dropdown** (grouped/badged by provider, disabling unconfigured models).
  - SSE streaming message renderer (Markdown + tables + live tool status indicator).
  - Expandable **Citations Cards** showing PDF filename/page snippets and executed DuckDB SQL + result table preview.

### Phase 6 Test & Review Checkpoint
- Run `ui`, `data-engine`, `rag-engine`, `minio`, and `postgres`.
- Ask questions in the browser across your 2 Excel + 5 PDF files, switch AI models mid-conversation, inspect SQL/PDF citations, and refresh the browser to confirm history persists.
- **Revise if needed**: Polish chat UX, error states, or citation layout before containerizing.

---

## Phase 7 — Full Docker Integration & Final Verification
**Goal**: Containerize `data-engine`, `rag-engine`, and `ui`, verify full `docker compose` startup behind Nginx, and document usage.

### Tasks
- [x] **7.1** Create production `Dockerfile`s for `data-engine/`, `rag-engine/`, and `ui/`.
- [x] **7.2** Verify `docker compose --profile full up --build` starts all services (`minio`, `postgres`, `data-engine`, `rag-engine`, `ui`, `nginx`) cleanly with health checks.
- [x] **7.3** Create `README.md` with setup instructions for both full Docker mode and local hot-reload dev mode.
- [x] **7.4** Run end-to-end smoke check through Nginx (`http://localhost:3000`): upload $\rightarrow$ inspect $\rightarrow$ multi-model chat $\rightarrow$ delete.

### Phase 7 Test & Review Checkpoint
- Run `docker compose --profile full up --build` from a clean state.
- Open `http://localhost:3000` and confirm all features work seamlessly through the single-origin Nginx proxy.
