import { AlertTriangle, CheckCircle2, FileSpreadsheet, Layers } from "lucide-react";
import type { FileRecord, SilverTableInfo } from "../../api/dataClient";

interface KpiBarProps {
  files: FileRecord[];
  tables: SilverTableInfo[];
}

export function KpiBar({ files, tables }: KpiBarProps) {
  const readyCount = files.filter((f) => f.status === "ready").length;
  const processingCount = files.filter((f) =>
    ["queued", "bronze", "silver", "gold"].includes(f.status),
  ).length;
  const failedCount = files.filter((f) => f.status === "failed").length;

  const totalChunks = files.reduce(
    (acc, f) => acc + (f.metadata_json?.chunk_count ?? 0),
    0,
  );
  const scanWarnings = files.filter((f) => f.metadata_json?.needs_ocr).length;

  const cards = [
    {
      label: "Ingested Files",
      value: `${readyCount} / ${files.length}`,
      sub:
        processingCount > 0
          ? `${processingCount} processing...`
          : failedCount > 0
            ? `${failedCount} failed`
            : "All files ready",
      icon: CheckCircle2,
      accent: failedCount > 0 ? "text-amber-400" : "text-emerald-400",
    },
    {
      label: "Silver Delta Tables",
      value: String(tables.length),
      sub: `${tables.reduce((acc, t) => acc + t.row_count, 0).toLocaleString()} total rows`,
      icon: FileSpreadsheet,
      accent: "text-indigo-400",
    },
    {
      label: "Gold PDF Chunks",
      value: totalChunks.toLocaleString(),
      sub: "Synced to pgvector",
      icon: Layers,
      accent: "text-cyan-400",
    },
    {
      label: "OCR / Scan Warnings",
      value: String(scanWarnings),
      sub: scanWarnings > 0 ? "Pages flagged needs_ocr" : "All PDFs clean text",
      icon: AlertTriangle,
      accent: scanWarnings > 0 ? "text-amber-400" : "text-slate-400",
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <div
            key={card.label}
            className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
                {card.label}
              </span>
              <Icon className={`h-4 w-4 ${card.accent}`} />
            </div>
            <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-100">
              {card.value}
            </div>
            <div className="mt-1 text-xs text-slate-400">{card.sub}</div>
          </div>
        );
      })}
    </div>
  );
}
