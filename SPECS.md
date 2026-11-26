# Lakehouse Hybrid RAG Chatbot — Architecture & Implementation Specification

> **Purpose of this document**: Complete, self-contained specification produced from the architecture design session. A new AI coding session can read this file and immediately scaffold, implement, and verify the entire project without re-asking foundational questions.

---

## 1. High-Level Goals & Settled Decisions

- **Goal**: Build a proper, clean prototype of a Hybrid RAG + Text-to-SQL AI Chatbot backed by a **Medallion Data Lakehouse** (`Bronze -> Silver -> Gold`).
- **Initial Dataset Target**: Starter set of **2 Excel files** (`.xlsx`/`.xls`) and **5 PDF files** (`.pdf`), uploaded via the UI, with support for arbitrary future uploads and incremental updates.
- **Strict Separation of Concerns**:
  1. **`ui/`** — Standalone SPA frontend (Vite + React + TypeScript + Tailwind).
  2. **`data-engine/`** — Standalone Data Management & Lakehouse Ingestion service (FastAPI + `uv`).
  3. **`rag-engine/`** — Standalone AI Chat & Hybrid Retrieval service (FastAPI + `uv`).
- **Dynamic AI Provider Switching**: Runtime UI dropdown lets the user switch the **Chat/Reasoning LLM** dynamically per request/conversation across **Anthropic (Claude), DeepSeek, Google AI (Gemini), OpenAI, Groq, and Ollama**, powered by **LiteLLM** and `.env` API keys.
- **External Embeddings**: Uses an external embedding API via `litellm.embedding(...)` (defaulting to **Google AI / `GEMINI_API_KEY`**), kept consistent across `data-engine` (ingestion) and `rag-engine` (query) so vector dimensions match. *(Note: Claude and DeepSeek do not provide native embedding endpoints, so embedding model selection is decoupled from the Chat LLM dropdown.)*
- **Auth & Persistence**: Single-user workspace (no login screen). Conversations, messages, model selections, file registry, and vector chunks persist in PostgreSQL.

---

## 2. Repository Layout

```text
lake/
├── SPECS.md                        # This specification
├── .env.example                    # All provider keys, DB, MinIO, and embedding config
├── docker-compose.yml              # Full stack + "infra" profile (minio, postgres) for local dev
├── infra/
│   ├── nginx/
│   │   └── default.conf            # Reverse proxy (/ -> ui, /api/data/* -> data-engine, /api/rag/* -> rag-engine)
│   └── postgres/
│       └── init.sql                # Enables pgvector; creates schemas (data_engine, rag) and tables
├── data-engine/
│   ├── Dockerfile
│   ├── pyproject.toml              # Managed with uv
│   └── app/
│       ├── main.py                 # FastAPI app + lifespan + CORS
│       ├── config.py               # Settings from env (MinIO, Postgres, LiteLLM embedding)
│       ├── db.py                   # SQLAlchemy / psycopg pool for data_engine & rag.document_chunks sync
│       ├── storage.py              # MinIO S3 client (Bronze raw file upload/delete, Delta storage options)
│       ├── models_registry.py      # Pydantic schemas for API requests/responses
│       ├── workers/
│       │   └── ingest_pipeline.py  # Async background orchestrator (Bronze -> Silver -> Gold)
│       └── parsers/
│           ├── excel_parser.py     # Multi-sheet auto-header detection, SQL-safe column normalization, Delta write
│           ├── pdf_parser.py       # PyMuPDF + pdfplumber extraction, blank-page/scan flag, chunker
│           └── embedder.py         # Batched litellm.embedding(...) client
├── rag-engine/
│   ├── Dockerfile
│   ├── pyproject.toml              # Managed with uv
│   ├── models.yaml                 # Registry of supported Chat LLMs & required env vars
│   └── app/
│       ├── main.py                 # FastAPI app + CORS
│       ├── config.py               # Settings (Postgres, MinIO, DuckDB, LiteLLM, configurable system prompt)
│       ├── db.py                   # Conversation/message persistence + pgvector search queries
│       ├── llm_router.py           # Dynamic model discovery (GET /models) + LiteLLM streaming & tool loop
│       ├── tools/
│       │   ├── vector_search.py    # search_documents tool (pgvector cosine search + metadata filters)
│       │   └── sql_query.py        # query_tabular_data tool (read-only DuckDB over MinIO Silver Delta tables)
│       └── prompts.py              # Domain-agnostic grounded system prompt + live schema catalog formatter
└── ui/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts              # Dev proxy: /api/data -> :8001, /api/rag -> :8002
    └── src/
        ├── api/                    # Typed API clients (dataClient.ts, ragClient.ts)
        ├── components/
        │   ├── chat/               # Thread sidebar, Model selector dropdown, SSE message stream, Citation cards
        │   └── lakehouse/          # Drag-drop uploader, Bronze/Silver/Gold pipeline badges, Silver table & chunk inspector
        └── App.tsx
```

