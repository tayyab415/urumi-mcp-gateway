import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, ArrowUpRight, Check, ChevronDown, CircleCheck, Hand, Layers, RotateCcw, X } from "lucide-react";
import { TextShimmer } from "@/arc/text-shimmer";
import { fade, settle, snap } from "@/lib/motion";
import { fmtDuration, fmtElapsed, plural } from "@/lib/format";
import { doneLabel, liveParts, splitTool, titleCase, toolWords } from "@/lib/labels";
import { useNow } from "@/lib/clock";
import { useReducedFlag } from "@/lib/reduced";
import { cx } from "@/lib/cx";
import { turnElapsed, type ChatStep, type Turn } from "@/state/chat";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { StepIcon } from "@/components/ui/StepIcon";
import { Button } from "@/components/ui/Button";
import { JsonView } from "@/components/ui/JsonView";
import styles from "./TaskCard.module.css";

export interface TaskCardProps {
  turn: Turn;
  modelLabel: string;
  modelIsAnthropic?: boolean;
  compact?: boolean;
  onApprove: (stepId: string) => void;
  onDeny: (stepId: string) => void;
  onReplay?: () => void;
  onOpenRun?: () => void;
}

const stepWords = (s: ChatStep) => toolWords(s.name, s.input, s.readOnly);

/** One card per task. Steps stream in; the live one is marked by weight; a write stops on a review row; a finished run folds. */
/** "Calling" -> "Call", "Updating" -> "Update": the approval card states the change as an instruction.
 *  Spelled out per verb, because English drops or doubles letters irregularly. */
const IMPERATIVE: Record<string, string> = {
  Adding: "Add", Calling: "Call", Cancelling: "Cancel", Changing: "Change", Creating: "Create", Deleting: "Delete",
  Fetching: "Fetch", Finding: "Find", Listing: "List", Quoting: "Quote", Reading: "Read", Refunding: "Refund",
  Removing: "Remove", Restocking: "Restock", Running: "Run", Searching: "Search", Sending: "Send", Setting: "Set",
  Tracking: "Track", Updating: "Update",
};
const imperative = (verb: string) => IMPERATIVE[verb] ?? verb.replace(/ing$/, "");

