/** A scripted run for demos: store provisioning on the Stores page and the design lab.
 *  It drives the same Run model the real cards use, with real timers, so the motion can be judged honestly. */
import type { Run, RunStep } from "@/components/run-card/types";
import type { StepKind } from "@/lib/labels";

export interface ScriptStep { kind: StepKind; live: string; done: string; ms: number; progressTotal?: number; approval?: string; waitingLabel?: string; fail?: string }

export const PROVISION_SCRIPT: ScriptStep[] = [
  { kind: "shell", live: "Creating namespace", done: "Created namespace", ms: 700 },
  { kind: "package", live: "Waiting for the database", done: "Database ready", ms: 2400 },
  { kind: "read", live: "Starting WordPress", done: "WordPress started", ms: 2600 },
  { kind: "edit", live: "Installing WooCommerce", done: "Installed WooCommerce", ms: 3200, progressTotal: 12 },
  { kind: "search", live: "Seeding 5 products", done: "Seeded 5 products", ms: 1400 },
  { kind: "shell", live: "Running health checks", done: "Passed 4 of 4 checks", ms: 1200 },
];

export const REFERENCE_SCRIPT: ScriptStep[] = [
  { kind: "read", live: "Reading 12 files", done: "Read 12 files", ms: 1900 },
  { kind: "search", live: "Searching for prorateInvoice", done: "Found 4 call sites", ms: 1500 },
  { kind: "edit", live: "Editing src/billing/invoice.ts", done: "Edited src/billing/invoice.ts", ms: 2500 },
  { kind: "edit", live: "Updating 5 test files", done: "Updated 5 test files", ms: 1600 },
  { kind: "shell", live: "Running tests", done: "Passed 12 of 12 tests", ms: 3000, progressTotal: 12 },
  { kind: "shell", live: "Pushing fix/proration-rounding", done: "Pushed fix/proration-rounding", ms: 1400, approval: "Needs your approval to run git push", waitingLabel: "Waiting for approval to run git push" },
];

export type Controls = { pause(): void; resume(): void; stop(): void; approve(): void; deny(): void; dispose(): void };

