"""Image clarity recovery for digiscoped target photos -- see
BACKLOG.md "AI-assisted spotting-scope image cleanup" for the decision
record and why this deliberately avoids generative upscaling.

Two techniques, both classical/deterministic (no neural net, nothing
that can hallucinate a pixel that wasn't in the source light):

  - `sharpen_denoise()`: single-frame cleanup (non-local-means denoise,
    then unsharp-mask sharpening). Works on any one photo.
  - `stack_frames()`: multi-frame alignment + averaging ("lucky
    imaging", the same principle amateur astrophotographers use to
    out-resolve a single frame from a shaky, noisy setup). Needs
    several photos of the SAME static scene -- exactly what a burst of
    a paper target from a tripod-mounted rig gives you for free, and
    the reason this is the higher-leverage of the two techniques for
    Ballistica's actual use case.

Both are safe to run ahead of a measurement (group size, hole count)
because neither invents detail that wasn't in at least one input frame
-- unlike a generative upscaler (Real-ESRGAN, Topaz, etc.), which is
deliberately NOT used here for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Sharpness:
    """Variance of the Laplacian -- a standard, cheap focus/clarity
    proxy: a crisp, detailed image has high local contrast everywhere,
    which shows up as high variance in its second derivative; a blurry
    one is smooth and scores low. Not a human-perceptual metric, but
    good enough to prove "before vs. after" moved the right direction."""
    score: float


def sharpness_score(frame) -> Sharpness:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return Sharpness(float(cv2.Laplacian(gray, cv2.CV_64F).var()))


def sharpen_denoise(frame, denoise_strength: float = 7.0, sharpen_amount: float = 1.5):
    """Single-frame cleanup: non-local-means denoise (removes sensor
    noise without smearing edges the way a plain blur would), then an
    unsharp mask (subtract a blurred copy, amplify the difference back
    in) to recover apparent edge contrast lost to the digiscoping
    setup's own optical softness. Order matters -- sharpening noise
    first would just amplify the noise."""
    import cv2

    denoised = cv2.fastNlMeansDenoisingColored(frame, None, denoise_strength, denoise_strength, 7, 21)
    blurred = cv2.GaussianBlur(denoised, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(denoised, 1 + sharpen_amount, blurred, -sharpen_amount, 0)
    return sharpened


@dataclass
class StackResult:
    stacked: "object"
    frames_used: int
    frames_dropped: int
    detail: str


def stack_frames(frames: list, min_frames: int = 3) -> StackResult:
    """Aligns a burst of photos of the same static scene (handheld/
    tripod micro-jitter between shots is assumed, not perfect
    registration) via ECC (Enhanced Correlation Coefficient) image
    alignment against the first frame, then averages them.

    Why this helps: random sensor noise partially cancels out across
    independent exposures (signal stays, noise shrinks roughly with
    sqrt(N) frames averaged) -- the same reason astrophotographers
    stack dozens of frames of a faint, noisy subject instead of taking
    one "good" shot. A static paper target on a stand-mounted rig is
    exactly this scenario: nothing in the scene moves between frames,
    only the camera's own hand-jitter and sensor noise do.

    A frame that fails to align (ECC doesn't converge -- e.g. someone
    bumped the rig mid-burst) is dropped rather than blended in
    misaligned, since a misaligned frame would blur the result instead
    of sharpening it."""
    import cv2
    import numpy as np

    if len(frames) < min_frames:
        raise ValueError(f"stack_frames needs at least {min_frames} frames, got {len(frames)}")

    reference = frames[0]
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32)
    accumulator = reference.astype(np.float32).copy()
    used = 1
    dropped = 0

    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    for frame in frames[1:]:
        warp_matrix = np.eye(2, 3, dtype=np.float32)
        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        try:
            _, warp_matrix = cv2.findTransformECC(
                ref_gray, frame_gray, warp_matrix, cv2.MOTION_EUCLIDEAN, criteria
            )
        except cv2.error:
            dropped += 1
            continue
        aligned = cv2.warpAffine(
            frame, warp_matrix, (reference.shape[1], reference.shape[0]),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )
        accumulator += aligned.astype(np.float32)
        used += 1

    stacked = (accumulator / used).astype(np.uint8)
    return StackResult(
        stacked, used, dropped,
        f"stacked {used}/{len(frames)} frames ({dropped} dropped for failed alignment)",
    )
