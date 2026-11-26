import { useEffect, useId, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  FileText,
  Loader2,
  Sparkles,
} from "lucide-react";
import {
  confirmSilver,
  getRawPreview,
  type ColumnMappingOverride,
  type FileRawPreviewResponse,
  type FileRecord,
  type RawSheetPreview,
} from "../../api/dataClient";
import { listModels, suggestSchema, type ModelOption } from "../../api/ragClient";

type SilverType = ColumnMappingOverride["type"];

interface EditableColumn {
  index: number;
  rawHeader: string;
  name: string;
  type: SilverType;
  keep: boolean;
  reason?: string;
}

interface SheetDraft {
  headerRowsText: string;
  columns: EditableColumn[];
}

interface SchemaReviewPanelProps {
  files: FileRecord[];
  selectedFileId: string | null;
  onSelectFile: (fileId: string) => void;
  onConfirmed: () => void | Promise<void>;
}

const VALID_TYPES: SilverType[] = ["VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN"];

function normalizeSilverType(t: string): SilverType {
  const upper = t.toUpperCase();
  if (upper === "BIGINT" || upper === "DOUBLE" || upper === "BOOLEAN" || upper === "VARCHAR") {
    return upper;
  }
  return "VARCHAR";
}

function parseHeaderRowsInput(text: string): number[] {
  const nums = text
    .split(/[,\s]+/)
    .map((part) => part.trim())
    .filter((part) => /^\d+$/.test(part))
    .map((part) => Number(part));
  return Array.from(new Set(nums)).sort((a, b) => a - b);
}

function toSnakeCase(raw: string, fallbackIdx: number): string {
  const cleaned = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!cleaned) return `col_${fallbackIdx + 1}`;
  return /^[0-9]/.test(cleaned) ? `col_${cleaned}` : cleaned;
}

function formatCell(val: unknown): string {
  if (val === null || val === undefined) return "";
  return String(val);
}

