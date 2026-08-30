# Ballistica Risk Register

A living one-page register of structural risks that don't show up in day-to-day
feature work. Updated whenever a relevant decision is made, not just when
something breaks. Each entry: current status (grounded in verified code/repo
state, not assumption), why it matters, next concrete step, owning lens.

This exists because of Addendum 39/40: a real cost decision (Render tier
upgrade) went through with no Finance or Marketing review, which surfaced a
broader pattern -- lenses only get applied when actively judged worth invoking,
so gaps stay invisible until something forces them into view. This register is
the forcing function for the risks that don't have a specific incident yet.

---

## Legal / Liability -- RESOLVED (2026-08-28)

**Status:** Closed for account creation. Attorney-approved liability
waiver (`Ballistica_Liability_Waiver_DRAFT.docx`, 11 sections covering
reference-only data, assumption of risk, release/waiver, indemnification,
no warranty, and eligibility) is now wired into a standalone acceptance
screen shown during account creation -- full text displayed (not just
linked), unchecked-by-default checkbox with the attorney-specified
acknowledgment text, account creation blocked client-side until checked,
kept deliberately separate from any general Terms of Service step.
Acceptance is recorded two ways: immediately in the Supabase signup
call's own `user_metadata` (captured atomically at signup regardless of
email-confirmation timing) and, once the account has a real session, in
an append-only `waiver_acceptances` table (`db/004_waiver_acceptance.sql`
-- no UPDATE/DELETE policy for anyone, including the row's own owner)
recording the exact version/hash of the text shown, so a later revision
to the waiver doesn't retroactively muddy what an earlier user actually
agreed to. Verified live end-to-end against the real Supabase project,
including the confirmation-required-signup path (metadata capture
confirmed by decoding the real confirmation JWT).

**Why it mattered:** Ballistica outputs are used for live-fire ballistic
corrections and handloading, both inherently dangerous activities. A wrong
dial direction, a unit mixup, a misheard voice correction, or bad
community-sourced load data has real physical consequences. This closes
the account-creation gate; it does not cover in-app reminders during
actual use (see next step).

**In-session reminder -- done (2026-08-29).** The remaining "next step"
here was a lightweight spoken caveat during actual voice use, not just
at signup. Rick gave final wording and shipped it: "My data is for
reference only, always verify before firing," woven into
`GREETING_PHRASES` (`ballistica/web/index.html`), spoken as part of
Ballistica's own greeting on the first wake-word of every voice
session -- not a popup, not a one-time notice, and not appended as a
separate sentence. Two independent external security reviews had also
flagged this as outstanding (see MULTI_TENANCY_DESIGN.md §22 for full
detail); this closes it.

**Owning lens:** Attorney approved the waiver content; Rick gave the
final spoken-reminder wording and decided to ship it now rather than
defer further; Build implemented and verified both pieces.

---

## Security / Multi-Tenancy Foundation

**Status (updated 2026-08-28, post-review hardening pass):** Resolved for
the live app, and the dormant single-tenant fallback is gone. Supabase-
backed per-user storage (`SupabaseProfileStore`), Postgres RLS as the real
isolation boundary, JWT-auth-gated `/v2/*` endpoints, and a full adversarial
tenant-isolation test suite were built, tested, and verified live; the live
voice and web UI were cut over to this path (§10). Following two
independent external reviews (Grok, ChatGPT) flagging the old unauthenticated
single-tenant surface as exposed risk rather than a real safety net now
that the cutover was confirmed stable, it was removed outright (not just
disabled) -- the shared `store`/`voice_cli` globals and every unauthenticated
endpoint (`/rifles`, `/status`, `/voice/query`, `/calc/drop-table`,
`/calc/mpbr-zero`, `/calc/angle`) are gone from `api.py`. `/v2/*` is now the
app's only data surface; the standalone single-tenant CLI (`python -m
ballistica.cli`) is untouched. Full test suite green after the removal
(68 passed) with the affected tests migrated to exercise either the `/v2`
API directly (real account, live network) or `BallisticaCLI` directly
(no HTTP dependency at all) depending on which layer they actually guard.

Same review pass also found and closed a real cross-reference ownership
gap: RLS on `loads`/`rifles` checked `user_id` on the row itself but never
verified a *related* id the row references (`loads.rifle_id`,
`rifles.active_load_id`) actually belongs to that same user -- confirmed
live against the real project (a legitimately-authenticated user could
insert a load referencing another user's real rifle_id, or point their own
rifle's active_load_id at another user's real load). Ballistica's own `/v2`
endpoints never exposed a path to do this (no raw id is ever accepted from
a client, only names), so this was reachable only by a client that skips
the app and talks to Supabase's REST API directly with their own valid
token -- real, but not exploitable through the actual product. Fix is
written (`db/003_close_cross_reference_ownership_gap.sql`) with two
adversarial tests that currently fail (proving the gap) and will pass once
applied -- **needs Rick to run it in the Supabase SQL Editor** (same as
migrations 001/002; no service-role/DDL access from this session).

Rate limiting also added (per-IP, `slowapi`, 100/minute default, 20/minute
on the three endpoints that proxy paid third-party APIs -- `/voice/speak`,
`/voice/transcribe`, `/v2/voice/query`) -- see this session's report for
the exact reasoning and a flagged caveat about trusting Render's
`X-Forwarded-For` header for the client IP.

**Why it mattered:** This was fine while there was exactly one user (Rick).
It stops being fine the moment there's a second real user, and retrofitting
auth + per-user isolation + audit logging after the fact is materially more
expensive than designing for it up front -- especially since the stated
future goal (aggregate anonymized cross-user shooting data) requires
knowing which data came from which user in the first place.

**Update 2026-08-29:** all four migrations above have been run
successfully (confirmed by Rick, including the urgent `db/007` fix for
a circular-RLS bug `db/003` itself introduced -- see MULTI_TENANCY_
DESIGN.md §11). Self-service account deletion is also now built
(§15) -- closes the account-side half of what "a real second user"
needs, though it still needs `SUPABASE_SERVICE_ROLE_KEY` configured
before it actually works (not set yet as of this writing). Remaining:
a decision on migrating Rick's existing single-tenant data into his
real account, whenever he's ready to treat that account as permanent
rather than for testing.

**Owning lens:** Build scoped and shipped the technical approach; Chief of
Staff to track the account-creation/migration decision and the pending
Supabase migration with Rick; Finance/Marketing already confirmed this
doesn't block the aggregate-data roadmap.

---

## Aggregate Data Anonymization -- RESOLVED (2026-08-28)

**Status:** Closed. Flagged as a decision point on 2026-08-28 (this
entry originally read "decision needed, not yet resolved" -- see git
history for that version if useful); Rick's follow-up instruction the
same day made the call: **anonymize at ingestion**, not at account
deletion. Built to that spec immediately after the decision, since no
aggregate pipeline exists in production yet and the `events` table was
confirmed empty -- no migration/backfill risk, nothing to get wrong by
moving fast on it.

**What changed:** `events.user_id` is gone from the schema entirely
(`db/005_anonymize_events_at_ingestion.sql` drops and recreates the
table without that column, superseding the original nullable-then-
nulled-on-delete design from `db/001`/§6.2 of MULTI_TENANCY_DESIGN.md).
A contribution carries no user identifier at any point in its life --
not a link that gets severed later, an link that's never established.
Practical consequence Rick called out explicitly: if a user later
deletes their account, there is nothing further to do for their past
aggregate contributions, because those contributions were never tied to
them internally in the first place. Account deletion still needs to
remove personal/individual records (rifles, loads, conversation_state,
profile) exactly as before -- this only ever concerned the shared pool.

**Confirmed in writing, per Rick's explicit ask:** aggregate
contributions cannot be traced back to a specific user by any internal
process, including by Rick or DT with full database access -- the
`events` table has no column, in this schema, that could be joined back
to `auth.users` or any other per-user table. (The one thing this schema
change cannot itself guarantee: a future contribution endpoint must
still avoid putting identifying data *inside* `payload` -- carried
forward as an explicit requirement in db/005's own comments, same as
the original design already called out.)

**Owning lens:** Rick decided; Build implemented and verified same-day.

---

## Data Architecture / Schema Evolution

**Status:** Flat JSON file (`profiles.json`), no schema versioning, no
migration mechanism. Works cleanly for one user's rifles/loads today.

**Why it matters:** Every field added this session (optic_type, suppressor
fields, etc.) has been a manual dataclass edit with no migration path for
existing data -- fine at this scale, but the pattern doesn't extend to
multi-user data, historical chronograph strings at volume, or aggregate
analytics. This is the same underlying gap as the persistent-disk incident:
the storage model wasn't designed for what it's being asked to eventually do.

**Next step:** No action needed now. When multi-tenancy work starts (above),
revisit storage as part of the same design pass rather than as an
afterthought -- likely a real database at that point (Render offers managed
Postgres), not a bigger JSON file.

**Owning lens:** Build.

---

## Competitive / IP Exposure

**Status:** Not evaluated. No prior discussion in this project's history of
whether the voice-first natural-language ballistics correction loop, or any
specific interaction pattern, warrants protection.

**Why it matters:** The natural-language voice correction loop is the
product's actual differentiator (per the Marketing lens's north star). If
it's genuinely novel, that's worth knowing before a well-resourced
competitor can freely copy it once the app is public.

**Next step:** Not a technical task -- this needs Rick's own judgment call
(and possibly outside legal advice) on whether it's worth pursuing, not
something to resolve inside a coding session.

**Owning lens:** Marketing to flag competitive read; outside the board
lenses' actual authority to resolve.

---

## Reliability Under Real Range Conditions

**Status:** Mixed. STT has been substantially improved (gpt-4o-transcribe +
domain prompt) and the VAD/silence-detection bugs from earlier addenda are
fixed, but none of it has been stress-tested against gunfire noise, wind
noise, or multiple people talking near the mic. The Bluetooth click fix
(Addendum 30) is deployed but not yet confirmed against real hardware.

**Why it matters:** This is a live-fire tool used outdoors in genuinely
noisy conditions -- the gap between "works in a quiet test" and "works at
the range" is exactly where a voice product either earns trust or loses it.

**Next step:** Rick's own live range testing is the only real signal here --
already the pattern this whole session has followed (nothing gets marked
fixed without his live retest). No new process needed, just keep it up as
new voice-path changes ship.

**Owning lens:** Build implements; verification is inherently Rick's, not
something a lens can substitute for.

---

## Cost Model Realism

**Status:** The Tier 1/2 pricing/cost-to-serve model from Addenda 18-21 was
solid work but is now stale -- it predates the STT provider upgrade to
gpt-4o-transcribe and hasn't been re-run against real per-minute costs and
realistic session lengths.

**Why it matters:** Any pricing decision made off that model right now would
be working from outdated inputs. It was already flagged as provisional
pending the STT investigation's outcome -- that investigation concluded,
but the model was never refreshed to close the loop.

**Next step:** Refresh the cost-to-serve model with current STT/LLM/TTS
pricing and real observed session lengths before it's used for any actual
pricing decision.

**Owning lens:** Finance.

---

## How this gets used

- Reviewed whenever a decision touches cost, infrastructure, data model,
  security, or anything hard to change later (per the Addendum 40 standing
  rule) -- not on a fixed schedule, as part of the same trigger.
- Updated in place as items get resolved or new ones surface -- entries are
  deleted or marked resolved with a date, not left stale.
- Not a replacement for Rick's own periodic external review (a second
  AI/human pass attacking assumptions) -- this register is what makes that
  review fast, since it's a diff against a known list rather than a from-
  scratch audit every time.
