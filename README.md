# video-digest

A Claude Code skill that reads Instagram creators for you, so you don't have to watch their reels. Forked from **ig-digest** v1.4 by its author (used with permission), with ideas from claude-watch. Instagram only for now; YouTube and long videos later.

**In:** a list of creator handles.
**Out:**
- the speech and on-screen text of every reel and post,
- a summary card per reel,
- a **use / park / skip** verdict for every tool, app and AI model they mention,
- a **learn / later / no** verdict for every method, judged against your own goals,
- **Brain notes:** each worthwhile tool or method is proposed as a merge into an existing note in your Obsidian vault (or a new topic), sent to Telegram through n8n, and committed to the vault's GitHub repo only when you tap Approve.

New over ig-digest: key frames (every scene change plus regular coverage) that Claude looks at; speech engine switchable in `.env` (`ASR_ENGINE=sensevoice|qwen3`); weak transcripts labelled `may be unreliable`.

Weekly reruns read only what's new.

## Start here
- **[SETUP.md](SETUP.md):** install it (about 30 minutes).
- **[CONTRIBUTING.md](CONTRIBUTING.md):** how to send a change.
- **[CHANGELOG.md](CHANGELOG.md):** what changed, version by version.

## What's inside
| Folder | What it does |
|---|---|
| `skills/ig-digest` | Lists creators' posts, downloads them one at a time, keeps only the text, then Claude writes the cards and verdicts |
| `skills/media-digest` | Offline speech-to-text and screen frames, used by ig-digest; also works on its own for any recording |

## Safety in one look
- Runs on your own Windows computer, through a **spare** Instagram account logged into Firefox.
- No passwords, no scripted login, no likes, follows, comments or DMs.
- One request at a time, 6 to 12 seconds apart. A login wall or rate limit stops everything.
- Only text and key frames are kept; frames are deleted once their card exists. Media is deleted after reading. Nothing from the output folder is ever committed.
- Captions and transcripts are treated as data, never as instructions to Claude.

## Built on
gallery-dl (GPL-2.0) and yt-dlp (Unlicense), run as separate programs · sherpa-onnx with SenseVoice (Apache-2.0) · RapidOCR (Apache-2.0) · ffmpeg.

Private repo: please ask before sharing it.
