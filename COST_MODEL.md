# Ballistica cost model: what it costs to run, per user per month

*Built 2026-09-19. Regenerate every table below with `python -m scripts.cost_model`.
Each input in that script is tagged **VERIFIED** (read from the provider's own
page, or measured against the live API in this repo), **MEASURED**, or
**ASSUMED**. Anything ASSUMED is my estimate and is called out again in
"What I couldn't verify."*

## Bottom line

- **Variable cost is small and driven almost entirely by one thing: the LLM
  call.** A typical user costs **~$0.15-$1.10/month**, a very heavy Session
  Mode user **~$5**, with prompt caching (built today). Without caching those
  are ~$0.19 / $2.07 / $12.23.
- **Fixed cost dominates until you have ~100 users:** ~$33-$53/month
  (hosting + database + email). All-in per user: **$34.52 at 1 user, $4.59 at
  10, $1.80 at 100, $1.34 at 1,000.**
- **Two things found while doing this, both fixed today:** the paid voice
  endpoints were open to anyone and their rate limit was bypassable (see
  "Exposure found"), and a prompt-caching change cuts the biggest cost line
  ~80%.
- **Two of my earlier numbers were wrong** (Session Mode's per-hour cost
  breakdown; the code uses `gpt-4o-transcribe`, not Whisper). Corrected below.

## Unit prices (what each provider charges)

