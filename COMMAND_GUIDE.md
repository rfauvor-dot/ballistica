# Ballistica — Command & Capability Guide

**What this is:** a ground-truth reference for everything Ballistica can
currently do, both by voice and in the web app. Built by reading the
actual command-routing code (`ballistica/cli.py`, `ballistica/intent.py`,
`ballistica/web/index.html`) and its tests directly — not from memory of
past sessions or design docs, since those can drift from what actually
shipped. Current as of 2026-08-28 (commit `11c49d6`, the multi-tenant
cutover). If a previously-discussed feature isn't listed as working
below, it isn't built yet — see **Known limitations** at the end.

This is written for you to read top-to-bottom and understand how the app
actually works today, and as a starting draft you can adapt into real
user-facing docs or an onboarding flow later.

---

## 1. How you actually talk to it

Voice works two different ways depending on what's said:

- **Wake word:** say **"Ballistica"** (anywhere in the sentence — it's a
  simple "does the heard text contain this word" check, not an exact
  phrase match) while voice is enabled and idle. The app plays a
  greeting the first time in a session ("Hey Rick, Ballistica's up. What
  are we working with today?") and a shorter ack on later wakes in the
  same session ("Copy that, standing by."), then listens for one
  command.
- **Mid-conversation, no wake word needed:** if the last reply was part
  of a guided interview (rifle/load setup, calibration) or a pending
  delete confirmation, the app keeps listening for your next answer
  without requiring "Ballistica" again. A plain one-shot answer (like a
  drop-at-range solution) drops back to sleep after one reply.
- Voice must be turned on with a tap first (browsers require a real tap
  before they'll grant microphone access) — after that it's hands-free
  until you tap "Disable voice." Turning it off plays a signoff line.
- Typing works identically to speaking — every command below can be
  typed at the CLI or sent as text through the API; voice just adds a
  transcription step in front (Whisper) and a spoken-reply step after
  (OpenAI TTS).

Every reply is either **terse/numeric** (live-fire tone — drop
solutions, calibration, angle-solving) or **conversational** (setup,
status, small talk) — deliberate, so the tone matches whether you're
mid-string at the line or at the bench setting things up.

---

## 2. Getting a ballistic solution

| Say | What happens |
|---|---|
| "drop at 400 yards" | Elevation + windage solution at that range, spoken in whatever unit the active rifle's reticle actually uses (mils or MOA) |
| "400 yards" / "range 400 yards" / "give me a solution for 400 yards" | Same thing — any sentence containing a bare number + yd/yard/yards is read as a range request if nothing more specific matched first |
| "table" / "table to 800 yards" / "table to 800 yards every 50 yards" | Full drop/windage table (defaults: out to 500 yards, every 100) |
| "repeat" / "repeat solution" / "say that again" | Re-speaks the **last** solution from memory — doesn't recalculate, so it still works right after switching a load/condition you haven't re-queried yet |
| "repeat elevation" / "what was the elevation again" | Just the elevation half |
| "repeat windage" | Just the windage half |
| "what zero minimizes my spread out to 600 yards" | Finds the zero distance that minimizes total vertical spread out to that range |
| "I'm seeing 3 clicks at 500 yards[, from 100 yards]" | Back-solves an uphill/downhill shooting angle from an observed click difference; reference distance defaults to 100 yards if not stated |

## 3. Switching rifles & loads

| Say | What happens |
|---|---|
| "switch to 23.5gr H335" / "switch to my heavier load" | Changes the active **load** on the current rifle (fuzzy match — natural descriptions work via the LLM fallback, not just exact names) |
| "switch rifle to AR-15 20in Faxon" | Changes the active **rifle** (checked before the plain "switch to" pattern, so "switch rifle to X" never gets misread as a load switch) |

**Quirk worth knowing:** this only *selects among rifles/loads that
already exist*. There's no voice command to delete a single load (see
Limitations) — only whole rifles.

## 4. Setting conditions & wind

| Say | What happens |
|---|---|
| "set conditions temp 90 pressure 29.92 altitude 1200 humidity 40" | Exact-phrase fast path — requires all four numbers |
| "it's about 90 out" / "pressure's 29.8, altitude's 4000 feet" | Natural phrasing via the LLM fallback — **merges** into whatever's already set; you only have to state what actually changed |
| "set wind 10 mph from 3 o'clock" | Sets wind speed + clock direction — **always fully replaces** the wind, both by exact phrase and by natural language (there's no partial "just update the speed" for wind the way there is for conditions) |

**Important, verified gap:** conditions/wind set this way live in the
**voice session's own memory** and carry forward for later voice
queries. The **web app's "Conditions (optional)" panel is a completely
separate set of fields** sent fresh with every "GET SOLUTION" click —
setting wind or conditions by voice does **not** change what the web
form uses, and vice versa. See §8 for why this matters.

## 5. Setting up a new rifle or load (guided interview)

| Say | What happens |
|---|---|
| "new load" / "add a load" / "set up a load" / "let's log a new load" | Starts a guided, multi-turn interview to add a new ammunition load |
| "new rifle" / "add a rifle" / "set up a new rifle" / "let's build a new rifle profile" | Same, for a new rifle |

Once started, **every following utterance is directed at the interview**
until it's confirmed or cancelled — including "quit"/"exit", which
**cancel the interview** in this state rather than doing anything to the
app itself (see the quit/exit note in §9).

- **Required fields** (load: name, bullet weight, BC, drag model,
  muzzle velocity, zero distance; rifle: name, scope height, optic type)
  can't be skipped — the interview keeps asking.
- **Everything else** (caliber, twist rate, click value, scope
  make/model, magnification, suppressor, reticle type, etc. — the same
  full field set as the manual web form) is asked one at a time; say
  **"skip"** (or "none"/"not sure"/"no"/"pass") to move past an optional
  one.
- Answering with more than was asked is fine — extra volunteered
  fields in the same breath are all captured.
- Once everything's asked, it reads back a summary and asks **"Sound
  right?"** Nothing is saved until you confirm. Saying "no" (with no
  correction in the same breath) asks what needs to change instead of
  guessing; saying "no, actually the zero's 50 yards" applies that
  correction directly.
- **"cancel" / "never mind" / "stop" / "abort" / "forget it"** bails out
  at any point, nothing saved.
- Three consecutive turns where nothing usable was understood
  auto-cancels the interview ("Having trouble understanding you...")
  rather than looping forever.

## 6. Editing an existing rifle

| Say | What happens |
|---|---|
| "change the twist rate to 1:8" / "switch my reticle to MRAD" / "the scope height is actually 2.6 inches" | Edits one or more fields on the **active** rifle's already-saved profile, in one shot — no interview |

This is the one-shot counterpart to guided setup — for fixing something
already saved, not for adding a new rifle. It always acts on whichever
rifle is currently active for voice, not whatever's open in the web app
(see §8).

## 7. Deleting a rifle

| Say | What happens |
|---|---|
| "delete this rifle" / "remove the rifle" / "get rid of the rifle" | Asks to confirm: "Delete the X, and all its loads? This can't be undone." |

**Flagged — narrow and unforgiving confirmation vocabulary.** Only
`yes / yeah / yep / yup / confirmed / save it / sounds good / good to go`
or a bare `correct`/`right` count as **yes**. Anything else — including
things that sound like clear confirmations, like **"delete it," "go
ahead," or "do it"** — is silently treated as **declining**, and the
rifle is kept with no further prompt. This is the safe direction
(defaults to *not* deleting), but it's inconsistent with how the rest of
the app handles an unrecognized reply: during rifle/load setup or
calibration, an unrecognized reply while confirming is treated as a
possible correction and re-processed, not silently assumed to mean "no."
Worth deciding whether to widen the yes-vocabulary here, or at least add
an "I didn't catch that — say yes or no" reply instead of a silent
decline.

There is currently **no voice command to delete a single load** — only
a whole rifle (which takes every load on it with it).

## 8. Rifle deletion — the web app version

The web app has its own delete flow (a native browser confirm dialog:
"Delete '<name>' and all its loads? This can't be undone.") — same
one-way, whole-rifle-only behavior as voice, just with a normal yes/no
dialog instead of a spoken confirmation.

## 9. Chronograph calibration

| Say | What happens |
|---|---|
| "start calibration" / "let's chrono this load" / "true up the velocity on this one" | Starts a live session for the **active** load, states its current book velocity, and asks for shots |
| a number (e.g. "2650") | Records one shot velocity, gives a running average; numbers must be 3–5 digits (100–99999) — a much shorter or longer number won't register as a shot reading. Only the **first** number in the utterance counts if you read more than one in one breath |
| "average" | States shot count, average, and spread so far without ending the session |
| "discard that" / "throw out" / "toss" / "scratch that" / "bad reading" | Removes the **most recently read** shot |
| "end calibration" / "that's it" / "we're done" / "finished" | Reads back count/average/spread and asks to confirm saving it as the load's new velocity |
| confirming: yes-word or "correct"/"right" | Saves the new average velocity to the load, with a note recording shot count/average/spread |
| confirming: no / not now / negated | Discards the whole session, nothing saved |
| "cancel" / "never mind" / "abort" | Cancels at any point, nothing saved |

Same 3-strikes-and-auto-stop behavior as setup if it can't understand
consecutive shot readings. A genuinely wild reading (statistically an
outlier — more than 40fps AND more than 2 standard deviations off the
average so far) gets flagged in the reply, but is still recorded, not
rejected — you'd still need to say "discard that" to drop it.

## 10. Status, listing, help

| Say | What happens |
|---|---|
| "status" | Reports the active rifle, load, current conditions, and wind |
| "list rifles" | Lists every saved rifle name — **exact phrase only, no natural-language fallback** (see §12) |
| "list loads" | Lists every saved load name on the **active** rifle — same exact-phrase-only limitation |
| "help" / "?" | Prints the built-in command cheat sheet |

## 11. Small talk

A short, fixed set of greetings ("hi"/"hello"/"hey") and thanks
("thanks"/"thank you"/"good job"/etc.) get an instant canned reply with
no API call. Anything else that isn't a recognized command — actual
banter, a personal remark, an unclear ask — goes to a separate small-talk
model that either replies warmly and briefly, in character, or (if it's
ambiguous enough to maybe be a mangled ballistics request) declines to
guess and asks for a rephrase instead. **Hard rule enforced in code:**
this small-talk reply is never allowed to state, estimate, or imply a
ballistics number — checked twice (in the prompt itself, and by
scanning the reply for any digit before it's ever spoken) since a
fabricated number read back as if it were real would be a safety issue.

## 12. Session, timeout & state behavior

- A guided setup or calibration session is **modal** — every utterance
  goes to it until confirmed or cancelled.
- **5-minute idle timeout:** any open setup, calibration, or pending-delete
  confirmation left untouched for 5 minutes is silently cleared before
  your next utterance is processed — so an old, forgotten session can
  never "eat" a later, unrelated command.
- **3-strikes auto-cancel:** three consecutive turns where nothing
  useful was understood (during setup or calibration) ends that session
  automatically rather than looping forever.
- Once signed in through the new multi-tenant login (see §13), this
  state — active rifle/load, any in-progress setup or calibration —
  **persists across separate requests and even a server restart**, tied
  to your account. Before this cutover it only lived in server memory
  for as long as the process stayed up.

---

## 13. The web app (non-voice actions)

The web app (`index.html`) is how you manage data day to day; voice is
for the range. As of this cutover, it requires signing in first.

- **Sign in / Create account:** email + password, straight to Supabase
  Auth. A new account may require confirming via a verification email
  before it can sign in (depends on your Supabase project's email
  settings) — see the note below.
- **Waiver acceptance (added 2026-08-28):** clicking "Create account"
  no longer creates one immediately — it shows the full attorney-approved
  liability waiver on its own screen first. The account isn't created
  until you check the acknowledgment box and click "Create my account";
  clicking "Back" returns to the sign-in form without creating anything.
  Signing in to an *existing* account skips this — it's only shown once,
  during account creation.
- **Sign out:** clears the session, drops back to the login screen.
- **Help / Walkthrough (added 2026-08-28):** a permanent, always-
  expandable menu section listing four narrated audio sections (Getting
  Started, Rifle and Equipment Setup, Checking a Load and Velocity, Long
  Range Shooting and Spotting), each with standard play/pause controls —
  play any of them, any time, as many times as you want. Section 1
  (Getting Started) also auto-plays once, automatically, the very first
  time a brand-new account signs in — never again after that, and the
  other three never auto-play at all.
- **Account → Delete my account (added 2026-08-29):** permanently
  deletes your account and everything tied to it — every rifle, load,
  and calibration record. One confirmation dialog, no undo. This is
  real deletion, not deactivation — there's no way to get the data back
  afterward, and no separate "are you sure you're sure" step beyond
  that one dialog.
- **Enable voice ("Ballistica"):** the one required tap to arm the
  wake-word listener (mic permission).
- **Rifle picker:** a dropdown/list of every saved rifle. Clicking one
  **loads its detail for viewing/editing — it does not change which
  rifle voice commands act on.** The "active" rifle for voice is only
  changed by a voice switch command, or automatically when you save a
  brand-new rifle through either voice or the web form.
- **+ New / Edit (rifle):** opens a blank or filled-in rifle form —
  the same field set as voice setup (name, caliber, barrel, twist rate,
  scope height, reticle unit, click value, optic type, make/model,
  suppressor + type, magnification, objective, focal plane, dot size,
  reticle type).
- **Save rifle:** creates or updates that rifle. **Saving a brand-new
  rifle makes it active** (same as voice) — this also changes what
  voice commands act on next.
- **Delete rifle:** see §8.
- **Load dropdown (within a rifle):** switches which load's fields are
  shown **in the edit form only** — same caveat as the rifle picker,
  this does **not** change the active load for voice or for a future
  "GET SOLUTION" unless you also pick it there.
- **+ New (load) / Save load:** same field set as voice load setup.
  **Saving always makes that load active** on its rifle — there is
  **no way in the web app to switch back to a previously-saved load**
  without either using voice ("switch to <name>") or re-saving it.
- **Distance + GET SOLUTION:** gets a drop/windage solution for
  whichever rifle is open and whichever load is selected in that rifle's
  dropdown — this part **is** explicit per-request (sends the exact
  rifle/load names), unlike voice's implicit "active" concept.
- **Conditions (optional) panel** (Temp, Humidity, Wind, Altitude,
  Pressure, wind Clock): its own one-shot values sent with each GET
  SOLUTION click — defaults (59°F, 0% humidity, no wind) apply unless
  filled in. Entirely separate from voice's conditions/wind state (§4).
- **"Use my location" (added 2026-08-28):** pre-fills Temp, Humidity,
  Wind speed, Altitude, and Pressure from the nearest live-reporting
  weather station, using your device's GPS — a real request to a public
  aviation-weather API, not a guess. Shows which station it used, how
  far away, and how recent the reading is, so you can judge it before
  trusting it. **Does not fill in the wind Clock (direction) field** —
  your GPS position doesn't tell the app which way you're aimed, so
  guessing that specific number would risk being wrong in a way that
  looks authoritative; you still set that one yourself.

---

## 14. Known limitations — things NOT currently supported

Verified absent from the code, not just undocumented:

- **No shot log.** There's no feature to log individual shots fired,
  hits/misses, or corrections during live fire for later review.
  Chronograph "calibration" only records shot **velocities** in memory
  during that one session, and only the resulting average gets saved —
  individual shot readings aren't persisted anywhere.
- **No voice persona selection.** A male/female voice choice was
  designed and scoped (male voice picked: OpenAI's "Onyx") but never
  implemented — the app always speaks in the single current voice.
  Tracked in `BACKLOG.md`.
- **No way to delete a single load** — only a whole rifle (and
  everything on it) can be deleted, by voice or web.
- **No way to switch the active load from the web app** — only by
  voice, or by re-saving a load through the form.
- **No spreadsheet/CSV import** for existing rifle/load/shooting data —
  backlogged, sequenced after this multi-tenancy work.
- **No bundled reference bullet/BC/factory-load database** — every
  rifle and load is entered from scratch. A seed dataset from published
  manufacturer data is scoped in `BACKLOG.md` but not built.
- **No in-app disclaimer/liability language** anywhere in the product —
  flagged separately in `RISK_REGISTER.md`.
- **`/calc/angle`, `/calc/drop-table`, and `/calc/mpbr-zero` have no
  REST endpoint at all anymore** (updated 2026-08-28 — the old
  unauthenticated single-tenant versions were removed in a security
  hardening pass, and no `/v2` equivalent was built since the web UI
  never called them). The underlying commands still work exactly as
  described above (table, minimum-spread zero, angle-solving) — they're
  just voice/CLI-only now, reached through `/v2/voice/query`'s natural-
  language routing rather than a dedicated calc endpoint.

---

## 15. Other things worth your attention

- **"quit"/"exit" mean different things depending on context.** Said
  normally, they end the session (in the API, this just replies "Ending
  session" — there's no real process to exit). Said **while a setup or
  calibration interview is open**, the exact same words instead
  **cancel just the interview**, leaving the rest of the app running.
  Same phrase, different scope, purely based on what's currently open.
- **"list rifles"/"list loads" require the exact phrase** — unlike
  almost every other command, there's no LLM fallback tool for these,
  so natural variants like "what rifles do I have saved" won't work at
  all (falls through to "didn't understand" or a small-talk decline).
- **New-account email confirmation:** depending on your Supabase
  project's auth settings, creating an account through the web app may
  require confirming via an emailed link before the first sign-in
  succeeds. Worth testing once you're ready to create your real account
  so there's no surprise mid-flow.

---

*Grounded entirely in `ballistica/cli.py`, `ballistica/intent.py`, and
`ballistica/web/index.html` as of commit `11c49d6`. If behavior changes
in a later session, this document needs a re-pass against the code
again — it isn't a spec, it's a snapshot.*
