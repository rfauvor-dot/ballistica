"""Hearing shots: find gunshot-like bangs in the camera's sound (2026-11-15).

Step 4 of the live-shot-detection plan, first part. A bang is only ever a hint
for the shot tracker -- "look at the paper now" -- never proof of a shot: on a
line with five or six shooters most bangs are other people's. A shot is still
only called when a new hole appears on the sheet.

What makes a gunshot easy to pick out: it goes from background noise to very
loud in a few milliseconds. Talking, wind, a bag being moved or a car door
don't rise that fast. So a bang here is a moment that is loud, well above the
recent background level, AND a big jump from the level about 50 ms earlier.

Tuned on the November 13 range video (2026-11-15): through the phone's
microphone a shot climbs about 30 dB in 30-40 ms -- not instantly, because the
phone evens out loud sound -- and then holds near -10 dBFS for about half a
second. Comparing with the level a few milliseconds earlier missed 13 of 17
shots; comparing with ~50 ms earlier finds them.

Strictly causal (each moment is compared only with what came before it), so
the same code can run on a live microphone. Pure numpy: no audio libraries.

    bangs = find_bangs(samples, rate)    # mono float samples in [-1, 1]
    for b in bangs: b.t_s, b.peak_db, b.rise_db
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SoundConfig:
    frame_ms: float = 5.0           # analysis frame length (and step)
    background_s: float = 1.0       # background = the quieter part of this much time before the frame
    background_percentile: float = 20.0  # (not the median: a shot's half-second ring-down would raise it
                                         # and hide a second shot fired a second later)
    min_above_background_db: float = 20.0
    min_rise_db: float = 20.0       # jump over the level `rise_lookback_ms` earlier
    rise_lookback_ms: float = 50.0  # (median of a `rise_span_ms` window centred there)
    rise_span_ms: float = 20.0
    min_level_dbfs: float = -30.0   # ignore anything quieter than this outright
    refractory_s: float = 0.25      # one bang per this long: its echo off the berm is not a second shot
    peak_window_ms: float = 50.0    # loudness is the peak within this long after the onset


@dataclass(frozen=True)
class Bang:
    t_s: float         # onset time, seconds from the start of the audio
    peak_db: float     # peak level, dB relative to full scale (0 = clipping)
    rise_db: float     # how sharply it rose over the frames just before it
    above_db: float    # how far above the recent background


def frame_levels_db(samples: np.ndarray, rate: int, frame_ms: float) -> np.ndarray:
    """RMS level of each consecutive frame, in dBFS."""
    n = max(1, int(round(rate * frame_ms / 1000.0)))
    usable = len(samples) // n * n
    frames = np.asarray(samples[:usable], dtype=np.float64).reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-9))


def find_bangs(samples: np.ndarray, rate: int, config: SoundConfig | None = None) -> list[Bang]:
    c = config or SoundConfig()
    levels = frame_levels_db(samples, rate, c.frame_ms)
    frame_s = c.frame_ms / 1000.0
    bg_n = max(1, int(round(c.background_s / frame_s)))
    back_hi = max(1, int(round((c.rise_lookback_ms - c.rise_span_ms / 2) / c.frame_ms)))
    back_lo = max(back_hi + 1, int(round((c.rise_lookback_ms + c.rise_span_ms / 2) / c.frame_ms)))
    peak_n = max(1, int(round(c.peak_window_ms / c.frame_ms)))
    refractory_n = max(1, int(round(c.refractory_s / frame_s)))
    bangs: list[Bang] = []
    last = -refractory_n
    for i in range(back_lo, len(levels)):
        level = levels[i]
        if level < c.min_level_dbfs or i - last < refractory_n:
            continue
        background = float(np.percentile(levels[max(0, i - bg_n):i], c.background_percentile))
        earlier = float(np.median(levels[i - back_lo:i - back_hi]))
        above, rise = level - background, level - earlier
        if above >= c.min_above_background_db and rise >= c.min_rise_db:
            peak = float(levels[i:i + peak_n].max())
            bangs.append(Bang(round(i * frame_s, 3), round(peak, 1), round(rise, 1), round(above, 1)))
            last = i
    return bangs


def to_mono_float(pcm16: np.ndarray, channels: int) -> np.ndarray:
    """Interleaved 16-bit samples -> mono floats in [-1, 1]."""
    x = pcm16.astype(np.float32) / 32768.0
    return x.reshape(-1, channels).mean(axis=1) if channels > 1 else x
