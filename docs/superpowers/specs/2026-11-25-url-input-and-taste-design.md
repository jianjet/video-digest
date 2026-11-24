# video-digest 3.0: links in, only new knowledge out (design, 2026-11-25)

## Why
The Brain (Obsidian vault in the `brain` GitHub repo) is meant to record everything the user knows. Today video-digest is built to
read whole Instagram creators every Sunday and judge tools as if for a business. The user wants instead: hand over a list of links
(Instagram reels now, YouTube and others later) whenever he likes, and get only **new** knowledge proposed to the Brain, approved
in Telegram, with the skill learning from his Approve / Reject choices.

## Goals
- `/video-digest <links or a file of links>` processes exactly those links, now.
- Any link yt-dlp can download works (YouTube, TikTok, ...), not only Instagram.
- A proposal only carries knowledge the Brain does not already hold. Repeats are skipped as noise, with no extra source line.
- Approve / Reject in Telegram is recorded and read on the next run: rejected kinds of topics rank lower (never dropped).
- Notes are in English, with the original term in brackets when it matters (a Chinese tool name, slang).
- The latest Brain lives on GitHub; Obsidian picking it up on its next pull is fine.

## Non-goals
- No Sunday task, no Chrome login (Firefox stays), no direct writes to the vault (n8n stays the only writer).
- No sandbox-VM testing of tools in this round (deadline 07:00): moved to HANDOFF "Next" with what exists (VMware VM
  `sandbox`, Ubuntu, snapshot `clean-base`, shared folders `Documents\ig-digest\inbox|outbox`, `vmrun.exe`).

## Anti-goals (failure even if it works)
- Getting the spare Instagram account banned: Instagram pacing and stop rules stay exactly as strict.
- Telegram flooded with things already in the Brain.
- Trading / investing notes that read as advice: they record the creator's claim, the proof shown (or "none shown") and hype
  flags, never "do this".

## Approaches considered
- **A (picked): one `run` command for any link.** Instagram links download through the existing Firefox-login path with its
  pacing and stop rules; every other link through yt-dlp without a login. After the download both share one pipeline and one
  output layout. One procedure in SKILL.md, one place to fix things.
- B: leave the Instagram script, route other links through `media_digest.py`'s "file on this computer" path. Less code now,
  but two output layouts and two procedures that drift apart.
- C: rewrite around a general source layer. Cleanest, but rewrites 1,400 tested lines right before the first real test.

## The design

### 1. Input and pipeline (`video_digest.py run`)
- `python video_digest.py run --urls <file or links> [--max 30]`: a file with one link per line (`#` comments) or links split
  by spaces/commas. Duplicates dropped. Each link gets an id `<site>/<id>` (instagram shortcode, YouTube video id, else
  yt-dlp's extractor + id).
- **Instagram:** Firefox cookie copy (`copy_login`), one download at a time, 3–8 s apart, exit 3 (login wall) / 4 (rate limit)
  stop every remaining Instagram link; other sites carry on. Reels via yt-dlp; a post yt-dlp cannot read as video (photo,
  carousel) falls back to gallery-dl as today.
- **Other sites:** yt-dlp, no cookies, one at a time.
- Caption, title, uploader and date come from yt-dlp's info JSON (no listing step any more).
- Then, as today: speech (`media_digest.py`, `ASR_ENGINE`), on-screen text (`screen_text.py`), key frames (`key_frames.py`),
  `may be unreliable` label, media deleted.
- **Long videos** (over 10 minutes): more key frames allowed (`MAX_FRAMES_LONG`, default 60) and on-screen text read every
  10 s instead of 2 s, so an hour of video stays an evening's CPU, not a night's. (Builder's call on exact numbers.)
- **Creator handles stay:** `list --handle H [--tab reels]` lists a creator's posts as today (pacing, stop rule, archive),
  and `run --handle H [--max 30]` feeds the listed, unread posts into the same pipeline as links.
- Output: `~/video-digest/<site>/<id>/` holding `transcript.md`, `frames/` (+ `index.csv`), `card.md`. `archive.sqlite3`
  remembers each id's status so a rerun of the same link is skipped unless `--again`.
- `drop-frames` deletes frames of items that have a card.

### 2. Judgement (Claude)
- Cards as today (Sonnet subagents, frames looked at), in English with original terms in brackets.
- The old use/park/skip and learn/later/no verdicts and `tools.csv` go. Per item a card names, Opus asks one question:
  **new to the Brain, or noise?** It checks `brain.py index` and reads the notes that touch the item.
  - Already there in substance → skipped as noise (listed in the run's digest, nothing sent).
  - Partly new → only the new detail is proposed, merged into the existing note.
  - New → proposed, into an existing note if one fits, else `Topics/<area>/<slug>.md`.
- Every item in a note carries a **worth trying?** line: `try now` / `later` / `just good to know` (trading and investing:
  always `just good to know`, never a recommendation).
- Tools stay knowledge: a new tool gets a web lookup (official page, licence, real cost), at most 20 per run.
- `digests/<date>.md` per run: proposed, skipped as already known, failed links.

### 3. Areas (`goals.md`)
- `goals.md` becomes "what the Brain is for": purpose (record all my knowledge) and areas: AI · 3D printing · Business tactics
  and mindset · Trading and investing. Areas map to folders under `Topics/`. A reel outside them may propose a new area.
- Claude writes the user's `~/video-digest/goals.md` from this conversation; `goals.example.md` in the repo gets the new shape.

### 4. Learning from Telegram choices
- n8n, on both Approve and Reject, appends one JSON line to `video-digest/choices.jsonl` in the `brain` repo:
  `{"at", "decision": "approve|reject", "path", "title", "area", "summary", "sources"}`. The whole approval workflow (as in
  the user's screenshot, plus the log nodes) is built on the n8n instance reachable over MCP; the user copies it into his own server
  and re-picks the Telegram and GitHub credentials. Its JSON is also saved as `n8n/approve-brain-note.json` in this repo.
- `brain.py index` (it already pulls the repo) also reads `choices.jsonl` and prints a per-area and per-topic tally.
- Claude orders a run's proposals by that tally: topics the user approved before first, topics with more rejects than approves last,
  their Telegram summary marked "you rejected similar before". Nothing is dropped automatically.

### 5. Removed
- `CONTRIBUTING.md` and every reference to it.
- Sunday mode: `weekly`, `check`, `creators.txt`, Task Scheduler and SessionStart sections, `weekly/` files.
- `playbook.md` leftovers in `goals.example.md`.

### Order of work (deadline 07:00; the last items slip first)
1. Installs and Firefox login · 2. links in (IG + YouTube), handles kept · 3. judging in SKILL.md · 4. n8n workflow ·
5. cleanup, docs, commit, push · 6. real test · 7. ranking from choices.

### 6. Delivery
- Commits straight to `main` with proper messages, pushed to GitHub (`jianjet/video-digest`).
- Docs updated: README, SETUP, HANDOFF, CHANGELOG (3.0.0), SKILL.md.
- Tests: offline suite updated for the new command and passing (`python -m unittest discover -s skills/video-digest/tests`).

### 7. First real test
- Installs on this PC, asked before running: yt-dlp, gallery-dl, rapidocr, ffmpeg; Firefox with the spare account logged in;
  ~1 GB speech models on first run.
- The user's own Instagram reel links plus one YouTube link, end to end to a Telegram proposal.

## Left to the builder
Exact flag names, the long-video thresholds, the `<site>/<id>` rule for unusual extractors, test structure.
