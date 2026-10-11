"""Generated product-usage data for a rehearsal organisation (a hosting company's control panel).

The CRM and mailbox of the rehearsal organisation are real; its product usage is not available, so this
writes a realistic CSV (the upload format of ``casefile.connectors.usage``) keyed by the organisation's
own CRM domains. Each company gets one usage story, chosen so every usage rule has a case that fires
and a look-alike that must not:

- fires: a sudden drop, a slow slide, deployments collapsing while logins hold, a customer going dark,
  a new team adopting the product, seats nearly full (needs ``seats_purchased`` in the CRM);
- must not fire: steady, growing, a holiday dip that recovered, spiky-but-flat, a tiny account
  shrinking (below the minimum), a new customer still ramping up (no baseline yet);
- data gaps: one company with no usage at all (a blind spot) and one product key that matches no CRM
  company (shows under Unmatched records).

Generated numbers, not measurements: never present them as a customer's real usage.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# Interesting stories first: they go to companies that are customers (have a deal).
STORY_ORDER = [
    "sudden_drop",
    "slow_slide",
    "new_team",
    "deploys_collapse",
    "went_dark",
    "seats_nearly_full",
    "growing",
    "holiday_dip_recovered",
    "steady",
    "spiky_but_flat",
    "new_customer_ramp",
    "tiny_account",
    "no_usage_data",
]
STORY_TEXT = {
    "sudden_drop": "active users fell by about half in the last four weeks (should flag: sudden decline)",
    "slow_slide": "logins sliding about 3% a week for half a year (should flag: slow decline)",
    "new_team": "a Marketing team started using the panel 3 weeks ago (should flag: new team, opportunity)",
    "deploys_collapse": "people still log in, but deployments fell ~65% (should flag: key actions down)",
    "went_dark": "no activity at all for the last two weeks (should flag: sudden decline)",
    "seats_nearly_full": "~96% of plan seats in use (flags only if the CRM has 'seats_purchased')",
    "growing": "steady growth, ~40% up over the year (should stay quiet)",
    "holiday_dip_recovered": "a two-week dip around Diwali, fully recovered (should stay quiet)",
    "steady": "flat, healthy usage with a weekly rhythm (should stay quiet)",
    "spiky_but_flat": "noisy day to day, flat on average (should stay quiet)",
    "new_customer_ramp": "started 45 days ago and ramping up (no baseline yet: should stay quiet)",
    "tiny_account": "two users, one stopped (below the minimum size: should stay quiet)",
    "no_usage_data": "no usage rows at all (shows as a blind spot)",
}
UNMATCHED_KEY = "brightpixel.studio"  # in the product, missing from the CRM


@dataclass(frozen=True)
class Company:
    name: str
    key: str  # what the product knows the customer by: its domain
    customer: bool


def _diwali_dip(day: date) -> float:
    start = date(2025, 10, 18)
    return 0.6 if start <= day < start + timedelta(days=14) else 1.0


def _year_end(day: date) -> float:
    return 0.75 if (day.month == 12 and day.day >= 24) or (day.month == 1 and day.day <= 2) else 1.0


def _curve(story: str, d: int, total: int) -> tuple[float, float]:
    """(users multiplier, deployments multiplier) d days before today (0 = yesterday)."""
    if story == "sudden_drop":
        return (0.45, 0.5) if d < 26 else (1.0, 1.0)
    if story == "slow_slide":
        m = 1.0 if d > 200 else 0.3 + 0.0035 * d
        return m, m
    if story == "deploys_collapse":
        return 1.0, (0.35 if d < 26 else 1.0)
    if story == "went_dark":
        return (0.0, 0.0) if d < 14 else (1.0, 1.0)
    if story == "growing":
        m = 1.0 + 0.4 * (1 - d / total)
        return m, m
    if story == "tiny_account":
        return (0.5, 0.5) if d < 30 else (1.0, 1.0)
    if story == "new_customer_ramp":
        m = min(1.0, 0.25 + (45 - d) / 40)
        return m, m
    return 1.0, 1.0


def generate(
    companies: list[Company], today: date, *, days: int = 400, seed: int = 2026
) -> tuple[list[dict[str, object]], dict[str, str]]:
    rng = random.Random(seed)
    customers = [c for c in companies if c.customer]
    others = [c for c in companies if not c.customer]
    stories: dict[str, str] = {}
    for i, c in enumerate(customers + others):
        stories[c.key] = STORY_ORDER[i % len(STORY_ORDER)] if i < len(STORY_ORDER) else "steady"
    rows: list[dict[str, object]] = []
    for c in companies:
        story = stories[c.key]
        if story == "no_usage_data":
            continue
        size = 2 if story == "tiny_account" else rng.choice([12, 18, 24, 35, 48, 64])
        seats = max(size + 2, round(size * 1.25 / 5) * 5)
        span = 45 if story == "new_customer_ramp" else days
        per_user = rng.uniform(4.0, 9.0)  # deployments, config changes and backups per active user
        for d in range(span, 0, -1):
            day = today - timedelta(days=d)
            rhythm = 0.35 if day.weekday() >= 5 else 1.0 + 0.06 * math.sin(day.weekday())
            season = _diwali_dip(day) if story == "holiday_dip_recovered" else 1.0
            season *= _year_end(day)
            mu, ma = _curve(story, d - 1, span)
            noise = rng.gauss(1.0, 0.35 if story == "spiky_but_flat" else 0.08)
            users = max(0, round(size * rhythm * season * mu * noise))
            if story == "tiny_account":
                users = (1 if d - 1 < 30 else 2) if day.weekday() < 5 else rng.choice([0, 1])
            # Deployments follow the account's own size and rhythm with their own multiplier, so a story
            # that changes logins and deployments together moves both by the same amount (not twice).
            if story == "tiny_account":
                actions = max(0, round(users * per_user * rng.gauss(1.0, 0.12)))
            else:
                actions = max(0, round(size * rhythm * season * ma * per_user * noise * rng.gauss(1.0, 0.12)))
            if story == "seats_nearly_full":
                seats_used = round(seats * (0.96 if d <= 30 else 0.8))
            else:
                seats_used = min(seats, max(users, round(size * 1.05)))
            depts = {"Engineering": round(users * 0.6), "DevOps": round(users * 0.25)}
            depts["Support"] = max(0, users - depts["Engineering"] - depts["DevOps"])
            if story == "new_team" and d <= 21 and day.weekday() < 5:
                marketing = rng.choice([3, 4, 4, 5])
                depts["Marketing"] = marketing
                users += marketing
            rows.append(
                {
                    "account_key": c.key,
                    "date": day.isoformat(),
                    "active_users": users,
                    "key_actions": actions,
                    "seats_used": seats_used,
                    "departments": json.dumps({k: v for k, v in depts.items() if v}),
                }
            )
    # A paying customer of the product that nobody entered in the CRM.
    for d in range(days, 0, -1):
        day = today - timedelta(days=d)
        users = max(0, round(9 * (0.35 if day.weekday() >= 5 else 1.0) * rng.gauss(1.0, 0.08)))
        rows.append(
            {
                "account_key": UNMATCHED_KEY,
                "date": day.isoformat(),
                "active_users": users,
                "key_actions": round(users * 5.5),
                "seats_used": 10,
                "departments": json.dumps({"Engineering": users} if users else {}),
            }
        )
    rows.sort(key=lambda r: (str(r["date"]), str(r["account_key"])))
    return rows, stories


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["account_key", "date", "active_users", "key_actions", "seats_used", "departments"]
        )
        writer.writeheader()
        writer.writerows(rows)
