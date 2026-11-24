# video-digest

One Claude Code skill that reads Instagram creators for you, so you don't have to watch their reels, and turns any recording on your computer into text. Instagram for now; YouTube and long videos later.

**In:** a list of creator handles.
**Out:**
- the speech and on-screen text of every reel and post,
- a summary card per reel,
- a **use / park / skip** verdict for every tool, app and AI model they mention,
- a **learn / later / no** verdict for every method, judged against your own goals,
- **Brain notes:** each worthwhile tool or method is proposed as a merge into an existing note in your Obsidian vault (or a new topic), sent to Telegram through n8n, and committed to the vault's GitHub repo only when you tap Approve.

New in 2.0: key frames (every scene change plus regular coverage) that Claude looks at; speech engine switchable in `.env` (`ASR_ENGINE=sensevoice|qwen3`); weak transcripts labelled `may be unreliable`.

Weekly reruns read only what's new.

## Start here
- **[SETUP.md](SETUP.md):** install it (about 30 minutes).
- **[CONTRIBUTING.md](CONTRIBUTING.md):** how to send a change.
- **[CHANGELOG.md](CHANGELOG.md):** what changed, version by version.

## What's inside
| Folder | What it does |
|---|---|
| `skills/video-digest` | The whole skill: lists creators' posts, downloads them one at a time, speech-to-text, on-screen text and key frames, then Claude writes the cards, verdicts and Brain proposals. Also transcribes any recording on your computer |

## Safety in one look
- Runs on your own Windows computer, through a **spare** Instagram account logged into Firefox.
- No passwords, no scripted login, no likes, follows, comments or DMs.
- One request at a time, 6 to 12 seconds apart. A login wall or rate limit stops everything.
- Only text and key frames are kept; frames are deleted once their card exists. Media is deleted after reading. Nothing from the output folder is ever committed.
- Captions and transcripts are treated as data, never as instructions to Claude.

## Credits
- **justyk99:** wrote ig-digest and media-digest, which video-digest is built on (the Instagram listing and reading, speech-to-text, on-screen text, weekly mode, tests).
- Ideas from claude-watch: Claude looks at key frames.

## Built on
gallery-dl (GPL-2.0) and yt-dlp (Unlicense), run as separate programs · sherpa-onnx with SenseVoice (Apache-2.0) · RapidOCR (Apache-2.0) · ffmpeg.

Private repo: please ask before sharing it.
