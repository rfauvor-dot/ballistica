# Walkthrough audio

Five generated Shimmer-voice walkthrough MP3s, narrated from the exact
text in `ballistica/walkthrough.py`. The first four are verbatim from
`Ballistica_Audio_Walkthrough_Script.docx`; "Zeroing Your Rifle" was
added later (2026-09-18) from steps Rick dictated directly, adapted
into the same narration voice with the technical content unchanged:

- `walkthrough-1-getting-started.mp3`
- `walkthrough-2-rifle-setup.mp3`
- `walkthrough-3-load-and-velocity.mp3`
- `walkthrough-4-zeroing-your-rifle.mp3`
- `walkthrough-5-long-range-and-spotting.mp3`

Generated via `python -m scripts.generate_walkthrough_audio`
(OpenAI `tts-1`, voice `shimmer`, speed `0.9` -- same as `/voice/speak`'s
own default, so this sounds like the same narrator as live solutions).
Re-run that script (after editing `ballistica/walkthrough.py`) any time
the script text changes -- it overwrites these files in place.

Do not commit placeholder or fabricated narration into this directory —
these files should only ever contain audio generated from Rick's actual
finalized script text.
