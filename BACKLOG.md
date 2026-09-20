# Ballistica Backlog

Future items that are real and worth keeping, but explicitly not being built
now. Each entry: what, why, status/sequencing, owning lenses. Move an item
out of this file into active work (or into an addendum) when it's actually
scoped, don't let it just accumulate here indefinitely.

---

## App icon to match the male/female visual direction

**Raised:** Rick, 2026-08-23 (alongside the now-built selectable voice
persona item -- see MULTI_TENANCY_DESIGN.md §30 -- which this was
originally a sub-item of).

**What:** Update the app icon/favicon to match the man-and-woman
range-partner visual direction now that both personas have a real,
functional role (Mrs. Ballistica as the default/female voice, Mr.
Ballistica as the male voice option, confirmed 2026-09-18).

**Constraint flagged 2026-08-23, still true:** a realistic photo
generally doesn't read well as a small app icon -- favicons and
home-screen icons render at 16-48px, where photographic detail turns
to mush and the shape has to be recognizable at a glance. The photo
works well for onboarding/landing-page use exactly as originally
framed; the icon itself likely wants a simpler, high-contrast
derivative (a mark, a silhouette, a cropped/stylized detail) rather
than the full photo shrunk down.

**Status:** Backlog. Not urgent, not blocking -- noted so the actual
icon work starts from the right brief rather than producing something
that looks fine at full size and unreadable at icon size.

**Owning lenses when scoped:** Build (the actual asset work),
Marketing (visual direction).

