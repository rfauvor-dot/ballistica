"""Tests for the waiver.py v2 amendment (2026-08-30): the new Section 4
disclosing automatic aggregate-data use, and the updated acceptance
mechanism/acknowledgment text covering it. Not testing the pre-existing
attorney-approved sections' content -- those are unchanged verbatim
text, not something to assert on here.
"""
from __future__ import annotations

from ballistica.waiver import (
    WAIVER_ACKNOWLEDGMENT_TEXT, WAIVER_SECTIONS, WAIVER_TEXT_SHA256, WAIVER_VERSION,
    waiver_canonical_text,
)


def test_version_bumped_from_v1():
    assert WAIVER_VERSION != "2026-08-28-v1"
    assert WAIVER_VERSION == "2026-08-30-v2"


def test_sections_numbered_1_through_12_with_no_gaps_or_duplicates():
    numbers = [int(s.heading.split(".")[0]) for s in WAIVER_SECTIONS]
    assert numbers == list(range(1, len(WAIVER_SECTIONS) + 1))


def test_data_use_section_present_and_discloses_the_real_terms():
    matches = [s for s in WAIVER_SECTIONS if "How Your Ballistic Data Is Used" in s.heading]
    assert len(matches) == 1
    text = " ".join(matches[0].paragraphs)
    assert "anonymized" in text
    assert "not optional" in text or "non-optional" in text
    assert "cannot be traced back to you" in text
    # The exclusion list is the whole point -- must name what's left out.
    assert "notes" in text.lower()


def test_acknowledgment_text_names_the_data_use_consent():
    assert "anonymized" in WAIVER_ACKNOWLEDGMENT_TEXT
    assert "Section 4" in WAIVER_ACKNOWLEDGMENT_TEXT


def test_acceptance_mechanism_section_covers_existing_users():
    matches = [s for s in WAIVER_SECTIONS if "How You Accept This Agreement" in s.heading]
    assert len(matches) == 1
    text = " ".join(matches[0].paragraphs)
    assert "existing account" in text
    assert "Continuing to use the App" in text


def test_hash_matches_current_text_not_hardcoded():
    """The hash must be derived, not a stale constant left over from
    v1 -- if this fails, WAIVER_TEXT_SHA256 stopped being computed from
    the real text somehow."""
    import hashlib
    assert WAIVER_TEXT_SHA256 == hashlib.sha256(waiver_canonical_text().encode("utf-8")).hexdigest()
