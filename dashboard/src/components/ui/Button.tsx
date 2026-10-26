import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { motion, type HTMLMotionProps } from "motion/react";
import { LoaderCircle } from "lucide-react";
import { snap } from "@/lib/motion";
import { cx } from "@/lib/cx";
import styles from "./Button.module.css";

export type ButtonVariant = "secondary" | "primary" | "ghost" | "danger" | "link";

export interface ButtonProps extends Omit<HTMLMotionProps<"button">, "ref" | "children"> {
  variant?: ButtonVariant;
  size?: "md" | "sm";
  icon?: ReactNode;
  trailing?: ReactNode;
  pending?: boolean;
  children?: ReactNode;
}

/** A pill button. Presses scale to 0.96 on Arc's snappy spring; color answers hover. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, trailing, pending, className, children, disabled, type = "button", ...rest },
  ref,
) {
  const iconOnly = !children && Boolean(icon || trailing);
  return (
    <motion.button
      ref={ref}
      type={type as ButtonHTMLAttributes<HTMLButtonElement>["type"]}
      className={cx(styles.button, styles[variant], size === "sm" && styles.sm, iconOnly && styles.icon, className)}
      whileTap={disabled || variant === "link" ? undefined : { scale: 0.96 }}
      transition={snap}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      {...rest}
    >
      {pending ? <LoaderCircle className="spin" /> : icon}
      {children}
      {trailing}
    </motion.button>
  );
});