| Item | Price | Status |
|---|---|---|
| Claude Haiku 4.5, input | $1.00 / M tokens | VERIFIED (platform.claude.com pricing) |
| Claude Haiku 4.5, output | $5.00 / M tokens | VERIFIED |
| Haiku 4.5 prompt-cache write (5 min) / read | $1.25 / $0.10 per M tokens | VERIFIED |
| `gpt-4o-transcribe` (speech to text) | ~$0.006 / minute of audio | VERIFIED (OpenAI's stated estimate) |
| `tts-1` (voice out) | $15.00 / M characters | VERIFIED |
| Supabase Pro | $25/mo (100K MAU, 8 GB DB, 250 GB egress) | VERIFIED |
| Supabase Free | $0, **pauses after 1 week of inactivity** | VERIFIED |
| Weather (aviationweather.gov METAR) | free | from the code |
| Wake-word listening | free (browser's on-device recognizer) | from the code |
| Render paid instance (~0.5 CPU / 512 MB) | ~$7/mo | **ASSUMED, not verified** |
| Render persistent disk | ~$0.25/GB/mo | **ASSUMED** (search snippet only) |
| Domain | ~$1/mo | **ASSUMED** |
| Custom SMTP for signup emails | $0-$20/mo | **ASSUMED range** |

## What one call actually costs (measured against the live API)

The model call is expensive for a reason that has nothing to do with the
answer: every call re-sends **~5,955 input tokens** of fixed instructions and
tool definitions and gets back only 30-120 tokens. Prompt caching bills that
fixed part at 10% after the first call.

| Call | Uncached | Cache hit | Cache miss (write) |
|---|---|---|---|
| Normal-mode intent | $0.00626 | **$0.00119** (81% cheaper) | $0.00766 (22% dearer) |
| Session Mode intent | $0.00722 | **$0.00157** (78% cheaper) | $0.00864 |
| Setup field extraction (1,233-1,696 tokens; too small to cache) | $0.00192 | n/a | n/a |
| Speech to text, one 5 s clip | $0.00050 | | |
| Voice out, one wake-word reply (~80 chars) | $0.00120 | | |
| Voice out, one Session Mode readback (~30 chars) | $0.00045 | | |

The cache key is the exact prefix, which is **identical for every user**, so
the cache is shared: more users means a warmer cache. A miss costs 22% more
than uncached, so caching pays back after a single hit. Verified end to end
through the real `extract_intent` path: the per-turn Session Mode suffix
(saved rifles, last reading) changes every call and does **not** break the
cache, because it sits in a separate block after the cache breakpoint.

## Per user per month

Usage profiles are **my assumptions**, anchored on two real data points: your
09-05 range session (45-75 min, 54 readings, ~90-200 speech segments) and the
50 real turns across two wake-word sessions in the 09-16/17 log review.

| Profile | Assumed usage | Uncached | **With caching** | (LLM / STT / TTS) |
|---|---|---|---|---|
| Casual | 2 range days x 25 wake-word turns | $0.19 | **$0.15** | $0.07 / $0.03 / $0.06 |
| Regular | 4 days x 30 turns + 1.5 Session Mode hrs | $2.07 | **$1.11** | $0.70 / $0.17 / $0.25 |
| Heavy | 10 days x 40 turns + 10 Session Mode hrs | $12.23 | **$5.34** | $3.23 / $0.95 / $1.15 |
| Heavy, noisy range (2x the segments, other people talking) | | | **$9.66** | |

Blended (50% casual / 35% regular / 15% heavy): **$1.27/user/month with
caching, $2.66 without.** One-time onboarding (setting up ~2 rifles and 3
loads by voice) is ~$0.22 per new user.

**One Session Mode hour (150 segments): $1.06 uncached (LLM $0.92 / STT $0.07
/ TTS $0.07), about $0.43 with caching.** Session Mode is where the money
goes: nearly every narrated reading is a model call, while wake-word mode
answers most commands from free pattern matching.

## Fixed costs, and what a user really costs at each size

| Users | Fixed / month | Fixed per user | Variable | **All-in per user** | (uncached) |
|---|---|---|---|---|---|
| 1 | $33.25 | $33.25 | $1.27 | **$34.52** | $35.91 |
| 10 | $33.25 | $3.33 | $1.27 | **$4.59** | $5.98 |
| 100 | $53.25 | $0.53 | $1.27 | **$1.80** | $3.19 |
| 500 | $71.25 | $0.14 | $1.27 | **$1.41** | $2.80 |
| 1,000 | $71.25 | $0.07 | $1.27 | **$1.34** | $2.73 |

Fixed = Supabase Pro $25 + Render ~$7 + disk ~$0.25 + domain ~$1, plus ~$20
SMTP from 100 users up and an assumed ~$18 larger Render instance from 500
up. **Scaling caveat (not load-tested):** the app runs as a single instance
because the rate limiter is in memory and a persistent disk is attached, so
horizontal scaling would need a shared limiter store first. One instance is
plausibly fine into the low thousands of users (voice turns are mostly
waiting on other services); I have not measured it.

## Price needed for a target margin

Variable cost + card fees only (fixed costs excluded; **the 2.9% + $0.30 card
fee is an assumed typical rate**):

| Profile | Variable cost | Price for 60% / 70% / 80% gross margin |
|---|---|---|
| Casual | $0.15 | $1.22 / $1.67 / $2.64 |
| Regular | $1.11 | $3.81 / $5.22 / $8.27 |
| Heavy | $5.34 | $15.19 / $20.80 / $32.97 |

Read as: any flat price of ~$10/month clears a 70% margin on a Regular user
comfortably but only just clears break-even-plus-margin on a Heavy
Session-Mode-every-trip user. If Session Mode is the premium feature,
that's the natural line to price around (or to meter).

## Corrections to my earlier estimates

1. **Session Mode cost (BACKLOG.md, 2026-09-05).** I estimated ~$0.50-$1.20
   per session from ~1,200 input tokens per model call. Measured reality is
   **~6,750 tokens per call**. The LLM line was underestimated 2-5x, while the
   STT line was overestimated ~4-6x (the app only sends speech segments, not
   every open-mic minute). The total landed near the old range by
   coincidence; the composition was wrong, which matters because the LLM line
   is the one caching fixes.
2. **STT provider.** The estimate priced Whisper; the code uses
   `gpt-4o-transcribe` (same ~$0.006/min list price, but worth being
   accurate). RISK_REGISTER.md had already flagged the older Tier 1/2 model
   as stale; this replaces it.

## Exposure found and fixed today (real money at risk)

`/voice/speak` (paid TTS) and `/voice/transcribe` (paid STT) had **no login
requirement, no cap on input size**, and were protected only by a per-IP rate
limit. I tested that limit against production with a harmless blank-text
request: plain requests and a fixed forged `X-Forwarded-For` header were
throttled at request 21, but **rotating a forged header got 26 requests and
zero throttles.** The code trusts the header's first value, which the caller
controls. So in practice there was no working limit: worst case per call was
~$0.06 (TTS at OpenAI's 4,096-character maximum) or ~$0.15 (25 minutes of
audio), bounded only by your OpenAI account's own rate limit.

I have **no evidence this was ever abused**, and I can't see your OpenAI
dashboard. Check its Usage page for any spike you can't explain.

**Fixed (in the same commit as this document):**
- Both endpoints now require a verified login, which also makes their rate
  limit per verified user instead of per spoofable IP.
- TTS text is clipped at 1,500 characters (real replies: median 46 chars);
  uploads are capped at 400 KB (the app itself hard-stops recordings at 12
  seconds, ~200 KB).
- The web app sends the login token on both calls. Verified in the browser
  against the real API; 8 new tests.

**Ceiling after the login/size fix alone, per signed-in account, at the
20/minute limit:** TTS $27/hr, STT $48/hr, model calls $8.67/hr, and anyone
can make a free account. The per-user daily budget below closes that.

## Per-user daily budget (built 2026-09-19)

Every paid call is metered in **actual dollars** and charged to the user who
made it: Claude calls from the response's own token counts (cache reads and
writes included), speech-to-text from its reported token usage, text-to-speech
from the exact character count. Once a user's day is spent, all three paid
paths refuse with a 429 until 00:00 UTC. Free features (drop solutions typed
or tapped, rifle and load management, offline mode) keep working.

- **Default: $3.00 per user per day**, changeable without a code change via the
  `DAILY_BUDGET_USD` environment variable (set it in Render). A missing,
  unparseable, or non-positive value falls back to $3.00 instead of
  silently turning the cap off; to lift it, set a large number.
- **Why $3:** a typical range day costs cents. A very heavy one (an hour of
  Session Mode, uncached) is ~$1. Three uncached Session Mode hours is ~$3.20,
  ~$1.30 cached. So $3 is several times a real heavy day and never in a
  legitimate user's way, while cutting the worst free-account abuse from
  ~$66/hour to $3/day per account.
- **The account menu shows it:** "Voice usage today: $0.42 of $3.00 (resets
  midnight UTC)". It's also the first source of **measured** per-user cost in
  this app (`GET /v2/usage/today`).