(The spreadsheet/CSV import item raised 2026-08-23 has been built --
see MULTI_TENANCY_DESIGN.md §24 and ballistica/import_export.py.
Selectable voice persona, raised the same day, has also been built --
see §30. Both moved out of backlog per this file's own convention.)

---

(The seed bullet/BC/factory-load dataset raised 2026-08-23 has been
built — see MULTI_TENANCY_DESIGN.md §25 and ballistica/bullet_
reference.py. The GPS/METAR weather auto-fill item logged 2026-08-28
has also been built — see MULTI_TENANCY_DESIGN.md and ballistica/
weather.py. Both moved out of backlog per this file's own convention.)

---

## Session Mode / Relaxed Mode — ambient, continuous-listening range logging

**Raised:** Rick, 2026-09-05, after a real range session (six rifles, multiple
loads, three calibers: .223 Wylde, .300 Blackout, 9mm) where Claude in voice
mode on his phone stood in as the session logger — tracking rifle, load, and
chronograph velocities from free-flowing narration with no command format,
because Ballistica itself had no discoverable path to do this live.

**What:** An explicit, user-selected "Session Mode"/"Relaxed Mode" (not
always-on ambient listening) that continuously extracts rifle/load/velocity/
corrections from natural narration during a range session, only speaking up
for clarification or confirmation rather than replying to every utterance.
Paired with: default behavior stays command-response (to avoid picking up
other range-goers' conversations); a manual mute control (physical/one-tap,
not an AI judgment call) that gates audio capture itself, not just spoken
replies, so private side-conversation doesn't burn STT/LLM cost.

**Feasibility findings (scoped 2026-09-05, Build lens, grounded in real code):**
- There's already a close analog: the `start calibration` flow
  (`ballistica/cli.py` `_handle_calibration_turn`) logs loose numeric shot
  readings with no rigid per-shot phrasing, computes running average, flags
  outliers — proving the "parse loose speech into structured data" half
  already works, just scoped to one rifle+load at a time.
- The real gap is **context switching**: today, moving to a new rifle or load
  requires an exact command (`switch rifle to <name>`, `switch to <load>`)
  before loose narration resumes. Session Mode needs that switch detected
  from narration itself.
- Architecture today is one blocking request/response per complete utterance
  (phone sends finished clip → Whisper → `BallisticaCLI.handle()` classifies
  and dispatches once → one reply). No persistent session state spans
  multiple exchanges except the existing modal flows (setup interview,
  calibration), which are themselves exact-turn state machines, not open
  narration.
- Three real pipeline changes needed: (1) continuous/streaming STT with
  voice-activity detection instead of clip-per-utterance — no prior art in
  the codebase for this piece; (2) a running session-state object tracking
  current rifle/load in focus that updates from narration, extending the
  existing LLM tool-use extraction pattern in `ballistica/intent.py` to run
  per speech segment against accumulating context rather than per isolated
  command; (3) a confidence-gated "worth interrupting for" policy on the
  reply side, since Session Mode is silent-by-default rather than
  reply-every-utterance.

**Cost estimate — computed 2026-09-05 against real session numbers.** Rick
supplied: 45-75 min mic-open time, 5 rifles fired (a 6th present but never
fired for record), ~10 rifle/load combos across those 5, 54 total shot
readings. Priced against confirmed current rates (Whisper STT $0.006/min,
Claude Haiku 4.5 $1/$5 per MTok in/out, OpenAI TTS $15/M chars — the same
three services already wired into Ballistica):
- **STT:** $0.27-$0.45 for the whole session (45-75 min × $0.006/min) —
  cheap enough that VAD/silence-stripping doesn't matter for *cost*, only
  for not falsely triggering the extraction layer on noise.
- **LLM extraction:** ~90-200 segments estimated (54 shot readings + ~30
  setup-narration segments across 10 combos + a 30-50% buffer for
  non-productive narration that still needs a "nothing to log" classification
  pass) × ~$0.0017/call (~1200 in/100 out tokens) = **$0.18-$0.40**.
- **TTS:** confirmation/clarification replies only under the silent-by-
  default design — 40-100 short replies ≈ **$0.03-$0.30**.
- **Total: ~$0.50-$1.20 per session** (under $1.50 with margin). Marginal
  per-session API cost is a non-issue at this volume; the real cost of this
  feature is the one-time engineering build (streaming STT/VAD, session-state
  tracker, confidence-gated reply policy — see pipeline-gap analysis above),
  not ongoing API spend.

**Correction, 2026-09-19 (measured against the live API, see COST_MODEL.md):**
the estimate above assumed ~1,200 input tokens per model call; the real
figure is ~6,750 (system prompt + tool definitions re-sent on every call).
The LLM line was underestimated 2-5x and the STT line overestimated 4-6x
(only speech segments are sent, not open-mic minutes); the total happened to
land near the old range. Corrected: **~$1.06 per Session Mode hour uncached,
~$0.43 with prompt caching** (built 2026-09-19, ~80% off each model call). The
conclusion that per-session API cost is small still holds; the composition
did not.

**Build-effort sizing — done 2026-09-05, corrects an error in the earlier
pipeline-gap analysis above.** That analysis said continuous listening had
"no prior art in the codebase" — wrong; `ballistica/web/index.html`'s
`recordCommand()` already has a live-tuned, energy-based VAD (speech onset
detection, silence-based end-of-utterance, min/max duration bounds) that's
been through multiple real fixes (Addendum 14 near-empty-clip hallucination,
Addendum 27 mid-sentence-pause truncation, Addendum 30 mic-lifecycle/
Bluetooth-renegotiation), plus an already-working continuous background
wake-word listener. Relative sizing per component:
- **Continuous segmented capture — Small.** Loop the existing proven VAD
  capture for the whole session instead of exiting after one command; no
  wake word needed per utterance. Reuses tested code as-is.
- **Session-state tracker + open-ended entity extraction — Medium-Large,
  the real engineering investment and the real risk.** No existing analog:
  today's setup-interview and calibration flows are stateful but expect a
  fixed field in a fixed order, whereas this needs any field in any order,
  implicit rifle/load context-switch detection from narration alone, fuzzy
  matching against existing rifles/loads vs. flagging new ones, and generic
  correction-handling (today's "discard that" only works for calibration
  shots).
- **Confidence-gated reply policy — Small.** Conditional on a confidence
  signal the extraction layer has to produce anyway.
- **Relaxed Mode toggle + hard mute — Small-Medium.** Mute likely reuses
  the existing enable/disable voice mic-lifecycle code (pause without full
  teardown) plus a UI control.

No dollar figure given for engineering hours — no real basis exists here for
pricing that, and inventing one wouldn't be an honest estimate. Grounded
instead in this codebase's own real history: every voice feature shipped so
far (wake-word timing, mid-sentence pauses, mic lifecycle, setup-interview
phrasing, calibration confirmation) needed a live-range test-and-fix round
after the initial build. Session Mode's extraction piece is structurally
more ambiguous than any prior feature, so budget for more than one live-range
iteration before it's reliable, not a single build-and-ship. Suggested
sequencing: build components 1 and 3 first (small, low-risk, reuse tested
code), then 2 (the real investment), then range-test.

**Fourth item — RESOLVED, no new build needed.** The existing CSV import
pipeline (`ballistica/import_export.py`) already fuzzy-matches the exact
column set Rick used today (Gun Name/Rifle Name, Barrel Length, Twist Rate,
Bullet Weight, Bullet Type, BC, Drag Model, Powder, Powder Charge, Muzzle
Velocity, Zero Distance, Temperature) via its existing header-alias
matching. Rick reconstructed today's freeform range notes into that
spreadsheet format and ran it through the existing desktop import today —
5 rifles touched, 0 rows failed. No backlog item remains here; the
reconstructed spreadsheet stands as the reference template for converting
future freeform-narrated sessions the same way.

**Status correction, 2026-09-19 -- this entry was stale.** Everything
above still describes the real remaining scope correctly, but the file
never got updated to reflect that **component 1 actually shipped the
same day it was scoped** (2026-09-05, commit `a2ffdf3`, live-voice
tested, 152 tests passing at the time) -- this file kept reading as if
nothing had been built at all. Corrected component-by-component status:
- **Component 1 (continuous capture) -- DONE.** `runSessionModeLoop()`
  in `ballistica/web/index.html`: loops the existing tested VAD capture
  for a whole session, no wake word between commands, stays silent on
  ordinary quiet stretches. Session toggle button, ack/sign-off phrases,
  spoken "end session" phrase to drop back to wake-word mode -- all
  real and live-tested.
  - **2026-09-19 addition:** start was tap-only until today. Rick's
    call: should be startable by voice too ("the whole idea is about
    talking to the app"), not just the button. Added
    `SESSION_MODE_START_RE`, checked in the same place
    `SESSION_MODE_END_RE` already was, wired into `handleWakeWord()` so
    a spoken "start session mode" (or "let's go hands free," etc.) from
    ordinary wake-word listening hands off into the same
    `runSessionModeLoop()` the button triggers. Fixed a small real bug
    found while wiring this in: both phrases used to be checked AFTER
    the backend `/v2/voice/query` round-trip, so saying "end session"
    got sent to the conversational layer as a real query first (an
    improvised reply got spoken) before the loop noticed the phrase and
    exited -- both phrases are now caught before the backend call,
    for both start and end. Verified: regex tested against real
    start/end phrases and realistic ballistics commands (no false
    positives, no cross-contamination between the two patterns) and
    against the actual literals served by the running app, not a
    reimplementation; JS syntax-checked; app loads clean, no console
    errors. Not yet live-mic tested (same caveat as every voice feature
    -- needs a real range/room test).
- **Component 2 (session-state tracker) -- DONE, 2026-09-19.** Full
  design record: MULTI_TENANCY_DESIGN.md §31. While Session Mode is on,
  narration is understood without a command: "okay now the SBR with the
  110 Lil Gun, 1150, 1162" switches rifle/load and logs the readings;
  "scratch that" / "no that was 1152" corrects the last one. One new
  LLM tool (`log_session_observation`, only offered in Session Mode)
  REPORTS what was said; deterministic code in `cli.py` decides
  everything that matters -- unique-match-only rifle/load resolution
  (else it asks), a 400-5000 fps plausibility gate, and what gets
  logged. Readings live in a `_SessionLog` (round-tripped through the
  existing conversation_state JSON, no migration), and nothing ever
  touches a saved load until an explicit, confirmed "save velocities"
  (>= 3 readings per load; same chrono-verified note calibration
  writes). New `POST /v2/session/end` returns a spoken recap. Two
  silent-data-corruption hazards found and closed while building
  (fast paths -- "chrono says 1150" started a modal calibration,
  "again, 1162" re-read the last solution -- stealing narration; and
  readings landing on a rifle's last-used load after a switch, now
  asked about instead of guessed) -- both regression-tested. A third
  turned up in the end-to-end run: "that FIRST one was 1152" got
  applied to the LAST reading despite a prompt telling the model not
  to; now refused in code. Only the most recent reading can be
  corrected -- editing an earlier one is NOT built.
  Verified: 33 new tests (LLM stubbed, pinning the deterministic
  side), full suite passing, the whole flow through the real HTTP
  endpoints (real Supabase, real model, separate stateless requests), and the REAL model exercised against
  realistic narration -- switch+readings, spoken-word numbers, chrono
  narration, discard/replace all route to the tracker; solutions,
  wind, and load switches still go to their own tools; chatter and a
  passing mention of another rifle stay conversation. (A real gap that
  check found and fixed: a bare "scratch that" had nothing to refer to
  until the model was told the last logged reading.) **Not yet
  live-mic tested at a range**, same caveat as every voice feature --
  the real risks left are STT mishearing numbers/names in noise, which
  no amount of text-level testing can characterize.
- **Component 3 (confidence-gated reply policy) -- NOT built, and now
  unblocked.** Every observation still gets a short spoken readback
  ("1150, shot 3."). That's deliberately kept for now as the
  STT-error check on numbers rather than going silent by default --
  worth deciding what's actually worth interrupting for from real range
  transcripts (the per-turn debug log already captures them), not
  guessing up front.
- **Component 4 (Relaxed Mode toggle + hard mute) -- DONE, 2026-09-19.**
  The on/off toggle existed already (component 1's button + spoken
  end-phrase). The actual mute -- pausing capture in place without
  ending the session, reachable by voice OR tap per Rick's call --
  is now built: `SESSION_MODE_MUTE_RE`/`SESSION_MODE_UNMUTE_RE` in
  `ballistica/web/index.html`, a `#muteBtn` enabled only during Session
  Mode, and `startMuteListening()`/`stopMuteListening()` -- a second
  free, on-device recognizer (same no-cost mechanism as the idle
  "Ballistica" wake-word listener) that listens ONLY for the unmute
  phrase while paused, so capture genuinely stops (no Whisper, no LLM
  call) rather than just going silent on the reply side. `muteBtn`
  works identically to the voice phrase; either one flips a flag that
  `runSessionModeLoop`'s own loop notices and acts on (mic release,
  spoken confirmation, starting/stopping the listener), so a tap works
  even mid-utterance -- worst case it takes effect at the end of
  whatever's already being recorded, not instantly mid-recording.
  Verified: mute/unmute regexes tested against real phrases and
  realistic ballistics commands (no false positives, no
  cross-contamination with the start/end phrases either), JS syntax
  checked, app loads and signs in clean in the browser preview with no
  console errors, initial disabled state on `#muteBtn` confirmed to
  match `#sessionModeBtn`'s existing pattern. Not yet live-mic tested
  (same caveat as every voice feature -- the browser sandbox used to
  verify this has no real microphone to test the actual recognizer
  against).

**Session Mode: components 1, 2, and 4 built; only component 3 left.**
The next real step isn't more code -- it's a range session. Component 3
should be designed from what the tracker actually gets wrong and gets
right in the field.

**Owning lenses:** Build (component 3, once there's range data),
Finance (cost estimate above still holds -- component 2 adds one Haiku
call per unmatched utterance, already inside the estimate's LLM
extraction line; may warrant a RISK_REGISTER.md entry if
continuous-listening cost turns out material in real use).

---

(The open-ended conversational layer raised 2026-09-05 -- genuine NLU via
`tool_choice: "auto"`, the reloading-safety deflection boundary, and capped
rolling chat memory -- has been built and verified against the live API
and the full test suite. Two things it left open, not solved here: the
charge-weight-safety guardrail relies on the system prompt alone (no
code-level numeric backstop like the old generate_warm_reply had), and it
has not yet had real live-voice/mic testing at an actual range session.
Moved out of backlog per this file's own convention.)

---

## Wind clock direction needs a firing-direction reference

**Raised:** Rick, range retest 2026-09-06 (afternoon session).

**What:** A spoken wind direction ("three o'clock", "nine o'clock") is
currently treated as if it means the same thing regardless of which way
the shooter is actually facing down range — it isn't. The same literal
wind (say, a west wind) is a headwind for someone shooting west and a
pure crosswind for someone shooting north; "three o'clock" only means
anything once you know which direction the shooter's own twelve o'clock
(down range) is pointed. The app has no concept of firing-direction
today, so a manually spoken clock position is being taken as if it
already had that reference, when it doesn't.

**Why this is separate from the wind-override bug it was found
alongside:** the override-not-applying bug (fixed same day — see
`intent.py`'s third hard rule and the widened wind persistence work) was
about a stated wind value failing to reach the solver at all. This is
different: even once a stated wind value *does* reach the solver
correctly, "three o'clock" is still ambiguous without a firing-direction
reference — fixing the override bug doesn't fix this, and this isn't
fixable as a quick toggle on top of it.

**Not scoped yet.** Real options worth weighing before building anything:
a one-time compass heading captured at rifle/range setup (phone compass
API); a spoken firing-direction question folded into calibration/setup
("which way are you facing — give me a compass heading or a landmark");
or accepting wind purely as a relative headwind/crosswind/tailwind
percentage spoken directly by the shooter (sidesteps needing an absolute
reference at all, at the cost of asking Rick to do that mental
conversion himself instead of the app doing it). GPS alone (already used
for "use my location" weather autofill) cannot supply this — it gives
position, not heading.

**Owning lenses:** Build (once an approach is picked), Chief of Staff
(worth Rick's own call on which UX tradeoff he actually wants at the
line, before this gets scoped).

---

## Camera-guided zeroing walkthrough (teaching feature)

**Raised:** Rick, 2026-09-06 (evening).

**What:** A guided, step-by-step voice walkthrough of the one-shot-plus-
one-adjustment zeroing method, not just a turret-click number. Most
shooters don't know this method and instead burn ammo walking their zero
in through repeated trial-and-error groups. The actual method: rifle
rested and NOT moved between the shot and the adjustment; leave the
crosshairs on the bullet hole (not the bullseye); adjust elevation first,
then windage, moving the crosshairs FROM the hole TO the bullseye while
the rifle stays exactly where it was resting. Once the crosshairs are on
the bullseye with the rifle still aimed at the hole, it's zeroed. Applies
at any zero distance the shooter is actually using (25/36/50/100yd,
etc), not one fixed distance.

**Suggested flow (Rick's own):** shooter states a zero distance -> takes
one shot, rifle stays rested -> camera spots the bullet hole relative to
the bullseye -> Ballistica: "leave your rifle exactly where it's at, put
your crosshairs on the hole instead of the bullseye, without moving the
rifle" -> walks elevation adjustment, then windage, one axis at a time
-> confirms "your rifle is now zeroed at [distance] yards" once
crosshairs are on the bullseye with the rifle undisturbed.

**Why this is a teaching feature, not a calculation shortcut:** the
emphasis Rick wants preserved is Ballistica actively teaching the
technique in plain, non-jargon language (explicitly telling the shooter
not to move the rifle between shot and adjustment is the crux of the
method) -- framed as instruction for someone who doesn't already know
this, not an automated correction.

**Not scoped yet.** Depends on real camera input (bullet-hole spotting
relative to a known bullseye position) -- Rick's own note ties this to
the camera-based field-conditions feature already documented separately
(mirage/wind reading via camera), since both need camera input during
live shooting, but this is a distinct guided-teaching workflow, not an
automatic environmental correction. Sequencing/scope against that other
camera feature not yet decided.

**Owning lenses:** Build (camera integration + guided-dialogue script,
once scoped), Marketing (a real differentiator -- teaching correct
technique, not just computing a number, is a meaningfully different
pitch from "ballistics calculator").

---

## Camera-based parallax detection (research, hardware/build undecided)

**Raised:** Rick, 2026-09-06. Research question, not a build request.

**What:** Use the scope-mounted camera to detect parallax error (target
image and reticle sitting on different focal planes), rather than
relying on the shooter noticing reticle "swim" via the manual head-shift
test. Rick's own research found this genuinely unaddressed industry-wide
(TrackingPoint's smart scope did target tracking/ballistic compensation,
not parallax) -- a real, unclaimed diagnostic gap, not just a Ballistica
differentiator. Framing: not a group-size silver bullet, but a way to
rule out one invisible variable shooters routinely misattribute to their
rifle/ammo/fundamentals.

**Key finding from this research pass:** a single camera in one truly
fixed position cannot detect parallax AT ALL, in principle -- parallax
is defined relative to a viewpoint change, so zero viewpoint change
means zero parallax-revealing signal, independent of algorithm
sophistication. This ruled out the passive "camera just watches, shooter
does nothing" version as not a v2-vs-v1 complexity question but a
genuine hardware-architecture requirement: getting any real signal needs
either (a) a controlled small nudge of the camera/mount itself (motorized
or shooter-assisted, camera measures its own displacement via optical
flow against the target), (b) a stereo camera pair at a fixed lateral
offset, or (c) an unproven depth-from-defocus approach exploiting the
camera's own aperture within a single frame.

**Recommended v1 (validates Rick's own instinct):** controlled-nudge
approach -- verify rifle stability via optical flow on the target
(shared groundwork with other camera features), introduce a small known
lateral shift of the camera/mount, sub-pixel-track reticle-vs-target
displacement across that shift (standard OpenCV optical flow, nothing
exotic), and compare against the drift-per-nudge ratio a real parallax
error of a given magnitude would predict (the transferable idea from
HCI display-parallax-correction research like EyePACT: two known-depth
planes + a tracked viewpoint change + a geometric model of expected
apparent shift) -- this ratio check is what distinguishes a genuine
parallax signature from mount flex/vibration noise.

**Complexity, relative to the other camera features on the roadmap:**
active-v1 parallax detection ranks low-to-medium -- comparable to
light/glare and precipitation detection, meaningfully simpler than
mirage wind reading or thermal heat lift (no atmospheric modeling, no
extra thermal hardware, no exotic ML). See chat log 2026-09-06 for the
full complexity comparison table across all camera features.

**Not scoped, hardware undecided** (ScopeMate vs. DIY, same open
question as the rest of the camera feature set).

**Owning lenses:** Build (once a hardware path and nudge mechanism are
picked), Marketing (a genuine, verifiably-unclaimed diagnostic feature --
worth keeping visible given how rare that is in this space).

---

## Camera-based wind reading (windsock / vegetation / mirage)

**Raised:** Rick, 2026-09-06, referenced but never actually written up --
two other camera-feature entries above ("Camera-guided zeroing
walkthrough," "Camera-based parallax detection") both cite this as
"documented separately," but no separate write-up existed anywhere in
this repo. Properly scoped now (2026-09-18) since the actual hardware
(Vector Optics Minotaur scope + phone digiscoping rig -- see [[project_
scopemate_focus_procedure]]) is now ordered and this stopped being
purely theoretical.

**What:** Estimate wind speed/direction visually from the camera feed,
the way a trained spotter reads it without instruments -- old, real
sniper/precision-shooting tradecraft, not a made-up feature. Three
distinct visual cues, genuinely different difficulty, not one uniform
problem:

- **Windsock** (if the range has one): easiest by far. Purpose-built to
  be read visually -- sock angle -> speed, sock direction -> wind
  direction. Straightforward CV, a clean signal.
- **Vegetation (trees/grass)**: moderate. Real precedent -- this is
  essentially automating the actual Beaufort wind scale (leaves
  rustling ~ light wind, branches moving ~ moderate, whole trees
  swaying ~ strong), estimated from motion in video. Gives a reasonable
  ballpark speed plus a direction from the consistent lean angle.
- **Mirage boil**: hardest. Worth being honest about even for a human
  expert, mirage reading is normally a QUALITATIVE skill (wind picked
  up / let off / shifted), not a precise number -- trained spotters
  read a relative "boil value," not an exact mph. A camera doing this
  well is a genuinely harder computer-vision problem than the other
  two (continuous atmospheric-distortion analysis across video frames,
  not a single still), and turning that into a number the solver can
  actually use is the hard part, more than detecting the boil itself.

**The calibration question Rick raised, 2026-09-18 -- this is the
actual key to making any of this accurate, not a footnote:** would a
real wind-measuring device (a handheld anemometer -- Kestrel-style
meters are already the standard tool precision shooters carry for
exactly this) help Ballistica get better at judging what the camera is
reading? Yes, and it's more central than the CV algorithm itself.
Without real ground-truth wind data, any visual estimator is just a
generic heuristic (the Beaufort-scale mapping above is a reasonable
starting point, but it's generic, not tuned to Rick's actual range,
lighting, camera angle, or distance). Reading a real Kestrel value out
loud to Ballistica (already-supported manual wind input, no new voice
command needed) at the same moment the camera captures the
windsock/vegetation/mirage state builds genuine PAIRED training data --
real wind speed alongside what the camera actually saw at that exact
moment. Across enough sessions and conditions, that's what turns a
generic guess into something actually calibrated. Practical
implication for sequencing: paired data collection can start the
moment the camera rig is up and running, well before any actual visual-
estimation model exists to calibrate -- collecting real Kestrel-reading
+ camera-footage pairs is itself useful early work, not something that
has to wait for the CV side to be built first.

**Related, same underlying gap:** [[reference_backlog]]'s "Wind clock
direction needs a firing-direction reference" entry applies here too --
a windsock/vegetation lean angle observed by the camera is relative to
the CAMERA's own orientation, not automatically a meaningful "three
o'clock" without knowing which way the shooter is actually facing down
range. Same open question, not yet resolved either place.

**Recommended sequencing:** windsock/vegetation first (tractable, real
signal, no atmospheric modeling needed) once the camera rig is
operational; start Kestrel-paired data collection in parallel from day
one; mirage boil reading as a later, harder stretch goal -- likely
scoped initially as a qualitative nudge ("wind's picked up," "wind's
shifted") rather than attempting a precise number, given even human
experts read it that way.

**Effective range is magnification-dependent, needs empirical
characterization, not assumption (Rick, 2026-09-18):** how far out any
of these three cues stays reliably readable scales with the scope's
actual magnification at the time -- a lower-power setting resolves
less fine detail (windsock texture, individual leaves/branches, mirage
structure) at a given distance than a higher-power one, the same
resolving-power relationship as the rangefinding item below. The
Minotaur's own 12-60x range gives a built-in way to test this for
real once it's in hand: sweep magnification at a few known distances
and find where each cue actually stops being reliably readable, rather
than assuming a number from a spec sheet.

**Not scoped as an actual build yet** -- hardware (camera rig) not
physically in hand at time of writing; this entry exists so the next
session has real material instead of a dangling cross-reference to dig
for.

**Owning lenses:** Build (CV approach + calibration pipeline, once
hardware's in hand), Marketing (visual wind reading is a genuine,
rare differentiator if it works -- same "verifiably unclaimed" framing
as the parallax detection item above).

---

## Camera-based rangefinding from a known target size

**Raised:** Rick, 2026-09-18. No prior trace of this one anywhere in
the repo (unlike the wind-reading item above, which at least had two
dangling cross-references) -- either discussed somewhere never
captured in writing at all, or from a conversation outside this
repo's history. Scoped fresh from the idea itself, not reconstructed
from an existing record.

**Phase:** 1 -- stand-mounted high-power scope (the Minotaur rig), same
camera already being built for group/impact detection and the wind-
reading item above. Not rifle-mounted.

**What:** Automate the classic mil-relation/angular-size ranging
formula shooters already do by hand with a reticle (`range = (known
target size x constant) / apparent angular size`) -- the camera
measures the target's angular size in the frame instead of a person
counting mil-dot subtensions by eye.

**What it needs:**
- A known target size -- straightforward if targets are standardized
  to known printed dimensions, or the size is entered once per target.
- The scope's calibrated angular field of view at each magnification
  setting -- from the manufacturer's specs, or calibrated once
  empirically (photograph a known size at a known distance, work out
  the pixels-to-angle ratio per power setting).
- Reliable target-edge detection in the image -- genuinely easy for a
  high-contrast paper target on a plain berm; no atmospheric modeling,
  no ML training data required in principle, unlike the wind-reading
  item above. One of the more tractable camera features on this whole
  list.

**Effective range is magnification-dependent, needs empirical
characterization, not assumption (Rick, 2026-09-18):** target-edge
detection needs enough resolved pixels on the target to work reliably,
so the max usable distance scales with magnification -- Rick's own
rough expectation going in is something like a 25x-class setting
holding up to roughly 500-600 yards versus a 36-55x-class setting
reaching toward 1000, but that's a starting expectation to test, not a
number to build around yet. Same empirical-characterization plan as
the wind-reading item above: sweep the Minotaur's own 12-60x range at
a few known distances once it's in hand and find the actual falloff
point rather than assuming one.

**Not scoped as an actual build yet** -- hardware not physically in
hand at time of writing.

**Owning lenses:** Build (once hardware's in hand), Marketing
(a genuinely useful cross-check against or replacement for a separate
laser rangefinder).

---

## AI-assisted spotting-scope image cleanup (denoise/deblur/multi-frame stacking)

**Raised:** Rick, 2026-09-19. Distinct from the group/impact-detection
camera work above -- this is specifically about a **spotting-scope +
phone/tablet rig used to verify shots downrange** (Rick's own framing:
"this isn't going to be something that the software would be running
off your rifle scope that you're looking through"). The competitive
angle he's after: a shooter shouldn't have to spend $1,500-3,000+ on
premium spotting-scope glass just to get target clarity if software can
close most of that gap on cheaper glass + a phone.

**Phase:** 1 -- stand-mounted, target-facing, same family as the
rangefinding/wind-reading items above, but this piece is pure image
processing with no dependency on Ballistica's ballistics data model, so
unlike those two it does **not** need to wait on the Minotaur/phone rig
to arrive to start.

**Prototype built and verified 2026-09-19**, ahead of hardware, same
pattern as `scope_stream.py`'s pre-hardware pipeline work:
- **[ballistica/image_enhance.py](ballistica/image_enhance.py)** --
  two deliberately classical/deterministic techniques, NOT a generative
  upscaler (Real-ESRGAN/Topaz-style tools): `sharpen_denoise()`
  (non-local-means denoise + unsharp mask) for a single photo, and
  `stack_frames()` (ECC alignment + averaging across a burst of the
  same static scene -- the "lucky imaging" technique from
  astrophotography). Generative upscaling was deliberately ruled out
  for anything Ballistica would measure off of -- it hallucinates
  plausible-looking detail rather than recovering real detail, which is
  fine for a keepsake photo and dangerous for judging a bullet hole.
- **[scripts/demo_image_enhance.py](scripts/demo_image_enhance.py)** --
  a controlled proof-of-concept: synthesizes a known-clean target image,
  degrades it the way a cheap digiscoping setup would (blur, jitter,
  sensor noise) to make a known-ground-truth test possible (a real
  digiscoped photo never has ground truth to check against), then
  measures PSNR recovered toward the real image, not just an
  unverifiable "looks better" claim.

**Real result, not a clean win across the board -- reported honestly:**
- Single-frame sharpen/denoise gave a clear, measurable improvement:
  +1.98 dB PSNR toward the true image, visually confirmed (crisper ring
  and hole edges, less graininess) by actually looking at the output
  images, not just trusting the number.
- Multi-frame stacking, which was the expected bigger lever going in
  (free burst capture off a static target), did **not** clearly
  outperform single-frame cleanup in this test (+1.88 dB, statistically
  a wash against the single-frame result) -- because stacking cancels
  *random* sensor noise across frames, but the dominant degradation
  modeled here was *fixed* optical blur (the same softness on every
  frame), which stacking doesn't touch. Whether stacking earns its
  complexity for real depends on which degradation actually dominates
  on the real rig -- noise or blur -- which this synthetic test can't
  know and only a real photo through real glass can answer.
- Caught and fixed a real bug in the process: the first stacking run
  produced a visible black-line artifact along one edge of the output
  (frame-alignment warp not covering the full canvas); fixed by
  matching the border-replicate handling already used in the
  degradation simulation, confirmed gone by re-inspecting the image
  afterward.

**Not yet done:** validation against an actual digiscoped photo (this
whole result is from a synthetic stand-in with known ground truth,
which is what makes the PSNR measurement possible in the first place --
real-world confirmation has to wait for the actual rig or at minimum a
real test photo through real glass). Sample output images are in
`scripts/demo_output/` (gitignored-worthy scratch output, not committed).

**Owning lenses:** Build (the module above, and the real-photo
validation once there's real glass to test against), Marketing (the
"skip the $2,000 spotting scope" framing is a genuine, testable
cost-democratization story, not just a nice-to-have polish feature).

---

## Camera-based incline angle from a scope-mounted level

**Raised:** Rick, 2026-09-18, recalled from an earlier conversation
with Claude that never made it into this repo -- same "no trace found"
situation as the rangefinding item above.

**Phase:** 2 -- rifle-mounted, explicitly sequenced BEHIND the current
phase-1 work (proving out what the stand-mounted high-power scope
camera can do: wind reading, rangefinding, group/impact detection).
Rick's own framing, 2026-09-18: "the whole idea right now is to see if
we can get Ballistica to do what we can do with a camera on a high
powered scope. And then we'll deal with the other stuff when it's on
the rifle itself." Not to be started before phase 1 is proven out.

**What:** A physical level (bubble/anti-cant indicator) mounted on the
rifle's own scope, read by a rifle-mounted camera together with the
reticle in the same frame. The mil displacement between "true level"
and where the rifle is actually being held/aimed gives the incline
angle directly -- mils are already an angular unit, so this is a
self-contained angular reading with no dependency on target size or
distance the way the rangefinding item above needs. Arguably the
simplest of the three camera features scoped this session.

**How this relates to what Ballistica already has:** there's already
real incline-angle ballistic correction math (`angle.py`, the "solve
incline angle" voice command / `solve_incline_angle` tool) -- but it's
currently fed indirectly, by back-solving the angle from an observed
click difference AFTER a miss. This feature would be a direct,
proactive alternative: read the actual angle before the shot instead
of inferring it afterward, feeding the exact same existing solver, no
new ballistic math needed -- only a new way to supply the angle input.

**Hardware note:** this needs a camera ON THE RIFLE (reading the
level-to-reticle relationship at the moment of aim), a different
physical context from the stand-mounted target-facing rig being built
for phase 1. Gives the already-purchased TriggerCam 2.1 -- currently
sitting in reserve for "some future rifle-mounted feature," see
[[project_scopemate_focus_procedure]] -- a real, fitting job once
phase 2 starts.

**Not scoped as an actual build yet**, and explicitly not next in line
-- phase 1 (stand-mounted scope camera) comes first per Rick's own
sequencing.

**Owning lenses:** Build (once phase 1 is proven out and phase 2
actually starts), Marketing (pairs naturally with the existing
incline-angle solver as a "we already compute this, now we can read it
for you too" story).

---

## Load/rifle decoupling refinement: velocity data stays barrel-specific

**Raised:** Rick, 2026-09-07. Refines (does not replace) the earlier
load/rifle independence work (§28, "freely-paired pools").

**The nuance:** loads (bullet, powder, charge, brass, primer -- the
recipe) should stay independent and reusable across any rifle sharing
that caliber, exactly as already built. But the MEASURED velocity from
actually firing a load is a property of the load-PLUS-BARREL
combination, not the load alone -- the same 5.56 load produces very
different velocity out of a 6in barrel than a 20in one. Today's model
doesn't yet have anywhere to hang a rifle-specific velocity record onto
a caliber-shared load.

**Correct data model per Rick:** loads and rifles both stay independent
(as already built); a separate pairing record links one specific load
to one specific rifle and holds the measured velocity (and other chrono
data) for exactly that combination. One load can have several such
records -- one per rifle it's actually been tested in -- and a solution
must use the record matching the CURRENTLY ACTIVE rifle, never a
generic or wrong-barrel value.

**Not scoped yet** -- no existing migration or code implements this
pairing-record concept; current schema has no barrel-specific velocity
table at all. Real design work needed on how this interacts with the
existing calibration flow (_CalibrationSession already measures a
real velocity per rifle+load -- the natural place this pairing record
would actually get written).

**Owning lenses:** Build (schema + calibration-flow wiring, once
scoped).
