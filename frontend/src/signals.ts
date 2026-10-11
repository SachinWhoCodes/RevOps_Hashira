import {
  CalendarClock,
  CalendarX,
  CircleArrowUp,
  Crown,
  HeartCrack,
  ListX,
  MessageSquareOff,
  TrendingDown,
  User,
  UserX,
  Users,
  UsersRound,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { SignalHit } from "./api";

// Short chip text per signal, built only from the signal's computed facts.
export function chipText(s: SignalHit): string {
  const f = s.facts as Record<string, number | string | null>;
  switch (s.type) {
    case "meeting_gap":
      return `No meeting ${f.days_since_last_meeting}d`;
    case "champion_silence":
      return `Champion silent · ${f.unanswered_emails} emails`;
    case "champion_departure":
      return "Champion may have left";
    case "usage_decline":
      return f.decline_kind === "gradual"
        ? `Usage ${f.active_users_change_vs_baseline_pct}% vs quarter`
        : `Usage ${Math.min(Number(f.active_users_change_pct), Number(f.key_actions_change_pct))}%`;
    case "missing_next_step":
      return f.reason === "no_renewal_deal" ? "No renewal deal" : f.reason === "stale" ? "Next step stale" : "No next step";
    case "renewal_proximity":
      return f.overdue ? "Renewal overdue" : `Renews in ${f.days_to_renewal}d`;
    case "single_threaded":
      return `Single-threaded · ${f.person_name}`;
    case "executive_engagement":
      return `Buyer met ${f.days_since_meeting}d ago`;
    case "seat_saturation":
      return `Seats ${f.seat_utilisation_pct}% used`;
    case "new_team_adoption":
      return `New team: ${f.department}`;
    default:
      return s.type.replace(/_/g, " ");
  }
}

export const polarityTone = { risk: "risk", opportunity: "ok", urgency: "warn", positive: "accent" } as const;

export const signalIcon: Record<string, LucideIcon> = {
  meeting_gap: CalendarX,
  champion_silence: MessageSquareOff,
  champion_departure: UserX,
  usage_decline: TrendingDown,
  missing_next_step: ListX,
  renewal_proximity: CalendarClock,
  single_threaded: User,
  executive_engagement: Crown,
  seat_saturation: Users,
  new_team_adoption: UsersRound,
  negative_tone: HeartCrack,
  upgrade_interest: CircleArrowUp,
};

export const iconFor = (type: string): LucideIcon => signalIcon[type] ?? Zap;

export const signalName: Record<string, string> = {
  meeting_gap: "Meeting gap",
  champion_silence: "Champion silence",
  champion_departure: "Champion departure",
  usage_decline: "Usage decline",
  missing_next_step: "Missing next step",
  renewal_proximity: "Renewal soon",
  single_threaded: "Single-threaded",
  executive_engagement: "Buyer engaged",
  seat_saturation: "Seat saturation",
  new_team_adoption: "New team adopting",
  negative_tone: "Negative tone",
  upgrade_interest: "Upgrade interest",
};

export const nameFor = (type: string) => signalName[type] ?? type.replace(/_/g, " ");

// Signals that lower risk when they fire (a lever that "adds" one is good news, not a new risk).
export const positiveSignals = new Set(["executive_engagement"]);
