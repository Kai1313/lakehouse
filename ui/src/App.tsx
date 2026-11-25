import { useEffect, useState } from "react";
import { Database, Layers, MessageSquare } from "lucide-react";
import { getHealth, type HealthStatus } from "./api/dataClient";
import { getRagHealth, type RagHealthStatus } from "./api/ragClient";
import { ChatWorkspaceView } from "./components/chat/ChatWorkspaceView";
import { DataLakehouseView } from "./components/lakehouse/DataLakehouseView";

type AppSection = "lakehouse" | "chat";

export function App() {
  const [section, setSection] = useState<AppSection>("lakehouse");
  const [dataHealth, setDataHealth] = useState<HealthStatus | null>(null);
  const [ragHealth, setRagHealth] = useState<RagHealthStatus | null>(null);

  useEffect(() => {
    let active = true;
    const poll = () => {
      getHealth()
        .then((h) => {
          if (active) setDataHealth(h);
        })
        .catch(() => {
          if (active) setDataHealth({ status: "offline", postgres: false, minio: false });
        });
      getRagHealth()
        .then((h) => {
          if (active) setRagHealth(h);
        })
        .catch(() => {
          if (active) setRagHealth({ status: "offline", postgres: false, duckdb: false });
        });
    };
    poll();
    const id = window.setInterval(poll, 15000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
                <Layers className="h-4 w-4" />
              </div>
              <span className="text-sm font-semibold tracking-tight text-slate-100">
                Lakehouse RAG Studio
              </span>
            </div>

            <nav aria-label="Primary sections" className="flex items-center gap-1.5">
              <button
                type="button"
                aria-current={section === "lakehouse" ? "page" : undefined}
                onClick={() => setSection("lakehouse")}
                className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  section === "lakehouse"
                    ? "bg-indigo-600/20 text-indigo-300 ring-1 ring-inset ring-indigo-500/40"
                    : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
                }`}
              >
                <Database className="h-4 w-4" />
                Data Lakehouse
              </button>

              <button
                type="button"
                aria-current={section === "chat" ? "page" : undefined}
                onClick={() => setSection("chat")}
                className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  section === "chat"
                    ? "bg-indigo-600/20 text-indigo-300 ring-1 ring-inset ring-indigo-500/40"
                    : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
                }`}
              >
                <MessageSquare className="h-4 w-4" />
                AI Chat
              </button>
            </nav>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
                dataHealth?.status === "ok"
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-300"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  dataHealth?.status === "ok" ? "bg-emerald-400" : "bg-rose-400"
                }`}
              />
              {dataHealth?.status === "ok" ? "Data Engine OK" : "Data Engine Offline"}
            </span>

            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
                ragHealth?.status === "ok"
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-300"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  ragHealth?.status === "ok" ? "bg-emerald-400" : "bg-rose-400"
                }`}
              />
              {ragHealth?.status === "ok" ? "RAG Engine OK" : "RAG Engine Offline"}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {section === "lakehouse" ? <DataLakehouseView /> : <ChatWorkspaceView />}
      </main>
    </div>
  );
}
