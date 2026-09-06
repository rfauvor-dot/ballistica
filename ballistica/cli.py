"""Text-based stand-in for voice interaction.

Real speech I/O (ElevenLabs STT/TTS, matching Rick's Pearl/Lucia stack)
is not wired up here -- that needs API keys and a decision on the
runtime. This REPL exists to exercise the same query patterns the
voice interface will need to support, and to make the engine usable
today from a terminal. Reply tone is mode-aware: live-fire/calibration
answers stay terse and numeric, setup/status/small-talk replies are
more conversational -- see the handler methods below for the split.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
import random
import re
import sys
import time

from dotenv import load_dotenv

# Idempotent: api.py also loads this, but cli.py needs its own copy so
# the LLM intent fallback works when this module is used standalone
# (python -m ballistica.cli), not just through the API server.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .angle import solve_incline_angle
from .atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE
from .intent import classify_calibration_turn, extract_intent, extract_setup_fields
from .profiles import Load, ProfileStore, Rifle
from .reporting import format_table_text, report_for_point, report_table
from .trajectory import TrajectorySolver, WindCondition
from .zero import find_minimum_spread_zero

HELP_TEXT = """\
Commands (voice-style phrasing is fine, punctuation is ignored):
  drop at <X> yards                        -- single-range query
  table [to <X> yards] [every <Y> yards]    -- full drop/windage table
  switch to <name>                         -- change active load (fuzzy match)
  switch rifle to <name>                   -- change active rifle
  what zero minimizes my spread out to <X> yards
  I'm seeing <N> clicks at <X> yards[, from <R> yards]  -- solve incline angle
  set conditions temp <T> pressure <P> altitude <A> humidity <H>
  set wind <speed> mph from <clock> oclock
  repeat solution / repeat elevation / repeat windage -- re-speak the last drop-at solution
  new load / new rifle                      -- guided voice setup, "cancel" to bail out,
                                                "skip" to pass on an optional field
  start calibration                         -- chrono the active load; read shots as numbers,
                                                "average", "discard that", "end calibration"
  status                                    -- show active rifle/load/atmosphere
  list rifles / list loads
  help
  quit
