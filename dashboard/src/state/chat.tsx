/** Chat state shared by the Chat page, the docked assistant and the corner pill.
 *
 *  One user message is one turn. The chat service streams events for it; this
 *  file turns them into steps the task card renders. Approval decisions go back
 *  over a plain POST that the service waits on, so the hold is real. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api, type ChatMeta } from "@/lib/api";
import { useAuth } from "@/state/auth";

export type StepStatus = "active" | "waiting" | "done" | "failed" | "denied" | "unknown";

export interface ChatStep {
  id: string;
  name: string; // public tool name, or "plan"
  input: Record<string, unknown>;
  status: StepStatus;
  startedAt: number;
  endedAt?: number;
  durationMs?: number;
  output?: string;
  isError?: boolean;
  readOnly?: boolean;
  approval?: { requestId: string; summary: string; server: string; decision?: "approve" | "deny" | "timeout"; deciding?: boolean };
}

export type TurnStatus = "running" | "waiting" | "done" | "failed" | "stopped";

export interface Turn {
  id: string;
  prompt: string;
  title: string;
  status: TurnStatus;
  steps: ChatStep[];
  text: string;
  error?: string;
  startedAt: number;
  endedAt?: number;
  pausedMs: number;
  waitingSince?: number;
  tools?: string[];
  modelCalls?: number;
  toolCalls?: number;
  usage?: { input_tokens: number; cached_input_tokens: number; output_tokens: number };
  source: "page" | "dock";
}

interface ChatState {
  conversationId: string | null;
  meta: ChatMeta | null;
  turns: Turn[];
  activeTurn: Turn | null;
  send: (message: string, options?: { context?: string; source?: "page" | "dock" }) => Promise<void>;
  stop: () => void;
  decide: (turnId: string, stepId: string, decision: "approve" | "deny") => Promise<void>;
  reset: () => void;
}

const Ctx = createContext<ChatState | null>(null);

export const titleFor = (prompt: string) => {
  const words = prompt.trim().replace(/\s+/g, " ").split(" ");
  const short = words.slice(0, 8).join(" ").replace(/[?.!,:;]+$/, "");
  const t = short + (words.length > 8 ? "…" : "");
  return t ? t[0]!.toUpperCase() + t.slice(1) : "Untitled";
};

const isSettled = (t: Turn) => t.status === "done" || t.status === "failed" || t.status === "stopped";

export const CHANGE_EVENT = "urumi:data-changed";

export function ChatProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [meta, setMeta] = useState<ChatMeta | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const token = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    api.chat.meta().then(setMeta).catch(() => setMeta(null));
  }, []);

  // A new sign-in is a new conversation.
  useEffect(() => {
    token.current = null;
    setConversationId(null);
    setTurns([]);
  }, [me?.id]);

  const getToken = useCallback(async (fresh = false) => {
    if (!token.current || fresh) token.current = (await api.auth.chatToken()).token;
    return token.current;
  }, []);

  const patchTurn = useCallback((id: string, fn: (t: Turn) => Turn) => {
    setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));
  }, []);

  const send = useCallback(async (message: string, options: { context?: string; source?: "page" | "dock" } = {}) => {
    const id = `t${++seq.current}-${Date.now()}`;
    const startedAt = Date.now();
    const turn: Turn = {
      id, prompt: message, title: titleFor(message), status: "running", text: "", startedAt, pausedMs: 0, source: options.source ?? "page",
      steps: [{ id: `${id}-plan`, name: "plan", input: {}, status: "active", startedAt }],
    };
    setTurns((ts) => [...ts, turn]);
    const controller = new AbortController();
    abort.current = controller;

    const finishPlan = (t: Turn, label: "lookup" | "change" | "drop"): Turn => {
      const plan = t.steps.find((s) => s.name === "plan");
      if (!plan || plan.status !== "active") return t;
      if (label === "drop") return { ...t, steps: t.steps.filter((s) => s.name !== "plan") };
      const now = Date.now();
      return { ...t, steps: t.steps.map((s) => (s === plan ? { ...s, status: "done", endedAt: now, durationMs: now - s.startedAt, output: label } : s)) };
    };

    const handle = (ev: Record<string, unknown>) => {
      const type = ev.type as string;
      const now = Date.now();
      if (type === "conversation") {
        setConversationId(ev.id as string);
        if (typeof ev.model === "string") setMeta((m) => ({ model: ev.model as string, model_label: (ev.model_label as string) ?? (ev.model as string), approval_timeout_seconds: m?.approval_timeout_seconds ?? 300 }));
      } else if (type === "tools") {
        patchTurn(id, (t) => ({ ...t, tools: ev.tools as string[] }));
      } else if (type === "tool_call") {
        patchTurn(id, (t) => {
          const base = finishPlan(t, "lookup");
          if (base.steps.some((s) => s.id === ev.id)) return base;
          return { ...base, steps: [...base.steps, { id: ev.id as string, name: ev.name as string, input: (ev.input as Record<string, unknown>) ?? {}, status: "active", startedAt: now }] };
        });
      } else if (type === "approval_required") {
        const summary = (ev.summary as string) ?? "";
        // The chat service sends the server's display name as data ("Store: North"); older ones only had it in the summary.
        const serverName = typeof ev.server_name === "string" && ev.server_name
          ? ev.server_name
          : summary.includes(" on ") ? summary.slice(summary.lastIndexOf(" on ") + 4) : (ev.server as string);
        patchTurn(id, (t) => ({
          ...t, status: "waiting", waitingSince: now,
          steps: t.steps.map((s) => s.name === "plan" ? { ...s, output: "change" } : s.id === ev.call_id ? { ...s, status: "waiting", readOnly: false, approval: { requestId: ev.request_id as string, summary, server: serverName } } : s),
        }));
      } else if (type === "approval_decided") {
        const decision = ev.decision as "approve" | "deny" | "timeout";
        patchTurn(id, (t) => ({
          ...t, status: "running", waitingSince: undefined, pausedMs: t.pausedMs + (t.waitingSince ? now - t.waitingSince : 0),
          steps: t.steps.map((s) => s.approval?.requestId === ev.request_id
            ? { ...s, status: decision === "approve" ? "active" : "denied", startedAt: decision === "approve" ? now : s.startedAt, approval: { ...s.approval!, decision, deciding: false } }
            : s),
        }));
      } else if (type === "tool_result") {
        const output = (ev.output as string) ?? "";
        const denied = Boolean(ev.denied);
        const isError = Boolean(ev.is_error);
        const unknown = isError && /OUTCOME UNKNOWN|outcome is unknown/i.test(output);
        const status: StepStatus = denied ? "denied" : unknown ? "unknown" : isError ? "failed" : "done";
        patchTurn(id, (t) => ({
          ...t,
          steps: t.steps.map((s) => s.id === ev.id ? { ...s, status, endedAt: now, durationMs: typeof ev.duration_ms === "number" ? ev.duration_ms : now - s.startedAt, output, isError } : s),
        }));
        if (status === "done" && !denied) window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { name: ev.name, input: ev.input } }));
      } else if (type === "text") {
        patchTurn(id, (t) => ({ ...finishPlan(t, t.steps.length > 1 ? "lookup" : "drop"), text: t.text + (ev.delta as string) }));
      } else if (type === "done") {
        patchTurn(id, (t) => ({ ...finishPlan(t, "drop"), status: "done", endedAt: now, modelCalls: ev.steps as number, toolCalls: ev.tool_calls as number, usage: ev.usage as Turn["usage"] }));
        window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { name: "turn", input: {} } }));
      } else if (type === "error") {
        patchTurn(id, (t) => ({ ...finishPlan(t, "drop"), status: "failed", endedAt: now, error: ev.message as string, steps: t.steps.map((s) => (s.status === "active" || s.status === "waiting" ? { ...s, status: "failed", endedAt: now } : s)) }));
      }
    };

    try {
      let tok = await getToken();
      const body = JSON.stringify({ message: options.context ? `${options.context}\n\n${message}` : message, conversation_id: conversationId });
      let res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` }, body, signal: controller.signal });
      if (res.status === 401) {
        tok = await getToken(true);
        res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` }, body, signal: controller.signal });
      }
      if (!res.ok || !res.body) {
        let msg = `The chat service answered ${res.status}.`;
        try { msg = ((await res.json()) as { error?: string }).error ?? msg; } catch { /* keep */ }
        if (res.status === 404) setConversationId(null);
        throw new ApiError(res.status, msg);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut: number;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          for (const line of frame.split("\n")) {
            if (line.startsWith("data: ")) {
              try { handle(JSON.parse(line.slice(6))); } catch { /* ignore a malformed frame */ }
            }
          }
        }
      }
      patchTurn(id, (t) => (isSettled(t) ? t : { ...finishPlan(t, "drop"), status: "stopped", endedAt: Date.now() }));
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      patchTurn(id, (t) => (isSettled(t) ? t : { ...finishPlan(t, "drop"), status: aborted ? "stopped" : "failed", endedAt: Date.now(), error: aborted ? undefined : e instanceof Error ? e.message : "The request failed", steps: t.steps.map((s) => (s.status === "active" || s.status === "waiting" ? { ...s, status: aborted ? "failed" : "failed", endedAt: Date.now() } : s)) }));
    } finally {
      if (abort.current === controller) abort.current = null;
    }
  }, [conversationId, getToken, patchTurn]);

  const stop = useCallback(() => abort.current?.abort(), []);

  const decide = useCallback(async (turnId: string, stepId: string, decision: "approve" | "deny") => {
    const turn = turns.find((t) => t.id === turnId);
    const step = turn?.steps.find((s) => s.id === stepId);
    if (!turn || !step?.approval || !conversationId) return;
    patchTurn(turnId, (t) => ({ ...t, steps: t.steps.map((s) => (s.id === stepId ? { ...s, approval: { ...s.approval!, deciding: true } } : s)) }));
    try {
      await api.chat.approve(await getToken(), conversationId, step.approval.requestId, decision);
    } catch (e) {
      patchTurn(turnId, (t) => ({ ...t, steps: t.steps.map((s) => (s.id === stepId ? { ...s, approval: { ...s.approval!, deciding: false } } : s)) }));
      throw e;
    }
  }, [turns, conversationId, getToken, patchTurn]);

  const reset = useCallback(() => {
    abort.current?.abort();
    setConversationId(null);
    setTurns([]);
  }, []);

  const activeTurn = useMemo(() => [...turns].reverse().find((t) => !isSettled(t)) ?? null, [turns]);
  const value = useMemo<ChatState>(() => ({ conversationId, meta, turns, activeTurn, send, stop, decide, reset }), [conversationId, meta, turns, activeTurn, send, stop, decide, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useChat(): ChatState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useChat outside ChatProvider");
  return v;
}

/** Elapsed time of a turn, excluding time spent waiting for the user. */
export function turnElapsed(t: Turn, now: number): number {
  const end = t.endedAt ?? now;
  const waiting = t.waitingSince ? now - t.waitingSince : 0;
  return Math.max(0, end - t.startedAt - t.pausedMs - waiting);
}

export const writesIn = (t: Turn) => t.steps.filter((s) => s.name !== "plan" && s.status === "done" && (s.readOnly === false || s.approval !== undefined));