export function TaskCard({ turn, modelLabel, modelIsAnthropic, compact, onApprove, onDeny, onReplay, onOpenRun }: TaskCardProps) {
  const reduced = useReducedFlag();
  const [folded, setFolded] = useState(false);
  const [openStep, setOpenStep] = useState<string | null>(null);
  const settled = turn.status === "done" || turn.status === "failed" || turn.status === "stopped";
  const toolSteps = turn.steps.filter((s) => s.name !== "plan");
  const stepsMs = turn.steps.reduce((a, s) => a + (s.durationMs ?? 0), 0);
  const writes = useMemo(() => toolSteps.filter((s) => s.approval?.decision === "approve" && s.status === "done"), [toolSteps]);

  useEffect(() => {
    if (turn.status === "done" && toolSteps.length > 0) {
      const t = setTimeout(() => setFolded(true), 600);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [turn.status, toolSteps.length]);

  return (
    <motion.div layout transition={reduced ? { duration: 0.12 } : settle} className={cx(styles.card, compact && styles.compact)} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <div className={styles.header}>
        <div className={styles.titleRow}>
          <h3 className={styles.title}>{turn.title}</h3>
          {onReplay && settled && (
            <button type="button" className={styles.replay} onClick={onReplay} aria-label="Ask again" title="Ask again"><RotateCcw strokeWidth={1.75} /></button>
          )}
        </div>
        <div className={styles.modelRow}>
          <span className={styles.model}>
            {modelIsAnthropic ? <AgentGlyph size={14} /> : <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--text-muted)", display: "inline-block" }} />}
            <span>{modelLabel}</span>
          </span>
          <StatusCluster turn={turn} />
        </div>
      </div>

      <AnimatePresence initial={false}>
        {toolSteps.length > 0 && (
          <motion.div key="steps" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={fade}>
            <div className={styles.divider} />
            {folded ? (
              <button type="button" className={styles.fold} onClick={() => setFolded(false)} aria-expanded={false}>
                <Layers strokeWidth={1.75} />
                <span>{plural(turn.steps.length, "step")} · {fmtDuration(stepsMs, true)}</span>
                <ChevronDown className={styles.chev} style={{ marginLeft: "auto" }} />
              </button>
            ) : (
              <motion.ul layout className={styles.log}>
                <AnimatePresence initial={false}>
                  {turn.steps.map((s) => (
                    <motion.li key={s.id} layout="position" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={settle}>
                      <StepRow step={s} turn={turn} open={openStep === s.id} onToggle={() => setOpenStep(openStep === s.id ? null : s.id)} onApprove={onApprove} onDeny={onDeny} onOpenRun={onOpenRun} compact={compact} />
                    </motion.li>
                  ))}
                </AnimatePresence>
                {settled && turn.status === "done" && (
                  <li>
                    <button type="button" className={styles.fold} style={{ height: 40, padding: 0 }} onClick={() => setFolded(true)}>
                      <span style={{ fontSize: 13, color: "var(--text-muted)" }}>Fold steps</span>
                    </button>
                  </li>
                )}
              </motion.ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {turn.status === "done" && writes.length > 0 && (
        <div className={styles.result}>
          <div className={styles.resultTitle}><CircleCheck strokeWidth={1.75} /> {writes.length === 1 ? describeWrite(writes[0]!) : `Changed ${writes.length} things`}</div>
          {writes.map((w) => <ChangeLine key={w.id} step={w} />)}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {onOpenRun && <a className={styles.openRun} style={{ marginLeft: 0 }} href="#" onClick={(e) => { e.preventDefault(); onOpenRun(); }}>Open run <ArrowUpRight /></a>}
          </div>
        </div>
      )}
      {turn.status === "done" && writes.length === 0 && toolSteps.length > 0 && folded && onOpenRun && (
        <div className={styles.bar} style={{ paddingTop: 0 }}>
          <a className={styles.openRun} href="#" onClick={(e) => { e.preventDefault(); onOpenRun(); }}>Open run <ArrowUpRight /></a>
        </div>
      )}
      {turn.error && <div className={styles.errorRow} role="alert">{turn.error}</div>}
      {turn.status === "stopped" && !turn.error && <div className={styles.errorRow} style={{ color: "var(--text-muted)" }}>Stopped. The service finished the turn on its own; the audit log has the record.</div>}
    </motion.div>
  );
}

function StatusCluster({ turn }: { turn: Turn }) {
  const now = useNow();
  const elapsed = turnElapsed(turn, now);
  const word =
    turn.status === "running" ? "Working" : turn.status === "waiting" ? "Waiting for you" : turn.status === "done" ? `Done in ${Math.max(1, Math.round(elapsed / 1000))}s` : turn.status === "failed" ? "Failed" : "Stopped";
  return (
    <span className={styles.status}>
      <AnimatePresence initial={false} mode="popLayout">
        {turn.status === "running" ? (
          <motion.span key="spin" className={styles.statusIcon} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: fade }}><Spinner /></motion.span>
        ) : turn.status === "waiting" ? (
          <motion.span key="hand" className={styles.statusIcon} style={{ color: "var(--warning)" }} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0, transition: fade }} transition={snap}><Hand size={16} strokeWidth={1.75} /></motion.span>
        ) : turn.status === "done" ? (
          <motion.span key="check" className={styles.statusIcon} style={{ color: "var(--success)" }} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={snap}><CircleCheck size={16} strokeWidth={1.75} /></motion.span>
        ) : (
          <motion.span key="x" className={styles.statusIcon} style={{ color: turn.status === "failed" ? "var(--danger)" : "var(--text-muted)" }} initial={{ opacity: 0 }} animate={{ opacity: 1 }}><X size={16} strokeWidth={1.75} /></motion.span>
        )}
      </AnimatePresence>
      <TextShimmer active={turn.status === "running"} duration={1.2}>{word}</TextShimmer>
      {turn.status !== "done" && <span className={cx(styles.timer, "tnum")}>{fmtElapsed(elapsed)}</span>}
    </span>
  );
}

function Spinner() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden style={{ animation: "spin 980ms linear infinite" }}>
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="var(--border-strong)" strokeWidth="1.5" />
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="var(--foreground)" strokeWidth="1.5" strokeLinecap="round" strokeDasharray="10 30" />
      <style>{`@keyframes spin { to { transform: rotate(360deg); } } @media (prefers-reduced-motion: reduce) { svg { animation: none !important; } }`}</style>
    </svg>
  );
}

