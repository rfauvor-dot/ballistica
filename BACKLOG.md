# Ballistica Backlog

Future items that are real and worth keeping, but explicitly not being built
now. Each entry: what, why, status/sequencing, owning lenses. Move an item
out of this file into active work (or into an addendum) when it's actually
scoped, don't let it just accumulate here indefinitely.

---

## TOP PRIORITY (set by Rick, 2026-11-13): live shot detection

The main goal for Ballistica is that Rick **stays on the gun**. Live video from a
tripod camera, calling each shot as it lands, is the priority; photos between
strings are only a backup. Ballistica pulls the still frames from the video
itself after each shot, so Rick never takes a photo. The same camera feed is also
the path to distance and wind reading. Details, test results and the on-phone
plan are in the entry "Video-to-shot-table" below (now: **live** shot
detection). Sequencing: this goes ahead of every other camera item in this file.

**Equipment check, built 2026-11-16** (Rick's ask): "Ballistica, equipment
check" walks a preflight list -- camera framing (sheet number, four
corners, edge room, size, one sheet) and sharpness through the camera
phone, with spoken focus coaching when soft ("better / softer / about the
same"); phone and tablet connected; which rifle; which ammunition; then
"All set" or what still needs fixing. Also the first piece of the
phone<->tablet link: while the range camera is on, the phone checks in
with the server and sends a 3-frame burst when the check asks (rig.py; one
server process, see RISK_REGISTER.md). Not yet tried at the range with a
real voice and two devices. Sharpness bars are a first judgment from the
November 13 clip and synthetic blur; revisit with ground-tripod footage.
The link is request/answer only -- live shot calls from the phone to the
tablet still need their own path.

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

**Update 2026-09-24 -- the measuring half now exists, without a scope.**
[ballistica/target.py](ballistica/target.py) + the "Target photo" panel find
bullet holes in a phone photo of a paper target (printable sheet with four
ArUco corner markers -> exact inches) and report where the group sits
relative to the aim point in plain inches ("0.7 in right, 0.4 in low").
What is deliberately NOT built: turning that into "dial N clicks up" -- a
wrong-way instruction at the line is worse than none, so the direction
convention (turret click direction, and whether the shooter is correcting
POI or POA) is left for a walkthrough that asks the shooter, not
automation. What remains for the walkthrough itself: the guided dialogue,
tying it to the rifle's click value, and single-shot (one-hole) offsets,
which the current code already returns.

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

**Paired-data collection, started 2026-11-18:** Rick's meter is a HoldPeak
HP-866B-APP (Bluetooth; its USB port is reportedly power only). The
Intelligent Anemometer app 1.4.3 records after Start and saves an .xls:
time of day plus mph and °F, about one reading a second. The date is only in
the file name, so don't rename the files. The fan spins down slowly (a
5.8 mph puff took ~8 s to read zero), so the meter shows gusts arriving
promptly but ending late. `tools/wind_pairs.py` (logic in
`ballistica/wind_log.py`) lines logs up with clips and writes per-window
mean/min/max/sd mph to CSV. Samsung clips: the file name is the local start
time; the embedded creation_time is the END, in UTC (checked on Rick's
clips). Record each clip's camera heading (`--heading`) and the meter's
(`--meter-heading`: the direction the meter takes wind FROM; its screen
faces the opposite way). If a different device films, film the app screen at
the start of each clip to measure `--clock-offset`.

Backyard test clips, 2026-11-20: both were handheld, and the picture
shifted 10-280 px between keyframes, which swamps leaf movement. Rick's
protocol now:
- the meter on its own tripod in the open, turned until the reading peaks,
  and outside 10+ minutes first (temperature took ~9 minutes to settle);
- 2-3 s on the meter's screen as a clock check;
- the phone set down on a table in landscape, 1x, focus and exposure
  locked, untouched for 3 minutes.

The tool now measures every stretch of 20 s or more where the camera held
still, with 1 s trimmed off each end.
- A keyframe step counts as still when the median shift over 5 steps is
  3 px or less and the step itself is no jolt over 10 px.
- A lone 3-4 px wobble from a gust doesn't break a stretch; a nudge or a
  slow creep does, and the footage on both sides is kept.
- A clip that never held still is measured whole and flagged "camera not
  still". `--start`/`--end` set the stretch by hand.

