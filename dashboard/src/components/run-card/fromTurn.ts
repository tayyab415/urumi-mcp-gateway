import { doneLabel, liveParts, toolWords } from "@/lib/labels";
import type { Turn } from "@/state/chat";
import type { Run, RunStep } from "./types";

/** Adapts a chat turn to the first reference's run card (for the corner pill while the user is on another page). */
export function runFromTurn(t: Turn, model: string): Run {
  const steps: RunStep[] = t.steps.map((s) => {
    if (s.name === "plan") {
      return { id: s.id, kind: "plan", liveLabel: "Planning", doneLabel: "Planned the lookup", status: s.status === "active" ? "active" : "done", startedAt: s.startedAt, endedAt: s.endedAt, durationMs: s.durationMs };
    }
    const w = toolWords(s.name, s.input, s.readOnly);
    const live = liveParts(w);
    const status: RunStep["status"] = s.status === "waiting" ? "waiting" : s.status === "denied" ? "denied" : s.status;
    return {
      id: s.id, kind: w.kind,
      liveLabel: s.status === "waiting" ? `Waiting for approval to ${w.verb.toLowerCase().replace(/ing$/, "e").replace(/ee$/, "e")} ${w.argument || w.noun}` : `${live.verb} ${live.argument}`,
      doneLabel: s.status === "denied" ? `Denied: ${w.argument || w.noun}` : s.status === "unknown" ? "Timed out. Check before retrying" : s.status === "failed" ? `${w.verb} ${w.argument || w.noun} failed` : doneLabel(w, s.output, s.isError),
      status, startedAt: s.startedAt, endedAt: s.endedAt, durationMs: s.durationMs,
    };
  });
  const writes = t.steps.filter((s) => s.approval?.decision === "approve" && s.status === "done").length;
  const tools = t.steps.filter((s) => s.name !== "plan").length;
  const waiting = t.steps.find((s) => s.status === "waiting");
  const waitingWords = waiting ? toolWords(waiting.name, waiting.input, false) : null;
  const secs = Math.max(1, Math.round(((t.endedAt ?? Date.now()) - t.startedAt - t.pausedMs) / 1000));
  const status: Run["status"] = t.status === "waiting" ? "waiting" : t.status === "running" ? "running" : t.status === "done" ? "done" : t.status === "failed" ? "failed" : "stopped";
  return {
    id: t.id, model, status, steps, startedAt: t.startedAt, endedAt: t.endedAt, pausedMs: t.pausedMs,
    summary: `Done in ${secs}s · ${tools} ${tools === 1 ? "tool" : "tools"}${writes ? `, ${writes} ${writes === 1 ? "change" : "changes"}` : ""}`,
    approval: waitingWords ? { prompt: `Needs your approval to ${waitingWords.verb.toLowerCase().replace(/ing$/, "e").replace(/ee$/, "e")} ${waitingWords.argument || waitingWords.noun}` } : undefined,
    headline: t.status === "failed" ? "Failed" : t.status === "stopped" ? "Stopped" : undefined,
  };
}
