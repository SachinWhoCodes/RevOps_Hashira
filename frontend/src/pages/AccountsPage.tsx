import { useQuery } from "@tanstack/react-query";
import { Building } from "lucide-react";
import { api, type AccountRow, type Queue } from "../api";
import { DataTable, type Column } from "../components/table/DataTable";
import { AccountMark, Avatar, Badge, PageHeader, Segmented, Select, StatusDot, Tooltip } from "../components/ui";
import { ago, date, money } from "../format";
import { cn } from "../lib/cn";
import { useUrlBatch, useUrlState } from "../lib/hooks";

type Status = "active" | "churned" | "all";
type Window = "any" | "30" | "90" | "later" | "unknown";

function inWindow(a: AccountRow, w: Window) {
  const d = a.days_to_renewal;
  if (w === "any") return true;
  if (w === "unknown") return d == null;
  if (d == null) return false;
  if (w === "later") return d > 90;
  return d >= 0 && d <= Number(w);
}

export default function AccountsPage() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts") });
  const risk = useQuery({ queryKey: ["queue", "risk", "all"], queryFn: () => api<Queue>("/queue?kind=risk&owner=all") });
  const [status] = useUrlState("status", "active") as [Status, (v: string) => void];
  const [win] = useUrlState("renewal", "any") as [Window, (v: string) => void];
  const [owner] = useUrlState("owner", "");
  const batch = useUrlBatch();

  const all = accounts.data;
  const atRisk = new Map((risk.data?.items ?? []).map((i) => [i.account.id, i.risk]));
  const owners = [...new Set((all ?? []).map((a) => a.owner).filter((o): o is string => !!o))].sort();
  const rows = all?.filter((a) => (status === "all" || a.status === status) && inWindow(a, win) && (!owner || a.owner === owner));
  const maxArr = Math.max(...(all ?? []).map((a) => a.arr ?? 0), 1);

  const columns: Column<AccountRow>[] = [
    {
      id: "name",
      header: "Account",
      sortValue: (a) => a.name,
      csv: (a) => a.name,
      cell: (a) => (
        <div className="flex min-w-0 items-center gap-3">
          <AccountMark name={a.name} />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium text-ink">{a.name}</span>
              {atRisk.has(a.id) && (
                <Tooltip content={`Open risk case · risk ${atRisk.get(a.id)}`}>
                  <StatusDot tone="risk" />
                </Tooltip>
              )}
            </div>
            <div className="truncate text-xs text-faint">{a.domains[0]}{a.domains.length > 1 && <span className="text-faint/80"> +{a.domains.length - 1}</span>}</div>
          </div>
        </div>
      ),
    },
    {
      id: "owner",
      header: "Owner",
      sortValue: (a) => a.owner,
      csv: (a) => a.owner,
      cell: (a) =>
        a.owner ? (
          <span className="flex items-center gap-2 whitespace-nowrap text-muted">
            <Avatar name={a.owner} size="xs" />
            {a.owner}
          </span>
        ) : (
          <span className="text-faint">Unassigned</span>
        ),
    },
    {
      id: "renewal",
      header: "Renewal",
      sortValue: (a) => (a.status === "churned" ? null : a.days_to_renewal),
      csv: (a) => a.renewal_date,
      cell: (a) =>
        a.status === "churned" ? (
          <Badge tone="risk">Churned</Badge>
        ) : a.days_to_renewal == null ? (
          <span className="text-faint">Unknown</span>
        ) : (
          <div className="whitespace-nowrap">
            <Badge tone={a.days_to_renewal < 0 ? "risk" : a.days_to_renewal <= 30 ? "risk" : a.days_to_renewal <= 90 ? "warn" : "neutral"}>
              {a.days_to_renewal < 0 ? `${-a.days_to_renewal}d overdue` : `in ${a.days_to_renewal}d`}
            </Badge>
            <div className="mt-1 text-xs text-faint">{date(a.renewal_date)}</div>
          </div>
        ),
    },
    {
      id: "arr",
      header: "ARR",
      align: "right",
      firstSort: "desc",
      sortValue: (a) => a.arr,
      csv: (a) => a.arr,
      cell: (a) => (
        <div className="inline-flex flex-col items-end gap-1">
          <span className="font-medium text-ink">{money(a.arr)}</span>
          <span className="h-1 w-16 overflow-hidden rounded-full bg-hover">
            <span className="block h-full rounded-full bg-accent/70" style={{ width: `${((a.arr ?? 0) / maxArr) * 100}%` }} />
          </span>
        </div>
      ),
    },
    { id: "seats", header: "Seats", align: "right", defaultHidden: true, firstSort: "desc", sortValue: (a) => a.seats_purchased, csv: (a) => a.seats_purchased, cell: (a) => a.seats_purchased ?? <span className="text-faint">—</span> },
    {
      id: "activity",
      header: "Last activity",
      firstSort: "desc",
      sortValue: (a) => a.last_activity_at,
      csv: (a) => a.last_activity_at,
      cell: (a) => <span className="whitespace-nowrap text-muted">{ago(a.last_activity_at)}</span>,
    },
    {
      id: "meeting",
      header: "Next meeting",
      sortValue: (a) => a.next_meeting_at,
      csv: (a) => a.next_meeting_at,
      cell: (a) => (a.next_meeting_at ? <span className="whitespace-nowrap text-ink">{date(a.next_meeting_at)}</span> : <span className="text-faint">None booked</span>),
    },
    { id: "emails", header: "Emails 30d", align: "right", firstSort: "desc", sortValue: (a) => a.emails_30d, csv: (a) => a.emails_30d, cell: (a) => <span className={cn(a.emails_30d === 0 && "text-faint")}>{a.emails_30d}</span> },
    {
      id: "users",
      header: "Active users 7d",
      align: "right",
      firstSort: "desc",
      sortValue: (a) => a.active_users_7d,
      csv: (a) => a.active_users_7d,
      cell: (a) => (a.active_users_7d == null ? <span className="text-faint">No data</span> : a.active_users_7d),
    },
  ];

  return (
    <>
      <PageHeader title="Accounts" description="Every customer Casefile knows about, with renewal, value and how recently you heard from them." />
      <DataTable
        id="accounts"
        rows={rows}
        loading={accounts.isLoading}
        error={accounts.error}
        columns={columns}
        rowKey={(a) => a.id}
        rowHref={(a) => `/accounts/${a.id}`}
        defaultSort={{ id: "renewal", dir: "asc" }}
        search={{ placeholder: "Search name or domain", text: (a) => `${a.name} ${a.domains.join(" ")} ${a.owner ?? ""}` }}
        csvName="casefile-accounts"
        noun="accounts"
        empty={{ icon: Building, title: "No accounts match", description: "Change the filters, or connect a CRM on the Connections page." }}
        filters={
          <>
            <Segmented<Status>
              size="xs"
              label="Status"
              value={status}
              onChange={(v) => batch({ status: v === "active" ? null : v, page: null })}
              options={[
                { value: "active", label: "Active" },
                { value: "churned", label: "Churned" },
                { value: "all", label: "All" },
              ]}
            />
            <Select selectSize="sm" className="w-40" value={win} onChange={(e) => batch({ renewal: e.target.value === "any" ? null : e.target.value, page: null })} aria-label="Renewal window">
              <option value="any">Any renewal</option>
              <option value="30">Next 30 days</option>
              <option value="90">Next 90 days</option>
              <option value="later">After 90 days</option>
              <option value="unknown">Date unknown</option>
            </Select>
            <Select selectSize="sm" className="w-40" value={owner} onChange={(e) => batch({ owner: e.target.value || null, page: null })} aria-label="Owner">
              <option value="">All owners</option>
              {owners.map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          </>
        }
      />
    </>
  );
}
