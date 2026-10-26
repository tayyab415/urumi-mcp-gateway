import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { motion } from "motion/react";
import { ArrowRight } from "lucide-react";
import { useAuth } from "@/state/auth";
import { ApiError, api, type User } from "@/lib/api";
import { settle } from "@/lib/motion";
import { AgentGlyph } from "@/components/ui/AgentGlyph";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import styles from "./Login.module.css";

export function LoginPage() {
  const { login, loginAs } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // The public demo lists the people to sign in as. null: still asking; []: the deployment has it off.
  const [people, setPeople] = useState<User[] | null>(null);
  const [withPassword, setWithPassword] = useState(false);
  const [signingIn, setSigningIn] = useState<number | null>(null);

  useEffect(() => {
    api.auth.demoUsers().then(setPeople, () => setPeople([]));
  }, []);

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

  async function pick(person: User) {
    setSigningIn(person.id);
    setError(null);
    try {
      await loginAs(person.id);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
      setSigningIn(null);
    }
  }

  const list = people !== null && people.length > 0 && !withPassword;

  return (
    <div className={styles.page}>
      <motion.div className={styles.wrap} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={settle}>
        <div className={styles.brand}><AgentGlyph size={30} /> Urumi</div>
        <div className={styles.tag}>Team MCP gateway</div>
        {people === null ? null : list ? (
          <>
            <Card className={styles.people}>
              <div className={styles.heading}>Sign in as</div>
              {people.map((p) => (
                <button key={p.id} type="button" className={styles.person} disabled={signingIn !== null} onClick={() => void pick(p)}>
                  <Avatar name={p.name || p.email} size={34} />
                  <span className={styles.who}>
                    <span className={styles.name}>{p.name || p.email}</span>
                    <span className={styles.email}>{p.email}</span>
                  </span>
                  <span className={p.role === "admin" ? styles.admin : styles.role}>{p.role === "admin" ? "Admin" : "Member"}</span>
                  <ArrowRight className={styles.go} size={16} aria-hidden />
                </button>
              ))}
              {error && <div className={styles.error} role="alert">{error}</div>}
            </Card>
            <div className={styles.foot}>
              Each person sees only what their role allows.{" "}
              <button type="button" className={styles.link} onClick={() => { setError(null); setWithPassword(true); }}>Use a password</button>
            </div>
          </>
        ) : (
          <>
            <Card className={styles.card}>
              <form onSubmit={submit} style={{ display: "contents" }}>
                <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />}</Field>
                <Field label="Password" error={error}>{(id) => <Input id={id} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required aria-invalid={error ? true : undefined} />}</Field>
                <Button type="submit" variant="primary" pending={pending} style={{ width: "100%", marginTop: 4 }}>Sign in</Button>
              </form>
            </Card>
            {people !== null && people.length > 0 ? (
              <button type="button" className={styles.link} onClick={() => { setError(null); setWithPassword(false); }}>Pick a person instead</button>
            ) : (
              <div className={styles.foot}>Ask an admin for an invite</div>
            )}
            {import.meta.env.DEV && <div className={styles.hint}>Local stack: admin@urumi.test / admin-password-1</div>}
          </>
        )}
      </motion.div>
    </div>
  );
}