"""

# Required fields for a new load/rifle to be saveable -- these can't be
# skipped, the interview keeps asking until they're answered.
_LOAD_REQUIRED = ["name", "bullet_weight_gr", "bc", "drag_model", "muzzle_velocity_fps", "zero_distance_yd"]
_LOAD_PROMPTS = {
    "name": "What do you want to call this load?",
    "bullet_weight_gr": "What's the bullet weight, in grains?",
    "bc": "What's the ballistic coefficient?",
    "drag_model": "G1 or G7 drag model?",
    "muzzle_velocity_fps": "What's the muzzle velocity, in feet per second?",
    "zero_distance_yd": "What yardage is it zeroed at?",
}

_RIFLE_REQUIRED = ["name", "scope_height_in", "optic_type"]
_RIFLE_PROMPTS = {
    "name": "What do you want to call this rifle?",
    "scope_height_in": "What's the scope height above bore, in inches?",
    # Asked early, right after the other required fields, because it
    # determines which optic-info questions make sense to ask next -- a
    # red dot has no magnification or focal plane at all. Added after a
    # real setup failure (Addendum 27): a Holosun 510C red dot couldn't
    # get through the interview because every optic-info question assumed
    # a magnified scope.
    "optic_type": "Is this a magnified scope or a red dot?",
}

# The rest of the same field set the manual Setup form has -- asked one at
# a time after the required fields, same as the required ones, but "skip"
# (or "none"/"not sure"/etc.) moves on without answering. Mirroring the
# manual form's full field list is deliberate: the voice flow used to stop
# at the required subset and silently never ask about scope info, twist
# rate, etc., which looked like the interview was incomplete/broken.
_LOAD_EXTRA_FIELDS = ["bullet_type", "powder", "powder_charge_gr", "notes"]
_LOAD_EXTRA_PROMPTS = {
    "bullet_type": "What bullet -- make and type?",
    "powder": "What powder are you running?",
    "powder_charge_gr": "What's the powder charge, in grains?",
    "notes": "Any notes to add?",
}

# Fields common to both optic types, plus the fields specific to each --
# magnification and focal plane are meaningless on a fixed-1x red dot, and
# a red dot's "reticle" is a dot/circle size in MOA, not a scope's
# crosshair pattern. _rifle_extra_fields() picks the right set once
# optic_type is known (see _RIFLE_REQUIRED above).
_RIFLE_EXTRA_FIELDS_COMMON = ["caliber", "barrel_length_in", "twist_rate", "click_value_mrad",
                              "reticle_unit", "scope_make", "scope_model", "has_suppressor"]
_RIFLE_EXTRA_FIELDS_SCOPE = ["magnification", "objective_lens_mm", "focal_plane", "reticle_type"]
_RIFLE_EXTRA_FIELDS_RED_DOT = ["dot_size_moa", "reticle_type"]
_RIFLE_EXTRA_PROMPTS = {
    "caliber": "What caliber?",
    "barrel_length_in": "Barrel length, in inches?",
    "twist_rate": "What's the twist rate?",
    "click_value_mrad": "What's the click value?",
    "reticle_unit": "Is the reticle MRAD or MOA?",
    "scope_make": "What's the make?",
    "scope_model": "What's the model?",
    "has_suppressor": "Does this rifle run a suppressor?",
    "suppressor_type": "What type or brand -- or just describe it if there isn't one?",
    "magnification": "What magnification range?",
    "objective_lens_mm": "Objective lens size, in millimeters?",
    "focal_plane": "First or second focal plane?",
    "reticle_type": "What reticle type?",
    "dot_size_moa": "What size dot, in MOA?",
}


def _speak_field(field: str, value) -> str:
    """Natural-language framing for one field value in a spoken
    readout -- names what the number IS and its unit, rather than
    reading a bare digit string. Confirmed live (2026-09-06): the old
    behavior (_extras_summary() joining raw str(value)s with commas,
    "Also got: 5.7x28, 11.0, 1:16...") was genuinely impossible to
    parse by ear -- Rick's own example, a zero distance or scope height
    read back as something like "zero three six five" with no way to
    tell which field it belonged to or what unit it was in. Shared by
    _extras_summary() (setup interview readback) and
    _update_rifle_fields()'s own description, same root cause either
    place a raw field dict gets spoken back."""
    if field == "caliber":
        return f"caliber {value}"
    if field == "barrel_length_in":
        return f"{value:g} inch barrel"
    if field == "twist_rate":
        return f"twist rate {value}"
    if field == "click_value_mrad":
        return f"click value {value:g}"
    if field == "reticle_unit":
        return f"{value} reticle"
    if field == "scope_make":
        return f"made by {value}"
    if field == "scope_model":
        return f"model {value}"
    if field == "suppressor_type":
        return f"suppressor, {value}"
    if field == "magnification":
        return f"{value} magnification"
    if field == "objective_lens_mm":
        return f"{value:g} millimeter objective lens"
    if field == "focal_plane":
        return f"{value} focal plane"
    if field == "reticle_type":
        return f"reticle, {value}"
    if field == "dot_size_moa":
        return f"{value:g} M O A dot"
    if field == "bullet_type":
        return f"bullet, {value}"
    if field == "powder":
        return f"powder, {value}"
    if field == "powder_charge_gr":
        return f"{value:g} grain charge"
    if field == "notes":
        return f"note, {value}"
    return f"{field.replace('_', ' ')} {value}"


def _rifle_extra_fields(optic_type: str, has_suppressor: bool = False) -> list[str]:
    # suppressor_type is only asked when has_suppressor is actually true --
    # tied to the rifle, not any one load (Addendum 36), and inserted right
    # after has_suppressor so the two questions stay adjacent in the
    # conversation rather than getting split apart by the optic questions.
    fields = list(_RIFLE_EXTRA_FIELDS_COMMON)
    if has_suppressor:
        fields.append("suppressor_type")
    fields += _RIFLE_EXTRA_FIELDS_RED_DOT if optic_type == "red_dot" else _RIFLE_EXTRA_FIELDS_SCOPE
    return fields

_SKIP_RE = re.compile(r"^(skip|none|n/?a|not sure|don.t know|no|nothing|pass)\b")

# Confirmed live (Addendum 28): "that is correct" -- a completely natural
# confirmation -- matched none of the old patterns (they only recognized
# "correct" as the FIRST word, and "that's right" as one specific literal
# phrase). It fell through to field extraction, found nothing, and got
# stuck in the generic "didn't catch that" retry loop with the setup
# session still open -- which looked exactly like a dead mic from the
# outside, even though this reproduces identically with plain text, no
# audio involved at all. Widened to an unanchored "correct"/"right"
# check (safe here specifically because this only runs while confirming
# a setup summary -- outside that state, `correct` is not treated as a
# universal yes). Checked AFTER negation, and negation itself is checked
# both anchored ("no, ...") and unanchored ("that's not right") so a
# rejection is never misread as a confirmation just because it contains
# the word "right" or "correct".
_CONFIRM_NO_RE = re.compile(r"^(no|nope|not (quite|right|correct)|wrong|incorrect)\b[,.]?\s*(.*)$")
_CONFIRM_YES_WORD_RE = re.compile(r"^(yes|yeah|yep|yup|confirm(ed)?|save( it)?|sounds good|good to go)\b")
# Separate from _CONFIRM_YES_WORD_RE above on purpose (2026-09-06, real
# bug confirmed live): that one's vocabulary is tuned for "do you want
# to SAVE this new profile" (setup/calibration confirmations), so
# "save"/"save it" is a real yes-word there but "delete"/"delete it" is
# not in its list at all. For "do you want to DELETE this", the single
# most natural word to say back is "delete" itself -- Rick said exactly
# that on a real delete confirmation and it fell through to the
# implicit-no branch, silently keeping the rifle with no indication
# anything had gone wrong. "Save" is deliberately NOT in this list --
# saying "save" to confirm a delete would be actively misleading.
_CONFIRM_DELETE_YES_RE = re.compile(r"^(yes|yeah|yep|yup|confirm(ed)?|delete( it)?|do it|go ahead)\b")
_NEGATED_CONFIRM_RE = re.compile(r"\b(not|isn.t|wasn.t|ain.t)\b[\w\s]{0,15}\b(correct|right)\b")

# Confirmed live (Addendum 11): asked something that isn't an answer to
# the current field (e.g. "what caliber", said while scope height was
# being asked), the extractor can hallucinate a placeholder value like
# "<UNKNOWN>" instead of just omitting the field -- which then overwrites
# a real value already in the draft with garbage, silently corrupting
# data, and *also* defeats the no-progress failure counter above (the
# draft dict genuinely changed, so it looks like progress every turn even
# though the same required field keeps going unanswered forever). Filtered
# out here so a hallucinated placeholder is never treated as a real value.
_PLACEHOLDER_RE = re.compile(
    r"^(unknown|n/?a|none|null|undefined|not specified|not given|not sure|unspecified)$"
    r"|^[<\[].*[>\]]$",
    re.IGNORECASE,
)


def _is_real_value(value) -> bool:
    if value in (None, ""):
        return False
    return not (isinstance(value, str) and _PLACEHOLDER_RE.match(value.strip()))

# A modal setup/calibration session used to stay open indefinitely on
# repeated "didn't catch that" turns -- reasonable for one bad mic pickup,
# but with no cap it could spin forever on wind noise, silence, or a
# transcription hiccup, re-asking the same question every few seconds with
# no way out except closing the app (confirmed live, Addendum 11: a
# consecutive-failure loop that even ignored disabling voice, because the
# frontend loop had no independent way to know the session was stuck vs.
# genuinely still making progress). Auto-cancelling after a few consecutive
# failures gives the loop a guaranteed exit regardless of what the
# frontend does.
_MAX_FAILED_ATTEMPTS = 3

# Root-caused live: a modal session left open (abandoned mid-interview, a
# forgotten test call against the shared production instance, an app
# closed without saying "cancel") sits there indefinitely -- unlike
# _MAX_FAILED_ATTEMPTS above, which only fires on repeated failed turns
# *within* an active back-and-forth, an abandoned session with zero
# further turns never increments that counter at all. It just waits,
# silently, until some completely unrelated later utterance arrives and
# gets swallowed as if it were an answer to a session the shooter has
# long since forgotten about -- confirmed live: "add a new rifle" (heard
# correctly, verbatim, by STT) was answered as a shot-velocity reading
# inside a stale calibration session instead of starting a new rifle
# setup, because that old session was still technically "active". Any
# session (setup, calibration, or a pending delete confirmation) idle
# longer than this is treated as abandoned and cleared before the
# current utterance is processed, rather than silently absorbing it.
_SESSION_STALE_SECONDS = 300

# How many recent user/assistant exchanges the open-ended conversational
# path (extract_intent's "converse" result) carries forward as context --
# bounds token cost per call rather than growing for the whole session;
# ordinary ballistics commands don't touch this at all (see
# BallisticaCLI._chat_history).
_CHAT_HISTORY_MAX_TURNS = 6


class _SetupSession:
    """In-progress voice interview for a new load or rifle -- nothing's
    written to the store until the shooter confirms. Lives purely in
    memory for the single-tenant CLI/REPL; the multi-tenant /v2 voice
    endpoint (api.py) instead round-trips this through
    to_dict()/from_dict() into conversation_state.state_json on every
    turn, so it survives across the stateless-per-request API model
    (and any restart in between) instead of needing a kept-alive
    per-user object in server memory."""

    def __init__(self, kind: str) -> None:
        self.kind = kind  # "load" or "rifle"
        self.draft: dict = {}
        self.skipped: set = set()
        self.confirming = False
        self.failed_attempts = 0
        self.last_activity = time.time()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "draft": self.draft, "skipped": sorted(self.skipped),
            "confirming": self.confirming, "failed_attempts": self.failed_attempts,
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_SetupSession":
        session = cls(data["kind"])
        session.draft = data["draft"]
        session.skipped = set(data["skipped"])
        session.confirming = data["confirming"]
        session.failed_attempts = data["failed_attempts"]
        session.last_activity = data["last_activity"]
        return session


class _CalibrationSession:
    """In-progress chronograph calibration for one load: a running list of
    shot velocities, accumulated live as they're read off. Nothing is
    written back to the load's muzzle_velocity_fps until the shooter ends
    the session and confirms -- matches _SetupSession's don't-save-until-
    confirmed pattern, for the same reason (a misheard number here is a
    silent, hard-to-notice data corruption, not just an annoyance). Same
    to_dict()/from_dict() round-trip purpose as _SetupSession above."""

    def __init__(self, rifle_name: str, load_name: str) -> None:
        self.rifle_name = rifle_name
        self.load_name = load_name
        self.shots: list[float] = []
        self.confirming = False
        self.failed_attempts = 0
        self.last_activity = time.time()

    def to_dict(self) -> dict:
        return {
            "rifle_name": self.rifle_name, "load_name": self.load_name, "shots": self.shots,
            "confirming": self.confirming, "failed_attempts": self.failed_attempts,
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_CalibrationSession":
        session = cls(data["rifle_name"], data["load_name"])
        session.shots = data["shots"]
        session.confirming = data["confirming"]
        session.failed_attempts = data["failed_attempts"]
        session.last_activity = data["last_activity"]
        return session


def bootstrap_default_profile(store: ProfileStore) -> None:
    """Preloads Rick's validation test case: 5.56/.223 Wylde 20in Faxon,
    77gr SMK, two H335 candidate loads, 36yd near-zero.

    BC is Sierra's own published figure for stock #9377 (77gr HPBT
    MatchKing): .362 for the 1700-3000 fps band, which covers both
    loads' muzzle velocities (cross-checked directly against Sierra's
    published ballistic coefficient table -- Sierra publishes this
    bullet as G1 only, no G7 figure). A chronograph-measured or
    Doppler-derived BC (or a proper G7 figure from Litz/Applied
    Ballistics, since G7 tends to track boat-tail match bullets more
    consistently across the transonic band) would be a meaningful
    upgrade once Rick has that data -- update_load_velocity() and the
    Load fields make that a one-line change, no need to touch anything
    else.
    """
    rifle = Rifle(
        name="AR-15 20in Faxon",
        scope_height_in=2.5,
        caliber=".223 Wylde",
        barrel_length_in=20.0,
        twist_rate="1:7",
        click_value_mrad=0.1,
    )
    rifle.add_load(Load(
        name="21.0gr H335", bullet_weight_gr=77, bc=0.362, drag_model="G1",
        muzzle_velocity_fps=2422, zero_distance_yd=36,
        bullet_type="77gr Sierra MatchKing (SMK)", powder="H335",
        powder_charge_gr=21.0,
        notes="Sierra book BC (G1, 1700-3000fps band); velocity is a book estimate pending chrono data",
    ), make_active=False)
    rifle.add_load(Load(
        name="23.5gr H335", bullet_weight_gr=77, bc=0.362, drag_model="G1",
        muzzle_velocity_fps=2766, zero_distance_yd=36,
        bullet_type="77gr Sierra MatchKing (SMK)", powder="H335",
        powder_charge_gr=23.5,
        notes="Sierra book BC (G1, 1700-3000fps band); pending pressure verification",
    ), make_active=True)
    store.add_rifle(rifle)
    store.save()


class BallisticaCLI:
    def __init__(self, store: ProfileStore) -> None:
        self.store = store
        self.atmosphere: AtmosphereConditions = STANDARD_ATMOSPHERE
        self.wind = WindCondition()
        self._last_solution: dict | None = None
        self._setup: _SetupSession | None = None
        self._calibration: _CalibrationSession | None = None
        self._pending_delete: str | None = None
        self._pending_delete_at: float = 0.0
        # Confirmation gate before a calibration session actually starts
        # (2026-09-06, range retest): start_calibration is one of several
        # tools the LLM can call via tool_choice="auto" alongside ordinary
        # conversation/wind/conditions handling, and a wind-adjustment
        # utterance mid-solution was confirmed live to occasionally
        # misfire into it -- once started, calibration is modal (every
        # later utterance becomes a shot reading unless it happens to
        # contain an exact interrupt word), so an accidental entry here
        # is far more disruptive than an accidental entry to most other
        # tools. The deterministic fast-path regex in handle() ("start
        # calibration", "let's chrono this") is already unambiguous and
        # starts immediately, unchanged -- this gate applies only to the
        # LLM-dispatched path (_request_start_calibration(), used by
        # _dispatch_intent), which asks first instead of committing.
        self._pending_calibration_start: bool = False
        self._pending_calibration_start_at: float = 0.0
        # Same confirmation gate, extended to setup (2026-09-06, Rick's
        # own follow-up ask): start_load_setup/start_rifle_setup are
        # LLM-dispatched exactly like start_calibration was, with the
        # same tool_choice="auto" risk of an unrelated utterance getting
        # classified into one of them. A guided setup interview is just
        # as modal as calibration once running (every utterance becomes
        # a field answer). Stores kind ("load"/"rifle") and the original
        # triggering text so field pre-fill (extract_setup_fields) can
        # still run once confirmed, exactly as it would have immediately
        # under the old unconfirmed path -- deferred until confirmation
        # rather than done speculatively before knowing this is really
        # wanted. The deterministic fast-path regex in handle() ("let's
        # set up a new load/rifle") is already unambiguous and still
        # starts immediately, unchanged -- this gate applies only to the
        # LLM-dispatched path.
        self._pending_setup_kind: str | None = None
        self._pending_setup_text: str = ""
        self._pending_setup_at: float = 0.0
        # Set True by whichever handler produces a dense numeric readout
        # this turn (drop-at-range, repeat, table, spread-zero, incline
        # angle) -- api.py reads this right after handle() returns so
        # the frontend knows to keep TTS at the slower, careful pace
        # ONLY for that reply, not for every reply (2026-09-06: general
        # conversation had been dragged down to the same slow pace as
        # numeric readouts, which is what the pace was ever slowed down
        # for in the first place -- Rick's own read, "she sounds drunk,
        # drugged, or a robot" for ordinary chat). Reset at the top of
        # every handle() call, not sticky across turns.
        self._last_reply_is_readout = False
        # Rolling short-term memory for genuine open-ended conversation
        # (2026-09-05) -- NOT populated by ordinary ballistics commands
        # (switch rifle, drop-at-range, etc.), only by "converse" turns,
        # so a follow-up like "is that safe to bump up" can resolve what
        # "that" refers to without every terse command bloating the
        # context sent to the LLM on every single turn.
        self._chat_history: list[dict] = []

    def solver(self) -> tuple[TrajectorySolver, Rifle, Load]:
        rifle = self.store.get_active_rifle()
        load = rifle.get_active_load()
        solver = TrajectorySolver(
            muzzle_velocity_fps=load.muzzle_velocity_fps,
            bc=load.bc,
            drag_model=load.drag_model,
            scope_height_in=rifle.scope_height_in,
            atmosphere=self.atmosphere,
            wind=self.wind,
        )
        return solver, rifle, load

    def _expire_stale_sessions(self) -> None:
        """Clears any modal session that's been sitting untouched longer
        than _SESSION_STALE_SECONDS -- see that constant's comment for
        why this exists (a real, confirmed-live bug: an abandoned
        session silently absorbing a later, completely unrelated
        utterance instead of the shooter ever finding out it was still
        open)."""
        now = time.time()
        if self._setup is not None and now - self._setup.last_activity > _SESSION_STALE_SECONDS:
            self._setup = None
        if self._calibration is not None and now - self._calibration.last_activity > _SESSION_STALE_SECONDS:
            self._calibration = None
        if self._pending_delete is not None and now - self._pending_delete_at > _SESSION_STALE_SECONDS:
            self._pending_delete = None
        if self._pending_calibration_start and now - self._pending_calibration_start_at > _SESSION_STALE_SECONDS:
            self._pending_calibration_start = False
        if self._pending_setup_kind is not None and now - self._pending_setup_at > _SESSION_STALE_SECONDS:
            self._pending_setup_kind = None
            self._pending_setup_text = ""

    def _requests_different_top_level_task(self, low: str) -> bool:
        """Whether `low` unambiguously asks for a different top-level task
        than whatever modal session is currently running -- only ever
        checked while a modal session is active (see handle()), so this
        never runs redundantly outside of one.

        Deliberately narrower than it could be, favoring a missed
        interrupt over a false one: a bare "100 yards" is a completely
        normal answer to "what yardage is it zeroed at?" mid-setup, NOT a
        solution request, so a distance alone never counts here -- only
        an explicit trigger word (solution/shoot) does. Same reasoning
        for "new rifle"/"new load": guarded against firing when we're
        already in that exact kind of setup, since "it's a new load I
        just made up, call it Test Load" is a real, plausible answer
        while naming a load, not a request to abandon the current draft
        and start another blank one."""
        already_rifle_setup = self._setup is not None and self._setup.kind == "rifle"
        already_load_setup = self._setup is not None and self._setup.kind == "load"

        if not already_rifle_setup and re.search(r"\b(?:new|add)\b.*\brifle\b", low):
            return True
        if not already_load_setup and re.search(r"\b(?:new|add|set ?up)\b.*\b(load|round)\b", low):
            return True
        if self._calibration is None and (re.search(r"\bcalibrat(?:e|ion)\b", low) or re.search(r"\bchrono(?:graph)?\b", low)):
            return True
        if re.search(r"\bswitch\b", low):
            return True
        # \bshoot\b alone missed "shooting" -- \b requires a boundary
        # right after "shoot", which "shooting" never has. Found live
        # (2026-09-06): stuck mid-calibration, "we're shooting distance
        # now" failed to interrupt for exactly this reason, alongside
        # "no, we're not doing velocities" and "I adjusted for wind, try
        # again" -- none of which contain any of this method's trigger
        # words at all, meaning today's fix only closes the "shoot" vs
        # "shooting" gap, not a full guarantee every plain-English way of
        # saying "get me out of this" will interrupt (that's the
        # limitation motivating the confirmation-gate fix alongside this
        # one -- see _start_calibration()).
        if re.search(r"\b(solutions?|shoot\w*)\b", low):
            return True
        return False

    def handle(self, text: str) -> str:
        t = text.strip()
        if not t:
            return ""
        low = t.lower()
        self._last_reply_is_readout = False

        self._expire_stale_sessions()

        # Top-level intent switching (2026-09-05, deployed to main
        # 2026-09-06 after being confirmed live-broken again in
        # production -- this fix existed on an in-progress branch but
        # never actually shipped): confirmed live, repeatedly, that once
        # a modal session (setup/calibration) started, EVERY utterance
        # got swallowed by it -- only a tiny fixed set of exact cancel
        # phrases ("cancel", "never mind", "quit"...) could ever escape
        # it. A clearly-stated request for a different top-level task
        # ("add a new rifle" while mid-load-setup, "give me a solution"
        # while mid-calibration) got treated as a field answer or a shot
        # reading instead of what it actually was -- Rick's own read on
        # this: it's a state-machine gap, not a vocabulary gap, and the
        # fix is checking for a mode switch FIRST, before committing the
        # utterance to whatever's currently running. Clearing state here
        # and falling through to the normal (non-modal) handling below
        # reuses every existing dispatch path (start_rifle_setup,
        # start_load_setup, switch_rifle, drop-at-range, calibration)
        # exactly as-is -- nothing about how those commands themselves
        # work changes, only when they're allowed to fire.
        if (self._setup is not None or self._calibration is not None) and self._requests_different_top_level_task(low):
            self._setup = None
            self._calibration = None
        # A guided load/rifle setup interview is modal: once it's running,
        # every utterance is directed at it (a field value, a correction,
        # or a way out) until it's confirmed or cancelled -- including
        # "quit"/"exit", which cancel the interview here rather than the
        # whole session.
        elif self._setup is not None:
            self._setup.last_activity = time.time()
            return self._handle_setup_turn(t)

        # Same modal pattern as setup, above: while a calibration is
        # running, every utterance is a shot reading, a control phrase
        # ("average", "discard that", "end calibration"), or a way out.
        elif self._calibration is not None:
            self._calibration.last_activity = time.time()
            return self._handle_calibration_turn(t)

        # A destructive action -- one confirmation gate, no separate
        # session class needed for a single yes/no.
        if self._pending_delete is not None:
            self._pending_delete_at = time.time()
            return self._handle_delete_confirm(t)

        if self._pending_calibration_start:
            self._pending_calibration_start_at = time.time()
            return self._handle_calibration_start_confirm(t)

        if self._pending_setup_kind is not None:
            self._pending_setup_at = time.time()
            return self._handle_setup_start_confirm(t)

        if low in ("help", "?"):
            return HELP_TEXT
        if low in ("quit", "exit"):
            raise SystemExit(0)
        # Fast, free path for the small talk that's common enough not to
        # burn an LLM call on -- everything else genuinely conversational
        # ("rough day at the range", "you're the best", an open question
        # about reloading) falls through to extract_intent()'s own
        # "converse" result (2026-09-05) rather than a separate personality
        # call -- it's allowed to just talk, not only classify a command.
        if low in ("hi", "hello", "hey"):
            return random.choice(["Hey, Rick.", "Hey there.", "What's up?"])
        if re.search(r"\b(thanks|thank you|good job|nice work|well done)\b", low):
            return random.choice(["Anytime.", "You got it.", "That's what I'm here for."])
        if low == "status":
            return self._status()
        if low == "list rifles":
            return "\n".join(self.store.rifles.keys()) or "No rifles configured."
        if low == "list loads":
            rifle = self.store.get_active_rifle()
            return "\n".join(rifle.loads.keys()) or "No loads configured."

        m = re.search(r"drop at ([\d.]+)\s*(?:yd|yard|yards)", low)
        if m:
            return self._drop_at(float(m.group(1)))

        m = re.search(r"^table(?:\s+to\s+([\d.]+)\s*(?:yd|yard|yards))?"
                       r"(?:\s+every\s+([\d.]+)\s*(?:yd|yard|yards))?", low)
        if low.startswith("table"):
            max_range = float(m.group(1)) if m and m.group(1) else 500.0
            step = float(m.group(2)) if m and m.group(2) else 100.0
            return self._table(max_range, step)

        # "repeat windage/elevation/solution" -- re-speaks the last drop-at
        # solution from memory rather than recalculating, so it works even
        # right after a load/condition switch that hasn't been re-queried
        # yet. Checked with word boundaries so it doesn't fire on unrelated
        # phrases that happen to contain "again".
        if re.search(r"\b(repeat|again)\b", low):
            if re.search(r"\bwindage\b", low):
                return self._repeat("windage")
            if re.search(r"\belevation\b", low):
                return self._repeat("elevation")
            return self._repeat("solution")

        # Start a guided voice interview for a brand new load/rifle --
        # deliberately distinct from "switch to <name>" above, which only
        # ever selects among loads/rifles that already exist.
        if re.search(r"\b(?:new|add|set ?up|create)\b.*\bload\b", low):
            return self._start_setup("load", t)
        if re.search(r"\b(?:new|add|set ?up|create)\b.*\brifle\b", low):
            return self._start_setup("rifle", t)

        if re.search(r"\bcalibrat(?:e|ion)\b", low) or re.search(r"\bchrono(?:graph)?\b", low):
            return self._start_calibration()

        m = re.search(r"\b(?:delete|remove|get rid of)\b\s*(?:the\s+)?(.*)", low)
        if m:
            # No longer requires the literal word "rifle" -- Rick's own
            # report (2026-09-06): "get rid of the 5.7x28 11 inch" has
            # no noun at all (some of what's stored here are pistols,
            # not rifles), just a caliber/barrel-length description.
            # Whatever's left after the delete verb becomes a fuzzy
            # query, same net find_rifle() already casts for switching.
            return self._request_delete_rifle_by_query(m.group(1).strip())

        m = re.search(r"switch rifle to (.+)", low)
        if m:
            return self._switch_rifle(m.group(1).strip())

        m = re.search(r"switch to (.+)", low)
        if m:
            return self._switch_load(m.group(1).strip())

        m = re.search(r"what zero minimizes my spread out to ([\d.]+)\s*(?:yd|yard|yards)", low)
        if m:
            return self._minimum_spread_zero(float(m.group(1)))

        m = re.search(
            r"seeing (-?[\d.]+)\s*clicks?(?:\s*(?:difference|diff))?\s*at\s*([\d.]+)\s*(?:yd|yard|yards)"
            r"(?:,?\s*from\s*([\d.]+)\s*(?:yd|yard|yards))?", low)
        if m:
            observed = float(m.group(1))
            los = float(m.group(2))
            ref = float(m.group(3)) if m.group(3) else 100.0
            return self._solve_angle(observed, los, ref)

        m = re.search(
            r"set conditions temp (-?[\d.]+)\s*pressure ([\d.]+)\s*altitude (-?[\d.]+)\s*humidity ([\d.]+)",
            low)
        if m:
            return self._apply_conditions_update(
                temp_f=float(m.group(1)), pressure_inhg=float(m.group(2)),
                altitude_ft=float(m.group(3)), humidity_pct=float(m.group(4)),
            )

        m = re.search(r"set wind ([\d.]+)\s*mph from ([\d.]+)\s*o.?clock", low)
        if m:
            return self._set_wind(float(m.group(1)), float(m.group(2)))

        # Fallback for natural phrasing that doesn't match "drop at X
        # yards" literally -- "set range for 400 yard and give solution",
        # "range 400 yards", "give me a solution for 400 yards" all land
        # here. A range number is by far the most common thing anyone
        # actually says, spoken or typed, so once nothing more specific
        # matched, any bare "<number> yd/yard/yards/yrd" is treated as a
        # drop-at-range request rather than forcing one exact phrasing.
        m = re.search(r"(\d+\.?\d*)\s*(?:yd|yrd|yard|yards)\b", low)
        if m:
            return self._drop_at(float(m.group(1)))

        # Last resort: every fast, free, exact pattern above missed.
        # Rather than keep discovering and patching one rigid regex at a
        # time as new phrasings turn up, hand this off to an LLM that
        # only decides *which* command was meant and *what* the
        # parameters are -- the actual math still runs through the same
        # deterministic functions every other path above uses.
        result = extract_intent(t, history=self._chat_history)
        if result is None:
            return "Didn't understand that. Type 'help' for supported commands."
        return self._dispatch_intent(*result, original_text=t)

    def _dispatch_intent(self, name: str, args: dict, original_text: str) -> str:
        # Two different failure modes need two different responses: the
        # LLM giving back malformed/missing arguments (its fault, a
        # generic "didn't understand" is honest) versus a well-formed,
        # correctly-understood request that just doesn't match anything
        # (e.g. a genuinely unknown load name) -- that one should surface
        # its real message, same as every deterministic regex path above
        # already does by not catching KeyError/ValueError at all.
        try:
            if name == "get_drop_at_range":
                range_yd = float(args["range_yd"])
            elif name == "switch_load":
                load_query = str(args["query"])
            elif name == "switch_rifle":
                rifle_query = str(args["query"])
                # Load info volunteered in the same breath as the switch
                # (see switch_rifle's own tool description in intent.py)
                # -- stripped of its "new_load_" prefix so the keys match
                # Load's own field names directly.
                new_load_fields = {
                    k[len("new_load_"):]: v for k, v in args.items()
                    if k.startswith("new_load_") and _is_real_value(v)
                }
            elif name == "get_minimum_spread_zero":
                max_range_yd = float(args["max_range_yd"])
            elif name == "solve_incline_angle":
                observed = float(args["observed_diff_clicks"])
                los = float(args["line_of_sight_distance_yd"])
                ref = float(args.get("reference_distance_yd") or 100.0)
            elif name == "set_wind":
                speed = float(args["speed_mph"])
                clock_hours = float(args["clock_hours"])
            elif name == "repeat_last_solution":
                part = str(args.get("part") or "solution")
            elif name == "delete_rifle":
                delete_query = str(args.get("query") or "")
            elif name not in ("set_conditions", "get_status", "converse", "update_rifle_field",
                               "start_load_setup", "start_rifle_setup", "start_calibration"):
                return "Didn't understand that. Type 'help' for supported commands."
        except (KeyError, ValueError, TypeError):
            return "Didn't understand that. Type 'help' for supported commands."

        if name == "get_drop_at_range":
            return self._drop_at(range_yd)
        if name == "switch_load":
            return self._switch_load(load_query)
        if name == "switch_rifle":
            return self._switch_rifle(rifle_query, new_load_fields)
        if name == "get_minimum_spread_zero":
            return self._minimum_spread_zero(max_range_yd)
        if name == "solve_incline_angle":
            return self._solve_angle(observed, los, ref)
        if name == "set_conditions":
            return self._apply_conditions_update(
                temp_f=args.get("temp_f"), pressure_inhg=args.get("pressure_inhg"),
                altitude_ft=args.get("altitude_ft"), humidity_pct=args.get("humidity_pct"),
            )
        if name == "set_wind":
            return self._set_wind(speed, clock_hours)
        if name == "repeat_last_solution":
            return self._repeat(part if part in ("elevation", "windage") else "solution")
        if name == "start_load_setup":
            return self._request_start_setup("load", original_text)
        if name == "start_rifle_setup":
            return self._request_start_setup("rifle", original_text)
        if name == "start_calibration":
            return self._request_start_calibration()
        if name == "update_rifle_field":
            return self._update_rifle_fields(args)
        if name == "delete_rifle":
            return self._request_delete_rifle_by_query(delete_query)
        if name == "converse":
            reply = str(args.get("reply") or "").strip()
            if not reply:
                return "Didn't understand that. Type 'help' for supported commands."
            self._remember_chat_turn(original_text, reply)
            return reply
        return self._status()  # only get_status left

    def _remember_chat_turn(self, user_text: str, reply: str) -> None:
        """Appends one exchange to the rolling conversational-memory
        buffer, trimmed to the last _CHAT_HISTORY_MAX_TURNS exchanges so
        the context sent to the LLM on every subsequent turn stays
        bounded rather than growing for the whole session."""
        self._chat_history.append({"role": "user", "content": user_text})
        self._chat_history.append({"role": "assistant", "content": reply})
        max_messages = _CHAT_HISTORY_MAX_TURNS * 2
        if len(self._chat_history) > max_messages:
            self._chat_history = self._chat_history[-max_messages:]

    # Setup-tone helpers: switching load/rifle/wind happens at the bench,
    # between strings -- not mid-shot -- so these read as a conversational
    # confirmation rather than the clipped numeric callouts _drop_at() and
    # _solve_angle() use for in-the-moment, live-fire solutions.

    def _switch_load(self, query: str) -> str:
        # Range retest, 2026-09-06: a flat "No load matching X" (the bare
        # KeyError text from find_load(), previously left to propagate
        # untouched) read as a flatly wrong, argumentative denial when
        # Rick asked for a load that genuinely existed -- he'd push back,
        # get an apology and a correct list of what's saved, then get the
        # SAME flat denial again on the identical next request. That
        # back-and-forth wasn't a personality/tone problem (this message
        # was never LLM-generated at all, just a terse Python string);
        # it's a UX gap -- the caller had to argue to get the same
        # information this could have offered immediately. Catching the
        # failure here and listing what's actually saved (ambiguous
        # candidates if there's a specific tie, every load on the rifle
        # if nothing matched at all) gives that helpful answer on the
        # first try instead of requiring a correction first.
        try:
            load = self.store.set_active_load(query)
        except KeyError:
            rifle = self.store.get_active_rifle()
            candidates = rifle.find_load_matches(query) or []
            options = candidates if candidates else list(rifle.loads.values())
            names = ", ".join(o.name for o in options)
            if not options:
                return f"No loads saved yet on the {rifle.name}."
            return (f"I'm not finding exactly one load matching '{query}' on the "
                    f"{rifle.name} -- here's what's saved: {names}. Which one do you want?")
        self.store.save()
        return f"Alright, you're on the {load.name} now -- {load.muzzle_velocity_fps:.0f} feet per second."

    def _switch_rifle(self, query: str, new_load_fields: dict | None = None) -> str:
        rifle = self.store.set_active_rifle(query)
        self.store.save()
        switched = f"Switched you over to the {rifle.name}."
        if new_load_fields:
            return f"{switched} {self._begin_setup_from_fields('load', new_load_fields)}"
        return switched

    def _set_wind(self, speed_mph: float, clock_hours: float) -> str:
        self.wind = WindCondition(speed_mph=speed_mph, clock_deg=clock_hours * 30.0)
        return f"Got it -- wind's {speed_mph:.0f} mph out of {clock_hours:g} o'clock."

    def _update_rifle_fields(self, fields: dict) -> str:
        """Edits fields on the ACTIVE rifle's existing saved profile --
        the one-shot counterpart to the guided setup interview, for
        correcting something already saved rather than adding a new
        rifle. Confirmed live (Addendum 29): before this existed, there
        was genuinely no voice command for this at all, so a spoken
        correction like "change the twist rate to 1:8" just declined
        with "didn't understand" -- which reads exactly like a save that
        silently failed, even though nothing was ever attempted."""
        valid = {f.name for f in dataclasses.fields(Rifle)} - {"name", "loads", "active_load_name"}
        updates = {k: v for k, v in fields.items() if k in valid and _is_real_value(v)}
        if not updates:
            return "Didn't catch a specific field to change there -- try again?"
        try:
            rifle = self.store.get_active_rifle()
            self.store.update_rifle_fields(rifle.name, **updates)
            self.store.save()
        except (KeyError, ValueError) as exc:
            return str(exc)
        def _describe(k: str, v) -> str:
            # "has suppressor True" reads wrong spoken back verbatim --
            # same reasoning as _extras_summary()'s handling of this field.
            if k == "has_suppressor":
                return "suppressed" if v else "no suppressor"
            return _speak_field(k, v)
        parts = ", ".join(_describe(k, v) for k, v in updates.items())
        return f"Updated -- {parts}."

    def _request_delete_rifle_by_query(self, query: str) -> str:
        """Voice-triggered rifle/pistol delete, matched fuzzily against
        caliber/barrel-length/manufacturer/name (find_rifle_matches()
        casts the same net switch_rifle already relies on) rather than
        requiring the exact stored name -- Rick may not recall or say
        the full name correctly, word order can flip ("5.7x28 11 inch"
        vs "11 inch 5.7x28"), and this is deliberately NOT solved by a
        separate tagging/color field: Rick's own convention is to
        disambiguate identical models (two S&W 5.7x28s, different
        barrels; two Taurus G2Cs, different colors) directly in the
        name, so the fuzzy match against name+caliber+barrel is the
        whole mechanism, not a workaround for a missing field.

        Always reads back the full matched name and asks for
        confirmation before deleting -- never guesses silently on an
        ambiguous match, never fails silently on no match. This is the
        standard shape for any voice-triggered delete now (2026-09-06,
        per Rick's own explicit read-back-to-confirm requirement).

        An empty/filler-only query (bare "delete it", "get rid of this
        rifle") falls back to the active rifle, same as before this
        query-aware version existed."""
        matches = self.store.find_rifle_matches(query)
        if matches is None:
            try:
                rifle = self.store.get_active_rifle()
            except ValueError as exc:
                return str(exc)
        elif not matches:
            return (f"I couldn't find anything matching '{query}'. "
                     f"You may need to check the name in Settings.")
        elif len(matches) > 1:
            names = [f"the {m.name}" for m in matches]
            listed = " or ".join(names) if len(names) == 2 else ", ".join(names[:-1]) + f", or {names[-1]}"
            return f"That could be {listed}. Which one do you mean?"
        else:
            rifle = matches[0]
        self._pending_delete = rifle.name
        self._pending_delete_at = time.time()
        return f"Delete the {rifle.name}, and all its loads? This can't be undone."

    def _handle_delete_confirm(self, text: str) -> str:
        low = text.lower().strip()
        name = self._pending_delete
        if _CONFIRM_DELETE_YES_RE.match(low) or (
            re.search(r"\b(correct|right)\b", low) and not _NEGATED_CONFIRM_RE.search(low)
        ):
            self._pending_delete = None
            self.store.delete_rifle(name)
            self.store.save()
            return f"Deleted the {name}."
        self._pending_delete = None
        return "Okay, keeping it."

    # Guided load/rifle setup -- a stateful, multi-turn interview. See
    # _SetupSession above: nothing gets written to the store until the
    # shooter explicitly confirms the read-back summary.

    def _start_setup(self, kind: str, original_text: str = "") -> str:
        # Pre-fill from the SAME utterance that triggered this, e.g.
        # "let's set up a new rifle, the 9mm PCC" or "new load, 110s at
        # 24 grains of Lil'Gun, zero at 50" -- extract_setup_fields()
        # already handles multi-field extraction fine (it's used every
        # turn once the interview is running), it just never used to run
        # on the triggering utterance itself, so anything said in that
        # same breath beyond "start a new X" was silently discarded and
        # asked for again a moment later. Confirmed live in testing: a
        # rifle name stated in the trigger utterance got re-asked for.
        fields = extract_setup_fields(original_text, kind, asking_about=None) if original_text else {}
        intro = "Alright, let's set up a new load." if kind == "load" else "Alright, let's set up a new rifle."
        return f"{intro} {self._begin_setup_from_fields(kind, fields)}"

    def _request_start_setup(self, kind: str, original_text: str = "") -> str:
        """Confirmation gate in front of _start_setup(), used only by the
        LLM-dispatched start_load_setup/start_rifle_setup tool calls
        (_dispatch_intent), never by handle()'s own deterministic "let's
        set up a new load/rifle" fast-path regex above, which is already
        unambiguous and starts immediately as before. Same reasoning and
        pattern as _request_start_calibration() (2026-09-06, Rick's own
        follow-up ask to extend that gate here too): a guided setup
        interview is just as modal as calibration once running, so an
        accidental LLM-dispatched entry is just as disruptive. Field
        pre-fill from original_text is deferred until confirmed, not run
        speculatively before knowing this is really wanted -- the text
        itself is stashed so it can still feed extract_setup_fields()
        once the shooter says yes, exactly as it would have immediately
        under the old unconfirmed path."""
        self._pending_setup_kind = kind
        self._pending_setup_text = original_text
        self._pending_setup_at = time.time()
        noun = "load" if kind == "load" else "rifle"
        return f"Set up a new {noun}? Say yes to begin."

    def _handle_setup_start_confirm(self, text: str) -> str:
        low = text.lower().strip()
        kind = self._pending_setup_kind
        original_text = self._pending_setup_text
        self._pending_setup_kind = None
        self._pending_setup_text = ""
        if _CONFIRM_YES_WORD_RE.match(low) or (
            re.search(r"\b(correct|right)\b", low) and not _NEGATED_CONFIRM_RE.search(low)
        ):
            return self._start_setup(kind, original_text)
        noun = "load" if kind == "load" else "rifle"
        return f"Okay, not setting up a new {noun}."

    def _begin_setup_from_fields(self, kind: str, fields: dict) -> str:
        """Starts a new load/rifle setup pre-filled with already-known
        field values, and returns the first prompt for whatever's still
        actually missing -- or the confirmation summary if nothing is.
        Shared by _start_setup() (fields volunteered in the utterance
        that triggered the setup) and _switch_rifle() (a plain rifle
        switch that also describes a brand new load in the same breath,
        e.g. "switching to the 300 blackout, first load is the 110s at
        24 grains of Lil Gun, zero at 50" -- confirmed live that this
        used to just switch rifles and silently drop the load info)."""
        self._setup = _SetupSession(kind)
        valid = {f.name for f in dataclasses.fields(Load if kind == "load" else Rifle)}
        if fields:
            self._setup.draft.update({k: v for k, v in fields.items() if k in valid and _is_real_value(v)})

        missing = self._next_field_to_ask()
        if missing is None:
            self._setup.confirming = True
            return self._setup_summary()
        if self._setup.draft:
            captured = [str(v) for v in self._setup.draft.values()]
            return f"Got it -- {', '.join(captured)}. {self._prompt_for(missing)}"
        return self._prompt_for(missing)

    def _next_field_to_ask(self) -> str | None:
        """Required fields first (can't be skipped), then every remaining
        optional field the manual Setup form has, in order -- skipped or
        already-filled ones are passed over. None once both are exhausted,
        meaning it's time to read back the summary."""
        required = _LOAD_REQUIRED if self._setup.kind == "load" else _RIFLE_REQUIRED
        for field in required:
            if self._setup.draft.get(field) in (None, ""):
                return field
        extras = (_LOAD_EXTRA_FIELDS if self._setup.kind == "load"
                  else _rifle_extra_fields(self._setup.draft.get("optic_type", ""),
                                            self._setup.draft.get("has_suppressor", False)))
        for field in extras:
            if field in self._setup.skipped:
                continue
            if self._setup.draft.get(field) in (None, ""):
                return field
        return None

    def _prompt_for(self, field: str) -> str:
        prompts = _LOAD_PROMPTS if self._setup.kind == "load" else _RIFLE_PROMPTS
        extra_prompts = _LOAD_EXTRA_PROMPTS if self._setup.kind == "load" else _RIFLE_EXTRA_PROMPTS
        return prompts.get(field) or extra_prompts[field]

    def _extras_summary(self) -> str:
        d = self._setup.draft
        extra_fields = (_LOAD_EXTRA_FIELDS if self._setup.kind == "load"
                        else _rifle_extra_fields(d.get("optic_type", ""), d.get("has_suppressor", False)))
        parts = []
        for f in extra_fields:
            v = d.get(f)
            if f == "has_suppressor":
                # "True"/"False" spoken back verbatim reads wrong -- and a
                # plain "no suppressor" isn't worth calling out, same as
                # any other unset/empty field here.
                if v:
                    parts.append("suppressed")
                continue
            if v not in (None, ""):
                parts.append(_speak_field(f, v))
        return f" Also got: {', '.join(parts)}." if parts else ""

    def _setup_summary(self) -> str:
        d = self._setup.draft
        if self._setup.kind == "load":
            base = (f"Here's what I've got -- {d['name']}: {d['bullet_weight_gr']:.0f} grain, "
                    f"ballistic coefficient {d['bc']}, {d['drag_model']} drag model, "
                    f"{d['muzzle_velocity_fps']:.0f} feet per second, "
                    f"zeroed at {d['zero_distance_yd']:.0f} yards.")
        else:
            base = f"Here's what I've got -- {d['name']}, scope height {d['scope_height_in']:g} inches."
        return f"{base}{self._extras_summary()} Sound right?"

    def _finalize_setup(self) -> str:
        d = self._setup.draft
        kind = self._setup.kind
        try:
            if kind == "load":
                valid = {f.name for f in dataclasses.fields(Load)}
                load = Load(**{k: v for k, v in d.items() if k in valid})
                rifle = self.store.get_active_rifle()
                rifle.add_load(load, make_active=True)
                self.store.save()
                self._setup = None
                return f"Saved -- you're on the {load.name} now."
            valid = {f.name for f in dataclasses.fields(Rifle)}
            rifle = Rifle(**{k: v for k, v in d.items() if k in valid})
            self.store.add_rifle(rifle, make_active=True)
            self.store.save()
            self._setup = None
            return f"Saved -- switched you to the {rifle.name}."
        except (ValueError, TypeError) as exc:
            # Deliberately don't clear self._setup here -- the draft is
            # still good except for whatever's wrong, so the interview
            # stays open and the next utterance is treated as a
            # correction rather than forcing a restart from scratch.
            return f"That didn't work -- {exc}. What should I fix?"

    def _handle_setup_turn(self, text: str) -> str:
        low = text.lower().strip()

        if re.match(r"^(cancel|never ?mind|stop|abort|forget it|quit|exit)\b", low):
            kind = self._setup.kind
            self._setup = None
            return f"Okay, scrapped the new {kind}. Nothing saved."

        if self._setup.confirming:
            no_m = _CONFIRM_NO_RE.match(low)
            if no_m:
                self._setup.confirming = False
                # No correction stated in the same breath -- ask, rather than
                # guess. If they *did* say more ("no, make the zero 50
                # yards"), fall through below and extract from the full
                # utterance instead of just acknowledging and losing it.
                if not no_m.group(3).strip():
                    return "Okay, what needs to change?"
            elif _NEGATED_CONFIRM_RE.search(low):
                # Rejection phrased mid-sentence rather than as the first
                # word ("that's not correct") -- the correction text (if
                # any) isn't cleanly separable here, so ask rather than
                # guess, same as the bare "no" case above.
                self._setup.confirming = False
                return "Okay, what needs to change?"
            elif _CONFIRM_YES_WORD_RE.match(low) or re.search(r"\b(correct|right)\b", low):
                return self._finalize_setup()

        # "Skip" only applies to whichever optional field is currently
        # being asked -- required fields can't be skipped, since there's
        # nothing to save without them.
        current_field = self._next_field_to_ask()
        required = _LOAD_REQUIRED if self._setup.kind == "load" else _RIFLE_REQUIRED
        if current_field is not None and _SKIP_RE.match(low):
            if current_field in required:
                return f"I need that one to save this {self._setup.kind} -- {self._prompt_for(current_field)}"
            self._setup.skipped.add(current_field)
            self._setup.failed_attempts = 0
            nxt = self._next_field_to_ask()
            if nxt is None:
                self._setup.confirming = True
                return self._setup_summary()
            return self._prompt_for(nxt)

        fields = extract_setup_fields(text, self._setup.kind, asking_about=current_field)
        valid = {f.name for f in dataclasses.fields(Load if self._setup.kind == "load" else Rifle)}
        before = dict(self._setup.draft)
        if fields:
            self._setup.draft.update({k: v for k, v in fields.items() if k in valid and _is_real_value(v)})

        # "No progress" -- not just "fields came back empty" -- is the real
        # failure signal: a correction that overwrites an existing field
        # with the same key ("no, actually zero it at 50 yards") must not
        # count as a failure just because the draft's size didn't grow.
        if self._setup.draft == before:
            self._setup.failed_attempts += 1
            if self._setup.failed_attempts >= _MAX_FAILED_ATTEMPTS:
                kind = self._setup.kind
                self._setup = None
                return (f"Having trouble understanding you -- stopping the {kind} setup for now. "
                        f"Say 'new {kind}' when you want to try again.")
            return "Didn't catch any details there -- try again?"
        self._setup.failed_attempts = 0

        missing = self._next_field_to_ask()
        if missing:
            self._setup.confirming = False
            # Real progress happened, but not on the specific field just
            # asked about (e.g. asked for a name, got a caliber instead) --
            # confirmed live (Addendum 27): silently re-asking the exact
            # same question reads as "didn't understand at all" even
            # though something real was captured. Naming what was heard
            # makes clear the utterance registered.
            if current_field is not None and self._setup.draft.get(current_field) in (None, ""):
                newly_captured = [str(v) for k, v in self._setup.draft.items() if k not in before]
                if newly_captured:
                    return f"Got it -- {', '.join(newly_captured)}. {self._prompt_for(missing)}"
            return self._prompt_for(missing)

        self._setup.confirming = True
        return self._setup_summary()

    # Chronograph calibration -- live-fire tone throughout (terse, numeric):
    # this runs mid-string, at the line, same as _drop_at()/_solve_angle().

    def _start_calibration(self) -> str:
        try:
            _, rifle, load = self.solver()
        except ValueError as exc:
            return str(exc)
        self._calibration = _CalibrationSession(rifle.name, load.name)
        return (f"Calibration started, {load.name}. Book velocity {load.muzzle_velocity_fps:.0f}. "
                f"Read me shots.")

    def _request_start_calibration(self) -> str:
        """Confirmation gate in front of _start_calibration(), used only
        by the LLM-dispatched start_calibration tool call (_dispatch_intent),
        never by handle()'s own deterministic "start calibration"/"chrono
        this" fast-path regex above, which is already unambiguous and
        starts immediately as before. Added 2026-09-06 after a wind-
        adjustment utterance mid-solution was confirmed live to
        occasionally get classified as start_calibration instead of
        set_wind -- tool_choice="auto" lets the model choose ANY tool
        for ANY utterance, and once calibration actually starts it's
        modal (every later utterance becomes a shot reading unless it
        hits an exact interrupt word), which is a much more disruptive
        failure than most other wrong-tool mistakes this session turned
        up. Asking first costs one extra turn on a genuine calibration
        request but costs nothing on a false one -- "no"/anything else
        just cancels with no session ever created."""
        try:
            _, rifle, load = self.solver()
        except ValueError as exc:
            return str(exc)
        self._pending_calibration_start = True
        self._pending_calibration_start_at = time.time()
        return f"Start a calibration session for the {load.name}? Say yes to begin."

    def _handle_calibration_start_confirm(self, text: str) -> str:
        low = text.lower().strip()
        self._pending_calibration_start = False
        if _CONFIRM_YES_WORD_RE.match(low) or (
            re.search(r"\b(correct|right)\b", low) and not _NEGATED_CONFIRM_RE.search(low)
        ):
            return self._start_calibration()
        return "Okay, not starting calibration."

    def _calibration_stats(self) -> tuple[float, float]:
        shots = self._calibration.shots
        avg = sum(shots) / len(shots)
        spread = max(shots) - min(shots) if len(shots) > 1 else 0.0
        return avg, spread

    def _record_shot(self, shot_fps: float) -> str:
        prior = self._calibration.shots
        outlier_note = ""
        # Flag only a genuinely dramatic reading, not ordinary shot-to-shot
        # spread: needs both several fps of statistical support (>2 stdev
        # of the shots so far) AND a real-world-meaningful gap (>40 fps) --
        # a tiny sample's stdev is too noisy to trust alone, and 2 stdev of
        # a very tight string can be just a few fps.
        if len(prior) >= 3:
            mean = sum(prior) / len(prior)
            stdev = (sum((s - mean) ** 2 for s in prior) / len(prior)) ** 0.5
            deviation = abs(shot_fps - mean)
            if deviation > 40 and deviation > 2 * stdev:
                outlier_note = " -- that one's an outlier"
        self._calibration.shots.append(shot_fps)
        self._calibration.failed_attempts = 0
        avg, _ = self._calibration_stats()
        return f"Shot {len(self._calibration.shots)}, {shot_fps:.0f}{outlier_note}. Average {avg:.0f}."

    def _finalize_calibration(self) -> str:
        avg, spread = self._calibration_stats()
        n = len(self._calibration.shots)
        load = self.store.update_load_velocity(
            self._calibration.rifle_name, self._calibration.load_name, avg,
        )
        chrono_note = f"Chrono-verified: {n} shots, avg {avg:.0f} fps, spread {spread:.0f} fps."
        load.notes = f"{load.notes} {chrono_note}".strip() if load.notes else chrono_note
        self.store.save()
        self._calibration = None
        return f"Saved -- {load.name} is now {avg:.0f} feet per second."

    def _handle_calibration_turn(self, text: str) -> str:
        low = text.lower().strip()

        if re.match(r"^(cancel|never ?mind|abort)\b", low):
            self._calibration = None
            return "Calibration cancelled. Nothing saved."

        if self._calibration.confirming:
            # Same widened confirmation matching as setup's confirming
            # block (Addendum 28) -- the same class of bug (narrow
            # anchored-only patterns missing natural phrasing like "that
            # is correct") applies here too, fixed proactively rather
            # than waiting for it to be hit live separately.
            if _CONFIRM_NO_RE.match(low) or re.match(r"^not now\b", low) or _NEGATED_CONFIRM_RE.search(low):
                self._calibration = None
                return "Discarded. Nothing saved."
            if _CONFIRM_YES_WORD_RE.match(low) or re.search(r"\b(correct|right)\b", low):
                return self._finalize_calibration()
            self._calibration.confirming = False
            # Falls through -- most likely one more shot came in after
            # "end calibration" was said a beat too early.

        if re.match(r"^(end calibration|that.s it|we.re done|finished?|finish( calibration)?)\b", low):
            return self._request_end_calibration()

        if re.search(r"\baverage\b", low):
            self._calibration.failed_attempts = 0
            if not self._calibration.shots:
                return "No shots recorded yet."
            avg, spread = self._calibration_stats()
            return f"{len(self._calibration.shots)} shots, average {avg:.0f}, spread {spread:.0f}."

        if re.search(r"\b(discard|throw out|toss|scratch that|bad (reading|shot))\b", low):
            return self._discard_last_shot()

        m = re.search(r"(\d{3,5}(?:\.\d+)?)", low)
        if m:
            return self._record_shot(float(m.group(1)))

        # Live-tested (2026-09-05): natural ways of signaling "done reading
        # shots" vary far more than the anchored regex above ever will
        # ("that's ten shots", "I think that's good", "that's enough") --
        # every one of those used to land here and just say "didn't catch
        # a number," identically to genuine noise/silence. Same fast-path-
        # then-LLM-fallback pattern extract_intent() already uses elsewhere,
        # applied to the one modal flow that didn't have it yet.
        classification = classify_calibration_turn(text)
        if classification == "end_calibration":
            return self._request_end_calibration()
        if classification == "discard_last_shot":
            return self._discard_last_shot()
        if classification == "cancel_calibration":
            self._calibration = None
            return "Calibration cancelled. Nothing saved."

        # "unclear", or None on an outright API failure -- same "give up
        # after repeated genuine failures" backstop as before.
        self._calibration.failed_attempts += 1
        if self._calibration.failed_attempts >= _MAX_FAILED_ATTEMPTS:
            self._calibration = None
            # Wording note (2026-09-05): "hearing shots" reads as acoustic
            # gunshot detection, which doesn't exist -- this only ever
            # means "couldn't parse what you said as a number, a control
            # phrase, or an end-of-string signal." Reworded after Rick
            # asked, live, whether real audio shot-detection was involved.
            return "Having trouble understanding the shot readings -- calibration stopped. Say 'start calibration' to try again."
        return "Didn't catch a number there -- try again?"

    def _request_end_calibration(self) -> str:
        if not self._calibration.shots:
            return "No shots recorded yet -- read me at least one first."
        self._calibration.confirming = True
        avg, spread = self._calibration_stats()
        return (f"{len(self._calibration.shots)} shots, average {avg:.0f}, spread {spread:.0f}. "
                f"Save as the new velocity for the {self._calibration.load_name}?")

    def _discard_last_shot(self) -> str:
        self._calibration.failed_attempts = 0
        if not self._calibration.shots:
            return "No shots to discard yet."
        removed = self._calibration.shots.pop()
        if not self._calibration.shots:
            return f"Tossed {removed:.0f}. No shots left."
        avg, _ = self._calibration_stats()
        return f"Tossed {removed:.0f}. Average {avg:.0f}."

    def _apply_conditions_update(
        self, temp_f: float | None = None, pressure_inhg: float | None = None,
        altitude_ft: float | None = None, humidity_pct: float | None = None,
    ) -> str:
        """Merges into the currently-set conditions rather than requiring
        every field restated -- "it's about 90 out" should work without
        also having to repeat pressure/altitude/humidity that haven't
        changed."""
        self.atmosphere = AtmosphereConditions(
            temp_f=temp_f if temp_f is not None else self.atmosphere.temp_f,
            pressure_inhg=pressure_inhg if pressure_inhg is not None else self.atmosphere.pressure_inhg,
            altitude_ft=altitude_ft if altitude_ft is not None else self.atmosphere.altitude_ft,
            humidity_pct=humidity_pct if humidity_pct is not None else self.atmosphere.humidity_pct,
        )
        return (f"Conditions updated -- {self.atmosphere.temp_f:.0f} degrees, "
                f"{self.atmosphere.pressure_inhg:.2f} inches of mercury, "
                f"{self.atmosphere.humidity_pct:.0f} percent humidity, "
                f"{self.atmosphere.altitude_ft:.0f} feet.")

    def _status(self) -> str:
        try:
            solver, rifle, load = self.solver()
        except ValueError as exc:
            return str(exc)
        # Range retest, 2026-09-06: "what rifle/load are we using" was read
        # back at full conversational speed -- fine for small talk, too
        # fast to follow for a dense rifle/load/conditions dump. Same
        # readout pace as a drop-at-range solution, not a general-chat one.
        self._last_reply_is_readout = True
        return (
            f"Rifle: {rifle.name} (scope height {rifle.scope_height_in}in, "
            f"click value {rifle.click_value_mrad} mrad)\n"
            f"Load: {load.name} -- {load.bullet_weight_gr}gr, BC {load.bc} {load.drag_model}, "
            f"{load.muzzle_velocity_fps:.0f} fps, zero {load.zero_distance_yd}yd\n"
            f"Conditions: {self.atmosphere.temp_f:.0f}F, {self.atmosphere.pressure_inhg:.2f}inHg, "
            f"{self.atmosphere.humidity_pct:.0f}% RH, {self.atmosphere.altitude_ft:.0f}ft\n"
            f"Wind: {self.wind.speed_mph:.0f} mph @ {self.wind.clock_deg / 30:g} o'clock"
        )

    def _drop_at(self, range_yd: float) -> str:
        solver, rifle, load = self.solver()
        point = solver.at_range(load.zero_distance_yd, range_yd)
        r = report_for_point(point, rifle.click_value_mrad)

        # Speak in whatever unit the rifle's own turrets/reticle actually
        # use -- not inches, not clicks, not "give three numbers and let
        # the shooter do the conversion in their head at the line." Rick's
        # feedback after the first live wake-word test: too much at once,
        # wrong units for what he actually dials.
        if rifle.reticle_unit == "MOA":
            elev_val, wind_val, unit_word = r.drop_moa, r.windage_moa, "M O A"
        else:
            elev_val, wind_val, unit_word = r.drop_mrad, r.windage_mrad, "mils"

        elev_dir = "up" if elev_val >= 0 else "down"
        wind_dir = "left" if wind_val >= 0 else "right"

        # Remembered so "repeat windage/elevation/solution" can re-speak
        # this without recalculating -- see _repeat().
        self._last_solution = {
            "range_yd": r.range_yd, "elev_dir": elev_dir, "elev_val": abs(elev_val),
            "wind_dir": wind_dir, "wind_val": abs(wind_val), "unit_word": unit_word,
        }

        # Two full sentences, not one comma-separated run-on: the period
        # gives TTS a natural pause between elevation and windage instead
        # of both numbers running together.
        self._last_reply_is_readout = True
        return (f"Solution, {r.range_yd:.0f} yards. "
                f"Elevation, {elev_dir} {abs(elev_val):.1f} {unit_word}. "
                f"Windage, {wind_dir} {abs(wind_val):.1f} {unit_word}.")

    def _repeat(self, part: str) -> str:
        s = self._last_solution
        if s is None:
            return "No solution given yet -- ask for a range first."
        self._last_reply_is_readout = True
        if part == "elevation":
            return f"Elevation, {s['elev_dir']} {s['elev_val']:.1f} {s['unit_word']}."
        if part == "windage":
            return f"Windage, {s['wind_dir']} {s['wind_val']:.1f} {s['unit_word']}."
        return (f"Solution, {s['range_yd']:.0f} yards. "
                f"Elevation, {s['elev_dir']} {s['elev_val']:.1f} {s['unit_word']}. "
                f"Windage, {s['wind_dir']} {s['wind_val']:.1f} {s['unit_word']}.")

    def _table(self, max_range_yd: float, step_yd: float) -> str:
        solver, rifle, load = self.solver()
        points = solver.drop_table(load.zero_distance_yd, max_range_yd, step_yd)
        reports = report_table(points, rifle.click_value_mrad)
        self._last_reply_is_readout = True
        return format_table_text(reports)

    def _minimum_spread_zero(self, max_range_yd: float) -> str:
        solver, rifle, load = self.solver()
        result = find_minimum_spread_zero(solver, max_range_yd)
        self._last_reply_is_readout = True
        return (f"Your {result.zero_distance_yd:.0f} yard zero minimizes spread out to "
                f"{max_range_yd:.0f} yards -- peak rise {result.max_height_in:.1f} inches, "
                f"terminal drop {result.min_height_in:.1f} inches, "
                f"total spread {result.spread_in:.1f} inches.")

    def _solve_angle(self, observed_clicks: float, los_yd: float, ref_yd: float) -> str:
        solver, rifle, load = self.solver()
        try:
            result = solve_incline_angle(
                solver, load.zero_distance_yd, ref_yd, los_yd,
                observed_clicks, rifle.click_value_mrad,
            )
        except ValueError as exc:
            return str(exc)
        holdover = result.corrected_holdover_clicks(solver, load.zero_distance_yd, los_yd, rifle.click_value_mrad)
        self._last_reply_is_readout = True
        return (f"Angle confirmed, {result.angle_deg:.1f} degrees. "
                f"Shoot-to distance {result.shoot_to_distance_yd:.0f} yards. "
                f"Corrected holdover {holdover:.1f} clicks. Solution locked.")


def main() -> None:
    store = ProfileStore()
    if not store.rifles:
        bootstrap_default_profile(store)
        print("No profiles found -- loaded the AR-15 20in Faxon / 77gr SMK test case.")

    cli = BallisticaCLI(store)
    print("Ballistica. Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        try:
            reply = cli.handle(line)
        except SystemExit:
            break
        if reply:
            print(reply)


if __name__ == "__main__":
    sys.exit(main() or 0)
