import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { motion } from "motion/react";
import { useAuth } from "@/state/auth";
import { ApiError } from "@/lib/api";
import { settle } from "@/lib/motion";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import styles from "./Login.module.css";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.page}>
      <motion.div className={styles.wrap} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={settle}>
        <div className={styles.brand}><AgentGlyph size={30} /> Urumi</div>
        <div className={styles.tag}>Team MCP gateway</div>
        <Card className={styles.card}>
          <form onSubmit={submit} style={{ display: "contents" }}>
            <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />}</Field>
            <Field label="Password" error={error}>{(id) => <Input id={id} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required aria-invalid={error ? true : undefined} />}</Field>
            <Button type="submit" variant="primary" pending={pending} style={{ width: "100%", marginTop: 4 }}>Sign in</Button>
          </form>
        </Card>
        <div className={styles.foot}>Ask an admin for an invite</div>
        {import.meta.env.DEV && <div className={styles.hint}>Local stack: admin@urumi.test / admin-password-1</div>}
      </motion.div>
    </div>
  );
}
