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

### 6.2 Account-deletion data policy — DECIDED, CLOSED (2026-08-23)

**Rick's decision, final, no further input needed on this question:**
anonymize, don't delete the ballistic data. Matches the Option B
recommendation above, confirmed rather than left open.

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
- **Anonymization happens in the same deletion transaction as the
  identity delete** — strip the identifying reference from `events` rows
  atomically, not flagged for a later cleanup pass. A deletion that
  removes the identity but leaves anonymization pending is not
  acceptable; the two must be one operation.
- No change to the events-table seam's shape (§3) — this only governs
  what cascading delete does to rows that reference a deleted user, not
  the schema itself.

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
3. Voice conversation state (setup/calibration) moving into
   `conversation_state`, per §6.1/§7.3's DB-persisted decision --
   not started yet this pass.
4. A migration path for Rick's own existing single-user data stays
   deferred -- that needs Rick to actually have a real account first,
   which is exactly the "real-account decision" category to loop him
   in on rather than create unilaterally.
