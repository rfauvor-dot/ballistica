"""Tests for the predictive usage-pace projection (Addendum 20). Pure
math over caller-supplied numbers -- no accounts/billing system exists
yet for this to read real data from, so these tests exercise the
function directly rather than through any API surface."""
from datetime import date

import pytest

from ballistica.usage import project_usage


def test_on_pace_user_gets_no_nudge():
    """Well within the allotment, pace projection stays under it too --
    no nudge, no top-up/upgrade recommendation."""
    result = project_usage(
        turns_used=100, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 15), included_allotment=1000, top_up_turns=500, top_up_price=15,
    )
    assert result.should_nudge is False
    assert result.top_ups_recommended == 0
    assert result.upgrade_recommended is False
    assert "on pace" in result.message.lower()


def test_trending_over_recommends_a_sized_topup():
    """Halfway through the cycle already past the allotment at the
    current pace -- should nudge and size a top-up from the actual
    projected overage, not a guess."""
    result = project_usage(
        turns_used=700, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 15), included_allotment=1000, top_up_turns=500, top_up_price=15,
        tier_upgrade_price_delta=40,
    )
    # Pace: 700 turns / 14 elapsed days = 50/day; 16 days remain ->
    # projected total = 700 + 50*16 = 1500 -> 500 over the 1000 allotment.
    assert result.should_nudge is True
    assert result.projected_overage == pytest.approx(500, abs=1)
    assert result.top_ups_recommended == 1
    assert result.top_up_cost == pytest.approx(15)
    assert result.upgrade_recommended is False
    assert "1 top-up" in result.message
    assert "$15.00" in result.message


def test_far_over_recommends_upgrade_when_topups_would_cost_more():
    """When enough top-ups to cover the projected overage would already
    cost more than upgrading a tier, recommend the upgrade instead --
    matches Rick's "not repeatedly buying top-ups" intent."""
    result = project_usage(
        turns_used=4000, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 15), included_allotment=1000, top_up_turns=500, top_up_price=15,
        tier_upgrade_price_delta=40,
    )
    # Pace: 4000/14 ~ 285.7/day * 16 remaining days + 4000 ~ 8571 projected,
    # ~7571 over allotment -> 16 top-ups -> $240, well past the $40 delta.
    assert result.should_nudge is True
    assert result.top_ups_recommended > 1
    assert result.top_up_cost > 40
    assert result.upgrade_recommended is True
    assert "upgrading" in result.message.lower()


def test_repeated_topup_months_recommends_upgrade_regardless_of_single_month_cost():
    """A recurring pattern (needed a top-up 2+ months running) should
    recommend upgrading even if this month's single top-up would
    technically be the cheaper choice in isolation."""
    result = project_usage(
        turns_used=700, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 15), included_allotment=1000, top_up_turns=500, top_up_price=15,
        tier_upgrade_price_delta=999,  # deliberately huge -- a single top-up is far cheaper
        repeated_topup_months=2,
    )
    assert result.upgrade_recommended is True
    assert "3rd month running" in result.message  # regression: used to render "3th"


def test_day_one_of_cycle_does_not_divide_by_zero():
    """today == cycle_start means zero elapsed days by calendar math --
    must clamp rather than raise."""
    result = project_usage(
        turns_used=50, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 1), included_allotment=1000, top_up_turns=500, top_up_price=15,
    )
    assert result.days_elapsed == 1
    assert result.daily_pace == pytest.approx(50)


def test_zero_usage_produces_zero_pace_and_no_nudge():
    result = project_usage(
        turns_used=0, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 15), included_allotment=1000, top_up_turns=500, top_up_price=15,
    )
    assert result.daily_pace == 0
    assert result.should_nudge is False


def test_early_heavy_usage_flagged_even_without_a_sustained_pace_projection():
    """On the last day of the cycle, there's no room left to extrapolate
    (days_remaining=0), so the pace projection alone would miss a user
    who front-loaded heavy usage early -- the raw-usage-fraction check
    must catch this independently."""
    result = project_usage(
        turns_used=850, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
        today=date(2026, 8, 31), included_allotment=1000, top_up_turns=500, top_up_price=15,
    )
    assert result.days_remaining == 0
    assert result.projected_overage == 0  # nothing left to extrapolate
    assert result.should_nudge is True  # but 850/1000 = 85% crossed the raw threshold
    assert result.top_ups_recommended == 0  # no overage to size a top-up from
    assert "early" in result.message.lower()


def test_negative_allotment_raises():
    with pytest.raises(ValueError):
        project_usage(
            turns_used=10, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
            today=date(2026, 8, 15), included_allotment=-1, top_up_turns=500, top_up_price=15,
        )


def test_zero_topup_turns_raises():
    with pytest.raises(ValueError):
        project_usage(
            turns_used=10, cycle_start=date(2026, 8, 1), cycle_end=date(2026, 8, 31),
            today=date(2026, 8, 15), included_allotment=1000, top_up_turns=0, top_up_price=15,
        )
