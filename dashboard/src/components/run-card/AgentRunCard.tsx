import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, type Transition } from "motion/react";
import { ArrowUpRight, Check, Pause, Play, RotateCcw, Square, SquareTerminal, X, type LucideIcon } from "lucide-react";
import { C, MAX_ROWS, fade, roll, settle, snap } from "@/lib/motion";
import { fmtDuration, fmtElapsed } from "@/lib/format";
import { useNow } from "@/lib/clock";
import { useReducedFlag } from "@/lib/reduced";
import { cx } from "@/lib/cx";
import { StatusOrb, type OrbState } from "@/components/ui/StatusOrb";
import { StepIcon } from "@/components/ui/StepIcon";
import type { Run, RunStep } from "./types";
import styles from "./AgentRunCard.module.css";

export interface AgentRunCardProps {
  run: Run;
  expanded: boolean;
  onToggle?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onStop?: () => void;
  onApprove?: () => void;
  onDeny?: () => void;
  onRetry?: () => void;
  openRunHref?: string;
  onOpenRun?: () => void;
  floating?: boolean; // the collapsed pill and corner cards float; page cards rest on their border
  className?: string;
}

export function orbStateFor(run: Run): OrbState {
  switch (run.status) {
    case "running": return "running";
    case "paused": return "paused";
    case "waiting": return "waiting";
    case "done": return "done";
    case "failed": return "failed";
    case "unknown": return "unknown";
    default: return "stopped";
  }
}

function activeIndex(run: Run): number {
  const i = run.steps.findIndex((s) => s.status === "active" || s.status === "waiting" || s.status === "paused");
  return i >= 0 ? i : run.steps.length - 1;
}

/** The headline mirrors the active row, except when the card speaks to the user (approval, done, failure). */
function headlineFor(run: Run): { text: string; icon: LucideIcon | null; kind: RunStep["kind"] | null; live: boolean; key: string } {
  const idx = activeIndex(run);
  const step = run.steps[idx];
  const n = idx + 1;
  switch (run.status) {
    case "waiting": return { text: run.approval?.prompt ?? "Needs your approval", icon: SquareTerminal, kind: null, live: false, key: `wait-${step?.id}` };
    case "done": return { text: run.summary ?? "Done", icon: null, kind: null, live: false, key: "done" };
    case "paused": return { text: run.headline ?? `Paused at step ${n}`, icon: Pause, kind: null, live: false, key: `paused-${n}` };
    case "failed": return { text: run.headline ?? `Failed at step ${n}`, icon: null, kind: step?.kind ?? null, live: false, key: `failed-${n}` };
    case "stopped": return { text: run.headline ?? `Stopped at step ${n}`, icon: null, kind: step?.kind ?? null, live: false, key: `stopped-${n}` };
    case "denied": return { text: run.headline ?? "Stopped. You denied the change", icon: null, kind: step?.kind ?? null, live: false, key: `denied-${n}` };
    case "unknown": return { text: run.headline ?? "Not sure the change applied", icon: null, kind: step?.kind ?? null, live: false, key: `unknown-${n}` };
    default: return { text: step?.liveLabel ?? "Starting", icon: null, kind: step?.kind ?? "plan", live: true, key: `live-${step?.id ?? "start"}` };
  }
}

