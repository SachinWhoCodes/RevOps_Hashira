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
