export type FileStatus = "queued" | "bronze" | "silver" | "gold" | "ready" | "failed";
export type FileType = "excel" | "pdf";

export interface HealthStatus {
  status: string;
  postgres: boolean;
  minio: boolean;
}

export interface FileMetadata {
  sheets_count?: number;
  total_rows?: number;
  tables?: string[];
  page_count?: number;
  tables_extracted?: number;
  scanned_pages?: number[];
  needs_ocr?: boolean;
  chunk_count?: number;
}

export interface FileRecord {
  id: string;
  filename: string;
  file_type: FileType;
  file_hash: string;
  file_size_bytes: number;
  bronze_uri: string;
  status: FileStatus;
  review_status: "needs_review" | "verified";
  document_note: string;
  error_message: string | null;
  metadata_json: FileMetadata;
  silver_tables: string[];
  created_at: string;
  updated_at: string;
}

export interface UploadItemResult {
  file_id: string;
  filename: string;
  file_type: FileType;
  file_hash: string;
  status: FileStatus;
  action: "queued" | "replaced" | "skipped_duplicate";
  bronze_uri: string;
}

export interface UploadResponse {
  items: UploadItemResult[];
}

export interface SilverColumnInfo {
  index?: number | null;
  name: string;
  original_name: string;
  type: string;
  keep?: boolean;
}

export interface SilverTableInfo {
  id: string;
  source_file_id: string;
  table_name: string;
  original_filename: string;
  sheet_name: string;
  delta_uri: string;
  row_count: number;
  review_status: string;
  header_rows_json: number[];
  mapping_override_json?: Record<string, unknown> | null;
  columns_json: SilverColumnInfo[];
  sample_rows_json: Record<string, unknown>[];
  created_at: string;
}

export interface TablePreviewResponse {
  table_name: string;
  row_count: number;
  review_status: string;
  columns: SilverColumnInfo[];
  rows: Record<string, unknown>[];
}

export interface RawSheetPreview {
  sheet_name: string;
  total_raw_rows: number;
  rows: unknown[][];
  detected_header_rows: number[];
  table_name: string | null;
  review_status: string;
  columns: SilverColumnInfo[];
}

export interface FileRawPreviewResponse {
  file_id: string;
  filename: string;
  file_type: FileType;
  review_status: "needs_review" | "verified";
  document_note: string;
  sheets: RawSheetPreview[];
}

export interface ColumnMappingOverride {
  index: number;
  name: string;
  type: "VARCHAR" | "BIGINT" | "DOUBLE" | "BOOLEAN";
  keep: boolean;
}

export interface SheetMappingOverride {
  sheet_name: string;
  header_rows: number[];
  columns: ColumnMappingOverride[];
}

export interface ConfirmSilverRequest {
  document_note?: string | null;
  sheet_overrides?: SheetMappingOverride[] | null;
}

export interface DocumentChunkInfo {
  id: string;
  source_file_id: string;
  filename: string;
  page_number: number | null;
  chunk_index: number;
  content: string;
  metadata_json: {
    has_tables?: boolean;
    needs_ocr?: boolean;
  };
  created_at: string;
}

export interface FileChunksResponse {
  file_id: string;
  filename: string;
  total_chunks: number;
  chunks: DocumentChunkInfo[];
}

const BASE = "/api/data";

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function getHealth(): Promise<HealthStatus> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listFiles(): Promise<FileRecord[]> {
  const res = await fetch(`${BASE}/files`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function uploadFiles(files: File[]): Promise<UploadResponse> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const res = await fetch(`${BASE}/files/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function reingestFile(fileId: string): Promise<FileRecord> {
  const res = await fetch(`${BASE}/files/${fileId}/reingest`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteFile(fileId: string): Promise<{ deleted: boolean; file_id: string; filename: string }> {
  const res = await fetch(`${BASE}/files/${fileId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listTables(): Promise<SilverTableInfo[]> {
  const res = await fetch(`${BASE}/tables`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function previewTable(tableName: string, limit = 20): Promise<TablePreviewResponse> {
  const res = await fetch(`${BASE}/tables/${encodeURIComponent(tableName)}/preview?limit=${limit}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getFileChunks(
  fileId: string,
  options?: { limit?: number; page?: number },
): Promise<FileChunksResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 200));
  if (options?.page !== undefined) {
    params.set("page", String(options.page));
  }
  const res = await fetch(`${BASE}/files/${fileId}/chunks?${params.toString()}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getRawPreview(fileId: string, rows = 15): Promise<FileRawPreviewResponse> {
  const res = await fetch(`${BASE}/files/${fileId}/raw-preview?rows=${rows}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function confirmSilver(
  fileId: string,
  payload: ConfirmSilverRequest,
): Promise<FileRecord> {
  const res = await fetch(`${BASE}/files/${fileId}/confirm-silver`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
