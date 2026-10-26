import { useEffect, useMemo, useRef, useState } from "react";
import { Play, RotateCcw } from "lucide-react";
import { AgentRunCard } from "@/components/run-card/AgentRunCard";
import type { Run } from "@/components/run-card/types";
import { TaskCard } from "@/components/task-card/TaskCard";
import { REFERENCE_SCRIPT, playScript, type Controls } from "@/lib/scripted-run";
import type { Turn } from "@/state/chat";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import styles from "./Lab.module.css";

function buildStates(): { label: string; run: Run; expanded: boolean }[] {
const t0 = Date.now() - 14000;
const base = (over: Partial<Run>): Run => ({ id: "x", model: "Claude Sonnet 5.5", status: "running", plannedSteps: 6, startedAt: t0, steps: [], ...over });
const step = (id: string, kind: Run["steps"][number]["kind"], live: string, done: string, status: Run["steps"][number]["status"], ms: number) => ({ id, kind, liveLabel: live, doneLabel: done, status, startedAt: t0, endedAt: t0 + ms, durationMs: ms });
return [
  { label: "Collapsed pill, running", expanded: false, run: base({ steps: [{ ...step("1", "read", "Reading 12 files", "Read 12 files", "active", 0), endedAt: undefined, durationMs: undefined, startedAt: Date.now() }] }) },
  { label: "Expanded, running", expanded: true, run: base({ steps: [step("1", "search", "Listing products", "Listed 5 products", "done", 1100), step("2", "search", "Finding orders for today", "Found 3 orders today", "done", 1200), { ...step("3", "read", "Reading order #1043", "Read order #1043", "active", 0), endedAt: undefined, durationMs: undefined, startedAt: Date.now() - 1000 }] }) },
  { label: "Needs approval", expanded: true, run: base({ status: "waiting", approval: { prompt: "Needs your approval to change order #1043" }, steps: [step("1", "search", "Finding order", "Found order #1043", "done", 1300), step("2", "edit", "Preparing update", "Prepared update", "done", 1100), { ...step("3", "shell", "Waiting for approval to update order #1043", "", "waiting", 0), endedAt: undefined, durationMs: undefined, startedAt: Date.now() - 2000 }] }) },
  { label: "Done", expanded: true, run: base({ status: "done", endedAt: t0 + 4000, summary: "Done in 4s · 3 tools", plannedSteps: 3, steps: [step("1", "search", "", "Found order #1043", "done", 1000), step("2", "edit", "", "Updated order", "done", 1500), step("3", "shell", "", "Passed tests", "done", 1500)] }) },
  { label: "Paused", expanded: true, run: base({ status: "paused", headline: "Paused at step 3", steps: [step("1", "search", "", "Found order #1043", "done", 1100), step("2", "edit", "", "Prepared update", "done", 900), { ...step("3", "shell", "Paused before applying", "", "paused", 0), endedAt: undefined, durationMs: undefined, startedAt: Date.now() - 500 }] }) },
  { label: "Denied", expanded: true, run: base({ status: "denied", endedAt: t0 + 5000, headline: "Stopped. You denied the change", steps: [step("1", "search", "", "Found order #1043", "done", 1200), step("2", "edit", "", "Prepared update", "done", 1100), step("3", "shell", "", "Denied: change order #1043", "denied", 0)] }) },
  { label: "Failed", expanded: true, run: base({ status: "failed", endedAt: t0 + 4000, headline: "Failed at step 2", steps: [step("1", "search", "", "Found supplier", "done", 1000), step("2", "package", "", "Supplier unreachable", "failed", 2400), step("3", "read", "", "Aborted run", "stopped", 0)] }) },
  { label: "Outcome unknown", expanded: true, run: base({ status: "unknown", endedAt: t0 + 5000, headline: "Not sure the change applied", steps: [step("1", "search", "", "Found order #1043", "done", 1200), step("2", "edit", "", "Timed out. Check the order before retrying", "unknown", 2800), step("3", "read", "", "No changes made", "done", 0)] }) },
];
}