---

## 3. Infrastructure, Storage & Database Design

### 3.1 Docker Compose Services & Dev Modes
`docker-compose.yml` supports **two workflows**:
1. **Full Stack**: `docker compose up --build` runs:
   - `minio` (`:9000` API, `:9001` Console) — Lakehouse object storage (bucket: `lakehouse`).
   - `postgres` (`pgvector/pgvector:pg16`, `:5432`) — App state + `pgvector`.
   - `data-engine` (`:8001`).
   - `rag-engine` (`:8002`).
   - `ui` (static build or Vite).
   - `nginx` (`:3000` or `:80`) — Single-origin reverse proxy:
     - `/api/data/*` $\rightarrow$ `http://data-engine:8001/*` (strip `/api/data` prefix, disable buffering)
     - `/api/rag/*` $\rightarrow$ `http://rag-engine:8002/*` (strip `/api/rag` prefix, `proxy_buffering off` for SSE!)
     - `/*` $\rightarrow$ `ui`
2. **Infra-Only Local Hot-Reload Mode**: Use Docker Compose profiles (e.g., `minio` and `postgres` have no profile / always start, while app services use `profiles: ["full"]`, or `docker compose up minio postgres`) so developers can run locally on host:
   - `cd data-engine && uv run uvicorn app.main:app --reload --port 8001`
   - `cd rag-engine && uv run uvicorn app.main:app --reload --port 8002`
   - `cd ui && npm run dev` (Vite proxies `/api/data` and `/api/rag`)

### 3.2 MinIO Medallion Bucket Layout (`s3://lakehouse/`)
```text
lakehouse/
├── bronze/
│   └── <source_file_id>/<original_filename>    # Untouched raw Excel or PDF
├── silver/
│   ├── tables/
│   │   └── <sanitized_table_name>/             # Delta Lake table (_delta_log/ + *.parquet) per Excel sheet
│   └── documents/                              # Delta Lake table (_delta_log/ + *.parquet) of parsed PDF pages
└── gold/
    └── document_chunks/                        # Delta Lake table (_delta_log/ + *.parquet) of chunked text + metadata
```
- **Format**: **Delta Lake** via Python `deltalake` (`delta-rs`) writing directly to MinIO S3 (no external Hive/Iceberg catalog server needed).
- **Read Boundary**: `data-engine` owns **all writes** to MinIO and `rag.document_chunks`. `rag-engine` reads Silver Delta tables **read-only** via DuckDB (`INSTALL delta; LOAD delta; INSTALL httpfs; LOAD httpfs;`).

### 3.3 PostgreSQL Schemas (`pgvector/pgvector:pg16`)
Single Postgres database (`lakehouse_db`) with two logically separated schemas:

#### Schema `data_engine` (owned by `data-engine`, read by `rag-engine` for catalog)
1. **`data_engine.file_registry`**
   - `id` (`UUID PK`)
   - `filename` (`TEXT NOT NULL`)
   - `file_type` (`TEXT NOT NULL`: `'excel'` | `'pdf'`)
   - `file_hash` (`TEXT NOT NULL` — SHA-256 hex)
   - `file_size_bytes` (`BIGINT`)
   - `bronze_uri` (`TEXT NOT NULL` — e.g. `s3://lakehouse/bronze/<id>/<filename>`)
   - `status` (`TEXT NOT NULL`: `'queued'` | `'bronze'` | `'silver'` | `'gold'` | `'ready'` | `'failed'`)
   - `error_message` (`TEXT NULL`)
   - `metadata_json` (`JSONB DEFAULT '{}'` — sheet count, page count, scanned-page warnings, chunk count)
   - `created_at` (`TIMESTAMPTZ DEFAULT now()`)
   - `updated_at` (`TIMESTAMPTZ DEFAULT now()`)
