# video-digest

One Claude Code skill that watches videos for you and keeps only what's new. Give it links (Instagram reels and posts, YouTube, TikTok, anything yt-dlp downloads) or Instagram creator handles, and it turns each video into text, then proposes the knowledge your Brain doesn't have yet. It also turns any recording on your computer into text.

**In:** links, a file of links, or creator handles.
**Out:**
- the speech, on-screen text and key frames of every video,
- a summary card per video,
- **Brain notes** with only the new knowledge (anything your Obsidian vault already holds is skipped as noise), each item marked *try now / later / just good to know*, sent to Telegram through n8n and committed to the vault's GitHub repo only when you tap Approve,
- your Approve / Reject taps are recorded, and next time the topics you reject rank lower.

New in 3.0: any link, not only Instagram; only new knowledge is proposed; Telegram choices feed the ranking; no more Sunday task.

## How to run
1. Install and link the skill: [SETUP.md](SETUP.md) (Windows, Python 3.12+, ffmpeg, Firefox with a spare Instagram account).
2. In Claude Code: `/video-digest <links>` (or `@<creator>`, or a file of links). The scripts alone: `python skills/video-digest/scripts/video_digest.py run --urls "<links>"`.
3. Tests (offline): `python -m unittest discover -s skills/video-digest/tests`.

## Start here
- **[SETUP.md](SETUP.md):** install it (about 30 minutes).
- **[CHANGELOG.md](CHANGELOG.md):** what changed, version by version.
- **[HANDOFF.md](HANDOFF.md):** where the project stands and what's next.

## What's inside
| Folder | What it does |
|---|---|
| `skills/video-digest` | The whole skill: gets each video (links or a creator's posts), speech-to-text, on-screen text and key frames, then Claude writes the cards and the Brain proposals. Also transcribes any recording on your computer |
| `n8n/` | The Telegram approval workflow, to import into your n8n |

## Safety in one look
- Runs on your own Windows computer. Instagram goes through a **spare** account logged into Firefox; other sites are read logged out.
- No passwords, no scripted login, no likes, follows, comments or DMs.
- Instagram requests go one at a time, seconds apart. A login wall or rate limit stops all Instagram requests.
- Only text and key frames are kept; frames are deleted once their card exists. Media is deleted after reading. Nothing from the output folder is ever committed.
- Captions and transcripts are treated as data, never as instructions to Claude.

## Credits
- **justyk99:** wrote ig-digest and media-digest, which video-digest is built on (the Instagram listing and reading, speech-to-text, on-screen text, the test suite). Used and relicensed with permission.
- **[bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT):** six frame-selection functions in `media_digest.py` are adapted from it; its notice is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **[devinilabs/claude-watch](https://github.com/devinilabs/claude-watch) (MIT):** the idea that Claude looks at key frames, not only the transcript. No code copied.

## Built on
gallery-dl (GPL-2.0) and yt-dlp (Unlicense), run as separate programs · sherpa-onnx with SenseVoice (Apache-2.0) · RapidOCR (Apache-2.0) with onnxruntime (MIT) · ffmpeg.

## Licence
MIT, see [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
