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
      via?: string | null;
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
  relationships?: RelationshipMap;
  outcomes?: PlayOutcomeView[];
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
  levers?: Levers;
};

// "What would lower this risk?" — each change is applied to a copy of the account and re-scored
// with the live rules (backend casefile/cases/levers.py). Not a forecast.
export type Lever = {
  key: string;
  text: string;
  detail: string;
  clears: string[];
  adds: string[];
  risk_after: number;
  change: number;
  plays: string[];
  in_plan: boolean;
  direct_action: string | null;
  done_by_plan: boolean;
};

export type LeverExpiry = { signal: string; points: number; stops_counting_on: string | null; risk_after: number; text: string };

export type Levers = {
  version: string;
  rules: { scoring: number; thresholds: number; playbooks: string | null };
  risk_now: number;
  close_below: number;
  open_at: number;
  levers: Lever[];
  expiries: LeverExpiry[];
  to_close: { needed: boolean; possible: boolean; levers: string[]; risk_after: number };
  plan_play: string | null;
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
  approval_id: string | null;
  model: string | null;
  prompt_version: string | null;
  hash: string;
  prev_hash: string | null;
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

// ---------------------------------------------------------------- Phase 4: change sets and approvals

export type ItemStatus =
  | "proposed" | "approved" | "edited_approved" | "rejected" | "queued" | "executing"
  | "succeeded" | "failed" | "conflict" | "would_execute" | "blocked" | "superseded";

export type ChangeItem = {
  id: string;
  seq: number;
  action_type: "crm_task" | "crm_note" | "crm_next_step" | "gmail_draft" | "slack_dm" | "review_manually" | string;
  label: string;
  tier: number;
  target_system: "hubspot" | "gmail" | "slack" | "casefile" | string;
  target_ref: Record<string, unknown>;
  before: Record<string, unknown>;
  proposed: Record<string, unknown>;
  final: Record<string, unknown> | null;
  editable: string[];
  field_update: boolean;
  status: ItemStatus;
  blocked_reason: string | null;
  result: Record<string, unknown>;
  why: { text: string; evidence: ClaimEvidence[] }[];
  evidence_count: number;
  payload_hash: string | null;
  links: Record<string, string>;
  approvals: { decision: "approve" | "reject"; by: string; edited: boolean; at: string; comment: string | null; hash: string; channel: string }[];
  attempts: { no: number; status: string; started_at: string; finished_at: string | null; error: string | null; external_id: string | null }[];
};

export type ChangeSet = {
  id: string;
  case_id: string;
  case_kind: "risk" | "expansion";
  account: { id: string; name: string };
  play: string;
  playbook_version: string;
  status: string;
  assessment_id: string | null;
  notes: { purpose?: string; skipped?: { type: string; reason: string }[]; draft_source?: string; draft_reasons?: string[]; draft_cost_usd?: number };
  created_at: string;
  can_decide: boolean;
  items: ChangeItem[];
};

export type InboxRow = {
  id: string;
  case_id: string;
  case_kind: "risk" | "expansion";
  account: { id: string; name: string };
  headline: string | null;
  score: number | null;
  play: string;
  status: string;
  counts: Partial<Record<ItemStatus, number>>;
  created_at: string;
};

export type ExecutorStatus = {
  running: boolean;
  last_heartbeat: string | null;
  shadow: boolean;
  targets: { kind: string; name: string; config: Record<string, unknown>; writer_loaded: boolean }[];
};

// ---------------------------------------------------------------- Phase 5: back-test

export type Metric = { value: number | null; ci95: [number, number] | null; n: number; [k: string]: unknown };

export type HorizonBlock = {
  n: number;
  positives?: number;
  prevalence?: number;
  auc?: Metric;
  precision?: Metric;
  recall?: Metric;
  precision_at_10?: Metric;
  flag_at?: number;
  best_single_signal?: { signal: string; auc: Metric; precision: Metric; recall: Metric };
  health_score?: { auc: Metric } | string;
  renewal_date_only?: { auc: Metric; note: string };
};

export type BacktestRunView = {
  id: string;
  started_at: string;
  label: "observed" | "simulated" | "estimated" | "untested";
  engine_version: string;
  config: { excluded_signals: string[]; label_coverage: number; horizons: number[]; min_history_days: number };
  metrics: {
    outcomes: Record<string, number>;
    positive_definition: string[];
    events: { total: number; scored: number; tuning: number; held_out: number };
    split_date: string;
    skipped: Record<string, number>;
    flag_threshold: { default: number; tuned_on_tuning_period: number; objective: string };
    best_single_signal: { name: string | null; chosen_on: string };
    horizon: Record<string, Record<"all" | "tuning_period" | "held_out" | "held_out_tuned_threshold", HorizonBlock>>;
    rolling: { weeks: number; precision_at_10: Metric; baseline_nearest_renewal_first: Metric; baseline_best_single_signal: Metric };
    lead_time: {
      positives: number;
      flagged_before_outcome: Metric;
      median_lead_days: Metric;
      share_flagged_30_days_ahead: Metric;
      missed: { account_id: string; renewal_date: string; outcome: string }[];
    };
    false_alarms: { negatives: number; flagged_in_last_90_days: Metric };
    headline_ok: boolean;
    fallback: string | null;
  };
};

export type OutcomeRow = {
  id: string;
  account: { id: string; name: string };
  renewal_date: string;
  outcome: "renewed" | "downgraded" | "churned" | "late";
  positive: boolean;
  label_source: "derived" | "human";
  arr_before: number | null;
  arr_after: number | null;
  risk_before: Record<string, number>;
  missed: boolean;
};

export type Replay = {
  account: { id: string; name: string };
  flag_at: number;
  excluded_signals: string[];
  points: { date: string; risk: number; opportunity: number; flagged: boolean; signals: string[] }[];
  outcomes: { renewal_date: string; outcome: string; label_source: string }[];
};

export type ReplaySaid = {
  as_of: string;
  risk: number;
  opportunity: number;
  headline: string;
  claims: Claim[];
  source: string;
};

// Relationship map (Phase 6.3): who at the customer we hear from, computed as of the viewed date.
export type RelationshipPerson = {
  id: string;
  name: string;
  email: string;
  title: string | null;
  role: string;
  status: string;
  origin: string;
  emails_from: number;
  emails_to: number;
  meetings: number;
  last_heard_from: string | null;
  days_since_heard: number | null;
  last_contacted: string | null;
  next_meeting: string | null;
  active: boolean;
};

export type RelationshipMap = {
  window_days: number;
  active_days: number;
  active_people: number;
  known_people: number;
  people: RelationshipPerson[];
};

// Outcome tracker (Phase 6.1): what happened after an executed play. "Measured after", not cause.
export type OutcomeState = { at: string; risk: number; opportunity: number; signals: string[]; active_people: number };
export type OutcomeEvents = {
  champion_replied: boolean;
  buyer_engaged: boolean;
  meeting_booked: boolean;
  meeting_held: boolean;
  next_step_set: boolean;
  usage_change_pct: number | null;
  customer_emails: number;
};
export type OutcomeMeasure = {
  before: OutcomeState;
  after: OutcomeState & { events: OutcomeEvents; cleared: string[]; new: string[] };
  result: "improved" | "no_change" | "worse";
};
export type PlayOutcomeView = {
  change_set_id: string;
  play: string;
  case_kind: string;
  executed_at: string;
  days_since: number;
  checkpoints: {
    day: number;
    due: string;
    result: "improved" | "no_change" | "worse" | "pending";
    before: OutcomeState | null;
    after: (OutcomeState & { events: OutcomeEvents; cleared: string[]; new: string[] }) | null;
  }[];
  so_far: OutcomeMeasure | null;
};
export type PlaysTable = {
  check_days: number[];
  plays: {
    play: string;
    sets: number;
    checkpoints: {
      day: number;
      measured: number;
      improved: number;
      no_change: number;
      worse: number;
      pending: number;
      median_risk_change: number | null;
      champion_replied: number;
      meeting_booked: number;
    }[];
  }[];
};

// Ask about an account (Phase 6.4): every returned sentence passed the claim checker.
export type QaEvidence = { eid: string; kind: Evidence["kind"]; id: string; label: string; at: string | null };
export type QaAnswer = {
  question: string;
  ok: boolean;
  sentences: { text: string; evidence: QaEvidence[]; fact_keys: string[] }[];
  removed: { text: string; reasons: string[] }[];
  gaps: string;
  error: string | null;
  tools_used: string[];
  iterations: number;
  cost_usd: number;
  cached: boolean;
  model: string | null;
  prompt_version: string | null;
};
