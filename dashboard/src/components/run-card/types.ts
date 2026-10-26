import type { StepKind } from "@/lib/labels";

export type RunStepStatus = "active" | "waiting" | "done" | "failed" | "unknown" | "denied" | "stopped" | "paused";

export interface RunStep {
  id: string;
  kind: StepKind;
  liveLabel: string; // "Reading 12 files"
  doneLabel?: string; // "Read 12 files"
  status: RunStepStatus;
  startedAt: number; // ms epoch
  endedAt?: number;
  durationMs?: number; // overrides endedAt - startedAt when the backend measured it
  progress?: { done: number; total: number };
}

export type RunStatus = "running" | "waiting" | "paused" | "done" | "failed" | "stopped" | "denied" | "unknown";

export interface Run {
  id: string;
  model: string;
  status: RunStatus;
  steps: RunStep[];
  plannedSteps?: number; // unknown for an open-ended tool loop
  startedAt: number;
  endedAt?: number;
  pausedMs?: number; // time spent waiting for the user, not counted
  summary?: string; // "Done in 13s · 6 files changed"
  approval?: { prompt: string };
  headline?: string; // explicit override for failed / stopped wording
}