2. **`data_engine.silver_tables`**
   - `id` (`UUID PK`)
   - `source_file_id` (`UUID REFERENCES data_engine.file_registry(id) ON DELETE CASCADE`)
   - `table_name` (`TEXT UNIQUE NOT NULL` — SQL-safe sanitized name, e.g. `sales_2026__summary`)
   - `original_filename` (`TEXT NOT NULL`)
   - `sheet_name` (`TEXT NOT NULL`)
   - `delta_uri` (`TEXT NOT NULL` — `s3://lakehouse/silver/tables/<table_name>`)
   - `row_count` (`INTEGER NOT NULL`)
   - `columns_json` (`JSONB NOT NULL` — `[{"name": "col_a", "original_name": "Col A", "type": "VARCHAR"}, ...]`)
   - `sample_rows_json` (`JSONB NOT NULL` — up to 3 sample rows for LLM schema context)
   - `created_at` (`TIMESTAMPTZ DEFAULT now()`)

#### Schema `rag` (`rag.document_chunks` written by `data-engine`, read by `rag-engine`; conversations owned by `rag-engine`)
3. **`rag.document_chunks`**
   - `id` (`UUID PK`)
   - `source_file_id` (`UUID REFERENCES data_engine.file_registry(id) ON DELETE CASCADE`)
   - `filename` (`TEXT NOT NULL`)
   - `page_number` (`INTEGER`)
   - `chunk_index` (`INTEGER`)
   - `content` (`TEXT NOT NULL`)
   - `metadata_json` (`JSONB DEFAULT '{}'`)
   - `embedding` (`vector(768)` or configured `EMBEDDING_DIM`, e.g., `768` or `1536` — **must be `<= 2000`** for `pgvector` HNSW index compatibility; use `output_dimensionality` / `dimensions` param on Gemini embeddings)
   - `created_at` (`TIMESTAMPTZ DEFAULT now()`)
   - Index: `CREATE INDEX ON rag.document_chunks USING hnsw (embedding vector_cosine_ops);`
4. **`rag.conversations`**
   - `id` (`UUID PK`)
   - `title` (`TEXT NOT NULL DEFAULT 'New Conversation'`)
   - `model_id` (`TEXT` — last selected LiteLLM model ID for this thread)
   - `created_at` (`TIMESTAMPTZ DEFAULT now()`)
   - `updated_at` (`TIMESTAMPTZ DEFAULT now()`)
5. **`rag.messages`**
   - `id` (`UUID PK`)
   - `conversation_id` (`UUID REFERENCES rag.conversations(id) ON DELETE CASCADE`)
   - `role` (`TEXT NOT NULL`: `'user'` | `'assistant'`)
   - `content` (`TEXT NOT NULL`)
   - `model_id` (`TEXT NULL`)
   - `citations_json` (`JSONB DEFAULT '[]'` — PDF citations & executed SQL queries/previews)
   - `created_at` (`TIMESTAMPTZ DEFAULT now()`)

---

## 4. Service 1: `data-engine/` Specification

### 4.1 API Endpoints
- `GET /health` $\rightarrow$ Checks Postgres + MinIO connectivity.
- `POST /files/upload` (multipart `files[]`) $\rightarrow$
  - Computes SHA-256 hash per file.
  - **Hash Deduplication**:
    - If an existing `ready` record has the exact same `file_hash`, skip re-processing and return its existing record (`deduplicated: true`).
    - If a file with the **same filename** exists with a **different hash**, reuse or replace its `source_file_id` cleanly (purging old Silver tables / Silver doc rows / `pgvector` chunks before ingesting the new version).
  - Uploads raw file to MinIO `bronze/` immediately, inserts `file_registry` row (`status='bronze'`), schedules FastAPI `BackgroundTasks` (or async worker queue) pipeline, and returns `202 Accepted` with file IDs and initial status.
