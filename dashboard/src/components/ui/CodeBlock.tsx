import { CopyButton } from "@/arc/copy-button";
import { cx } from "@/lib/cx";
import styles from "./CodeBlock.module.css";

export function CodeBlock({ code, className, copy = true }: { code: string; className?: string; copy?: boolean }) {
  return (
    <div className={cx(styles.block, className)}>
      <pre style={{ paddingRight: copy ? 40 : 0 }}>{code}</pre>
      {copy && <CopyButton value={code} iconOnly variant="plain" className={styles.copy} />}
    </div>
  );
}

export function InlineCode({ code, className }: { code: string; className?: string }) {
  return <span className={cx(styles.inline, className)} title={code}><code>{code}</code></span>;
}
