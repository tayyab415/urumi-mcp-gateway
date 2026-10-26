import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { useResource } from "@/state/resource";
import { api, byRegistration } from "@/lib/api";
import { fmtInt, fmtMs, fmtPct, relTime } from "@/lib/format";
import { healthWords } from "@/lib/labels";
import { useNow } from "@/lib/clock";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import styles from "./Overview.module.css";

/** Per-server calls, error rate and latency from the audit log, plus why an upstream is down. */
export function OverviewPage() {
  const servers = useResource(() => api.servers.list().then(byRegistration), [], { interval: 5000 });
  const summary = useResource(() => api.audit.summary(24), [], { interval: 15000 });
  const navigate = useNavigate();
  const now = useNow();
  const [retrying, setRetrying] = useState<number | null>(null);
  const down = (servers.data ?? []).filter((s) => s.enabled && s.health !== "ok" && s.last_checked_at);
  const totalCalls = summary.data?.servers.reduce((a, s) => a + s.calls, 0) ?? 0;

  return (
    <>
      <PageHeader title="Overview" subtitle={summary.data ? `Last 24 hours · ${fmtInt(totalCalls)} tool calls` : "Last 24 hours"} />
      {down.map((s) => (
        <Alert key={s.id} tone="danger" className="" title={`${s.name} is ${healthWords(s).label.toLowerCase()}`}
          actions={<>
            <Button size="sm" icon={<RefreshCw />} pending={retrying === s.id} onClick={async () => { setRetrying(s.id); try { await api.servers.refresh(s.id); await servers.reload(); } finally { setRetrying(null); } }}>Retry now</Button>
            <Button size="sm" variant="link" trailing={<ArrowUpRight />} onClick={() => navigate("/servers")}>Open server</Button>
          </>}>
          {s.last_error} {s.last_ok_at ? `Last healthy ${relTime(s.last_ok_at, now)}.` : ""} Calls to it fail fast and the other servers keep working.
        </Alert>
      ))}
      <div className={styles.grid3} style={{ marginTop: down.length ? 12 : 0 }}>
        {servers.loading ? [0, 1, 2].map((i) => <Card key={i} className={styles.metric}><Skeleton width="40%" /><Skeleton width="60%" /><Skeleton width="50%" /></Card>) : servers.data?.length === 0 ? (
          <Card style={{ gridColumn: "1 / -1" }}><EmptyState action={<Button onClick={() => navigate("/servers")}>Add server</Button>}>No servers yet</EmptyState></Card>
        ) : servers.data?.map((s) => {
          const st = summary.data?.servers.find((x) => x.slug === s.slug);
          const h = healthWords(s);
          return (
            <Card key={s.id} className={styles.metric}>
              <div className={styles.metricHead}>
                <StatusOrb state={!s.enabled ? "idle" : s.health === "ok" ? "done" : s.health === "down" || s.health === "auth_failed" ? "failed" : "checking"} size={22} glyph={false} />
                {s.slug}
                <small>{h.label}</small>
              </div>
              <div className={styles.big}>{st ? `${fmtInt(st.calls)} calls` : "0 calls"}</div>
              <div className={styles.metricLine}>{st ? `${fmtPct(st.errors, st.calls)} errors` : "no calls yet"}</div>
              <div className={styles.metricLine}>{st?.p50_ms != null ? `p50 ${fmtMs(st.p50_ms)} · p95 ${fmtMs(st.p95_ms)}` : s.latency_ms != null ? `tool list ${fmtMs(s.latency_ms)}` : "—"}</div>
            </Card>
          );
        })}
      </div>
      <div className={styles.grid2}>
        <Card padded>
          <div className={styles.h}>Tool calls per hour</div>
          {summary.data ? <HourChart data={summary.data.per_hour} /> : <Skeleton height={180} />}
        </Card>
        <Card padded>
          <div className={styles.h}>Slowest tools (p95)</div>
          {summary.data ? summary.data.slowest_tools.length === 0 ? <EmptyState>No calls in the last 24 hours</EmptyState> : summary.data.slowest_tools.map((t) => (
            <div key={t.public_name} className={styles.slow}><code>{t.public_name}</code><span>{fmtMs(t.p95_ms)}</span></div>
          )) : <Skeleton height={120} />}
        </Card>
      </div>
    </>
  );
}

function useWidth<T extends HTMLElement>(fallback: number): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.getBoundingClientRect().width || fallback));
    ro.observe(el);
    setW(el.getBoundingClientRect().width || fallback);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w];
}

function HourChart({ data }: { data: { hour: string; calls: number; errors: number }[] }) {
  const [box, W] = useWidth<HTMLDivElement>(560);
  const H = 180, padL = 32, padB = 22, padT = 8;
  const max = Math.max(1, ...data.map((d) => d.calls));
  const step = (W - padL) / Math.max(1, data.length);
  const ticks = useMemo(() => [0, Math.ceil(max / 2), max], [max]);
  const y = (v: number) => padT + (H - padB - padT) * (1 - v / max);
  const labelIdx = new Set([0, Math.floor(data.length / 4), Math.floor(data.length / 2), Math.floor((3 * data.length) / 4), data.length - 1]);
  return (
    <div ref={box}>
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className={styles.chart} role="img" aria-label="Tool calls per hour">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={W} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeDasharray="2 4" />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className={styles.axis}>{t}</text>
        </g>
      ))}
      {data.map((d, i) => {
        const x = padL + i * step + step * 0.2;
        const w = Math.max(2, step * 0.6);
        const last = i === data.length - 1;
        return (
          <g key={d.hour}>
            <rect x={x} y={y(d.calls)} width={w} height={Math.max(0, H - padB - y(d.calls))} rx={2} fill={last ? "var(--accent)" : "color-mix(in oklab, var(--accent) 65%, var(--surface))"} />
            {d.errors > 0 && <rect x={x} y={y(d.errors)} width={w} height={Math.max(0, H - padB - y(d.errors))} rx={2} fill="var(--danger)" opacity={0.8} />}
            {labelIdx.has(i) && <text x={x + w / 2} y={H - 6} textAnchor="middle" className={styles.axis}>{last ? "now" : new Date(d.hour).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</text>}
          </g>
        );
      })}
    </svg>
    </div>
  );
}
