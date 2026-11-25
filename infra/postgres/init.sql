-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Logical service schemas
CREATE SCHEMA IF NOT EXISTS data_engine;
CREATE SCHEMA IF NOT EXISTS rag;

-- ============================================================================
-- Schema: data_engine (owned by data-engine service)
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_engine.file_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK (file_type IN ('excel', 'pdf')),
    file_hash TEXT NOT NULL,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    bronze_uri TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'bronze', 'silver', 'gold', 'ready', 'failed')),
    error_message TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_file_registry_hash
    ON data_engine.file_registry (file_hash);

CREATE INDEX IF NOT EXISTS idx_file_registry_filename
    ON data_engine.file_registry (filename);

CREATE TABLE IF NOT EXISTS data_engine.silver_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file_id UUID NOT NULL REFERENCES data_engine.file_registry(id) ON DELETE CASCADE,
    table_name TEXT UNIQUE NOT NULL,
    original_filename TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    delta_uri TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    columns_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    sample_rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_silver_tables_source_file
    ON data_engine.silver_tables (source_file_id);

-- ============================================================================
-- Schema: rag (document_chunks synced by data-engine; conversations by rag-engine)
-- ============================================================================

CREATE TABLE IF NOT EXISTS rag.document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file_id UUID NOT NULL REFERENCES data_engine.file_registry(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    page_number INTEGER,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_source_file
    ON rag.document_chunks (source_file_id);

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON rag.document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS rag.conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL DEFAULT 'New Conversation',
    model_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES rag.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model_id TEXT,
    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON rag.messages (conversation_id, created_at);
