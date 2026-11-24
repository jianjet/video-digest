---
name: media-digest
description: Turn any recording — a webinar, a WhatsApp voice note, a meeting, a phone video of a talk, a YouTube download — into text Claude can read and judge, offline, on the laptop CPU — transcript with timestamps (Chinese, English, Cantonese, Japanese, Korean, mixed), a frame wherever the screen changed (up to 100, near-duplicates dropped, times in frames/index.csv), and an eight-section summary (structure with times, every number and claim, demo steps, questions and whether they were answered, objections, the offer, arithmetic and source checks, what it means for us). Use when a user drops an mp4/mp3/ogg/m4a/wav, says "transcribe", "summarise this recording / voice note / webinar", "what did they say", or wants knowledge pulled out of a video for a brain or a competitor file. Nothing leaves the machine; the first run downloads ~1 GB of models from GitHub once.
when_to_use: /media-digest <file or folder> [--frames scene|every|off] [--max-frames 100] [--at 4:32,7:10] [--lang auto|zh|en|yue|ja|ko] · a recording dropped into the chat · "summarise the recording"
argument-hint: "<file or folder> [--frames scene|every|off] [--at T1,T2] [--lang auto]"
allowed-tools: Read, Write, Edit, Glob, Bash(python *), Bash(python3 *), Bash(ls *), Bash(dir *), Agent
---

# media-digest — recordings in, knowledge out (v1.1, 2026-11-09: scene frames, cue frames)

Script: `scripts/media_digest.py` beside this file (ffmpeg → 16 kHz wav → Silero VAD → SenseVoice int8 on CPU → `transcript.md`; `ffmpeg` scene-change frames, near-duplicates dropped, capped → `frames/` + `frames/index.csv`; `DIGEST.md` with the summary outline). Verified 21 Oct 2026 on a 54-minute Chinese webinar (RTX 4050 laptop, CPU only: both recordings transcribed in about 4 minutes) and on files whose names contain `%`.

## Procedure
1. **Find the files.** `$ARGUMENTS` is a file or a folder; a folder means every mp4/mkv/webm/mov/mp3/m4a/ogg/opus/wav/flac in it. If the user dropped a file into the chat, use that path.
2. **Run the script** from this skill's folder (never copy it; call it by path):
   `python <this folder>/scripts/media_digest.py "<file>" [--frames scene|every|off] [--max-frames 100] [--at 4:32,7:10] [--lang auto]`
   First run: it pip-installs `sherpa-onnx` and `numpy`, uses `ffmpeg` from PATH or installs `imageio-ffmpeg`, and downloads the models to `~/.cache/media-digest/models` (~1 GB, once). Tell the user that before the first run; after that nothing is downloaded. A 1-hour recording takes 5–10 minutes on a laptop CPU.
   Frames (video only): `--frames scene` (default) keeps the frame where the picture changed, drops near-duplicates (`--no-dedup` keeps them) and thins to `--max-frames 100` (first and last kept); a video that barely changes (6 cuts or fewer) gets one frame every `--frames-every 30` s instead. `--frames every --frames-every 30` is the old fixed-step output; `--frames off` cuts none. `frames/index.csv` (`file,seconds,time,reason`) gives every frame's time; times past an hour read `1:05:30`. If the frame step fails, the transcript is still made, `DIGEST.md` says why and the exit code is 1; rerun with `--frames off`.
   Cue frames: `--at 4:32,7:10` also cuts the frame at those moments (`frames/cue_000432.jpg`), never counted against the cap; a time past the end is listed in `DIGEST.md` and gets no file. To look at a moment the transcript cites, add `--frames-only` (no transcript, no models, takes seconds): `python <this folder>/scripts/media_digest.py "<file>" --frames-only --at 12:05` adds `frames/cue_001205.jpg` to the existing `_digest` folder and leaves everything else alone. `--frames-only` needs frames to cut: `--frames scene|every` or `--at`.
   `--engine sensevoice|qwen3`: the default stays `sensevoice`; `qwen3` (Qwen3-ASR-0.6B int8) needs its model folder `sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25` in the models folder first, is never downloaded by the script, and stops with the folder and download link when missing.
3. **Read `DIGEST.md`** in the new `<name>_digest/` folder. It tells you what exists and holds the outline.
4. **Frames → `frames-notes.md`.** Delegate to a cheaper model (`Agent`, mechanical): one line per JPEG — file, time taken from `frames/index.csv` (frames are not evenly spaced; never compute a time from the number), what the screen shows, readable text and numbers, visible chat lines with timestamps. Split the folder across 2–4 agents when it holds more than 60 frames; `DIGEST.md` says when the cap bit (then raise `--max-frames` or use `--at`); tell them "keep thinking short, transcribe what you see".
5. **Transcript → judgement (you, not an agent).** Read `transcript.md`. English terms inside Chinese speech are often garbled ("reD max" = 3ds Max); names are unreliable; infer from context and flag what you inferred.
6. **Write `summary.md`** with the eight sections from `DIGEST.md`, in order. Facts only; every number as stated; mark "no source on screen" where it applies; do the arithmetic yourself and say when it does not add up; list every question and whether it was answered with a quote.
7. **Report in chat in ≤ 12 lines**: duration, gaps, the five facts that matter, where the files are. Offer to file the knowledge where the user keeps it (brain, competitor page, project doc) — write it only when asked or when the user's brain rules already say to.

## Rules
- Never upload the recording anywhere; the whole pipeline is local. Do not install `faster-whisper` or CUDA packages: SenseVoice on CPU is enough and ten times faster to set up.
- Write only inside `<name>_digest/`; never rename or delete the recording.
- Two recordings of one event (laptop + phone) → digest both, then one summary; state the gap between them in minutes.
- A recording with no speech (music, silence) → say so after the run; do not invent a transcript.
- Privacy: a voice note from a named person stays summarised, never quoted at length in a shared file; no phone numbers, unit numbers or addresses copied into any file.

## When the recording is on another machine (the user's laptop, the chat is in the cloud)
Files over ~2 MB do not cross a slow laptop link. Have the laptop session run step 2 and send only the text: paste into a laptop Claude Code session
`Run ~/.claude/skills/media-digest/scripts/media_digest.py on <file>; then follow the skill's steps 4–6 and write summary.md beside it.`
Then pull `summary.md`, `frames-notes.md` and `transcript.md` (all small) through the device link.
