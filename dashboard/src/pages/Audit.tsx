import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { useAuth } from "@/state/auth";
import { CHANGE_EVENT } from "@/state/chat";
import { api, type AuditEvent, type Server, type User } from "@/lib/api";
import { fmtDateTime, fmtMs, relTime } from "@/lib/format";
import { auditLabel, outcomeTone, splitTool, toolWords } from "@/lib/labels";
import { useNow } from "@/lib/clock";
import { settle } from "@/lib/motion";
import { cx } from "@/lib/cx";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Chip } from "@/components/ui/Chip";
import { Button } from "@/components/ui/Button";
import { Table, tableStyles } from "@/components/ui/Table";
import { ToneLabel } from "@/components/ui/ToneLabel";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { StepIcon } from "@/components/ui/StepIcon";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Alert } from "@/components/ui/Alert";
import { JsonView } from "@/components/ui/JsonView";
import styles from "./Audit.module.css";

const OUTCOMES = [
  { value: "", label: "All outcomes" }, { value: "ok", label: "Success" }, { value: "denied", label: "Refused (role)" }, { value: "disabled", label: "Refused (disabled)" },
  { value: "unknown_tool", label: "Refused (unknown tool)" }, { value: "unreachable", label: "Unreachable" }, { value: "timeout", label: "Timed out" }, { value: "auth_failed", label: "Credential rejected" }, { value: "tool_error", label: "Tool error" },
  { value: "interrupted", label: "Interrupted (outcome unknown)" },
];

