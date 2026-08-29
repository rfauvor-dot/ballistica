"""Run this AFTER connecting this machine's WiFi directly to the GoPro's
own hotspot (camera on, in video/photo mode -- not already recording;
its WiFi turned on; this computer joined to the camera's network by
name, same as joining any other WiFi network). Joining that network
replaces this machine's normal internet connection for as long as it's
connected -- that's how the camera's own hotspot works, not something
this script can avoid.

Usage:
    python -m scripts.test_scope_stream                 # HERO4-7 (legacy API)
    python -m scripts.test_scope_stream --generation open_gopro   # HERO9+

What it proves, concretely: not just "the command was accepted," but
that real video frames are actually arriving over the local network --
and saves one as a JPEG (scope_stream_sample.jpg) so there's an actual
photo to look at, not just a log line claiming success.
"""
from __future__ import annotations

import argparse
import sys

from ballistica.scope_stream import save_sample_frame, start_preview_stream, verify_stream


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generation", choices=["legacy", "open_gopro"], default="legacy",
        help="'legacy' for HERO4-7 (default -- matches Rick's HERO4), 'open_gopro' for HERO9+.",
    )
    args = parser.parse_args()

    print(f"Sending start-stream command ({args.generation} API)...")
    if not start_preview_stream(args.generation):
        print("FAILED: the camera did not accept the start-stream command.")
        print("Check: is this machine actually connected to the GoPro's own WiFi network right now?")
        sys.exit(1)
    print("Camera accepted the command. Opening the video stream...")

    result = verify_stream(duration_s=5.0)
    print(result.detail)
    if not result.ok:
        sys.exit(1)

    print("Saving one real frame as scope_stream_sample.jpg...")
    if save_sample_frame("scope_stream_sample.jpg"):
        print("Done -- open scope_stream_sample.jpg to see exactly what the camera is seeing right now.")
    else:
        print("Stream verified, but saving a sample frame failed -- not fatal, the pipeline itself works.")


if __name__ == "__main__":
    main()
