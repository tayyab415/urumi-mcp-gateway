import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, ChevronDown, CircleAlert, CircleX, Pencil, Play, Plus, RefreshCw, Search, Trash2, TriangleAlert } from "lucide-react";
import { ConfirmMorph } from "@/arc/confirm-morph";
import { useResource } from "@/state/resource";
import { api, byRegistration, type AuditSummary, type Server, type ServerDraft, type ServerTool, type TestResult } from "@/lib/api";
import { fmtMs, relTime } from "@/lib/format";
import { healthWords } from "@/lib/labels";
import { useNow } from "@/lib/clock";
import { fade, settle } from "@/lib/motion";
import { cx } from "@/lib/cx";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { Field, Input } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { Tabs } from "@/components/ui/Tabs";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { StepIcon } from "@/components/ui/StepIcon";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Alert } from "@/components/ui/Alert";
import { useToast } from "@/components/ui/Toast";
import { toolWords } from "@/lib/labels";
import styles from "./Servers.module.css";

const orbFor = (s: Server): "idle" | "done" | "failed" | "checking" => (!s.enabled ? "idle" : s.health === "ok" ? "done" : s.health === "down" || s.health === "auth_failed" ? "failed" : "checking");

export function ServersPage() {
  const servers = useResource(() => api.servers.list().then(byRegistration), [], { interval: 5000 });
  const summary = useResource(() => api.audit.summary(24), [], { interval: 30000 });
  const [open, setOpen] = useState<number | null>(null);
  const [editing, setEditing] = useState<Server | null | "new">(null);
  const enabled = servers.data?.filter((s) => s.enabled).length ?? 0;

  useEffect(() => {
    if (open === null && servers.data && servers.data.length > 0) setOpen(servers.data[0]!.id);
  }, [servers.data, open]);

  return (
    <>
      <PageHeader
        title="Servers"
        subtitle={servers.data ? <>{enabled} of {servers.data.length} enabled. Tools are shared with your team as <code>&lt;server&gt;__&lt;tool&gt;</code>.</> : "Upstream MCP servers behind the gateway."}
        actions={<Button variant="primary" icon={<Plus />} onClick={() => setEditing("new")}>Add server</Button>}
      />
      {servers.error && <Alert tone="danger" actions={<Button size="sm" onClick={() => void servers.reload()}>Retry</Button>}>{servers.error}</Alert>}
      <div className={styles.list}>
        {servers.loading ? <Card padded><SkeletonRows rows={4} height={36} /></Card> : servers.data?.length === 0 ? (
          <Card><EmptyState action={<Button icon={<Plus />} onClick={() => setEditing("new")}>Add server</Button>}>No servers yet</EmptyState></Card>
        ) : servers.data?.map((s) => (
          <ServerCard key={s.id} server={s} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)} onEdit={() => setEditing(s)} onChanged={(next) => servers.setData((list) => (list ?? []).map((x) => (x.id === next.id ? next : x)))} onRemoved={() => servers.setData((list) => (list ?? []).filter((x) => x.id !== s.id))} stats={summary.data} />
        ))}
      </div>
      <ServerDrawer server={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void servers.reload(); }} existing={servers.data ?? []} />
    </>
  );
}