- `GET /files` $\rightarrow$ Lists all files in `file_registry` with status, metadata, and associated Silver table names.
- `GET /files/{file_id}` $\rightarrow$ Detailed status + error info for polling (`queued -> bronze -> silver -> gold -> ready / failed`).
- `POST /files/{file_id}/reingest` $\rightarrow$ Re-runs Silver $\rightarrow$ Gold pipeline from the stored Bronze raw file in MinIO.
- `DELETE /files/{file_id}` $\rightarrow$ Purges raw Bronze object(s), deletes associated Silver Delta table(s) in MinIO + `silver_tables` rows, deletes `silver/documents` & `gold/document_chunks` entries for `source_file_id` (via Delta delete), and cascades deletion of `rag.document_chunks` + `file_registry`.
- `GET /tables` $\rightarrow$ Lists all Silver Delta tables from `data_engine.silver_tables` (name, source file, sheet, row count, columns).
- `GET /tables/{table_name}/preview?limit=20` $\rightarrow$ Reads first N rows from the Delta table in MinIO (via `deltalake` / DuckDB) for the UI inspector.
- `GET /files/{file_id}/chunks?limit=50` $\rightarrow$ Lists extracted chunks (`page_number`, `chunk_index`, `content` snippet) from `rag.document_chunks` for the UI inspector.

### 4.2 Excel Ingestion Pipeline (`excel_parser.py`)
- Reads all sheets from `.xlsx`/`.xls` (`openpyxl` / `pandas` / `calamine`).
- **Auto Header Detection**: Scans top ~15 rows to locate the first dense non-empty header row (handles reports with title banners above headers).
- **Column Normalization**: Sanitizes column names to unique SQL-safe `snake_case` identifiers (preserving `original_name` in `columns_json`), drops completely empty rows/columns, injects `_source_file_id` and `_ingested_at` audit columns, and infers clean types (numeric, date/timestamp, string).
- **Delta Table Naming**: Deterministic sanitized name `<sanitized_filename_stem>__<sanitized_sheet_name>` (handle collisions cleanly).
- Writes Delta table with `write_deltalake(..., mode="overwrite", schema_mode="overwrite")` to `s3://lakehouse/silver/tables/<table_name>/` and upserts metadata + 3 sample rows into `data_engine.silver_tables`.

### 4.3 PDF Ingestion Pipeline (`pdf_parser.py` & `embedder.py`)
- **Extraction**: Uses `PyMuPDF` (`fitz`) for fast page-by-page text extraction + `pdfplumber` for structured tables inside PDFs (formatted cleanly as Markdown tables inline with page text).
- **Blank-Page / Scan Detection Hook**: If a page yields `< 30` characters of extracted text while containing images/drawings, flag `needs_ocr: true` in page metadata and record a warning in `file_registry.metadata_json` (leaving a clean hook for future Vision LLM OCR fallback without blocking v1).
- **Silver Write**: Appends/replaces rows (`source_file_id`, `filename`, `page_number`, `text`, `needs_ocr`) in `s3://lakehouse/silver/documents/` Delta table.
- **Chunking & Gold**: Splits page text into overlapping chunks (e.g., ~800–1000 chars with ~150 char overlap, preserving `page_number` and `filename`). Writes chunks to `s3://lakehouse/gold/document_chunks/` Delta table.
- **External Embeddings**: Calls `litellm.embedding(model=EMBEDDING_MODEL, input=batch, dimensions=EMBEDDING_DIM)` (with batching & retry/backoff), defaulting to Google Gemini (`gemini/gemini-embedding-001` or `gemini/text-embedding-004` at `768` dimensions). Inserts vectors into `rag.document_chunks`.

---

## 5. Service 2: `rag-engine/` Specification

### 5.1 API Endpoints
- `GET /health` $\rightarrow$ Checks Postgres + MinIO/DuckDB readiness.
- `GET /models` $\rightarrow$ Reads `models.yaml` + checks which `.env` keys (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `OLLAMA_BASE_URL`) are non-empty; returns available Chat LLM options (`id`, `label`, `provider`, `configured: bool`, `supports_tools: bool`).
- `GET /conversations` $\rightarrow$ Lists persisted threads ordered by `updated_at DESC`.
- `POST /conversations` $\rightarrow$ Creates a new conversation thread.
- `GET /conversations/{id}` $\rightarrow$ Returns conversation details + full message history with `citations_json`.
- `DELETE /conversations/{id}` $\rightarrow$ Deletes conversation and messages.
- `POST /conversations/{id}/chat` $\rightarrow$ Accepts `{ "message": "...", "model_id": "..." }`, persists user message, executes the Hybrid RAG + Tool-Calling loop, streams response as **Server-Sent Events (`text/event-stream`)**:
  - `event: status` (e.g. `"Searching documents..."`, `"Running SQL query..."`)
  - `event: tool_call` / `event: tool_result` (transparent tool trace for SQL/RAG)
  - `event: token` (`{"delta": "..."}`)
  - `event: citations` (`{"pdf_citations": [...], "sql_citations": [...]}`)
  - `event: done` (`{"message_id": "..."}`)
  - `event: error` (`{"detail": "..."}`)

