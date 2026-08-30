# Multi-Tenancy / Security Architecture — Design Document

Status: **Design only — not yet implemented.** Per Rick's instruction, this is the
checkpoint deliverable before implementation starts: design/schema/approach
finalized, not coded and shipped. This document is meant to go to independent
adversarial review (ChatGPT + Grok) before implementation is locked in.

Prepared: 2026-08-23

```
Lenses applied: Build (architecture) / Security (threat model, data handling) / Finance (real infra cost) / Marketing (sync as a side-benefit) / CoS (synthesis)
Key risks flagged: hand-rolled password auth is a common small-app vulnerability source; the aggregate-data seam is illustrative, not final, per Rick's explicit scoping; unbounded voice-endpoint requests are a real cost exposure (added post-Grok-review)
Alternatives considered: hand-rolled email/password auth vs. managed auth provider vs. OAuth-only; per-user JSON files vs. real database; in-memory vs. DB-persisted conversation state (resolved: DB-persisted, see §6.1); Clerk vs. Supabase Auth (open, see §6.5)
Recommendation + confidence: Supabase Auth + Postgres (co-located, one vendor) + per-user-scoped queries (app filter + native RLS via auth.uid()) + DB-persisted conversation state + an events table as the aggregate-data seam — high confidence on the full shape; both open items from the reviews are now closed (§6.2 deletion policy, §7.2 auth vendor); implementation is gated only on the required tenant-isolation test suite (§7.4) actually passing, not on further design
```

**Revision note (2026-08-23): updated twice, after Grok's and then
ChatGPT's adversarial reviews — see §6 and §7.** The in-memory
conversation-state call from §2.3 is superseded by §6.1 (and reconciled
against ChatGPT's pushback in §7.3 — the DB-persisted decision stands).
§2.1's Clerk recommendation is superseded by §7.2's final decision:
Supabase Auth. Sections 1-5 are left as originally written for a clean
record of what changed and why, rather than silently edited in place.
**Both reviews are now fully incorporated; the design is closed pending
Rick's confirmation of §7.2 and the required test suite in §7.4.**

---

## 1. Current state (verified, not assumed)

- `ballistica/api.py`: `store = ProfileStore()` — **one shared instance, module-level, for every request.** No user concept anywhere in the codebase.
- `voice_cli = BallisticaCLI(store)` — **one shared conversation-state object** for every voice interaction. Two simultaneous users' setup/calibration sessions would interleave and corrupt each other's state.
- Storage: a single flat JSON file (`data/profiles.json`, now on a persistent Render disk at `/data`). No per-user separation is possible with this shape — it's one document, not a table.
- Auth: **none.** Grepped `api.py` for auth/session/login patterns earlier this session — the only match was the unrelated `OPENAI_API_KEY` server-secret comment. Every endpoint is open to whoever can reach the URL.
- Confirmed in the Addendum 18-21 cost model's own "bigger gate" finding: this was already flagged as the real prerequisite to selling the product at all, independent of pricing or STT quality.

This document proposes replacing all four of the above.

---

## 2. Target architecture

### 2.1 Authentication — recommend a managed provider, not hand-rolled passwords

**Superseded by §7.2 — final decision is Supabase Auth, not Clerk.** Left
as originally written below for a clean record of the reasoning; the
managed-provider-over-hand-rolled conclusion still holds, only the
specific vendor changed after both reviews.

**Recommendation: Clerk** (or an equivalent managed auth provider — Supabase Auth is
a reasonable alternative if Rick prefers to co-locate auth with the database
provider). Reasoning:

- Hand-rolled password auth (hashing, storage, reset-flow, rate-limiting login
  attempts, session/token management) is exactly the category of "cheap to get
  wrong" that small apps most often get wrong — weak hashing choices, missing
  rate limits, insecure reset-token generation. A managed provider has this
  audited and hardened by specialists; Ballistica doesn't need to be the place
  that re-solves password security.
- **Real, verified pricing**: Clerk's free tier covers 50,000 MRU (monthly
  *retained* users, i.e. people who actually come back — a smaller, harder bar
  than raw signups). Ballistica is nowhere near that scale for a long time.
  Effectively **$0/month** at any realistic near-term user count. [Clerk
  pricing](https://clerk.com/pricing)
- Also unlocks a side-benefit Marketing should note: proper accounts mean
  cross-device sync becomes possible for free — a feature Strelok already has
  (per the competitive brief) and Ballistica currently doesn't, purely as a
  byproduct of doing the security work correctly. Not a new requirement, just
  worth messaging once it ships.

**Open question for review**: sign-in method (email/password via Clerk vs.
Google/Apple OAuth via Clerk, or both). Leaning toward offering both since
Clerk supports it natively at no extra engineering cost — but this is a UX
call, not a security one, worth Rick's own read.

### 2.2 Data model — move off the flat JSON file to Postgres

**Recommendation: Render-managed Postgres**, co-located with the existing
Render-hosted app (same platform, one less vendor relationship). **Real,
verified pricing**: Basic-256mb tier is $6/month; a small Starter-service +
Basic-Postgres setup runs roughly $13/month before storage/usage overage
($0.30/GB beyond included). [Render Postgres
pricing](https://kuberns.com/blogs/render-postgres-pricing-setup-limits/) —
on top of the existing ~$8/month (Starter + disk), total infra lands around
**$19-21/month**. Small, easily justified against any of the pricing tiers
discussed in the market-alignment work.

Proposed schema shape (illustrative — exact columns follow the existing
`Rifle`/`Load` dataclass fields, just user-scoped):

```
users
  id (pk), auth_provider_id (Clerk user id), email, created_at, deleted_at

rifles
  id (pk), user_id (fk -> users.id), name, scope_height_in, caliber, ...
  (every existing Rifle field, unchanged)

loads
  id (pk), rifle_id (fk -> rifles.id), name, bullet_weight_gr, ...
  (every existing Load field, unchanged)
```

**Every query scoped by `user_id`**, enforced two ways rather than relying on
"remember to add the WHERE clause" alone (the single most common real-world
multi-tenancy bug class):
1. Application-level: a query helper that always injects the current
   authenticated user's id, so a forgotten filter is a compile-time-visible
   pattern violation, not a silent leak.
2. Database-level: Postgres row-level security (RLS) policies as a second,
   independent enforcement layer — even a bug in (1) can't leak cross-tenant
   data if (2) also has to agree.

### 2.3 Voice conversation state — per-user, not global

`BallisticaCLI`'s `_setup`/`_calibration`/`_pending_delete` state currently
lives on one shared instance. Proposed: a `dict[user_id, BallisticaCLI]`
in-memory cache, keyed by authenticated user, created on first use per user.

**Known limitation, flagged rather than hidden**: this doesn't survive a
server restart mid-conversation (an in-progress setup interview would reset),
and doesn't scale past a single server process/worker. Both are acceptable at
Ballistica's realistic near-term scale — a reset mid-setup is a "say that
again" UX hit, not data loss, since nothing is written to the database until
a setup session actually confirms. Revisit with DB-persisted session state
if/when the app ever runs multiple worker processes.

### 2.4 Data deletion

`DELETE /account` endpoint: cascading deletes (`ON DELETE CASCADE` at the
schema level) remove the user's rifles, loads, and auth record together, not
as separate manually-sequenced steps prone to partial failure.

**Open question, explicitly deferred**: once the future aggregate-data layer
exists, does account deletion need to purge that user's contribution to
already-anonymized aggregate data? This is a real policy question, but it's
downstream of a system that doesn't exist yet (per Rick's explicit scoping —
the aggregate layer is a separate future project). Flagging it now so it's
not forgotten, not resolving it now.

### 2.5 Security practices checklist

- Passwords: never touch the app's own code/database if using a managed
  auth provider (Clerk stores and hashes credentials itself).
- In transit: HTTPS — already true today, Render terminates TLS on its
  public URLs. No change needed, confirming rather than assuming.
- At rest: encryption at rest for the Postgres data is expected to be a
  platform-provided default with most managed Postgres offerings, but this
  needs confirming directly against Render's own current documentation
  before this is treated as settled — flagged as unverified, not assumed.
- No secrets in client-side code — already true (API keys are server-side
  env vars only); the same discipline extends to the new database
  credentials and auth provider keys.
- Rate limiting on auth endpoints: handled by the managed auth provider if
  that path is chosen; would need building explicitly if hand-rolled auth
  were chosen instead (another point in favor of the managed option).

---

## 3. The aggregate-data "seam" — illustrative, not final

Per Rick's explicit constraint: build a clean extension point, not the
aggregate system itself. Proposed seam: an `events` table, populated from day
one even though nothing reads it yet —

```
solve_events
  id (pk), user_id (fk), created_at, rifle_caliber, range_yd,
  drop_mrad, windage_mrad, (other non-identifying ballistic inputs/outputs)

calibration_events
  id (pk), user_id (fk), created_at, shot_velocities_fps[]
```

The point: logging these per-user from the start means a future aggregation
project can strip `user_id` and any identifying fields and build on real
historical data, instead of needing every user to have been using a
not-yet-built instrumentation system before any aggregate data exists.
**This is a shape, not a commitment** — the actual categorization, data
quality, and statistical handling is explicitly out of scope here per Rick's
own instruction, and should be treated as a placeholder for that future
project to redesign around, not a locked schema.

---

## 4. What's verified vs. theoretical

**Verified with real evidence:**
- Current architecture has zero auth and one shared global store (grepped,
  not assumed).
- Render Postgres pricing (web search, current as of this document).
- Clerk pricing and free-tier scale (web search, current as of this
  document).

**Theoretical / not yet tested:**
- The entire schema and query-scoping approach above — none of this is
  built or running yet.
- Postgres at-rest encryption on Render specifically — not independently
  confirmed against Render's own docs.
- Whether Clerk (vs. Supabase Auth or another provider) is the right final
  choice — one credible option evaluated, not an exhaustive vendor
  comparison.
- The in-memory per-user conversation-state approach's real-world behavior
  under restart — reasoned through, not load-tested.

## 5. Open questions for adversarial review

1. Is a managed auth provider (Clerk) the right call, or is there a reason
   to prefer hand-rolled auth or a different provider (Supabase Auth,
   Auth0)?
2. Is the events-table seam shape reasonable, or does it bake in
   assumptions the future aggregate-data project would rather not inherit?
3. Is in-memory (not DB-persisted) per-user conversation state an
   acceptable tradeoff at this stage, or should session state be
   DB-persisted from the start given how cheap that would be to add now
   vs. retrofit later?
4. Anything in the cascading-delete / data-deletion design that doesn't
   hold up — e.g., audit-log requirements this doesn't yet account for?
5. Any cross-tenant leakage vector not covered by the two-layer
   (application + RLS) query-scoping approach?

---

## 6. Grok review response (2026-08-23)

Grok's review confirmed the overall shape and flagged real gaps — five of
its questions are addressed below with a design change or a concrete
answer; the two genuine policy calls are left for Rick, not decided here.

### 6.1 Design change: conversation state moves to DB-persisted, not in-memory

Grok's pushback is right, and it changes the recommendation from §2.3.
The original "in-memory, acceptable tradeoff" reasoning underweighted how
often this app actually restarts — Render redeploys have happened
repeatedly over the course of this same project, not as a rare edge case.
For a voice-first product, dropping a mid-setup conversation on every
deploy is a real, recurring rough edge, not a theoretical one.

**Revised**: a `conversation_state` table (`user_id`, `state_json`,
`updated_at`) replacing the in-memory dict. This is exactly the same
"cheap to build in now, expensive to retrofit later" reasoning already
applied to the aggregate-data seam — it should have been applied to this
too. Low additional cost (one more small table, no new infra) for a real
reliability gain.

### 6.2 Account-deletion data policy — DECIDED, CLOSED (2026-08-23; anonymization mechanism updated 2026-08-28)

**Rick's decision, final, no further input needed on this question:**
anonymize, don't delete the ballistic data. Matches the Option B
recommendation above, confirmed rather than left open.

**Updated 2026-08-28 — anonymization moved to ingestion, not deletion.**
The original mechanism below (nullable `events.user_id`, severed on
account deletion) was superseded after this session's security/privacy
hardening review surfaced a real conflict: Rick's separately-stated
intent was that a contribution should have **no traceable link back to
the user at all, even internally, from the moment it enters the pool** —
not just hidden from other users, and not only severed later at deletion
time. Flagged rather than resolved unilaterally at the time (see
RISK_REGISTER.md's "Aggregate Data Anonymization" entry); Rick's
follow-up decision closes it: **anonymize at ingestion.** `events` has no
`user_id` column at all anymore (db/005_anonymize_events_at_ingestion.sql
drops and recreates it without one) — not nullable-then-nulled-later,
absent from the moment a row is written. The account-deletion
implication below ("strip the identifying reference... atomically")
is now moot for `events` specifically, for the strongest possible
reason: there is nothing to strip, because nothing was ever attached.
Everything else about this section's reasoning (why anonymize rather
than delete the underlying ballistic facts at all) is unchanged and
still the closed decision -- only *when* the link is severed changed,
from "at deletion" to "never established."

**The policy, in Rick's own framing:** everything personally identifying
about the departed user is deleted without exception — name, contact
info, account credentials, any direct identifier tying data back to that
specific person. The ballistic data itself, once it's part of the
aggregate pool, is retained — fully anonymized, tied to no account.
Rick's reasoning: once a contribution is folded into the aggregate pool
it stops being meaningfully "theirs" — a blended dataset built from
everyone's contributions together, the way a drop of dye disappears into
a bucket of paint, with no clean way to re-isolate one person's
contribution as a separate thing. What must be deleted without exception
is anything that could re-identify the person, not the underlying facts
once they've become part of something larger than any one account. The
resulting honest answer to a departing user: *"you personally are gone —
but the shooting data already became part of a shared pool that no
longer belongs to any one person."*

**Implementation implications, as specified:**
- Cascading delete on account deletion targets three things: the user
  record, auth credentials, and any direct-identifier fields on
  historical records/profiles.
- ~~Anonymization happens in the same deletion transaction as the
  identity delete~~ — **superseded 2026-08-28**: `events` rows are
  anonymous from the moment they're written (no `user_id` column at
  all), so there is no identifying reference on them for account
  deletion to strip in the first place. This bullet described the
  original at-deletion mechanism; see the updated note above.
