import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Plus, Trash2 } from "lucide-react";
import { ConfirmMorph } from "@/arc/confirm-morph";
import type { Run } from "@/components/run-card/types";
import { AgentRunCard } from "@/components/run-card/AgentRunCard";
import { PROVISION_SCRIPT, playScript, type Controls } from "@/lib/scripted-run";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { ToneLabel } from "@/components/ui/ToneLabel";
import { Field, Input } from "@/components/ui/Field";
import { Drawer } from "@/components/ui/Drawer";
import { cx } from "@/lib/cx";
import styles from "./Stores.module.css";

interface Store { id: string; name: string; host: string; state: "ready" | "provisioning" | "failed"; createdAt: number; run?: Run; real?: boolean }

const QUOTA = 5;
const REAL_STORE: Store = { id: "urumi-store", name: "Urumi store", host: "shop.urumi.test:18080", state: "ready", createdAt: Date.now() - 3 * 86400000, real: true };

/** The bonus multi-store page. The provisioning run card is the first reference card doing what it is for:
 *  a background job with a known number of steps. Provisioning itself is simulated here and says so. */
export function StoresPage() {
  const [stores, setStores] = useState<Store[]>([REAL_STORE]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const controls = useRef<Record<string, Controls>>({});

  useEffect(() => () => Object.values(controls.current).forEach((c) => c.dispose()), []);

  function create() {
    const id = `s${Date.now()}`;
    const host = `${name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "store"}.urumi.test`;
    setStores((s) => [...s, { id, name: name.trim(), host, state: "provisioning", createdAt: Date.now() }]);
    setExpanded(id);
    controls.current[id] = playScript(PROVISION_SCRIPT, "Provisioner (simulated)", (run) => {
      setStores((s) => s.map((x) => (x.id === id ? { ...x, run, state: run.status === "done" ? "ready" : run.status === "failed" || run.status === "stopped" ? "failed" : "provisioning" } : x)));
    }, { summary: (r) => `Done in ${Math.round(((r.endedAt ?? Date.now()) - r.startedAt - (r.pausedMs ?? 0)) / 1000)}s · store ready`, failAt: name.trim().toLowerCase().includes("fail") ? 1 : undefined });
    setAdding(false);
    setName("");
  }

  const used = stores.length;
  return (
    <>
      <PageHeader
        title="Stores"
        subtitle={<span className={styles.quota}><span>{used} of {QUOTA} stores used</span><span className={styles.meter}>{Array.from({ length: QUOTA }, (_, i) => <span key={i} className={cx(styles.seg, i < used && styles.segOn)} />)}</span></span>}
        actions={<Button variant="primary" icon={<Plus />} disabled={used >= QUOTA} onClick={() => setAdding(true)}>Create store</Button>}
      />
      <Alert tone="warning" title="Provisioning is simulated on this page" className="">
        The first store is the real Helm release from <code>charts/urumi-store</code>. New stores here run a scripted provisioning job so the run card can be judged with real timers; no namespace is created. Name a store with "fail" in it to see the failed state.
      </Alert>
      <div className={styles.list} style={{ marginTop: 12 }}>
        {stores.map((s) => (
          <Card key={s.id} className={styles.store}>
            <div className={styles.head}>
              <StatusOrb state={s.state === "ready" ? "done" : s.state === "failed" ? "failed" : "running"} size={26} glyph={false} />
              <span className={styles.name}>{s.name}</span>
              <span className={styles.host}>{s.host}</span>
              <span className={styles.state}><ToneLabel tone={s.state === "ready" ? "success" : s.state === "failed" ? "danger" : "checking"} quiet>{s.state === "ready" ? "Ready" : s.state === "failed" ? "Failed" : "Provisioning"}</ToneLabel></span>
            </div>
            {s.run && s.state !== "ready" ? (
              <AgentRunCard
                run={s.run}
                expanded={expanded === s.id}
                onToggle={() => setExpanded(expanded === s.id ? null : s.id)}
                onPause={() => controls.current[s.id]?.pause()}
                onResume={() => controls.current[s.id]?.resume()}
                onStop={() => controls.current[s.id]?.stop()}
                onRetry={() => { controls.current[s.id]?.dispose(); setStores((xs) => xs.filter((x) => x.id !== s.id)); setName(s.name.replace(/fail/i, "retry")); setTimeout(create, 0); }}
              />
            ) : (
              <div className={styles.meta}>
                <span>Created {Math.round((Date.now() - s.createdAt) / 86400000) || "today"}{Math.round((Date.now() - s.createdAt) / 86400000) ? " days ago" : ""}</span>
                <span>{s.real ? "WordPress + WooCommerce + MariaDB on Kubernetes" : "simulated"}</span>
              </div>
            )}
            {s.state === "ready" && (
              <div className={styles.bar}>
                <Button icon={<ArrowUpRight />} onClick={() => window.open(`http://${s.host}/`, "_blank")}>Storefront</Button>
                <Button icon={<ArrowUpRight />} onClick={() => window.open(`http://${s.host}/wp-admin/`, "_blank")}>WP admin</Button>
                {!s.real && <ConfirmMorph label="Delete" icon={<Trash2 size={16} strokeWidth={1.75} />} prompt={`Delete ${s.name}?`} confirmMode="countdown" countdown={3} confirmLabel="Delete" pendingLabel="Deleting" doneLabel="Deleted" onConfirm={() => { setStores((xs) => xs.filter((x) => x.id !== s.id)); }} />}
              </div>
            )}
          </Card>
        ))}
      </div>
      <Drawer open={adding} onClose={() => setAdding(false)} title="Create store" footer={<><Button variant="ghost" onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim()} onClick={create}>Create store</Button></>}>
        <Field label="Store name" hint="The host is derived from it.">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Atlas Coffee" autoFocus onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} />}</Field>
        <Alert>Simulated. A real provisioner would run the six steps shown on the card: namespace, database, WordPress, WooCommerce, seed data, health checks.</Alert>
      </Drawer>
    </>
  );
}
