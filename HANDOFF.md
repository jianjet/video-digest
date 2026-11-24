# Handoff — video-digest (2026-11-25, v3.0)

Read this first when picking the project up in a new Claude Code session.

## Done
- **One skill:** `skills/video-digest/` (ig-digest and media-digest by justyk99, merged; credits in README).
- **Links in (3.0):** `video_digest.py run --urls <file or links>` reads Instagram, YouTube, TikTok or any yt-dlp link. Instagram keeps the Firefox login, pacing and stop rules; other sites are read logged out. Creator handles still work (`list` + `transcribe --all`).
- **Only new knowledge:** SKILL.md judges each item "new or noise" against the Brain; new items get *try now / later / just good to know*. English notes, original term in brackets. Trading = claims, never advice.
- **Choices:** n8n saves every Approve / Reject as `video-digest/choices/<id>.json` in the Brain repo; `brain.py index` tallies them and rejected topics rank lower.
- **goals.md** (`~/video-digest/goals.md`): purpose + areas `ai`, `3d-printing`, `business`, `trading-investing`.
- **Settings in `.env`** at the repo root (template `.env.example`, kept out of git). `ASR_ENGINE=sensevoice|qwen3`; `MAX_FRAMES_LONG` for videos over 10 minutes.
- **Removed in 3.0:** the Sunday task, the session-start check, `creators.txt`, CONTRIBUTING.md, use/park/skip verdicts, `tools.csv`.
- **Installed on the owner's PC (2026-11-25):** ffmpeg (winget, on the user PATH: open a new terminal), yt-dlp, gallery-dl, rapidocr, sherpa-onnx, SenseVoice models.
- Tests: `python -m unittest discover -s skills/video-digest/tests` (no network).

## How it fits together
link or creator → transcript + on-screen text + key frames → card → new or noise against the Brain (+ past choices) → proposal → n8n → Telegram Approve / Reject → note committed to the Brain repo, choice saved in `video-digest/choices/`.

## First real run (2026-11-25)
Two public Instagram reels → transcripts, on-screen text, frames → cards → two `create` proposals (`Topics/ai/claude-code-skills.md`, `Topics/ai/claude-features.md`) → both arrived in Telegram. Found and fixed on the way: rapidocr needs `onnxruntime`. The approval workflow with the choices log runs on the owner's n8n server (Telegram chat id set).

## Next
1. After the first Approve / Reject: check the note and `video-digest/choices/*.json` landed in the brain repo, then that `brain.py index` shows the tally.
2. YouTube test (`run --urls` with a YouTube link). Transcript titles for links show `(@instagram)` instead of the creator: cosmetic fix.
3. Delete `Topics/test.md` from the Brain repo (left from the workflow test).
4. **Sandbox VM testing of `try now` tools** (moved out of 3.0 for the deadline). What exists: VMware Workstation VM `sandbox` (`Documents\Virtual Machines\sandbox`, Ubuntu, 4 vCPU, 8 GB, NAT), snapshot `clean-base`, copy/paste off, shared folders `Documents\ig-digest\inbox` and `outbox` (rename to video-digest), `vmrun.exe` in `C:\Program Files\VMware\VMware Workstation`. Open question for the owner: what a test should prove (installs and runs? does what the video claimed? not malware?) and whether it runs by itself for every `try now` tool. Retake `clean-base` with copy/paste off.
5. About a week after the n8n upgrade works fine: delete the old n8n backups on the server.

## Rules
- Spare Instagram account only; never the main one.
- Never commit, paste or print `.env` (it holds the webhook secret).
- Only n8n writes to the Brain, after Approve. The skill never writes, commits or pushes in the vault.
- Commit straight to `main` and push, with proper messages (owner's call, 2026-11-25).
- Shell commands for the user: Windows cmd, not PowerShell.