- ~~No change to the events-table seam's shape (§3)~~ — **superseded**:
  the shape did change (db/005) -- `user_id` removed entirely, not
  merely nulled on delete.

This closes the first of the two items §6.6 flagged as needing Rick's
decision. Clerk vs. Supabase Auth (§6.5) remains open, pending ChatGPT's
review pass.

### 6.3 Missing pieces — addressed

- **Rate limiting**: added to scope. Voice endpoints call paid APIs
  (OpenAI STT/TTS, Anthropic) per request — an abusive or looped client
  is a real, direct cost exposure, not just an availability concern. Needs
  per-user request throttling before this ships to a second real user.
- **Audit logging**: a minimal version added to scope — who did what,
  when, for account-level actions (creation, deletion, auth events) at
  least. Not a full activity log for every rifle/load edit at this stage.
- **Secrets management**: no new pattern needed — Clerk and database
  credentials follow the same server-side environment-variable approach
  already in use for `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, confirmed
  working in production today.
- **Migration path**: smaller than the general concern suggests, given
  Ballistica's actual current state — there is exactly one existing data
  record (Rick's own profile) to migrate, not a real multi-user dataset.
  The migration is a one-time script inserting that single profile's rows
  under a first user account, not a dual-write/cutover project.

### 6.4 Pre-implementation security test requirements (added to scope)

Answering Grok's three adversarial questions with concrete acceptance
criteria rather than reasoning alone:
- **RLS bypass**: before shipping, a test must confirm that querying the
  database directly (bypassing the application-level filter entirely,
  simulating a bug in that layer) still cannot return another user's rows
  — i.e. RLS alone has to hold, not just the app-level filter. This
  becomes a required test, not just a design intention.
- **Clerk webhook failure/delay**: the app's local `users` table can only
  be a cache of Clerk's own state, never the source of truth — needs
  either verifying against Clerk's session token at request time (not
  trusting a possibly-stale local record) or a periodic reconciliation
  job. Exact mechanism to confirm against Clerk's own documented best
  practice during implementation, not solved from first principles here.
- **Session/token theft for voice state**: no new attack surface beyond
  what correct auth already has to handle — this rides on Clerk's own
  session token security (expiry, rotation), which needs confirming
  against Clerk's actual documented behavior during implementation.

### 6.5 Clerk vs. alternatives — short comparison, as requested

Real, verified-where-possible numbers, with one honest caveat: sources
disagree on Clerk's exact current free-tier size (one search found it
raised to 50,000 MRU as of Feb 2026; a separate comparison source still
shows the older 10,000 MAU figure). Not resolved with full confidence
either way — but Ballistica is nowhere near either number for a long
time, so it doesn't change the recommendation.

| Provider | Free tier (as found, see caveat) | Notes |
|---|---|---|
| Clerk | 10,000-50,000 MAU/MRU (sources disagree) | Best developer experience for this stack; the option evaluated in §2.1 |
| Auth0 | ~25,000 MAU (recently expanded) | Deepest enterprise/compliance feature set — more than Ballistica needs right now |
| Supabase Auth | ~50,000 MAU | **Worth real consideration**: bundles auth with Postgres hosting in one vendor, which would mean one vendor relationship instead of two (Supabase for both DB+auth, vs. Clerk+Render-Postgres). Cheapest at real scale if the app ever grows large (~$25/mo at 100k MAU vs. Clerk's ~$1,800/mo per one comparison). |

**Updated recommendation given this**: Clerk remains reasonable for
developer experience, but Supabase Auth is a real enough alternative
(same cost class today, meaningfully cheaper at scale, and simplifies to
one vendor instead of two) that it deserves a real side-by-side
evaluation before implementation starts, not just a note that
alternatives exist. Flagging as upgraded from "documented alternative"
to "worth Rick's own evaluation before locking in."

### 6.6 Updated recommendation

Per Grok's own closing recommendation, three things needed to close
before implementation starts:
1. Aggregate-data retention policy on deletion — **CLOSED (§6.2):
   anonymize on deletion, atomic with the identity delete. Final, no
   further input needed.**
2. Conversation state persistence — **resolved above (§6.1): moving to
   DB-persisted, no longer open.**
3. Clerk vs. alternatives — **narrowed to Clerk vs. Supabase Auth
   specifically (§6.5), needs Rick's evaluation before locking in.**

---

## 7. ChatGPT review response (2026-08-23)

ChatGPT's review confirms the architecture direction and the (now closed)
deletion policy without conflict, and adds one substantive new thing this
design was missing: it isn't enough for the isolation boundary to look
correct on paper — it has to be proven with actual adversarial tests
before multi-tenancy is called complete. That requirement is now binding,
not optional, per §7.4 below.

### 7.1 Deletion policy — confirmed, not reopened

Per Rick's own instruction, this is not being re-litigated. Noted only
that ChatGPT's independent reasoning (personal data vs. aggregate/
anonymized data are different categories with different rules) reaches
the same place as Rick's closed decision in §6.2 — reinforcement, not a
new input.

### 7.2 Clerk vs. Supabase Auth — CLOSED

Per Rick's instruction not to keep researching indefinitely: running both
providers against ChatGPT's seven-point checklist rather than a broad
comparison, and closing the decision here.

| Requirement | Clerk | Supabase Auth |
|---|---|---|
| Stable unique user ID | ✅ | ✅ |
| Secure authentication (hashing/OAuth handled by the provider) | ✅ | ✅ |
| Reliable session/token validation | ✅ (JWT + JWKS) | ✅ (JWT + JWKS) |
| Straightforward FastAPI integration | ✅ — but tooling is primarily polished for Next.js frontends; backend (FastAPI) integration is a standard but secondary path | ✅ — documented JWT-verification-via-dependency-injection pattern, equally standard |
| Account deletion support | ✅ — admin API + webhook | ✅ — admin API |
| No Ballistica-owned password storage | ✅ | ✅ |
| Predictable cost | ✅ (free tier large enough for a long time; exact figure disputed across sources, doesn't change the conclusion) | ✅ (same cost class; bundled with DB hosting, not a separate bill) |

All seven pass for both — this alone doesn't break the tie, which is
exactly why ChatGPT is right not to turn this into more research. What
breaks it is two specific things this design already cares about most:

1. **ChatGPT's own #1 flagged security question — how does authenticated
   identity get reliably propagated into Postgres's security context?**
   Supabase Auth has a native, first-class answer: `auth.uid()` is a
   built-in RLS helper function that reads directly from the verified
   JWT, so a policy like `(select auth.uid()) = user_id` enforces
   isolation **even if application code is bypassed entirely** — the
   exact property ChatGPT wants proven. With Clerk, the same guarantee
   requires the application to manually propagate Clerk's user ID into
   Postgres's session context on every request (e.g. `SET LOCAL` inside
   each transaction) — achievable, but a hand-built mechanism to get
   right, not a supported primitive.
2. **Rick's closed deletion-policy requirement — anonymization must
   happen in the same transaction as the identity delete, not a deferred
   step.** With Supabase, the identity record and the data being
   anonymized live in the *same* Postgres database — a single
   `BEGIN; ... COMMIT;` genuinely covers both. With Clerk, the identity
   lives in a separate external system reachable only over HTTP — true
   single-transaction atomicity across Clerk's user store and
   Ballistica's own Postgres isn't achievable in the strict sense; it
   would need a two-phase delete with its own failure-handling logic.
   Supabase satisfies Rick's exact requirement more directly; Clerk would
   need extra engineering to approximate it.

**Decision: Supabase Auth.** Not because Clerk fails the checklist — it
doesn't — but because Supabase's architecture directly and natively
serves the two specific guarantees this project has already said matter
most, without extra engineering to bridge the gap. This closes the last
open item from §6.6.

**CONFIRMED by Rick, 2026-08-23. Final — implementation is authorized to
begin.**

### 7.3 In-memory vs. DB-persisted conversation state — reconciled, not reversed

ChatGPT pushed back on §6.1's move to DB-persisted state, specifically
against the justification "eventually we'll have multiple servers" —
and that pushback is correct; that particular justification would be
premature architecture.

But that isn't the justification §6.1 actually used. The reasoning there
was that this project's *current* deploy frequency already makes
in-memory state fragile *today*, at today's single-process scale — a
different claim than "future horizontal scaling requires it." Grok's
point and ChatGPT's rebuttal are each right about the justification they
were addressing; they're not actually talking about the same claim.
**§6.1 stands, with the reasoning sharpened**: DB-persisted state is
justified by observed restart frequency at current scale, not by
anticipated future scaling.

What both reviewers agree on regardless of this question, and what's now
binding: **a voice-state object must never be able to cross user
boundaries, in-memory or persisted, and this must be tested explicitly**
— folded into the required test suite below, §7.4.

### 7.4 Required tenant-isolation test suite (binding — multi-tenancy is not complete until these pass)

Per ChatGPT: a design that looks correct isn't the same as one that's
been proven correct. This is now a required acceptance-test list, not an
optional nice-to-have, before multi-tenancy work is considered done:

**The attack scenario to test, every time:** User A is malicious and
knows or guesses User B's IDs (user, rifle, load, event). Can User A
cause the system to return, modify, delete, or infer any of User B's
data?

Test across every operation — create, read, update, delete, list,
lookup-by-ID, voice commands, profile/load selection, conversation
state, event creation, account deletion — and through **both** direct
API access and voice-mediated access (the voice path is a second,
separate surface with its own isolation requirements, not covered just
because the API is covered).

Specific required tests:
1. Can any API endpoint return another user's object by guessing/
   supplying their ID?
2. Are list/search endpoints tenant-scoped (not just single-object
   lookups)?
3. Are update/delete operations tenant-scoped?
4. Can a voice command ever operate against another user's rifle/load?
5. Can conversation/voice state cross users under any sequence of
   requests?
6. What happens when auth is missing, expired, malformed, or forged —
   does every code path fail closed, not open?
7. Direct-database test (not through the app at all): confirm RLS alone
   blocks cross-tenant reads, independent of application-level filtering
   — i.e. the app-level filter being buggy or absent must not be
   sufficient to leak data.

### 7.5 Authorization vs. authentication — design principle, not urgent work

Authentication answers who a user is; authorization answers what they're
allowed to do — and the design so far has been entirely about the
former. Not urgent to build now with a single user tier, but the
architecture must not assume "authenticated user = unrestricted access
to everything associated with that user" as a hardcoded shortcut,
since retrofitting real authorization later (admin/support roles,
service accounts for future aggregate-data processing jobs) is
meaningfully harder if the whole codebase was written assuming there's
only ever one kind of authenticated actor. Concretely: keep the
"what can this identity do" check as its own explicit layer, even while
it's trivial (one tier, everyone can do the same things) today.

### 7.6 Aggregate schema caution — already aligned, no change needed

ChatGPT: don't finalize the aggregate-data schema until that project is
formally scoped, keep the events table "deliberately dumb" for now. This
already matches §3's own framing ("a shape, not a commitment... a
placeholder for that future project to redesign around, not a locked
schema") — confirmation, not a new requirement. One addition worth
folding in from ChatGPT's specific field list: each event should record
its schema/version and whether it's been incorporated into an aggregate
dataset yet, alongside the fields §3 already lists — cheap to add now,
useful once that project actually starts.

### 7.7 Remaining open questions, tracked for implementation (not blocking the checkpoint)

ChatGPT's fuller list, not all answerable at design time — carried
forward as things implementation must address, not re-litigated here:
missing/expired/forged-auth handling (§7.4 item 6 makes this a required
test); admin/service-account separation (§7.5 covers the principle);
what's retained in logs/backups/telemetry/third-party services after a
deletion, and whether deleted data could be reconstructed from those
secondary systems (genuinely open, needs answering during
implementation, not designed away here); behavior under Postgres or the
auth provider being temporarily unavailable (standard reliability
engineering, not a tenant-isolation question, lower priority than §7.4).

### 7.8 Status after both reviews

Both independent reviews are now incorporated. Every item either of them
flagged is closed, decided, or explicitly tracked as required
implementation/test work — nothing is being carried forward as vague
"someone should think about this later." **CONFIRMED by Rick, 2026-08-23
— implementation begins now.**

---

## 8. Implementation progress

### 8.1 Schema written — `db/001_multi_tenant_schema.sql`

Full DDL for `profiles`, `rifles`, `loads`, `conversation_state`, and
`events`, with RLS policies on every table. One implementation detail
found while writing it that's stronger than what §7.2 described:

**The atomic-anonymization requirement (§6.2) is satisfied at the
database level, not just by careful application code.** `events.user_id`
uses `ON DELETE SET NULL` rather than `ON DELETE CASCADE` — this is
Supabase's own documented pattern for exactly this situation (a
referencing row that should outlive the deleted user). When
`auth.admin.deleteUser()` performs its underlying `DELETE FROM
auth.users`, every foreign-key constraint referencing it fires as part
of that *same* Postgres transaction automatically — `rifles`/`loads`/
`conversation_state`/`profiles` are deleted, `events.user_id` is nulled,
all atomically, guaranteed by Postgres itself rather than by
remembering to write the right application-level transaction code. This
is a stronger guarantee than the single-`BEGIN/COMMIT` framing in §7.2
suggested.

**One application-level requirement this schema can't enforce on its
own, noted so it isn't lost**: `events.payload` must never contain
identifying fields. Nulling `user_id` only anonymizes the row if the
payload itself was never PII to begin with — that discipline has to
hold wherever code writes to this table.

### 8.2 Blocked on: a real Supabase project

This is where implementation has to pause for something only Rick can
do — creating third-party accounts and entering payment/billing
information isn't something I'll do on his behalf, regardless of
authorization, the same boundary that applied to Render's persistent
disk in Addendum 38.

**What's needed from Rick:**
1. Create a Supabase project (free tier is enough to start —
   supabase.com, sign up, "New Project").
2. In the project's SQL Editor, run `db/001_multi_tenant_schema.sql`
   (now in the repo) — creates the schema this design specifies.
3. From Project Settings → API, get: the Project URL, the `anon` public
   key, and the JWT secret (or JWKS URL, depending on how Supabase
   currently exposes it for server-side verification). Set these as
   environment variables the same way `OPENAI_API_KEY`/
   `ANTHROPIC_API_KEY` already are — never paste them into chat.

Once those env vars exist (locally in `.env` for testing, and in
Render's environment config for production), implementation continues:
a FastAPI dependency that verifies the Supabase JWT on every request,
replacing the single shared `store`/`voice_cli` globals in `api.py` with
per-user-scoped equivalents, and the migration script moving Rick's one
existing profile from the flat JSON file into the new schema.

Not writing that request-handling code blind before there's a real
project to test it against — auth-critical code that's never been
exercised against a live system is exactly the kind of thing that
should be tested before being trusted, not assumed correct because it
reads correctly.

---

## 9. Implementation progress, continued (2026-08-24, autonomous session)

### 9.1 SupabaseProfileStore — built

`ballistica/supabase_store.py`: a thin `ProfileStore` subclass. Rifle/
Load and all of ProfileStore's fuzzy-matching/validation/CRUD logic are
reused completely unchanged (already correct, already tested) --
`load()`/`save()` are the only two methods overridden, talking to
Supabase's REST API (PostgREST) via `httpx` (already a dependency, no
new SDK added) instead of the flat JSON file. Every request uses the
authenticated user's own access token, never a service-role key --
that's what makes RLS the real enforcement boundary, not just this
Python code's own filtering.

`active_rifle_name` (no `rifles` table column for this) is stored in
`conversation_state.state_json` instead, read-modify-write per save --
that table already exists for per-user session preference/state, and
"which rifle is currently selected" fits it directly rather than
needing a schema change.

### 9.2 Real blocker found, fixed, and CONFIRMED CLOSED (2026-08-24)

Testing `SupabaseProfileStore` against the real project immediately hit
`403 permission denied for table rifles` (Postgres 42501). Real gap:
001's RLS policies restrict *which rows* a role can see, but Postgres
separately requires the base table-level GRANT before `authenticated`
can touch a table at all -- 001 never granted this. Fix prepared as
`db/002_grant_authenticated_access.sql`, sent to Rick as a download
(same pattern as the original schema file), run by him in the SQL
Editor against the correct project -- confirmed via screenshot
("Success. No rows returned") after first catching that the project
selector was pointed at an unrelated project ("pearl-mike-pilot") and
having him switch it before running. **Grant applied, confirmed live
by re-running the tests below.**

### 9.3 Adversarial tenant-isolation suite — WRITTEN AND PASSING, per §7.4

`tests/test_tenant_isolation.py`: real tests against the real project
(no mocking), using two genuine throwaway Supabase accounts
(dt-auth-smoketest@mailinator.com / dt-tenant-test-b@mailinator.com,
pre-provisioned so tests sign in rather than repeating the email-
confirmation round-trip every run). Covers §7.4's required scenarios:
list/search tenant-scoping, by-ID lookup, update, delete, a forged-
ownership insert attempt (User B trying to write a row claiming User
A's user_id), a cross-table check (loads inherit the same isolation as
their parent rifle), and the unauthenticated-request baseline.

**All 7 pass against the live project, for the right reason** -- not
just "the request errored," but the follow-up checks in each test
confirm User A's data was genuinely untouched after User B's attempt.
One test bug caught and fixed along the way: the update/delete tests
initially asserted status in (200, 403, 404), but PostgREST correctly
returns 204 for a write that matched zero rows -- exactly what RLS
hiding another user's row produces. That's a good sign (RLS worked),
just an assertion that didn't yet account for it; fixed and re-verified.

A second real bug found via this same live pass, unrelated to RLS:
`SupabaseProfileStore._rest()` crashed with a duplicate-keyword
`TypeError` whenever a caller also passed `headers=` (every insert/
upsert in `save()` does, to set `Prefer`) -- it was hardcoding
`headers=self._headers()` instead of merging. Fixed to merge caller-
supplied headers on top of the base auth headers. Caught by actually
running `SupabaseProfileStore`'s full save/load/delete cycle live
(not just the raw-PostgREST isolation tests, which don't exercise this
class at all) -- full round trip now confirmed working: create a
rifle+load, save, reload in a fresh instance, active rifle/load
resolved correctly, delete, confirm empty state.

### 9.4 What's next

1. ~~Re-run `tests/test_tenant_isolation.py`~~ -- done, 7/7 passing for
   real. ~~Verify `SupabaseProfileStore` itself~~ -- done, full round
   trip confirmed live.
2. ~~Wire authentication into api.py as new, parallel, auth-gated
   endpoints~~ -- **done.** `/v2/rifles` (list, create), `/v2/rifles/
   {name}` (get, delete), each gated by a `_get_user_store` dependency
   that verifies the bearer token and returns a request-scoped
   `SupabaseProfileStore`. Deliberately NOT replacing the existing
   single-tenant endpoints Rick's live app depends on today -- built
   and proven in isolation, zero risk to what's currently working in
   production. Verified live, both manually (curl against a real local
   server, real tokens) and as 4 new permanent tests in
   `test_tenant_isolation.py` (11 total now) -- confirmed isolation
   holds not just at the raw-PostgREST/RLS level but through
   Ballistica's own API logic end to end: User B gets an empty list, a
   404 by exact name (not even needing to guess an ID), and a rejected
   delete, while User A's rifle is proven to survive every attempt.
   Also covers the app-layer fail-closed baseline: no auth header, and
   a well-formed-but-forged token, both rejected before reaching any
   data. Cutting production over to this path is still a real
   deployment decision for Rick to make explicitly once he's ready --
   not something to fold into this autonomous pass.
3. ~~Voice conversation state (setup/calibration) moving into
   `conversation_state`~~ -- **done**, per §6.1/§7.3's DB-persisted
   decision. `_SetupSession`/`_CalibrationSession` (cli.py) gained
   `to_dict()`/`from_dict()`; `last_activity` switched from
   `time.monotonic()` to `time.time()` (wall-clock), since monotonic
   time's epoch is arbitrary per-process and meaningless once state has
   to survive across separate requests/processes rather than just
   within one long-lived object. `SupabaseProfileStore.get_conversation_
   state()`/`set_conversation_state()` made public (they already had
   the right read-modify-write semantics from the active_rifle_id work).
   New `POST /v2/voice/query`: a fresh `BallisticaCLI` every request
   (never a per-user cached object, so one user's Python objects can
   never leak into another's), hydrated from that user's persisted
   state before `handle()` runs, dehydrated back after.

   Verified live, not just written: two genuinely separate HTTP
   requests (no shared Python object between them) -- the second
   correctly recognized and continued the setup interview the first
   one started, including a real LLM-extracted field (the rifle name),
   proving the full draft survives the round trip, not just the
   session's bare existence. Also verified the isolation half: User A
   mid-interview, User B's unrelated command processed as a fresh
   command for B, never routed into A's session. Both now permanent
   tests in `test_tenant_isolation.py` (13 total).

   One real bug found and fixed via this same live testing, unrelated
   to conversation state itself: `supabase_auth.verify_token()` fell
   through to the legacy HS256 secret on ANY JWKS failure, including a
   transient one -- producing a misleading "alg not allowed" error for
   what was actually an intermittent JWKS lookup issue on a genuine
   ES256 token (confirmed this project never uses the legacy scheme).
   Fixed to retry with a fully independent PyJWKClient (no reliance on
   any cached state) before ever falling back. Confirmed fixed with 3
   consecutive clean full-suite runs (39 real token verifications, zero
   failures) after intermittent ~1-in-13 failures beforehand.
4. A migration path for Rick's own existing single-user data stays
   deferred -- that needs Rick to actually have a real account first,
   which is exactly the "real-account decision" category to loop him
   in on rather than create unilaterally.

## 10. Cutting the live app over to the `/v2` path

Per Rick's explicit 9-point cutover instruction (2026-08-28): everything
above was built, tested, and verified live sitting *alongside* the
original single-tenant app with zero impact -- a parallel path nobody
was actually using yet. This section is that path becoming the one the
real voice and web UI use, while the old single-tenant endpoints and the
shared global `store`/`voice_cli` objects in `api.py` stay fully intact,
untouched, and dormant, exactly as instructed -- a cutover of which path
is *active*, not a deletion of the old one.

**Endpoint surface completed.** The prior pass only covered what its own
tests exercised (`/v2/rifles` list/create, `/v2/rifles/{name}` get/
delete, `/v2/voice/query`); the actual web UI also needs update-rifle,
add-load, status, and drop-at-range. Four endpoints added, each modeled
directly on its single-tenant counterpart but parameterized on the
per-request `user_store` instead of the global `store`: `PUT /v2/rifles/
{rifle_name}`, `POST /v2/rifles/{rifle_name}/loads`, `GET /v2/status`,
`POST /v2/calc/drop-at-range`. (`/calc/angle`, `/calc/drop-table`, `/calc/
mpbr-zero` are not called by `index.html` at all -- only by other
tooling -- so no `/v2` versions were needed for this cutover.)

**Login/signup added to the web UI.** `index.html` gained an `#authPanel`
(email/password sign-in and create-account) that gates the entire
existing app, now wrapped in `#appContent`. Session handling is direct
`fetch()` calls to Supabase's own `/auth/v1/signup` and `/auth/v1/token`
REST endpoints (matching the codebase's existing minimalist style --
no new SDK dependency), with the resulting `{access_token, refresh_token,
expires_at}` persisted in `localStorage` and proactively refreshed
(a minute of headroom) before it's used, rather than waiting for a
request to fail on an expired token first -- a voice session at the
range can run long, and Supabase tokens last about an hour.

