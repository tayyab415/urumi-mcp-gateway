import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useAuth } from "@/state/auth";
import { ApiError, api } from "@/lib/api";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { Alert } from "@/components/ui/Alert";
import styles from "./Login.module.css";

export function AcceptInvitePage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [invite, setInvite] = useState<{ email: string; role: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!token) { setProblem("This link has no invitation token."); return; }
    api.invites.lookup(token).then(setInvite).catch((e) => setProblem(e instanceof ApiError ? e.message : "Could not read the invitation"));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    try {
      await api.invites.accept(token, name, password);
      await refresh();
      navigate("/chat", { replace: true });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Could not accept the invitation");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.wrap}>
        <div className={styles.brand}><AgentGlyph size={30} /> Urumi</div>
        <div className={styles.tag}>Join the team</div>
        <Card className={styles.card}>
          {problem ? <Alert tone="danger">{problem}</Alert> : invite ? (
            <form onSubmit={submit} style={{ display: "contents" }}>
              <Alert>Invited as <strong>{invite.role}</strong> for {invite.email}</Alert>
              <Field label="Your name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required autoFocus />}</Field>
              <Field label="Password" hint="At least 10 characters">{(id) => <Input id={id} type="password" autoComplete="new-password" minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} required />}</Field>
              <Button type="submit" variant="primary" pending={pending} style={{ width: "100%" }}>Create account</Button>
            </form>
          ) : <div style={{ color: "var(--text-muted)" }}>Checking the invitation…</div>}
        </Card>
      </div>
    </div>
  );
}
