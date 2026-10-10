// Thin fetch wrapper. Every request carries the CSRF header the API requires for writes.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, options: { method?: string; json?: unknown } = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: options.method ?? (options.json !== undefined ? "POST" : "GET"),
    credentials: "same-origin",
    headers: {
      "x-casefile-csrf": "1",
      ...(options.json !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: options.json !== undefined ? JSON.stringify(options.json) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- types

export type User = { id: string; email: string; name: string; role: "owner" | "admin" | "leader" };

export type AccountRow = {
  id: string;
  name: string;
  domains: string[];
  owner: string | null;
  status: "active" | "churned";
  renewal_date: string | null;
  days_to_renewal: number | null;
  arr: number | null;
  seats_purchased: number | null;
  last_activity_at: string | null;
  emails_30d: number;
  next_meeting_at: string | null;
  active_users_7d: number | null;
};

export type TimelineEvent =
  | {
      kind: "email" | "meeting" | "note" | "task" | "call";
      id: string;
      at: string;
      known_at: string;
      scheduled: boolean;
      cancelled: boolean;
      direction: string;
      source: string;
      subject: string | null;
      snippet: string | null;
      from: string | null;
      participants: string[];
      people: string[];
      auto_reply: boolean;
      bounce: boolean;
      injection_flag: boolean;
      match: { method: string; confidence: number };
    }
  | {
      kind: "crm_change";
      at: string;
      changes: { entity: string; entity_id: string; label: string; field: string; value: unknown }[];
    };

export type Timeline = {
  as_of: string;
  exists: boolean;
  account: {
    id: string;
    name: string;
    domains?: string[];
    owner?: string | null;
    renewal_date?: string | null;
    days_to_renewal?: number | null;
    arr?: number | null;
    seats_purchased?: number | null;
  };
  sources?: string[];
  contacts?: { id: string; name: string; email: string; title: string | null; role: string; status: string; origin: string }[];
  deals?: {
    id: string;
    name: string;
    type: string;
    stage: string | null;
    closed: boolean;
    won: boolean;
    amount: number | null;
    close_date: string | null;
    next_step: string;
    next_step_since: string | null;
  }[];
  usage_weekly?: { week: string; active_users_avg: number; key_actions: number; seats_used: number }[];
  events?: TimelineEvent[];
  event_count?: number;
};

export type ConnectionRow = {
  id: string;
  kind: string;
  name: string;
  zone: string;
  status: string;
  interval_minutes: number;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  consecutive_failures: number;
  raw_records: number;
  freshness: { state: "fresh" | "stale" | "never_synced"; stale_since: string | null };
  last_run: { status: string; started_at: string; finished_at: string | null; fetched: number; new: number; error: string | null } | null;
};

export type Unmatched = {
  activities_total: number;
  activities: {
    id: string;
    type: string;
    at: string;
    subject: string | null;
    from: string | null;
    participants: string[];
    external: string[];
    method: string;
    candidates: { id: string; name: string }[];
  }[];
  usage_keys: { key: string; rows: number; last_date: string }[];
};
