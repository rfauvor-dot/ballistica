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

## Seed dataset — published manufacturer ballistic data (bullets/BCs/factory loads)

**Raised:** Rick, 2026-08-23.

**What:** Bundle a starting set of real bullet/BC/factory-load data (Hornady,
Sierra, Berger, Lapua, Nosler, Barnes, etc.) so a new user gets usable data
out of the box instead of starting from zero. Directly closes a real,
verified competitive gap — Strelok Pro advertises 75+ bullets and 55+
factory loads bundled offline; Ballistica currently has none.

**Why it matters:** New-user onboarding friction is a real cost right now —
every rifle/load has to be hand-entered even for extremely common,
well-known factory ammunition. This is table-stakes in the category, not a
differentiator to skip.

**Licensing research (done 2026-08-23, low-risk parallel work while the
multi-tenancy checkpoint sits with Rick for external review):**
- Legal grounding: under U.S. copyright law (*Feist Publications v. Rural
  Telephone*, 1991), raw facts — including measured physical quantities
  like a bullet's weight, diameter, or ballistic coefficient — are not
  copyrightable. Only an *original compilation's specific selection or
  arrangement* can get "thin" copyright protection, and even then only over
  that arrangement, not the underlying numbers. Practical read: the BC/
  weight/velocity *values themselves* are safe to use; copying a
  manufacturer's *table, chart, or page verbatim* is the thing to avoid.