/** The design lab: every card state side by side, and the reference sequence replayed with real timers. */
export function LabPage() {
  const [run, setRun] = useState<Run | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [floating, setFloating] = useState(true);
  const STATES = useMemo(buildStates, []);
  const controls = useRef<Controls | null>(null);
  const play = () => {
    controls.current?.dispose();
    controls.current = playScript(REFERENCE_SCRIPT, "Claude Sonnet 5.5", setRun, { summary: (r) => `Done in ${Math.round(((r.endedAt ?? Date.now()) - r.startedAt - (r.pausedMs ?? 0)) / 1000)}s · 6 files changed` });
  };
  useEffect(() => () => controls.current?.dispose(), []);

  return (
    <>
      <PageHeader title="Design lab" subtitle="The agent run card and the chat task card in every state, plus the reference clip's sequence replayed with real timers. For checking motion, not a product page." />
      <div className={styles.controls}>
        <Button variant="primary" icon={run ? <RotateCcw /> : <Play />} onClick={play}>{run ? "Replay the reference" : "Play the reference sequence"}</Button>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "var(--text-secondary)" }}><Switch label="Expanded" checked={expanded} onChange={setExpanded} /> expanded</label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "var(--text-secondary)" }}><Switch label="Floating shadow" checked={floating} onChange={setFloating} /> floating</label>
      </div>
      {run && (
        <div style={{ padding: 32, borderRadius: 22, border: "1px dashed var(--border)", display: "flex", justifyContent: "center" }}>
          <AgentRunCard run={run} expanded={expanded} floating={floating} onToggle={() => setExpanded((v) => !v)}
            onPause={() => controls.current?.pause()} onResume={() => controls.current?.resume()} onStop={() => controls.current?.stop()}
            onApprove={() => controls.current?.approve()} onDeny={() => controls.current?.deny()} openRunHref="#" />
        </div>
      )}
      <div className={styles.h}>Agent run card · states</div>
      <div className={styles.grid}>
        {STATES.map((s) => (
          <div key={s.label} className={styles.cell}>
            <AgentRunCard run={s.run} expanded={s.expanded} openRunHref="#" />
            <div className={styles.caption}>{s.label}</div>
          </div>
        ))}
      </div>
      <div className={styles.h}>Chat task card · states</div>
      <TaskCardDemo />
    </>
  );
}

function TaskCardDemo() {
  const [turn, setTurn] = useState<Turn>(() => makeTurn("waiting"));
  const [key, setKey] = useState(0);
  const play = (kind: "waiting" | "live" | "done") => { setTurn(makeTurn(kind)); setKey((k) => k + 1); };
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className={styles.controls} style={{ marginBottom: 0 }}>
        <Button size="sm" onClick={() => play("live")}>Live</Button>
        <Button size="sm" onClick={() => play("waiting")}>Waiting for you</Button>
        <Button size="sm" onClick={() => play("done")}>Done</Button>
      </div>
      <TaskCard key={key} turn={turn} modelLabel="Kimi K3"
        onApprove={() => setTurn((t) => ({ ...t, status: "done", endedAt: Date.now(), steps: t.steps.map((s) => (s.status === "waiting" ? { ...s, status: "done", endedAt: Date.now(), durationMs: 1600, output: "{\"id\": 1043, \"status\": \"completed\"}", approval: { ...s.approval!, decision: "approve" } } : s)) }))}
        onDeny={() => setTurn((t) => ({ ...t, status: "done", endedAt: Date.now(), steps: t.steps.map((s) => (s.status === "waiting" ? { ...s, status: "denied", endedAt: Date.now(), durationMs: 0, approval: { ...s.approval!, decision: "deny" } } : s)) }))}
      />
    </div>
  );
}

function makeTurn(kind: "waiting" | "live" | "done"): Turn {
  const t0 = Date.now() - 6000;
  const steps: Turn["steps"] = [
    { id: "p", name: "plan", input: {}, status: "done", startedAt: t0, endedAt: t0 + 700, durationMs: 700, output: "change" },
    { id: "a", name: "woo__get_order", input: { order_id: 1043 }, status: "done", startedAt: t0 + 700, endedAt: t0 + 1500, durationMs: 800, output: "{\"id\": 1043, \"status\": \"processing\", \"total\": \"84.00\"}", readOnly: true },
    { id: "b", name: "shipping__quote_shipping", input: { order_id: 1043 }, status: "done", startedAt: t0 + 1500, endedAt: t0 + 2500, durationMs: 1000, output: "[{\"carrier\": \"DHL\"}, {\"carrier\": \"UPS\"}]", readOnly: true },
  ];
  if (kind === "live") steps.push({ id: "c", name: "woo__list_orders", input: { status: "processing" }, status: "active", startedAt: Date.now() - 800, readOnly: true });
  if (kind === "waiting") steps.push({ id: "c", name: "woo__update_order_status", input: { order_id: 1043, status: "completed" }, status: "waiting", startedAt: Date.now() - 1200, readOnly: false, approval: { requestId: "demo", summary: "Run woo__update_order_status on WooCommerce", server: "woo" } });
  if (kind === "done") steps.push({ id: "c", name: "woo__update_order_status", input: { order_id: 1043, status: "completed" }, status: "done", startedAt: t0 + 2500, endedAt: t0 + 4100, durationMs: 1600, output: "{\"id\": 1043, \"status\": \"completed\"}", readOnly: false, approval: { requestId: "demo", summary: "", server: "woo", decision: "approve" } });
  return { id: `demo-${kind}`, prompt: "Mark order #1043 as completed", title: "Mark order #1043 as completed", status: kind === "live" ? "running" : kind === "waiting" ? "waiting" : "done", steps, text: kind === "done" ? "Order #1043 is now Completed. WooCommerce emailed the customer." : "", startedAt: t0, endedAt: kind === "done" ? t0 + 4100 : undefined, pausedMs: 0, waitingSince: kind === "waiting" ? Date.now() - 1200 : undefined, source: "page" };
}