/** The step log at page scale: a table people sort and filter, with new records streaming in at the top. */
export function AuditPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const chatId = params.get("chat_id");
  const [userId, setUserId] = useState<string>("");
  const [server, setServer] = useState<string>("");
  const [outcome, setOutcome] = useState<string>("");
  const [via, setVia] = useState<string>("");
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const newest = useRef<number | null>(null);
  const now = useNow();

  const filters = useMemo(() => ({ user_id: userId ? Number(userId) : null, server: server || null, outcome: outcome || null, via: via || null, chat_id: chatId, limit: 50 }), [userId, server, outcome, via, chatId]);

  useEffect(() => {
    if (me?.role === "admin") {
      api.users.list().then(setUsers).catch(() => {});
    }
    api.servers.list().then(setServers).catch(() => {});
  }, [me?.role]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.audit.list(filters);
      setRows(data);
      newest.current = data[0]?.id ?? null;
      setExhausted(data.length < 50);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the audit log");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);

  // Live: ask only for rows newer than the newest one we have, and mark them so they enter with a tint.
  // A call still running has its row already (outcome "pending"), so polling starts just before the
  // oldest running one shown, and its finished row replaces it in place.
  const shown = useRef<AuditEvent[]>([]);
  shown.current = rows;
  const poll = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const running = shown.current.filter((r) => r.outcome === "pending").map((r) => r.id);
      const since = running.length ? Math.min(...running) - 1 : newest.current ?? undefined;
      const data = await api.audit.list({ ...filters, after_id: since, limit: 50 });
      if (data.length === 0) return;
      newest.current = Math.max(newest.current ?? 0, data[0]!.id);
      const added = data.filter((d) => !shown.current.some((r) => r.id === d.id)).map((d) => d.id);
      setFresh((s) => new Set([...s, ...added]));
      setRows((prev) => [...data, ...prev.filter((p) => !data.some((d) => d.id === p.id))].sort((a, b) => b.id - a.id));
      setTimeout(() => setFresh((s) => { const n = new Set(s); added.forEach((id) => n.delete(id)); return n; }), 1500);
    } catch { /* keep the last good list */ }
  }, [filters]);

  useEffect(() => {
    const t = setInterval(() => void poll(), 3000);
    const onChange = () => void poll();
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => { clearInterval(t); window.removeEventListener(CHANGE_EVENT, onChange); };
  }, [poll]);

  async function loadOlder() {
    const last = rows[rows.length - 1];
    if (!last) return;
    const older = await api.audit.list({ ...filters, before_id: last.id });
    setRows((r) => [...r, ...older]);
    if (older.length < 50) setExhausted(true);
  }

  return (
    <>
      <PageHeader title="Audit log" subtitle={me?.role === "admin" ? "Every tool call, who made it, through which door, and how it ended." : "Your tool calls, through chat and your API keys."} />
      <div className={styles.filters}>
        {me?.role === "admin" && (
          <Select value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="User">
            <option value="">All users</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.email})</option>)}
          </Select>
        )}
        <Select value={server} onChange={(e) => setServer(e.target.value)} aria-label="Server">
          <option value="">All servers</option>
          {servers.map((s) => <option key={s.id} value={s.slug}>{s.name}</option>)}
        </Select>
        <Select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome">
          {OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
        <Select value={via} onChange={(e) => setVia(e.target.value)} aria-label="Via">
          <option value="">Chat and MCP</option>
          <option value="chat">Chat only</option>
          <option value="mcp">MCP clients only</option>
        </Select>
        {chatId && <Chip selected icon={<X />} onClick={() => { params.delete("chat_id"); setParams(params); }}>Run {chatId.slice(0, 8)}</Chip>}
        <span className={styles.live}><StatusOrb state="running" size={18} glyph={false} /> Live</span>
      </div>
      {error && <Alert tone="danger" actions={<Button size="sm" onClick={() => void load()}>Retry</Button>}>{error}</Alert>}
      <Card>
        <div className={styles.tableWrap}>
          <Table>
            <thead>
              <tr>
                <th style={{ width: "44%" }}>Event</th>
                <th>User</th>
                <th>Via</th>
                <th>When</th>
                <th className={tableStyles.right}>Took</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr><td colSpan={6}><SkeletonRows rows={6} height={44} /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6}><EmptyState>No calls match these filters yet</EmptyState></td></tr>
              ) : (
                <AnimatePresence initial={false}>
                  {rows.map((r) => {
                    const o = outcomeTone(r.outcome, r.error);
                    const words = toolWords(r.public_name, safeJson(r.arguments));
                    const isOpen = open === r.id;
                    return [
                      <motion.tr key={r.id} layout="position" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={settle} className={cx(styles.rowBtn, fresh.has(r.id) && "tint-sweep", o.tone === "danger" && styles.danger, o.tone === "warning" && styles.warning)} onClick={() => setOpen(isOpen ? null : r.id)} aria-expanded={isOpen}>
                        <td>
                          <div className={styles.event}>
                            <StepIcon kind={words.kind} />
                            <div className={styles.eventText}>
                              <div className={styles.eventTitle}>{auditLabel(r.public_name, r.arguments, r.outcome, r.error)}</div>
                              <div className={styles.eventTool}>{r.public_name}</div>
                            </div>
                          </div>
                        </td>
                        <td className={styles.userCell} title={r.user_email}>{displayUser(r.user_email, users)}</td>
                        <td className={tableStyles.muted}>{r.via}</td>
                        <td className={cx(tableStyles.muted, "tnum", styles.nowrap)} title={r.started_at ? fmtDateTime(r.started_at) : ""}>{relTime(r.started_at, now)}</td>
                        <td className={cx(tableStyles.right, tableStyles.muted, "tnum", styles.nowrap)}>{fmtMs(r.duration_ms)}</td>
                        <td><ToneLabel tone={o.tone}>{o.label}</ToneLabel></td>
                      </motion.tr>,
                      isOpen ? (
                        <tr key={`${r.id}-d`}>
                          <td colSpan={6} style={{ padding: 0 }}>
                            <div className={styles.detail}>
                              <div className={styles.detailMeta}>
                                <span>{r.started_at ? fmtDateTime(r.started_at) : ""}</span>
                                <span>server {splitTool(r.public_name).server || "?"}</span>
                                {r.api_key_prefix && (r.api_key_prefix.startsWith("oauth:")
                                  ? <span>app {r.api_key_prefix.slice(6)} (browser sign-in)</span>
                                  : <span>key {r.api_key_prefix}…</span>)}
                                {r.chat_id && <a href="#" onClick={(e) => { e.preventDefault(); navigate(`/audit?chat_id=${encodeURIComponent(r.chat_id!)}`); }} style={{ color: "var(--text-secondary)" }}>chat run {r.chat_id.slice(0, 8)} ↗</a>}
                              </div>
                              {r.error && <div className={styles.errorText}>{r.error}</div>}
                              <JsonView value={r.arguments} maxHeight={160} />
                            </div>
                          </td>
                        </tr>
                      ) : null,
                    ];
                  })}
                </AnimatePresence>
              )}
            </tbody>
          </Table>
        </div>
        {!exhausted && rows.length > 0 && (
          <div style={{ padding: 12 }}><Button variant="link" onClick={() => void loadOlder()}>Load older ↗</Button></div>
        )}
      </Card>
    </>
  );
}

const safeJson = (s: string): Record<string, unknown> | null => { try { return JSON.parse(s || "{}"); } catch { return null; } };
const displayUser = (email: string, users: User[]) => users.find((u) => u.email === email)?.name ?? email;
