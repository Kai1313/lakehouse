export interface RagHealthStatus {
  status: string;
  postgres: boolean;
  duckdb: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  provider: string;
  configured: boolean;
  supports_tools: boolean;
}

export interface ModelsResponse {
  models: ModelOption[];
  default_model_id: string | NoneOrString;
}

type NoneOrString = string | null;

export interface PdfCitation {
  chunk_id: string;
  filename: string;
  page_number: number | null;
  chunk_index: number;
  score: number;
  content: string;
}

export interface SqlCitation {
  sql: string;
  explanation?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

export interface CitationsPayload {
  pdf_citations?: PdfCitation[];
  sql_citations?: SqlCitation[];
}

export interface MessageRecord {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  model_id: string | null;
  citations_json: CitationsPayload | unknown[];
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  model_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageRecord[];
}

export interface StreamCallbacks {
  onStatus?: (message: string) => void;
  onToolCall?: (payload: { id: string; name: string; arguments: Record<string, unknown> }) => void;
  onToolResult?: (payload: { id: string; name: string; summary: string }) => void;
  onToken?: (delta: string) => void;
  onCitations?: (payload: CitationsPayload) => void;
  onDone?: (payload: { message_id: string; conversation_id: string; title: string }) => void;
  onError?: (detail: string) => void;
}

const BASE = "/api/rag";

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function getRagHealth(): Promise<RagHealthStatus> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listModels(): Promise<ModelsResponse> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/conversations`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createConversation(
  title = "New Conversation",
  modelId?: string | null,
): Promise<ConversationSummary> {
  const res = await fetch(`${BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, model_id: modelId ?? null }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const res = await fetch(`${BASE}/conversations/${conversationId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteConversation(conversationId: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${BASE}/conversations/${conversationId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function streamChat(
  conversationId: string,
  message: string,
  modelId: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${conversationId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, model_id: modelId }),
    signal,
  });

  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  if (!res.body) {
    throw new Error("Streaming response body is missing.");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // SSE frames are delimited by blank lines (\r\n\r\n or \n\n)
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      if (!frame.trim()) continue;
      let eventName = "message";
      const dataLines: string[] = [];

      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        }
      }

      if (dataLines.length === 0) continue;
      const rawJson = dataLines.join("\n");
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(rawJson);
      } catch {
        continue;
      }

      switch (eventName) {
        case "status":
          callbacks.onStatus?.(String(parsed.message ?? ""));
          break;
        case "tool_call":
          callbacks.onToolCall?.(parsed as { id: string; name: string; arguments: Record<string, unknown> });
          break;
        case "tool_result":
          callbacks.onToolResult?.(parsed as { id: string; name: string; summary: string });
          break;
        case "token":
          callbacks.onToken?.(String(parsed.delta ?? ""));
          break;
        case "citations":
          callbacks.onCitations?.(parsed as unknown as CitationsPayload);
          break;
        case "done":
          callbacks.onDone?.(parsed as { message_id: string; conversation_id: string; title: string });
          break;
        case "error":
          callbacks.onError?.(String(parsed.detail ?? "Unknown streaming error"));
          break;
      }
    }
  }
}
