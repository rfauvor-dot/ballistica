"""Predictive usage-pace projection for the (not-yet-built) subscription
tiers described in Addendum 20.

Deliberately standalone and dependency-free: it's pure math over numbers a
caller supplies (turns used, a billing cycle, an allotment) rather than
anything that reads real per-user data, because there's no accounts/billing
system in this codebase yet to read that data from -- see api.py's module
docstring ("single-user local tool ... not multi-tenant SaaS"). This module
is the one piece of Addendum 20 that doesn't depend on that gap: once real
per-user turn counts exist, wiring them into project_usage() is a small
integration, not a rewrite. The web-UI nudge banner, the top-up purchase
flow, and the actual turn-counting itself all still need that groundwork
built first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math

# A pace-based projection that only fires once the cycle is already blown is
# useless -- it should catch a user trending over before they hit the wall.
# Checked in ADDITION to the pace projection (not instead of it) so a user
# who front-loaded heavy usage early in the cycle gets flagged even if their
# pace would technically taper off before the projected total crosses the
# allotment.
_RAW_USAGE_NUDGE_THRESHOLD = 0.8


@dataclass
class UsageProjection:
    turns_used: int
    days_elapsed: int
    days_remaining: int
    daily_pace: float
    projected_total: float
    projected_overage: float
    should_nudge: bool
    top_ups_recommended: int
    top_up_cost: float
    upgrade_recommended: bool
    message: str


def project_usage(
    turns_used: int,
    cycle_start: date,
    cycle_end: date,
    today: date,
    included_allotment: int,
    top_up_turns: int,
    top_up_price: float,
    tier_upgrade_price_delta: float | None = None,
    repeated_topup_months: int = 0,
) -> UsageProjection:
    """Projects a user's usage to the end of their billing cycle from their
    actual pace so far, and recommends a top-up or a tier upgrade sized to
    that projection -- not a guess, and not just a raw "you're at 80%"
    meter.

    tier_upgrade_price_delta: how much more the next tier up costs per
        month than the current one. When omitted, upgrade_recommended is
        only ever driven by repeated_topup_months (there's no price to
        compare the top-up cost against).
    repeated_topup_months: how many of the last few billing cycles also
        needed at least one top-up. Requires history this module doesn't
        track itself -- the caller supplies it once that history exists.
        Defaults to 0 (unknown/not yet tracked), which just means this
        signal doesn't contribute to the recommendation yet.
    """
    if included_allotment < 0:
        raise ValueError("included_allotment must not be negative")
    if top_up_turns <= 0:
        raise ValueError("top_up_turns must be positive")

    # Day 1 of a cycle has zero elapsed days by calendar math, which would
    # divide by zero -- clamp to 1 so a brand-new cycle still produces a
    # (necessarily rough) pace instead of crashing.
    days_elapsed = max(1, (today - cycle_start).days)
    days_remaining = max(0, (cycle_end - today).days)

    daily_pace = turns_used / days_elapsed
    projected_total = turns_used + daily_pace * days_remaining
    projected_overage = max(0.0, projected_total - included_allotment)

    raw_fraction = turns_used / included_allotment if included_allotment else 0.0
    should_nudge = projected_overage > 0 or raw_fraction >= _RAW_USAGE_NUDGE_THRESHOLD

    top_ups_recommended = math.ceil(projected_overage / top_up_turns) if projected_overage > 0 else 0
    top_up_cost = top_ups_recommended * top_up_price

    upgrade_recommended = repeated_topup_months >= 2 or (
        tier_upgrade_price_delta is not None
        and top_up_cost > 0
        and top_up_cost > tier_upgrade_price_delta
    )

    message = _build_message(
        should_nudge=should_nudge, daily_pace=daily_pace, projected_total=projected_total,
        projected_overage=projected_overage, top_ups_recommended=top_ups_recommended,
        top_up_cost=top_up_cost, upgrade_recommended=upgrade_recommended,
        repeated_topup_months=repeated_topup_months,
    )

    return UsageProjection(
        turns_used=turns_used, days_elapsed=days_elapsed, days_remaining=days_remaining,
        daily_pace=daily_pace, projected_total=projected_total, projected_overage=projected_overage,
        should_nudge=should_nudge, top_ups_recommended=top_ups_recommended, top_up_cost=top_up_cost,
        upgrade_recommended=upgrade_recommended, message=message,
    )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _build_message(
    *, should_nudge: bool, daily_pace: float, projected_total: float, projected_overage: float,
    top_ups_recommended: int, top_up_cost: float, upgrade_recommended: bool,
    repeated_topup_months: int,
) -> str:
    if not should_nudge:
        return "On pace to stay within your plan this month."

    pace_clause = (
        f"At your current pace (about {daily_pace:.1f} turns/day), you're on track for "
        f"roughly {projected_total:.0f} turns this month."
    )
    if projected_overage <= 0:
        # Raw usage crossed the threshold but the pace projection hasn't --
        # early heavy use rather than a sustained trend. Say so plainly
        # rather than recommending a top-up size math doesn't support yet.
        return pace_clause + " You're using a large share of your plan early, worth keeping an eye on."

    overage_clause = f" That's about {projected_overage:.0f} more than your plan includes."

    if upgrade_recommended and repeated_topup_months >= 2:
        return (
            pace_clause + overage_clause
            + f" This is the {_ordinal(repeated_topup_months + 1)} month running you've needed extra --"
              " upgrading would cover this without buying a top-up every month."
        )
    if upgrade_recommended:
        return (
            pace_clause + overage_clause
            + f" {top_ups_recommended} top-up{'s' if top_ups_recommended != 1 else ''} "
              f"(${top_up_cost:.2f}) would cover it, but upgrading works out cheaper this month."
        )
    return (
        pace_clause + overage_clause
        + f" {top_ups_recommended} top-up{'s' if top_ups_recommended != 1 else ''} "
          f"(${top_up_cost:.2f}) would finish out the month without interruption."
    )