The public `SUPABASE_URL`/`SUPABASE_ANON_KEY` needed client-side to talk
to Supabase Auth directly are injected server-side into `index.html` at
request time (`web_ui()` in `api.py` now reads the file and does a
placeholder string replacement) rather than hardcoded into the committed
file -- the anon key is meant to be public/client-side by Supabase's own
design (RLS is the real security boundary, not secrecy of this key), but
it still has to come from env vars, not committed source.

**Data call sites switched in one place.** Every existing UI data call
already went through one shared `api()` helper in `index.html` -- so the
cutover for those was a single change: `api()` now prepends `/v2`,
resolves a valid (refreshing if needed) access token first, and attaches
it as `Authorization: Bearer`. A 401 clears the stored session and drops
back to the login screen rather than silently retrying. All 8 existing
call sites (`/rifles` list/create/get/update/delete, `/rifles/*/loads`,
`/status`, `/calc/drop-at-range`) map exactly onto the 9 registered `/v2`
routes with no per-call-site changes needed. The one call site that
bypasses `api()` -- the voice conversation turn's `/voice/query` fetch --
was updated directly to `/v2/voice/query` with the same auth attached.
`/voice/speak` and `/voice/transcribe` were deliberately left unchanged:
stateless OpenAI STT/TTS proxy calls with no per-user data involved, not
part of "the old single-tenant data layer" this cutover is about.