First good clip, 20261120_124045 (12:40, 2026-11-20): phone on the patio
table, 296 s of still footage, 3.7 mph average, 7.3 mph peak. The phone
crept ~13 px at 79-83 s. Camera faced west (270), wind from the west
into the meter (270), so the wind blew toward the camera. Results are in
`data/wind/2026-11-20-backyard-1240.csv`. Still portrait, with the patio overhang in the
top edge and the lower half lawn.

Second clip, 20261120_143855 (14:38): a crosswind. Camera south (180),
wind from the west (270), blowing right to left across the frame. 293 s
still (77-370 s; the phone was set down at ~34 s and repositioned at
~61-71 s), 3.2 mph average, 7.1 mph peak, with a near-calm lull at
14:44:01-14:44:21. In view: a pine, a red-leaf tree, a mostly bare tree.
Still portrait. Results in `data/wind/2026-11-20-backyard-1438.csv`.

Third clip, 20261120_151518 (15:15): wind at 45 degrees. Camera southwest
(225), wind from the west (270), coming from the front right. 309 s still
(16-326 s), lighter wind: 2.3 mph average, 4.9 peak, near calm around
15:15:50. Aimed up at a leafy tree and a close pine that fill most of the
frame. Results in `data/wind/2026-11-20-backyard-1515.csv`.

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
for phase 1. Gives a rifle-mounted scope camera a concrete future job. (Correction 2026-09-24: the TriggerCam 2.1 this used to name as already purchased was never bought -- Rick declined the ~$600 price. What he actually has is a GoPro Hero 11 Black; see [[project_scopemate_focus_procedure]]. Camera choice for phase 2 is open.)

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

---

## Video-to-shot-table (timestamped shots from range video) + per-sheet numbering

**Raised:** Rick, 2026-11-13, after a real range video of target sheet 2
(100 yd, filmed through his Athlon spotting scope with a phone mount, three numbered sheets hung side by side).

**What:** take a range video of a Ballistica target sheet and produce a
shot table automatically -- each new hole's firing order, video timestamp,
and position in inches from the aim point, plus group size, plus a
numbered/annotated image. Done once by hand on 2026-11-13 (5 shots found at
0:21 / 0:32 / 0:40 / 1:06 / 1:16, group 1.14 in over shots 1-4, 1.77 in with
the flyer); the throwaway scripts were scratch work, not in the repo.
Method that worked: sample frames at ~2/sec, find the sheet's corner markers
in each frame and flatten it to 200 px/in with the existing homography, find
dark blobs against a blurred background, then confirm a shot only when a dot
persists in several consecutive good frames. Frames where the sheet isn't
found (camera jolts) are skipped, and a shot lands in the first good frame
after the jolt, so timestamps are good to ~0.5 s.

**Per-sheet numbering (the enabling piece):** every sheet uses the same four
ArUco IDs (0-3), so two sheets in one frame corrupt the marker fit (4.9 in
error, 0 holes found) and software cannot tell sheet 1 from sheet 2. Rick
numbers his sheets 1, 2, 3 by hand at the top, but the digit was not legible
on video. Proposed: a large dark sheet number in the title area, and ideally
distinct marker IDs per sheet number, so one frame can hold several sheets,
each is measured separately, and results are labeled by sheet automatically.
Changing marker IDs touches target.py's `_MARKER_ORIGINS` and the printable
sheet, and must stay readable for sheets already printed.

**Known gaps in the current photo analyzer on video (target.py):** it found 0
holes on soft video frames -- it expects sharp still-photo hole edges, masks
the printed ring (three of the five holes sat on it), and the default marker
detector decoded only 2 of 4 markers at ~55 px per marker until detector
parameters were loosened (3 of 4 decoded, 0.046 in fit error, above the
0.03 in warning threshold). A video path needs its own thresholds and marker
settings, not the still-photo defaults.

**Capture guidance that goes with it:** camera on a ground tripod separate
from the bench (Rick's tripod on the table jolted the camera at every shot,
plus ~33 other disturbances), locked focus/exposure, highest resolution, one
sheet filling the frame with all four corner squares visible.

**Live detection test, 2026-11-13 (prototype `tools/live_shot_replay.py`,
scorer `tools/live_shot_eval.py`):** the 22-minute video replayed frame by
frame so each frame is judged only against what came before it, as a live feed
would be. Against the hand-measured table (17 shots on sheets 1 and 2): all 17
found, 16 on time (called ~1-1.5 s after the hole appears, positions within
0.12 in), 1 called 11 s late (a hole 0.2-0.3 in from an old one is absorbed
until it separates), 0 false calls. The first version of the detector made 50
false calls, all repeats of known holes: whenever the camera moved away and
back it forgot the sheet. Fixed by recognizing a sheet from the holes already
on it. Limits: one video, the scoring table is my own offline measurement (not
independent), sheet 2's later shots happened off camera and sheet 3 never
came into frame, and it has not been tried on the ground-tripod setup.
Footage was shot through Rick's Athlon spotting scope with a phone mount.
Stills (camera frame + flattened sheet with the new hole ringed) are saved
automatically per shot; no photos by hand.

