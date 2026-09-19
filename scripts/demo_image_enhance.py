"""Runnable proof-of-concept for ballistica/image_enhance.py -- answers
"is AI/software-based photo cleanup for a spotting-scope rig even
possible" with actual before/after numbers and images, not just an
argument.

No real hardware or real target photo needed for this to be meaningful:
this synthesizes a clean "target" image, then degrades it the way a
cheap spotting scope + phone digiscoping setup would (optical blur,
sensor noise, hand jitter between burst frames) -- a controlled test
where the ground truth is known, so "did this actually recover real
detail" is checkable, not just asserted. Real optics have their own
aberrations this doesn't model; this proves the enhancement math works,
not that it will look exactly this good through real glass.

Run: python -m scripts.demo_image_enhance
Writes 3 JPEGs to scripts/demo_output/ for a human to actually look at.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

from ballistica.image_enhance import sharpen_denoise, sharpness_score, stack_frames

OUT_DIR = os.path.join(os.path.dirname(__file__), "demo_output")


def psnr_vs_ground_truth(result: np.ndarray, clean: np.ndarray) -> float:
    """PSNR against the known-clean synthetic image -- only meaningful
    here because this is a controlled test with real ground truth,
    which a real digiscoped photo never has. Aligns `result` back onto
    `clean` first via ECC, so jitter (a framing difference, not a
    quality difference) doesn't get scored as error. This is the
    metric that actually answers "did this recover real detail,"
    unlike variance-of-Laplacian, which can't tell a sharp edge from
    amplified noise -- see the first run of this script, where
    denoising correctly REDUCED that score by removing noise the
    metric had been miscounting as detail."""
    result_gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY).astype(np.float32)
    clean_gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY).astype(np.float32)
    warp_matrix = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    try:
        _, warp_matrix = cv2.findTransformECC(clean_gray, result_gray, warp_matrix, cv2.MOTION_EUCLIDEAN, criteria)
    except cv2.error:
        pass  # fall back to unaligned comparison rather than failing the demo
    aligned = cv2.warpAffine(
        result, warp_matrix, (clean.shape[1], clean.shape[0]),
        flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
    )
    return cv2.PSNR(aligned, clean)


def make_clean_target(size: int = 600) -> np.ndarray:
    """A synthetic paper target: white background, black scoring rings,
    and a handful of "bullet holes" (small dark circles off-center) --
    enough fine detail (ring edges, hole edges) for a sharpness/clarity
    test to mean something."""
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    center = (size // 2, size // 2)
    for radius, thickness in [(260, 3), (200, 3), (140, 3), (80, 3), (30, 3)]:
        cv2.circle(img, center, radius, (20, 20, 20), thickness, lineType=cv2.LINE_AA)
    holes = [(-60, -40), (35, -70), (10, 20), (-20, 55), (75, 15)]
    for dx, dy in holes:
        cv2.circle(img, (center[0] + dx, center[1] + dy), 9, (10, 10, 10), -1, lineType=cv2.LINE_AA)
    return img


def degrade(clean: np.ndarray, rng: np.random.Generator, jitter_px: int = 4) -> np.ndarray:
    """Simulates one frame out of a handheld/tripod-jitter burst shot
    through a cheap digiscoping setup: slight random translation
    (hand/rig jitter), optical blur (soft cheap glass + digiscoping
    vignetting-adjacent softness), then sensor noise (small-sensor
    phone camera in non-ideal light)."""
    h, w = clean.shape[:2]
    dx, dy = rng.uniform(-jitter_px, jitter_px, size=2)
    m = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
    jittered = cv2.warpAffine(clean, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    blurred = cv2.GaussianBlur(jittered, (0, 0), sigmaX=2.2)
    noise = rng.normal(0, 14, blurred.shape).astype(np.float32)
    noisy = np.clip(blurred.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return noisy


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.default_rng(seed=7)

    clean = make_clean_target()
    burst = [degrade(clean, rng) for _ in range(8)]

    single_degraded = burst[0]
    single_cleaned = sharpen_denoise(single_degraded)
    stack_result = stack_frames(burst)
    stacked_and_cleaned = sharpen_denoise(stack_result.stacked)

    cv2.imwrite(os.path.join(OUT_DIR, "0_ground_truth_clean.jpg"), clean)
    cv2.imwrite(os.path.join(OUT_DIR, "1_degraded_single_frame.jpg"), single_degraded)
    cv2.imwrite(os.path.join(OUT_DIR, "2_single_frame_sharpen_denoise.jpg"), single_cleaned)
    cv2.imwrite(os.path.join(OUT_DIR, "3_stacked_8_frames_then_cleaned.jpg"), stacked_and_cleaned)

    p_degraded = psnr_vs_ground_truth(single_degraded, clean)
    p_single_cleaned = psnr_vs_ground_truth(single_cleaned, clean)
    p_stacked = psnr_vs_ground_truth(stack_result.stacked, clean)
    p_stacked_cleaned = psnr_vs_ground_truth(stacked_and_cleaned, clean)

    print("PSNR vs. known-clean ground truth (higher = closer to the real target, dB):")
    print(f"  degraded single frame (simulated cheap scope+phone photo): {p_degraded:6.2f} dB")
    print(f"  -> single-frame sharpen/denoise only:                      {p_single_cleaned:6.2f} dB  ({p_single_cleaned - p_degraded:+.2f} dB)")
    print(f"  -> {stack_result.detail}")
    print(f"     8-frame stack, before sharpening:                      {p_stacked:6.2f} dB  ({p_stacked - p_degraded:+.2f} dB)")
    print(f"     8-frame stack THEN sharpen/denoise:                    {p_stacked_cleaned:6.2f} dB  ({p_stacked_cleaned - p_degraded:+.2f} dB)")

    print("\n(Laplacian-variance 'sharpness' score, included for context -- see")
    print(" psnr_vs_ground_truth()'s docstring for why this metric is misleading")
    print(" once denoising is involved: it can't tell a real edge from noise.)")
    for label, img in [
        ("degraded single frame", single_degraded),
        ("single-frame cleaned", single_cleaned),
        ("8-frame stack (raw)", stack_result.stacked),
        ("8-frame stack + cleaned", stacked_and_cleaned),
    ]:
        print(f"  {label:28s} {sharpness_score(img).score:8.1f}")

    print(f"\nImages written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