/** Plays a script. `onChange` receives a fresh Run each tick. Approval waits for approve() or deny(). */
export function playScript(script: ScriptStep[], model: string, onChange: (run: Run) => void, options: { summary?: (run: Run) => string; failAt?: number } = {}): Controls {
  const startedAt = Date.now();
  let run: Run = { id: `r${startedAt}`, model, status: "running", steps: [], plannedSteps: script.length, startedAt, pausedMs: 0 };
  let timer: ReturnType<typeof setTimeout> | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let index = -1;
  let pausedAt: number | null = null;
  let waitingAt: number | null = null;
  let stepStartedAt = 0;
  let disposed = false;

  const emit = () => { if (!disposed) onChange({ ...run, steps: run.steps.map((s) => ({ ...s })) }); };
  const current = () => run.steps[run.steps.length - 1];

  const settleStep = (status: RunStep["status"], doneLabel?: string) => {
    const s = current();
    if (!s) return;
    s.status = status;
    s.endedAt = Date.now();
    if (doneLabel) s.doneLabel = doneLabel;
    if (s.progress) s.progress = { ...s.progress, done: s.progress.total };
  };

  const finish = () => {
    run.status = "done";
    run.endedAt = Date.now();
    run.summary = options.summary ? options.summary(run) : `Done in ${Math.round((run.endedAt - run.startedAt - (run.pausedMs ?? 0)) / 1000)}s · ${run.steps.length} steps`;
    stopTicker();
    emit();
  };

  const stopTicker = () => { if (ticker) { clearInterval(ticker); ticker = null; } };

  const next = () => {
    if (disposed) return;
    index += 1;
    const step = script[index];
    if (!step) { finish(); return; }
    stepStartedAt = Date.now();
    run.steps.push({ id: `s${index}`, kind: step.kind, liveLabel: step.live, doneLabel: step.done, status: "active", startedAt: stepStartedAt, progress: step.progressTotal ? { done: 0, total: step.progressTotal } : undefined });
    emit();
    if (step.progressTotal) {
      const total = step.progressTotal;
      ticker = setInterval(() => {
        if (pausedAt !== null) return;
        const s = current();
        if (!s?.progress) return;
        const done = Math.min(total, Math.floor(((Date.now() - stepStartedAt) / step.ms) * total));
        if (done !== s.progress.done) { s.progress = { done, total }; s.liveLabel = `${step.live} ${done} of ${total}`; emit(); }
      }, 120);
    }
    timer = setTimeout(() => {
      stopTicker();
      if (options.failAt === index) {
        settleStep("failed", step.fail ?? "Upstream timed out");
        run.status = "failed"; run.endedAt = Date.now(); run.headline = `Failed at step ${index + 1}`;
        emit();
        return;
      }
      if (step.approval) {
        const s = current()!;
        s.status = "waiting";
        s.liveLabel = step.waitingLabel ?? `Waiting for approval: ${step.live.charAt(0).toLowerCase()}${step.live.slice(1)}`;
        run.status = "waiting";
        run.approval = { prompt: step.approval };
        waitingAt = Date.now();
        emit();
        return;
      }
      settleStep("done");
      next();
    }, step.ms);
  };

  const clearTimer = () => { if (timer) { clearTimeout(timer); timer = null; } };

  next();

  return {
    pause() {
      if (run.status !== "running") return;
      clearTimer(); pausedAt = Date.now();
      run.status = "paused"; run.headline = `Paused at step ${index + 1}`;
      const s = current(); if (s) s.status = "paused";
      emit();
    },
    resume() {
      if (run.status !== "paused" || pausedAt === null) return;
      const step = script[index]!;
      const elapsed = pausedAt - stepStartedAt;
      run.pausedMs = (run.pausedMs ?? 0) + (Date.now() - pausedAt);
      stepStartedAt = Date.now() - elapsed;
      const s = current(); if (s) { s.status = "active"; s.startedAt = stepStartedAt; }
      pausedAt = null;
      run.status = "running"; run.headline = undefined;
      emit();
      timer = setTimeout(() => { stopTicker(); settleStep("done"); next(); }, Math.max(0, step.ms - elapsed));
    },
    stop() {
      clearTimer(); stopTicker();
      const s = current(); if (s && (s.status === "active" || s.status === "paused" || s.status === "waiting")) { s.status = "stopped"; s.doneLabel = "Stopped"; s.endedAt = Date.now(); }
      run.status = "stopped"; run.endedAt = Date.now(); run.headline = `Stopped at step ${index + 1}`;
      emit();
    },
    approve() {
      if (run.status !== "waiting") return;
      const step = script[index]!;
      run.pausedMs = (run.pausedMs ?? 0) + (waitingAt ? Date.now() - waitingAt : 0);
      waitingAt = null;
      const s = current()!;
      s.status = "active"; s.liveLabel = step.live; s.startedAt = Date.now(); stepStartedAt = s.startedAt;
      run.status = "running"; run.approval = undefined;
      emit();
      timer = setTimeout(() => { settleStep("done"); next(); }, step.ms);
    },
    deny() {
      if (run.status !== "waiting") return;
      const step = script[index]!;
      run.pausedMs = (run.pausedMs ?? 0) + (waitingAt ? Date.now() - waitingAt : 0);
      const s = current()!;
      s.status = "denied"; s.doneLabel = `Denied: ${step.live.charAt(0).toLowerCase()}${step.live.slice(1)}`; s.endedAt = Date.now();
      run.status = "denied"; run.endedAt = Date.now(); run.approval = undefined; run.headline = `Stopped. You denied ${step.live.charAt(0).toLowerCase()}${step.live.slice(1)}`;
      emit();
    },
    dispose() { disposed = true; clearTimer(); stopTicker(); },
  };
}
