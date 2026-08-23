# Multi-Tenancy / Security Architecture — Design Document

Status: **Design only — not yet implemented.** Per Rick's instruction, this is the
checkpoint deliverable before implementation starts: design/schema/approach
finalized, not coded and shipped. This document is meant to go to independent
adversarial review (ChatGPT + Grok) before implementation is locked in.

Prepared: 2026-08-23

```
Lenses applied: Build (architecture) / Security (threat model, data handling) / Finance (real infra cost) / Marketing (sync as a side-benefit) / CoS (synthesis)
Key risks flagged: hand-rolled password auth is a common small-app vulnerability source; per-user conversation state has a real single-process limitation; the aggregate-data seam is illustrative, not final, per Rick's explicit scoping
Alternatives considered: hand-rolled email/password auth vs. managed auth provider vs. OAuth-only; per-user JSON files vs. real database; in-memory vs. DB-persisted conversation state
Recommendation + confidence: managed auth provider (Clerk) + Postgres + per-user-scoped queries + an events table as the aggregate-data seam — high confidence on the shape, medium on the specific auth vendor pending Rick's own evaluation
```

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
