"""Tests for ballistica/bullet_reference.py -- the bundled bullet
reference dataset (2026-08-30). No network dependency: operates
entirely on the already-built data/bullet_reference/bullet_reference.json.
"""
from __future__ import annotations

from ballistica.bullet_reference import BULLET_REFERENCE, search


def test_loads_a_substantial_dataset():
    # Real count as of the pinned source commit is 822 -- a loose bound
    # here so this doesn't need updating for every unrelated change,
    # while still catching "the file failed to load" (0) or "something
    # is very wrong" (a handful).
    assert len(BULLET_REFERENCE) > 700


def test_every_entry_has_a_usable_bc_and_drag_model():
    for b in BULLET_REFERENCE:
        assert b.bc > 0
        assert b.drag_model in ("G1", "G7")
        assert b.bullet_weight_gr > 0


def test_sierra_77gr_matchking_correctly_excluded_not_guessed():
    """The exact bullet Rick's real rifle uses. Confirmed live (this
    session) that ammolytics' own Sierra data only publishes this
    bullet's BC as a velocity-banded structure, not a clean single G1/
    G7 value -- so it must NOT appear here. If it ever does, something
    started guessing/parsing that malformed field instead of skipping
    it honestly."""
    matches = [b for b in BULLET_REFERENCE if b.company == "Sierra" and "77gr MatchKing" in b.bullet_type
               and "Tipped" not in b.bullet_type]
    assert matches == []


def test_all_sierra_entries_excluded():
    """Every Sierra row in the source hits the same velocity-banded-BC
    issue -- not just the 77gr MatchKing. Confirmed via the build
    report; this pins that finding as a real test, not just a one-off
    observation in a chat message."""
    assert not any(b.company == "Sierra" for b in BULLET_REFERENCE)


def test_prefers_g7_when_both_available():
    berger_vld = [b for b in BULLET_REFERENCE if b.company == "Berger" and "70gr VLD Target" in b.bullet_type]
    assert len(berger_vld) == 1
    assert berger_vld[0].drag_model == "G7"
    assert berger_vld[0].bc == 0.191  # matches the source's own bc_g7 column, not bc_g1's 0.374


def test_provenance_recorded_on_every_entry():
    for b in BULLET_REFERENCE:
        assert b.source == "ammolytics/projectiles"
        assert len(b.source_commit) == 40  # a real git SHA, not a placeholder


def test_search_matches_bullet_type_and_company():
    results = search("berger")
    assert results
    assert all(b.company == "Berger" for b in results)


def test_search_empty_query_returns_nothing():
    assert search("") == []
    assert search("   ") == []
