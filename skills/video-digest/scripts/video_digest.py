#!/usr/bin/env python3
"""video_digest.py - Instagram creators -> post lists -> transcripts. The reading and judging is in ../SKILL.md.

    python video_digest.py list --handle H [--days 180] [--tab posts|reels]
    python video_digest.py transcribe --handle H --all [--max 30]
    python video_digest.py transcribe --handle H --picks <file or shortcodes> [--max 30]

Writes to ~/video-digest/ (override with VIDEO_DIGEST_HOME, or the older IG_DIGEST_HOME), never into the repo:
    <handle>/index.csv                one row per post, merged across runs
    <handle>/transcripts/<code>.md    speech and on-screen text, one file per reel or post that has any
    archive.sqlite3                   what was listed and transcribed; reruns skip what is done
    .tmp/                             media while it is being read; emptied at start and end
    goals.md                          the user's goals; verdicts are judged against them (see goals.example.md)

Exit codes: 0 done · 1 a tool or input failed · 3 login wall or checkpoint · 4 rate limited (no retries; wait).

Login: the spare account in Firefox, read with --cookies-from-browser. No password, no scripted login.
Requests go one at a time. Tools: gallery-dl (GPL-2.0) and yt-dlp (Unlicense) run as separate programs, not bundled.

Why the listing looks the way it does (gallery-dl 1.32.19, read in its source and seen on a real run):
- --date-after stops at the first older post, and pinned reels are old. -A and --download-archive count skipped
  downloads, so they do nothing for a listing. The stop rule and the seen-state therefore live here.
- -j hides errors: with output.jsonl a failure prints nothing and exits 0, so it would look like the end of the profile.
  A metadata post-processor on the normal job streams the same JSON and keeps real errors and exit codes.
- Firefox holds a fresh login in cookies.sqlite-wal, and both tools copy only cookies.sqlite, so they would see a
  logged-out browser. copy_login() merges the two into a temporary copy and the tools read that.
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import key_frames  # noqa: E402
import vd_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def console_python():
    """The Python for child processes. A scheduled task can run this under pythonw.exe, a windowed program, and Windows ignores
    CREATE_NO_WINDOW for those: their own console children (ffmpeg, whisper-cli) would each flash a black window. The
    python.exe beside it is a console program, so NO_WINDOW applies and the hidden console is inherited by its children."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return sys.executable


PYTHON = console_python()
GALLERY_DL = [PYTHON, "-m", "gallery_dl"]  # not the .exe on PATH: same reason as yt_digest.py
YTDLP = [PYTHON, "-m", "yt_dlp"]  # -x runs ffmpeg
MEDIA_DIGEST = [PYTHON, str(Path(__file__).resolve().parent / "media_digest.py")]
SCREEN_TEXT = [PYTHON, str(Path(__file__).resolve().with_name("screen_text.py"))]

STOP_AFTER = 3            # posts in a row, older than the cut-off or already known
MAX_429 = 3               # 429 answers before all Instagram requests stop
MAX_FAILS = 3             # downloads failing in a row before the run stops
PAUSE = (3.0, 8.0)        # seconds between downloads
THREADS = 2               # leaves the rest of the CPU free for other work
TMP_NAME = ".tmp"
SCREEN_HEAD = "## On screen"
SCREEN_LINE = f"\n{SCREEN_HEAD}\n"  # a whole line: a caption can hold the same words
REFUSED = {"login": "login_required", "rate": "429"}  # what classify() reads, for a reason found in a saved reply
CAPTION_CHARS = 300
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)  # Windows only; children inherit it
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows only: no console window for a console child (python.exe, see console_python); its children inherit the hidden console
EXIT_FAIL, EXIT_LOGIN, EXIT_RATE = 1, 3, 4
LOGIN_MSG = "Log into Instagram in Firefox (spare account), then rerun"
RATE_MSG = "Instagram is rate limiting. All requests stopped; do not retry today."

FIELDS = ["shortcode", "url", "date", "type", "pinned", "views", "likes", "comments", "caption", "archive"]
AUDIO_EXT = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac"}
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mkv", ".mov"}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT | {".ts", ".part", ".ytdl", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
HAS_RESULT = ("transcribed", "no_speech")

HANDLE_RE = re.compile(r"^(?!\.+$)[A-Za-z0-9._]{1,30}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,40}$")
URL_CODE_RE = re.compile(r"instagram\.com/(?:[^/\s]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]+)")
REQUEST_RE = re.compile(r'https?://(\S+?):\d+ "(?:GET|POST|HEAD) (\S+) HTTP/[\d.]+" (\d{3})')
LOG_LINE_RE = re.compile(r"^\[[\w.-]+\]\[(?:error|warning)\]")  # -v can also dump whole pages; those lines must not count
WALL_PATH_RE = re.compile(r"^/(?:accounts/login|challenge)\b")
DEAD_COOKIES_RE = re.compile(r"cookies are no longer valid", re.I)  # yt-dlp's warning: it drops the cookie and goes on logged out
# The first line is gallery-dl's wording, the second line is yt-dlp's: instagram.py (redirected to the login page, only
# available for registered users, Failed to parse JSON for a login page served as 200) and the hint that common.py's
# raise_login_required appends: "Use --cookies-from-browser or --cookies for the authentication", or "Use --cookies, ...".
LOGIN_RE =re.compile(r"redirect to (?:login|challenge|home) page|/accounts/login|/challenge/|login_required|"
                      r"checkpoint_required|challenge_required|AuthRequired|AuthenticationError|"
                      r"login required|empty media response|HTTP Error 40[013]\b|"
                      r"redirected to the login page|only available for registered users|Failed to parse JSON|"
                      r"use --cookies[^.\n]*(?:for the authentication|--username)|" + DEAD_COOKIES_RE.pattern, re.I)
