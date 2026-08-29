# Ballistica — Scope Camera Feed: Design & Status

**Status: CONFIRMED, in build phase.** Hardware not yet purchased —
software pipeline development doesn't require it (see below). Full
feasibility research (protocol comparison, mount options, cost,
alternatives, CV-feasibility assessment) is preserved as a published
artifact; this doc is the living record of the decision and build
status going forward.

## The decision, final

**Use case:** not slow-motion or catching a projectile in flight — a
real-time feedback loop during a range session: see where a shot landed
on target, make a scope adjustment, send the next round, all within a
shooter's time limit. This is the requirement the whole protocol choice
below is driven by.

**Hardware spec:**
- Camera: a **used GoPro HERO9, 10, or 11 Black**. Not the current
  $199 base "Hero" model (no WiFi streaming at all); not needed to be
  new — these are well off current pricing used/refurb.
- Mount: **EagleVision scope cam mount** (Hajimoto Productions),
  **75/25 image split** — 75% of the light goes to the camera, 25% to
  the shooter's eye, favoring the camera's low-light capture over the
  shooter's own view through the glass.
- Cost: **~$400** for the mount + lens kit. Camera purchased
  separately (~$130–220 used for HERO9–11 Black, per the research
  below).

**Protocol: local WiFi UDP preview stream — not RTMP.** GoPro has two
unrelated systems both loosely called "livestreaming":

| | Latency | What it needs |
|---|---|---|
| RTMP | **2–13 seconds** | Internet + a remote relay server |
| Local WiFi UDP preview | **~210 ms** | Nothing but joining the camera's own WiFi hotspot directly |

RTMP's delay is disqualifying for this use case — a shooter can't wait
several seconds to see where a shot landed. The local UDP stream is the
one that actually fits the "adjust and send the next round" loop, and
it's well within budget for a human reaction-and-adjustment cycle.

## Full research

The original feasibility research — GoPro model support broken down by
generation, the RTMP-vs-local-stream discovery, mount-type comparison
(cheap rail mount vs. eyepiece adapter) with real user complaints
sourced, automatic-shot-detection feasibility (real precedent exists,
but for a fixed camera-on-target setup, not a scope-mounted live feed —
a materially harder, separate problem), and a comparison against
Tactacam/Triggercam (confirmed closed, no public API) and DJI Osmo
Action/Insta360 (both have real open SDKs, considered and set aside for
now) — is published here, with full sourcing:

**[Live Scope Feed Feasibility](https://claude.ai/code/artifact/f597472a-2cc2-4640-9ad7-157d8f5d2ed4)**

## What's built (2026-08-29)

The receiving/decoding pipeline — the part of this that doesn't need
the physical scope mount to develop or test, only *a* WiFi-capable
GoPro:

- **[ballistica/scope_stream.py](ballistica/scope_stream.py)** —
  `start_preview_stream()` sends the correct HTTP command for either
  camera generation (the older "legacy" gpControl API for HERO4–7,
  confirmed against real community documentation for exactly Rick's own
  HERO4; the newer official Open GoPro HTTP API for HERO9+, the actual
  target hardware). `verify_stream()` opens the UDP feed
  (`udp://10.5.5.9:8554`) via OpenCV's bundled FFmpeg build — no
  separate ffmpeg install needed — and proves *real frames are actually
  decoding*, not just that a command was accepted; it times out with a
  clear message rather than hanging forever if the camera never
  responds. `save_sample_frame()` grabs one real frame to a JPEG, so
  there's an actual photo to look at as proof, not just a log line.
- **[scripts/test_scope_stream.py](scripts/test_scope_stream.py)** — the
  runnable diagnostic: `python -m scripts.test_scope_stream` (defaults
  to the HERO4-era API to match the camera this gets tested against
  first). Prints exactly what happened at each step and saves
  `scope_stream_sample.jpg` on success.
- `opencv-python` added to `requirements.txt` — the one new dependency
  this needed; it bundles its own FFmpeg, so no system-level install is
  required on whatever machine runs the test.

## What this cannot verify yet

Two things genuinely require the physical EagleVision mount and wait
for it:
1. **The actual reticle view** — today's test proves the UDP pipeline
   itself works against *a* GoPro on a tripod pointed at anything; it
   says nothing about image quality or framing through a real scope.
2. **True end-to-end latency once physically assembled** — the ~210ms
   figure is GoPro's own spec for the stream itself; the full chain
   (camera → mount optics → WiFi → decode → whatever displays it) needs
   measuring for real once there's a real assembled rig, not assumed
   from the spec sheet.

## Immediate next step

Rick has a **HERO4 at home already** — right at the edge of the
documented support range (HERO4 Silver/Black is the oldest generation
confirmed to support the local UDP stream at all; HERO4 Session needs
different handling and isn't what this targets). Testing against it
first, before any purchase, is the plan: connect that specific machine's
WiFi directly to the HERO4's own hotspot (this replaces that machine's
normal internet connection for the duration — not avoidable, it's how
the camera's hotspot works), then run:

```bash
python -m scripts.test_scope_stream
```

If it reports real frames decoding, the pipeline is proven end-to-end
against real hardware and the only remaining unknown is the physical
mount. If HERO4 turns out not to work, that's exactly the kind of thing
this test is for — it settles the question with real evidence instead
of assumption, and the fallback is simply to develop/test against a
borrowed or purchased HERO9–11 instead.
