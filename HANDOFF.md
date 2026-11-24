# Handoff — video-digest (2026-11-25)

Read this first when picking the project up in a new Claude Code session.

## Done
- **One skill:** `skills/video-digest/` (ig-digest and media-digest by justyk99, merged and renamed; credits in README).
- **Settings in `.env`** at the repo root (template `.env.example`, kept out of git). `ASR_ENGINE=sensevoice|qwen3` switches speech-to-text.
- **Key frames** per reel (`key_frames.py`): every scene change plus one every few seconds, capped. Claude looks at them for the cards; `drop-frames` deletes them once the card exists.
- **Unreliable label:** a transcript with too few words a minute gets `may be unreliable` under the caption.
- **Brain** (`brain.py`): `index` lists the notes under `Topics/` in the Obsidian vault (a private git repo); `propose` checks each proposal and POSTs it to the n8n webhook with the secret header.
- **n8n approval workflow** ("video-digest – Approve Brain Note") on the personal n8n server: webhook → Telegram Approve/Reject → stale check → create or update the note on GitHub → Telegram confirmation. Tested end to end.
- Tests: `python -m unittest discover -s skills/video-digest/tests` (250, no network).

## How it fits together
reel → transcript + on-screen text + key frames → card → verdict against goals.md → Brain proposal (merge into an existing topic or a new topic) → n8n → Telegram Approve → commit to the Brain repo.

## Next
1. **D1:** first real run on 2 reels from a small creator, spare Instagram account logged into Firefox: `/video-digest <handle> --max 2`. Check the transcript, frames, card and that a Telegram Approve arrives.
2. Delete `Topics/test.md` from the Brain repo (left from the workflow test).
3. Retake the sandbox VM snapshot `clean-base` with copy/paste off.
4. About a week after the n8n upgrade works fine: delete the old n8n backups on the server.

## Later
- YouTube and long videos (a new "get the video" step; the rest is reused).
- Sandbox VM testing of tools with a `use` verdict.

## Rules
- Spare Instagram account only; never the main one.
- Never commit, paste or print `.env` (it holds the webhook secret).
- Only n8n writes to the Brain, after Approve. The skill never writes, commits or pushes in the vault.
- Shell commands for the user: Windows cmd, not PowerShell.