# gallery-dl's own wording for a refused post. A 400 is judged by the reply's body instead: a removed post can answer 400 too.
GALLERY_WALL_RE = re.compile(r"'40[13] [^']*' for 'https?://[\w.]*\binstagram\.com", re.I)
PAGE_WALL_RE = re.compile(r"checkpoint_required|challenge_required|login_required")
SLOW_DOWN_RE = re.compile(r"wait a few minutes|feedback_required", re.I)
RATE_RE = re.compile(r"\b429\b|too many requests|" + SLOW_DOWN_RE.pattern, re.I)
SPEECH_RE = re.compile(r"^\[(?:\d+:\d\d|\d\d+):\d\d\] \S", re.M)  # [MM:SS] under an hour, [H:MM:SS] from it
NO_AUDIO_RE = re.compile(r"unable to obtain file audio codec", re.I)  # yt-dlp: the reel came down but has no sound track


def log(msg):
    if sys.stdout is not None:  # None under pythonw.exe
        print(msg, flush=True)


def home():
    return vd_config.home()


def check_handle(handle):
    handle = handle.strip().lstrip("@").lower()  # Instagram handles are lowercase; one creator, one folder
    if not HANDLE_RE.match(handle):
        raise ValueError(f"not an Instagram handle: {handle!r}")
    return handle


def one_line(text, limit=CAPTION_CHARS):
    return " ".join((text or "").split())[:limit]


def parse_date(value):
    """gallery-dl's dates are UTC, written '2026-10-01 12:34:56'."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_line(line):
    """One line of gallery-dl's output -> a post's metadata, or None for anything else."""
    try:
        meta = json.loads(line)
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def parse_post(meta):
    """A post's metadata -> an index row, or None when it has no shortcode or date."""
    code = meta.get("post_shortcode") or meta.get("shortcode")
    date = meta.get("post_date") or meta.get("date")
    if not code or not date:
        return None
    return {"shortcode": code,
            "url": meta.get("post_url") or f"https://www.instagram.com/p/{code}/",
            "dt": parse_date(date),
            "type": meta.get("type") or "post",
            "pinned": bool(meta.get("pinned")),
            "views": "",  # Instagram's timeline reply has view_count: null
            "likes": meta.get("likes", ""),
            "comments": "",  # filled from the saved replies; see page_facts()
            "caption": one_line(meta.get("description"))}


class StopRule:
    """Decides when the listing is finished. Pinned posts never count: they sit on top and can be years old."""

    def __init__(self, cutoff, known=(), limit=STOP_AFTER):
        self.cutoff, self.known, self.limit = cutoff, set(known), limit
        self.old_run = self.known_run = 0
        self.reason = None

    def feed(self, row):
        """Returns True when the row belongs in the index."""
        older = row["dt"] < self.cutoff
        if row["pinned"]:
            return not older
        if older:
            self.old_run += 1
            self.known_run = 0
            if self.old_run >= self.limit:
                self.reason = f"{self.limit} posts in a row older than {self.cutoff:%Y-%m-%d}"
            return False
        self.old_run = 0
        self.known_run = self.known_run + 1 if row["shortcode"] in self.known else 0
        if self.known_run >= self.limit:
            self.reason = f"{self.limit} already-known posts in a row"
        return True


def read_posts(lines, rule):
    """Feeds gallery-dl's JSON lines through the rule until it fires or the output ends. Returns the index rows."""
    rows, seen = [], set()
    for line in lines:
        meta = parse_line(line)
        row = parse_post(meta) if meta else None
        if row is None or row["shortcode"] in seen:
            continue
        seen.add(row["shortcode"])
        if rule.feed(row):
            rows.append(row)
        if rule.reason:
            break
    return rows


class LogWatch:
    """Reads gallery-dl's -v log: counts HTTP requests and spots a login wall or a run of 429s."""

    def __init__(self, on_block=None):
        self.requests = self.too_many = 0
        self.blocked = None  # 'login' or 'rate'
        self.on_block = on_block
        self.tail = deque(maxlen=5)

    def block(self, kind):
        if not self.blocked:
            self.blocked = kind
            if self.on_block:
                self.on_block()

    def feed(self, line):
        m = REQUEST_RE.search(line)
        if m:
            host, path, status = m.groups()
            self.requests += 1
            self.too_many += status == "429"  # a single 429 only waits; MAX_429 of them stop the run
            if WALL_PATH_RE.match(path) or (status in ("401", "403") and "instagram.com" in host
                                            and "cdninstagram" not in host):
                self.block("login")
        elif LOG_LINE_RE.match(line):
            self.tail.append(line.strip())
            if LOGIN_RE.search(line):
                self.block("login")
            elif SLOW_DOWN_RE.search(line):
                self.block("rate")
        if self.too_many >= MAX_429:
            self.block("rate")