### 5.2 Dynamic LLM & Hybrid Retrieval Loop (`llm_router.py`)
1. **Model Validation**: Verifies the requested `model_id` is configured (key present in `.env`) and passes it to LiteLLM (`litellm.acompletion(..., stream=True, tools=...)`).
2. **Schema Catalog Injection**: Fetches all rows from `data_engine.silver_tables` (`table_name`, `original_filename`, `sheet_name`, `row_count`, `columns_json`, `sample_rows_json`) and injects a concise schema summary into the system prompt so the LLM knows every available tabular dataset before deciding which tool to invoke.
3. **Dual Tools**:
   - **`search_documents(query: str, filename: str | None = None, top_k: int = 5)`**: Embeds `query` using the configured `EMBEDDING_MODEL` (`litellm.embedding`) and runs cosine similarity (`1 - (embedding <=> query_vec)`) against `rag.document_chunks`, returning ranked chunks with `filename`, `page_number`, `score`, and `content`.
   - **`query_tabular_data(sql: str, explanation: str)`**: Executes SQL against Silver Delta tables in MinIO using an in-memory **DuckDB** connection configured with `delta` + `httpfs` (S3 creds pointing to MinIO). Registers/views every Silver table from `data_engine.silver_tables` using `CREATE OR REPLACE TEMP VIEW "<table_name>" AS SELECT * FROM delta_scan('<delta_uri>')` so the LLM can write clean standard SQL like `SELECT ... FROM sales_2026__summary`!
4. **SQL Guardrails & Self-Correction**:
   - Reject any SQL containing non-read-only keywords/statements (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `COPY`, `EXPORT`, `PRAGMA`, multiple statements separated by `;`). Allow only `SELECT` or `WITH ... SELECT`.
   - Automatically wrap or enforce `LIMIT 100` if no smaller limit is present.
   - If DuckDB raises a SQL/column error, return the error message to the LLM as the tool result so it can self-correct (capped at **max 2 SQL retries / 4 total tool rounds** per turn).
5. **System Prompt**: Domain-agnostic, configurable via `SYSTEM_PROMPT` env/file; instructs the LLM to stay strictly grounded in retrieved PDF chunks and SQL query results, cite filenames/page numbers or SQL findings clearly, never fabricate calculations, and reply in the user's language (English/Indonesian/etc.).

---

## 6. Service 3: `ui/` Specification (Vite + React + TypeScript + Tailwind)

### 6.1 Navigation & Layout
Clean two-tab or sidebar-switched workspace:
1. **Chat Workspace**:
   - Left sidebar: Conversation list (`New Chat`, thread titles, delete thread).
   - Top bar: **Dynamic AI Model Dropdown** (populated from `GET /api/rag/models`, showing provider badge e.g. Claude / DeepSeek / Gemini / OpenAI / Groq / Ollama, disabling unconfigured providers with tooltip).
   - Message stream: Markdown rendering (with code blocks & tables), live tool-execution status indicator (`Searching PDFs...`, `Querying DuckDB...`), streaming assistant tokens, and collapsible/clickable **Citations Panel** per message:
     - **Document Citations**: Filename, page number, similarity score, expandable chunk snippet.
     - **SQL Citations**: Generated DuckDB SQL query + tabular preview of returned rows.
