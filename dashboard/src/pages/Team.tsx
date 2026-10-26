import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { KeyRound, Plus, X } from "lucide-react";
import { ConfirmMorph } from "@/arc/confirm-morph";
import { CopyButton } from "@/arc/copy-button";
import { useAuth } from "@/state/auth";
import { useResource } from "@/state/resource";
import { api, type Role } from "@/lib/api";
import { relTime } from "@/lib/format";
import { settle } from "@/lib/motion";
import { cx } from "@/lib/cx";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, InputRow } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { Tabs } from "@/components/ui/Tabs";
import { Avatar } from "@/components/ui/Avatar";
import { StaticChip } from "@/components/ui/Chip";
import { InlineCode } from "@/components/ui/CodeBlock";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Alert } from "@/components/ui/Alert";
import { useToast } from "@/components/ui/Toast";
import styles from "./Team.module.css";

export function TeamPage() {
  const [tab, setTab] = useState<"members" | "keys">("members");
  const users = useResource(() => api.users.list(), [], { interval: 15000 });
  const keys = useResource(() => api.keys.list(true), [], { interval: 15000 });
  return (
    <>
      <PageHeader title="Team" subtitle="Who can use the gateway, and which keys they hold. Keys are stored hashed and shown once." />
      <Tabs ariaLabel="Team sections" value={tab} onChange={setTab} items={[{ value: "members", label: "Members", count: users.data?.length }, { value: "keys", label: "API keys", count: keys.data?.filter((k) => !k.revoked_at).length }]} className="" />
      <div style={{ marginTop: 16 }}>
        {tab === "members" ? <Members users={users} /> : <Keys keys={keys} />}
      </div>
    </>
  );
}

function Members({ users }: { users: ReturnType<typeof useResource<Awaited<ReturnType<typeof api.users.list>>>> }) {
  const { me } = useAuth();
  const toast = useToast();
  const invites = useResource(() => api.invites.list(), [], { interval: 15000 });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [sending, setSending] = useState(false);
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);

  async function invite() {
    if (!email.trim()) return;
    setSending(true);
    try {
      const inv = await api.invites.create(email.trim(), role);
      setLink({ email: inv.email, url: inv.invite_url });
      setEmail("");
      await invites.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not create the invitation", "danger");
    } finally { setSending(false); }
  }

  async function setUser(id: number, patch: { role?: Role; active?: boolean }) {
    try {
      const u = await api.users.update(id, patch);
      users.setData((list) => (list ?? []).map((x) => (x.id === u.id ? { ...x, ...u } : x)));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not update the member", "danger");
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card padded>
        <div className={styles.invite}>
          <Input type="email" placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void invite()} style={{ flex: 1, minWidth: 220 }} aria-label="Email to invite" />
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Role"><option value="member">Member</option><option value="admin">Admin</option></Select>
          <Button variant="primary" icon={<Plus />} pending={sending} disabled={!email.trim()} onClick={() => void invite()}>Invite member</Button>
        </div>
        <AnimatePresence initial={false}>
          {link && (
            <motion.div key={link.url} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={settle} style={{ overflow: "hidden" }}>
              <div style={{ paddingTop: 12, display: "grid", gap: 6 }}>
                <InputRow><InlineCode code={link.url} /><CopyButton value={link.url} label="Copy link" /></InputRow>
                <div className={styles.note} style={{ marginTop: 0 }}>No email is sent. Share this link with {link.email}; it works once and expires in 7 days.</div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
      <Card>
        {users.error && <Alert tone="danger" className="m">{users.error}</Alert>}
        {users.loading ? <div style={{ padding: 16 }}><SkeletonRows rows={3} height={44} /></div> : users.data?.map((u) => (
          <div key={u.id} className={cx(styles.row, !u.active && styles.inactive)}>
            <Avatar name={u.name} size={36} />
            <div style={{ minWidth: 0 }}>
              <div className={styles.name}>{u.name}{u.id === me?.id && <span className={styles.muted} style={{ marginLeft: 8 }}> you</span>}</div>
              <div className={styles.email}>{u.email}</div>
            </div>
            <div className={styles.right}>
              <span className={styles.muted}>{u.active ? `${u.active_keys ?? 0} active ${u.active_keys === 1 ? "key" : "keys"}` : "deactivated"}</span>
              <Select size="sm" value={u.role} disabled={u.id === me?.id} onChange={(e) => void setUser(u.id, { role: e.target.value as Role })} aria-label={`Role of ${u.name}`}>
                <option value="member">Member</option><option value="admin">Admin</option>
              </Select>
              {u.id !== me?.id && (u.active ? (
                <ConfirmMorph label="Deactivate" prompt={`Deactivate ${u.name}?`} confirmLabel="Deactivate" pendingLabel="Deactivating" doneLabel="Deactivated" resultTimeout={1500} onConfirm={() => setUser(u.id, { active: false })} />
              ) : (
                <Button size="sm" onClick={() => void setUser(u.id, { active: true })}>Reactivate</Button>
              ))}
            </div>
          </div>
        ))}
        {(invites.data ?? []).map((i) => (
          <div key={`inv-${i.id}`} className={styles.row}>
            <Avatar name={i.email} size={36} />
            <div style={{ minWidth: 0 }}>
              <div className={styles.name} style={{ color: "var(--text-secondary)" }}>{i.email}</div>
              <div className={styles.email}>invited, not joined · expires {relTime(i.expires_at).replace(" ago", "")}</div>
            </div>
            <div className={styles.right}>
              <StaticChip>{i.role}</StaticChip>
              <Button size="sm" variant="ghost" icon={<X />} aria-label="Revoke invitation" onClick={() => void api.invites.revoke(i.id).then(() => invites.reload())} />
            </div>
          </div>
        ))}
      </Card>
      <div className={styles.note}>Deactivating a member ends their browser sessions now and makes every key they hold stop working on its next call.</div>
    </div>
  );
}

function Keys({ keys }: { keys: ReturnType<typeof useResource<Awaited<ReturnType<typeof api.keys.list>>>> }) {
  return (
    <div>
      <Card>
        {keys.loading ? <div style={{ padding: 16 }}><SkeletonRows rows={3} height={44} /></div> : keys.data?.length === 0 ? <EmptyState>No keys yet. Members create their own on the Connect page.</EmptyState> : keys.data?.map((k) => (
          <div key={k.id} className={cx(styles.row, k.revoked_at && styles.revoked)}>
            <KeyRound size={18} strokeWidth={1.75} style={{ color: "var(--text-muted)", flex: "none" }} />
            <div style={{ minWidth: 0 }}>
              <div className={styles.name}><code>{k.prefix}…</code> <span style={{ marginLeft: 8 }}>{k.name}</span></div>
              <div className={styles.email}>{k.user_email} · created {relTime(k.created_at)} · {k.revoked_at ? `revoked ${relTime(k.revoked_at)}` : k.last_used_at ? `used ${relTime(k.last_used_at)}` : "never used"}</div>
            </div>
            <div className={styles.right}>
              {!k.revoked_at && (
                <ConfirmMorph label="Revoke" prompt="Revoke this key?" confirmLabel="Confirm revoke" pendingLabel="Revoking" doneLabel="Revoked" resultTimeout={2000} onConfirm={async () => { await api.keys.revoke(k.id); await keys.reload(); }} />
              )}
            </div>
          </div>
        ))}
      </Card>
      <div className={styles.note}>Keys are stored hashed. A key is shown once, when it is created. A revoked key is refused on its next call, including over a connection that is already open.</div>
    </div>
  );
}
