import { useId } from "react";
import { cn } from "../lib/cn";

// The Casefile mark: a document with a check (evidence, verified) on the brand gradient.
export function LogoMark({ size = 28, className }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={`${id}g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="0.55" stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id}g)`} />
      <path d="M10 8.5h8.2l4.3 4.3V22a1.5 1.5 0 0 1-1.5 1.5H10A1.5 1.5 0 0 1 8.5 22V10A1.5 1.5 0 0 1 10 8.5Z" fill="#fff" fillOpacity=".95" />
      <path d="M18 8.5v3.3c0 .6.4 1 1 1h3.5" fill="none" stroke="#c7c9ff" strokeWidth="1.2" />
      <path d="m12 17.2 2.4 2.3 4.6-4.8" fill="none" stroke="#6366f1" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ className, light = false }: { className?: string; light?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className={cn("text-[15px] font-semibold tracking-[-0.02em]", light ? "text-white" : "text-ink")}>Casefile</span>
    </span>
  );
}