**Running it live on the S25 (not built, not measured on the phone):** on a
4-core desktop CPU in Python the per-frame cost at 1080x1920 was roughly
0.25-0.4 s (marker finding 180-285 ms, flatten + hole find 75-137 ms), so 2
frames/sec uses about half to most of one core. The S25 is probably in the
same range or faster, but that is a guess until measured. Three places to run
it: (A) stream frames to the Ballistica server (about 3-4 Mbit/s uplink at
1080p/2 fps, needs steady signal at the range, Render CPU cost, plan decision
still deferred); (B) in the phone's browser with OpenCV compiled to WebAssembly
(the default OpenCV.js build lacks the marker module, so a custom build);
(C) a native Android app (best camera control, biggest build). Recommended
first step: run the server on Rick's PC on local wifi, phone page streams
frames to it, calls spoken to Bluetooth earbuds, to prove the whole loop before
choosing A/B/C. Also worth using: the phone mic hears each shot, so the sound
can mark when to look for a new hole (cuts false calls and gives exact timing).
Open risks: phone heat and battery over a 20+ minute session, and how much
focus/exposure control a browser page gets on Android.

**Capture setup Rick will use next (2026-11-13):** one sheet in frame,
scope/camera on a ground tripod beside him (not the table), big black felt-pen
sheet numbers. Pen numbers are for human review; software identity should
come from per-sheet marker IDs (still to do) or a voice "sheet 2 is up".
Do not tune thresholds until a ground-tripod clip exists.

**Build plan, ranked (agreed with Rick 2026-11-14).** Capture setup from now
on: one sheet in frame, tripod on the ground, sheets numbered in black felt pen.
1. Detector as a tested module plus a replay benchmark with labeled clips. **Done 2026-11-14, see below.**
2. **Done 2026-11-14, see below.** Sheet identity: a number picker on the printable sheet, each number with
   its own corner markers and a large printed number; a saved hole list per
   sheet number; never call a shot while identity is uncertain. Felt-pen
   numbers stay as the human check -- software should not depend on reading
   handwriting through a spotting scope.
3. Find new holes by comparing the flattened sheet with how it looked before
   the shot, instead of re-finding every hole in every frame.
4. Use the shot sound as the trigger: one shot, at most one new hole.
5. Better stills (sharpest or combined frame, before/after pair) and an
   after-the-session shot table from a recorded video.
6. The live loop with spoken calls (phone to laptop over the phone's hotspot
   first, then decide phone vs server).
7. Saved shot records tied to rifle, load and session.
8. Later: calling shots, a distance check from the sheet's known size, and
   wind from mirage (start logging marker shimmer in step 3).

**Step 1, done 2026-11-14.** The prototype's logic now lives in
`ballistica/shot_tracker.py` (frames in, shots out) with scoring in
`ballistica/shot_scoring.py`. `tools/live_shot_replay.py` is a thin wrapper
round it; `tools/shot_benchmark.py` runs every labeled clip listed in
`data/shot_benchmark/clips.json` and fails if a clip does worse than its
recorded numbers (`tools/live_shot_eval.py` is gone, replaced by it). Videos
are not in the repo: set `BALLISTICA_CLIPS_DIR` to the folder holding them.
`tools/replay_in_chunks.py` does the same replay gently (2 threads, low
priority, 60 s chunks with pauses, resumable after an interruption); the full
November 13 video through it gave the prototype's exact 17 shots.
`tests/test_shot_tracker.py` drives the tracker with synthetic camera frames
(`tests/synthetic_range.py`), so it runs in CI with no video.