- Searched directly for Hornady/Sierra terms-of-use language on
  reproducing their published ballistic data — found only standard
  load-data liability disclaimers ("use at your own risk"), no explicit
  statement on reproduction rights either way. Practical read: independently
  re-entering the factual values into Ballistica's own schema/format, and
  citing the manufacturer as the data source (for credibility and good
  practice, not because it's legally required for a bare fact), is the
  low-risk path — not scraping/republishing their pages or PDFs as-is.
- Found a real candidate source: `ammolytics/projectiles` on GitHub — MIT-licensed,
  community-maintained dataset covering Barnes/Berger/Hornady/Lapua/Sierra/
  Speer. Appears stale/dormant (work-in-progress, old CI badges), so useful
  as a reference/starting point to evaluate, not something to adopt blindly
  — worth checking what it actually contains before relying on it.
- Caveat, not resolved here: this is a U.S.-law reading. The EU has a
  separate "sui generis" database right that can protect compilations even
  without originality — only relevant if Ballistica ever has EU users/data,
  flagged so it isn't forgotten, not something to solve now.
- **This research is not a substitute for real legal review before a public
  commercial launch** — it's enough to say the idea is viable and worth
  scoping, not enough to skip counsel entirely once real money/liability is
  on the line.

**Status:** Backlog, not scheduled — explicitly does not block or fold into
the current multi-tenancy/security priority. Data structure question (how
seed/reference data relates to per-user profile data) should be resolved
once the multi-tenancy schema is locked, since this is generic reference
data that should live separately from per-user data, not duplicated into
every account.

**Owning lenses when scoped:** Build (schema — likely a separate
read-only/shared reference table, not per-user; sourcing/ingestion
pipeline), Marketing (competitive-parity framing — this is closing a gap,
not building a moat), Legal-adjacent (real counsel review before this data
ships to real customers, per the caveat above).

**Update 2026-08-30: built.** See MULTI_TENANCY_DESIGN.md §25 for full
detail. Pulled and inspected the actual `ammolytics/projectiles` repo
(not just its GitHub license badge) — real, unmodified MIT license,
pinned to commit `5b51ab231c66f60de6fcb62a6b4c4795240948e5`. 822 of
1032 source rows cleanly mapped into `ballistica/bullet_reference.py`
(Barnes/Berger/Hornady/Lapua/Speer); every Sierra row (198, including
the 77gr MatchKing) was excluded and explicitly flagged rather than
guessed at, since Sierra's own BC data in this source is only
published as an inconsistently-formatted, velocity-banded structure,
not a clean single value. Spot-checked against real, live-fetched
manufacturer data (Hornady's and Berger's own published BC pages) for
4 bullets — exact matches on every one — plus internal-consistency
checks across Lapua/Speer/Barnes. Moved out of backlog per this file's
own convention.

---

(The GPS/METAR weather auto-fill item logged here 2026-08-28 has been
built — see MULTI_TENANCY_DESIGN.md and ballistica/weather.py. Moved out
of backlog per this file's own convention.)

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

**Status:** Backlog — feasibility, pipeline-gap analysis, and cost estimate
all done. Only remaining open question is whether Rick wants build-effort
(engineering time/complexity) sized separately from the per-session running
cost above. Import-tool ask closed — already-built infra handled it.

**Owning lenses:** Build (pipeline changes, import tool), Finance (cost
estimate once real numbers land — may warrant a RISK_REGISTER.md entry if
continuous-listening cost turns out material, not filed there yet since no
decision has been made and the proposed mode is explicitly opt-in/bounded).

---

## Open-ended conversational layer (genuine NLU, not pattern-matched phrases)

**Raised:** Rick, 2026-09-05, after two live-voice Session Mode tests. Once
natural-phrasing rifle/load switching and end-of-string detection both
worked, the next thing that broke was Rick just talking to Ballistica the
way he'd talk to Claude directly — a conversational question about casing
pressure signs and whether to bump a load. Outside the app's known command
vocabulary, it just failed. Rick's point: he doesn't need Ballistica itself
to give reloading advice -- what he's after is that the interaction *feels*
like talking to an assistant, not issuing commands to software, and he
(with ChatGPT's input too) believes that conversational quality is a real
competitive/sales differentiator (individual sales and any future
licensing conversation, e.g. Leupold), not a nice-to-have polish item.

**Feasibility (scoped 2026-09-05, Build lens, grounded in real code):**
Layerable on top of the existing deterministic core without touching it —
`intent.py`'s own design already separates "what did they mean" (LLM) from
"compute the answer" (100% deterministic Python), which is exactly the
separation this needs. The narrowness today isn't architectural, it's that
every existing LLM call is forced to pick from a fixed tool menu
(`tool_choice={"type": "any"}` in `extract_intent()`) — it's not that the
model can't understand an open question, it's that it's never structurally
allowed to just answer one.
- **Small, foundational piece:** switch to `tool_choice: "auto"` (lets the
  model call a tool OR respond in free text), rewrite the system prompt
  from "classify into one of these commands" into an actual persona with
  real domain latitude, handle the new "response was text, not a tool
  call" case in `cli.py`'s dispatch. Rough terms: smaller than Session
  Mode's component 1.
- **Real, harder piece: conversational memory.** Every LLM call today is
  stateless (no history beyond what's implicit in already-known session
  fields) — fine for "switch to load X," not enough for "yeah but what
  about..." follow-ups that make it feel like a real conversation. Needs a
  rolling context buffer, a decision on how much history to carry, and how
  it coexists with the existing setup/calibration session-state hydration
  without becoming a second, competing memory system. Rough terms: same
  ballpark as Session Mode's component 2 (the session-state tracker) — new
  architecture, not a prompt tweak.
- **Orthogonal to Session Mode, worth weighing on its own:** Session Mode
  is about not needing the wake word every turn; this is about whether it
  can say anything beyond a fixed menu once it's listening. This could ship
  on top of today's wake-word-per-command flow with zero Session Mode work
  and likely move the "feels like an assistant" perception more than
  finishing Session Mode's session-state tracker would alone — worth
  considering as the higher-leverage thing to build first, not just a
  parallel track.

**Safety/advice boundary — DECIDED by Rick, 2026-09-05 (this was the one
open question gating scope, not an engineering call):** general reloading
conversation and terminology are fine; discussing what pressure signs
generally look like is fine. Ballistica must never state, confirm, or
imply a specific numeric safety judgment on "is this charge weight safe"
— always deflect to the reloading manual/reference data instead, in the
same natural way a knowledgeable person (or Claude, in conversation)
defers when the stakes are too high to answer from memory. Rick's own
canonical example, to build the system prompt's tone from directly: asked
"I don't see any pressure signs on the casings, is it possible we could
bump this up?", the right answer is something like "Worth checking your
reloading manual to see how close you are to max load, I don't want to
rely on memory for something like that" — not a yes/no, not a number, but
not a dead-end refusal either.

**Implementation nuance flagged during scoping (not yet built, worth
getting right from the start):** the guardrail has to catch *implied*
safety judgments without any number stated, not just literal digits —
"no pressure signs, sounds like you're in a good spot" is functionally the
same violation as naming a charge weight, just without a number in it. A
system prompt that only says "don't state a number" would miss this;
needs to explicitly bar qualitative safety reassurance too, always
redirecting to the manual regardless of phrasing.

**Status: built and verified 2026-09-05** (Rick chose to sequence this
ahead of Session Mode's remaining components). Both pieces landed
together, since memory needs something open-ended to attach to:
- `intent.py`'s `extract_intent()` now uses `tool_choice: "auto"` instead
  of `"any"` — it can call a real ballistics tool or just respond in its
  own voice, no longer forced to pick one of a fixed menu every time. The
  old `no_match` tool and the separate `generate_warm_reply` personality
  call are both removed — one unified path replaces them.
- The safety boundary Rick decided is written directly into the system
  prompt, with his own example as the canonical tone, plus the
  implied-judgment nuance flagged above (explicitly bars qualitative
  reassurance, not just literal numbers).
- Conversational memory: `BallisticaCLI._chat_history`, a capped rolling
  buffer (last 6 exchanges) populated ONLY by genuine "converse" turns —
  ordinary ballistics commands don't touch it, so terse commands don't
  bloat context sent to the LLM. Wired through `api.py`'s existing
  `_hydrate_cli`/`_dehydrate_cli` round-trip so it survives across the
  stateless multi-tenant API the same way setup/calibration state already
  does.
- Verified against the live API, not just stubbed: Rick's own example
  question ("I don't see any pressure signs... is it possible we could
  bump this up?") produces exactly the intended deflect-to-manual tone,
  no number, no yes/no. Caught and fixed a real quality issue in the
  process — first-pass replies used markdown formatting (bold, bullet
  lists, line breaks) and ran multiple paragraphs, both wrong for a
  TTS-spoken interface; tightened the prompt to plain spoken sentences,
  2-3 max, plain ASCII punctuation (also fixed a stray non-ASCII dash
  that slipped through initially, inconsistent with this codebase's
  ASCII-only house style). Full suite passing (151) after the change,
  including 3 new tests covering the converse/history/hydrate-dehydrate
  paths and updates to existing extract_intent stubs for the new
  `history` parameter.
- Known limitation, not solved here: unlike the old `generate_warm_reply`
  (which had a code-level "reject any reply containing a digit" backstop
  since pure small talk should never have numbers), the new broader
  conversational scope legitimately needs numbers sometimes (bullet
  weights, cartridge names, general reloading facts) -- so the
  charge-weight-safety boundary relies on the system prompt alone, not a
  code-level backstop. A narrower heuristic (flag replies combining a
  weight-shaped number with safety-adjacent language) could be added
  later once there's real transcript data to calibrate it against,
  rather than guessing at false-positive rates now.
- Not yet done: real live-voice/mic testing (same caveat as every other
  voice feature this session) -- verified directly against the API and
  via the full test suite, not yet in front of an actual range session.

**Owning lenses:** Build (tool_choice/prompt/dispatch change, then the
harder conversational-memory piece), Marketing (the competitive-
differentiator framing driving this is explicitly Rick's own strategic
read, worth keeping visible to that lens), Legal-adjacent (the safety-
boundary decision above is exactly the kind of call the liability waiver
work already treated with real seriousness — same category of risk,
now resolved by Rick directly rather than left to an embedded prompt
choice).

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