2. **Lakehouse Data Management Panel**:
   - Drag-and-drop multi-file uploader (supports `.xlsx`, `.xls`, `.pdf` — test directly with the 2 Excel + 5 PDF starter files).
   - Live File Registry table polling `GET /api/data/files` while any file is in `queued/bronze/silver/gold` state, displaying:
     - Filename, Type, SHA-256 prefix, Status badge (`queued -> bronze -> silver -> gold -> ready / failed`), error details, `Re-ingest` button, `Delete` button.
   - **Silver Lakehouse Inspector Drawer/Tabs**:
     - **Silver Tables (Excel)**: List of Delta tables from `GET /api/data/tables`, column schema & types, row count, and live data preview (`GET /api/data/tables/{name}/preview`).
     - **PDF Chunks**: Inspect extracted chunks + page numbers + scan warnings (`GET /api/data/files/{id}/chunks`).

---

## 7. `.env.example` Contract

```env
# PostgreSQL + pgvector
POSTGRES_DB=lakehouse_db
POSTGRES_USER=lakehouse
POSTGRES_PASSWORD=lakehouse_secret
DATABASE_URL=postgresql://lakehouse:lakehouse_secret@postgres:5432/lakehouse_db

# MinIO (S3-compatible Lakehouse Storage)
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=lakehouse
AWS_REGION=us-east-1

# Fixed External Embedding Model (shared by data-engine and rag-engine)
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_DIM=768

# Dynamic Chat LLM API Keys (configure any providers you want in the UI dropdown)
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
GROQ_API_KEY=
OLLAMA_API_BASE=http://host.docker.internal:11434

# Optional System Prompt Override
DEFAULT_SYSTEM_PROMPT=
```

---

## 8. Implementation Sequence for Next Session

> See [`TASKS.md`](./TASKS.md) for the granular, 8-phase task checklist with per-phase **Test & Review Checkpoints** so you can test and revise after each phase.

When continuing in a new session, build and verify phase-by-phase following `TASKS.md`:
1. **Phase 1 — Infrastructure & Lakehouse Foundation**: `.env.example`, `infra/postgres/init.sql`, `infra/nginx/default.conf`, and `docker-compose.yml` (`minio`, `postgres`, `minio-init`, plus full stack profile).
2. **Phase 2 — `data-engine/` Core & Excel Lakehouse**: `uv` project, MinIO Bronze uploads, SHA-256 dedup, async jobs, Excel auto-header + Delta Lake Silver tables, and management/preview endpoints.
3. **Phase 3 — `data-engine/` PDF Parsing & External Embeddings**: `PyMuPDF`/`pdfplumber` parser, scan-detection hook, Silver/Gold Delta writes, and `litellm.embedding(...)` sync to `pgvector`.
4. **Phase 4 — `ui/` Scaffold & Lakehouse Data Management**: Vite + React + TypeScript + Tailwind + `lucide-react` dual-tab shell (`Data Lakehouse` active + `AI Chat` placeholder), drag-and-drop uploader, live status polling, Silver table & PDF chunk inspector, plus `/files/{id}/chunks` `page` filter & `limit <= 500`.
5. **Phase 5 — `rag-engine/`**: `uv` project, `models.yaml` + `GET /models`, conversation/message persistence, DuckDB Delta S3 reader + SQL guardrails, `pgvector` retriever, LiteLLM streaming + dual tool-calling loop, SSE endpoint.
6. **Phase 6 — `ui/` Chat Workspace**: Wire `AI Chat` tab to `rag-engine/` (threads, live model dropdown, streaming markdown, PDF/SQL citation cards).
7. **Phase 7 — Full Docker Integration & Verification**: Service `Dockerfile`s, full `docker compose --profile full up --build` behind Nginx, `README.md`, and end-to-end smoke test.
8. **Phase 8 — Smart Excel Headers & Silver HITL Review**: Multi-row merged Excel header coalescing, locale-aware numeric parsing, saved mapping presets (`data_engine.silver_mapping_presets`), `GET /files/{id}/raw-preview`, `POST /files/{id}/confirm-silver`, `POST /schema/suggest` (`rag-engine`), and inline `Review & Edit Schema` UI (`needs_review` $\rightarrow$ `verified`).

---

## 9. Tech Debt & Deferred Scope

- **TD-1 — Gold Business Glossary & KPI Rules**: User-editable column descriptions, official domain KPI formulas (e.g., authoritative Budget/Pagu vs. Realisasi/Overbudget/Serapan definitions), Gold materialized analytical views, and cross-file join specifications.
- **TD-2 — Vision/OCR Fallback for Scanned PDFs**: Automatic OCR/Vision LLM extraction for PDF pages flagged `needs_ocr: true`.
