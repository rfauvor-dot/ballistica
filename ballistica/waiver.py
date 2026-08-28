"""Canonical liability waiver text -- the single source of truth for both
what's displayed on the account-creation waiver screen and what's hashed
into a user's acceptance record, so the two can never independently drift
apart. `GET /waiver` (api.py) serves this structure as JSON; index.html
renders it directly rather than keeping its own separate copy of the text.

Sourced verbatim from the attorney-approved draft
(Ballistica_Liability_Waiver_DRAFT.docx, provided by Rick 2026-08-28) --
every substantive section and the acknowledgment paragraph reproduced
unedited. Two paragraphs from that file are deliberately NOT reproduced
here: the "DRAFT -- For Attorney Review Only -- Not Yet in Effect" header
and the "Drafting note to Rick (delete before this becomes the live
version)" note -- the source document's own text instructs deleting both
before going live, and per Rick's 2026-08-28 instruction the attorney has
approved this content contingent on exactly the acceptance flow built
around this module, i.e. this is that live version.

WAIVER_VERSION is a plain human-readable label (bump it -- and only it --
whenever the section text below changes); WAIVER_TEXT_SHA256 is computed
directly from the section text itself, not hand-maintained, so a version
label someone forgets to bump can never silently mismatch a text change --
the hash always reflects the exact text a user actually saw.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

WAIVER_VERSION = "2026-08-28-v1"

WAIVER_TITLE = "SAFETY ACKNOWLEDGMENT, ASSUMPTION OF RISK, DATA DISCLAIMER, AND LIABILITY WAIVER"


@dataclass(frozen=True)
class WaiverSection:
    heading: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()


WAIVER_SECTIONS: tuple[WaiverSection, ...] = (
    WaiverSection(
        heading="1. What Ballistica Is",
        paragraphs=(
            "Ballistica (“the App,” “we,” “us”) is a reference and "
            "calculation tool for firearms ballistics and handloading/reloading information. The "
            "App is provided to help you organize, calculate, and estimate ballistic data for your "
            "own firearms, loads, and shooting activity. The App is an informational and "
            "organizational tool only. It is not a substitute for a published reloading manual, "
            "manufacturer load data, professional instruction, or your own independent judgment.",
        ),
    ),
    WaiverSection(
        heading="2. Reloading and Firearms Use Involves Serious Risk",
        paragraphs=(
            "You acknowledge that handloading/reloading ammunition and the use of firearms are "
            "inherently dangerous activities that can result in serious injury, death, or property "
            "damage if performed incorrectly. Small variations in components, measurements, "
            "equipment, or technique — including variations not reflected in any dataset — "
            "can produce dangerous outcomes such as excess pressure, firearm damage, or personal "
            "injury.",
        ),
    ),
    WaiverSection(
        heading="3. No Professional Advice; Reference Data Only",
        paragraphs=(
            "Nothing in the App constitutes professional, engineering, or safety advice. Ballistic "
            "and load data displayed, calculated, stored, or suggested by the App — including "
            "data you enter yourself, data drawn from open or community-sourced datasets, and any "
            "aggregate or “community average” data — is provided for reference and "
            "organizational purposes only and may be incomplete, outdated, drawn from unverified "
            "sources, or simply wrong.",
        ),
        bullets=(
            "Community-sourced or aggregate data is not laboratory-verified and is not reviewed by "
            "a ballistician before being shown to you.",
            "Data you or other users enter is self-reported and is not independently checked for "
            "accuracy by us.",
            "Any calculation, suggestion, or estimate produced by the App may differ from "
            "real-world performance.",
        ),
    ),
    WaiverSection(
        heading="4. Your Independent Duty to Verify",
        paragraphs=(
            "Before loading, firing, or otherwise relying on any data displayed in the App, you "
            "agree to independently verify that data against current, published data from a "
            "component manufacturer (e.g., powder, bullet, primer, or case manufacturer) or another "
            "authoritative source, and to follow that manufacturer's published load data, published "
            "pressures, and safety warnings — including starting at reduced charge weights and "
            "working up gradually while watching for pressure signs — rather than relying on "
            "the App as your sole source of truth. You agree that you, and not Ballistica, are "
            "solely responsible for confirming that any load, component combination, or firearm "
            "setting is safe before use.",
        ),
    ),
    WaiverSection(
        heading="5. Assumption of Risk",
        paragraphs=(
            "By creating an account or using the App, you knowingly and voluntarily assume all "
            "risks associated with handloading, reloading, and firearms use, and all risks "
            "associated with any decision you make in reliance on information displayed, "
            "calculated, or stored by the App, whether or not that information originated from us, "
            "from open datasets, from other users, or from your own data entry.",
        ),
    ),
    WaiverSection(
        heading="6. Release and Waiver of Liability",
        paragraphs=(
            "To the fullest extent permitted by law, you release, waive, and discharge Ballistica, "
            "its owner(s), developers, and affiliates from any and all liability, claims, demands, "
            "or causes of action arising out of or related to any injury, death, or property damage "
            "resulting from your handloading, reloading, or firearms activity, whether or not "
            "related to your use of the App, including claims based on the accuracy, completeness, "
            "or currency of any data displayed by the App.",
        ),
    ),
    WaiverSection(
        heading="7. Indemnification",
        paragraphs=(
            "You agree to indemnify and hold harmless Ballistica and its owner(s), developers, and "
            "affiliates from any claims, damages, losses, or expenses (including reasonable "
            "attorney's fees) arising out of your use of the App, your handloading or reloading "
            "activity, or your violation of this agreement or of any applicable law.",
        ),
    ),
    WaiverSection(
        heading="8. No Warranty; Limitation of Liability",
        paragraphs=(
            "The App and all data within it are provided “as is” and “as "
            "available,” without warranty of any kind, express or implied, including any "
            "warranty of accuracy, merchantability, or fitness for a particular purpose. To the "
            "fullest extent permitted by law, Ballistica's total liability arising out of or "
            "related to your use of the App shall not exceed the amount, if any, you paid to use "
            "the App in the twelve (12) months before the claim arose.",
        ),
    ),
    WaiverSection(
        heading="9. Compliance With Law; Eligibility",
        paragraphs=(
            "You represent that you are of legal age in your jurisdiction to purchase and possess "
            "firearms and ammunition components, and that your use of the App and any handloading "
            "or shooting activity will comply with all applicable federal, state, and local laws.",
        ),
    ),
    WaiverSection(
        heading="10. How You Accept This Agreement",
        paragraphs=(
            "This agreement is not enforceable against a user until the user has affirmatively "
            "accepted it through a clear, standalone acceptance step in the App — for example, "
            "a checkbox that is unchecked by default, presented on its own screen (not bundled into "
            "general Terms of Service), that a new user must actively check before an account can "
            "be created, with the acknowledgment text visible above the checkbox rather than behind "
            "a separate link only.",
        ),
    ),
    WaiverSection(
        heading="11. Severability and General Terms",
        paragraphs=(
            "If any provision of this agreement is found unenforceable, the remaining provisions "
            "remain in full effect. This agreement is governed by the laws of the State of Idaho, "
            "without regard to conflict-of-law principles. [Attorney to confirm venue/arbitration "
            "language, if any, before this becomes final.]",
        ),
    ),
)

WAIVER_ACKNOWLEDGMENT_TEXT = (
    "I have read and understand this Safety Acknowledgment, Assumption of Risk, Data Disclaimer, "
    "and Liability Waiver. I understand that handloading and firearms use are dangerous activities, "
    "that data in this App is reference-only and may be inaccurate, and that I am solely "
    "responsible for independently verifying any load or ballistic data before use. I voluntarily "
    "accept these terms."
)


def waiver_canonical_text() -> str:
    """Deterministic plain-text rendering of the whole waiver -- the exact
    string WAIVER_TEXT_SHA256 is computed over. Changing a single
    character of any section, bullet, or the acknowledgment text changes
    this string, and therefore the hash -- that's the entire point: an
    acceptance record's hash provably ties it to one specific exact
    wording, not just a version label someone has to remember to bump."""
    parts = [WAIVER_TITLE]
    for section in WAIVER_SECTIONS:
        parts.append(section.heading)
        parts.extend(section.paragraphs)
        parts.extend(section.bullets)
    parts.append("Acknowledgment (in-app acceptance text)")
    parts.append(WAIVER_ACKNOWLEDGMENT_TEXT)
    return "\n\n".join(parts)


WAIVER_TEXT_SHA256 = hashlib.sha256(waiver_canonical_text().encode("utf-8")).hexdigest()