def classify(text):
    """A tool's error text -> 'login', 'rate' or None. The login wall wins: yt-dlp's message names both."""
    if LOGIN_RE.search(text) or GALLERY_WALL_RE.search(text):
        return "login"
    return "rate" if RATE_RE.search(text) else None


class Archive:
    """archive.sqlite3: what was listed and transcribed. Written only after a clean finish, so a listing that was cut
    short cannot make the next run stop early at posts it never got past.
    Status: new (just listed) · listed (a run has passed it; a reel stays on --all's list) · transcribed · no_speech."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS posts (shortcode TEXT PRIMARY KEY, handle TEXT NOT NULL, listed_at TEXT NOT NULL,
                                              status TEXT NOT NULL DEFAULT 'listed', transcribed_at TEXT);
            CREATE TABLE IF NOT EXISTS creators (handle TEXT PRIMARY KEY, cutoff TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen (tab TEXT NOT NULL, shortcode TEXT NOT NULL, PRIMARY KEY (tab, shortcode));""")
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(posts)")]
        for name in ("screen_at", "failed_at"):  # archives from before on-screen text
            if name not in columns:
                self.db.execute(f"ALTER TABLE posts ADD COLUMN {name} TEXT")

    def statuses(self):
        """Across all creators: a collab post shows up under both and needs handling only once."""
        return dict(self.db.execute("SELECT shortcode, status FROM posts"))

    def screened(self):
        """Posts whose on-screen text has been read, whether or not it held any."""
        return {row[0] for row in self.db.execute("SELECT shortcode FROM posts WHERE screen_at IS NOT NULL")}

    def failed(self):
        """When each post last failed. --all tries these last, so that dead posts cannot hold up the rest."""
        return dict(self.db.execute("SELECT shortcode, failed_at FROM posts WHERE failed_at IS NOT NULL"))

    def seen(self, tab):
        """What one tab has shown. A tab stops only on its own known posts: the other tab's prove nothing about how far it got."""
        return {row[0] for row in self.db.execute("SELECT shortcode FROM seen WHERE tab = ?", (tab,))}

    def covered_from(self, handle):
        """How far back a listing has reached. The reels tab counts apart, as '<handle>/reels'."""
        row = self.db.execute("SELECT cutoff FROM creators WHERE handle = ?", (handle,)).fetchone()
        return parse_date(row[0]) if row else None

    def save_listing(self, handle, codes, cutoff, now, coverage=None):
        coverage = coverage or handle
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO posts (shortcode, handle, listed_at, status) VALUES (?, ?, ?, 'new')",
                                [(c, handle, now.isoformat()) for c in codes])
            self.db.executemany("INSERT OR IGNORE INTO seen (tab, shortcode) VALUES (?, ?)", [(coverage, c) for c in codes])
            covered = self.covered_from(coverage)
            self.db.execute("INSERT OR REPLACE INTO creators (handle, cutoff) VALUES (?, ?)",
                            (coverage, min(cutoff, covered or cutoff).isoformat()))

    def mark(self, handle, code, status, now):
        with self.db:
            self.db.execute("""INSERT INTO posts (shortcode, handle, listed_at, status, transcribed_at)
                               VALUES (?, ?, ?, ?, ?) ON CONFLICT (shortcode) DO UPDATE
                               SET status = excluded.status, transcribed_at = excluded.transcribed_at""",
                            (code, handle, now.isoformat(), status, now.isoformat()))

    def mark_screen(self, handle, code, now):
        with self.db:
            self.db.execute("""INSERT INTO posts (shortcode, handle, listed_at, screen_at) VALUES (?, ?, ?, ?)
                               ON CONFLICT (shortcode) DO UPDATE SET screen_at = excluded.screen_at""",
                            (code, handle, now.isoformat(), now.isoformat()))

    def mark_failed(self, codes, now):
        with self.db:
            self.db.executemany("UPDATE posts SET failed_at = ? WHERE shortcode = ?", [(now.isoformat(), c) for c in codes])

    def close_triage(self, handle, keep):
        """After a transcribe run: what it passed over is no longer new. --all still finds the reels among these."""
        marks = ",".join("?" * len(keep))
        with self.db:
            self.db.execute(f"UPDATE posts SET status = 'listed' WHERE handle = ? AND status = 'new' "
                            f"AND shortcode NOT IN ({marks})", (handle, *keep))

    def close(self):
        self.db.close()


def read_index(path):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["shortcode"]: r for r in csv.DictReader(f)}


def write_index(path, rows, statuses):
    """Merges this run's rows into index.csv (newest first) and refreshes the archive column."""
    merged = read_index(path)
    for r in rows:
        merged[r["shortcode"]] = {**r, "date": r["dt"].strftime("%Y-%m-%d %H:%M"), "pinned": "yes" if r["pinned"] else ""}
    for code, r in merged.items():
        r["archive"] = statuses.get(code, "")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(merged.values(), key=lambda r: r["date"], reverse=True))


def fail(watch_kind, detail=""):
    if detail:
        log(detail)
    log(LOGIN_MSG if watch_kind == "login" else RATE_MSG)
    return EXIT_LOGIN if watch_kind == "login" else EXIT_RATE


