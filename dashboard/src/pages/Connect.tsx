import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { KeyRound, Plus } from "lucide-react";
import { ConfirmMorph } from "@/arc/confirm-morph";
import { CopyButton } from "@/arc/copy-button";
import { useAuth } from "@/state/auth";
import { useResource } from "@/state/resource";
import { api, type NewKey } from "@/lib/api";
import { relTime } from "@/lib/format";
import { healthWords } from "@/lib/labels";
import { settle } from "@/lib/motion";
import { cx } from "@/lib/cx";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, InputRow } from "@/components/ui/Field";
import { CodeBlock, InlineCode } from "@/components/ui/CodeBlock";
import { Tabs } from "@/components/ui/Tabs";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Alert } from "@/components/ui/Alert";
import { useToast } from "@/components/ui/Toast";
import styles from "./Connect.module.css";

type Client = "claude" | "cursor" | "inspector";

export function ConnectPage() {
  const { me } = useAuth();
  const toast = useToast();
  const keys = useResource(() => api.keys.list(), [], { interval: 15000 });
  const tools = useResource(() => api.tools.mine(), [], { interval: 15000 });
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<NewKey | null>(null);
  const [client, setClient] = useState<Client>("claude");
  const url = me?.mcp_url ?? "";
  const keyValue = fresh?.key ?? "<your key>";

  const snippet = useMemo(() => {
    switch (client) {
      case "claude": return `claude mcp add --transport http urumi ${url} \\\n  --header "Authorization: Bearer ${keyValue}"`;
      case "cursor": return JSON.stringify({ mcpServers: { urumi: { url, headers: { Authorization: `Bearer ${keyValue}` } } } }, null, 2);
      default: return `npx @modelcontextprotocol/inspector --cli ${url} \\\n  --transport http --header "Authorization: Bearer ${keyValue}" --method tools/list`;
    }
  }, [client, url, keyValue]);

  async function create() {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const k = await api.keys.create(name.trim());
      setFresh(k);
      setName("");
      await keys.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not create the key", "danger");
    } finally {
      setCreating(false);
    }
  }

  const grouped = useMemo(() => {
    const m = new Map<string, { name: string; status: string; tools: { public_name: string; read_only: boolean }[] }>();
    for (const t of tools.data ?? []) {
      const g = m.get(t.server_slug) ?? { name: t.server, status: t.server_status, tools: [] };
      g.tools.push({ public_name: t.public_name, read_only: t.read_only });
      m.set(t.server_slug, g);
    }
    return [...m.entries()];
  }, [tools.data]);

  return (
    <>
      <PageHeader title="Connect" subtitle="Use one URL and your own key. You never see the servers' credentials." />
      <div className={styles.grid}>
        <div className={styles.col}>
          <Card padded>
            <div className={styles.h}>Gateway URL</div>
            <InputRow>
              <InlineCode code={url || "…"} />
              <CopyButton value={url} label="Copy" />
            </InputRow>
          </Card>

          <Card padded>
            <div className={styles.h}>Your API keys</div>
            <AnimatePresence initial={false}>
              {fresh && (
                <motion.div key={fresh.id} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={settle} style={{ overflow: "hidden" }}>
                  <div style={{ display: "grid", gap: 8, paddingBottom: 14 }}>
                    <InputRow>
                      <InlineCode code={fresh.key} />
                      <CopyButton value={fresh.key} label="Copy" />
                    </InputRow>
                    <div style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)" }}>Shown once. Save it now. The gateway keeps only a hash.</div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            <InputRow>
              <Input placeholder="Key name, e.g. laptop" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void create()} aria-label="Key name" />
              <Button icon={<Plus />} pending={creating} disabled={!name.trim()} onClick={() => void create()}>New key</Button>
            </InputRow>
            <div style={{ marginTop: 12 }}>
              {keys.loading ? <SkeletonRows rows={2} /> : keys.data?.length === 0 ? <EmptyState inline>No keys yet</EmptyState> : keys.data?.map((k) => (
                <div key={k.id} className={cx(styles.keyRow, k.revoked_at && styles.revoked)}>
                  <KeyRound size={16} strokeWidth={1.75} style={{ color: "var(--text-muted)", flex: "none" }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className={styles.keyName}><code>{k.prefix}…</code> <span style={{ fontFamily: "var(--font-body)", marginLeft: 6 }}>{k.name}</span></div>
                    <div className={styles.keyMeta}>{k.revoked_at ? `Revoked ${relTime(k.revoked_at)}` : `created ${relTime(k.created_at)} · ${k.last_used_at ? `used ${relTime(k.last_used_at)}` : "never used"}`}</div>
                  </div>
                  {!k.revoked_at && (
                    <ConfirmMorph label="Revoke" prompt="Revoke this key?" confirmLabel="Confirm revoke" pendingLabel="Revoking" doneLabel="Revoked" resultTimeout={2500}
                      onConfirm={async () => { await api.keys.revoke(k.id); await keys.reload(); }} />
                  )}
                </div>
              ))}
            </div>
          </Card>

          <Card padded>
            <div className={styles.h}>Add it to your client</div>
            <Tabs ariaLabel="Client" value={client} onChange={setClient} items={[{ value: "claude", label: "Claude Code" }, { value: "cursor", label: "Cursor" }, { value: "inspector", label: "MCP Inspector" }]} />
            <div style={{ marginTop: 12 }}><CodeBlock code={snippet} /></div>
            {!fresh && <div style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", marginTop: 8 }}>Create a key above and the snippet fills in the real value.</div>}
          </Card>
        </div>

        <Card padded>
          <div className={styles.h}>Tools you can use</div>
          <div style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", marginBottom: 12 }}>
            {tools.data ? `${tools.data.length} tools from ${grouped.length} servers` : <Skeleton width={120} />}
          </div>
          {tools.error && <Alert tone="danger">{tools.error}</Alert>}
          {tools.loading ? <SkeletonRows rows={6} /> : grouped.length === 0 ? <EmptyState>No tools are enabled for your role right now</EmptyState> : grouped.map(([slug, g]) => {
            const h = healthWords({ enabled: true, health: g.status, status: g.status });
            return (
              <div key={slug} className={styles.serverGroup}>
                <div className={styles.serverHead}>
                  <StatusOrb state={h.tone === "success" ? "done" : h.tone === "danger" ? "failed" : "checking"} glyph={false} />
                  <span className={styles.serverName}>{slug}</span>
                  <span className={styles.serverCount}>{g.tools.length} tools</span>
                </div>
                {g.tools.map((t) => <div key={t.public_name} className={styles.tool}><span title={t.public_name}>{t.public_name}</span>{t.read_only && <span className={styles.ro}>read-only</span>}</div>)}
              </div>
            );
          })}
          <div style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", marginTop: 12 }}>Restricted tools are hidden from you, and the gateway rejects direct calls to them.</div>
        </Card>
      </div>
    </>
  );
}
