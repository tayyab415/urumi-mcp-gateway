import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, byRegistration, type Role, type Server, type ServerTool } from "@/lib/api";
import { settle } from "@/lib/motion";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Alert } from "@/components/ui/Alert";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import styles from "./Permissions.module.css";

type Key = `${number}/${string}`;

/** Per-role tool switches. A role that is switched off neither sees the tool nor can call it. */
export function PermissionsPage() {
  const toast = useToast();
  const [servers, setServers] = useState<Server[]>([]);
  const [tools, setTools] = useState<Record<number, ServerTool[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<Key, Role[]>>({});
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const ss = byRegistration(await api.servers.list());
      setServers(ss);
      const entries = await Promise.all(ss.map(async (s) => [s.id, await api.servers.tools(s.id)] as const));
      setTools(Object.fromEntries(entries));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load tools");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, []);

  const rolesOf = (s: Server, t: ServerTool): Role[] => draft[`${s.id}/${t.name}`] ?? t.allowed_roles;
  const changes = useMemo(() => Object.keys(draft).length, [draft]);

  const toggle = (s: Server, t: ServerTool, role: Role, on: boolean) => {
    const next = on ? [...new Set([...rolesOf(s, t), role])] : rolesOf(s, t).filter((r) => r !== role);
    const key: Key = `${s.id}/${t.name}`;
    setDraft((d) => {
      const same = next.length === t.allowed_roles.length && next.every((r) => t.allowed_roles.includes(r));
      const copy = { ...d };
      if (same) delete copy[key]; else copy[key] = next;
      return copy;
    });
  };

  async function save() {
    setSaving(true);
    try {
      for (const [key, roles] of Object.entries(draft)) {
        const [id, name] = key.split("/") as [string, string];
        await api.servers.setToolRoles(Number(id), name, roles);
      }
      toast(`Saved ${changes} ${changes === 1 ? "change" : "changes"}. Tool lists update on the next call.`, "success");
      setDraft({});
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not save", "danger");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader title="Permissions" subtitle="A tool a role cannot use is hidden from its tool list and rejected if called directly." />
      {error && <Alert tone="danger" actions={<Button size="sm" onClick={() => void load()}>Retry</Button>}>{error}</Alert>}
      <Card>
        <div className={styles.head}><span>Tool</span><span>Admin</span><span>Member</span></div>
        {loading ? <div style={{ padding: 16 }}><SkeletonRows rows={8} height={40} /></div> : servers.length === 0 ? <EmptyState>No servers yet</EmptyState> : servers.map((s) => (
          <div key={s.id} className={styles.group}>
            <div className={styles.server}>
              <StatusOrb state={!s.enabled ? "idle" : s.health === "ok" ? "done" : s.health === "down" || s.health === "auth_failed" ? "failed" : "checking"} size={18} glyph={false} />
              {s.slug}<small>{s.name}{!s.enabled && " · disabled"}</small>
            </div>
            {(tools[s.id] ?? []).filter((t) => t.present).map((t) => {
              const roles = rolesOf(s, t);
              const readOnly = Boolean(t.annotations.readOnlyHint);
              return (
                <div key={t.name} className={styles.row}>
                  <div style={{ minWidth: 0 }}>
                    <div className={styles.name}>{t.public_name}{!readOnly && <span style={{ fontFamily: "var(--font-body)", color: "var(--text-muted)", fontSize: 11, marginLeft: 8 }}>changes data</span>}</div>
                    <div className={styles.desc}>{t.title ?? t.description}</div>
                  </div>
                  <div className={styles.cell}><Switch label={`Admin may use ${t.public_name}`} checked={roles.includes("admin")} onChange={(on) => toggle(s, t, "admin", on)} /></div>
                  <div className={styles.cell}><Switch label={`Member may use ${t.public_name}`} checked={roles.includes("member")} onChange={(on) => toggle(s, t, "member", on)} /></div>
                </div>
              );
            })}
          </div>
        ))}
      </Card>
      <AnimatePresence>
        {changes > 0 && (
          <motion.div key="bar" className={styles.bar} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 12 }} transition={settle}>
            <div className={styles.barInner}>
              <span className="tnum">{changes} unsaved {changes === 1 ? "change" : "changes"}</span>
              <Button variant="link" onClick={() => setDraft({})}>Discard</Button>
              <Button variant="primary" pending={saving} onClick={() => void save()}>Save</Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