def firefox_profile():
    """The most recently used profile, the same one the tools would pick."""
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Mozilla" / "Firefox" / "Profiles"
    found = sorted(base.glob("*/cookies.sqlite"), key=lambda p: p.stat().st_mtime)
    return found[-1].parent if found else None


def copy_login(tmp):
    """Copies Firefox's cookies into <tmp>/cookies with the write-ahead log folded in. Firefox's own files are only read.
    Returns the folder for --cookies-from-browser, or None when Instagram is not logged in (no request is needed to tell).
    The -shm index stays behind on purpose: SQLite rebuilds it from the log."""
    profile = firefox_profile()
    if profile is None:
        return None
    folder = tmp / "cookies"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("cookies.sqlite", "cookies.sqlite-wal"):
        if (profile / name).exists():
            shutil.copy2(profile / name, folder / name)
    db = sqlite3.connect(folder / "cookies.sqlite")
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        found = db.execute("SELECT 1 FROM moz_cookies WHERE name = 'sessionid' AND host LIKE '%instagram.com' LIMIT 1").fetchone()
    finally:
        db.close()
    return folder if found else None


def page_facts(pages):
    """What gallery-dl drops but --write-pages keeps, at no extra request: code -> (comment count, is it a reel).
    gallery-dl calls a reel with a downloadable sound a 'post'; Instagram's product_type says clips.
    Views stay blank: the same replies carry view_count: null."""
    facts = {}

    def walk(node):
        if isinstance(node, dict):
            if "code" in node and "comment_count" in node:
                facts[node["code"]] = (node["comment_count"], node.get("product_type") == "clips")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for page in pages.glob("*graphql_query*"):
        try:
            walk(json.loads(page.read_text(encoding="utf-8", errors="replace")))
        except ValueError:
            continue
    return facts


def page_number(page):
    """gallery-dl names a saved reply NN_<url>.txt; NN counts up and passes 99, so it sorts as a number."""
    m = re.match(r"\d+", page.name)
    return int(m.group()) if m else -1


def refusal(watch, pages):
    """Why a listing that exited with an error failed, when Instagram refused it: 'login', 'rate' or None.
    Only the last saved reply is read: gallery-dl saves a reply before it checks the status, so that one failed.
    A JSON reply is judged by its own top-level message; a healthy reply has none, so a caption cannot match."""
    last = max(pages.glob("*.txt"), key=page_number, default=None)
    if last is not None:
        text = last.read_text(encoding="utf-8", errors="replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if isinstance(body, dict):
            text = str(body.get("message") or "")
        if PAGE_WALL_RE.search(text):
            return "login"  # a checkpoint can come as an HTTP 400 with the reason only in the body
        if SLOW_DOWN_RE.search(text):
            return "rate"
    if watch.too_many:
        return "rate"  # with --retries 1, gallery-dl gives up after two 429s, before MAX_429
    return None


def gallery_cmd(handle, cookies, tab="posts"):
    stream = json.dumps([{"name": "metadata", "mode": "jsonl", "filename": "-", "event": "post"}])
    return GALLERY_DL + ["--config-ignore", "-v", "--no-download", "--no-skip", "--write-pages", "-o", "output.mode=false",
                         "-o", f"postprocessors={stream}", "--cookies-from-browser", f"firefox:{cookies}",
                         "--sleep-request", "6-12", "--sleep-429", "120", "--retries", "1",
                         f"https://www.instagram.com/{handle}/{tab}/"]


def cmd_list(handle, days, root=None, now=None, tab="posts"):
    """The posts tab costs one request per 12 posts. The reels tab costs one per reel, and shows reels kept off the grid."""
    root = root or home()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    root.mkdir(parents=True, exist_ok=True)
    tmp = clean_tmp(root)
    coverage = handle if tab == "posts" else f"{handle}/{tab}"
    archive = Archive(root / "archive.sqlite3")
    try:
        cookies = copy_login(tmp)
        if cookies is None:
            return fail("login", "no Instagram login in Firefox; 0 requests made")
        index_path = root / handle / "index.csv"
        statuses = archive.statuses()
        covered = archive.covered_from(coverage)
        # Known means shown by an earlier listing of this same tab: a reel transcribed by URL, or shown by the other tab, says
        # nothing about the posts below it. A wider window than any earlier run must reach the older posts, so nothing is known.
        known = archive.seen(coverage) & set(read_index(index_path)) if covered is not None and covered <= cutoff else ()
        rule = StopRule(cutoff, known)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}  # unbuffered: each post arrives at once
        pages = tmp / "pages"  # --write-pages writes to the working folder
        shutil.rmtree(pages, ignore_errors=True)  # a killed run's replies must not pass for this run's last one
        pages.mkdir(exist_ok=True)
        proc = subprocess.Popen(gallery_cmd(handle, cookies, tab), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", env=env, cwd=pages, creationflags=NO_WINDOW)
        watch = LogWatch(on_block=proc.terminate)

        def drain():
            for line in proc.stderr:
                watch.feed(line)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            rows = read_posts(proc.stdout, rule)
        except BaseException:
            proc.terminate()  # no more requests from a listing nobody is reading
            raise
        if proc.poll() is None and rule.reason:
            proc.terminate()  # the rule fired: no more requests
        code = proc.wait(timeout=30)
        reader.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()

        failed = code and not rule.reason
        kind = watch.blocked or (refusal(watch, pages) if failed else None)
        if kind:
            return fail(kind, f"listing stopped after {len(rows)} rows, {watch.requests} requests; nothing saved")
        if failed:
            log(f"gallery-dl failed for {handle} (exit {code}) after {len(rows)} rows, {watch.requests} requests; nothing saved")
            log("\n".join(watch.tail))
            return EXIT_FAIL

        facts = page_facts(pages)
        for r in rows:
            comments, reel = facts.get(r["shortcode"], ("", False))
            r["comments"] = comments
            if (reel or tab == "reels") and r["type"] != "reel":
                r["type"], r["url"] = "reel", f"https://www.instagram.com/reel/{r['shortcode']}/"
        new = [r for r in rows if r["shortcode"] not in statuses]
        # The index first: if it cannot be written, the archive must not claim these posts were seen.
        write_index(index_path, rows, {**statuses, **{r["shortcode"]: "new" for r in new}})
        archive.save_listing(handle, [r["shortcode"] for r in rows], cutoff, now, coverage)
        if not rows:
            log("no posts: the profile is empty or private, and a listing cannot tell which")
        log(f"rows={len(rows)} new={len(new)} known={len(rows) - len(new)} requests={watch.requests} "
            f"stop=\"{rule.reason or 'end of profile'}\" index={index_path}")
        return 0
    finally:
        archive.close()
        shutil.rmtree(tmp / "cookies", ignore_errors=True)
        shutil.rmtree(tmp / "pages", ignore_errors=True)


def parse_picks(value):
    """A file with one shortcode or URL per line ('#' comments), or shortcodes and URLs split by commas or spaces."""
    p = Path(value)
    text = p.read_text(encoding="utf-8") if len(value) < 260 and p.is_file() else value
    text = "\n".join(line.split("#")[0] for line in text.splitlines())
    picks = []
    for token in re.split(r"[,\s]+", text):
        m = URL_CODE_RE.search(token)
        code = m.group(1) if m else token
        if not code:
            continue
        if not CODE_RE.match(code):
            raise ValueError(f"not a shortcode: {code!r}")
        if code not in picks:
            picks.append(code)
    return picks


def still_to_read(index, statuses, screened, failed=None):
    """What --all takes: everything in index.csv short of its speech or its on-screen text.
    Reels first, and what failed before goes last, so that dead posts cannot hold up the rest."""
    failed = failed or {}
    codes = [c for c in index if statuses.get(c) not in HAS_RESULT or c not in screened]
    return sorted(codes, key=lambda c: (failed.get(c, ""), index[c].get("type") != "reel"))


def clean_tmp(root):
    """Empties <root>/.tmp and nothing else."""
    tmp = root / TMP_NAME
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def media_files(folder):
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXT]


