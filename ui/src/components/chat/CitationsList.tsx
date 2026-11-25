import { ChevronDown, Database, FileText } from "lucide-react";
import type { CitationsPayload } from "../../api/ragClient";

interface CitationsListProps {
  citations: CitationsPayload | unknown[] | null | undefined;
}

export function CitationsList({ citations }: CitationsListProps) {
  if (!citations || Array.isArray(citations)) return null;
  const pdfs = citations.pdf_citations ?? [];
  const sqls = citations.sql_citations ?? [];
  if (pdfs.length === 0 && sqls.length === 0) return null;

  return (
    <section
      aria-label="Retrieved sources and SQL citations"
      className="mt-3 space-y-2 border-t border-slate-800/80 pt-3"
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
        Sources ({pdfs.length} PDF, {sqls.length} SQL)
      </div>

      <div className="space-y-1.5">
        {sqls.map((sqlItem, idx) => (
          <details
            key={`sql-${idx}`}
            className="citation-item rounded-lg border border-emerald-500/25 bg-slate-950/70 text-xs"
          >
            <summary className="flex items-center gap-2 px-3 py-2 text-slate-200 hover:bg-slate-900/70">
              <ChevronDown className="citation-chevron h-3.5 w-3.5 shrink-0 text-emerald-400" />
              <Database className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              <span className="truncate font-medium">
                DuckDB Silver SQL{sqlItem.explanation ? `: ${sqlItem.explanation}` : ""}
              </span>
              <span className="ml-auto shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[11px] text-emerald-300">
                {sqlItem.row_count} row(s)
              </span>
            </summary>

            <div className="space-y-2.5 border-t border-slate-800 px-3 py-2.5">
              <pre className="overflow-x-auto rounded border border-slate-800 bg-slate-900 p-2 font-mono text-[11px] text-emerald-300">
                {sqlItem.sql}
              </pre>
              {sqlItem.rows.length > 0 && (
                <div
                  tabIndex={0}
                  role="region"
                  aria-label="SQL query result rows"
                  className="table-wrapper rounded border border-slate-800"
                >
                  <table className="lake-table text-left text-[11px]">
                    <thead>
                      <tr className="bg-slate-900 text-slate-300">
                        {sqlItem.columns.map((col) => (
                          <th
                            key={col}
                            className="whitespace-nowrap border-b border-slate-800 px-2.5 py-1.5 font-mono font-medium"
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/70">
                      {sqlItem.rows.map((r, rIdx) => (
                        <tr key={rIdx}>
                          {sqlItem.columns.map((col) => {
                            const val = r[col];
                            return (
                              <td
                                key={col}
                                className="max-w-[200px] truncate whitespace-nowrap px-2.5 py-1.5 text-slate-200"
                                title={val === null || val === undefined ? "" : String(val)}
                              >
                                {val === null || val === undefined ? "null" : String(val)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </details>
        ))}

        {pdfs.map((pdf) => (
          <details
            key={pdf.chunk_id}
            className="citation-item rounded-lg border border-indigo-500/25 bg-slate-950/70 text-xs"
          >
            <summary className="flex items-center gap-2 px-3 py-2 text-slate-200 hover:bg-slate-900/70">
              <ChevronDown className="citation-chevron h-3.5 w-3.5 shrink-0 text-indigo-400" />
              <FileText className="h-3.5 w-3.5 shrink-0 text-indigo-400" />
              <span className="truncate font-medium">{pdf.filename}</span>
              <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[11px] text-indigo-300">
                p.{pdf.page_number ?? "?"}
              </span>
              <span className="ml-auto shrink-0 font-mono text-[11px] text-slate-400">
                score {pdf.score.toFixed(2)}
              </span>
            </summary>

            <div className="border-t border-slate-800 px-3 py-2.5">
              <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-slate-300">
                {pdf.content}
              </pre>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
