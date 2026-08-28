"""One-off generator for the four in-app audio walkthrough MP3s.

Run manually whenever the walkthrough script text changes (ballistica/
walkthrough.py) -- not run automatically at deploy time, and not called
per-playback. This content is fixed and narrator-neutral across every
user; generating it live via /voice/speak on every play would just be a
paid API call for identical output, unlike a real ballistic solution
which is genuinely different every time.

Usage:
    python -m scripts.generate_walkthrough_audio

Requires OPENAI_API_KEY (same .env as the rest of the app). Writes
directly into ballistica/web/audio/, served by api.py's /audio mount.
Uses the same TTS model/voice/speed as /voice/speak's own default
(tts-1, shimmer, 0.9) -- this content should sound like it's coming from
the same narrator as live solutions, not a different voice.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# openai_client.get_openai_client() expects .env to already be loaded by
# its caller (matches api.py/cli.py's own pattern) -- it doesn't load it
# itself, so a standalone script using it directly has to do this first.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from ballistica.openai_client import get_openai_client
from ballistica.walkthrough import WALKTHROUGH_SECTIONS

_OUT_DIR = Path(__file__).resolve().parent.parent / "ballistica" / "web" / "audio"
_MODEL = "tts-1"
_VOICE = "shimmer"
_SPEED = 0.9  # matches /voice/speak's default -- see api.py's VoiceSpeakIn


def main() -> None:
    client = get_openai_client()
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    for section in WALKTHROUGH_SECTIONS:
        out_path = _OUT_DIR / f"{section.key}.mp3"
        print(f"Generating {out_path.name} ({len(section.narration_text)} chars)...")
        result = client.audio.speech.create(
            model=_MODEL, voice=_VOICE, input=section.narration_text, speed=_SPEED,
        )
        out_path.write_bytes(result.content)
        print(f"  wrote {out_path} ({out_path.stat().st_size} bytes)")
    print("Done.")


if __name__ == "__main__":
    main()
