import { useId, useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { uploadFiles, type UploadItemResult } from "../../api/dataClient";

interface FileUploaderProps {
  onUploaded: (items: UploadItemResult[]) => void;
}

const ACCEPTED_EXT = [".xlsx", ".xls", ".pdf"];

function isAccepted(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ACCEPTED_EXT.some((ext) => lower.endsWith(ext));
}

export function FileUploader({ onUploaded }: FileUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResults, setLastResults] = useState<UploadItemResult[]>([]);

  async function handleFiles(fileList: FileList | File[]) {
    const picked = Array.from(fileList);
    if (picked.length === 0) return;

    const valid = picked.filter(isAccepted);
    const rejected = picked.filter((f) => !isAccepted(f));

    if (rejected.length > 0 && valid.length === 0) {
      setError(
        `Unsupported file(s): ${rejected.map((f) => f.name).join(", ")}. Allowed: .xlsx, .xls, .pdf`,
      );
      return;
    }

    setError(
      rejected.length > 0
        ? `Skipped unsupported file(s): ${rejected.map((f) => f.name).join(", ")}`
        : null,
    );
    setUploading(true);
    try {
      const resp = await uploadFiles(valid);
      setLastResults(resp.items);
      onUploaded(resp.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <section
      aria-label="Upload Lakehouse files"
      className="rounded-xl border border-slate-800 bg-slate-900/70 p-5"
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files?.length) {
            void handleFiles(e.dataTransfer.files);
          }
        }}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging
            ? "border-indigo-400 bg-indigo-500/10"
            : "border-slate-700 bg-slate-950/50 hover:border-slate-600"
        }`}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-500/15 text-indigo-400">
          <UploadCloud className="h-6 w-6" />
        </div>
        <p className="mt-3 text-sm font-medium text-slate-200">
          Drag and drop your Excel (`.xlsx`, `.xls`) or PDF (`.pdf`) files here
        </p>
        <p className="mt-1 text-xs text-slate-400">
          Files enter Bronze S3 storage immediately, deduplicate by SHA-256, and process async into Silver &amp; Gold.
        </p>

        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          accept=".xlsx,.xls,.pdf"
          className="sr-only"
          onChange={(e) => {
            if (e.target.files?.length) {
              void handleFiles(e.target.files);
            }
          }}
        />
        <label
          htmlFor={inputId}
          className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 focus-within:outline-2 focus-within:outline-indigo-400"
        >
          Select Files
        </label>

        {uploading && (
          <div className="mt-4 w-full max-w-sm space-y-1.5">
            <progress
              className="lake-progress"
              aria-label="Uploading files to Bronze storage"
            />
            <p className="text-xs text-indigo-300">Uploading to MinIO Bronze layer...</p>
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      {lastResults.length > 0 && (
        <div
          role="status"
          aria-live="polite"
          className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300"
        >
          <span className="font-medium text-slate-200">Last upload:</span>
          {lastResults.map((item) => (
            <span
              key={`${item.file_id}-${item.filename}`}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5"
            >
              <span className="font-medium text-slate-100">{item.filename}</span>
              <span
                className={
                  item.action === "skipped_duplicate"
                    ? "text-amber-400"
                    : item.action === "replaced"
                      ? "text-cyan-400"
                      : "text-emerald-400"
                }
              >
                ({item.action.replace("_", " ")})
              </span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
