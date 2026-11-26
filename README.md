# Lakehouse RAG Studio

A decoupled **Hybrid RAG + Text-to-SQL AI Chatbot** backed by a **Medallion Data Lakehouse** (`Bronze -> Silver -> Gold`) with dynamic multi-provider Chat LLM switching.

## Architecture

- **`ui/`** (`Vite` + `React` + `TypeScript` + `Tailwind`) — Dual-tab workspace:
  - **Data Lakehouse**: Drag-and-drop uploader (`.xlsx`, `.xls`, `.pdf`), live async pipeline status, Silver Excel Delta table inspector, and Gold PDF chunk viewer.
  - **AI Chat**: Persisted conversation threads, live AI model dropdown, SSE token streaming, and expandable PDF + DuckDB SQL citation cards.
- **`data-engine/`** (`FastAPI` + `uv` + `deltalake` + `PyMuPDF`/`pdfplumber` + `litellm`) — Handles Bronze MinIO uploads, SHA-256 deduplication, Excel $\rightarrow$ Silver Delta tables, PDF extraction $\rightarrow$ Gold chunks, and `pgvector` embedding sync.
- **`rag-engine/`** (`FastAPI` + `uv` + `litellm` + `duckdb` + `psycopg`) — Dynamic LLM switching (DeepSeek, Gemini, Claude, OpenAI, Groq, Ollama), `pgvector` semantic search (`search_documents`), and read-only DuckDB SQL (`query_tabular_data`) against Silver Delta tables in MinIO.
- **`infra/`** — PostgreSQL (`pgvector/pgvector:pg16`), MinIO (`pgsty/silo` S3 storage), and Nginx reverse proxy (`/api/data/*`, `/api/rag/*`, `/`).

---

## Setup

1. **Create `.env` from template**:
   ```bash
   cp .env.example .env
   ```
2. **Configure API keys in `.env`**:
   - Set an embedding-capable API key for PDF vectorization (`GEMINI_API_KEY` for default `gemini/gemini-embedding-001`, or `OPENAI_API_KEY`).
   - Add one or more Chat LLM keys (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, or run Ollama locally).

---

## Option 1: Full Docker Compose Mode (Recommended for Demo)

Builds and starts all containers behind the Nginx single-origin proxy (`http://localhost:3100`, configurable via `APP_PORT` in `.env`):

```bash
docker compose --profile full up --build -d
```

- **App (via Nginx)**: http://localhost:3100 (`APP_PORT`)
- **MinIO Console**: http://localhost:9001 (`MINIO_CONSOLE_PORT`, `minioadmin` / `minioadmin`)
- **Data Engine Swagger**: http://localhost:8001/docs (`DATA_ENGINE_PORT`)
- **RAG Engine Swagger**: http://localhost:8002/docs (`RAG_ENGINE_PORT`)
- **PostgreSQL**: `localhost:5435` (`POSTGRES_PORT`)

Check status:
```bash
docker compose --profile full ps
```

Stop containers (preserving volumes):
```bash
docker compose --profile full down
```

---

## Option 2: Local Hot-Reload Dev Mode

Run only MinIO and PostgreSQL in Docker:
```bash
docker compose up -d
```

Then run each service locally in its own terminal:

```bash
# Terminal 1: Data Management Engine (:8001)
cd data-engine
uv sync --all-groups
uv run uvicorn app.main:app --reload --port 8001

# Terminal 2: RAG & Dynamic AI Engine (:8002)
cd rag-engine
uv sync --all-groups
uv run uvicorn app.main:app --reload --port 8002

# Terminal 3: Frontend UI (:5173)
cd ui
npm install
npm run dev
```

Open **http://localhost:5173**.

---

## Running Automated Tests

```bash
# Data Engine tests (Excel Delta + PDF pgvector e2e)
cd data-engine && uv run pytest -v

# RAG Engine tests (SQL guardrails + DuckDB/pgvector + SSE streaming)
cd rag-engine && uv run pytest -v
```