function StepRow({ step, turn, open, onToggle, onApprove, onDeny, onOpenRun, compact }: { step: ChatStep; turn: Turn; open: boolean; onToggle: () => void; onApprove: (id: string) => void; onDeny: (id: string) => void; onOpenRun?: () => void; compact?: boolean }) {
  const isPlan = step.name === "plan";
  const words = isPlan ? null : stepWords(step);
  const live = step.status === "active";
  const review = step.status === "waiting";
  const expandable = !isPlan && !live && !review;

  let content: React.ReactNode;
  if (isPlan) {
    content = live ? <TextShimmer duration={1.2}>Planning the change</TextShimmer> : <span className={styles.label}>Planned the {step.output === "change" ? "change" : "lookup"}</span>;
  } else if (review) {
    content = <span className={styles.label}>Review {words!.kind === "create" ? `new ${words!.noun}` : `change to ${words!.argument || words!.noun}`}</span>;
  } else if (live) {
    const parts = liveParts(words!);
    content = parts.argument
      ? <span className={styles.label}><span className={styles.verb}>{parts.verb} </span><span className={styles.arg}>{parts.argument}</span></span>
      : <TextShimmer duration={1.2}>{parts.verb}</TextShimmer>;
  } else if (step.status === "denied") {
    content = <span className={styles.label}>Denied {words!.argument ? `the change to ${words!.argument}` : `${words!.noun}`}</span>;
  } else if (step.status === "failed") {
    content = <span className={styles.label} title={step.output}>{firstSentence(step.output) || `${words!.verb} ${words!.noun} failed`}</span>;
  } else if (step.status === "unknown") {
    content = <span className={styles.label}>Timed out. Check {words!.argument || `the ${words!.noun}`} before retrying</span>;
  } else {
    content = <span className={styles.label}>{doneLabel(words!, step.output, step.isError)}</span>;
  }

  return (
    <div className={cx(step.status === "failed" && styles.failed, step.status === "unknown" && styles.unknown, step.status === "denied" && styles.denied, review && styles.review)}>
      <div className={styles.row}>
        <button type="button" className={styles.rowBtn} onClick={expandable ? onToggle : undefined} aria-expanded={expandable ? open : undefined} style={{ cursor: expandable ? "pointer" : "default" }}>
          {review ? <Hand className={styles.rowIcon} strokeWidth={1.75} /> : <StepIcon kind={isPlan ? "plan" : words!.kind} className={styles.rowIcon} />}
          {content}
          <span className={styles.right}>
            {!live && !review && step.durationMs !== undefined && <span className={cx(styles.duration, "tnum")}>{fmtDuration(step.durationMs, true)}</span>}
            {step.status === "done" && <DrawnCheck />}
            {step.status === "denied" && <X className={styles.check} style={{ color: "var(--text-muted)" }} strokeWidth={1.75} />}
            {expandable && <ChevronDown className={cx(styles.chev, open && styles.chevOpen)} strokeWidth={1.75} />}
          </span>
        </button>
      </div>
      <AnimatePresence initial={false}>
        {expandable && open && (
          <motion.div key="detail" className={styles.detail} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={settle} style={{ overflow: "hidden" }}>
            <div className={styles.detailMeta}>
              <code>{step.name}</code>
              <span>{titleCase(splitTool(step.name).server)}</span>
              {step.approval?.decision && <span>· {step.approval.decision === "approve" ? "approved by you" : step.approval.decision === "deny" ? "denied by you" : "no decision in time"}</span>}
              {onOpenRun && <a href="#" onClick={(e) => { e.preventDefault(); onOpenRun(); }} style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>Audit entry ↗</a>}
            </div>
            <JsonView value={step.input} maxHeight={160} />
            {step.output !== undefined && <JsonView value={step.output} maxHeight={200} />}
          </motion.div>
        )}
      </AnimatePresence>
      {review && <ReviewBlock step={step} turn={turn} onApprove={onApprove} onDeny={onDeny} onOpenRun={onOpenRun} compact={compact} />}
    </div>
  );
}

