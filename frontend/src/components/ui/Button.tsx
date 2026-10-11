import type { LucideIcon } from "lucide-react";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Spinner } from "./Feedback";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "soft" | "danger" | "dangerSoft";
export type ButtonSize = "xs" | "sm" | "md" | "lg";

const base =
  "relative inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium " +
  "transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-150 active:translate-y-px " +
  "disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.16),0_1px_2px_rgb(0_0_0/0.12)] hover:bg-accent-hover",
  secondary: "border border-line bg-panel text-ink shadow-xs hover:border-line-strong hover:bg-hover",
  ghost: "text-muted hover:bg-hover hover:text-ink",
  soft: "bg-accent-soft text-accent-fg hover:bg-accent/15",
  danger: "bg-risk text-white shadow-xs hover:bg-risk/90",
  dangerSoft: "border border-line bg-panel text-risk-fg shadow-xs hover:border-risk/40 hover:bg-risk-soft",
};

const sizes: Record<ButtonSize, string> = {
  xs: "h-7 px-2 text-xs [&_svg]:size-3.5",
  sm: "h-8 px-2.5 text-[13px] [&_svg]:size-4",
  md: "h-9 px-3.5 text-sm [&_svg]:size-4",
  lg: "h-11 px-5 text-[15px] [&_svg]:size-[18px]",
};

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "sm", className?: string) {
  return cn(base, variants[variant], sizes[size], className);
}

type Common = { variant?: ButtonVariant; size?: ButtonSize; icon?: LucideIcon; iconRight?: LucideIcon; children?: ReactNode };

export function Button({
  variant = "secondary",
  size = "sm",
  icon: Icon,
  iconRight: IconRight,
  loading = false,
  className,
  children,
  type = "button",
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & Common & { loading?: boolean }) {
  return (
    <button type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <Spinner /> : Icon ? <Icon aria-hidden /> : null}
      {children}
      {IconRight && <IconRight aria-hidden />}
    </button>
  );
}

// A square icon-only button; the label is required (it becomes the accessible name and the tooltip).
export function IconButton({
  icon: Icon,
  label,
  variant = "ghost",
  size = "sm",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; variant?: ButtonVariant; size?: ButtonSize }) {
  const square = { xs: "w-7 px-0", sm: "w-8 px-0", md: "w-9 px-0", lg: "w-11 px-0" }[size];
  return (
    <button type="button" aria-label={label} title={label} className={buttonClass(variant, size, cn(square, className))} {...rest}>
      <Icon aria-hidden />
    </button>
  );
}

// Router link that looks like a button (keeps real <a> semantics: middle-click, copy link).
export function ButtonLink({
  to,
  variant = "secondary",
  size = "sm",
  icon: Icon,
  iconRight: IconRight,
  className,
  children,
}: Common & { to: string; className?: string }) {
  return (
    <Link to={to} className={buttonClass(variant, size, className)}>
      {Icon && <Icon aria-hidden />}
      {children}
      {IconRight && <IconRight aria-hidden />}
    </Link>
  );
}

// External or API link (downloads, exports) styled as a button.
export function ButtonAnchor({
  variant = "secondary",
  size = "sm",
  icon: Icon,
  className,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & Common) {
  return (
    <a className={buttonClass(variant, size, className)} {...rest}>
      {Icon && <Icon aria-hidden />}
      {children}
    </a>
  );
}
