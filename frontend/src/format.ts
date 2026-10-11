const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
const inrFull = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const day = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
const dayShort = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });
const dayTime = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const weekday = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long" });

export const money = (v: number | null | undefined) => (v == null ? "—" : inr.format(v));
export const moneyFull = (v: number | null | undefined) => (v == null ? "—" : inrFull.format(v));
export const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-IN"));
export const date = (iso: string | null | undefined) => (iso ? day.format(new Date(iso)) : "—");
export const dateShort = (iso: string | null | undefined) => (iso ? dayShort.format(new Date(iso)) : "—");
export const dateTime = (iso: string | null | undefined) => (iso ? dayTime.format(new Date(iso)) : "—");
export const longDate = (d: Date) => weekday.format(d);

export function ago(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "—";
  const ms = now.getTime() - new Date(iso).getTime();
  const future = ms < 0;
  const abs = Math.abs(ms);
  const min = Math.round(abs / 60_000);
  const h = Math.round(abs / 3_600_000);
  const d = Math.round(abs / 86_400_000);
  const text = min < 1 ? "just now" : min < 60 ? `${min} min` : h < 24 ? `${h} h` : d === 1 ? "1 day" : `${d} days`;
  if (text === "just now") return text;
  return future ? `in ${text}` : `${text} ago`;
}

// Whole days only (used where a date, not a moment, is meant: renewals, meetings).
export function daysFrom(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "—";
  const days = Math.round((new Date(iso).getTime() - now.getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

export function renewalText(days: number | null | undefined): string {
  if (days == null) return "Renewal date unknown";
  if (days < 0) return `Renewal overdue by ${-days} day${days === -1 ? "" : "s"}`;
  if (days === 0) return "Renews today";
  return `Renews in ${days} day${days === 1 ? "" : "s"}`;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
export const pct = (v: number | null | undefined, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const label = (s: string) => s.replace(/_/g, " ");
export const sentence = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function greeting(now = new Date()): string {
  const h = now.getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