def dir_size(folder):
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def download_audio(code, url, folder, cookies):
    """One reel into its own folder: -x pulls the audio out and -k keeps the video for its on-screen text.
    Returns (audio path or None, error text)."""
    folder.mkdir(parents=True, exist_ok=True)
    cmd = YTDLP + ["--cookies-from-browser", f"firefox:{cookies}", "-f", "best/bv*+ba", "-x", "-k", "--no-playlist",
                   "--playlist-items", "1", "--no-progress", "-o", str(folder / f"{code}.%(ext)s"), url]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    files = [p for p in media_files(folder) if p.suffix.lower() in AUDIO_EXT]
    if done.returncode == 0 and files:
        # exit 0 is not success when the cookie was refused: yt-dlp warns, goes on logged out, and may still get a public reel
        dead = next((ln for ln in done.stderr.splitlines() if DEAD_COOKIES_RE.search(ln)), None)
        if dead:
            return None, dead.strip()
        return files[0], ""
    return None, (done.stderr or done.stdout or "no file written").strip()


def download_post(code, url, folder, cookies):
    """A photo or carousel post's files into its own folder, named <code>_<slide>. Returns (folder or None, message):
    the message is why it failed, or on success that Instagram left a slide out."""
    pages = folder / "pages"  # --write-pages writes to the working folder
    pages.mkdir(parents=True, exist_ok=True)
    cmd = GALLERY_DL + ["--config-ignore", "--no-skip", "--write-pages", "--cookies-from-browser", f"firefox:{cookies}",
                        "--sleep-request", "6-12", "--sleep-429", "120", "--retries", "1", "-D", str(folder),
                        "-f", "{post_shortcode}_{num}.{extension}", url]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=pages,
                          creationflags=NO_WINDOW)
    if done.returncode == 0 and media_files(folder):
        return folder, next((ln.strip() for ln in done.stderr.splitlines() if "Missing media" in ln), "")
    error = (done.stderr or done.stdout or "no file written").strip()
    kind = refusal(LogWatch(), pages)  # a checkpoint can come as an HTTP 400 with the reason only in the body
    return None, f"{error}\n{REFUSED[kind]}" if kind else error


