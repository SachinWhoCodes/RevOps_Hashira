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
      injection_reason: string | null;
      injection_quote: string | null;
      labels: AiLabel[];
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
  assessment?: LiveAssessment;
  explanation?: Explanation | null;
  flagged_emails?: { id: string; at: string; subject: string | null; reason: string | null; quote: string | null }[];
  cases?: { id: string; kind: string; status: string; opened_at: string; resolved_at: string | null; feedback: string | null }[];
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

// ---------------------------------------------------------------- Phase 2: signals, scores, cases

export type Evidence = { kind: "activity" | "usage" | "field" | "contact" | "deal"; id: string; label: string; at: string | null };

export type SignalHit = {
  type: string;
  polarity: "risk" | "opportunity" | "urgency" | "positive";
  value: number;
  summary: string;
  facts: Record<string, unknown>;
  evidence: Evidence[];
};

export type ScoreLine = { signal: string; points: number; summary: string };
export type BlindSpot = { code: string; text: string };

export type QueueItem = {
  rank: number;
  case_id: string;
  opened_at: string;
  account: { id: string; name: string; domains: string[] };
  owner: string | null;
  risk: number;
  opportunity: number;
  confidence: "high" | "medium" | "low";
  sources_missing: string[];
  priority: number;
  opportunity_priority: number;
  arr: number | null;
  arr_assumed: boolean;
  days_to_renewal: number | null;
  urgency_factor: number;
  risk_lines: ScoreLine[];
  opportunity_lines: ScoreLine[];
  signals: SignalHit[];
  blind_spots: BlindSpot[];
  explanation_status: string;
  headline: string | null;
  claims: Claim[];
  removed_by_checker: number;
};

export type Queue = { kind: string; items: QueueItem[]; revenue_at_stake: number; owners: { id: string; name: string }[] };

export type LiveAssessment = {
  computed_for: string;
  churned: boolean;
  risk: { lines: ScoreLine[]; raw: number; score: number };
  opportunity: { lines: ScoreLine[]; score: number };
  urgency: { days_to_renewal: number | null; factor: number };
  arr: { value: number; assumed: boolean };
  confidence: { level: string; sources_present: string[]; sources_missing: string[] };
  priority: number;
  signals: SignalHit[];
  blind_spots: BlindSpot[];
};

export type BlindSpots = {
  accounts_assessed: number;
  by_code: Record<string, { count: number; text: string; accounts: { id: string; name: string }[] }>;
  unmatched_records: number;
  ambiguous_records: number;
  unmatched_usage_keys: number;
  connections_not_fresh: { kind: string; name: string; status: string; last_error: string | null }[];
};

export type AuditRow = {
  seq: number;
  ts: string;
  actor: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: Record<string, unknown>;
  evidence_count: number;
  hash: string;
};

// ---------------------------------------------------------------- Phase 3: AI layer

export type AiLabel = { label: string; status: "accepted" | "possible" | "quarantined"; confidence: number; quote: string };

export type ClaimEvidence = { eid: string; kind: Evidence["kind"]; id: string; label: string; at: string | null };
export type Claim = { text: string; evidence: ClaimEvidence[]; fact_keys: string[] };

export type Explanation = {
  assessment_id: string;
  status: "verified" | "fallback" | "pending";
  headline: string | null;
  claims: Claim[];
  blind_spot_note: string;
  model: string | null;
  prompt_version: string | null;
  removed_by_checker: number;
  stale_removed: number;
  attempts: number;
};

export type EvidenceRecord = {
  id: string;
  type: string;
  direction: string;
  source: string;
  at: string;
  subject: string | null;
  from: string | null;
  to: string[];
  cc: string[];
  body: string | null;
  body_note: string | null;
  cancelled: boolean;
  hidden_text_present: boolean;
  injection: { flagged: boolean; reason: string | null; quote: string | null };
  labels: (AiLabel & { model: string })[];
  match: { method: string; confidence: number };
};

export type AiUsage = {
  enabled: boolean;
  budget_usd_per_day: number;
  spent_today_usd: number;
  by_purpose: { purpose: string; model: string; calls: number; cost_usd: number; avg_cost_usd: number; failed: number }[];
};