- **Verified live** with real Supabase, Claude, and OpenAI: the ledger equalled
  the provider-reported Claude usage to the last decimal (2 real calls,
  $0.00243), TTS was exact ($0.000345 for 23 characters), a real 1.4 s
  transcription cost $0.000318, and with the budget set tiny all three paid
  endpoints returned 429 with a Retry-After (~22 hours) while free endpoints
  still worked.

**What it is not:** it's an in-memory safety cap, not a billing record. It
**resets when the server restarts or redeploys** (a user can get a fresh
budget after a deploy) and is per server instance, the same single-instance
assumption the rate limiter already makes. It checks before a call and
charges after, so one call can overshoot. If usage ever becomes billing or a
paid tier, it needs to move to a database table. It also bounds each
*account*, not each person: someone creating many accounts gets $3/day each
(email confirmation slows that but doesn't stop it), so the provider-side
monthly spend limits below remain the backstop.

## Levers, in order of value

1. **Set hard monthly spend limits in the OpenAI and Anthropic consoles.**
   This is the strongest protection and only you can do it (it's an account
   setting). Nothing in the code can cap an account's total bill.
2. **Prompt caching. Done today.** ~80% off the biggest line. Roughly halves
   the blended per-user cost.
3. **A per-user daily spend budget. Done (2026-09-19)** -- see the next
   section.
4. Not recommended yet: `gpt-4o-mini-transcribe` at ~$0.003/min would halve the
   STT line (it's only ~10-15% of cost) but STT accuracy on numbers and
   ballistics terms is what the whole product depends on; test before
   trading it for pennies.
5. Small: prune `conversation_debug_log` (it's labeled temporary and grows
   forever: ~0.4 KB/turn, harmless for a long time); pre-generate the fixed
   wake-word acknowledgement phrases instead of calling TTS each time.

## What I couldn't verify (please confirm these)

- **Your actual Render plan and price.** Render's pricing page wouldn't
  render for me. I assumed ~$7/mo + ~$0.25/GB disk. Your invoice is the
  source of truth. (A persistent disk requires a paid instance, so you're
  at least on one.)
- **Whether Supabase is on Free or Pro.** Free pauses after a week of
  inactivity, which would take the app down; I've assumed Pro.
- **Whether custom SMTP is set up.** Supabase's built-in email service is
  limited to **2 emails per hour** and documented as non-production
  (verified, supabase.com docs). If you haven't configured custom SMTP,
  signups beyond two an hour will fail.
- **Whether spend limits are set** on the OpenAI and Anthropic accounts.
- **My usage profiles.** They're anchored on two real sessions but they're
  estimates. Your downloaded conversation log from a real range day is the
  best way to replace them with measured numbers.
- **Card-processing fees and any pricing plan.** I don't know your intended
  price or payment provider; the margin table uses a typical 2.9% + $0.30.
