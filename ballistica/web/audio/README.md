# Walkthrough audio

Four generated Shimmer-voice walkthrough MP3s, narrated from the exact
text in `Ballistica_Audio_Walkthrough_Script.docx` (verbatim, via
`ballistica/walkthrough.py`):

- `walkthrough-1-getting-started.mp3`
- `walkthrough-2-rifle-setup.mp3`
- `walkthrough-3-load-and-velocity.mp3`
- `walkthrough-4-long-range-and-spotting.mp3`

Generated 2026-08-28 via `python -m scripts.generate_walkthrough_audio`
(OpenAI `tts-1`, voice `shimmer`, speed `0.9` -- same as `/voice/speak`'s
own default, so this sounds like the same narrator as live solutions).
Re-run that script (after editing `ballistica/walkthrough.py`) any time
the script text changes -- it overwrites these files in place.

Do not commit placeholder or fabricated narration into this directory —
these files should only ever contain audio generated from Rick's actual
finalized script text.
