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

## Legal / Liability

**Status:** No disclaimer, terms of service, or "verify independently" language
exists anywhere in the app or repo (verified: grepped the full codebase for
disclaimer/liability/ToS language, zero matches).

**Why it matters:** Ballistica outputs are used for live-fire ballistic
corrections. A wrong dial direction, a unit mixup (MRAD vs MOA), or a
misheard voice correction has real physical consequences, not just a bad UX
moment. Right now there's nothing in the product telling a user this is a
calculation aid, not a substitute for their own verification.

**Next step:** Add a lightweight, non-intrusive disclaimer -- e.g. a one-time
notice on first use, and/or a short spoken caveat the first time voice mode
is enabled in a session ("solutions are a starting point -- verify before
you dial them in"). Not a wall of legal text; a real, minimal safety net.

**Owning lens:** Chief of Staff to scope with Rick; Build to implement.

---

## Security / Multi-Tenancy Foundation

**Status (updated 2026-08-28):** Resolved for the live app. Supabase-backed
per-user storage (`SupabaseProfileStore`), Postgres RLS as the real
isolation boundary, JWT-auth-gated `/v2/*` endpoints, and a full adversarial
tenant-isolation test suite (§7.4, 68/68 passing) were built, tested, and
verified live. The live voice and web UI have now been cut over to this
path (see [MULTI_TENANCY_DESIGN.md](MULTI_TENANCY_DESIGN.md) §10) -- signed-
in, per-user, RLS-isolated storage is the active path a real second user
would get. The original single-tenant `store`/`voice_cli` globals and their
endpoints remain in `api.py`, deliberately left running and untouched as a
dormant fallback, not yet removed.

**Why it mattered:** This was fine while there was exactly one user (Rick).
It stops being fine the moment there's a second real user, and retrofitting
auth + per-user isolation + audit logging after the fact is materially more
expensive than designing for it up front -- especially since the stated
future goal (aggregate anonymized cross-user shooting data) requires
knowing which data came from which user in the first place.

**Remaining before this is fully closed out:** (1) Rick creating his real
account through the new login flow and confirming it works for him live --
his decision, not something to do unilaterally; (2) a decision on migrating
Rick's existing single-tenant data into his new account; (3) eventually
retiring the dormant single-tenant path once the new one is confirmed
solid, rather than carrying both indefinitely.

**Owning lens:** Build scoped and shipped the technical approach; Chief of
Staff to track the account-creation/migration decision with Rick; Finance/
Marketing already confirmed this doesn't block the aggregate-data roadmap.

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
