# Changelog

One line per change, newest first. Pull requests add their line under **Unreleased**.

## Unreleased

## 3.0.0 (2026-11-25): links in, only new knowledge out
- `video_digest.py run --urls <file or links>`: Instagram, YouTube, TikTok or any yt-dlp link, through the same pipeline. Instagram keeps the Firefox login and its stop rules; other sites are read logged out and a refusal fails that link only. Creator handles still work.
- Videos over 10 minutes keep up to `MAX_FRAMES_LONG` (60) key frames and read on-screen text every 10 s.
- Judging is now "new or noise" against the Brain: only knowledge the Brain does not hold is proposed, each item marked try now / later / just good to know. Notes in English with the original term in brackets. Trading and investing are recorded as claims, never advice. The use/park/skip verdicts and `tools.csv` are gone.
- `brain.py index` tallies the Approve / Reject choices n8n saves in `video-digest/choices/`; rejected topics rank lower.
- `goals.md` now says what the Brain is for and its areas.
- Removed: the Sunday task, the session-start check, `creators.txt`, CONTRIBUTING.md.

## 2.0.0 (2026-11-25): video-digest
- ig-digest and media-digest merged into one skill, `skills/video-digest`; `ig_digest.py` renamed `video_digest.py`. Built on justyk99's ig-digest 1.4 and media-digest 1.1.
- Renamed the project video-digest; output folder `~/video-digest` (`VIDEO_DIGEST_HOME`; `IG_DIGEST_HOME` still works).
- Settings in `.env` (`vd_config.py`, template `.env.example`); `ASR_ENGINE` switches speech-to-text between sensevoice and qwen3.
- Key frames per reel (`key_frames.py`): scene changes plus one every few seconds, capped; Claude looks at them for the cards. `drop-frames` deletes them once the card exists.
- A transcript with too few words a minute is labelled `may be unreliable`.
- `brain.py`: `index` lists the Brain's notes, `propose` sends merge or new-topic proposals to an n8n webhook for Telegram approval. Replaces `playbook.md`.

## 1.4.0 (2026-11-22)
- First shared version. Goals now live in `~/ig-digest/goals.md` (see `goals.example.md`), not in SKILL.md.
- Output folder defaults to `~/ig-digest` (`IG_DIGEST_HOME` still overrides it).
- media-digest ships in the same repo, because ig-digest uses it for speech-to-text.
- Added README, SETUP, CONTRIBUTING, this changelog and a pull request template.

## 1.3.0 (2026-11-10)
- Judges methods and apps, not only installable tools. Keeps a learning guide, `playbook.md`.

## 1.2.0
- Reads every post: speech plus on-screen text, photos and carousels, and the reels tab.
- Weekly mode: Task Scheduler runs the listing and reading on Sundays; a session-start line reports what is new or wrong.

## 1.1.0
- Reads every reel (no more picking by caption).

## 1.0.0
- Instagram creators to post lists to transcripts to verdicts. Stops at the first login wall or rate limit.
