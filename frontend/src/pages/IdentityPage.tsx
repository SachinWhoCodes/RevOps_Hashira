import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar, CircleCheck, KeySquare, Link2, Mail, Unlink } from "lucide-react";
import { useState } from "react";
import { api, type AccountRow, type Unmatched } from "../api";
import { DataTable, type Column } from "../components/table/DataTable";
import { Badge, Button, Callout, ErrorNote, PageHeader, Select, Tabs, useToast } from "../components/ui";
import { dateTime, label } from "../format";
import { useUrlBatch, useUrlState } from "../lib/hooks";
import { useMe } from "../lib/session";

type Override = { kind: "activity" | "email" | "domain" | "usage_key"; value: string; account_id: string };
type Activity = Unmatched["activities"][number];
type UsageKey = Unmatched["usage_keys"][number];

function MapControl({ accounts, onMap, options, busy }: {
  accounts: AccountRow[];
  options: { kind: Override["kind"]; value: string; text: string }[];
  onMap: (o: Override) => void;
  busy: boolean;
}) {
  const [target, setTarget] = useState("");
  const [choice, setChoice] = useState(0);
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {options.length > 1 && (
        <Select selectSize="sm" className="w-48" value={choice} onChange={(e) => setChoice(Number(e.target.value))} aria-label="What to map">
          {options.map((o, i) => <option key={i} value={i}>{o.text}</option>)}
        </Select>
      )}
      <Select selectSize="sm" className="w-48" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="To which account">
        <option value="">Choose account…</option>
        {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </Select>
      <Button size="sm" variant="primary" icon={Link2} disabled={!target || busy} onClick={() => onMap({ kind: options[choice].kind, value: options[choice].value, account_id: target })}>
        Map
      </Button>
    </div>
  );
}

export default function IdentityPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab] = useUrlState("tab", "records");
  const batch = useUrlBatch();
  const data = useQuery({ queryKey: ["unmatched"], queryFn: () => api<Unmatched>("/identity/unmatched") });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts") });
  const map = useMutation({
    mutationFn: (o: Override) => api<{ activities_changed: number; usage_keys_matched: number }>("/identity/overrides", { json: o }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      toast({ tone: "ok", title: "Mapped", description: `${r.activities_changed} records and ${r.usage_keys_matched} usage keys re-linked. Scores update on the next recompute.` });
    },
    onError: (e) => toast({ tone: "risk", title: "Could not map", description: e instanceof Error ? e.message : undefined }),
  });
  const isAdmin = me.data?.role === "admin";
  const sorted = [...(accounts.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const d = data.data;

  const activityColumns: Column<Activity>[] = [
    {
      id: "record",
      header: "Record",
      sortValue: (a) => a.subject,
      csv: (a) => a.subject,
      cell: (a) => (
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-subtle text-muted ring-1 ring-line">
            {a.type === "meeting" ? <Calendar className="size-4" /> : <Mail className="size-4" />}
          </span>
          <div className="min-w-0 max-w-md">
            <div className="truncate font-medium text-ink">{a.subject ?? "(no subject)"}</div>
            <div className="truncate text-xs text-muted">From {a.from} · outside: {a.external.join(", ") || "none"}</div>
            {a.candidates.length > 0 && <div className="mt-0.5 truncate text-xs text-warn-fg">Could be {a.candidates.map((c) => c.name).join(" or ")}</div>}
          </div>
        </div>
      ),
    },
    { id: "when", header: "When", sortValue: (a) => a.at, firstSort: "desc", csv: (a) => a.at, cell: (a) => <span className="whitespace-nowrap text-muted">{dateTime(a.at)}</span> },
    {
      id: "why",
      header: "Why unmatched",
      sortValue: (a) => a.method,
      csv: (a) => a.method,
      cell: (a) => <Badge tone={a.method === "ambiguous" ? "warn" : "neutral"}>{label(a.method)}</Badge>,
    },
    ...(isAdmin
      ? [
          {
            id: "map",
            header: "Map to",
            hideable: false,
            align: "right" as const,
            cell: (a: Activity) => (
              <MapControl
                accounts={sorted}
                busy={map.isPending}
                onMap={(o) => map.mutate(o)}
                options={[
                  { kind: "activity" as const, value: a.id, text: "Only this record" },
                  ...a.external.map((p) => ({ kind: "email" as const, value: p, text: `Everything from ${p}` })),
                  ...[...new Set(a.external.map((p) => p.split("@")[1]))].map((dm) => ({ kind: "domain" as const, value: dm, text: `Everything from @${dm}` })),
                ]}
              />
            ),
          },
        ]
      : []),
  ];

  const keyColumns: Column<UsageKey>[] = [
    { id: "key", header: "Usage key", sortValue: (u) => u.key, csv: (u) => u.key, cell: (u) => <span className="font-mono text-[12.5px] text-ink">{u.key}</span> },
    { id: "rows", header: "Days of usage", align: "right", firstSort: "desc", sortValue: (u) => u.rows, csv: (u) => u.rows, cell: (u) => u.rows },
    { id: "last", header: "Last day", sortValue: (u) => u.last_date, firstSort: "desc", csv: (u) => u.last_date, cell: (u) => <span className="text-muted">{u.last_date}</span> },
    ...(isAdmin
      ? [
          {
            id: "map",
            header: "Map to",
            hideable: false,
            align: "right" as const,
            cell: (u: UsageKey) => <MapControl accounts={sorted} busy={map.isPending} onMap={(o) => map.mutate(o)} options={[{ kind: "usage_key", value: u.key, text: "This usage key" }]} />,
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title="Unmatched records"
        description="Records Casefile could not tie to exactly one customer. They never count towards a score until someone maps them."
      />
      {data.error && <ErrorNote error={data.error} />}
      {!isAdmin && <Callout className="mb-4" tone="neutral" title="Only admins can map records">You can see what is unmatched; ask an admin to map it.</Callout>}
      <Tabs
        className="mb-4"
        value={tab}
        onChange={(v) => batch({ tab: v === "records" ? null : v, page: null, q: null })}
        tabs={[
          { value: "records", label: "Emails and meetings", icon: Mail, count: d?.activities_total },
          { value: "usage", label: "Product usage keys", icon: KeySquare, count: d?.usage_keys.length },
        ]}
      />
      {tab === "records" ? (
        <DataTable
          id="unmatched"
          rows={d?.activities}
          loading={data.isLoading || accounts.isLoading}
          columns={activityColumns}
          rowKey={(a) => a.id}
          defaultSort={{ id: "when", dir: "desc" }}
          search={{ placeholder: "Search subject or address", text: (a) => `${a.subject ?? ""} ${a.from ?? ""} ${a.external.join(" ")}` }}
          csvName="casefile-unmatched-records"
          noun="records"
          empty={{ icon: CircleCheck, title: "Everything is matched", description: "Every email and meeting is tied to exactly one customer." }}
        />
      ) : (
        <DataTable
          id="usage-keys"
          rows={d?.usage_keys}
          loading={data.isLoading}
          columns={keyColumns}
          rowKey={(u) => u.key}
          defaultSort={{ id: "rows", dir: "desc" }}
          search={{ placeholder: "Search keys", text: (u) => u.key }}
          csvName="casefile-unmatched-usage-keys"
          noun="keys"
          empty={{ icon: Unlink, title: "Every usage key is matched" }}
        />
      )}
    </>
  );
}
