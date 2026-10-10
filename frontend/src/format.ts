const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
const day = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
const dayTime = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export const money = (v: number | null | undefined) => (v == null ? "—" : inr.format(v));
export const date = (iso: string | null | undefined) => (iso ? day.format(new Date(iso)) : "—");
export const dateTime = (iso: string | null | undefined) => (iso ? dayTime.format(new Date(iso)) : "—");

export function ago(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "—";
  const days = Math.round((now.getTime() - new Date(iso).getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days > 0) return `${days}d ago`;
  return `in ${-days}d`;
}

export const label = (s: string) => s.replace(/_/g, " ");