export function AgentRunCard({ run, expanded, onToggle, onPause, onResume, onStop, onApprove, onDeny, onRetry, openRunHref, onOpenRun, floating, className }: AgentRunCardProps) {
  const reduced = useReducedFlag();
  const headline = headlineFor(run);
  const settled = run.status === "done" || run.status === "failed" || run.status === "stopped" || run.status === "denied" || run.status === "unknown";
  const idx = activeIndex(run);
  const total = Math.min(8, Math.max(run.plannedSteps ?? run.steps.length, 1));
  const doneCount = run.steps.filter((s) => s.status === "done").length;

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onToggle?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, onToggle]);

  return (
    <div className={cx(styles.wrap, className)}>
      <motion.div
        layout
        transition={reduced ? { duration: 0.12 } : settle}
        style={{ borderRadius: expanded ? 22 : 20 }}
        className={cx(styles.card, expanded ? styles.expanded : styles.collapsed, floating && styles.floating)}
      >
        <button type="button" className={styles.header} onClick={onToggle} aria-expanded={expanded} aria-label={expanded ? "Collapse run" : "Expand run"}>
          <StatusOrb state={orbStateFor(run)} />
          <div className={styles.headlineBox} aria-live={run.status === "waiting" ? "assertive" : "polite"}>
            <AnimatePresence initial={false} mode="popLayout">
              <motion.div key={headline.key} className={styles.headlineRow} initial={roll.initial} animate={roll.animate} exit={roll.exit} transition={snap} layout="position">
                {headline.icon ? <headline.icon className={styles.glyph} strokeWidth={1.75} /> : headline.kind ? <StepIcon kind={headline.kind} className={styles.glyph} /> : null}
                <span className={cx(styles.headlineText, headline.live ? "shimmer" : styles.bright)}>{headline.text}</span>
              </motion.div>
            </AnimatePresence>
          </div>
          <AnimatePresence initial={false}>
            {run.status !== "done" && (
              <motion.span key="timer" className={cx(styles.timer, "tnum")} exit={{ opacity: 0, transition: fade }}>
                <ElapsedTimer run={run} />
              </motion.span>
            )}
          </AnimatePresence>
        </button>

        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div key="body" className={styles.body} initial={{ opacity: 0 }} animate={{ opacity: 1, transition: { ...fade, delay: 0.05 } }} exit={{ opacity: 0, transition: fade }}>
              <div className={styles.meta}>
                <span>{run.model}</span>
                <span className="tnum">
                  {run.status === "done" ? `${run.steps.length} of ${run.steps.length} steps` : run.plannedSteps ? `Step ${Math.min(idx + 1, run.plannedSteps)} of ${run.plannedSteps}` : `Step ${idx + 1}`}
                </span>
              </div>
              <SegmentedProgress total={total} done={run.status === "done" ? total : doneCount} active={settled ? null : Math.min(idx, total - 1)} fraction={fractionOf(run.steps[idx])} />
              <StepLog steps={run.steps} />
              <ActionBar run={run} onPause={onPause} onResume={onResume} onStop={onStop} onApprove={onApprove} onDeny={onDeny} onRetry={onRetry} openRunHref={openRunHref} onOpenRun={onOpenRun} />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}

function fractionOf(step: RunStep | undefined): number {
  if (!step?.progress || step.progress.total <= 0) return 0;
  return Math.max(0, Math.min(1, step.progress.done / step.progress.total));
}

function ElapsedTimer({ run }: { run: Run }) {
  const now = useNow();
  const end = run.endedAt ?? now;
  return <>{fmtElapsed(end - run.startedAt - (run.pausedMs ?? 0))}</>;
}

/** One thin segment per planned step. The fill slides in with `x`, so its rounded end keeps its shape at every value. */
export function SegmentedProgress({ total, done, active, fraction }: { total: number; done: number; active: number | null; fraction: number }) {
  return (
    <div className={styles.segments} role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
      {Array.from({ length: total }, (_, i) => (
        <div key={i} className={cx(styles.segment, i === active && styles.segmentActive)}>
          <motion.div className={styles.fill} initial={false} animate={{ x: `${((i < done ? 1 : i === active ? fraction : 0) - 1) * 100}%` }} transition={snap} />
        </div>
      ))}
    </div>
  );
}

function StepLog({ steps }: { steps: RunStep[] }) {
  const visible = steps.slice(-MAX_ROWS);
  return (
    <motion.ul layout transition={settle} className={styles.log}>
      <AnimatePresence initial={false} mode="popLayout">
        {visible.map((s) => (
          <motion.li key={s.id} layout="position" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fade }} transition={settle} className={styles.row}>
            <StepIcon kind={s.kind} className={cx(styles.rowIcon, iconClass(s.status))} />
            <RowLabel step={s} />
            <Duration step={s} />
          </motion.li>
        ))}
      </AnimatePresence>
    </motion.ul>
  );
}

const iconClass = (status: RunStep["status"]) =>
  status === "active" ? styles.iconActive : status === "waiting" ? styles.iconWait : status === "failed" ? styles.iconFail : status === "unknown" ? styles.iconUnknown : styles.iconDone;
const textClass = (status: RunStep["status"]) =>
  status === "active" ? "shimmer" : status === "waiting" ? styles.textWait : status === "failed" ? styles.textFail : status === "unknown" ? styles.textUnknown : styles.textDone;

