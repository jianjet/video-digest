# Setup (about 30 minutes)

**Shortcut:** do step 2 (accept the invite, clone), open Claude Code in the repo folder, and say:
"Read SETUP.md and set me up step by step. Ask me before every install."

## 1. What you need
- **Windows 10 or 11.** Mac and Linux aren't supported yet (the Firefox login path is Windows only). That's a good first pull request.
- **Python 3.12 or newer** (tested on 3.13). Check: `python --version`.
- **Git** and **Claude Code**.
- **Firefox.**
- **ffmpeg** on PATH: `winget install Gyan.FFmpeg`, then open a new terminal. Check: `ffmpeg -version`.
- **A spare Instagram account.** Never your main one: Instagram can ban an account that reads a lot, however slowly.

## 2. Get the code
1. Accept the GitHub invite (email, or github.com/notifications).
2. Clone the repo, for example into `%USERPROFILE%\Documents\video-digest` (cmd):
   ```
   git clone https://github.com/jianjet/video-digest %USERPROFILE%\Documents\video-digest
   ```

## 3. Install
```
python -m pip install -r %USERPROFILE%\Documents\video-digest\requirements.txt
```
Then link the skill into Claude Code. A junction (a folder shortcut) needs no admin rights, and your edits in the repo work straight away:
```
mklink /J "%USERPROFILE%\.claude\skills\video-digest" "%USERPROFILE%\Documents\video-digest\skills\video-digest"
```

## 4. Check it works (no internet needed)
```
cd %USERPROFILE%\Documents\video-digest
python -m unittest discover -s skills/video-digest/tests
```
It should end with `OK`.

## 5. Settings
Copy `.env.example` to `.env` in the same folder (the repo root) and fill it in. `.gitignore` keeps it out of git. `IGDIGEST_SECRET` must match the Header Auth credential on the n8n webhook. Never commit or paste this file.

## 5b. Log in
Open Firefox and log into Instagram with the **spare** account. That's all. The script reads a copy of Firefox's login. It never asks for a password and never logs in by itself.

## 5c. The Telegram approval (n8n)
Import `n8n/approve-brain-note.json` into your n8n (Workflows → Import from file), then on each Telegram and GitHub node pick your own credentials, and on the webhook node your Header Auth credential (`X-IGD-Secret`, the same value as `IGDIGEST_SECRET`). Set the GitHub owner and repo to your Brain repo and the Telegram chat id to yours. Activate it. Every Approve or Reject is saved as `video-digest/choices/<id>.json` in the Brain repo; that's how later runs learn what you like.

## 6. First run
In Claude Code, from any folder:
```text
/video-digest https://www.instagram.com/reel/<code>/ https://youtu.be/<id>
/video-digest @<creator handle>
```
- On the first run Claude asks what your Brain is for and writes `~/video-digest/goals.md` (the shape is in `goals.example.md`).
- The first speech-to-text downloads about 1 GB of models once.
- Everything it makes goes to `~/video-digest/`. Set `VIDEO_DIGEST_HOME` to use another folder.

## Rules that keep the account alive
- Spare account only. No scripted login.
- Never like, follow, comment or DM from it.
- A login wall or rate limit (exit 3 or 4) stops all Instagram requests. Wait; don't retry the same day.

## Stuck?
- `ffmpeg is not on PATH`: open a new terminal after installing it.
- `no Instagram login in Firefox`: log in with the spare account in Firefox, then rerun.
- Listing or download suddenly fails for everyone: update the tools, `python -m pip install -U yt-dlp gallery-dl`.
