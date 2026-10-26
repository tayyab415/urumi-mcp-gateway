import { CircleAlert, CircleCheck, CircleHelp, CircleX, Circle } from "lucide-react";
import { cx } from "@/lib/cx";
import styles from "./Tone.module.css";

/** A status word with its icon. Color never stands alone: the word is always there. */
export function ToneLabel({ tone, children, quiet, className }: { tone: "success" | "danger" | "warning" | "neutral" | "checking"; children: React.ReactNode; quiet?: boolean; className?: string }) {
  const Icon = tone === "success" ? CircleCheck : tone === "danger" ? CircleX : tone === "warning" ? CircleHelp : tone === "checking" ? CircleAlert : Circle;
  return (
    <span className={cx(styles.dot, styles[tone], quiet && styles.quiet, className)}>
      <Icon />
      <span className={styles.label}>{children}</span>
    </span>
  );
}