/** Tense change in place: the live label crossfades into the done label at the same position. */
function RowLabel({ step }: { step: RunStep }) {
  const settledText = step.doneLabel ?? step.liveLabel;
  const isLive = step.status === "active" || step.status === "waiting" || step.status === "paused";
  const text = isLive ? step.liveLabel : settledText;
  return (
    <div className={styles.rowLabel}>
      <AnimatePresence initial={false} mode="sync">
        <motion.span key={`${isLive ? "live" : "done"}-${text}`} className={cx(styles.rowText, textClass(step.status))} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
          {text}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

function Duration({ step }: { step: RunStep }) {
  const now = useNow();
  const settled = step.status !== "active" && step.status !== "waiting" && step.status !== "paused";
  const ms = step.durationMs ?? (step.endedAt ?? now) - step.startedAt;
  return <span className={cx(styles.duration, "tnum")}>{fmtDuration(ms, settled)}</span>;
}

/** One button component morphs between roles. Stable keys (slot 0, slot 1) let motion tween color and width in place. */
function BarButton({ variant, icon: Icon, label, onClick }: { variant: "primary" | "secondary"; icon: LucideIcon; label: string; onClick?: () => void }) {
  const primary = variant === "primary";
  const [hover, setHover] = useState(false);
  const transition: Transition = { layout: settle, backgroundColor: fade };
  return (
    <motion.button
      layout
      type="button"
      onClick={onClick}
      onHoverStart={() => setHover(true)}
      onHoverEnd={() => setHover(false)}
      transition={transition}
      initial={false}
      animate={{ backgroundColor: primary ? (hover ? C.accentHover : C.accent) : hover ? C.surfaceHover : C.surface }}
      whileTap={{ scale: 0.96, backgroundColor: primary ? C.accentPressed : C.surfaceHover }}
      className={cx(styles.barButton, primary && styles.barPrimary)}
    >
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span key={label} style={{ display: "flex", alignItems: "center", gap: 10 }} initial={{ opacity: 0, filter: "blur(4px)" }} animate={{ opacity: 1, filter: "blur(0px)" }} exit={{ opacity: 0, filter: "blur(4px)" }} transition={fade}>
          <Icon strokeWidth={1.75} />
          {label}
        </motion.span>
      </AnimatePresence>
    </motion.button>
  );
}

function ActionBar({ run, onPause, onResume, onStop, onApprove, onDeny, onRetry, openRunHref, onOpenRun }: Omit<AgentRunCardProps, "expanded" | "onToggle">) {
  const slots = useMemo(() => {
    switch (run.status) {
      case "running": return [{ variant: "secondary" as const, icon: Pause, label: "Pause", onClick: onPause }, { variant: "secondary" as const, icon: Square, label: "Stop", onClick: onStop }];
      case "paused": return [{ variant: "secondary" as const, icon: Play, label: "Resume", onClick: onResume }, { variant: "secondary" as const, icon: Square, label: "Stop", onClick: onStop }];
      case "waiting": return [{ variant: "primary" as const, icon: Check, label: "Approve", onClick: onApprove }, { variant: "secondary" as const, icon: X, label: "Deny", onClick: onDeny }];
      case "failed": return onRetry ? [{ variant: "secondary" as const, icon: RotateCcw, label: "Retry", onClick: onRetry }] : [];
      default: return [];
    }
  }, [run.status, onPause, onResume, onStop, onApprove, onDeny, onRetry]);
  return (
    <div className={styles.bar}>
      <AnimatePresence initial={false} mode="popLayout">
        {slots.map((slot, i) => (
          <motion.div key={`slot-${i}`} layout initial={{ opacity: 0, filter: "blur(4px)" }} animate={{ opacity: 1, filter: "blur(0px)" }} exit={{ opacity: 0, filter: "blur(4px)", transition: fade }} transition={fade}>
            <BarButton variant={slot.variant} icon={slot.icon} label={slot.label} onClick={slot.onClick} />
          </motion.div>
        ))}
      </AnimatePresence>
      {(openRunHref || onOpenRun) && (
        <a className={styles.openRun} href={openRunHref ?? "#"} onClick={(e) => { if (onOpenRun) { e.preventDefault(); onOpenRun(); } }}>
          Open run <ArrowUpRight />
        </a>
      )}
    </div>
  );
}
