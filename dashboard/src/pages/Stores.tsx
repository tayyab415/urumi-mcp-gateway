import { useState } from "react";
import { ArrowUpRight, Plus, RotateCcw, Trash2 } from "lucide-react";
import { ConfirmMorph } from "@/arc/confirm-morph";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { ToneLabel } from "@/components/ui/ToneLabel";
import { Field, Input } from "@/components/ui/Field";
import { Drawer } from "@/components/ui/Drawer";
import { api, type Store } from "@/lib/api";
import { relTime } from "@/lib/format";
import { useResource } from "@/state/resource";
import { useAuth } from "@/state/auth";
import { cx } from "@/lib/cx";
import styles from "./Stores.module.css";

const PER_USER = 2;

const toSlug = (name: string) =>
  name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[0-9-]+/, "").slice(0, 20).replace(/-+$/, "");

const orb = (s: Store) => (s.phase === "Ready" ? (s.reason ? "failed" : "done") : s.phase === "Failed" ? "failed" : "running");
const tone = (s: Store) => (s.phase === "Ready" ? (s.reason ? "warning" : "success") : s.phase === "Failed" ? "danger" : "checking");

/** The multi-store bonus. Each store is a real Helm release in its own namespace, made by the store
 *  provisioner. When it is Ready, the gateway registers its MCP server as `store-<name>` by itself. */