**What the synthetic tests found (each is a test marked as an expected
failure, labelled with the step that should fix it):**
- Tight groups cause duplicates without the camera moving. Touching holes
  merge into one blob too big to count and both vanish; if that hides enough
  known holes the sheet is no longer recognised, a new empty sheet is started,
  and the holes still visible are called again. November 13 never exercised
  this (sheet 2's dense cluster was off camera).
- If a sheet's only hole gets a touching neighbour, the tracker starts a new
  empty sheet every frame and calls nothing more on that sheet.
- A hole within 0.3 in of an old one is never called.
- Two different sheets with a hole in a similar place are taken for one sheet.
  (Correction to the 2026-11-13 note: ONE confirmed hole is enough to
  recognise a sheet again; only a blank sheet depends on image position.)
- In a crisp, full-contrast view the printed crosshair is called as a shot at
  dead centre, a hole on the ring is lost, and the scale bar can be called
  when the view is tilted. The November 13 footage was flat enough to stay
  under the threshold; a sharper ground-tripod clip may not be. **Watch for a
  phantom shot at the exact centre on the next clip.**

**For the next range session:** record with sound on, all four corner squares
in frame with a little margin, zoom/focus/exposure left alone once set, and
one pair of shots deliberately close together. That clip becomes the second
benchmark clip.

**Step 2, done 2026-11-14.** Numbered sheets 1-11: sheet N uses corner
markers 4N..4N+3 (the existing marker set has room for exactly 11 more; the
original sheet's 0-3 means "unnumbered"), and prints N large at the top
right, outside the shot area. Picker next to the sheet link in the target
photo panel, or `/target-sheet.pdf?number=N`; photo analysis reports the
number. In the live tracker a numbered sheet's identity comes from its
markers, so a bump, a swing away or a restart can't make old holes new;
`sheet_holes()` gives each numbered sheet's hole list to save and
`ShotTracker(known_holes=...)` takes it back. A numbered view is used only
with 3+ of its markers in sight, and holes already on a sheet the first time
it is seen are recorded, not called. Unnumbered sheets behave exactly as
before (the November 13 benchmark is unchanged). Not done: storing the hole
lists anywhere (step 7), and a way to number an already-printed sheet by
voice or tap.

**Framing check, done 2026-11-15.** "Check framing" button in the range
camera panel: one frame goes to `POST /v2/target/framing`
(`shot_tracker.check_framing`), which answers in plain sentences -- sheet
number, which corner squares are missing or right at the edge, whether the
sheet is too small in the picture, whether another sheet is in view, and
whether it's an unnumbered sheet. Uses the tracker's own video marker
settings. Size bar from the one real data point (November 13: ~55 px corner
squares worked); focus and exposure are NOT judged yet. Needs a signal.

**Step 4, first part, done 2026-11-15: hearing shots.** `ballistica/shot_sound.py`
finds bangs in the camera's sound (causal, pure numpy);
`tools/shot_sound_test.py` checks it against a video with known shots. On the
November 13 video: a bang found for 15 of the 17 known shots, 0.3-1.6 s
before the hole was first seen on camera. The two misses: one shot rose more
slowly over loud background (probably talking); one hole was itself detected
11 s late, so which bang fired it can't be told. Through the phone's
microphone a shot climbs ~30 dB in 30-40 ms and holds near -10 dBFS for about
half a second (the phone evens out loud sound), so the detector compares
with the level ~50 ms earlier, not a few ms. 225 other bangs in 22 minutes,
69 as loud as Rick's quietest shot -- but that recording mixes other
shooters, Rick's own off-camera shots at sheets 2 and 3, and the camera
sitting on the shooting table, so they can't be told apart yet. Consistent
with the plan: a bang is only "look now", never a shot. Not done: feeding
bangs into the tracker.

**Range hardware (Rick, 2026-11-15):** a Samsung Galaxy Tab 11+ 5G at the
range, with its own cell signal. **Decided 2026-11-15: the S25 stays on the
spotting scope as the camera (and its microphone hears the shots, in the same
recording as the picture, so the timing lines up); the tablet is the screen
and the voice at the bench.** Plan for the live loop that follows from it:
the phone joins the tablet's own Wi-Fi hotspot, so it gets the tablet's 5G
connection with no cell signal of its own needed; the phone's page sends
frames (and bang times) to the Ballistica server, which runs the tracker; the
tablet's page shows the flattened sheet with each new hole ringed and speaks
the calls. Nothing new has to run on either device beyond a web page, and the
detector stays the tested Python code. Costs to check before relying on it:
upload over the tablet's 5G (~3-4 Mbit/s for 1080p at 2 frames/s, less if
the phone sends only the sheet), and server CPU (~0.3 s per frame on the PC,
so over half a core per shooter while live -- part of the deferred hosting
decision). Running the detector on the tablet itself stays the later option;
its speed is unmeasured.

**On-device speed test, built 2026-11-15:** `/speed-test`
(https://ballistica.onrender.com/speed-test). A browser version of the
detector's per-frame work (`ballistica/web/speedtest/detector.js`, OpenCV.js
5.0 from jsdelivr, pinned by integrity hash) timed on 12 frames from the
November 13 video; the page also checks its answers against the Python
detector's (`manifest.json`, built by `scripts/build_speed_test_frames.py`)
and matched on all 12. **Correction:** the standard OpenCV.js build DOES
include the marker detector (`cv.aruco_ArucoDetector`); the earlier note
here that it needed a custom build was wrong. It runs on one core. On the
PC's browser (in the background, so slow) about 0.85 s a frame, most of it
finding the corner squares on the full 1080x1920 frame; the S25 and the tab
are what count. Not ported yet: the frame-to-frame shot calling.

**Speed test results (Rick, 2026-11-15), full corner-square search on every frame:**
S25 268 ms per frame = 3.7 frames/s ("just fast enough", against 2 needed);
Galaxy Tab 667 ms = 1.5 frames/s (not fast enough).

**Decided 2026-11-15 (supersedes the server-first plan above): the S25 on the
scope runs the detection itself, in its browser; the tablet is the display
and voice.** So the browser detector (`ballistica/web/speedtest/detector.js`)
becomes the production path, not just a measurement: it needs the
frame-to-frame shot calling ported next, and its own replay benchmark so it is
held to the same standard as the Python tracker. The server is no longer in
the live loop -- no upload cost, and it works with no signal at all -- but
the phone and tablet still need a link between them (likely over the
tablet's hotspot).

**Patch search, built 2026-11-15** (`detector.js`, `track: true`): after the
first frame, the corner squares are searched only in small patches where each
sheet's four squares should be (found ones where they were, missed ones where
the last fit puts them), with a full search on the first frame, after two
patch searches in a row lose a sheet that was in use, and every 10 frames
(5 s). One lost frame is usually a shot's blurred jolt frame (the picture
moved at most 23 px between frames on November 13, against a ~57 px margin),
so the same patches are kept for one more frame. On the 10-second November 13
stretch: 3 full searches in 21 frames, and all 21 frames give the same answer
as the server's detector (plus the 12 single frames). A square found in a
patch has exactly the corners a full search gives (0.00 px apart). On the PC
browser: corner squares 656 -> 216 ms a frame, whole frame 921 -> 482 ms
(1.9x). Flattening the sheet and finding holes (~250 ms there) is now the
largest cost. Phone numbers not yet measured with it.