def has_sound(path):
    """True or False, or None when the file cannot be read: a broken download is not a silent one."""
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW)
    return None if done.returncode else "audio" in done.stdout


def slide_of(path, code):
    """A post's file is <code>_<slide>; 0 for anything else. A shortcode can itself end in _12."""
    m = re.fullmatch(re.escape(code) + r"_(\d+)", path.stem)
    return int(m.group(1)) if m else 0


def videos_in(folder):
    return sorted((p for p in media_files(folder) if p.suffix.lower() in VIDEO_EXT), key=lambda p: slide_of(p, folder.name))


def sound_of(video):
    """A video's sound as a file of its own, or None. Given a video, media_digest also cuts a frame every 30 s, and
    ffmpeg 9 stops with an error on a clip too short to give one. screen_text.py reads the frames here anyway."""
    wav = video.with_suffix(".wav")
    done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return None if done.returncode else wav


def sounds_in(folder):
    """The sound of a post's videos, in slide order; media_digest stops at a file without any, so silent ones stay out.
    None when one cannot be read, so that the post is tried again and not closed without its speech."""
    sound = {v: has_sound(v) for v in videos_in(folder)}
    if None in sound.values():
        return None
    files = [sound_of(v) for v, heard in sound.items() if heard]
    return None if None in files else files


def truly_silent(folder):
    """yt-dlp's no-audio-codec error also covers a broken file: only a readable video with no sound track counts."""
    videos = videos_in(folder)
    return bool(videos) and all(has_sound(v) is False for v in videos)


WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
TIME_MARK_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
UNRELIABLE = "- transcript: may be unreliable"
KEEP_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def spoken_words(body):
    """Words in a transcript body: headings and [mm:ss] marks left out; CJK text counted as about 2 characters a word."""
    text = TIME_MARK_RE.sub(" ", "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#")))
    cjk = len(CJK_RE.findall(text))
    return len(WORD_RE.findall(CJK_RE.sub(" ", text))) + cjk // 2


def reliability_note(body, seconds, min_wpm):
    """A one-line warning when the transcript is far shorter than its audio, else None. Short clips are not judged."""
    if seconds < 15:
        return None
    words = spoken_words(body)
    if words * 60 / seconds >= min_wpm:
        return None
    return f"{UNRELIABLE} ({words} words over {round(seconds)} s of audio; or mostly music)"


def mark_unreliable(path, note):
    """Puts the warning under the caption line of a transcript, once."""
    if not note or not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if any(ln.startswith(UNRELIABLE) for ln in lines):
        return
    at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("- caption:")), min(len(lines), 4))
    lines.insert(at, note)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def keep_key_frames(folder, dest, cfg):
    """The frames Claude looks at, kept before the media is deleted: key frames of the videos, else the photo slides.
    Returns how many were kept."""
    videos = videos_in(folder)
    cap = int(vd_config.number("MAX_FRAMES", cast=float) if cfg is None else float(cfg.get("MAX_FRAMES") or 20))
    if videos:
        return key_frames.keep(videos, dest, threshold=float(cfg.get("SCENE_THRESHOLD") or 0.30),
                               every=float(cfg.get("FRAME_EVERY_SEC") or 3), cap=cap,
                               width=int(float(cfg.get("FRAME_WIDTH") or 1080)))
    images = sorted((p for p in media_files(folder) if p.suffix.lower() in KEEP_IMAGE_EXT),
                    key=lambda p: (slide_of(p, folder.name), p.name))[:cap]
    if images:
        dest.mkdir(parents=True, exist_ok=True)
        for img in images:
            shutil.copy2(img, dest / img.name)
    return len(images)


def priority_name(proc):
    """The real priority class of a running process, as Windows reports it."""
    if os.name != "nt":
        return "n/a"
    import ctypes
    value = ctypes.windll.kernel32.GetPriorityClass(int(proc._handle))
    return {0x4000: "BELOW_NORMAL", 0x20: "NORMAL", 0x40: "IDLE", 0x8000: "ABOVE_NORMAL"}.get(value, hex(value))


def run_media_digest(files, out, engine="sensevoice"):
    cmd = MEDIA_DIGEST + [str(f) for f in files] + ["--out", str(out), "--threads", str(THREADS), "--lang", "auto",
                                                    "--engine", engine]
    env = {**os.environ, **{name: str(THREADS) for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=BELOW_NORMAL | NO_WINDOW, env=env)
    log(f"transcriber pid={proc.pid} priority={priority_name(proc)} threads={THREADS} files={len(files)}")
    tail = deque(proc.stdout, maxlen=5)
    proc.stdout.close()
    if proc.wait():
        log(f"media_digest exited {proc.returncode}: {' | '.join(s.strip() for s in tail)}")


def run_screen_text(folders, result):
    """On-screen text of the given folders -> {code: [[label, text], ...]}. A folder that broke has no entry."""
    if not folders:
        return {}
    proc = subprocess.Popen(SCREEN_TEXT + [str(result)] + [str(f) for f in folders], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            creationflags=BELOW_NORMAL | NO_WINDOW)
    log(f"screen text pid={proc.pid} priority={priority_name(proc)} folders={len(folders)}")
    tail = deque(proc.stdout, maxlen=5)
    proc.stdout.close()
    if proc.wait():
        log(f"screen_text exited {proc.returncode}: {' | '.join(s.strip() for s in tail)}")
    return json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}


