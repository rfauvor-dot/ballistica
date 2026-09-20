"""Per-user monthly cost model for Ballistica (2026-09-19). Run:
    python -m scripts.cost_model
Every input is a named constant with its source, so the assumptions can be
changed and the tables regenerated -- COST_MODEL.md is the written-up
version. VERIFIED = read from the provider's own page or measured against
the live API in this repo; ASSUMED = my estimate, flagged as such.
"""
from __future__ import annotations

# ---------------------------------------------------------------- prices
# VERIFIED 2026-09-19 (platform.claude.com pricing page): Claude Haiku 4.5
HAIKU_IN, HAIKU_OUT, HAIKU_CACHE_WRITE_5M, HAIKU_CACHE_READ = 1.00, 5.00, 1.25, 0.10   # $/M tokens
# VERIFIED 2026-09-19 (developers.openai.com pricing page)
STT_PER_MIN = 0.006          # gpt-4o-transcribe, OpenAI's stated estimate
TTS_PER_M_CHARS = 15.00      # tts-1
# VERIFIED (supabase.com/pricing): Pro plan. Free pauses after 1 week idle -> not viable for production.
SUPABASE_PRO = 25.00
# ASSUMED, NOT VERIFIED (Render's pricing page would not render for me): confirm against your invoice.
RENDER_INSTANCE = 7.00       # 0.5 CPU / 512 MB paid instance; a persistent disk requires a paid instance
RENDER_DISK = 0.25           # ~1 GB at ~$0.25/GB/mo (a search snippet said this; not from Render's own page)
DOMAIN = 1.00                # ~$12/yr
SMTP_LOW, SMTP_HIGH = 0.0, 20.0   # custom SMTP (Supabase's built-in email is 2/hour, non-production) -- ASSUMED range

# ------------------------------------------------- measured per-call tokens
# MEASURED against the live API (count_tokens + real calls, 2026-09-19)
INTENT_IN, INTENT_OUT = 5955, 60          # normal-mode intent call (system prompt + tools)
SESSION_IN, SESSION_OUT = 6773, 90        # Session Mode intent call (adds tool + per-turn suffix)
SETUP_IN, SETUP_OUT = 1465, 90            # setup field extraction (avg of load 1233 / rifle 1696) -- too small to cache
# cached (measured through the real extract_intent path): static prefix read from cache, small dynamic part billed normally
INTENT_CACHED_UNCACHED_IN, INTENT_CACHED_READ = 332, 5623
SESSION_CACHED_UNCACHED_IN, SESSION_CACHED_READ = 500, 6151
INTENT_CACHE_WRITE_TOKENS, SESSION_CACHE_WRITE_TOKENS = 5623, 6151


def llm_cost(uncached_in, out, cache_read=0, cache_write=0):
    return (uncached_in * HAIKU_IN + out * HAIKU_OUT + cache_read * HAIKU_CACHE_READ
            + cache_write * HAIKU_CACHE_WRITE_5M) / 1e6


INTENT_UNCACHED = llm_cost(INTENT_IN, INTENT_OUT)
INTENT_HIT = llm_cost(INTENT_CACHED_UNCACHED_IN, INTENT_OUT, cache_read=INTENT_CACHED_READ)
INTENT_MISS = llm_cost(INTENT_CACHED_UNCACHED_IN, INTENT_OUT, cache_write=INTENT_CACHE_WRITE_TOKENS)
SESSION_UNCACHED = llm_cost(SESSION_IN, SESSION_OUT)
SESSION_HIT = llm_cost(SESSION_CACHED_UNCACHED_IN, SESSION_OUT, cache_read=SESSION_CACHED_READ)
SESSION_MISS = llm_cost(SESSION_CACHED_UNCACHED_IN, SESSION_OUT, cache_write=SESSION_CACHE_WRITE_TOKENS)
SETUP_CALL = llm_cost(SETUP_IN, SETUP_OUT)


def cached_llm(hit_rate, hit, miss):
    return hit_rate * hit + (1 - hit_rate) * miss


