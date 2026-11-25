import { useId } from "react";
import {
  AlertTriangle,
  Eye,
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { FileRecord, FileStatus } from "../../api/dataClient";

interface FileRegistryTableProps {
  files: FileRecord[];
  selectedFileId: string | null;
  onInspect: (file: FileRecord) => void;
  onReingest: (fileId: string) => void;
  onRequestDelete: (file: FileRecord) => void;
}

const STATUS_STYLE: Record<FileStatus, string> = {
  queued: "border-slate-700 bg-slate-800 text-slate-300",
  bronze: "border-amber-500/30 bg-amber-500/15 text-amber-300",
  silver: "border-sky-500/30 bg-sky-500/15 text-sky-300",
  gold: "border-indigo-500/30 bg-indigo-500/15 text-indigo-300",
  ready: "border-emerald-500/30 bg-emerald-500/15 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/15 text-rose-300",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function FileRegistryTable({
  files,
  selectedFileId,
  onInspect,
  onReingest,
  onRequestDelete,
}: FileRegistryTableProps) {
  const captionId = useId();

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70">
      <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3.5">
        <div>
          <h2 id={captionId} className="text-sm font-semibold text-slate-100">
            Lakehouse File Registry (Bronze &rarr; Silver &rarr; Gold)
          </h2>
          <p className="text-xs text-slate-400">
            Select any ready file to preview its Silver Excel tables or Gold PDF chunks below.
          </p>
        </div>
        <span className="rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-300">
          {files.length} {files.length === 1 ? "file" : "files"}
        </span>
      </div>

      {files.length === 0 ? (
        <div className="px-6 py-12 text-center text-sm text-slate-400">
          No files ingested yet. Upload your starter Excel and PDF files above.
        </div>
      ) : (
        <div
          tabIndex={0}
          role="region"
          aria-labelledby={captionId}
          className="table-wrapper"
        >
          <table className="lake-table text-left text-sm">
            <thead>
              <tr className="bg-slate-900 text-xs uppercase tracking-wider text-slate-400">
                <th className="border-b border-slate-800 px-4 py-3 font-medium">File</th>
                <th className="border-b border-slate-800 px-4 py-3 font-medium">Pipeline Status</th>
                <th className="border-b border-slate-800 px-4 py-3 font-medium">Extracted Summary</th>
                <th className="border-b border-slate-800 px-4 py-3 font-medium">SHA-256</th>
                <th className="border-b border-slate-800 px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/80">
              {files.map((file) => {
                const isSelected = file.id === selectedFileId;
                const isActive = ["queued", "bronze", "silver", "gold"].includes(file.status);
                const needsOcr = Boolean(file.metadata_json?.needs_ocr);
                const scannedPages = file.metadata_json?.scanned_pages ?? [];

                return (
                  <tr
                    key={file.id}
                    className={`transition-colors ${
                      isSelected ? "bg-indigo-500/10" : "hover:bg-slate-800/40"
                    }`}
                  >
                    <th scope="row" className="px-4 py-3 font-normal">
                      <div className="flex items-center gap-2.5">
                        {file.file_type === "excel" ? (
                          <FileSpreadsheet className="h-4 w-4 shrink-0 text-emerald-400" />
                        ) : (
                          <FileText className="h-4 w-4 shrink-0 text-indigo-400" />
                        )}
                        <div className="min-w-0">
                          <div className="truncate font-medium text-slate-100">
                            {file.filename}
                          </div>
                          <div className="text-xs text-slate-400">
                            {file.file_type.toUpperCase()} &middot; {formatBytes(file.file_size_bytes)}
                          </div>
                        </div>
                      </div>
                    </th>

                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        <span
                          className={`inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[file.status]}`}
                        >
                          {isActive && (
                            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                          )}
                          {file.status}
                        </span>
                        {file.error_message && (
                          <span className="max-w-xs text-xs text-rose-400">
                            {file.error_message}
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="px-4 py-3 text-xs text-slate-300">
                      {file.file_type === "excel" ? (
                        <div>
                          <span>
                            {file.metadata_json?.sheets_count ?? file.silver_tables.length} table(s) &middot;{" "}
                            {(file.metadata_json?.total_rows ?? 0).toLocaleString()} rows
                          </span>
                        </div>
                      ) : (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span>
                            {file.metadata_json?.page_count ?? 0} pages &middot;{" "}
                            {file.metadata_json?.chunk_count ?? 0} chunks
                          </span>
                          {needsOcr && (
                            <span
                              title={`Pages needing OCR: ${scannedPages.join(", ")}`}
                              className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-300"
                            >
                              <AlertTriangle className="h-3 w-3" />
                              scan p.{scannedPages.join(",")}
                            </span>
                          )}
                        </div>
                      )}
                    </td>

                    <td className="px-4 py-3 font-mono text-xs text-slate-400">
                      {file.file_hash.slice(0, 12)}
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => onInspect(file)}
                          disabled={file.status !== "ready"}
                          className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Eye className="h-3.5 w-3.5" />
                          Inspect
                        </button>
                        <button
                          type="button"
                          onClick={() => onReingest(file.id)}
                          disabled={isActive}
                          title="Re-run Silver/Gold pipeline from Bronze"
                          className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          Re-ingest
                        </button>
                        <button
                          type="button"
                          onClick={() => onRequestDelete(file)}
                          title="Delete from Bronze, Silver, Gold & pgvector"
                          className="inline-flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-500/20"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
