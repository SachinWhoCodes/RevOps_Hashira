import { useQuery } from "@tanstack/react-query";
import { ChevronDown, EyeOff, KeySquare, Plug, Unlink } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type BlindSpots } from "../api";
import { AccountMark, Badge, Card, ErrorNote, Loading, PageHeader, StatTile } from "../components/ui";
import { label, plural, sentence } from "../format";
import { cn } from "../lib/cn";

function GapRow({ code, entry, total }: { code: string; entry: BlindSpots["by_code"][string]; total: number }) {
  const [open, setOpen] = useState(false);
  const share = total ? entry.count / total : 0;
  return (
    <li>
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-hover/60">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-medium text-ink">{sentence(label(code))}</div>
          <div className="mt-0.5 text-[13px] text-muted">{entry.text}</div>
        </div>
        <div className="hidden w-40 shrink-0 sm:block">
          <div className="h-1.5 overflow-hidden rounded-full bg-hover">
            <div className={cn("h-full origin-left rounded-full animate-grow-x", share > 0.25 ? "bg-warn" : "bg-faint/60")} style={{ width: `${share * 100}%` }} />
          </div>
          <div className="mt-1 text-right text-[11.5px] text-faint tabular-nums">{Math.round(share * 100)}% of accounts</div>
        </div>
        <Badge tone={entry.count > 10 ? "warn" : "neutral"}>{plural(entry.count, "account")}</Badge>
        <ChevronDown className={cn("size-4 shrink-0 text-faint transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="flex flex-wrap gap-1.5 px-5 pb-4 animate-fade-in">
          {entry.accounts.map((a) => (
            <Link key={a.id} to={`/accounts/${a.id}`} className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel py-1 pr-2.5 pl-1 text-[12.5px] text-muted transition-colors hover:border-accent/40 hover:text-accent-fg">
              <AccountMark name={a.name} size="xs" className="rounded-md" />
              {a.name}
            </Link>
          ))}
        </div>
      )}
    </li>
  );
}

export default function BlindSpotsPage() {
  const data = useQuery({ queryKey: ["blind-spots"], queryFn: () => api<BlindSpots>("/blind-spots") });
  const d = data.data;
  const entries = d ? Object.entries(d.by_code).sort((a, b) => b[1].count - a[1].count) : [];
  return (
    <>
      <PageHeader title="Blind spots" description="What Casefile cannot see. A quiet account with no data is not a healthy account — it is an unknown one." />
      {data.isLoading && <Loading variant="cards" rows={3} />}
      {data.error && <ErrorNote error={data.error} />}
      {d && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <StatTile
              label="Unmatched records"
              icon={Unlink}
              tone={d.unmatched_records + d.ambiguous_records ? "warn" : "ok"}
              value={d.unmatched_records + d.ambiguous_records}
              to="/identity"
              hint={`${d.unmatched_records} unmatched · ${d.ambiguous_records} ambiguous`}
            />
            <StatTile label="Unmatched usage keys" icon={KeySquare} tone={d.unmatched_usage_keys ? "warn" : "ok"} value={d.unmatched_usage_keys} to="/identity" delay={60} hint="Product accounts not tied to a customer" />
            <StatTile
              label="Sources not fresh"
              icon={Plug}
              tone={d.connections_not_fresh.length ? "risk" : "ok"}
              value={d.connections_not_fresh.length}
              to="/connections"
              delay={120}
              hint={d.connections_not_fresh.map((c) => `${c.kind}/${c.name}`).join(", ") || "Every source is fresh"}
            />
          </div>
          <Card title="Accounts with gaps" description={`Out of ${plural(d.accounts_assessed, "account")} assessed · click a gap to see which`} icon={EyeOff} flush>
            {!entries.length ? (
              <p className="px-5 py-8 text-center text-sm text-muted">No gaps found.</p>
            ) : (
              <ul className="divide-y divide-line">
                {entries.map(([code, entry]) => <GapRow key={code} code={code} entry={entry} total={d.accounts_assessed} />)}
              </ul>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