# ------------------------------------------------------------- per turn I/O
CLIP_SECONDS = 5.0            # ASSUMED avg speech clip incl. VAD padding
STT_TURN = CLIP_SECONDS / 60 * STT_PER_MIN
REPLY_CHARS_WAKE = 80         # MEASURED mean reply 61 chars + wake ack (~25 chars) spread over turns
REPLY_CHARS_SESSION = 30      # MEASURED: session readbacks ("1150, shot 3.") median 21, mean 28
TTS_WAKE_TURN = REPLY_CHARS_WAKE * TTS_PER_M_CHARS / 1e6
TTS_SESSION_SEG = REPLY_CHARS_SESSION * TTS_PER_M_CHARS / 1e6
SEGMENTS_PER_SESSION_HOUR = 150   # from Rick's real 09-05 session estimate (90-200), see BACKLOG.md
SESSION_LLM_SHARE = 0.85          # ASSUMED: readings/narration go to the tracker's LLM call; commands/mute/summary don't

# ------------------------------------------------------------ usage profiles
# turns are wake-word turns; mix = (fast_path, intent_llm, setup_small_llm) shares
PROFILES = {
    "Casual":  dict(days=2,  wake_turns_per_day=25, mix=(0.60, 0.30, 0.10), session_hours=0.0,  hit=0.60),
    "Regular": dict(days=4,  wake_turns_per_day=30, mix=(0.55, 0.33, 0.12), session_hours=1.5,  hit=0.80),
    "Heavy":   dict(days=10, wake_turns_per_day=40, mix=(0.55, 0.33, 0.12), session_hours=10.0, hit=0.90),
}


def monthly_variable(p, cached: bool, noise_multiplier: float = 1.0):
    turns = p["days"] * p["wake_turns_per_day"]
    fast, intent, setup = p["mix"]
    per_intent = cached_llm(p["hit"], INTENT_HIT, INTENT_MISS) if cached else INTENT_UNCACHED
    per_session = cached_llm(p["hit"], SESSION_HIT, SESSION_MISS) if cached else SESSION_UNCACHED
    stt = turns * STT_TURN
    tts = turns * TTS_WAKE_TURN
    llm = turns * (intent * per_intent + setup * SETUP_CALL)
    segs = p["session_hours"] * SEGMENTS_PER_SESSION_HOUR * noise_multiplier
    stt += segs * STT_TURN
    tts += segs * TTS_SESSION_SEG
    llm += segs * SESSION_LLM_SHARE * per_session
    return dict(llm=llm, stt=stt, tts=tts, total=llm + stt + tts, turns=turns, segments=segs)


ONBOARDING_ONE_TIME = 60 * (SETUP_CALL + STT_TURN + TTS_WAKE_TURN)   # ~2 rifles + 3 loads by voice, ~60 modal turns

FIXED_FLOOR = SUPABASE_PRO + RENDER_INSTANCE + RENDER_DISK + DOMAIN + SMTP_LOW
FIXED_WITH_SMTP = SUPABASE_PRO + RENDER_INSTANCE + RENDER_DISK + DOMAIN + SMTP_HIGH


def fmt(x):
    return f"${x:,.2f}"


