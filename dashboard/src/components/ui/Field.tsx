import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import styles from "./Field.module.css";

export function Field({ label, hint, error, children, className }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cx(styles.field, className)}>
      <label className={styles.label} htmlFor={id}>{label}</label>
      {children(id)}
      {error ? <div className={styles.error} role="alert">{error}</div> : hint ? <div className={styles.hint}>{hint}</div> : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }>(function Input({ className, mono, ...rest }, ref) {
  return <input ref={ref} className={cx(styles.input, mono && styles.mono, className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cx(styles.input, styles.textarea, className)} {...rest} />;
});

export const InputRow = ({ children, className }: { children: ReactNode; className?: string }) => <div className={cx(styles.row, className)}>{children}</div>;
