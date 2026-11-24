# video-digest 3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Links (Instagram, YouTube, any yt-dlp site) in → transcripts/frames → only new knowledge proposed to the Brain, with Telegram choices logged and used for ranking.

**Architecture:** `video_digest.py` gains `run --urls`, which groups links by folder and feeds each group through the existing
`cmd_transcribe` (now link-aware: optional URLs, optional cookies, info-json metadata). Sunday mode is deleted. `brain.py index`
adds a tally of `video-digest/choices/*.json` from the Brain repo. SKILL.md's judging becomes "new or noise" + "worth trying?".

**Tech Stack:** Python 3.13 stdlib, yt-dlp, gallery-dl, RapidOCR, sherpa-onnx, ffmpeg, n8n.

**Spec:** `docs/superpowers/specs/2026-11-25-url-input-and-taste-design.md`

## Global Constraints
- Instagram requests one at a time, 3–8 s apart; exit 3 login wall, exit 4 rate limit stop every remaining Instagram link.
- Firefox login only, read from a copy of the cookie files; never a password.
- Nothing from `~/video-digest` and no `.env` is ever committed or printed.
- Offline tests: `python -m unittest discover -s skills/video-digest/tests` must end `OK`.
- Commits straight to `main`, pushed to `origin`.

## Builder's calls (from the spec's "left to the builder")
- **Layout:** keep today's per-folder layout, `<folder>/transcripts/<id>.md`, `<folder>/frames/<id>/`, `<folder>/cards/<id>.md`.
  `<folder>` is the creator handle for `transcribe --handle`, `instagram` for loose Instagram links, the site name
  (`youtube`, `tiktok`, else `web`) for other links. One layout for everything; no rewrite of 200 tested lines.
- **Ids:** Instagram shortcode; YouTube 11-char id from `v=`, `youtu.be/`, `shorts/`, `live/`; TikTok `/video/<digits>`;
  else `u` + first 10 hex of sha1(url).
- **Choices log:** one file per decision, `video-digest/choices/<id>.json`, created by n8n (no append race, no missing-file branch).
- **Long videos:** over 600 s of audio: key-frame cap `MAX_FRAMES_LONG` (default 60), on-screen text every 10 s instead of 2 s.
- `run --handle` from the spec stays `transcribe --handle H --all` (already exists).

## Review Focus
1. A YouTube link that needs sign-in ("confirm you're not a bot"): must stop that link with its message, not claim an Instagram wall for other links.
2. The same link twice in one list, or rerun next day: processed once; a rerun skips it.
3. An Instagram `/p/` photo post given as a link: falls back to gallery-dl, as listing-typed posts do.
4. A link no extractor knows: fails that link only, the rest carry on.
5. `choices/` missing or holding a broken file: `index` still works, the bad file is named in `choices_warnings`.

---

### Task 1: Delete Sunday mode
**Files:** Modify `skills/video-digest/scripts/video_digest.py` (remove `WEEKLY_*`, `CREATOR_PAUSE`, `STATUS*`, `LIST_RE`,
`REQUESTS_RE`, `HEADLINE`, `FIX`, `LogSink`, `LOG_SINK`, and `parse_status` … `cmd_check` except `one_at_a_time` and
`cmd_drop_frames`; `weekly`/`check` subparsers); Test `tests/test_pipeline.py` (remove `Weekly`, `Check`).
- [ ] Remove; `log()` keeps only the print.
- [ ] Run suite → OK. Commit `refactor: remove the Sunday task and the session-start check`.

### Task 2: Links in
**Files:** Modify `video_digest.py`; Test `tests/test_pipeline.py` (new class `Links`).
**Produces:**
- `link_id(url: str) -> tuple[str, str]` → `(folder, id)`: `("instagram", "ABC123")`, `("youtube", "dQw4w9WgXcQ")`, `("tiktok", "7301…")`, `("web", "u<10 hex>")`.
- `parse_links(value: str) -> list[tuple[str, str, str]]` → `(folder, id, url)`, file or text, `#` comments, duplicates by id dropped, a token that is not `http(s)://…` raises `ValueError`.
- `cmd_run(value: str, limit: int, root=None) -> int`: Instagram group first (needs login), then other groups; worst exit code wins, but exit 3/4 from Instagram never stops other sites.
- `cmd_transcribe(handle, picks_arg, limit, root=None, urls=None, login=True)`: `urls` = `{id: url}` overrides index URLs and is the pick list; `login=False` skips `copy_login` and passes no cookies.
- `download_audio(..., cookies)` / `download_post(...)`: `cookies=None` → no `--cookies-from-browser`; yt-dlp also `--write-info-json`.
- `info_meta(folder: Path, code: str) -> dict` → `{"url","date","caption"}` from `<code>.info.json` (`webpage_url`, `upload_date` as `YYYY-MM-DD`, `title` + " · " + `description`, one line, 300 chars); `{}` when missing.
- [ ] Tests: `link_id` for the 4 kinds above and `https://youtu.be/dQw4w9WgXcQ?si=x`; `parse_links` drops a duplicate and rejects `hello`; a fake-tool run of 1 IG + 1 YouTube link writes `instagram/transcripts/<code>.md` and `youtube/transcripts/<id>.md`, and the YouTube call has no `--cookies-from-browser`; a rerun skips both.
- [ ] Implement. Long-video: `keep_key_frames` uses `MAX_FRAMES_LONG` when total duration > 600 s; `screen_text.py` takes `--every N` and video_digest passes 10 for long items.
- [ ] Suite OK. Commit `feat: run any list of links (Instagram, YouTube, other yt-dlp sites)`.

### Task 3: Choices tally in `brain.py index`
**Files:** Modify `skills/video-digest/scripts/brain.py`; Test `tests/test_video_digest.py` (`Brain`).
**Produces:** `choices(repo: Path) -> dict` → `{"areas": {area: {"approve": n, "reject": n}}, "notes": {path: {...}}, "warnings": [..]}`; area = path part after `Topics/`, or `(new)` . `cmd_index` output gains `"choices"`.
- [ ] Test: two approve + one reject files under a temp repo's `video-digest/choices/`, plus one broken file → counts right, one warning.
- [ ] Implement; suite OK; commit `feat: brain.py index tallies Telegram approve/reject choices`.

### Task 4: SKILL.md, goals, docs
- [ ] SKILL.md: new procedure (links → `run`; handles → `list` + `transcribe --all`), judging = new or noise against the Brain, worth-trying line, English + original term in brackets, trading = claims only, ranking from `choices`, remove weekly, verdicts, `tools.csv`.
- [ ] `goals.example.md` new shape; write `~/video-digest/goals.md` for the user (areas: AI · 3D printing · Business tactics and mindset · Trading and investing).
- [ ] Delete `CONTRIBUTING.md`; update README, SETUP, HANDOFF (VM → Next), CHANGELOG 3.0.0. Commit `docs: …`; push.

### Task 5: n8n workflow
- [ ] Build "video-digest – Approve Brain Note" on the MCP n8n instance: today's nodes + `Log Approve` / `Log Reject` (GitHub create `video-digest/choices/<id>.json`). Save JSON to `n8n/approve-brain-note.json`. Commit, push.

### Task 6: Real test
- [ ] The user's links + one YouTube link → transcripts, frames, cards, proposals `--dry-run`, then send.