function ServerCard({ server: s, open, onToggle, onEdit, onChanged, onRemoved, stats }: { server: Server; open: boolean; onToggle: () => void; onEdit: () => void; onChanged: (s: Server) => void; onRemoved: () => void; stats: AuditSummary | null }) {
  const toast = useToast();
  const navigate = useNavigate();
  const now = useNow();
  const tools = useResource(() => api.servers.tools(s.id), [s.id, s.tool_count, s.last_checked_at], { enabled: open });
  const h = healthWords(s);
  const perTool = useMemo(() => new Map((stats?.slowest_tools ?? []).map((t) => [t.public_name, t.p95_ms])), [stats]);
  const serverStats = stats?.servers.find((x) => x.slug === s.slug);
  const [refreshing, setRefreshing] = useState(false);

  const setEnabled = async (enabled: boolean) => {
    onChanged(await api.servers.update(s.id, { enabled }));
    toast(enabled ? `${s.name} enabled. Its tools are back on the next tool list.` : `${s.name} disabled. Its tools are gone from chat and MCP clients now.`, "success");
  };
  const refresh = async () => {
    setRefreshing(true);
    try { onChanged(await api.servers.refresh(s.id)); } finally { setRefreshing(false); }
  };

  return (
    <Card className={cx(open && styles.open, !s.enabled && styles.dim)}>
      <button type="button" className={styles.head} onClick={onToggle} aria-expanded={open}>
        <StatusOrb state={refreshing ? "checking" : orbFor(s)} size={26} glyph={false} />
        <span className={styles.name}>{s.name}</span>
        <span className={styles.url}>{s.url}</span>
        <span className={cx(styles.status, styles[h.tone])}>{h.label}</span>
        <ChevronDown className={styles.chev} strokeWidth={1.75} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="body" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0, transition: fade }} transition={settle} style={{ overflow: "hidden" }}>
            <div className={styles.body}>
              <div className={styles.meta}>
                <span>{s.slug} · {s.auth_type === "none" ? "no credential" : `${s.auth_type === "bearer" ? "bearer token" : s.auth_header} stored encrypted`} · timeout {s.timeout_seconds}s{s.forward_identity ? " · sends caller's name" : ""}</span>
                <span className="tnum">{s.tool_count} tools{serverStats?.p50_ms != null ? ` · p50 ${fmtMs(serverStats.p50_ms)}` : s.latency_ms != null ? ` · list ${fmtMs(s.latency_ms)}` : ""}</span>
              </div>
              {s.enabled && s.health !== "ok" && s.last_error && (
                <div className={styles.errorLine}>
                  <CircleX strokeWidth={1.75} />
                  <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.last_error}</span>
                  <span className={styles.errorRight}>{s.last_ok_at ? `last healthy ${relTime(s.last_ok_at, now)}` : "never reached"}</span>
                </div>
              )}
              {tools.loading ? <div style={{ paddingTop: 8 }}><SkeletonRows rows={3} height={36} /></div> : (tools.data ?? []).filter((t) => t.present).map((t) => (
                <ToolRow key={t.name} tool={t} p95={perTool.get(t.public_name) ?? null} dim={!s.enabled || s.health !== "ok"} />
              ))}
              <div className={styles.bar}>
                {s.enabled ? (
                  <ConfirmMorph label="Disable" icon={<Pause16 />} prompt={`Disable ${s.name}?`} confirmLabel="Confirm disable" pendingLabel="Disabling" doneLabel="Disabled" tone="neutral" resultTimeout={1500} onConfirm={() => setEnabled(false)} />
                ) : (
                  <Button icon={<Play />} onClick={() => void setEnabled(true)}>Enable</Button>
                )}
                {s.enabled && s.health !== "ok" && <Button icon={<RefreshCw />} pending={refreshing} onClick={() => void refresh()}>Retry now</Button>}
                <Button icon={<Pencil />} onClick={onEdit}>Edit</Button>
                <ConfirmMorph label="Remove" icon={<Trash2 size={16} strokeWidth={1.75} />} prompt={`Remove ${s.name} and its tools?`} confirmLabel="Remove" pendingLabel="Removing" doneLabel="Removed" confirmMode="hold" holdDuration={900} resultTimeout={800}
                  onConfirm={async () => { await api.servers.remove(s.id); onRemoved(); }} />
                <a className={styles.openAudit} href="#" onClick={(e) => { e.preventDefault(); navigate(`/audit?server=${s.slug}`); }}>Open audit <ArrowUpRight /></a>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

const Pause16 = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"><path d="M9 5v14M15 5v14" /></svg>;

