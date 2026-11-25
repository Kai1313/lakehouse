import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileSpreadsheet, FileText, Search, Table2 } from "lucide-react";
import {
  getFileChunks,
  previewTable,
  type DocumentChunkInfo,
  type FileRecord,
  type SilverTableInfo,
  type TablePreviewResponse,
} from "../../api/dataClient";

export type InspectorTab = "tables" | "chunks";

interface MedallionInspectorProps {
  activeTab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  files: FileRecord[];
  tables: SilverTableInfo[];
  selectedTableName: string | null;
  onSelectTable: (name: string) => void;
  selectedPdfId: string | null;
  onSelectPdf: (id: string) => void;
}

export function MedallionInspector({
  activeTab,
  onTabChange,
  files,
  tables,
  selectedTableName,
  onSelectTable,
  selectedPdfId,
  onSelectPdf,
}: MedallionInspectorProps) {
  const tablesTabId = useId();
  const chunksTabId = useId();
  const previewCaptionId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Silver table preview state
  const [limit, setLimit] = useState<number>(20);
  const [tablePreview, setTablePreview] = useState<TablePreviewResponse | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState<string | null>(null);

  // PDF chunks state
  const pdfFiles = useMemo(() => files.filter((f) => f.file_type === "pdf"), [files]);
  const [pageFilter, setPageFilter] = useState<string>("all");
  const [chunkSearch, setChunkSearch] = useState("");
  const [chunks, setChunks] = useState<DocumentChunkInfo[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [chunksError, setChunksError] = useState<string | null>(null);

  const activeTableMeta = useMemo(
    () => tables.find((t) => t.table_name === selectedTableName) ?? tables[0] ?? null,
    [tables, selectedTableName],
  );

  const activePdf = useMemo(
    () => pdfFiles.find((f) => f.id === selectedPdfId) ?? pdfFiles[0] ?? null,
    [pdfFiles, selectedPdfId],
  );

  useEffect(() => {
    if (!activeTableMeta) {
      setTablePreview(null);
      return;
    }
    let cancelled = false;
    setTableLoading(true);
    setTableError(null);
    previewTable(activeTableMeta.table_name, limit)
      .then((res) => {
        if (!cancelled) setTablePreview(res);
      })
      .catch((err) => {
        if (!cancelled) setTableError(err instanceof Error ? err.message : "Failed to load preview");
      })
      .finally(() => {
        if (!cancelled) setTableLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTableMeta?.table_name, activeTableMeta?.created_at, limit]);

  useEffect(() => {
    setPageFilter("all");
  }, [activePdf?.id]);

  useEffect(() => {
    if (!activePdf || activePdf.status !== "ready") {
      setChunks([]);
      setTotalChunks(0);
      return;
    }
    let cancelled = false;
    setChunksLoading(true);
    setChunksError(null);
    const pageNum = pageFilter === "all" ? undefined : Number(pageFilter);
    getFileChunks(activePdf.id, { limit: 500, page: pageNum })
      .then((res) => {
        if (!cancelled) {
          setChunks(res.chunks);
          setTotalChunks(res.total_chunks);
        }
      })
      .catch((err) => {
        if (!cancelled) setChunksError(err instanceof Error ? err.message : "Failed to load chunks");
      })
      .finally(() => {
        if (!cancelled) setChunksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activePdf?.id, activePdf?.updated_at, pageFilter]);

  const filteredChunks = useMemo(() => {
    const q = chunkSearch.trim().toLowerCase();
    if (!q) return chunks;
    return chunks.filter((c) => c.content.toLowerCase().includes(q));
  }, [chunks, chunkSearch]);

  const pageOptions = useMemo(() => {
    const count = activePdf?.metadata_json?.page_count ?? 0;
    return Array.from({ length: count }, (_, i) => i + 1);
  }, [activePdf?.metadata_json?.page_count]);

  function handleTabKeyDown(e: React.KeyboardEvent, idx: number) {
    const nextTab: InspectorTab = idx === 0 ? "chunks" : "tables";
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      onTabChange(nextTab);
      tabRefs.current[idx === 0 ? 1 : 0]?.focus();
    }
  }

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 pt-3">
        <div
          role="tablist"
          aria-label="Lakehouse Silver and Gold Inspector"
          className="flex gap-4 overflow-x-auto"
        >
          <button
            ref={(el) => {
              tabRefs.current[0] = el;
            }}
            id={tablesTabId}
            type="button"
            role="tab"
            aria-selected={activeTab === "tables"}
            tabIndex={activeTab === "tables" ? 0 : -1}
            onClick={() => onTabChange("tables")}
            onKeyDown={(e) => handleTabKeyDown(e, 0)}
            className={`inline-flex shrink-0 items-center gap-2 pb-3 text-sm font-medium transition-colors ${
              activeTab === "tables"
                ? "text-indigo-300"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileSpreadsheet className="h-4 w-4" />
            Silver Tables (Excel)
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">
              {tables.length}
            </span>
          </button>

          <button
            ref={(el) => {
              tabRefs.current[1] = el;
            }}
            id={chunksTabId}
            type="button"
            role="tab"
            aria-selected={activeTab === "chunks"}
            tabIndex={activeTab === "chunks" ? 0 : -1}
            onClick={() => onTabChange("chunks")}
            onKeyDown={(e) => handleTabKeyDown(e, 1)}
            className={`inline-flex shrink-0 items-center gap-2 pb-3 text-sm font-medium transition-colors ${
              activeTab === "chunks"
                ? "text-indigo-300"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileText className="h-4 w-4" />
            PDF Chunks (Gold)
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">
              {pdfFiles.length}
            </span>
          </button>
        </div>
      </div>

      {/* Panel 1: Silver Excel Tables */}
      <div
        role="tabpanel"
        aria-labelledby={tablesTabId}
        hidden={activeTab !== "tables"}
        tabIndex={activeTab === "tables" && tables.length === 0 ? 0 : undefined}
      >
        <div className="space-y-4 p-5">
          {tables.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-400">
              No Silver Delta tables yet. Upload an Excel file to inspect normalized sheets and rows.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor="silver-table-select" className="text-xs font-medium text-slate-400">
                    Table:
                  </label>
                  <select
                    id="silver-table-select"
                    value={activeTableMeta?.table_name ?? ""}
                    onChange={(e) => onSelectTable(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 focus:border-indigo-400 focus:outline-none"
                  >
                    {tables.map((t) => (
                      <option key={t.table_name} value={t.table_name}>
                        {t.table_name} ({t.original_filename} &rarr; {t.sheet_name})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  <label htmlFor="preview-limit" className="text-xs text-slate-400">
                    Preview rows:
                  </label>
                  <select
                    id="preview-limit"
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value))}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                  >
                    <option value={20}>20</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </div>
              </div>

              {activeTableMeta && (
                <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                    <span>
                      Delta URI: <code className="text-slate-300">{activeTableMeta.delta_uri}</code>
                    </span>
                    <span>{activeTableMeta.row_count.toLocaleString()} total rows</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {activeTableMeta.columns_json.map((col) => (
                      <span
                        key={col.name}
                        title={`Original Excel header: ${col.original_name}`}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs"
                      >
                        <span className="font-mono text-indigo-300">{col.name}</span>
                        <span className="text-slate-500">({col.type})</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {tableError && (
                <p role="alert" className="rounded-lg bg-rose-500/10 p-3 text-xs text-rose-300">
                  {tableError}
                </p>
              )}

              {tableLoading ? (
                <div className="py-8 text-center text-sm text-slate-400">
                  Loading Delta table preview from MinIO...
                </div>
              ) : tablePreview ? (
                <div
                  tabIndex={0}
                  role="region"
                  aria-labelledby={previewCaptionId}
                  className="table-wrapper rounded-lg border border-slate-800"
                >
                  <table className="lake-table text-left text-xs">
                    <caption id={previewCaptionId} className="sr-only">
                      Preview rows for {tablePreview.table_name}
                    </caption>
                    <thead>
                      <tr className="bg-slate-900 text-slate-300">
                        {tablePreview.columns.map((col) => (
                          <th
                            key={col.name}
                            className="whitespace-nowrap border-b border-slate-800 px-3 py-2.5 font-mono font-medium"
                          >
                            {col.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/70">
                      {tablePreview.rows.map((row, idx) => (
                        <tr key={idx} className="hover:bg-slate-800/30">
                          {tablePreview.columns.map((col) => {
                            const val = row[col.name];
                            return (
                              <td
                                key={col.name}
                                className="max-w-xs truncate whitespace-nowrap px-3 py-2 text-slate-200"
                                title={val === null || val === undefined ? "" : String(val)}
                              >
                                {val === null || val === undefined ? (
                                  <span className="text-slate-600">null</span>
                                ) : (
                                  String(val)
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>

      {/* Panel 2: PDF Chunks */}
      <div
        role="tabpanel"
        aria-labelledby={chunksTabId}
        hidden={activeTab !== "chunks"}
        tabIndex={activeTab === "chunks" && pdfFiles.length === 0 ? 0 : undefined}
      >
        <div className="space-y-4 p-5">
          {pdfFiles.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-400">
              No PDF files yet. Upload PDFs above to inspect extracted pages and vector chunks.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2.5">
                  <label htmlFor="pdf-file-select" className="text-xs font-medium text-slate-400">
                    PDF:
                  </label>
                  <select
                    id="pdf-file-select"
                    value={activePdf?.id ?? ""}
                    onChange={(e) => onSelectPdf(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 focus:border-indigo-400 focus:outline-none"
                  >
                    {pdfFiles.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.filename} ({f.metadata_json?.chunk_count ?? 0} chunks)
                      </option>
                    ))}
                  </select>

                  <label htmlFor="page-filter" className="text-xs text-slate-400">
                    Page:
                  </label>
                  <select
                    id="page-filter"
                    value={pageFilter}
                    onChange={(e) => setPageFilter(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-indigo-400 focus:outline-none"
                  >
                    <option value="all">All Pages</option>
                    {pageOptions.map((p) => (
                      <option key={p} value={String(p)}>
                        Page {p}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-500" />
                  <input
                    type="search"
                    value={chunkSearch}
                    onChange={(e) => setChunkSearch(e.target.value)}
                    placeholder="Filter chunk text..."
                    aria-label="Filter chunk text"
                    className="rounded-lg border border-slate-700 bg-slate-950 py-1.5 pl-8 pr-3 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none"
                  />
                </div>
              </div>

              {activePdf?.metadata_json?.needs_ocr && (
                <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>
                    Scanned / low-text pages detected on page(s):{" "}
                    <strong>{(activePdf.metadata_json.scanned_pages ?? []).join(", ")}</strong>.
                    Future OCR/Vision fallback can extract text from these pages.
                  </span>
                </div>
              )}

              {chunksError && (
                <p role="alert" className="rounded-lg bg-rose-500/10 p-3 text-xs text-rose-300">
                  {chunksError}
                </p>
              )}

              {chunksLoading ? (
                <div className="py-8 text-center text-sm text-slate-400">
                  Loading extracted PDF chunks...
                </div>
              ) : filteredChunks.length === 0 ? (
                <div className="py-8 text-center text-sm text-slate-400">
                  {activePdf?.status !== "ready"
                    ? `File status is '${activePdf?.status}'. Chunks appear once processing finishes.`
                    : "No chunks match the current filter."}
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-slate-400">
                    Showing {filteredChunks.length} of {totalChunks} chunks
                  </p>
                  <div className="max-h-[460px] space-y-3 overflow-y-auto pr-1">
                    {filteredChunks.map((chunk) => (
                      <article
                        key={chunk.id}
                        className="rounded-lg border border-slate-800 bg-slate-950/70 p-3.5"
                      >
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                          <div className="flex items-center gap-2">
                            <span className="rounded bg-indigo-500/15 px-2 py-0.5 font-medium text-indigo-300">
                              Page {chunk.page_number ?? "?"}
                            </span>
                            <span className="font-mono text-slate-400">
                              chunk #{chunk.chunk_index}
                            </span>
                            {chunk.metadata_json?.has_tables && (
                              <span className="inline-flex items-center gap-1 rounded border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-300">
                                <Table2 className="h-3 w-3" />
                                table
                              </span>
                            )}
                            {chunk.metadata_json?.needs_ocr && (
                              <span className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
                                <AlertTriangle className="h-3 w-3" />
                                needs_ocr
                              </span>
                            )}
                          </div>
                          <span className="text-slate-500">{chunk.content.length} chars</span>
                        </div>
                        <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-slate-200">
                          {chunk.content}
                        </pre>
                      </article>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