function firstSentence(text?: string): string {
  if (!text) return "";
  const t = text.replace(/\s+/g, " ").trim();
  const m = t.match(/^(.{10,140}?[.!?])(\s|$)/);
  return m ? m[1]! : t.slice(0, 140);
}

/** The green check draws itself when a step lands. */
function DrawnCheck() {
  const reduced = useReducedFlag();
  return (
    <svg className={styles.check} viewBox="0 0 16 16" fill="none" aria-hidden>
      <motion.path d="M3.5 8.5l3 3 6-6.5" stroke="var(--success)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" initial={reduced ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }} />
    </svg>
  );
}

/** The recording's diff, applied to a tool call: one tab for the object, the arguments as added lines. */
function ReviewBlock({ step, turn, onApprove, onDeny, onOpenRun, compact }: { step: ChatStep; turn: Turn; onApprove: (id: string) => void; onDeny: (id: string) => void; onOpenRun?: () => void; compact?: boolean }) {
  const words = stepWords(step);
  const lines = Object.entries(step.input).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  const [shown, setShown] = useState(0);
  const reduced = useReducedFlag();
  useEffect(() => {
    if (reduced) { setShown(lines.length); return; }
    setShown(0);
    let i = 0;
    const t = setInterval(() => { i += 1; setShown(i); if (i >= lines.length) clearInterval(t); }, 70);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step.id, reduced]);
  const consequence = words.kind === "create"
    ? `Create a new ${words.noun}${words.argument ? ` called ${words.argument}` : ""} on ${step.approval?.server ?? titleCase(words.server)}.`
    : `${imperative(words.verb)} ${words.argument || words.noun} on ${step.approval?.server ?? titleCase(words.server)}. This cannot be undone from here.`;
  const deciding = Boolean(step.approval?.deciding);
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={fade}>
      <div className={styles.summary}>{consequence}</div>
      <div className={styles.reviewBlock}>
        <div className={styles.tabs}>
          <span className={styles.tab}>
            {words.argument ? titleCase(words.argument) : titleCase(words.noun)}
            <span className={styles.plus}>+{lines.length}</span>
          </span>
        </div>
        <div style={{ padding: "6px 0" }}>
          {lines.slice(0, shown).map((l, i) => (
            <div key={i} className={cx(styles.line, styles.added)}>
              <span className={styles.lineNo}>{i + 1}</span>
              <span className={styles.sign}>+</span>
              <span>{l}</span>
            </div>
          ))}
          {lines.length === 0 && <div className={styles.line}><span className={styles.lineNo}>1</span><span className={styles.sign}>+</span><span>(no arguments)</span></div>}
        </div>
      </div>
      <div className={cx(styles.bar)} style={{ paddingTop: 0, paddingBottom: compact ? 10 : 6 }}>
        <Button variant="primary" icon={<Check />} pending={deciding} onClick={() => onApprove(step.id)}>Approve</Button>
        <Button icon={<X />} disabled={deciding} onClick={() => onDeny(step.id)}>Deny</Button>
        {onOpenRun && turn.id && <a className={styles.openRun} href="#" onClick={(e) => { e.preventDefault(); onOpenRun(); }}>Open run <ArrowUpRight /></a>}
      </div>
    </motion.div>
  );
}

function describeWrite(step: ChatStep): string {
  const w = stepWords(step);
  return `${w.past} ${w.argument || `1 ${w.noun}`}`;
}

function ChangeLine({ step }: { step: ChatStep }) {
  const w = stepWords(step);
  const after = typeof step.input.status === "string" ? titleCase(step.input.status) : typeof step.input.name === "string" ? (step.input.name as string) : null;
  return (
    <div className={styles.change}>
      <span style={{ color: "var(--foreground)" }}>{titleCase(w.argument || w.noun)}</span>
      {after && w.kind === "edit" && <><span className={styles.from}>previous</span><ArrowRight className={styles.arrow} /><span>{after}</span></>}
      {after && w.kind === "create" && <span className={styles.from}>created</span>}
      <code style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 12 }}>{step.name}</code>
    </div>
  );
}
