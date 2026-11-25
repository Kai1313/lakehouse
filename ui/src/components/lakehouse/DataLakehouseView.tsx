import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  deleteFile,
  listFiles,
  listTables,
  reingestFile,
  type FileRecord,
  type SilverTableInfo,
} from "../../api/dataClient";
import { ConfirmDialog } from "../ConfirmDialog";
import { FileRegistryTable } from "./FileRegistryTable";
import { FileUploader } from "./FileUploader";
import { KpiBar } from "./KpiBar";
import { MedallionInspector, type InspectorTab } from "./MedallionInspector";

export function DataLakehouseView() {
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [tables, setTables] = useState<SilverTableInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("tables");
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [selectedTableName, setSelectedTableName] = useState<string | null>(null);
  const [selectedPdfId, setSelectedPdfId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<FileRecord | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [fileList, tableList] = await Promise.all([listFiles(), listTables()]);
      setFiles(fileList);
      setTables(tableList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach data-engine");
    }
  }, []);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const hasActiveJobs = files.some((f) =>
    ["queued", "bronze", "silver", "gold"].includes(f.status),
  );

  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => {
      void refreshAll();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refreshAll]);

  function handleInspect(file: FileRecord) {
    setSelectedFileId(file.id);
    if (file.file_type === "excel") {
      setInspectorTab("tables");
      if (file.silver_tables.length > 0) {
        setSelectedTableName(file.silver_tables[0]);
      }
    } else {
      setInspectorTab("chunks");
      setSelectedPdfId(file.id);
    }
  }

  async function handleReingest(fileId: string) {
    try {
      await reingestFile(fileId);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Re-ingest failed");
    }
  }

  async function handleConfirmDelete() {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setPendingDelete(null);
    try {
      await deleteFile(target.id);
      if (selectedFileId === target.id) setSelectedFileId(null);
      if (selectedPdfId === target.id) setSelectedPdfId(null);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">
            Lakehouse Data Management
          </h1>
          <p className="text-sm text-slate-400">
            Ingest Excel &amp; PDF files through Bronze S3 storage, Delta Lake Silver tables, and Gold pgvector chunks.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refreshAll()}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${hasActiveJobs ? "animate-spin text-indigo-400" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-300">
          {error}
        </div>
      )}

      <KpiBar files={files} tables={tables} />

      <FileUploader
        onUploaded={() => {
          void refreshAll();
        }}
      />

      <FileRegistryTable
        files={files}
        selectedFileId={selectedFileId}
        onInspect={handleInspect}
        onReingest={(id) => void handleReingest(id)}
        onRequestDelete={(file) => setPendingDelete(file)}
      />

      <MedallionInspector
        activeTab={inspectorTab}
        onTabChange={setInspectorTab}
        files={files}
        tables={tables}
        selectedTableName={selectedTableName}
        onSelectTable={setSelectedTableName}
        selectedPdfId={selectedPdfId}
        onSelectPdf={setSelectedPdfId}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete file from Lakehouse?"
        description={
          pendingDelete
            ? `This will permanently remove "${pendingDelete.filename}" from Bronze object storage, Silver/Gold Delta tables, and pgvector chunks.`
            : ""
        }
        confirmLabel="Delete Permanently"
        onConfirm={() => void handleConfirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