def screen_text_missing():
    """Why on-screen text cannot be read, or None. Asked before any Instagram request is spent."""
    done = subprocess.run(SCREEN_TEXT + ["--check"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW)
    return (done.stdout or done.stderr or "screen_text.py failed").strip() if done.returncode else None


def transcript_head(code, handle, meta):
    return [f"# {code} (@{handle})", "",
            f"- url: {meta.get('url') or f'https://www.instagram.com/reel/{code}/'}",
            f"- date: {meta.get('date', '')}",
            f"- caption: {meta.get('caption', '')}", ""]


def write_transcript(path, code, handle, meta, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in body.splitlines() if not ln.startswith("# transcript")]
    text = "\n".join(transcript_head(code, handle, meta) + lines).rstrip() + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if SCREEN_LINE in old:  # speech that arrives after the on-screen text must not wipe it
        text += old[old.index(SCREEN_LINE):]
    path.write_text(text, encoding="utf-8")


def spoken(code, files, bodies):
    """The transcripts of one post as one body, each under its slide when it came from a carousel."""
    return "\n".join(f"### Slide {slide_of(f, code)}\n{b}" if slide_of(f, code) else b
                     for f, b in zip(files, bodies) if SPEECH_RE.search(b))


def add_screen_text(path, code, handle, meta, found):
    """Puts the on-screen text under the speech, in a file of its own when the post has no speech. Written once."""
    if not found:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else "\n".join(transcript_head(code, handle, meta))
    if SCREEN_LINE in text:
        return
    lines = [f"[{label}] {line}" for label, line in found]
    path.write_text("\n".join([text.rstrip(), "", SCREEN_HEAD] + lines) + "\n", encoding="utf-8")


def cmd_transcribe(handle, picks_arg, limit, root=None):
    root = root or home()
    root.mkdir(parents=True, exist_ok=True)
    tmp = clean_tmp(root)
    picks = None if picks_arg is None else parse_picks(picks_arg)  # None is --all
    archive = Archive(root / "archive.sqlite3")
    try:
        missing = screen_text_missing()
        if missing:
            log(f"on-screen text cannot be read: {missing}; 0 requests made")
            return EXIT_FAIL
        cfg = vd_config.load(root)
        try:
            engine = vd_config.engine(root)
        except ValueError as exc:
            log(f"{exc}; fix .env in {root}; 0 requests made")
            return EXIT_FAIL
        cookies = copy_login(tmp)
        if cookies is None:
            return fail("login", "no Instagram login in Firefox; 0 requests made")
        index = read_index(root / handle / "index.csv")
        statuses, screened = archive.statuses(), archive.screened()
        if picks is None:
            picks = still_to_read(index, statuses, screened, archive.failed())
        done_before = [c for c in picks if statuses.get(c) in HAS_RESULT and c in screened]
        todo = [c for c in picks if c not in done_before]
        later = todo[limit:]
        if later:
            log(f"capped at {limit}: left for the next run: {' '.join(later)}")
            todo = todo[:limit]

        listen, fetched, failed, blocked, in_a_row = {}, [], [], None, 0
        speech, silent = [], []
        for n, code in enumerate(todo):
            if n:
                time.sleep(random.uniform(*PAUSE))
            url = index.get(code, {}).get("url") or f"https://www.instagram.com/reel/{code}/"
            is_post = index.get(code, {}).get("type") == "post"
            path, error = (download_post if is_post else download_audio)(code, url, tmp / code, cookies)
            files = [path] if path and not is_post else sounds_in(path) if path else []
            if path and files is None:
                path, error = None, "a video in it could not be read"
            if path:
                in_a_row = 0
                fetched.append(code)
                if statuses.get(code) not in HAS_RESULT:
                    listen[code] = files
                log(f"fetched {n + 1}/{len(todo)}: {code}" + (f" ({error})" if error else ""))
                continue
            blocked = classify(error)
            if blocked:
                log(f"{code}: {error.splitlines()[-1]}")
                break  # no more Instagram requests; what is already here still gets transcribed
            if NO_AUDIO_RE.search(error) and truly_silent(tmp / code):
                in_a_row = 0  # Instagram served it; retrying would fail the same way on every run
                fetched.append(code)
                if statuses.get(code) not in HAS_RESULT:  # a reel read earlier keeps its speech
                    silent.append(code)
                    archive.mark(handle, code, "no_speech", datetime.now(timezone.utc))
                log(f"{code}: no sound track")
                continue
            failed.append(code)
            in_a_row += 1
            log(f"{code}: download failed: {error.splitlines()[-1]}")
            if in_a_row >= MAX_FAILS:
                log(f"{MAX_FAILS} downloads failed in a row; stopping all Instagram requests")
                break

        out = tmp / "_out"
        if any(listen.values()):
            run_media_digest([f for files in listen.values() for f in files], out, engine)
        for code, files in listen.items():
            if not files:  # a post with no sound anywhere
                archive.mark(handle, code, "no_speech", datetime.now(timezone.utc))
                continue
            made = [out / f"{f.stem}_digest" / "transcript.md" for f in files]
            if not all(m.exists() for m in made):
                failed.append(code)
                log(f"{code}: no transcript came back")
                continue
            body = spoken(code, files, [m.read_text(encoding="utf-8") for m in made])
            if body:
                write_transcript(root / handle / "transcripts" / f"{code}.md", code, handle, index.get(code, {}), body)
                try:
                    seconds = sum(key_frames.duration(f) for f in files)
                    note = reliability_note(body, seconds, float(cfg.get("MIN_WORDS_PER_MIN") or 40))
                    mark_unreliable(root / handle / "transcripts" / f"{code}.md", note)
                    if note:
                        log(f"{code}: {note[2:]}")
                except Exception as exc:  # a warning that cannot be worked out must not cost the transcript
                    log(f"{code}: reliability check skipped: {exc}")
                speech.append(code)
            else:
                silent.append(code)
                log(f"{code}: no speech in the audio")
            archive.mark(handle, code, "transcribed" if body else "no_speech", datetime.now(timezone.utc))

        found = run_screen_text([tmp / c for c in fetched], tmp / "screen.json")
        for code in fetched:
            if code not in found:
                failed.append(code)
                log(f"{code}: its on-screen text could not be read")
                continue
            add_screen_text(root / handle / "transcripts" / f"{code}.md", code, handle, index.get(code, {}), found[code])
            archive.mark_screen(handle, code, datetime.now(timezone.utc))

        for code in fetched:  # the frames Claude looks at, before the media is deleted
            try:
                kept = keep_key_frames(tmp / code, root / handle / "frames" / code, cfg)
                if kept:
                    log(f"{code}: kept {kept} frames for Claude")
            except Exception as exc:  # frames are extra: a failure here must not cost the text
                log(f"{code}: key frames skipped: {exc}")

        failed = list(dict.fromkeys(failed))
        archive.mark_failed(failed, datetime.now(timezone.utc))
        if not blocked and in_a_row < MAX_FAILS:
            archive.close_triage(handle, failed + later)  # those two keep their place for the next run
        if (root / handle / "index.csv").exists():
            try:
                write_index(root / handle / "index.csv", [], archive.statuses())
            except PermissionError:
                log("index.csv is open in another program; its archive column catches up on the next run")
        clean_tmp(root)
        size = dir_size(root)
        statuses, screened = archive.statuses(), archive.screened()
        left = sum(statuses.get(c) not in HAS_RESULT or c not in screened for c in picks)
        log(f"picked={len(picks)} skipped_done={len(done_before)} transcribed={len(speech)} no_speech={len(silent)} "
            f"failed={len(failed)} tmp_media={len(media_files(tmp))} disk={size} bytes ({size / 1048576:.2f} MB) "
            f"screen={sum(1 for c in found.values() if c)} left={left}")
        if blocked:
            return fail(blocked)
        return EXIT_FAIL if failed else 0
    finally:
        archive.close()
        clean_tmp(root)  # also when something above raised: no cookies or audio stay behind


@contextmanager
def one_at_a_time(root):
    """Two commands at once would empty each other's .tmp. The system drops the lock when a process dies, so none goes stale."""
    root.mkdir(parents=True, exist_ok=True)
    lock = open(root / ".lock", "w")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("another video-digest command is already running; wait for it to finish") from None
        yield
    finally:
        lock.close()


def cmd_drop_frames(handle, root=None):
    """Deletes the kept frames of every reel that has its card: the frames were only there for Claude to look at."""
    root = root or home()
    frames, cards = root / handle / "frames", root / handle / "cards"
    if not frames.exists():
        log("no frames to drop")
        return 0
    dropped, waiting = 0, 0
    for folder in sorted(p for p in frames.iterdir() if p.is_dir()):
        if (cards / f"{folder.name}.md").exists():
            shutil.rmtree(folder, ignore_errors=True)
            dropped += 1
        else:
            waiting += 1
    log(f"dropped frames of {dropped} reels with a card; {waiting} still wait for their card")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="list a creator's posts, metadata only")
    p.add_argument("--handle", required=True)
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--tab", choices=("posts", "reels"), default="posts", help="reels also finds reels kept off the grid, at one request per reel")
    p = sub.add_parser("transcribe", help="media -> speech and on-screen text -> delete the media")
    p.add_argument("--handle", required=True)
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="everything in index.csv short of its speech or on-screen text")
    which.add_argument("--picks", help="a file, or comma-separated shortcodes; a code starting with - needs --picks=<code>")
    p.add_argument("--max", type=int, default=30, help="most reels per run (default 30); the rest wait for the next run")
    p = sub.add_parser("drop-frames", help="delete the kept frames of every reel that already has its card")
    p.add_argument("--handle", required=True)
    a = ap.parse_args(argv)
    try:
        handle = check_handle(a.handle)
        with one_at_a_time(home()):
            if a.cmd == "list":
                return cmd_list(handle, a.days, tab=a.tab)
            if a.cmd == "drop-frames":
                return cmd_drop_frames(handle)
            return cmd_transcribe(handle, a.picks, a.max)
    except ValueError as exc:
        log(str(exc))
        return EXIT_FAIL
    except PermissionError as exc:
        log(f"cannot write {exc.filename}: close it in the other program (Excel?) and rerun. Nothing was recorded as seen.")
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
