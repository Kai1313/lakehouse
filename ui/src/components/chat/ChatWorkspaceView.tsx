import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bot,
  Cpu,
  MessageSquarePlus,
  Send,
  Square,
  Trash2,
  User,
  Wrench,
} from "lucide-react";
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  listModels,
  streamChat,
  type CitationsPayload,
  type ConversationSummary,
  type MessageRecord,
  type ModelOption,
} from "../../api/ragClient";
import { ConfirmDialog } from "../ConfirmDialog";
import { CitationsList } from "./CitationsList";

interface ToolTrace {
  id: string;
  name: string;
  summary?: string;
}

const EXAMPLE_PROMPTS = [
  "Summarize the main requirements and topics across all uploaded PDFs.",
  "Show the total row count and key breakdown from the Silver Excel tables.",
  "Compare the rules in the PDFs with the data recorded in the Excel sheets.",
];

export function ChatWorkspaceView() {
  const inputId = useId();
  const modelSelectId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>("");

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [pendingDeleteConv, setPendingDeleteConv] = useState<ConversationSummary | null>(null);

  const [inputText, setInputText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [statusLabel, setStatusLabel] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [streamingTraces, setStreamingTraces] = useState<ToolTrace[]>([]);
  const [streamingCitations, setStreamingCitations] = useState<CitationsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const modelsByProvider = useMemo(() => {
    const groups: Record<string, ModelOption[]> = {};
    for (const m of models) {
      if (!groups[m.provider]) groups[m.provider] = [];
      groups[m.provider].push(m);
    }
    return groups;
  }, [models]);

  const loadBootstrap = useCallback(async () => {
    try {
      const [modelsResp, convList] = await Promise.all([listModels(), listConversations()]);
      setModels(modelsResp.models);
      if (modelsResp.default_model_id) {
        setSelectedModelId((prev) => prev || modelsResp.default_model_id || "");
      }
      setConversations(convList);
      if (convList.length > 0) {
        setActiveConvId((prev) => prev ?? convList[0].id);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect to rag-engine (:8002).");
    }
  }, []);

  useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  useEffect(() => {
    if (!activeConvId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    getConversation(activeConvId)
      .then((detail) => {
        if (cancelled) return;
        setMessages(detail.messages);
        if (detail.model_id) {
          const matching = models.find((m) => m.id === detail.model_id && m.configured);
          if (matching) setSelectedModelId(matching.id);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load thread.");
      });
    return () => {
      cancelled = true;
    };
  }, [activeConvId, models]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, streamingTraces.length]);

  async function handleNewChat() {
    if (streaming) return;
    try {
      const created = await createConversation("New Conversation", selectedModelId || null);
      setConversations((prev) => [created, ...prev]);
      setActiveConvId(created.id);
      setMessages([]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create conversation.");
    }
  }

  async function handleConfirmDeleteConv() {
    if (!pendingDeleteConv) return;
    const target = pendingDeleteConv;
    setPendingDeleteConv(null);
    try {
      await deleteConversation(target.id);
      setConversations((prev) => {
        const next = prev.filter((c) => c.id !== target.id);
        if (activeConvId === target.id) {
          setActiveConvId(next[0]?.id ?? null);
        }
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    }
  }

  async function sendMessage(textToSend: string) {
    const trimmed = textToSend.trim();
    if (!trimmed || streaming) return;
    if (!selectedModelId) {
      setError("No configured AI model available. Add an API key in .env.");
      return;
    }

    setError(null);
    let convId = activeConvId;
    if (!convId) {
      const created = await createConversation("New Conversation", selectedModelId);
      setConversations((prev) => [created, ...prev]);
      convId = created.id;
      setActiveConvId(convId);
    }

    const optimisticUser: MessageRecord = {
      id: `local-user-${Date.now()}`,
      conversation_id: convId,
      role: "user",
      content: trimmed,
      model_id: selectedModelId,
      citations_json: {},
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    setInputText("");
    setStreaming(true);
    setStatusLabel("Connecting...");
    setStreamingText("");
    setStreamingTraces([]);
    setStreamingCitations(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        convId,
        trimmed,
        selectedModelId,
        {
          onStatus: (msg) => setStatusLabel(msg),
          onToolCall: (tc) =>
            setStreamingTraces((prev) => [...prev, { id: tc.id, name: tc.name }]),
          onToolResult: (tr) =>
            setStreamingTraces((prev) =>
              prev.map((item) => (item.id === tr.id ? { ...item, summary: tr.summary } : item)),
            ),
          onToken: (delta) => setStreamingText((prev) => prev + delta),
          onCitations: (cits) => setStreamingCitations(cits),
          onDone: (doneInfo) => {
            setConversations((prev) =>
              prev.map((c) =>
                c.id === doneInfo.conversation_id
                  ? { ...c, title: doneInfo.title, model_id: selectedModelId, updated_at: new Date().toISOString() }
                  : c,
              ),
            );
          },
          onError: (detail) => setError(detail),
        },
        controller.signal,
      );

      // Reload persisted conversation messages so IDs and citations match DB cleanly
      const updated = await getConversation(convId);
      setMessages(updated.messages);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "Streaming failed.");
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
      setStatusLabel("");
      setStreamingText("");
      setStreamingTraces([]);
      setStreamingCitations(null);
    }
  }

  function handleFormSubmit(e: React.FormEvent) {
    e.preventDefault();
    void sendMessage(inputText);
  }

  function handleTextareaKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // IME safety check including keyCode 229 fallback (modern-web-guidance: ime-safe-enter-submit)
      if (e.nativeEvent.isComposing || e.keyCode === 229) {
        return;
      }
      formRef.current?.requestSubmit();
    }
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[280px_1fr]">
      {/* Left Sidebar: Conversations */}
      <aside className="flex flex-col rounded-xl border border-slate-800 bg-slate-900/70 p-3 lg:h-[calc(100vh-8.5rem)]">
        <button
          type="button"
          onClick={() => void handleNewChat()}
          disabled={streaming}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          <MessageSquarePlus className="h-4 w-4" />
          New Chat
        </button>

        <div className="mt-3 flex-1 space-y-1 overflow-y-auto pr-1">
          {conversations.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-slate-500">
              No conversations yet. Ask a question to start.
            </p>
          ) : (
            conversations.map((conv) => {
              const isActive = conv.id === activeConvId;
              return (
                <div
                  key={conv.id}
                  className={`group flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs transition-colors ${
                    isActive
                      ? "bg-indigo-500/15 text-indigo-200 ring-1 ring-inset ring-indigo-500/30"
                      : "text-slate-300 hover:bg-slate-800/60"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => !streaming && setActiveConvId(conv.id)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="truncate font-medium">{conv.title}</div>
                    {conv.model_id && (
                      <div className="mt-0.5 truncate font-mono text-[10px] text-slate-400">
                        {conv.model_id}
                      </div>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingDeleteConv(conv)}
                    disabled={streaming}
                    aria-label={`Delete conversation ${conv.title}`}
                    className="rounded p-1 text-slate-500 opacity-0 hover:bg-rose-500/15 hover:text-rose-300 group-hover:opacity-100 focus:opacity-100 disabled:opacity-0"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="flex flex-col rounded-xl border border-slate-800 bg-slate-900/70 lg:h-[calc(100vh-8.5rem)]">
        {/* Top Bar: Dynamic Model Switcher */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <Cpu className="h-4 w-4 text-indigo-400" />
            <label htmlFor={modelSelectId} className="text-xs font-medium text-slate-300">
              AI Model:
            </label>
            <select
              id={modelSelectId}
              value={selectedModelId}
              onChange={(e) => setSelectedModelId(e.target.value)}
              disabled={streaming}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-slate-100 focus:border-indigo-400 focus:outline-none disabled:opacity-60"
            >
              {models.length === 0 && <option value="">Loading models...</option>}
              {Object.entries(modelsByProvider).map(([provider, group]) => (
                <optgroup key={provider} label={provider.toUpperCase()}>
                  {group.map((m) => (
                    <option key={m.id} value={m.id} disabled={!m.configured}>
                      {m.label}
                      {m.configured ? "" : " (unconfigured in .env)"}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <span className="text-xs text-slate-400">
            Switch providers mid-chat anytime via LiteLLM
          </span>
        </div>

        {error && (
          <div
            role="alert"
            className="mx-4 mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
          >
            {error}
          </div>
        )}

        {/* Messages Scroll Area */}
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6">
          {messages.length === 0 && !streaming ? (
            <div className="mx-auto max-w-xl py-10 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-400">
                <Bot className="h-6 w-6" />
              </div>
              <h2 className="mt-3 text-base font-semibold text-slate-100">
                Ask Your Data Lakehouse
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-slate-400">
                I automatically route between semantic vector search over your PDFs and read-only DuckDB SQL over your Silver Excel tables.
              </p>

              <div className="mt-5 grid gap-2 text-left">
                {EXAMPLE_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => void sendMessage(prompt)}
                    className="rounded-lg border border-slate-800 bg-slate-950/70 px-3.5 py-2.5 text-xs text-slate-300 transition-colors hover:border-indigo-500/40 hover:bg-slate-800/60"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {msg.role === "assistant" && (
                  <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300">
                    <Bot className="h-4 w-4" />
                  </div>
                )}

                <div
                  className={`max-w-3xl rounded-xl px-4 py-3 text-sm ${
                    msg.role === "user"
                      ? "bg-indigo-600 text-white"
                      : "w-full border border-slate-800 bg-slate-950/70 text-slate-200"
                  }`}
                >
                  {msg.role === "assistant" && msg.model_id && (
                    <div className="mb-1.5 font-mono text-[10px] text-slate-400">
                      {msg.model_id}
                    </div>
                  )}
                  {msg.role === "user" ? (
                    <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                  ) : (
                    <>
                      <div className="markdown-body leading-relaxed">
                        <Markdown remarkPlugins={[remarkGfm]}>{msg.content}</Markdown>
                      </div>
                      <CitationsList citations={msg.citations_json} />
                    </>
                  )}
                </div>

                {msg.role === "user" && (
                  <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-slate-300">
                    <User className="h-4 w-4" />
                  </div>
                )}
              </div>
            ))
          )}

          {/* Live Streaming Assistant Bubble */}
          {streaming && (
            <div className="flex gap-3 justify-start">
              <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300">
                <Bot className="h-4 w-4 animate-pulse" />
              </div>
              <div className="w-full max-w-3xl rounded-xl border border-indigo-500/30 bg-slate-950/80 px-4 py-3 text-sm text-slate-200">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-indigo-300">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
                  <span>{statusLabel || "Streaming..."}</span>
                </div>

                {streamingTraces.length > 0 && (
                  <div className="mb-2.5 flex flex-wrap gap-1.5">
                    {streamingTraces.map((tr) => (
                      <span
                        key={tr.id}
                        className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300"
                      >
                        <Wrench className="h-3 w-3 text-cyan-400" />
                        {tr.name}
                        {tr.summary ? ` — ${tr.summary}` : " ..."}
                      </span>
                    ))}
                  </div>
                )}

                {streamingText && (
                  <div className="markdown-body leading-relaxed">
                    <Markdown remarkPlugins={[remarkGfm]}>{streamingText}</Markdown>
                  </div>
                )}

                <CitationsList citations={streamingCitations} />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Composer Form */}
        <form
          ref={formRef}
          onSubmit={handleFormSubmit}
          className="border-t border-slate-800 p-3 sm:p-4"
        >
          <label htmlFor={inputId} className="sr-only">
            Ask a question about your uploaded Excel and PDF files
          </label>
          <div className="flex items-end gap-2.5">
            <textarea
              id={inputId}
              rows={2}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleTextareaKeyDown}
              placeholder="Ask about your PDFs or Excel tables... (Enter to send, Shift+Enter for new line)"
              disabled={streaming}
              className="min-h-[48px] flex-1 resize-none rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none disabled:opacity-60"
            />
            {streaming ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="inline-flex h-11 items-center gap-1.5 rounded-xl bg-rose-600 px-4 text-sm font-medium text-white hover:bg-rose-500"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!inputText.trim() || !selectedModelId}
                className="inline-flex h-11 items-center gap-1.5 rounded-xl bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
                Send
              </button>
            )}
          </div>
        </form>
      </section>

      <ConfirmDialog
        open={pendingDeleteConv !== null}
        title="Delete conversation?"
        description={
          pendingDeleteConv
            ? `Delete "${pendingDeleteConv.title}" and all of its persisted messages?`
            : ""
        }
        confirmLabel="Delete Thread"
        onConfirm={() => void handleConfirmDeleteConv()}
        onCancel={() => setPendingDeleteConv(null)}
      />
    </div>
  );
}
