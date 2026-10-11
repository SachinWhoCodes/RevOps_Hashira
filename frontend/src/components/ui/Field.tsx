import { Check, ChevronDown, Eye, EyeOff, Search, X, type LucideIcon } from "lucide-react";
import { useId, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export const controlClass =
  "w-full rounded-lg border border-line bg-panel text-sm text-ink shadow-xs outline-none transition-[border-color,box-shadow] " +
  "placeholder:text-faint hover:border-line-strong focus:border-accent focus:shadow-ring disabled:cursor-not-allowed disabled:opacity-60 " +
  "aria-invalid:border-risk/60 aria-invalid:focus:shadow-[0_0_0_4px_color-mix(in_oklab,var(--c-risk)_16%,transparent)]";

const heights = { sm: "h-8 text-[13px]", md: "h-9", lg: "h-11 text-[15px]" } as const;

export function Field({
  label,
  hint,
  error,
  optional,
  children,
  htmlFor,
  className,
  action,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      {(label || action) && (
        <div className="flex items-center justify-between gap-2">
          {label && (
            <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink">
              {label}
              {optional && <span className="ml-1 font-normal text-faint">(optional)</span>}
            </label>
          )}
          {action}
        </div>
      )}
      {children}
      {error ? <p className="text-xs text-risk-fg">{error}</p> : hint ? <p className="text-xs leading-relaxed text-faint">{hint}</p> : null}
    </div>
  );
}

type InputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> & {
  icon?: LucideIcon;
  right?: ReactNode;
  inputSize?: keyof typeof heights;
};

export function Input({ icon: Icon, right, inputSize = "md", className, ...rest }: InputProps) {
  return (
    <div className={cn("relative", className)}>
      {Icon && <Icon aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" />}
      <input className={cn(controlClass, heights[inputSize], Icon ? "pl-9" : "pl-3", right ? "pr-10" : "pr-3")} {...rest} />
      {right && <div className="absolute inset-y-0 right-1 flex items-center">{right}</div>}
    </div>
  );
}

export function PasswordInput(props: Omit<InputProps, "type" | "right">) {
  const [shown, setShown] = useState(false);
  return (
    <Input
      {...props}
      type={shown ? "text" : "password"}
      right={
        <button
          type="button"
          onClick={() => setShown((s) => !s)}
          aria-label={shown ? "Hide password" : "Show password"}
          className="grid size-8 place-items-center rounded-md text-faint transition-colors hover:text-ink"
        >
          {shown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      }
    />
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  className,
  inputSize = "sm",
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  inputSize?: keyof typeof heights;
  autoFocus?: boolean;
}) {
  return (
    <Input
      icon={Search}
      inputSize={inputSize}
      value={value}
      autoFocus={autoFocus}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && value) {
          e.stopPropagation();
          onChange("");
        }
      }}
      placeholder={placeholder}
      aria-label={placeholder}
      className={className}
      right={
        value ? (
          <button type="button" onClick={() => onChange("")} aria-label="Clear search" className="grid size-7 place-items-center rounded-md text-faint hover:text-ink">
            <X className="size-3.5" />
          </button>
        ) : undefined
      }
    />
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(controlClass, "min-h-24 px-3 py-2 leading-relaxed", className)} {...rest} />;
}

export function Select({
  className,
  selectSize = "md",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { selectSize?: keyof typeof heights }) {
  return (
    <div className={cn("relative", className)}>
      <select className={cn(controlClass, heights[selectSize], "appearance-none truncate pr-8 pl-3")} {...rest}>
        {children}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-faint" />
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  description,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <label htmlFor={id} className={cn("group flex items-start gap-2.5 text-sm", disabled ? "cursor-not-allowed opacity-70" : "cursor-pointer", className)}>
      <span className="relative mt-[3px] grid size-4 shrink-0 place-items-center">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="peer size-4 cursor-[inherit] appearance-none rounded-[5px] border border-line-strong bg-panel shadow-xs transition-colors checked:border-accent checked:bg-accent focus-visible:shadow-ring focus-visible:outline-none"
        />
        <Check aria-hidden strokeWidth={3} className="pointer-events-none absolute size-3 text-white opacity-0 transition-opacity peer-checked:opacity-100" />
      </span>
      {(label || description) && (
        <span className="min-w-0">
          {label && <span className="text-ink">{label}</span>}
          {description && <span className="mt-0.5 block text-xs text-muted">{description}</span>}
        </span>
      )}
    </label>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean }) {
  return (
    <label className={cn("inline-flex items-center gap-2 text-[13px] text-muted", disabled ? "opacity-60" : "cursor-pointer")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors", checked ? "bg-accent" : "bg-line-strong")}
      >
        <span className={cn("absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow-sm transition-transform duration-200", checked && "translate-x-4")} />
      </button>
      {label}
    </label>
  );
}