if __name__ == "__main__":
    print("== per-call model cost ==")
    print(f"intent call, uncached           {INTENT_UNCACHED:.5f}")
    print(f"intent call, cache HIT          {INTENT_HIT:.5f}   ({(1 - INTENT_HIT / INTENT_UNCACHED) * 100:.0f}% cheaper)")
    print(f"intent call, cache MISS (write) {INTENT_MISS:.5f}   ({(INTENT_MISS / INTENT_UNCACHED - 1) * 100:.0f}% dearer)")
    print(f"session call, uncached          {SESSION_UNCACHED:.5f}")
    print(f"session call, cache HIT         {SESSION_HIT:.5f}   ({(1 - SESSION_HIT / SESSION_UNCACHED) * 100:.0f}% cheaper)")
    print(f"session call, cache MISS        {SESSION_MISS:.5f}")
    print(f"setup extraction call           {SETUP_CALL:.5f}")
    print(f"STT per 5s clip                 {STT_TURN:.5f}   TTS per wake reply {TTS_WAKE_TURN:.5f}   TTS per session readback {TTS_SESSION_SEG:.5f}")

    one_hr = dict(session_hours=1.0, days=0, wake_turns_per_day=0, mix=(1, 0, 0))
    u1 = monthly_variable(dict(one_hr, hit=0.9), False)
    c1 = monthly_variable(dict(one_hr, hit=0.9), True)
    print(f"ONE Session Mode hour ({SEGMENTS_PER_SESSION_HOUR} segments): uncached {fmt(u1['total'])} "
          f"(LLM {fmt(u1['llm'])} / STT {fmt(u1['stt'])} / TTS {fmt(u1['tts'])}); "
          f"cached at 90% hits {fmt(c1['total'])} (LLM {fmt(c1['llm'])})")

    print("\n== monthly variable cost per user ==")
    print(f"{'profile':9s} {'turns':>5s} {'segs':>5s} | {'uncached total':>14s} | {'cached total':>12s} (LLM/STT/TTS)      | noisy range x2 segs, cached")
    for name, p in PROFILES.items():
        u, c = monthly_variable(p, False), monthly_variable(p, True)
        n = monthly_variable(p, True, 2.0)
        print(f"{name:9s} {u['turns']:5d} {u['segments']:5.0f} | {fmt(u['total']):>14s} | {fmt(c['total']):>12s} ({fmt(c['llm'])}/{fmt(c['stt'])}/{fmt(c['tts'])}) | {fmt(n['total'])}")
    print(f"one-time onboarding (voice setup of ~2 rifles + 3 loads): {fmt(ONBOARDING_ONE_TIME)}")

    print("\n== fixed monthly costs ==")
    print(f"floor {fmt(FIXED_FLOOR)}  (Supabase Pro 25 + Render ~7 + disk ~.25 + domain ~1 + SMTP 0)")
    print(f"with paid SMTP {fmt(FIXED_WITH_SMTP)}")

    mix = {"Casual": 0.50, "Regular": 0.35, "Heavy": 0.15}
    blended_c = sum(w * monthly_variable(PROFILES[k], True)["total"] for k, w in mix.items())
    blended_u = sum(w * monthly_variable(PROFILES[k], False)["total"] for k, w in mix.items())
    print(f"\nblended variable per user (50% casual / 35% regular / 15% heavy): uncached {fmt(blended_u)}, cached {fmt(blended_c)}")
    print("\n== all-in cost per user per month by user count (blended mix, caching on) ==")
    print(f"{'users':>6s} {'fixed/mo':>9s} {'fixed per user':>14s} {'variable':>9s} {'ALL-IN per user':>15s}   {'(uncached)':>10s}")
    for n, fixed in [(1, FIXED_FLOOR), (10, FIXED_FLOOR), (100, FIXED_WITH_SMTP), (500, FIXED_WITH_SMTP + 18),
                     (1000, FIXED_WITH_SMTP + 18)]:
        print(f"{n:6d} {fmt(fixed):>9s} {fmt(fixed / n):>14s} {fmt(blended_c):>9s} {fmt(fixed / n + blended_c):>15s}   {fmt(fixed / n + blended_u):>10s}")

    print("\n== monthly price needed for a target gross margin (variable + payment fee; fixed excluded), caching on ==")
    fee_pct, fee_fixed = 0.029, 0.30   # ASSUMED typical card processing
    print(f"{'profile':9s} {'var cost':>8s} | price for 60% / 70% / 80% margin")
    for name, p in PROFILES.items():
        v = monthly_variable(p, True)["total"]
        outs = []
        for m in (0.60, 0.70, 0.80):
            # price*(1-fee_pct) - fee_fixed - v = m*price  ->  price = (v+fee_fixed)/(1-fee_pct-m)
            outs.append((v + fee_fixed) / (1 - fee_pct - m))
        print(f"{name:9s} {fmt(v):>8s} | " + " / ".join(fmt(x) for x in outs))

    print("\n== abuse ceilings: what ONE caller can spend per hour, by endpoint, at the 20/min limit ==")
    tts_call_before = 4096 * TTS_PER_M_CHARS / 1e6      # OpenAI's own 4096-char TTS input maximum
    tts_call_after = 1500 * TTS_PER_M_CHARS / 1e6
    stt_max_before = 25 * STT_PER_MIN                   # gpt-4o-transcribe's 1500 s (25 min) per-file maximum
    stt_after = 6.7 * STT_PER_MIN                       # 400 KB cap; crafted ~8 kbps upload ~= 6.7 min (worst case; real app clips are <=12 s)
    print(f"TTS   per call before {fmt(tts_call_before)} -> after {fmt(tts_call_after)};  x20/min x60 = {fmt(tts_call_before * 1200)}/hr -> {fmt(tts_call_after * 1200)}/hr")
    print(f"STT   per call before {fmt(stt_max_before)} -> after {fmt(stt_after)};  x20/min x60 = {fmt(stt_max_before * 1200)}/hr -> {fmt(stt_after * 1200)}/hr")
    print(f"LLM   per call {fmt(SESSION_UNCACHED)} uncached;  x20/min x60 = {fmt(SESSION_UNCACHED * 1200)}/hr (this endpoint was always login-gated)")