export function StoresPage() {
  const { me } = useAuth();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const stores = useResource(() => api.stores.list().then((r) => r.stores), [], { interval: 3000 });
  const list = stores.data ?? [];
  const mine = list.filter((s) => s.mine && s.phase !== "Deleting").length;
  // From the gateway's settings: the dashboard's own address may be a proxy's (infra/cloud-run),
  // so it says nothing about where the stores live. The guess is only for an older gateway.
  const firstStoreUrl = me?.store_url ?? `${window.location.protocol}//${window.location.host.replace(/^app\./, "shop.")}`;
  const firstStore = firstStoreUrl.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const storeDomain = me?.store_domain ?? window.location.host.replace(/^app\./, "");

  async function create(retrySlug?: string, retryName?: string) {
    setBusy(true);
    setProblem(null);
    try {
      await api.stores.create(retrySlug ?? slug, retryName ?? name.trim());
      setAdding(false);
      setName(""); setSlug(""); setSlugEdited(false);
      await stores.reload();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Stores"
        subtitle={<span className={styles.quota}><span>{mine} of {PER_USER} of your stores used</span><span className={styles.meter}>{Array.from({ length: PER_USER }, (_, i) => <span key={i} className={cx(styles.seg, i < mine && styles.segOn)} />)}</span></span>}
        actions={<Button variant="primary" icon={<Plus />} disabled={mine >= PER_USER} onClick={() => { setProblem(null); setAdding(true); }}>Create store</Button>}
      />
      {stores.error && <Alert tone="danger" title="Could not load stores">{stores.error}</Alert>}
      {problem && !adding && <Alert tone="danger" title="That did not work">{problem}</Alert>}
      <div className={styles.list} style={{ marginTop: 12 }}>
        <Card className={styles.store}>
          <div className={styles.head}>
            <StatusOrb state="done" size={26} glyph={false} />
            <span className={styles.name}>Urumi Sports</span>
            <span className={styles.host}>{firstStore}</span>
            <span className={styles.state}><ToneLabel tone="success" quiet>Ready</ToneLabel></span>
          </div>
          <div className={styles.meta}>
            <span>The first store: Helm release <code>urumi</code> in namespace <code>urumi-store</code>. Its tools are <code>woo__*</code>.</span>
            <span>Installed with Helm, not managed here</span>
          </div>
          <div className={styles.bar}>
            <Button icon={<ArrowUpRight />} onClick={() => window.open(`${firstStoreUrl.replace(/\/$/, "")}/`, "_blank")}>Storefront</Button>
          </div>
        </Card>
        {list.map((s) => (
          <Card key={s.slug} className={styles.store}>
            <div className={styles.head}>
              <StatusOrb state={orb(s)} size={26} glyph={false} />
              <span className={styles.name}>{s.name}</span>
              <span className={styles.host}>{s.url.replace(/^https?:\/\//, "")}</span>
              <span className={styles.state}><ToneLabel tone={tone(s)} quiet>{s.phase === "Ready" && s.reason ? "Degraded" : s.phase}</ToneLabel></span>
            </div>
            {s.reason && s.phase !== "Deleting" && <div className={cx(styles.reason, s.phase === "Failed" && styles.reasonBad)}>{s.reason}</div>}
            <div className={styles.meta}>
              <span>
                Namespace <code>{s.namespace}</code> · created {relTime(s.created_at)} by {s.mine ? "you" : s.owner_email}
                {s.ready_at && s.created_at && <> · ready in {Math.max(1, Math.round((Date.parse(s.ready_at) - Date.parse(s.created_at)) / 60000))} min</>}
              </span>
              <span>
                {!s.mine && me?.role !== "admin" ? "Its tools are visible to its owner and admins"
                  : s.registered ? <>Tools <code>{s.server_slug}__*</code> · {s.server_status ?? "registered"}</>
                  : s.phase === "Ready" ? "Registering its MCP server" : "Tools appear when it is ready"}
              </span>
            </div>
            <div className={styles.bar}>
              {s.phase === "Ready" && <Button icon={<ArrowUpRight />} onClick={() => window.open(s.url, "_blank")}>Storefront</Button>}
              {s.phase === "Ready" && <Button icon={<ArrowUpRight />} onClick={() => window.open(s.admin_url, "_blank")}>WP admin</Button>}
              {s.phase === "Failed" && s.mine && <Button icon={<RotateCcw />} disabled={busy} onClick={() => void create(s.slug, s.name)}>Retry</Button>}
              {s.can_delete && s.phase !== "Deleting" && (
                <ConfirmMorph label="Delete" icon={<Trash2 size={16} strokeWidth={1.75} />} prompt={`Delete ${s.name} and its data?`} confirmMode="countdown" countdown={3}
                  confirmLabel="Delete" pendingLabel="Deleting" doneLabel="Deleting"
                  onConfirm={async () => { try { await api.stores.remove(s.slug); } catch (e) { setProblem(e instanceof Error ? e.message : String(e)); } await stores.reload(); }} />
              )}
            </div>
          </Card>
        ))}
        {!stores.loading && list.length === 0 && (
          <div className={styles.empty}>No extra stores yet. Each one you create gets its own namespace, database, volumes, Secret, address and MCP server.</div>
        )}
      </div>
      <Drawer open={adding} onClose={() => setAdding(false)} title="Create store"
        footer={<><Button variant="ghost" onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim() || slug.length < 2 || busy} onClick={() => void create()}>{busy ? "Creating" : "Create store"}</Button></>}>
        <Field label="Store name">{(id) => <Input id={id} value={name} maxLength={40} autoFocus placeholder="North Sports"
          onChange={(e) => { setName(e.target.value); if (!slugEdited) setSlug(toSlug(e.target.value)); }} />}</Field>
        <Field label="Short name" hint={`Its address and tool prefix: ${slug || "north"}.${storeDomain} and store-${slug || "north"}__list_orders.`}>
          {(id) => <Input id={id} value={slug} maxLength={20} placeholder="north" onChange={(e) => { setSlugEdited(true); setSlug(e.target.value.toLowerCase()); }}
            onKeyDown={(e) => e.key === "Enter" && name.trim() && slug.length >= 2 && void create()} />}
        </Field>
        {problem && <Alert tone="danger">{problem}</Alert>}
        <Alert>Takes a few minutes: Kubernetes makes a namespace, a database, WordPress with WooCommerce, and this store's MCP server. You can make {PER_USER} stores{me?.role === "admin" ? "; as an admin you can delete anyone's" : ""}.</Alert>
      </Drawer>
    </>
  );
}