**Verified live locally**, signed in as the existing throwaway
`dt-auth-smoketest@mailinator.com` test account, end to end through the
actual browser UI (not just direct API calls): sign in, session survives
a page reload, create a rifle, add a load, get a ballistic solution,
sign out drops back to the login screen and clears the stored session,
signing back in restores the same data. Every one of those UI actions
was confirmed hitting `/v2/*` (not the old paths) via live network-
request inspection. `/v2/voice/query` was confirmed separately with a
direct authenticated request using the session's own token, correctly
resolving the just-created rifle/load and returning a real ballistic
solution. Test rifle cleaned up afterward.

**Old single-tenant path confirmed still intact**: `python -c` route
dump after all changes shows every original endpoint (`/rifles`,
`/voice/query`, `/status`, `/calc/*`) still registered, unmodified,
alongside the `/v2/*` set -- nothing removed, nothing rewritten.

## 11. Post-review security hardening (2026-08-28)

Two independent external reviews (Grok, ChatGPT) ran against the app
after §10's cutover and the ballistic-data-seed backlog work. Four items
came back, all actioned in this pass:

**Removed the dormant single-tenant path.** §10 deliberately kept it
alive as a rollback safety net during the cutover; both reviews flagged
that leaving a second, unauthenticated data surface live in production is
exposed risk once the new path is confirmed stable, not an actual safety
net anymore. Recommended removal (option a) over further isolation
(option b) -- no concrete reason surfaced to keep it, and the cutover was
already independently verified live in production. Removed from
`api.py` entirely: the shared `store`/`voice_cli` globals, every
unauthenticated endpoint (`/rifles`, `/status`, `/voice/query`, and
`/calc/drop-table`/`/calc/mpbr-zero`/`/calc/angle`, which had no `/v2`
equivalent and weren't called by the web UI anyway -- their capability
stays reachable through `/v2/voice/query`'s natural-language routing),
and the now-dead Pydantic schemas/helpers only those endpoints used. The
standalone single-tenant CLI (`python -m ballistica.cli`) is untouched --
this only removed the HTTP surface. `/v2/*` is now the app's only
data-touching surface.

Four `test_engine.py` tests that exercised the old HTTP endpoints were
migrated rather than deleted, split by what they actually guard: three
that specifically pin API/Pydantic-layer behavior (suppressor fields
round-tripping through a PUT, GET/POST not wrongly requiring an active
load, `/status` returning null instead of 404 for a loadless rifle) moved
to `test_tenant_isolation.py` against the real `/v2` endpoints with a real
account (that's genuinely what they test); four that pin
BallisticaCLI's own conversational engine behavior (state persisting
across calls, "repeat", `awaiting_response`, natural range phrasing) were
rewritten to call `BallisticaCLI.handle()` directly, dropping the
HTTP/network dependency entirely -- matching the dominant pattern already
used by the rest of that file. A new `tests/conftest.py` holds the
shared real-account sign-in fixtures both files now use, deduplicated
from what used to be defined only in `test_tenant_isolation.py`.

**Closed a cross-reference ownership gap in RLS.** The existing adversarial
suite (§7.4) proved User B can't read/write User A's rifle or load
*directly* -- this pass tested the sneakier variant Rick specifically
asked about: can B reference one of A's real ids on a *related* record?
Confirmed live, empirically, against the real project: B could insert a
`loads` row with their own `user_id` (passing the old policy's check) but
`rifle_id` pointing at A's real rifle; and B could `PATCH` their own
rifle's `active_load_id` to point at A's real load. Root cause: the
`loads_all_own`/`rifles_all_own` RLS policies only ever checked
`user_id = auth.uid()` on the row being written, never that a related id
the row references also belongs to that same user. Ballistica's own `/v2`
endpoints never exposed a path to trigger either case -- no endpoint
accepts a raw rifle_id/load_id from the client, only names, resolved
against the caller's own already-scoped in-memory rifles -- so this was
reachable only by a client that skips the app and calls Supabase's REST
API directly with their own valid token. Real gap, not exploitable through
the actual product, per the two-layer-isolation principle in #7.2 (RLS
should hold even if application code has a bug, or in this case is
bypassed entirely). Fix: `db/003_close_cross_reference_ownership_gap.sql`
tightens both policies to also require the referenced row exist and be
owned by the same user (and, for `active_load_id`, that it actually
belongs to that specific rifle -- a data-integrity tightening that came
free with the same fix). Two new adversarial tests in
`test_tenant_isolation.py` reproduce both attacks and currently fail
(proving the gap); they'll pass once the migration is applied. **Needs
Rick to run it in Supabase's SQL Editor** -- same as 001/002, no
service-role/DDL access from an agent session.

**Added rate limiting.** `slowapi`, per-IP (see `_rate_limit_key` in
api.py for why IP comes from `X-Forwarded-For` rather than the raw
connection address -- Render proxies every request, so the raw address
would be Render's own proxy, not the real caller), in-memory (Render runs
this as a single instance, no Redis/shared store needed at this scale). A
blanket default of 100/minute applies to every route via
`SlowAPIMiddleware`; the three endpoints that proxy a paid, per-call
third-party API -- `/voice/speak` and `/voice/transcribe` (OpenAI), and
`/v2/voice/query` (can fall through to a real Claude call via `intent.py`)
-- get a tighter 20/minute of their own, since those are the actual
cost-abuse and runaway-client-loop exposure, not the cheap CRUD
endpoints. Confirmed live against a local server: 20 requests to a
20/minute-limited route succeeded, the next 5 came back 429. Numbers are
a starting point, not a measured ceiling -- flagged for Rick to sanity-
check, especially since a real multi-turn setup/calibration conversation
can legitimately fire several requests a minute on its own. The full test
suite shares one `app` object and (through Starlette's TestClient) one
synthetic IP across every test in a run, which would otherwise trip these
limits partway through for reasons unrelated to what any given test is
checking -- `tests/conftest.py` disables the limiter for the duration of
the test session (autouse fixture) rather than trying to tune limits
around test traffic; production behavior is unaffected.

**Aggregate-data anonymization: verified, not fixed -- flagged as a
decision point.** No aggregate-data pipeline exists in the app yet
(confirmed: nothing anywhere writes to or reads the `events` table from
§3), so there's no live data-handling risk today. But the *schema as
designed* -- `events.user_id` stays set, tied to the live account, until
the account is deleted (§6.2's closed decision: anonymize atomically on
deletion, not before) -- doesn't match what Rick described wanting in
this hardening instruction: no traceable link back to the user at all,
even internally, from the moment data enters the pool. These are two of
Rick's own decisions from two different points in time that don't agree
with each other. Per his explicit instruction for this exact situation,
not resolved unilaterally -- logged as a decision point in
`RISK_REGISTER.md`'s new "Aggregate Data Anonymization" entry instead.
Whichever way Rick decides has a real, different consequence for what
account deletion needs to cover: if the link stays live until deletion,
the already-designed atomic anonymize-on-delete is the right mechanism;
if it's anonymized at ingestion instead, there's nothing left to delete
for that data at all.

**Decided the same day** -- see §6.2's updated note and §12 below:
anonymize at ingestion. Not left open long.

## 12. Liability waiver acceptance + aggregate-anonymization decision (2026-08-28)

Same day as §11's hardening pass, two more items: an attorney-approved
liability waiver needed wiring into account creation, and the aggregate-
data anonymization question §11 flagged got Rick's decision.

**Waiver acceptance flow.** New [ballistica/waiver.py](ballistica/waiver.py)
holds the canonical waiver text (sourced verbatim from
`Ballistica_Liability_Waiver_DRAFT.docx`, attorney-approved contingent on
exactly this acceptance mechanism) as the single source of truth for both
display and hashing -- `GET /waiver` (public, no auth -- has to be
readable before an account exists) serves it as structured JSON;
`index.html` renders it directly rather than keeping a separate copy that
could drift. A new `#waiverPanel` screen sits between the existing auth
form and actual account creation: clicking "Create account" no longer
calls Supabase's signup API directly -- it validates email/password,
then shows this screen. The full text renders in a scrollable panel, a
checkbox (unchecked by default, the exact attorney-specified
acknowledgment text) gates a "Create my account" button that stays
disabled until checked, and only THEN does the real signup call fire,
carrying the accepted waiver's version/hash/timestamp along with it.

Acceptance is captured two ways, deliberately redundant:
1. **Immediately, in the Supabase signup call's own `data` field** --
   lands in `auth.users.raw_user_meta_data` atomically with account
   creation, regardless of whether email confirmation is required (no
   session exists yet in that case, so nothing authenticated could be
   written anyway). This is the capture that never depends on the user
   coming back.
2. **In a new append-only table**, `waiver_acceptances`
   (`db/004_waiver_acceptance.sql`) -- RLS defines INSERT and SELECT of
   a user's own row only, deliberately no UPDATE or DELETE policy at
   all, so once written a row can never be altered or removed through
   the API by anyone, including the user it belongs to. Written via a
   new `POST /v2/waiver/accept` the instant a session exists -- right
   after signup if Supabase returns one immediately, or on the first
   sign-in afterward if email confirmation was required (staged
   client-side in `localStorage` in the meantime, mirrored in once a
   real token is available, and left in place for retry if that mirror
   write fails for any reason rather than being silently dropped).

Verified live, end to end, against the real Supabase project: full
waiver text renders correctly (headings, paragraphs, and the bulleted
list under Section 3, matching the source .docx's structure exactly);
the accept button is genuinely disabled until the checkbox is checked
and does nothing if clicked while disabled; signup with email
confirmation required correctly staged the acceptance in localStorage
and returned the "check your email" flow; **the confirmation email's own
JWT was decoded directly and shown to already contain
`waiver_accepted: true`, the exact version, sha256, and timestamp** --
proving the metadata capture happens atomically at signup, independent
of confirmation timing, not just claimed to. The `waiver_acceptances`
table mirror-write was confirmed to fail gracefully (the table doesn't
exist in production yet, pending migration) without breaking sign-in --
the pending localStorage entry correctly stayed in place for retry
rather than being dropped.

**Aggregate anonymization: decided.** §11 flagged a real conflict
between Rick's original §6.2 decision (anonymize `events.user_id` at
account deletion) and what he described wanting when this was revisited
(no traceable link at all, from ingestion). Rick's follow-up: anonymize
at ingestion. `db/005_anonymize_events_at_ingestion.sql` drops and
recreates `events` without a `user_id` column at all -- not nullable-
then-nulled-later, structurally absent. Safe as a clean drop/recreate
rather than a careful `ALTER`: confirmed (again) that no application
code anywhere reads or writes this table yet, and Rick confirmed the
live table is empty -- no migration or backfill risk. §6.2 above is
updated in place to reflect this as the current mechanism, with the
original 2026-08-23 reasoning left visible rather than erased.

**Three migrations now pending, none applied yet** (Rick needs to run
each in the Supabase SQL Editor -- no service-role/DDL access from an
agent session, same constraint as every prior migration):
`db/003_close_cross_reference_ownership_gap.sql` (§11),
`db/004_waiver_acceptance.sql`, and
`db/005_anonymize_events_at_ingestion.sql` (both this section).

## 13. In-app audio walkthrough (2026-08-28)

Four narrated sections (Getting Started; Rifle and Equipment Setup;
Checking a Load and Velocity; Long Range Shooting and Spotting), sourced
verbatim from `Ballistica_Audio_Walkthrough_Script.docx` into
[ballistica/walkthrough.py](ballistica/walkthrough.py) (same
canonical-source-of-truth pattern as `waiver.py`), narrated in the
Shimmer voice via `scripts/generate_walkthrough_audio.py` -- **pre-
generated once, not live TTS per playback**: this content is identical
for every user and never changes on its own, so regenerating it on
every play would just be a paid API call for the same output every
time, unlike an actual ballistic solution. The four MP3s live in
`ballistica/web/audio/`, served statically via a new `/audio` mount,
and are committed to the repo the same way `ballistica/web/icons/`
already is (no git-lfs in this project).

**First-login auto-play**: `GET /v2/walkthrough-status` /
`POST /v2/walkthrough/mark-first-played` track a new
`profiles.first_walkthrough_played_at` column
(`db/006_walkthrough_progress.sql`) -- nullable, no row required to
exist ahead of time (the status endpoint upserts one on first read,
touching only `user_id` so an existing timestamp is never clobbered).
The mark-first-played PATCH only matches a row where the column is
still null, which is what makes it safe against a double-fire (a fast
refresh, a retry) without needing a distributed lock -- whichever
request's write lands first wins, the second matches zero rows and
no-ops. The frontend marks an account as having heard it the instant
auto-play is triggered, not after playback finishes, so a closed tab or
a browser-blocked autoplay can't leave the account eligible to
auto-play again on the next login.

**Menu access**: a permanent "Help / Walkthrough" `<details>` panel
(matching the existing rifle-picker's collapsible pattern) lists all
four sections by title, each with a native `<audio controls>` element
(`preload="none"` -- nothing fetched until actually played) -- standard
browser play/pause/seek controls, no custom playback UI, no
conversational/listening behavior at all, matching the one-way
recorded-narration requirement exactly.

Verified live against a real account: full menu renders with the
correct four titles and `/audio/*.mp3` paths; a fetched file matches
its generated byte size exactly and actually plays (confirmed
`currentTime` advancing during real playback, not just that the
`<audio>` tag exists); the walkthrough-status/mark-first-played calls
were confirmed to fail gracefully (500, since `db/006` isn't applied
yet) without breaking sign-in or the rest of the app -- same pattern as
every other pending-migration endpoint added this session.

## 14. GPS/weather auto-fill (2026-08-28)

Closes the backlog item from §13's own day -- Rick asked to pull
conditions "from a local tower" rather than typing them in by hand.
[ballistica/weather.py](ballistica/weather.py) queries the public
Aviation Weather Center METAR API (aviationweather.gov -- free, no key,
confirmed live) for the nearest reporting station within a bounding box
around the caller's GPS coordinates, skips any station reporting wind-
only data with no temperature/pressure, and converts units into what
the app's atmosphere model already expects (°C→°F, hPa→inHg, m→ft,
knots→mph, dewpoint→RH via the standard Magnus-Tetens approximation).
`GET /v2/conditions/from-location` exposes it, rate-limited alongside
the other third-party-proxying endpoints (protects aviationweather.gov
from abuse via this server, not a paid-API-cost concern -- their API is
free). A new "Use my location" button on the web Conditions panel calls
the browser's own Geolocation API, then this endpoint, then fills
Temp/Humidity/Wind speed/Altitude/Pressure -- leaving every field still
editable, with a status line naming the station, its distance, and how
old the reading is, so it's a starting point to judge, not a black box.

**Deliberately does not fill in wind direction (the Clock field).**
METAR reports wind direction as an absolute compass bearing; Ballistica's
Clock field is relative to whichever way the shooter is actually facing
downrange. GPS coordinates say where you are, not which way you're
aimed, and there's no reliable way to derive one from the other here --
guessing would produce a value that looks auto-filled and trustworthy
but could easily be wrong, which is a real problem for a field that
directly drives a live-fire correction, not just a UX gap. Left for
manual entry, exactly as before.

Verified live end-to-end: the raw weather module against real
coordinates (Phoenix, AZ -- correct station, correct unit conversions,
sensible values), the authenticated endpoint through the real app, and
the full browser click-through (geolocation stubbed to simulate a real
GPS fix, since no real hardware is available here) -- confirmed every
field pre-fills correctly and the Clock field is left untouched at its
prior value.

## 15. Self-service account deletion (2026-08-29)

Closes a gap flagged back in §6.2 and carried in RISK_REGISTER.md ever
since: the schema was always designed for account deletion (every per-
user table CASCADEs on `auth.users` delete), but nothing let a user
actually trigger one. Confirmed live need: Rick had no way to reset his
own test account and retry the signup flow -- a real user would have
the identical problem.

**This is the first time this codebase uses the Supabase service_role
key**, and deliberately the only place it's used. Every other endpoint
in `api.py` runs on the caller's own access token -- RLS is the real
isolation boundary, per `supabase_store.py`'s whole design. Deleting the
actual login (the `auth.users` row) is an admin-only operation in
Supabase's API; there's no way to do it with a user's own token. The
new `DELETE /v2/account` (`api.py`) verifies the caller's own token
first, exactly like every other endpoint, and only ever acts on the
user_id that verification produced -- there is no code path that
accepts an id from the client, which is the one thing standing between
"delete your own account" and "delete anyone's account."

**Why this is safe to be this simple:** every per-user table's foreign
key to `auth.users` is already `ON DELETE CASCADE` (rifles, loads,
conversation_state, profiles, waiver_acceptances -- confirmed against
the actual schema, not assumed). A single admin-level delete of the
auth user atomically removes all of it in one Postgres transaction,
exactly matching this section's own original design intent -- no
manual per-table cleanup needed or attempted. `events` has no user_id
column at all anymore (§14/db/005) -- nothing there to touch either way.

**Depends on `SUPABASE_SERVICE_ROLE_KEY` being set** -- not configured
yet as of this writing, in either `.env` or Render. The endpoint fails
closed with a clear 503 ("Account deletion isn't configured yet on this
server") rather than crashing or silently doing nothing if it's
missing -- confirmed live, the account was untouched when tested
without the key configured. Get the key from the Supabase dashboard
(Settings → API → service_role secret, NOT the anon key) and add it to
both places before this can actually delete anything.

Frontend: a new "Account" panel (matching the Help/Walkthrough
collapsible pattern) with a single "Delete my account" button, styled
as a clear danger action, gated by one strongly-worded native
`confirm()` -- consistent with how rifle deletion already confirms
elsewhere in the app, not a heavier multi-step flow. On success, clears
the session AND the "known account" memory from the signup-UX fix
earlier the same day (§ above) -- leaving that in place would
incorrectly default a deleted account's browser straight to Sign In.

---

## 16. Sign in / Create account: symmetric choice instead of a guessed default (2026-08-29)

Supersedes the `KNOWN_ACCOUNT_KEY`-based default from earlier the same
day (§15's "known account" memory). That fix used per-browser
`localStorage` to guess whether a returning visitor should see Sign In
or Create Account by default. Rick's own follow-up testing showed it
wasn't enough: "real customers won't know to click a small secondary
link... this needs a proper fix so sign in versus create account
behaves correctly and predictably for anyone, on any browser" --
`localStorage` can't help a new browser, a new device, or incognito,
which is exactly the population most likely to hit this confusion.

**The tempting wrong fix, considered and rejected:** an unauthenticated
endpoint that checks whether an email is already registered, so the
frontend could pick the right screen with certainty. Rick's explicit
call: no. Ballistica accounts carry firearm, ammunition, load, and
shooting data, which makes "does this email have an account" a more
sensitive fact than it would be for a generic SaaS product -- a
dedicated check-endpoint is an enumeration oracle regardless of rate
limiting or other hardening layered on top, and he judged the UX
benefit didn't justify that privacy tradeoff even with those
protections in place. Standing instruction: **no unauthenticated
email-existence endpoint, ever; preserve Supabase's own anti-
enumeration behavior; solve this in the UI instead.**

**What shipped instead:** stopped trying to guess. "Sign in" and
"Create account" are now both always-visible, equally-weighted
buttons (`ballistica/web/index.html`) -- no default mode, no hidden
secondary link, no dependency on browser memory at all (the
`localStorage` email prefill from §15 is kept purely as a convenience;
it no longer picks which button shows). Every visitor makes their own
explicit choice, which sidesteps the ambiguity instead of resolving it
computationally -- the same idea as showing both options on a login
screen rather than the app deciding which one someone probably wants.

Each path's response also had to be made genuinely non-revealing on
its own terms, since Supabase's `/token` (sign in) and `/signup`
endpoints don't (and per the instruction above, must not be made to)
distinguish "wrong password" from "no such account," or "new signup"
from "already registered":

- **Sign in failure** (any cause other than an unconfirmed email or a
  network error) now surfaces one fixed message, "Email or password is
  incorrect.", instead of passing Supabase's raw error text through
  unmodified. Verified live: attempting sign-in against a genuinely
  nonexistent address produces this exact message, not a distinct
  "account not found" variant.
- **Create account**, once Supabase's own response comes back
  (identical whether the email was new or already registered), now
  shows one message written to be true and actionable either way:
  "Check your email for a verification link to finish creating your
  account. Already have an account with this email? Sign in instead."
  Verified live end-to-end with a genuinely new throwaway address
  (`dt-signup-uxtest-<timestamp>@mailinator.com`, not a real account or
  either hardcoded test fixture) through the full waiver -> signup
  flow.

"Email not confirmed" is deliberately left as its own distinct message
rather than folded into the generic sign-in failure -- it's not a
credentials guess, and someone seeing it already knows they have an
account here (they just typed the right password), so it isn't a new
disclosure to a stranger the way distinguishing "wrong password" from
"no account" would be.

**Owning lens:** Rick made the privacy-vs-UX call explicitly and
rejected the endpoint; Build implemented the UI-only fix and verified
both the sign-in and create-account response paths live.

---

## 17. Account menu consolidated behind a three-dot disclosure (2026-08-29)

Follow-up the same day: "Delete my account" (§15) was sitting in a
top-level, always-expanded-to-one-tap `Account` section on the main
app screen, alongside `Help / Walkthrough` -- both equally reachable
from the main content flow. Rick's instruction: move delete out of the
main panel entirely, into a three-dot/hamburger menu grouped with
other account actions (password change, the audio walkthrough), so it
isn't immediately visible or accidentally tappable.

**Structure** (`ballistica/web/index.html`): the header row's email
label now sits next to a `&#8942;` (three-dot) icon button -- a native
`<details id="accountMenu">` styled as a small icon rather than a
labeled section, opening a floating panel (absolutely positioned,
matching the app's existing disclosure-widget convention rather than a
bespoke dropdown component). Inside: `Sign out` and `Change password`
as flat buttons, then `Help / Walkthrough` as its own nested
disclosure (moved in unchanged, same ids, same JS), then a separate
nested `Danger zone` disclosure containing `Delete my account`. Reaching
delete now takes opening the account menu, then opening Danger zone,
then the button itself, then the existing `confirm()` -- four
deliberate steps where there were two, matching "not accidentally
tappable" concretely rather than just moving the same one-tap button
to a new location.

**Change password** is new, not just relocated -- closes a real gap:
the create-account UX message added in §16 already told people to "try
... resetting your password," but no such action existed anywhere in
the app. Implemented as a single button (no form -- the email is
already known from the signed-in session) that calls Supabase's own
`/auth/v1/recover` endpoint. This carries no enumeration exposure
regardless: it's never called with a typed-in address, only the
current session's own verified email, so there's nothing for the §16
anti-enumeration constraint to even apply to here. Depends on the same
pending Supabase Site URL fix noted in §-earlier-this-doc (the
confirmation-link redirect misconfiguration) -- the reset email's link
will point at `localhost:3000` until that dashboard setting is
corrected; not a new issue, the same one still outstanding.

Verified live locally (dev server): opening the menu shows Sign out /
Change password / Help / Walkthrough / Danger zone with no delete
button visible; opening Danger zone reveals it. Full test suite still
green (all existing ids for walkthrough and delete-account preserved
unchanged, so their JS needed no changes).

**Owning lens:** Rick specified the restructuring and flagged the
accidental-tap risk; Build implemented and verified locally.

**Update, same day:** the branding thumbnail Rick asked for in the
same instruction -- initially deferred, no such image existed in the
repo or the conversation -- is done. Rick provided the actual asset
from his own Desktop (`Mr and Mrs. Ballistica photo.jpg`, an AI-
generated marketing image, not a photo of real people); copied into
`ballistica/web/images/mr-and-mrs-ballistica.jpg` and served via a new
static mount (`/images`, `api.py`) matching the existing `/icons` and
`/audio` pattern -- unauthenticated, non-personalized static content.
Rendered as a small (56px) circular badge, `position:fixed` in the
bottom-right corner, placed as a direct child of `<body>` (outside
every panel div) so it stays visible across every screen -- auth,
waiver, and signed-in app content alike -- and through scrolling,
satisfying "visible at all times" literally rather than per-screen.
Verified locally across both the signed-out and signed-in views; no
collision with the account menu (opposite corner).

---

## 18. Walkthrough script updated to mention GPS weather auto-fill (2026-08-29)

Rick caught a real gap after listening to the deployed walkthrough
again: Section 4 (Long Range Shooting and Spotting) tells the listener
they can update wind/temperature/altitude/humidity by voice, but never
mentions "Use my location" -- the GPS-to-nearest-METAR-station auto-
fill built in §14 -- even though that feature has been live in the app
this whole time. The script (`Ballistica_Audio_Walkthrough_Script.docx`)
simply predated that feature; nothing was broken, the narration was
just incomplete.

**Fix:** added two sentences to Section 4's conditions paragraph in
`ballistica/walkthrough.py` -- the single source of truth for this
narration -- describing "Use my location" and the same wind-direction
caveat already documented in COMMAND_GUIDE.md ("GPS has no way of
knowing which direction you're actually facing"). Per §13's own design
principle (narration must match `walkthrough.py` exactly, since that's
also literally what gets sent to TTS), text and audio have to move
together -- regenerated all four MP3s via
`python -m scripts.generate_walkthrough_audio` (same tts-1/shimmer/0.9
settings as every other narration in the app) rather than hand-patching
just the one file, and replaced all four in
`ballistica/web/audio/`.

Only Section 4's content actually changed; Sections 1-3 were
regenerated incidentally as a side effect of the script re-running
against all four sections, not because their text changed.

**Owning lens:** Rick caught the gap by listening to the real, deployed
narration against the real, deployed feature set -- exactly the kind of
check a script review alone wouldn't catch; Build fixed the source text
and regenerated the audio.

---

## 19. Offline fallback mode -- core drop/windage calc (2026-08-29)

Rick's ask: rifle/load profiles cache locally once set up with a
connection, so at the range with no cell service, tapping a saved
rifle and typing a distance still gives an instant drop/windage
solution -- no voice, no internet required. Assessed before building
(see the technical-assessment message earlier the same day): scoped to
the core calculation for v1; chronograph calibration (zero.py) and
incline-angle solving (angle.py) deferred -- both are setup-time tools
used at home with a connection, not needed for "offline at the range."

**Three genuinely separate pieces**, easy to conflate into one:

1. **The engine port** (`ballistica/web/engine.js`) -- a line-for-line
   JS port of `trajectory.py`, `drag_tables.py`, `atmosphere.py`,
   `units.py`, and `reporting.py`. Feasible specifically because that
   code is pure stdlib Python (`math`, `bisect`, dataclasses only, no
   NumPy) -- a mechanical translation, not a redesign. Verified against
   the real Python engine, not assumed correct: `scripts/
   generate_engine_parity_cases.py` runs the actual Python engine
   across 11 realistic rifle/load/distance/condition combinations
   (short/long range, multiple calibers, both drag models, extreme
   atmospheres, a strong crosswind, and the `pressure_inhg=null`
   altitude-estimate fallback specifically) and writes the results to
   `scripts/engine_parity_cases.json`; `scripts/check_engine_parity.js`
   (`node scripts/check_engine_parity.js`) runs the same inputs through
   `engine.js` and asserts every field matches to 1e-6. All 11
   cases/132 fields pass. Re-run both whenever either engine changes --
   they must never be allowed to silently drift apart, since the whole
   point is that an offline solution shows the same number a live one
   would.

2. **The local cache** (`ballistica/web/index.html`, IndexedDB, store
   `ballistica-offline`) -- every saved rifle's full detail (specs,
   click value, every load's BC/velocity/zero) is exactly the JSON
   `RifleDetail` shape the server already returns, so caching it is a
   straight mirror, no transformation. Written opportunistically
   whenever the live rifle list loads successfully (`cacheAllRifleDetails`
   in `loadRifles()`, fire-and-forget); read back only when a live
   fetch actually fails (`tryLoadFromOfflineCache`).

3. **The service worker** (`ballistica/web/sw.js`, registered from
   `/sw.js`, served via a dedicated `api.py` route so its scope covers
   the whole origin) -- without this, the page itself can't open at
   all with zero connectivity, regardless of how good the offline math
   is; easy to miss if "offline mode" is thought of as one feature
   rather than three. Deliberately narrow: it only ever caches and
   falls back for the static shell (`/`, `/manifest.json`, `/engine.js`,
   icons, the branding image) via network-first-falling-back-to-cache,
   and never touches API traffic (`/v2/*`, `/waiver`, Supabase calls) --
   those pass straight through untouched. This preserves the no-cache
   guarantee on `/` from earlier the same project (see the `web_ui()`
   comment in `api.py`) for the online case exactly as before; the
   cached fallback is only ever seen with genuinely zero connectivity,
   at which point nothing dynamic (signup, sign-in, waiver, sync) is
   reachable anyway regardless of which shell version is cached.

**A real bug this surfaced and fixed in passing:** `getValidAccessToken()`
previously treated a failed token refresh as a dead session regardless
of *why* it failed, clearing it and forcing a re-login -- fine when the
cause is a genuinely revoked/expired refresh token, actively broken
when the cause is simply no network (exactly the offline scenario this
whole feature is for). It now only clears the session on a real
rejection; a network error just means no live token is available right
now, leaving the stored session intact for when connectivity returns.
`api()` had the same conflation (any missing token forced the auth
screen) and got the same fix: only a genuinely absent session forces
sign-out; a session that exists but is currently unreachable fails
individual calls as network errors instead, letting callers (`loadRifles`,
`solveBtn`) fall back to cache/local-compute rather than getting bounced
out of the app entirely.

**UI:** a visible amber banner ("Offline -- using saved rifle data
cached [relative time]...") whenever running against cached data
instead of a live fetch, so cached-but-possibly-stale data is never
mistaken for a fresh solution -- an important distinction for a live-
fire tool. Voice mode is explicitly disabled while offline (it needs
live OpenAI STT/TTS calls and would otherwise fail into a confusing
half-listening state rather than a clean error); rifle/load editing is
left enabled and just fails naturally with a network-error message if
attempted, rather than gating every individual button.

**Verified live** (not just unit-tested): local dev server, a real
rifle cached via the actual UI flow, then the backend process stopped
entirely (not simulated -- genuinely not running) and the page
reloaded. The shell rendered fully from the service worker cache with
zero backend running; the cached rifle loaded from IndexedDB; clicking
GET SOLUTION for 500 yd on a 77gr .223 load produced 68.9 in / 3.83
mrad / 38.3 clicks / 1329 fps -- matching the real Python engine's own
output for identical inputs exactly.

**Owning lens:** Rick scoped the ask and approved the core-only v1
boundary after the technical assessment; Build implemented and
verified against the real engine, not a mock.

---

## 20. JWT verification hardening -- two real bugs found by new tests (2026-08-29)

Followed up on a cross-model (ChatGPT) security review of a summary of
this whole app: most of its "must resolve" list turned out to already
be handled (the old single-tenant endpoints are fully removed, not a
fallback; rate limiting is real and live) or forward-looking for
features that don't exist yet (aggregate-model provenance/poisoning
protection, with no aggregate pipeline built). One item was concretely
actionable and correct: JWT tests covered forged-signature and
missing-token, but not expired tokens, tampered signatures, or
malformed/missing claims. Rick asked for that coverage explicitly.

**New file: `tests/test_auth_hardening.py`** -- 15 adversarial cases
against `supabase_auth.py`'s real `verify_token()`, signed with the
project's actual legacy HS256 secret (`SUPABASE_JWT_SECRET`) rather
than a made-up one, specifically so these tests exercise claim
validation, not just "wrong secret gets rejected" (already covered by
`test_tenant_isolation.py`'s forged-token case). Covers: expired
tokens (including right at the leeway boundary), completely malformed/
empty/truncated tokens, a tampered signature, a tampered payload
(swapped-in different `sub` without re-signing), missing/null/empty
`sub`, missing/wrong `aud`, missing `exp`, the classic `alg: none`
attack, and an out-of-allowlist algorithm family.

**Two real bugs found and fixed, not just tested:**

1. **Missing `sub` claim caused an unhandled 500, not a 401.**
   `payload["sub"]` on a validly-signed token with no `sub` claim at
   all raised a bare `KeyError`, which `get_current_user_id`'s except
   clause (`jwt.InvalidTokenError, jwt.PyJWKClientError`) doesn't
   catch -- confirmed live via `TestClient(raise_server_exceptions=
   False)` before fixing, not assumed. Fixed via a new `_extract_sub()`
   helper that checks for a real value and raises a proper
   `jwt.InvalidTokenError` if absent, used by both the JWKS and legacy
   verification paths.
2. **Missing `exp` or `aud` claims were silently accepted.** PyJWT's
   `jwt.decode()` only *validates* `exp`/`aud` if they're present in
   the token -- it doesn't *require* them to be there at all unless
   told to. A token with no `exp` claim verified successfully and
   would never expire; confirmed live (the first version of the new
   test suite caught this on its first run, not by inspection). Fixed
   by adding `options={"require": ["exp", "aud", "sub"]}` to both
   `jwt.decode()` calls.

**Neither bug was reachable through the actual product** -- both
require a validly-signed JWT (the real Supabase secret or a real
JWKS-matched key) with a claim deliberately stripped out, which
Supabase's own token issuance never produces. Real exposure was
narrow: (1) the missing-`sub` case was a robustness/DoS-adjacent bug
(an unhandled 500 on a crafted request) more than a data-access one;
(2) the missing-`exp`/`aud` case only matters if the legacy HS256
secret is ever exposed by some other means, at which point a forged
token could additionally never expire -- a real defense-in-depth gap,
not a live exploitable one today. Fixed anyway because "not reachable
today" isn't the same as "correct," and this is exactly the kind of
latent gap that becomes reachable the moment some other assumption
changes.

**Verified:** all 15 new tests pass after the fix; full suite (85 tests
total now -- 70 existing + 15 new) re-run to confirm zero regression
against real, live Supabase-issued tokens -- legitimate tokens always
carry `exp`/`aud`/`sub`, so requiring them changes nothing for real
usage.

**Owning lens:** Rick directed the cross-model review and the specific
follow-up ask; Build found, fixed, and verified both bugs against real
code execution, not inspection alone.

---

## 21. Second cross-model review -- three more fixes (2026-08-29)

A second independent external review (of the same app summary) raised
several points; most either duplicated the first review's already-
resolved items or were forward-looking for unbuilt features (aggregate-
model consent, threat-model doc, audit logging infra) -- noted for
Rick to prioritize, not acted on unilaterally. Three were concrete,
verified-real, and cheap enough to fix immediately:

1. **`iss` (issuer) claim was never checked.** Both this review and the
   first one flagged it. Confirmed the real value live against an
   actual issued token (`{SUPABASE_URL}/auth/v1`) rather than
   guessing, then pinned it in `supabase_auth.py` (`_EXPECTED_ISSUER`,
   added to `_REQUIRED_CLAIMS`) and added two new tests
   (`test_missing_iss_claim_rejected`, `test_wrong_iss_claim_rejected`)
   to `test_auth_hardening.py`. Not exploitable today -- the JWKS keys
   fetched for verification are already scoped to this one project, so
   a token from anywhere else can't pass signature verification
   regardless -- but a token should say who issued it, and nothing
   checked that until now.

2. **`/waiver` had no cache-control headers** -- the same staleness
   class already fixed for `/` (§ earlier this doc): a browser could
   serve a stale cached waiver after Rick revises the text, letting
   someone accept an outdated version without ever seeing the current
   one. Fixed with the identical `no-store, no-cache, must-revalidate`
   headers `/` already uses. Verified live: `fetch('/waiver')` now
   returns those headers.

3. **The offline rifle cache (§19) was never cleared on sign-out or
   account deletion** -- the sharpest, most concrete finding in this
   review. On a shared/borrowed device, if a second person signs into
   their own real account but their own `/v2/rifles` fetch happens to
   fail right after (network blip), `loadRifles()`'s existing fallback
   would show them whatever the *first* person's rifles were, still
   sitting in IndexedDB under their own logged-in identity -- a real
   cross-user data exposure on shared hardware, not a device-theft
   hypothetical. Fixed with a new `clearOfflineCache()`, called from
   both `signOutBtn` and the account-deletion handler, mirroring how
   `KNOWN_ACCOUNT_KEY` is already cleared at both of those same points.
   Verified live: seeded the cache, fired sign-out, confirmed the
   cache is empty afterward.

**Not acted on, flagged for Rick to prioritize:** per-user (not just
per-IP) rate limiting on voice endpoints; a `solver_version` +
input-hash field surfaced on every solution for traceability;
shipping the in-session spoken liability reminder sooner (raised by
both reviews now, plus it's already in RISK_REGISTER.md as a deferred
next step); explicit consent/disclosure UX for any future aggregate-
data contribution (also raised by the first review); IndexedDB cache-
version bump discipline if the `RifleDetail` shape ever changes
non-additively; voice audio/transcript retention disclosure; structured
audit logging for RLS-critical paths; a written threat model. None of
these are code bugs -- they're either product/consent decisions needing
Rick's own call, or larger infrastructure investments worth sequencing
deliberately rather than building reactively off a review.

**Verified:** all 17 `test_auth_hardening.py` cases pass (15 + 2 new
issuer tests); full suite re-run for zero regression.

**Owning lens:** Rick ran the second review and shared it; Build
triaged, verified each concrete claim against real code before acting,
and fixed the three that were genuine and in-scope.

---

## 22. Per-user rate limiting; the spoken safety reminder finally shipped (2026-08-29)

Two decisions Rick made explicitly, closing two of the items §21 had
flagged as "needs your call, not mine."

**Per-user rate limiting.** Previously every limit (`api.py`) was keyed
purely by IP (`X-Forwarded-For`), so people sharing a connection --
same office, same home network, same NAT -- competed for the same
bucket. `_rate_limit_key()` now tries per-user first: it independently
re-verifies the request's bearer token (via the real `verify_token()`,
a genuine signature check, not a cheap unverified decode) and keys on
the resulting user id if that succeeds, falling back to the existing
IP-based key (renamed `_ip_rate_limit_key()`) otherwise. The real-
verification requirement matters: an unverified `sub` claim would let
an attacker manufacture a fresh, never-throttled bucket on every
request just by changing a claim in a token with no valid signature,
quietly defeating rate limiting for exactly the traffic it most needs
to catch. A token that fails verification falls straight through to
the same IP-based key as before -- no regression for the unauthenticated-
abuse case. Verified directly (not just by reading the code): two
different real tokens produce two different keys; an invalid token and
no token at all both correctly fall back to the IP key.

**The spoken safety reminder.** RISK_REGISTER.md's "Legal / Liability"
entry has carried this as an outstanding next step since the waiver
screen shipped (§5 of the app summary, §12 of two separate external
reviews) -- Rick gave the final exact wording and shipped it today
rather than leaving it deferred further: "My data is for reference
only, always verify before firing," spoken as part of Ballistica's own
greeting, not a separate popup or confirmation step. Landed in
`GREETING_PHRASES` (`index.html`), which `handleWakeWord()` already
spoke on the first wake-word of every voice session (`sessionWakeCount
=== 0`) -- the existing "warm hello" moment, not a new code path. All
three greeting variants keep the core safety clause close to verbatim
so the substance never varies even though the surrounding sentence
does, by design -- this is the one place variety shouldn't dilute the
message. Fires every session, not once ever, matching "always verify"
being an always-true fact rather than an onboarding-only notice.

**Noted, not fixed (out of scope for this ask):** all three greeting
variants (and `ACK_PHRASES`) still hardcode "Rick" by name, a leftover
from the single-tenant era -- now a real, visible issue for any other
real signed-in user, who'd hear Ballistica call them "Rick." No display-
name field exists on a profile today (only email), so this isn't a
one-line fix; flagged for Rick to prioritize separately, not silently
patched as a side effect of this change.

**Verified:** full suite green, zero regression, after both changes.

**Owning lens:** Rick made both calls explicitly and supplied the exact
safety-line wording; Build implemented and verified each independently.

---

## 23. Display name -- closes the hardcoded "Rick" gap (2026-08-30)

Direct follow-up to §22's noted-but-not-fixed item: `GREETING_PHRASES`
and `ACK_PHRASES` hardcoded "Rick" by name, wrong for any other real
signed-in account. No display-name field existed anywhere -- only
email. Built end to end:

- **`db/008_display_name.sql`** -- nullable `display_name` on the
  existing `profiles` table (same table `voice_id` and
  `first_walkthrough_played_at` already live on), bounded 1-40 chars
  when set via a check constraint. Nullable and optional by design --
  nothing at signup requires it, so this is purely additive for anyone
  who never sets one. **Needs Rick to run it in the Supabase SQL
  Editor**, same as every prior migration -- not yet applied as of
  this writing.
- **`GET /v2/profile` / `PATCH /v2/profile`** (`api.py`) -- same
  ensure-the-row-exists upsert pattern as the existing walkthrough-
  status endpoints, same caller-token-scoped REST calls (no service-
  role involved). Length validated server-side too (`ProfileUpdateIn`),
  not just by the DB constraint, so a bad value gets a clean 422 rather
  than a raw Postgres error.
- **Account menu** (`index.html`) -- a "Your name" field at the top of
  the three-dot menu (above Sign out, not nested in Danger zone --
  this is routine, not destructive), pre-filled from `GET /v2/profile`
  on load, saved via `PATCH /v2/profile`.
- **`GREETING_PHRASES`/`ACK_PHRASES` are now functions** (`greetingPhrases(name)`,
  `ackPhrases(name)`) instead of static arrays, called with
  `userDisplayName` at the actual wake-word handling site
  (`handleWakeWord()`). A name is used naturally when set ("Hey Sarah,
  Ballistica's up...", "I'm here, Sarah. Go ahead."); the phrasing
  degrades to name-less when it's not ("Hey there, Ballistica's
  up...", "I'm here. Go ahead.") rather than falling back to anyone
  else's name.

**Not fetched offline:** `loadDisplayName()` only runs when there's a
live token, same guard as the walkthrough autoplay call right next to
it. No gap in practice -- voice mode (and so the greeting itself)
already can't run offline at all (§19 disables `enableVoiceBtn` in
that mode), so there's nothing to cache a name for.

**Verified:** `greetingPhrases()`/`ackPhrases()` checked directly in
the browser console for both the named and unset cases; the account-
menu field renders correctly, positioned above Sign out. Full suite
re-run for zero regression. **End-to-end save/fetch against the real
Supabase project not yet verified** -- blocked on Rick running
db/008 first, same as any schema-dependent feature before its
migration lands.

**Owning lens:** Rick asked for this directly, as the fix for a gap
Build had flagged but not touched the day before; Build implemented
end to end and verified everything short of the still-pending
migration.

---

## 24. Bulk CSV/Excel import + export (2026-08-30)

The backlogged "Spreadsheet / CSV data import" item (raised 2026-08-23,
explicitly sequenced after multi-tenancy) -- unblocked now that
multi-tenancy exists, and built to the fuller spec Rick actually asked
for: a column-mapping step (not an exact-header-match requirement),
paired with an export in the same shape, surfaced both generally and
specifically before account deletion. Real test case: Rick's own 20in
Faxon .223 Wylde barrel data (3 charge-weight loads, chronograph
strings, real conditions -- see the correction he made to the 22.5gr
spread, 104 fps not 80, confirmed and used as the actual test input).

**Three pieces** (`ballistica/import_export.py`, new `/v2/import/preview`,
`/v2/import/commit`, `/v2/export/rifles` in `api.py`, an `#importPanel`
in `index.html`):

1. **Parsing** -- CSV (stdlib `csv`) and `.xlsx` (`openpyxl`, new
   dependency). 2MB / 500-row caps, checked both via `Content-Length`
   before the body is even read and again after parsing, since
   `Content-Length` isn't guaranteed present or accurate for every
   client.
2. **Column mapping** -- `TARGET_FIELDS` is the full rifle+load field
   set (27 fields, same order as export, so an export re-imports with
   zero manual correction). `suggest_mapping()` best-guesses which of
   the file's own headers goes with which target field via a two-pass
   exact-then-loose alias match; the user confirms/corrects on the
   mapping screen before anything is written. **A real bug found while
   testing, not by inspection:** a single pass-per-field let a short,
   generic alias on one field ("model" on `scope_model`) steal a
   header that was an exact match for a different field (`drag_model`'s
   own "drag model" alias, against a real "Drag Model" column) just
   because that field happened to be checked first in iteration order.
   Fixed by resolving every field's exact match first, across the
   whole set, before any field is allowed to fall back to a looser
   one -- closes the whole class of bug, not just this instance.
3. **Per-row validation, not all-or-nothing** -- every row is checked
   independently using the exact same `Rifle`/`Load` dataclass
   validation every other write path already enforces; a file with
   some invalid rows still imports every row that IS valid, reporting
   exactly why each failure failed. This is deliberate, not a
   shortcut: Rick's own real chronograph data has no ballistic
   coefficient (BC is a published-data lookup, not something a
   chronograph measures) -- importing it correctly produces three
   clean "BC is missing or not positive" failures, which is the
   CORRECT behavior, confirmed live against the real API, not a
   limitation to work around. Re-running the same import with a BC
   value added succeeds cleanly and persists for real (verified via a
   direct `GET /v2/rifles/{name}` afterward).

   A load name isn't required in the mapped file at all -- most real
   load-development logs have no such column (charge weight + powder
   IS the identifier a shooter thinks in). `apply_mapping()` synthesizes
   one ("22.5gr H335") from powder charge + powder, or bullet weight +
   type, falling back to "Load N" only if neither is present.

   Re-importing into an existing rifle only ever ADDS a load -- it
   never overwrites that rifle's own already-saved metadata (scope
   height, caliber, etc.) even if the row includes different values
   for those columns, so a partial re-import can't clobber real data
   filled in some other way since.

**Export** (`generate_export_csv`) -- one CSV, column order matching
`TARGET_FIELDS` exactly (the round-trip property above), UTF-8 BOM so
Excel opens it correctly rather than mis-encoding it. **CSV/formula-
injection protected**: every free-text field (rifle name, notes, ...)
a user fully controls gets escaped if it starts with a formula-trigger
character (`=+-@`, tab, CR) -- a leading apostrophe forces Excel to
treat it as text rather than evaluating it as a formula, since this
file is explicitly meant to be opened in a spreadsheet app and
potentially handed to someone else. Verified directly, not assumed:
a rifle named `=cmd|/c calc!A1` and a note starting `+SUM(...)` both
export with the escape applied, confirmed present in the actual output
bytes.

**Surfaced two ways with the same function**, per Rick's explicit ask:
generally, any time, in an "Import / Export" account-menu section; and
again specifically as "Download my data first," directly above the
delete button in Danger Zone.

**Reuses the existing rifle/load store**, not a separate write path --
`apply_mapping()` builds/mutates real `Rifle`/`Load` objects against
`user_store.rifles` (already loaded for the request) and a single
`user_store.save()` persists everything, the same method every other
rifle-writing endpoint already calls. New rifles have to be merged
back into `user_store.rifles` before `save()` (it does a full delete-
then-reinsert of whatever's in that dict, same as every existing write
path) -- mutating an *existing* rifle object in place is automatically
reflected there, but a brand-new one only exists in the import's own
local result set until explicitly merged in.

**Verified end to end against the real API** (not just the standalone
module): preview and commit both tested live via the `dt-auth-
smoketest` fixture account -- Rick's real (BC-less) data correctly
failed all three rows with the expected reason; the same data with a
BC value added succeeded, persisted (confirmed via a real `GET`), and
appeared correctly in an actual export download; the account-menu UI
and the full mapping-screen flow were exercised through real clicks,
not just API calls. Every test rifle created during verification was
deleted afterward -- the fixture account was confirmed back to zero
rifles before finishing. 23 new unit tests
(`tests/test_import_export.py`) cover parsing, the mapping-collision
fix, every validation failure path, and the export/re-import round
trip, with no network dependency.

**Not built:** XLSX export (CSV only, both directions of "same
format" -- import accepts it, export produces it; sufficient for
print/share/re-import, and a second export format didn't seem worth
the added surface for what this is actually for).

**Owning lens:** Rick specified the full scope (mapping UI, paired
export, deletion-flow placement, printable/shareable framing, and the
specific real-data test case); Build implemented end to end and
verified against the real API and a real dataset, not the module in
isolation.

---

## 25. Bundled bullet reference dataset (2026-08-30)

Closes the "Seed dataset" backlog item (raised 2026-08-23, licensing-
researched but never actually pulled or built until now). Rick's
instruction was specific: confirm the license explicitly permits
commercial use and name it, pull the real data and map it, flag fields
that don't map cleanly rather than guessing, spot-check 10-15 entries
across calibers against real published specs (including the 77gr
Sierra MatchKing specifically), run the suite, report exactly what was
included/skipped and why, and confirm this data can never override a
user's own real chronograph data.

**License, confirmed directly, not from a GitHub badge:** cloned the
actual `ammolytics/projectiles` repository and read `LICENSE` itself
-- standard, unmodified MIT, pinned to commit
`5b51ab231c66f60de6fcb62a6b4c4795240948e5` (2026-08-30). Explicitly
grants "use, copy, modify, merge, publish, distribute, sublicense,
and/or sell" -- covers commercial use without qualification. Only
obligation is retaining the copyright notice, satisfied by
`data/bullet_reference/ammolytics_projectiles_source/` keeping the
real `LICENSE` file alongside a `PROVENANCE.md` recording the pinned
commit and what was kept.

**Pipeline** (`scripts/build_bullet_reference.py` -> `data/
bullet_reference/bullet_reference.json` -> `ballistica/
bullet_reference.py`): reads the source's own `bc_g1`/`bc_g7` columns
only -- no guessing. A row is included only if it has weight,
diameter, and a real single-value G1 or G7 BC in those columns. When
both are present (148 rows, every single one a Berger boat-tail match/
target bullet), G7 is preferred, matching the same reasoning already
documented elsewhere in this codebase (drag_tables.py, walkthrough.py)
for exactly this bullet shape.

**What got flagged instead of guessed, and why -- the real finding of
this pass:** every one of Sierra's 198 rows (198/198, not a handful)
publishes its BC only as an inconsistently-formatted, velocity-banded
structure in a separate `bc_fn` field (e.g. `{".372": [3000, null],
".362": [3000, 2500], ".362": [2500, 1700], ...}` -- note the same key
repeated with different values, which isn't valid JSON, just JSON-
shaped text) instead of a clean single number in `bc_g1`/`bc_g7`. This
includes the exact bullet Rick's own real rifle uses, the 77gr
MatchKing. Parsing that field would mean guessing which velocity band
to trust and silently trusting a malformed structure -- exactly what
Rick's instruction said not to do. It was excluded and reported, not
patched using the real G1/G7 values Rick separately confirmed
(§ earlier this doc) -- mixing a value from a different, undocumented
source into "what the open dataset contains" would blur provenance
in exactly the way this whole exercise is trying to avoid. Worth
noting as a partial corroboration, not a fix: the top-band value
embedded in that malformed field, 0.372, matches the real G1 number
Rick got directly from Sierra -- the underlying source data is
accurate, it just isn't in a form this pipeline will silently trust.
7 other rows (a handful across Barnes/Berger/Lapua) were excluded for
missing weight or having no BC of any kind in the source at all -- see
`data/bullet_reference/build_report.md` for the complete list with
reasons, regenerated every time the build script runs.

**Result:** 822 of 1032 source rows included (Barnes 207/210, Berger
99/100, Hornady 301/301, Lapua 56/57, Sierra 0/198, Speer 159/166).

**Spot-checked against real, live data, not memory:** fetched Hornady's
and Berger's own current published ballistic-coefficient pages
directly and compared by exact SKU/part number --

| Company | Bullet | Part # | This dataset | Manufacturer's own page |
|---|---|---|---|---|
| Hornady | .308 168gr ELD Match | 30506 | 0.263 G7 | 0.263 G7 (top velocity band) |
| Hornady | 6.5mm 147gr ELD Match | 26333 | 0.351 G7 | 0.351 G7 (top velocity band) |
| Berger | 6.5mm 140gr Hybrid Target | 26414 | 0.311 G7 | 0.311 G7 |
| Berger | 6mm 105gr Hybrid Target | 24433 | 0.275 G7 | 0.275 G7 |

All four exact matches. (Interesting side note: Hornady's own page also
publishes multiple velocity-banded BCs per bullet, same underlying
pattern as Sierra's data -- but ammolytics captured Hornady's top-band
figure into a clean column while leaving Sierra's in the unparsed
`bc_fn` field. A dataset quirk in how the two manufacturers'
source pages happened to be scraped, not a Ballistica decision.)
Beyond the 4 live-fetched, sampled and reviewed roughly a dozen more
across Lapua/Speer/Barnes/Hornady spanning .224 through .451 caliber
and both rifle and pistol types, checked for internal consistency
(BC scaling sensibly with weight within a bullet family, G1 always
exceeding G7 for the same bullet, pistol bullets sitting in a
noticeably lower BC range than rifle match bullets) -- all consistent,
no anomalies found.

**Subordinate to a user's own real data -- guaranteed by having no
write path at all, not by a rule that could be bypassed:**
`ballistica/bullet_reference.py` is not imported anywhere else in the
codebase (confirmed by grep, not assumed) -- no endpoint, no CLI
command, nothing touches a user's actual `rifles`/`loads` from this
module. A user's own chronograph-calibrated velocity and self-entered
BC always win, because there is currently no code path by which this
data could touch theirs at all. A future "start from a factory bullet"
UI feature could offer these values as a pre-fill a user then saves
themselves (the same pattern book-data velocity already uses per
earlier sections of this doc) -- that's a real next step if wanted,
not built in this pass.

**Not built in this pass:** any UI for browsing/selecting from this
data when setting up a load (Rick's instruction was scoped to the data
pipeline itself -- license, pull, map, flag, spot-check, test, report
-- not a user-facing feature on top of it).

**Verified:** 8 new unit tests (`tests/test_bullet_reference.py`),
including a regression test pinning the Sierra-exclusion finding so it
can't silently start being guessed later. Full suite green,
zero regression.

**Owning lens:** Rick specified the exact verification bar (license
confirmed and named, real spot-checks against real specs, flag don't
guess, explicit subordination guarantee); Build pulled the real
source, built the pipeline, and verified every item against live data
or the real code, not inspection alone.

---

## 26. Waiver disclosure + mandatory aggregate contribution pipeline (2026-08-30)

Rick made three explicit, deliberate product/legal calls here,
overriding what an external review (and this doc's own earlier
framing) had recommended -- his calls, implemented as specified, not
re-litigated:

1. **No separate consent checkbox.** Data-use consent folds into the
   existing waiver acceptance itself.
2. **Existing users:** shown a plain on-screen notice on next login,
   not a blocking re-acceptance screen. Continuing to use the app after
   that notice constitutes acceptance.
3. **No opt-in population at all.** Every load a user saves or enters
   is automatically anonymized and merged into the aggregate pool, as
   a standard, non-optional part of how the app works -- not a
   per-load action, not a togglable preference.

Flagged once, not re-argued: continued-use consent is legally weaker
than the explicit-checkbox pattern the rest of this waiver flow uses,
and mandatory (non-optional) data collection raises its own disclosure
bar -- same "not a substitute for real legal review before real paying
customers" caveat already on record for every aggregate-data decision
in this project.

**Waiver text (`ballistica/waiver.py`, version bumped `2026-08-28-v1`
-> `2026-08-30-v2`):** new Section 4, "How Your Ballistic Data Is
Used," inserted before the renumbered "Your Independent Duty to
Verify" (sections 4-11 shifted to 5-12). States plainly: load data is
automatically anonymized and merged into the aggregate pool as a
standard, non-optional part of the app; anonymization happens at save
time and can never be traced back to the account, including after
deletion; rifle names, load names, and notes are excluded. Section 11
("How You Accept This Agreement") gained a second paragraph describing
the existing-user notice-and-continued-use path alongside the
unchanged new-account checkbox path. The acknowledgment text itself
(what the checkbox literally says) now names this consent explicitly,
not just the body text above it. Flagged directly in the module's own
docstring: unlike sections 1-11's original attorney-approved text,
this new section and acceptance-mechanism paragraph are Rick's own
operational instruction, not independently attorney-reviewed --
recorded so that distinction is never lost track of later.

**Existing-user notice** (`GET /v2/waiver/status`, `index.html`): a
new endpoint reads the caller's own most recent `waiver_acceptances`
row (RLS-scoped, no service role) and compares it to the live
`WAIVER_VERSION`. `init()` calls it once per session; a stale or
missing acceptance immediately records acceptance of the *current*
version (reusing the exact same `recordWaiverAcceptance()` the
signup flow already uses) and shows a plain, dismissible banner --
acceptance is recorded the moment the mismatch is detected, per
Rick's instruction that the notice itself is the mechanism, not a
further click. Verified live: the `dt-auth-smoketest` fixture account
(last accepted the old v1 text) showed the banner, `GET /v2/waiver/
status` immediately reflected `up_to_date: true` afterward, and a
second load produced no banner at all.

**Aggregate pipeline** (`ballistica/aggregate_pool.py`, migration
`db/009_add_load_event_type.sql`): `contribute_load()` fires from
every place a load gets saved -- the single-load endpoint
(`v2_add_load`), rifle creation with inline loads (`v2_create_rifle`),
and the bulk import commit path -- covering "saves or enters" exactly
as specified, not just one of the three save paths. Uses the caller's
own access token for the insert, not a service role -- the
`events_insert_any_authenticated` RLS policy only requires a real
session, and `events` has no `user_id` column at all (§ earlier this
doc), so the row itself carries no identity regardless of whose token
inserted it.

`build_load_event_payload()` is an explicit allow-list -- caliber,
barrel length, twist rate, bullet type/weight, BC, drag model, muzzle
velocity, zero distance, powder, charge -- deliberately not "every
field except a documented exclude-list," so a future field added to
`Rifle`/`Load` can't silently start leaking into the aggregate pool
just because nobody remembered to add it to an exclude-list. Rifle
name, load name, and notes (the highest-risk free-text field a user
fully controls) never appear. Verified directly, not assumed: a test
fixture with a rifle/load name and notes deliberately containing an
email address and a city confirms none of it survives into the built
payload.

Contribution is best-effort and non-fatal by design (`contribute_load`
catches and logs, never raises) -- confirmed live, not just by reading
the code: `db/009` hasn't been run yet, so a real save's contribution
attempt currently gets a 400 from Supabase's still-narrower check
constraint, logged clearly in the server log, while the rifle/load
save itself succeeded and returned normally. Once Rick runs `db/009`,
the exact same code path succeeds instead -- no code change needed,
only the pending migration, same pattern as every other schema-
dependent feature in this project.

**Verified:** 9 new tests (5 waiver-text assertions, 4 aggregate-
payload exclusion tests) plus the live checks above; full suite green
(127 passed), zero regression. `db/009` still needs Rick to run it in
the Supabase SQL Editor before contributions actually persist.

**Owning lens:** Rick made all three consent/pipeline-design calls
explicitly, after an initial round of clarifying questions; Build
implemented exactly as specified, flagged the legal-weight tradeoff
once, and verified every piece against live behavior -- including the
still-pending-migration failure mode, not just the success path.

---

## 27. Rename bug (rifles and loads) + single-load/rifle delete (2026-08-30)

Rick reported: "editing a rifle profile's name creates a duplicate
instead of updating in place." Confirmed by reading the actual code,
not assumed: `saveRifleBtn`'s handler decided create-vs-update purely
by comparing the typed name against `currentRifleDetail.name` --
`const isNew = !currentRifleDetail || currentRifleDetail.name !==
name;`. Editing the name field makes that comparison true by
construction, so editing a name looked identical to starting a
brand-new rifle: it silently POSTed a second rifle under the new name
(empty, no loads) while the original sat untouched under its old name
-- not a rename, a duplicate. **The same root cause turned out to
affect loads too** (found while fixing the rifle case, not separately
reported): `saveLoadBtn` always POSTed to the create-a-load endpoint
regardless of whether a load by that name already existed, so editing
a load's name produced a second, duplicate load rather than renaming
the original. Fixed both, plus added rifle- and load-level single-item
delete, all in one pass since it's the identical dict-keyed-by-name
architecture underneath both bugs and both new features.

**Root fix, not a workaround:** `self.rifles` and `rifle.loads` are
both plain dicts keyed by name -- a rename has to re-key the dict, not
just set an attribute on the object sitting at the old key. Added to
`ProfileStore` (`profiles.py`): `update_rifle_fields()` now accepts an
optional `name`, pops it out, applies the other field changes (with
the existing rollback-on-validation-failure behavior unchanged), then
-- only if the name actually changed -- deletes the old dict key,
sets the object's `.name`, and inserts it at the new key, updating
`active_rifle_name` too if it pointed at the old name. `update_load_
fields()` is the exact same pattern one level down, for `rifle.loads`/
`active_load_name`. Both reject an empty new name and a collision with
an existing name at that level (rifle-name collisions across the
account, load-name collisions within that one rifle).

**Frontend fix:** the create-vs-update decision no longer infers
anything from comparing names. Two explicit flags, `creatingNewRifle`/
`creatingNewLoad`, are set exactly where "+ New" is clicked (and the
zero-rifles/zero-loads fallback states, which are equally "nothing to
edit yet") and cleared exactly where a real rifle/load gets loaded for
editing (`applyRifleDetailToUI`/`fillLoadForm`) -- not inferred
anywhere else. `saveRifleBtn` now PUTs to the rifle's *original* name
(from `currentRifleDetail.name`, not the possibly-just-edited form
field) with the new name in the body; `saveLoadBtn` does the same
against `loadSelect.value` (which holds the original load name even
after the name *input* has been edited, since typing in a text field
doesn't change a separate `<select>`'s own value).

**New endpoints:** `PUT /v2/rifles/{rifle_name}/loads/{load_name}`
(update/rename a load -- the load-level counterpart to the rifle's
existing `PUT /v2/rifles/{rifle_name}`, which already existed but
didn't accept a name until this fix); `DELETE /v2/rifles/{rifle_name}/
loads/{load_name}` (closes a real, previously-documented gap --
COMMAND_GUIDE.md's own "Known limitations" said the only way to remove
one bad load was deleting the whole rifle). Rifle-level delete already
existed; only load-level was new.

**Verified against the real API, not just unit tests:** using the
`dt-auth-smoketest` fixture account -- created a rifle with a load,
edited the rifle's name field and saved through the actual save
handler, confirmed exactly one rifle exists afterward under the new
name with the load intact (not two rifles, not zero loads). Same
check for a load rename via the real `PUT .../loads/{name}` endpoint,
and for the new delete-load endpoint (removes only that load, rifle
and its other loads untouched, active_load_name reassigned or cleared
correctly). All test data cleaned up afterward -- fixture account
confirmed back to zero rifles.

**Noticed in passing, not caused by this work:** `GET /v2/profile`
(the display-name feature from earlier) throws a 500 against the real
project right now -- `KeyError: 'display_name'`, because `db/008_
display_name.sql` still hasn't been run. Already known and already
flagged when that feature shipped; degrades silently for the user
(`loadDisplayName()` catches every error), not a regression from
today's changes, just re-confirmed live while testing something else
in the same area.

**Verified:** 18 new unit tests (`tests/test_profiles_rename_delete.py`)
covering rename (dict re-keying, load preservation, no duplicate left
behind, active-name pointer updates, collision/empty-name rejection)
and delete (single-item removal, active pointer reassignment, 404s for
missing rifle/load) at both the rifle and load level. Full suite green,
zero regression. `COMMAND_GUIDE.md`'s "Known limitations" section
updated -- removed the now-resolved single-load-delete entry, and (found
stale while in there) two other already-resolved entries from earlier
sessions (CSV import, bundled bullet reference) that were never cleaned
up when those shipped.

**Owning lens:** Rick reported the rifle bug and specified building
both deletes now rather than queuing them; Build found the same root
cause independently affected loads, fixed both together, and verified
against the real API and a real account, not just new unit tests in
isolation.