**Faster flattening, 2026-11-15:** the median blur runs only over the area a
hole can be in (plus its reach), and the "inside the picture" area is drawn
from the picture's outline instead of warping a whole-frame mask. Same answers
on all 33 sample frames; flatten step 218 -> 189 ms on the PC browser (-14%).
Rejected: flattening the grey picture instead of the colour one -- 1-grey-level
differences split two touching holes into two spots on the 12-hole frame. The
median blur is now most of what's left; the real saving there is step 3
(compare with the sheet before the shot), which replaces it.

**Speed test results with patch search (Rick, 2026-11-16):** S25, test
version 2 (patch search, before the faster flattening): 139 ms a frame = 7.2
frames/s live, against 2 needed (3.6x); full search 293 ms = 3.4 frames/s
(patch 2.1x faster); all 33 frames match. Galaxy Tab, version 2: 334 ms = 3.0
frames/s. S25, version 3 (faster flattening too): 126 ms a frame = 7.9
frames/s live (~4x what's needed); full search 276 ms; all 33 frames match.
Galaxy Tab, version 3: 307 ms a frame = 3.3 frames/s live (1.6x what's
needed); all 33 frames match. From 667 ms / 1.5 frames/s with the first
version, so the tab could now run detection itself too, with less to spare.

**Browser shot calling, built 2026-11-16** (`web/speedtest/shot_caller.js`):
the frame-to-frame part of the tracker (which dots are new holes, when to
call a shot, which unnumbered sheet a view is) ported line for line, so the
browser now does the whole job: `detector.js` -> views -> `shot_caller.js` ->
shots. Python's `ShotTracker.process_frame` was split so the same calling
(`process_views`) can be fed recorded views. Checks:
- Logic, exact (in CI, `tests/test_shot_caller.py`): fed the views the Python
  replay recorded from the whole November 13 video
  (`data/shot_benchmark/*.views.jsonl.gz`, from `tools/replay_in_chunks.py
  --record-views`), the browser caller calls the same 17 shots, same times,
  same places, same score; also identical on 300 random view streams.
- End to end (`tools/browser_replay.py`, PC Chrome, patch search on, all 2703
  frames saved as JPEG): 16 of 17 matched, 1 missed, 0 late, 0 duplicates,
  0 false calls; position error mean 0.026 in, max 0.054 in; called a median
  1.0 s after first seen. Python run on the SAME JPEGs: 16 matched, 1 missed, but 1
  duplicate and 4 false calls -- JPEG compression alone pushes borderline
  frames over (it explains ~950 of the 1084 frames where the browser saw
  different holes from Python on the decoded video). Browser vs Python on the
  same JPEGs: different holes on 133 of 2703 frames, 132 of them patch-search
  frames (a patch finds one corner square more or fewer than a full search,
  so the fit and the holes move by up to ~0.25 in on that frame). The missed
  shot is the known close-holes limit on a knife edge: the new hole was 0.29
  in from where the browser had put an older hole (limit 0.30), 0.31 in from
  where Python had -- step 3 is the real fix.
- Speed: 414 ms a frame on the PC browser over the whole video, 33.6% of
  frames needing a full search (the 10-second stretch measured 3 of 21).

**Live calling page, built 2026-11-16** (`/live-shots`, `web/live/`): the
camera phone runs camera -> `detector.js` -> `shot_caller.js` at 2 frames a
second and posts each call (`POST /v2/rig/shots`); the bench device's "Live
shot calls" panel collects them (`GET /v2/rig/shots?after=N`) and speaks the
sentence the server builds (`rig.shot_sentence`). Uses the main app's saved
sign-in and the same rig link (so the equipment check works with it open);
remembers numbered sheets' holes across reloads; wake lock; calls made with
no signal are queued. Own security policy like /speed-test (the main app's
stays strict). Checked by playing an 85-s November 13 clip (360-445 s)
through the page's "Replay a video file" option in desktop Chrome: all 5
real shots called within 0.03 in of the hand-measured positions; 4 seen
within 2.5 s of the table time, the 390 s shot 22 s late (0.29 in from an
older hole); plus 2 calls for holes already on that unnumbered sheet when
the clip starts. Not yet run on the S25 or with the tablet speaking: that
is the range test (checklists for Nov 20 / Dec 4 / Dec 11).

**Status: top priority. Steps 1-2 done, step 4 started, framing check and speed test done, detection on the phone decided, live calling page built (phone calls, tablet speaks); not yet tried at the range. Next: step 3 (compare with the sheet before the shot, for close/touching holes), feed bangs into the tracker.**
Related: the target photo panel (see the zeroing walkthrough entry), and
"Session Mode" shot logging, which could supply the shot order to match
against detected holes.

**Owning lenses:** Build (video frame pipeline, marker/sheet changes),
Marketing (a shot-by-shot replay of a group is a strong demo), Chief of
Staff (decide whether this is app functionality or a coaching/review tool
first).

---

## Easier rifle naming: build the name from picks (Rick, 2026-11-25)

Renaming by voice works now (live 2026-11-25), but Rick found it faster to
type the name in the edit form and save. His rule of thumb for names:
**caliber, then barrel length, then maybe the maker** (Winchester,
Remington, Ruger...), e.g. "308 20-inch Ruger". He'd like that on a list
to pick from rather than said or typed out.

Starting idea, to talk through with Rick before building: in the rifle
form, a caliber pick list, a barrel-length field and a maker pick list
that fill in the name (still editable), plus a "Use suggested name"
button on existing rifles. `scheme_name()` in `rifle_names.py` already
builds this name; voice rename already offers it. Open questions: which
makers go on the list, whether the maker is the rifle or barrel maker,
and whether existing rifles get a one-tap rename to the scheme.

Rick's answers (2026-11-25):
- **Maker is whichever matters for that gun**: the barrel maker on an AR
  (Faxon -- his favorite), the rifle maker and model on a bolt gun (a .338
  Win Mag that's a Ruger Model 77). So one "maker / model" slot, not two
  fixed fields, and it should take a model as well as a brand.
- **Barrel style is worth naming**: flame fluted vs plain fluted Faxon
  barrels. Likely an optional barrel-profile pick (flame fluted, fluted,
  heavy, pencil...).
- **Chamber matters within a caliber**: 5.56 NATO vs .223 Wylde, so the
  caliber list needs the chamber variants, not just ".223".
- Example names this implies: "223 Wylde 20-inch Faxon flame fluted",
  "338 Win Mag Ruger Model 77".

Status: not started; Rick wants to discuss first. Lenses: Build, Marketing.