function ToolRow({ tool, p95, dim }: { tool: ServerTool; p95: number | null; dim: boolean }) {
  const w = toolWords(tool.public_name, null, Boolean(tool.annotations.readOnlyHint));
  return (
    <div className={styles.tool} style={{ opacity: dim ? 0.6 : 1 }}>
      <StepIcon kind={w.kind} />
      <span className={styles.toolName}>{tool.public_name}</span>
      <span className={styles.toolTitle}>{tool.title ?? tool.description}{!tool.allowed_roles.includes("member") && <span style={{ color: "var(--text-muted)", fontSize: 11, marginLeft: 8 }}>admins only</span>}</span>
      <span className={cx(styles.toolDur, "tnum")}>{p95 != null ? fmtMs(p95) : "—"}</span>
    </div>
  );
}

const EMPTY: ServerDraft = { slug: "", name: "", url: "", auth_type: "none", auth_header: null, credential: null, timeout_seconds: 10, forward_identity: false, enabled: true };

/** Add or edit a server in a side sheet. "Test connection" lists the upstream's tools without saving anything. */
function ServerDrawer({ server, onClose, onSaved, existing }: { server: Server | null | "new"; onClose: () => void; onSaved: () => void; existing: Server[] }) {
  const toast = useToast();
  const isNew = server === "new";
  const current = typeof server === "object" ? server : null;
  const [draft, setDraft] = useState<ServerDraft>(EMPTY);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (server === null) return;
    setTest(null); setError(null);
    setDraft(server === "new" ? EMPTY : { slug: server.slug, name: server.name, url: server.url, auth_type: server.auth_type, auth_header: server.auth_header, credential: null, timeout_seconds: server.timeout_seconds, forward_identity: server.forward_identity, enabled: server.enabled });
  }, [server]);

  const set = useCallback(<K extends keyof ServerDraft>(k: K, v: ServerDraft[K]) => setDraft((d) => ({ ...d, [k]: v })), []);
  const slugTaken = isNew && existing.some((s) => s.slug === draft.slug);

  async function runTest() {
    setTesting(true); setTest(null); setError(null);
    try {
      setTest(await api.servers.test({ slug: draft.slug, url: draft.url, auth_type: draft.auth_type, auth_header: draft.auth_header, credential: draft.credential, timeout_seconds: draft.timeout_seconds, server_id: current?.id ?? null }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test failed");
    } finally { setTesting(false); }
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      if (isNew) await api.servers.create(draft);
      else if (current) await api.servers.update(current.id, { name: draft.name, url: draft.url, auth_type: draft.auth_type, auth_header: draft.auth_header, credential: draft.credential || undefined, timeout_seconds: draft.timeout_seconds, forward_identity: draft.forward_identity });
      toast(isNew ? `${draft.name} added. Its tools are listed now.` : `${draft.name} updated.`, "success");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally { setSaving(false); }
  }

  return (
    <Drawer open={server !== null} onClose={onClose} title={isNew ? "Add MCP server" : `Edit ${current?.name ?? ""}`}
      footer={<>
        <Button icon={<Play />} pending={testing} disabled={!draft.url || !draft.slug} onClick={() => void runTest()}>Test connection</Button>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" pending={saving} disabled={!draft.url || !draft.slug || !draft.name || slugTaken} onClick={() => void save()}>{isNew ? "Add server" : "Save"}</Button>
      </>}>
      <Field label="Name">{(id) => <Input id={id} value={draft.name} onChange={(e) => set("name", e.target.value)} placeholder="Supplier catalog" autoFocus />}</Field>
      <Field label="Prefix" hint={draft.slug ? `Tools will appear as ${draft.slug}__<tool>` : "Lowercase letters, digits and single hyphens"} error={slugTaken ? "That prefix is taken" : undefined}>
        {(id) => <Input id={id} mono value={draft.slug} disabled={!isNew} onChange={(e) => set("slug", e.target.value.toLowerCase())} placeholder="supplier" />}
      </Field>
      <Field label="URL">{(id) => <Input id={id} mono value={draft.url} onChange={(e) => set("url", e.target.value)} placeholder="http://supplier.demo.svc:8080/mcp" />}</Field>
      <Field label="Authentication">{() => (
        <Tabs ariaLabel="Authentication" value={draft.auth_type} onChange={(v) => set("auth_type", v)} items={[{ value: "none", label: "None" }, { value: "bearer", label: "Bearer token" }, { value: "header", label: "Custom header" }]} />
      )}</Field>
      {draft.auth_type === "header" && (
        <Field label="Header name">{(id) => <Input id={id} mono value={draft.auth_header ?? ""} onChange={(e) => set("auth_header", e.target.value)} placeholder="X-Api-Key" />}</Field>
      )}
      {draft.auth_type !== "none" && (
        <Field label={draft.auth_type === "bearer" ? "Token" : "Header value"} hint={current?.has_credential && !draft.credential ? "A credential is stored. Leave blank to keep it." : "Stored encrypted on the server. Never shown again and never sent to clients."}>
          {(id) => <Input id={id} type="password" autoComplete="off" value={draft.credential ?? ""} onChange={(e) => set("credential", e.target.value || null)} />}
        </Field>
      )}
      <Field label="Timeout (seconds)" hint="How long the gateway waits for this server before it reports a timeout. Keep it under 45 s so chat sees the gateway's message first.">
        {(id) => <Input id={id} type="number" min={1} max={120} value={draft.timeout_seconds} onChange={(e) => set("timeout_seconds", Number(e.target.value))} style={{ width: 120 }} />}
      </Field>
      <Field label="Tell this server who is asking" hint={draft.forward_identity ? "Each call carries the caller's display name, never their email. Turn this on only for servers your team runs or trusts." : "Off: the server cannot tell one teammate from another. The audit log still records who asked."}>
        {() => <Switch label="Send the caller's name to this server" checked={draft.forward_identity} onChange={(on) => set("forward_identity", on)} />}
      </Field>
      {error && <Alert tone="danger">{error}</Alert>}
      <AnimatePresence initial={false}>
        {test && (
          <motion.div key="test" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={settle}>
            <TestPanel result={test} slug={draft.slug} />
          </motion.div>
        )}
      </AnimatePresence>
    </Drawer>
  );
}

function TestPanel({ result, slug }: { result: TestResult; slug: string }) {
  if (!result.ok) {
    return (
      <div className={styles.test}>
        <div className={styles.testHead}><StatusOrb state="failed" size={22} glyph={false} />{result.kind === "auth_failed" ? "Credential rejected" : result.kind === "timeout" ? "Timed out" : result.kind === "unreachable" ? "Unreachable" : "Not an MCP server"}</div>
        <div className={styles.testRow}><CircleAlert style={{ color: "var(--danger)" }} />{result.message}</div>
      </div>
    );
  }
  return (
    <div className={styles.test}>
      <div className={styles.testHead}><StatusOrb state="done" size={22} glyph={false} />Connected in {fmtMs(result.latency_ms)}</div>
      <div className={styles.testRow}><Search />Found {result.tools.length} {result.tools.length === 1 ? "tool" : "tools"} · protocol {result.protocol_version}</div>
      {result.tools.map((t) => <div key={t.name} className={styles.testRow} style={{ paddingLeft: 26 }}><code style={{ color: "var(--foreground)" }}>{t.public_name}</code>{t.read_only && <span style={{ color: "var(--text-muted)", fontSize: 11 }}>read-only</span>}</div>)}
      {result.collisions.map((c) => (
        <div key={c.name} className={styles.testRow}><TriangleAlert style={{ color: "var(--warning)" }} /><span>{slug}__{c.name} and {c.servers.map((s) => `${s}__${c.name}`).join(", ")} share a base name. Both stay available under their own prefix.</span></div>
      ))}
    </div>
  );
}