export function SchemaReviewPanel({
  files,
  selectedFileId,
  onSelectFile,
  onConfirmed,
}: SchemaReviewPanelProps) {
  const fileSelectId = useId();
  const sheetSelectId = useId();
  const headerRowsInputId = useId();
  const headerRowsHelpId = useId();
  const modelSelectId = useId();
  const noteInputId = useId();
  const noteHelpId = useId();
  const formHeadingId = useId();

  const readyFiles = useMemo(() => files.filter((f) => f.status === "ready"), [files]);

  const activeFile = useMemo(
    () =>
      readyFiles.find((f) => f.id === selectedFileId) ??
      readyFiles.find((f) => f.review_status === "needs_review") ??
      readyFiles[0] ??
      null,
    [readyFiles, selectedFileId],
  );

  const [rawPreview, setRawPreview] = useState<FileRawPreviewResponse | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [selectedSheetName, setSelectedSheetName] = useState<string>("");
  const [sheetDrafts, setSheetDrafts] = useState<Record<string, SheetDraft>>({});
  const [documentNote, setDocumentNote] = useState("");

  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [suggesting, setSuggesting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );

  useEffect(() => {
    listModels()
      .then((res) => {
        setModels(res.models);
        if (res.default_model_id) {
          setSelectedModelId((prev) => prev || res.default_model_id || "");
        }
      })
      .catch(() => {
        // Optional AI suggest; silent fallback if rag-engine unreachable
      });
  }, []);

  useEffect(() => {
    if (!activeFile) {
      setRawPreview(null);
      return;
    }
    let cancelled = false;
    setLoadingPreview(true);
    setPreviewError(null);
    setStatusMessage(null);

    getRawPreview(activeFile.id, 15)
      .then((res) => {
        if (cancelled) return;
        setRawPreview(res);
        setDocumentNote(res.document_note ?? "");

        const drafts: Record<string, SheetDraft> = {};
        for (const sheet of res.sheets) {
          drafts[sheet.sheet_name] = {
            headerRowsText:
              sheet.detected_header_rows.length > 0
                ? sheet.detected_header_rows.join(", ")
                : "0",
            columns: sheet.columns.map((col, idx) => ({
              index: col.index ?? idx,
              rawHeader: col.original_name || col.name,
              name: col.name,
              type: normalizeSilverType(col.type),
              keep: col.keep ?? true,
            })),
          };
        }
        setSheetDrafts(drafts);
        setSelectedSheetName(res.sheets[0]?.sheet_name ?? "");
      })
      .catch((err) => {
        if (!cancelled) {
          setPreviewError(err instanceof Error ? err.message : "Failed to load raw preview");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingPreview(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeFile?.id, activeFile?.updated_at]);

  const activeSheet: RawSheetPreview | null = useMemo(() => {
    if (!rawPreview || rawPreview.sheets.length === 0) return null;
    return (
      rawPreview.sheets.find((s) => s.sheet_name === selectedSheetName) ?? rawPreview.sheets[0]
    );
  }, [rawPreview, selectedSheetName]);

  const activeDraft: SheetDraft | null = activeSheet
    ? sheetDrafts[activeSheet.sheet_name] ?? null
    : null;

  const parsedHeaderRows = useMemo(
    () => (activeDraft ? parseHeaderRowsInput(activeDraft.headerRowsText) : []),
    [activeDraft],
  );

  function updateActiveDraft(updater: (draft: SheetDraft) => SheetDraft) {
    if (!activeSheet) return;
    const sheetName = activeSheet.sheet_name;
    setSheetDrafts((prev) => {
      const current = prev[sheetName];
      if (!current) return prev;
      return { ...prev, [sheetName]: updater(current) };
    });
  }

  function toggleHeaderRow(rowIdx: number) {
    const next = parsedHeaderRows.includes(rowIdx)
      ? parsedHeaderRows.filter((r) => r !== rowIdx)
      : [...parsedHeaderRows, rowIdx].sort((a, b) => a - b);
    const fallback = next.length > 0 ? next : [0];
    updateActiveDraft((d) => ({ ...d, headerRowsText: fallback.join(", ") }));
  }

  function syncNamesFromSelectedHeaders() {
    if (!activeSheet || !activeDraft) return;
    const rowsToUse = parsedHeaderRows.length > 0 ? parsedHeaderRows : [0];
    const usedNames = new Set<string>();

    updateActiveDraft((d) => ({
      ...d,
      columns: d.columns.map((col) => {
        const parts: string[] = [];
        for (const rIdx of rowsToUse) {
          const cellVal = activeSheet.rows[rIdx]?.[col.index];
          const text = formatCell(cellVal).trim();
          if (text && parts[parts.length - 1] !== text) {
            parts.push(text);
          }
        }
        const combined = parts.join(" / ") || col.rawHeader;
        let candidate = toSnakeCase(combined, col.index);
        if (usedNames.has(candidate)) {
          let suffix = 2;
          while (usedNames.has(`${candidate}_${suffix}`)) suffix++;
          candidate = `${candidate}_${suffix}`;
        }
        usedNames.add(candidate);
        return {
          ...col,
          rawHeader: combined,
          name: candidate,
        };
      }),
    }));
  }

  async function handleSuggestWithAi() {
    if (!activeFile || !activeSheet || !activeDraft || !selectedModelId) return;
    setSuggesting(true);
    setStatusMessage(null);
    try {
      const suggestion = await suggestSchema({
        model_id: selectedModelId,
        filename: activeFile.filename,
        sheet_name: activeSheet.sheet_name,
        raw_rows: activeSheet.rows.slice(0, 12),
        detected_header_rows: parsedHeaderRows,
        current_columns: activeDraft.columns.map((c) => ({
          index: c.index,
          name: c.name,
          original_name: c.rawHeader,
          type: c.type,
        })),
      });

      const byIndex = new Map(suggestion.columns.map((c) => [c.index, c]));
      updateActiveDraft((draft) => ({
        headerRowsText:
          suggestion.header_rows.length > 0
            ? suggestion.header_rows.join(", ")
            : draft.headerRowsText,
        columns: draft.columns.map((col) => {
          const sug = byIndex.get(col.index);
          if (!sug) return col;
          return {
            ...col,
            name: sug.name || col.name,
            type: normalizeSilverType(sug.type),
            keep: sug.keep,
            reason: sug.reason,
          };
        }),
      }));

      setStatusMessage({
        kind: "ok",
        text: `Applied AI suggestions using ${selectedModelId}. Review and click "Confirm & Rebuild Silver" to save.`,
      });
    } catch (err) {
      setStatusMessage({
        kind: "err",
        text: err instanceof Error ? err.message : "AI schema suggestion failed",
      });
    } finally {
      setSuggesting(false);
    }
  }

  async function handleSubmitExcel(e: React.FormEvent) {
    e.preventDefault();
    if (!activeFile || !rawPreview) return;

    setSubmitting(true);
    setStatusMessage(null);
    try {
      const sheetOverrides = rawPreview.sheets.map((sheet) => {
        const draft = sheetDrafts[sheet.sheet_name];
        const headerRows = parseHeaderRowsInput(draft?.headerRowsText ?? "");
        const cols: ColumnMappingOverride[] = (draft?.columns ?? []).map((col) => ({
          index: col.index,
          name: col.name.trim() || `col_${col.index + 1}`,
          type: col.type,
          keep: col.keep,
        }));
        return {
          sheet_name: sheet.sheet_name,
          header_rows: headerRows.length > 0 ? headerRows : sheet.detected_header_rows,
          columns: cols,
        };
      });

      await confirmSilver(activeFile.id, {
        document_note: documentNote.trim(),
        sheet_overrides: sheetOverrides,
      });
      await onConfirmed();
      setStatusMessage({
        kind: "ok",
        text: "Silver Delta table(s) rebuilt from Bronze, verified, and preset saved for future re-ingests.",
      });
    } catch (err) {
      setStatusMessage({
        kind: "err",
        text: err instanceof Error ? err.message : "Failed to confirm Silver schema",
      });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitPdf(e: React.FormEvent) {
    e.preventDefault();
    if (!activeFile) return;

    setSubmitting(true);
    setStatusMessage(null);
    try {
      await confirmSilver(activeFile.id, {
        document_note: documentNote.trim(),
      });
      await onConfirmed();
      setStatusMessage({
        kind: "ok",
        text: "PDF document context saved and marked Verified.",
      });
    } catch (err) {
      setStatusMessage({
        kind: "err",
        text: err instanceof Error ? err.message : "Failed to save PDF context note",
      });
    } finally {
      setSubmitting(false);
    }
  }

  if (readyFiles.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        No ready files yet. Upload and ingest an Excel or PDF file first to review its Silver structure.
      </p>
    );
  }

  const configuredModels = models.filter((m) => m.configured);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={fileSelectId} className="text-xs font-medium text-slate-300">
            File to Review:
          </label>
          <select
            id={fileSelectId}
            name="review_file_id"
            value={activeFile?.id ?? ""}
            onChange={(e) => onSelectFile(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 focus:border-indigo-400 focus:outline-none"
          >
            {readyFiles.map((f) => (
              <option key={f.id} value={f.id}>
                {f.filename} ({f.file_type.toUpperCase()} &middot;{" "}
                {f.review_status === "verified" ? "Verified" : "Needs Review"})
              </option>
            ))}
          </select>
        </div>

        {activeFile && (
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              activeFile.review_status === "verified"
                ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-300"
                : "border-amber-500/30 bg-amber-500/15 text-amber-300"
            }`}
          >
            {activeFile.review_status === "verified" ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <AlertTriangle className="h-3.5 w-3.5" />
            )}
            {activeFile.review_status === "verified" ? "Verified" : "Needs Review"}
          </span>
        )}
      </div>

      {loadingPreview && (
        <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
          Loading raw Bronze preview...
        </div>
      )}

      {previewError && (
        <div
          role="alert"
          className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
        >
          {previewError}
        </div>
      )}

      {!loadingPreview && rawPreview && rawPreview.file_type === "pdf" && (
        <form
          onSubmit={(e) => void handleSubmitPdf(e)}
          aria-labelledby={formHeadingId}
          className="space-y-4 rounded-lg border border-slate-800 bg-slate-950/60 p-4"
        >
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-indigo-400" />
            <h3 id={formHeadingId} className="text-sm font-semibold text-slate-100">
              PDF Document Context &amp; Verification
            </h3>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={noteInputId} className="text-xs font-medium text-slate-300">
              Document Context Note (optional)
            </label>
            <textarea
              id={noteInputId}
              name="document_note"
              rows={3}
              value={documentNote}
              onChange={(e) => setDocumentNote(e.target.value)}
              aria-describedby={noteHelpId}
              placeholder="e.g. Official FY 2026 RKA budget document; Figures represent planned ceiling per SKPD."
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-400 focus:outline-none"
            />
            <span id={noteHelpId} className="text-xs text-slate-400">
              Injected into the AI Chat catalog context whenever the assistant searches this PDF.
            </span>
          </div>

          {statusMessage && (
            <div
              role="status"
              aria-live="polite"
              className={`rounded-lg border px-3 py-2 text-xs ${
                statusMessage.kind === "ok"
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-300"
              }`}
            >
              {statusMessage.text}
            </div>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              {submitting ? "Saving..." : "Confirm PDF Context"}
            </button>
          </div>
        </form>
      )}

      {!loadingPreview && rawPreview && rawPreview.file_type === "excel" && activeSheet && activeDraft && (
        <div className="space-y-4">
          {/* Sheet selector & AI suggest toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <FileSpreadsheet className="h-4 w-4 text-emerald-400" />
              <label htmlFor={sheetSelectId} className="text-xs font-medium text-slate-300">
                Sheet:
              </label>
              <select
                id={sheetSelectId}
                value={activeSheet.sheet_name}
                onChange={(e) => setSelectedSheetName(e.target.value)}
                className="rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
              >
                {rawPreview.sheets.map((s) => (
                  <option key={s.sheet_name} value={s.sheet_name}>
                    {s.sheet_name} {s.table_name ? `(${s.table_name})` : ""}
                  </option>
                ))}
              </select>
              <span className="text-xs text-slate-400">
                {activeSheet.total_raw_rows.toLocaleString()} raw rows
              </span>
            </div>

            {configuredModels.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor={modelSelectId} className="text-xs text-slate-400">
                  Model:
                </label>
                <select
                  id={modelSelectId}
                  value={selectedModelId}
                  onChange={(e) => setSelectedModelId(e.target.value)}
                  disabled={suggesting}
                  className="rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                >
                  {configuredModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => void handleSuggestWithAi()}
                  disabled={suggesting || !selectedModelId}
                  className="inline-flex items-center gap-1.5 rounded-md border border-indigo-500/40 bg-indigo-500/15 px-3 py-1 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {suggesting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  {suggesting ? "Analyzing..." : "Auto-Suggest with AI"}
                </button>
              </div>
            )}
          </div>

          {/* Raw Bronze rows preview */}
          <div className="space-y-1.5">
            <div className="text-xs font-medium text-slate-300">
              Raw Bronze Grid (First {activeSheet.rows.length} rows &mdash; click a row index to toggle Header Row)
            </div>
            <div
              tabIndex={0}
              role="region"
              aria-label="Raw Bronze Excel rows"
              className="table-wrapper max-h-64"
            >
              <table className="lake-table text-left text-xs">
                <thead>
                  <tr className="bg-slate-900 text-[11px] text-slate-400">
                    <th className="border-b border-slate-800 px-2.5 py-1.5 font-mono">Row</th>
                    {activeDraft.columns.map((c) => (
                      <th key={c.index} className="border-b border-slate-800 px-2.5 py-1.5 font-mono">
                        C{c.index}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {activeSheet.rows.map((row, rowIdx) => {
                    const isHeader = parsedHeaderRows.includes(rowIdx);
                    return (
                      <tr
                        key={rowIdx}
                        className={isHeader ? "bg-indigo-500/15 text-indigo-100" : "text-slate-300"}
                      >
                        <th scope="row" className="border-r border-slate-800 px-2 py-1 font-mono">
                          <button
                            type="button"
                            onClick={() => toggleHeaderRow(rowIdx)}
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${
                              isHeader
                                ? "bg-indigo-600 text-white"
                                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                            }`}
                          >
                            #{rowIdx}
                            {isHeader && <span>HDR</span>}
                          </button>
                        </th>
                        {activeDraft.columns.map((c) => (
                          <td
                            key={c.index}
                            className="max-w-[180px] truncate px-2.5 py-1"
                            title={formatCell(row[c.index])}
                          >
                            {formatCell(row[c.index]) || (
                              <span className="text-slate-600">&mdash;</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Schema Edit Form */}
          <form
            onSubmit={(e) => void handleSubmitExcel(e)}
            aria-labelledby={formHeadingId}
            className="space-y-4 rounded-lg border border-slate-800 bg-slate-950/60 p-4"
          >
            <h3 id={formHeadingId} className="text-sm font-semibold text-slate-100">
              Silver Schema Mapping ({activeSheet.sheet_name})
            </h3>

            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1">
                <label htmlFor={headerRowsInputId} className="text-xs font-medium text-slate-300">
                  Header Rows (0-based indices)
                </label>
                <input
                  id={headerRowsInputId}
                  name="header_rows"
                  type="text"
                  required
                  value={activeDraft.headerRowsText}
                  onChange={(e) =>
                    updateActiveDraft((d) => ({ ...d, headerRowsText: e.target.value }))
                  }
                  aria-describedby={headerRowsHelpId}
                  autoCorrect="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  className="w-48 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 font-mono text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                />
              </div>

              <button
                type="button"
                onClick={syncNamesFromSelectedHeaders}
                className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
              >
                Sync Names from Header Rows
              </button>

              <span id={headerRowsHelpId} className="text-xs text-slate-400">
                Merged multi-row headers (e.g. <code>3, 4</code>) coalesce parent + child names automatically on rebuild.
              </span>
            </div>

            <div
              tabIndex={0}
              role="region"
              aria-label="Silver column mapping editor"
              className="table-wrapper max-h-96"
            >
              <table className="lake-table text-left text-xs">
                <thead>
                  <tr className="bg-slate-900 text-[11px] uppercase tracking-wider text-slate-400">
                    <th className="border-b border-slate-800 px-3 py-2">Col</th>
                    <th className="border-b border-slate-800 px-3 py-2">Detected Header</th>
                    <th className="border-b border-slate-800 px-3 py-2">Silver Column Name</th>
                    <th className="border-b border-slate-800 px-3 py-2">Data Type</th>
                    <th className="border-b border-slate-800 px-3 py-2">Keep</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80">
                  {activeDraft.columns.map((col) => {
                    const nameInputId = `col-name-${activeSheet.sheet_name}-${col.index}`;
                    const typeSelectId = `col-type-${activeSheet.sheet_name}-${col.index}`;
                    const keepCheckboxId = `col-keep-${activeSheet.sheet_name}-${col.index}`;
                    return (
                      <tr key={col.index} className={col.keep ? "" : "opacity-50"}>
                        <td className="px-3 py-2 font-mono text-slate-400">C{col.index}</td>
                        <td className="max-w-[220px] px-3 py-2 text-slate-300">
                          <div className="truncate" title={col.rawHeader}>
                            {col.rawHeader}
                          </div>
                          {col.reason && (
                            <div className="mt-0.5 text-[11px] text-indigo-300">{col.reason}</div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <label htmlFor={nameInputId} className="sr-only">
                            Silver column name for column {col.index}
                          </label>
                          <input
                            id={nameInputId}
                            name={`col_${col.index}_name`}
                            type="text"
                            required
                            value={col.name}
                            onChange={(e) =>
                              updateActiveDraft((d) => ({
                                ...d,
                                columns: d.columns.map((item) =>
                                  item.index === col.index
                                    ? { ...item, name: e.target.value }
                                    : item,
                                ),
                              }))
                            }
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            className="w-56 rounded border border-slate-700 bg-slate-900 px-2.5 py-1 font-mono text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                          />
                        </td>
                        <td className="px-3 py-2">
                          <label htmlFor={typeSelectId} className="sr-only">
                            Data type for column {col.index}
                          </label>
                          <select
                            id={typeSelectId}
                            name={`col_${col.index}_type`}
                            value={col.type}
                            onChange={(e) =>
                              updateActiveDraft((d) => ({
                                ...d,
                                columns: d.columns.map((item) =>
                                  item.index === col.index
                                    ? { ...item, type: normalizeSilverType(e.target.value) }
                                    : item,
                                ),
                              }))
                            }
                            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                          >
                            {VALID_TYPES.map((t) => (
                              <option key={t} value={t}>
                                {t}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="px-3 py-2">
                          <label
                            htmlFor={keepCheckboxId}
                            className="inline-flex items-center gap-1.5 text-slate-300"
                          >
                            <input
                              id={keepCheckboxId}
                              name={`col_${col.index}_keep`}
                              type="checkbox"
                              checked={col.keep}
                              onChange={(e) =>
                                updateActiveDraft((d) => ({
                                  ...d,
                                  columns: d.columns.map((item) =>
                                    item.index === col.index
                                      ? { ...item, keep: e.target.checked }
                                      : item,
                                  ),
                                }))
                              }
                              className="h-4 w-4 rounded border-slate-600 accent-indigo-500"
                            />
                            Keep
                          </label>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {statusMessage && (
              <div
                role="status"
                aria-live="polite"
                className={`rounded-lg border px-3 py-2 text-xs ${
                  statusMessage.kind === "ok"
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                }`}
              >
                {statusMessage.text}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-slate-400">
                Rebuilds the Silver Delta table from Bronze and saves these overrides for Re-ingest.
              </span>
              <button
                type="submit"
                disabled={submitting}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                )}
                {submitting ? "Rebuilding Silver..." : "Confirm & Rebuild Silver"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
