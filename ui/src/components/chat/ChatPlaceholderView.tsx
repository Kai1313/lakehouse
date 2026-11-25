import { Bot, Database, MessageSquare, Sparkles } from "lucide-react";

export function ChatPlaceholderView() {
  return (
    <div className="mx-auto max-w-2xl py-12">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-8 text-center shadow-lg">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-500/15 text-indigo-400">
          <MessageSquare className="h-7 w-7" />
        </div>
        <span className="mt-4 inline-block rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-300">
          Coming in Phase 6
        </span>
        <h1 className="mt-3 text-xl font-semibold text-slate-100">
          AI Chat Workspace (RAG + Text-to-SQL)
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-400">
          Once you verify your 2 Excel + 5 PDF files in the Lakehouse tab, Phase 5 &amp; Phase 6 will wire this tab to{" "}
          <code className="text-slate-200">rag-engine</code>.
        </p>

        <div className="mt-6 grid grid-cols-1 gap-3 text-left sm:grid-cols-3">
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5">
            <Sparkles className="h-4 w-4 text-indigo-400" />
            <div className="mt-2 text-xs font-semibold text-slate-200">Dynamic AI Switcher</div>
            <p className="mt-1 text-xs text-slate-400">
              Switch live between DeepSeek, Gemini, Claude, and more via LiteLLM.
            </p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5">
            <Database className="h-4 w-4 text-emerald-400" />
            <div className="mt-2 text-xs font-semibold text-slate-200">DuckDB Silver SQL</div>
            <p className="mt-1 text-xs text-slate-400">
              Exact calculations and aggregations directly over Silver Delta tables.
            </p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5">
            <Bot className="h-4 w-4 text-cyan-400" />
            <div className="mt-2 text-xs font-semibold text-slate-200">PDF Vector Citations</div>
            <p className="mt-1 text-xs text-slate-400">
              Streaming SSE answers grounded with clickable PDF page snippets.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
