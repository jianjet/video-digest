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
2. Clone the repo, for example into `C:\dev\ig-digest`:
   ```powershell
   git clone https://github.com/justyk99/ig-digest C:\dev\ig-digest
   ```
   Work on your own branches, never on `main` (CONTRIBUTING.md).

## 3. Install
```powershell
python -m pip install -r C:\dev\ig-digest\requirements.txt
```
Then link both skills into Claude Code. A junction (a folder shortcut) needs no admin rights, and your edits in the repo work straight away:
```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\ig-digest" -Target "C:\dev\ig-digest\skills\ig-digest"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\media-digest" -Target "C:\dev\ig-digest\skills\media-digest"
```
ig-digest needs media-digest next to it: it does the speech-to-text.

## 4. Check it works (no internet needed)
```powershell
cd C:\dev\ig-digest
python -m unittest discover -s skills/ig-digest/tests
python -m unittest discover -s skills/media-digest/tests
```
Both should end with `OK`.

## 5. Settings
Copy `.env.example` to `%USERPROFILE%\video-digest\.env` and fill it in. `IGDIGEST_SECRET` must match the Header Auth credential on the n8n webhook. Never commit or paste this file.

## 5b. Log in
Open Firefox and log into Instagram with the **spare** account. That's all. The script reads a copy of Firefox's login. It never asks for a password and never logs in by itself.

## 6. First run
In Claude Code, from any folder:
```text
/ig-digest <creator handle>
```
- On the first run Claude asks about your goals and writes `~/ig-digest/goals.md` (the shape is in `goals.example.md`).
- The first speech-to-text downloads about 1 GB of models once.
- Everything it makes goes to `~/video-digest/`. Set `VIDEO_DIGEST_HOME` to use another folder.

## 7. Optional: weekly by itself
SKILL.md, section "Weekly mode", has the Task Scheduler command (Sundays 09:00) and a session-start line that tells you when new posts are waiting. Put the creators in `~/ig-digest/creators.txt`, one handle per line.

## Rules that keep the account alive
- Spare account only. No scripted login.
- Never like, follow, comment or DM from it.
- A login wall or rate limit (exit 3 or 4) stops everything. Wait; don't retry the same day.

## Stuck?
- `ffmpeg is not on PATH`: open a new terminal after installing it.
- `no Instagram login in Firefox`: log in with the spare account in Firefox, then rerun.
- Listing or download suddenly fails for everyone: update the tools, `python -m pip install -U yt-dlp gallery-dl`.
