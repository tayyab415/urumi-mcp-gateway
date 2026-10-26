import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, CircleX, Info } from "lucide-react";
import { cx } from "@/lib/cx";
import styles from "./Alert.module.css";

export function Alert({ tone = "neutral", title, children, actions, className }: { tone?: "neutral" | "danger" | "warning" | "success"; title?: ReactNode; children?: ReactNode; actions?: ReactNode; className?: string }) {
  const Icon = tone === "danger" ? CircleX : tone === "warning" ? CircleAlert : tone === "success" ? CircleCheck : Info;
  return (
    <div className={cx(styles.alert, className)} role={tone === "danger" ? "alert" : "status"}>
      <Icon className={styles[tone]} />
      <div style={{ minWidth: 0, flex: 1 }}>
        {title && <div className={styles.title}>{title}</div>}
        {children && <div className={styles.body}>{children}</div>}
        {actions && <div className={styles.actions}>{actions}</div>}
      </div>
    </div>
  );
}
