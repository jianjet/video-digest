"""Unit and mock-integration tests for video_digest.py. No network, no real tools.

    python -m unittest discover -s skills/video-digest/tests -v
"""
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import video_digest as ig  # noqa: E402
import vd_config  # noqa: E402

vd_config.REPO_ENV = None  # the developer's own .env must not change test results
import screen_text as st  # noqa: E402

REAL_LOG = ig.log  # Weekly needs the real one: run.log is written by it, and Tmp swaps it for a list
HAS_SOUND = ig.has_sound  # the real ones: Tmp stubs them, because the fake videos are not video
SOUND_OF = ig.sound_of


def fake_sound_of(video):
    wav = video.with_suffix(".wav")
    wav.write_bytes(b"sound")
    return wav

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=180)
TEMPLATE = json.loads((HERE / "fixtures" / "post.json").read_text(encoding="utf-8"))
FAKE = [sys.executable, str(HERE / "fake_tool.py")]
# yt-dlp's own wording, from extractor/instagram.py and the hint common.py's raise_login_required appends
HINT = ("Use --cookies-from-browser or --cookies for the authentication. See  "
        "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies")
HINT_ANY = ("Use --cookies, --cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (instagram) "
            "to provide account credentials. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
            "for how to manually pass cookies")
WALL_RATE = ("ERROR: [Instagram] reel1: The webpage request was redirected to the login page. "
             "You have exceeded the rate-limit for accessing posts anonymously. " + HINT)
YTDLP_WALLS = (
    WALL_RATE,
    "ERROR: [Instagram] reel1: This content is only available for registered users who follow this account. " + HINT,
    "ERROR: [Instagram] reel1: This content is unreachable. " + HINT,
    "ERROR: [Instagram] reel1: You need to log in to access this content. " + HINT,
    "ERROR: [Instagram] reel1: Restricted Video: Sensitive. " + HINT_ANY,
    "ERROR: [Instagram] reel1: Unable to download JSON metadata: Failed to parse JSON (caused by JSONDecodeError)",  # a login page served as 200: not verified against yt-dlp
)
DEAD_COOKIES = "WARNING: [instagram] reel1: The provided Instagram account cookies are no longer valid"
NO_AUDIO = "ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe"  # seen live on a reel with no sound


def meta(code, days_ago=1, pinned=False, **extra):
    """One post, built from the scrubbed real gallery-dl record."""
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {**TEMPLATE, "post_shortcode": code, "post_url": f"https://www.instagram.com/reel/{code}/", "date": stamp,
            "post_date": stamp, "pinned": ["1"] if pinned else [], **extra}


def jsonl(*metas):
    return [json.dumps(m) for m in metas]


def row(code, days_ago=1, pinned=False):
    return ig.parse_post(meta(code, days_ago, pinned))


class Parsing(unittest.TestCase):
    def test_post_fields(self):
        r = ig.parse_post(meta("code1", 3, likes=7, description="a\n\n  b " + "x" * 400))
        self.assertEqual((r["shortcode"], r["type"], r["likes"], r["pinned"]), ("code1", "reel", 7, False))
        self.assertEqual(r["url"], "https://www.instagram.com/reel/code1/")
        self.assertEqual(r["dt"], (NOW - timedelta(days=3)).replace(microsecond=0))
        self.assertTrue(r["caption"].startswith("a b xxx"))
        self.assertEqual(len(r["caption"]), 300)
        self.assertNotIn("\n", r["caption"])

    def test_speech_marks_past_an_hour_count_as_speech(self):
        # media-digest writes [1:02:05] from the hour on (media_digest.py hms)
        for body in ("[00:04] hello", "[59:59] hi", "[1:02:05] later", "[12:00:01] much later"):
            self.assertTrue(ig.SPEECH_RE.search(body), body)
        for body in ("[1:02] short", "[slide 2] text", "[1:2:05] bad", "[00:04]"):
            self.assertFalse(ig.SPEECH_RE.search(body), body)

    def test_pinned_is_a_list_of_ids(self):
        self.assertTrue(ig.parse_post(meta("code1", pinned=True))["pinned"])

    def test_missing_caption_and_counts(self):
        m = meta("code1", description=None)
        m.pop("likes")
        r = ig.parse_post(m)
        self.assertEqual((r["caption"], r["likes"], r["views"]), ("", "", ""))

    def test_record_without_shortcode_or_date_is_skipped(self):
        self.assertIsNone(ig.parse_post({"date": "2026-10-01 12:00:00"}))
        self.assertIsNone(ig.parse_post({"post_shortcode": "code1"}))

    def test_lines(self):
        self.assertEqual(ig.parse_line(json.dumps({"a": 1})), {"a": 1})
        self.assertIsNone(ig.parse_line("not json"))
        self.assertIsNone(ig.parse_line("[]"))

    def test_handles(self):
        self.assertEqual(ig.check_handle("@Some.One_1"), "some.one_1")
        for bad in ("..", "a/b", "a\\b", "", "x" * 31):
            with self.assertRaises(ValueError):
                ig.check_handle(bad)

    def test_picks_from_text(self):
        got = ig.parse_picks("abcde1, https://www.instagram.com/reel/abcde2/?x=1 https://www.instagram.com/someone/p/ab-d_3/,abcde1")
        self.assertEqual(got, ["abcde1", "abcde2", "ab-d_3"])

    def test_picks_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "picks.txt"
            f.write_text("# triage\nabcde1  # why\n\nabcde2\n", encoding="utf-8")
            self.assertEqual(ig.parse_picks(str(f)), ["abcde1", "abcde2"])

    def test_bad_pick(self):
        with self.assertRaises(ValueError):
            ig.parse_picks("../../etc")


class StopRuleTests(unittest.TestCase):
    def feed(self, rule, *rows):
        return [rule.feed(r) for r in rows]

    def test_old_pinned_posts_do_not_stop_it(self):
        rule = ig.StopRule(CUTOFF)
        kept = self.feed(rule, *[row(f"pin{i}x", 900, pinned=True) for i in range(5)], row("fresh", 1))
        self.assertEqual(kept, [False] * 5 + [True])
        self.assertIsNone(rule.reason)

    def test_recent_pinned_post_is_kept(self):
        self.assertTrue(ig.StopRule(CUTOFF).feed(row("pin1x", 5, pinned=True)))

    def test_stops_after_three_older_in_a_row(self):
        rule = ig.StopRule(CUTOFF)
        self.feed(rule, row("old1x", 200), row("old2x", 201))
        self.assertIsNone(rule.reason)
        self.assertFalse(rule.feed(row("old3x", 202)))
        self.assertIn("3 posts in a row older than 2026-05-08", rule.reason)

    def test_a_newer_post_resets_the_run(self):
        rule = ig.StopRule(CUTOFF)
        self.feed(rule, row("old1x", 200), row("old2x", 201), row("new1x", 10), row("old3x", 202), row("old4x", 203))
        self.assertIsNone(rule.reason)

    def test_pinned_between_old_posts_neither_counts_nor_resets(self):
        rule = ig.StopRule(CUTOFF)
        self.feed(rule, row("old1x", 200), row("old2x", 201), row("pin1x", 900, pinned=True), row("old3x", 202))
        self.assertIn("older", rule.reason)

    def test_boundary_exactly_on_the_cutoff_is_kept(self):
        self.assertTrue(ig.StopRule(CUTOFF).feed(row("edge1", 180)))

    def test_stops_after_three_known_in_a_row(self):
        rule = ig.StopRule(CUTOFF, known={"seen1", "seen2", "seen3"})
        self.feed(rule, row("new1x"), row("seen1"), row("seen2"), row("seen3"))
        self.assertIn("already-known", rule.reason)

    def test_known_pinned_posts_do_not_count(self):
        rule = ig.StopRule(CUTOFF, known={"seen1", "seen2", "seen3"})
        self.feed(rule, *[row(c, pinned=True) for c in ("seen1", "seen2", "seen3")])
        self.assertIsNone(rule.reason)

    def test_read_posts_stops_reading_when_the_rule_fires(self):
        lines = jsonl(meta("pin1x", 900, pinned=True), meta("new1x"), meta("old1x", 200), meta("old2x", 201),
                      meta("old3x", 202), meta("never", 1))
        rows = ig.read_posts(iter(lines), ig.StopRule(CUTOFF))
        self.assertEqual([r["shortcode"] for r in rows], ["new1x"])

    def test_read_posts_skips_repeats_and_noise(self):
        rows = ig.read_posts(iter(jsonl(meta("new1x"), meta("new1x")) + ["noise"]), ig.StopRule(CUTOFF))
        self.assertEqual(len(rows), 1)


class LogWatchTests(unittest.TestCase):
    OK = '[urllib3.connectionpool][debug] https://www.instagram.com:443 "GET /api/v1/feed/ HTTP/1.1" {} None'

    def test_counts_requests(self):
        w = ig.LogWatch()
        for line in (self.OK.format(200), self.OK.format(200), "[instagram][debug] something else"):
            w.feed(line)
        self.assertEqual((w.requests, w.blocked), (2, None))

    def test_a_run_of_429_blocks_a_single_one_waits(self):
        w = ig.LogWatch()
        w.feed(self.OK.format(429))
        w.feed("[instagram][warning] '429 Too Many Requests' for 'https://www.instagram.com/api'")
        self.assertIsNone(w.blocked)
        w.feed(self.OK.format(429))
        w.feed(self.OK.format(429))
        self.assertEqual(w.blocked, "rate")

    def test_login_wall(self):
        for line in ("[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)",
                     "[instagram][error] HTTP redirect to challenge page (https://www.instagram.com/challenge/)",
                     self.OK.format(401),
                     '[urllib3.connectionpool][debug] https://www.instagram.com:443 "GET /accounts/login/?next=x HTTP/1.1" 200 None'):
            w = ig.LogWatch()
            w.feed(line)
            self.assertEqual(w.blocked, "login", line)

    def test_a_dumped_page_is_not_a_login_wall(self):
        w = ig.LogWatch()
        w.feed('<a href="/accounts/login/">Log in</a> too many requests, wait a few minutes')
        w.feed("[instagram][debug] Server response: /accounts/login/")
        self.assertIsNone(w.blocked)

    def test_a_refused_script_file_is_not_a_login_wall(self):
        w = ig.LogWatch()
        w.feed('[urllib3.connectionpool][debug] https://static.cdninstagram.com:443 "GET /rsrc.php/a.js HTTP/1.1" 403 None')
        self.assertEqual((w.requests, w.blocked), (1, None))

    def test_blocking_calls_back_once(self):
        calls = []
        w = ig.LogWatch(on_block=lambda: calls.append(1))
        w.feed(self.OK.format(401))
        w.feed(self.OK.format(401))
        self.assertEqual(calls, [1])

    def test_classify_tool_errors(self):
        for message in YTDLP_WALLS:
            self.assertEqual(ig.classify(message), "login", message)
        self.assertEqual(ig.classify(DEAD_COOKIES), "login")
        self.assertEqual(ig.classify("HTTP Error 429: Too Many Requests"), "rate")
        for code in (400, 401, 403):
            self.assertEqual(ig.classify(f"ERROR: Unable to download: HTTP Error {code}: Refused"), "login")
        self.assertIsNone(ig.classify("ERROR: HTTP Error 404: Not Found"))
        self.assertIsNone(ig.classify("ERROR: Unsupported URL"))

    def test_the_login_wall_wins_when_a_message_names_both(self):
        self.assertEqual(ig.classify(WALL_RATE), "login")
        self.assertEqual(ig.classify(WALL_RATE + " HTTP Error 429: Too Many Requests"), "login")


class Tmp(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "video-digest"
        self.root.mkdir()
        for patcher in (mock.patch.object(ig, "GALLERY_DL", FAKE + ["gallery"]), mock.patch.object(ig, "YTDLP", FAKE + ["ytdlp"]),
                        mock.patch.object(ig, "MEDIA_DIGEST", FAKE + ["digest"]), mock.patch.object(ig, "PAUSE", (0, 0)),
                        mock.patch.object(ig, "SCREEN_TEXT", FAKE + ["screen"]), mock.patch.object(ig, "has_sound", return_value=False),
                        mock.patch.object(ig, "sound_of", fake_sound_of),
                        mock.patch.dict(os.environ, {"FAKE_RAN": str(self.root.parent / "ran"), "IG_DIGEST_HOME": str(self.root)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.printed = []
        patcher = mock.patch.object(ig, "log", self.printed.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile = self.root.parent / "firefox-profile"
        self.profile.mkdir()
        self.cookie_db(("sessionid", ".instagram.com")).close()
        patcher = mock.patch.object(ig, "firefox_profile", lambda: self.profile)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cookie_db(self, *rows, wal=False):
        """Firefox's cookies.sqlite; with wal=True the rows stay in the write-ahead log while the connection is open."""
        db = sqlite3.connect(self.profile / "cookies.sqlite")
        db.execute("CREATE TABLE IF NOT EXISTS moz_cookies (name TEXT, host TEXT)")
        db.commit()
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
        db.executemany("INSERT INTO moz_cookies VALUES (?, ?)", rows)
        db.commit()
        return db

    def lines(self, *metas, tail=()):
        f = self.root.parent / "lines.jsonl"
        f.write_text("\n".join(jsonl(*metas) + list(tail)), encoding="utf-8")
        return mock.patch.dict(os.environ, {"FAKE_LINES": str(f)})

    def index(self, handle="creator"):
        with open(self.root / handle / "index.csv", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def archive(self):
        db = sqlite3.connect(self.root / "archive.sqlite3")
        rows = dict(db.execute("SELECT shortcode, status FROM posts"))
        db.close()
        return rows


class Login(Tmp):
    def fresh(self):
        (self.profile / "cookies.sqlite").unlink()

    def test_a_login_still_in_the_write_ahead_log_is_found(self):
        self.fresh()
        self.cookie_db(("mid", ".instagram.com")).close()
        db = self.cookie_db(("sessionid", ".instagram.com"), wal=True)
        self.addCleanup(db.close)
        self.assertGreater((self.profile / "cookies.sqlite-wal").stat().st_size, 0)
        before = (self.profile / "cookies.sqlite").read_bytes()
        folder = ig.copy_login(self.root)
        self.assertEqual(folder, self.root / "cookies")
        copy = sqlite3.connect(folder / "cookies.sqlite")
        self.assertIn(("sessionid",), copy.execute("SELECT name FROM moz_cookies").fetchall())
        copy.close()
        self.assertEqual((self.profile / "cookies.sqlite").read_bytes(), before, "Firefox's file must stay untouched")

    def test_no_session_cookie_means_no_login(self):
        self.fresh()
        self.cookie_db(("mid", ".instagram.com"), ("sessionid", ".example.com")).close()
        self.assertIsNone(ig.copy_login(self.root))

    def test_no_firefox_profile_means_no_login(self):
        with mock.patch.object(ig, "firefox_profile", lambda: None):
            self.assertIsNone(ig.copy_login(self.root))

    def test_listing_without_a_login_makes_no_request(self):
        self.fresh()
        self.cookie_db(("mid", ".instagram.com")).close()
        with self.lines(meta("new1x")):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_LOGIN)
        self.assertEqual(self.printed[-1], ig.LOGIN_MSG)
        self.assertFalse((self.root.parent / "ran").exists())

    def test_transcribing_without_a_login_downloads_nothing(self):
        self.fresh()
        self.cookie_db(("mid", ".instagram.com")).close()
        self.assertEqual(ig.cmd_transcribe("creator", "reel1", 30, self.root), ig.EXIT_LOGIN)
        self.assertEqual(ig.media_files(self.root), [])

    def test_the_cookie_copy_is_gone_after_each_command(self):
        with self.lines(meta("new1x")):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertFalse((self.root / ".tmp" / "cookies").exists())
        ig.cmd_transcribe("creator", "new1x", 30, self.root)
        self.assertFalse((self.root / ".tmp" / "cookies").exists())

    def test_the_tools_get_the_copy(self):
        cmd = ig.gallery_cmd("creator", Path("C:/x/cookies"))
        self.assertIn(f"firefox:{Path('C:/x/cookies')}", cmd)
        self.assertEqual(cmd[-1], "https://www.instagram.com/creator/posts/")


class Cleanup(Tmp):
    def test_clean_tmp_touches_only_its_own_folder(self):
        keep = self.root / "creator" / "transcripts" / "keep.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("x", encoding="utf-8")
        (self.root / "archive.sqlite3").write_text("x", encoding="utf-8")
        left = self.root / ".tmp" / "abcde1" / "abcde1.m4a"
        left.parent.mkdir(parents=True)
        left.write_bytes(b"a")
        tmp = ig.clean_tmp(self.root)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertTrue(keep.exists() and (self.root / "archive.sqlite3").exists())

    def test_media_files_finds_partial_downloads(self):
        (self.root / "a.m4a.part").write_bytes(b"a")
        (self.root / "b.MP4").write_bytes(b"a")
        (self.root / "c.md").write_bytes(b"a")
        self.assertEqual(sorted(p.name for p in ig.media_files(self.root)), ["a.m4a.part", "b.MP4"])


class ListRuns(Tmp):
    REQ = '[urllib3.connectionpool][debug] https://www.instagram.com:443 "GET /api/v1/feed/ HTTP/1.1" 200 None'

    def test_first_run_then_rerun_finds_nothing_new(self):
        posts = [meta("pin1x", 900, pinned=True), meta("new1x", 1), meta("new2x", 2), meta("new3x", 3), meta("new4x", 4),
                 meta("old1x", 200), meta("old2x", 201), meta("old3x", 202)]
        with self.lines(*posts), mock.patch.dict(os.environ, {"FAKE_LOG": self.REQ + "\n" + self.REQ}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertIn("rows=4 new=4 known=0 requests=2", self.printed[-1])
        self.assertIn("older than", self.printed[-1])
        self.assertEqual([r["shortcode"] for r in self.index()], ["new1x", "new2x", "new3x", "new4x"])
        self.assertEqual({r["archive"] for r in self.index()}, {"new"})

        with self.lines(*posts), mock.patch.dict(os.environ, {"FAKE_LOG": self.REQ}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertIn("rows=3 new=0 known=3 requests=1", self.printed[-1])
        self.assertIn("already-known", self.printed[-1])
        self.assertEqual(len(self.index()), 4, "a short rerun must not shrink the index")

    def test_new_post_is_found_on_a_weekly_rerun(self):
        with self.lines(meta("new1x", 8), meta("new2x", 9), meta("new3x", 10)):
            ig.cmd_list("creator", 180, self.root, NOW)
        with self.lines(meta("week1", 1), meta("new1x", 8), meta("new2x", 9), meta("new3x", 10)):
            ig.cmd_list("creator", 180, self.root, NOW + timedelta(days=7))
        self.assertIn("rows=4 new=1 known=3", self.printed[-1])
        self.assertEqual(self.index()[0]["shortcode"], "week1")

    def test_a_wider_window_ignores_known_posts(self):
        with self.lines(meta("new1x", 1), meta("new2x", 2), meta("new3x", 3)):
            ig.cmd_list("creator", 30, self.root, NOW)
        with self.lines(meta("new1x", 1), meta("new2x", 2), meta("new3x", 3), meta("mid1x", 60)):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertIn("rows=4 new=1", self.printed[-1])

    def test_the_rule_ends_a_running_gallery_dl(self):
        with self.lines(meta("old1x", 200), meta("old2x", 201), meta("old3x", 202)), \
                mock.patch.dict(os.environ, {"FAKE_HANG": "1"}):
            started = datetime.now()
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertLess((datetime.now() - started).total_seconds(), 20)

    def test_login_wall_exits_3_and_saves_nothing(self):
        wall = "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_LOG": wall, "FAKE_RC": "4"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_LOGIN)
        self.assertEqual(self.printed[-1], ig.LOGIN_MSG)
        self.assertEqual(self.archive(), {})
        self.assertFalse((self.root / "creator" / "index.csv").exists())

    def test_a_run_of_429_exits_4_while_gallery_dl_hangs(self):
        req429 = self.REQ.replace(" 200 ", " 429 ")
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_LOG": "\n".join([req429] * 3), "FAKE_HANG": "1"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_RATE)
        self.assertEqual(self.archive(), {})

    def test_two_429s_end_gallery_dl_and_count_as_rate_limited(self):
        req429 = self.REQ.replace(" 200 ", " 429 ")
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_LOG": "\n".join([req429] * 2), "FAKE_RC": "4"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_RATE)
        self.assertEqual(self.printed[-1], ig.RATE_MSG)

    def test_a_checkpoint_only_in_the_reply_body_is_a_login_wall(self):
        body = '{"message": "checkpoint_required", "status": "fail"}'
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_PAGE": body, "FAKE_RC": "4"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_LOGIN)
        self.assertEqual(self.archive(), {})

    def test_a_slow_down_only_in_the_reply_body_is_rate_limiting(self):
        body = '{"message": "Please wait a few minutes before you try again."}'
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_PAGE": body, "FAKE_RC": "4"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_RATE)

    CHECKPOINT = '{"message": "checkpoint_required", "status": "fail"}'
    SLOW_DOWN = '{"message": "Please wait a few minutes before you try again.", "status": "fail"}'
    HEALTHY = json.dumps({"data": {"caption": "wait a few minutes, checkpoint_required, then post"}, "status": "ok"})

    def listing(self, pages, rc="4", log=""):
        """A failing gallery-dl run that saved these replies, as [[number, body], ...]; returns the exit code."""
        env = {"FAKE_PAGES": json.dumps(pages), "FAKE_RC": rc, "FAKE_LOG": log}
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, env):
            code = ig.cmd_list("creator", 180, self.root, NOW)
        self.assertEqual(self.archive(), {})
        return code

    def test_only_the_last_reply_decides(self):
        script = '<script>var s = "checkpoint_required";</script>'
        req429 = self.REQ.replace(" 200 ", " 429 ")
        self.assertEqual(self.listing([[1, self.HEALTHY], [2, "Internal Server Error"]], rc="1"), ig.EXIT_FAIL)
        self.assertEqual(self.listing([[1, script], [2, self.SLOW_DOWN]]), ig.EXIT_RATE)
        self.assertEqual(self.listing([[1, self.CHECKPOINT]], log=req429), ig.EXIT_LOGIN, "a checkpoint beats one earlier 429")

    def test_two_429s_still_count_when_the_last_reply_refuses_nothing(self):
        req429 = self.REQ.replace(" 200 ", " 429 ")
        self.assertEqual(self.listing([[1, self.HEALTHY]], log="\n".join([req429] * 2)), ig.EXIT_RATE)

    def test_a_healthy_last_reply_with_a_failure_that_is_not_http_is_a_plain_failure(self):
        self.assertEqual(self.listing([[1, self.HEALTHY]], rc="1"), ig.EXIT_FAIL)

    def test_a_json_reply_is_judged_by_its_message_only(self):
        body = json.dumps({"message": "Something broke", "items": ["checkpoint_required", "wait a few minutes"]})
        self.assertEqual(self.listing([[1, body]], rc="1"), ig.EXIT_FAIL)

    def test_reply_numbers_are_sorted_as_numbers(self):
        self.assertEqual(self.listing([[99, self.CHECKPOINT], [100, self.SLOW_DOWN]]), ig.EXIT_RATE)
        self.assertEqual(self.listing([[9, self.SLOW_DOWN], [10, self.CHECKPOINT]]), ig.EXIT_LOGIN)

    def test_a_stale_reply_from_a_killed_run_is_not_read(self):
        stale = self.root / ".tmp" / "pages" / "99_https_www.instagram.com_graphql_query.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text(self.CHECKPOINT, encoding="utf-8")
        self.assertEqual(self.listing([[1, self.HEALTHY]], rc="1"), ig.EXIT_FAIL)

    def test_a_stale_reply_does_not_change_the_comment_counts(self):
        page = (HERE / "fixtures" / "graphql_page.json").read_text(encoding="utf-8")
        stale = self.root / ".tmp" / "pages" / "07_https_www.instagram.com_graphql_query.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text(page, encoding="utf-8")
        with self.lines(meta("FIXTURE0001")):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertEqual(self.index()[0]["comments"], "")

    def test_a_locked_index_loses_nothing(self):
        posts = [meta(f"new{n}x", n) for n in range(1, 8)]
        with self.lines(*posts):
            with mock.patch.object(ig, "write_index", side_effect=PermissionError(13, "locked", "index.csv")):
                with self.assertRaises(PermissionError):
                    ig.cmd_list("creator", 180, self.root, NOW)
            self.assertEqual(self.archive(), {}, "the archive must not claim posts the index lacks")
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertEqual(len(self.index()), 7)

    def test_main_explains_a_locked_file(self):
        with mock.patch.object(ig, "cmd_list", side_effect=PermissionError(13, "locked", "index.csv")):
            self.assertEqual(ig.main(["list", "--handle", "creator"]), ig.EXIT_FAIL)
        self.assertIn("close it", self.printed[-1])

    def test_a_reel_gallery_dl_calls_a_post_is_a_reel(self):
        page = (HERE / "fixtures" / "graphql_page.json").read_text(encoding="utf-8")
        m = meta("FIXTURE0001", type="post", post_url="https://www.instagram.com/p/FIXTURE0001/")
        with self.lines(m), mock.patch.dict(os.environ, {"FAKE_PAGE": page}):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertEqual(self.index()[0]["type"], "reel")
        self.assertEqual(self.index()[0]["url"], "https://www.instagram.com/reel/FIXTURE0001/")

    def test_reels_transcribed_by_url_do_not_end_a_listing(self):
        with self.lines(meta("old1x", 9)):
            ig.cmd_list("creator", 180, self.root, NOW)
        ig.cmd_transcribe("creator", "top1x,top2x,top3x", 30, self.root)
        with self.lines(meta("top1x", 1), meta("top2x", 2), meta("top3x", 3), meta("mid1x", 5), meta("old1x", 9)):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertEqual(self.archive()["mid1x"], "new")

    def test_a_collab_post_is_handled_once(self):
        with self.lines(meta("both1")):
            ig.cmd_list("alice", 180, self.root, NOW)
        ig.cmd_transcribe("alice", "both1", 30, self.root)
        with self.lines(meta("both1")):
            ig.cmd_list("bob", 180, self.root, NOW)
        self.assertIn("new=0", self.printed[-1])
        self.assertEqual(self.index("bob")[0]["archive"], "transcribed")

    def test_unknown_handle_fails_plainly(self):
        gone = "[instagram][error] NotFoundError: Requested user could not be found"
        with self.lines(), mock.patch.dict(os.environ, {"FAKE_LOG": gone, "FAKE_RC": "16"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_FAIL)
        self.assertTrue(any("could not be found" in p for p in self.printed))

    def test_a_failure_halfway_is_not_the_end_of_the_profile(self):
        with self.lines(meta("new1x"), meta("new2x")), mock.patch.dict(os.environ, {"FAKE_RC": "1"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), ig.EXIT_FAIL)
        self.assertEqual(self.archive(), {})
        self.assertFalse((self.root / "creator" / "index.csv").exists())

    def test_comment_counts_come_from_the_saved_reply_and_are_deleted(self):
        page = (HERE / "fixtures" / "graphql_page.json").read_text(encoding="utf-8")
        with self.lines(meta("FIXTURE0001"), meta("other")), mock.patch.dict(os.environ, {"FAKE_PAGE": page}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        rows = {r["shortcode"]: r for r in self.index()}
        self.assertEqual((rows["FIXTURE0001"]["comments"], rows["other"]["comments"]), ("7", ""))
        self.assertEqual(rows["FIXTURE0001"]["views"], "")
        self.assertFalse((self.root / ".tmp" / "pages").exists())

    def test_a_broken_saved_reply_is_skipped(self):
        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_PAGE": "<html>not json"}):
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertEqual(self.index()[0]["comments"], "")

    def test_a_profile_with_no_posts_is_fine(self):
        with self.lines():
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertIn("rows=0", self.printed[-1])


class TranscribeRuns(Tmp):
    def listed(self, *codes):
        with self.lines(*[meta(c, n + 1) for n, c in enumerate(codes)]):
            ig.cmd_list("creator", 180, self.root, NOW)

    def test_three_picks_leave_text_and_no_media(self):
        self.listed("reel1", "reel2", "reel3", "reel4")
        self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2,reel3", 30, self.root), 0)
        for code in ("reel1", "reel2", "reel3"):
            text = (self.root / "creator" / "transcripts" / f"{code}.md").read_text(encoding="utf-8")
            self.assertIn(f"[00:01] words from {code}", text)
            self.assertIn(f"https://www.instagram.com/reel/{code}/", text)
        self.assertEqual(ig.media_files(self.root), [])
        self.assertEqual(list((self.root / ".tmp").iterdir()), [])
        self.assertEqual(self.archive()["reel1"], "transcribed")
        self.assertEqual(self.archive()["reel4"], "listed")
        self.assertEqual(self.index()[0]["archive"], "transcribed")
        self.assertIn("transcribed=3 no_speech=0 failed=0 tmp_media=0", self.printed[-1])

    def test_the_transcriber_runs_below_normal(self):
        self.listed("reel1")
        ig.cmd_transcribe("creator", "reel1", 30, self.root)
        line = next(p for p in self.printed if p.startswith("transcriber"))
        self.assertIn("threads=2", line)
        if os.name == "nt":
            self.assertIn("priority=BELOW_NORMAL", line)

    def test_leftovers_are_removed_on_start(self):
        stale = self.root / ".tmp" / "stale" / "stale.m4a"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"a")
        self.listed("reel1")
        ig.cmd_transcribe("creator", "reel1", 30, self.root)
        self.assertFalse(stale.exists())

    def test_done_reels_are_skipped(self):
        self.listed("reel1", "reel2")
        ig.cmd_transcribe("creator", "reel1", 30, self.root)
        self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2", 30, self.root), 0)
        self.assertIn("skipped_done=1 transcribed=1", self.printed[-1])

    def test_the_cap_holds(self):
        self.listed("reel1", "reel2", "reel3")
        ig.cmd_transcribe("creator", "reel1,reel2,reel3", 2, self.root)
        self.assertIn("transcribed=2", self.printed[-1])
        self.assertTrue(any("capped at 2" in p and "reel3" in p for p in self.printed))

    def test_no_speech_is_recorded_not_written(self):
        self.listed("reel1", "reel2")
        with mock.patch.dict(os.environ, {"FAKE_SILENT": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2", 30, self.root), 0)
        self.assertFalse((self.root / "creator" / "transcripts" / "reel1.md").exists())
        self.assertEqual(self.archive()["reel1"], "no_speech")

    def test_a_failed_download_does_not_stop_the_others(self):
        self.listed("reel1", "reel2")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": "ERROR: Unsupported URL"}):
            self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2", 30, self.root), ig.EXIT_FAIL)
        self.assertIn("transcribed=1 no_speech=0 failed=1", self.printed[-1])
        self.assertEqual(self.archive()["reel1"], "new", "a failed pick keeps its place for the next run")

    def test_a_refused_download_stops_at_once(self):
        self.listed("reel1", "reel2", "reel3")
        env = {"FAKE_BAD": "reel1,reel2,reel3", "FAKE_YTDLP_ERROR": "ERROR: Unable to download: HTTP Error 403: Forbidden"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2,reel3", 30, self.root), ig.EXIT_LOGIN)
        self.assertEqual(sum("audio" in p or "reel" in p for p in self.printed[:-2]), 1)

    def test_three_failures_in_a_row_stop_the_run(self):
        codes = [f"reel{n}" for n in range(1, 7)]
        self.listed(*codes)
        with mock.patch.dict(os.environ, {"FAKE_BAD": ",".join(codes), "FAKE_YTDLP_ERROR": "ERROR: Unsupported URL"}):
            self.assertEqual(ig.cmd_transcribe("creator", ",".join(codes), 30, self.root), ig.EXIT_FAIL)
        self.assertIn("failed=3", self.printed[-1])
        self.assertEqual(self.archive()["reel6"], "new", "an unfinished run must not close the triage")

    def test_a_weekly_triage_sees_only_what_is_new(self):
        self.listed("reel1", "reel2", "reel3", "reel4")
        ig.cmd_transcribe("creator", "reel1", 30, self.root)
        self.assertEqual({r["shortcode"]: r["archive"] for r in self.index()},
                         {"reel1": "transcribed", "reel2": "listed", "reel3": "listed", "reel4": "listed"})
        with self.lines(meta("week1", 1), meta("reel1", 2), meta("reel2", 3), meta("reel3", 4)):
            ig.cmd_list("creator", 180, self.root, NOW + timedelta(days=7))
        self.assertEqual([r["shortcode"] for r in self.index() if r["archive"] == "new"], ["week1"])

    def test_capped_picks_stay_new(self):
        self.listed("reel1", "reel2", "reel3")
        ig.cmd_transcribe("creator", "reel1,reel2", 1, self.root)
        self.assertEqual(self.archive()["reel2"], "new")
        self.assertEqual(self.archive()["reel3"], "listed")

    def test_a_locked_index_at_the_end_of_a_transcribe_run_is_only_a_note(self):
        self.listed("reel1")
        with mock.patch.object(ig, "write_index", side_effect=PermissionError(13, "locked", "index.csv")):
            self.assertEqual(ig.cmd_transcribe("creator", "reel1", 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "transcribed")

    def test_a_login_wall_stops_downloads_but_keeps_what_arrived(self):
        self.listed("reel1", "reel2", "reel3")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel2", "FAKE_YTDLP_ERROR": YTDLP_WALLS[1]}):
            self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2,reel3", 30, self.root), ig.EXIT_LOGIN)
        self.assertEqual(self.printed[-1], ig.LOGIN_MSG)
        self.assertEqual(self.archive()["reel1"], "transcribed")
        self.assertEqual(self.archive()["reel3"], "new")
        self.assertEqual(ig.media_files(self.root), [])

    def test_every_real_yt_dlp_login_wall_stops_after_one_attempt(self):
        codes = ["reel1", "reel2", "reel3", "reel4"]
        self.listed(*codes)
        for message in YTDLP_WALLS:
            with self.subTest(message=message[:90]), mock.patch.object(ig, "download_audio", wraps=ig.download_audio) as tries, \
                    mock.patch.dict(os.environ, {"FAKE_BAD": ",".join(codes), "FAKE_YTDLP_ERROR": message}):
                self.assertEqual(ig.cmd_transcribe("creator", ",".join(codes), 30, self.root), ig.EXIT_LOGIN)
                self.assertEqual(tries.call_count, 1)
                self.assertEqual(self.printed[-1], ig.LOGIN_MSG)
                self.assertEqual({self.archive()[c] for c in codes}, {"new"})

    def test_a_404_and_an_unrelated_error_are_ordinary_failures(self):
        self.listed("reel1", "reel2", "reel3")
        for message in ("ERROR: [Instagram] reel1: Unable to download: HTTP Error 404: Not Found", "ERROR: Unsupported URL"):
            with self.subTest(message=message), mock.patch.object(ig, "download_audio", wraps=ig.download_audio) as tries, \
                    mock.patch.dict(os.environ, {"FAKE_BAD": "reel1,reel2,reel3", "FAKE_YTDLP_ERROR": message}):
                self.assertEqual(ig.cmd_transcribe("creator", "reel1,reel2,reel3", 30, self.root), ig.EXIT_FAIL)
                self.assertEqual(tries.call_count, ig.MAX_FAILS)

    def test_a_dead_cookie_warning_with_a_file_is_a_login_wall(self):
        codes = ["reel1", "reel2", "reel3"]
        self.listed(*codes)
        with mock.patch.object(ig, "download_audio", wraps=ig.download_audio) as tries, \
                mock.patch.dict(os.environ, {"FAKE_BAD": ",".join(codes), "FAKE_YTDLP_WARNING": DEAD_COOKIES}):
            self.assertEqual(ig.cmd_transcribe("creator", ",".join(codes), 30, self.root), ig.EXIT_LOGIN)
        self.assertEqual(tries.call_count, 1)
        self.assertEqual(self.printed[-1], ig.LOGIN_MSG)
        self.assertEqual({self.archive()[c] for c in codes}, {"new"})
        self.assertEqual(ig.media_files(self.root), [])
        self.assertFalse((self.root / "creator" / "transcripts" / "reel1.md").exists())

    def test_download_audio_refuses_a_file_that_came_with_the_dead_cookie_warning(self):
        url = "https://www.instagram.com/reel/reel1/"
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_WARNING": DEAD_COOKIES}):
            path, error = ig.download_audio("reel1", url, self.root / ".tmp" / "reel1", self.root)
        self.assertIsNone(path)
        self.assertEqual(ig.classify(error), "login")
        self.assertIn("cookies are no longer valid", error)
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_WARNING": "WARNING: [instagram] reel1: something else"}):
            path, error = ig.download_audio("reel1", url, self.root / ".tmp" / "other", self.root)
        self.assertEqual((path.name, error), ("reel1.m4a", ""), "any other warning on a good download stays a success")

    def test_a_pick_that_was_never_listed_still_works(self):
        self.assertEqual(ig.cmd_transcribe("creator", "reel9", 30, self.root), 0)
        self.assertEqual(self.archive()["reel9"], "transcribed")

    def test_all_takes_every_reel_even_ones_an_earlier_run_left_behind(self):
        photo = meta("photo1", 5, type="post", post_url="https://www.instagram.com/p/photo1/")
        with self.lines(meta("reel1", 1), meta("reel2", 2), meta("reel3", 3), photo):
            ig.cmd_list("creator", 180, self.root, NOW)
        ig.cmd_transcribe("creator", "reel1", 30, self.root)
        self.assertEqual(self.archive()["reel2"], "listed")
        with mock.patch.object(ig, "download_audio", wraps=ig.download_audio) as tries:
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(sorted(c.args[0] for c in tries.call_args_list), ["reel2", "reel3"], "reel1 is done, photo1 is no reel")
        self.assertEqual({self.archive()[c] for c in ("reel1", "reel2", "reel3")}, {"transcribed"})
        self.assertEqual(self.archive()["photo1"], "no_speech")
        self.assertIn("left=0", self.printed[-1])

    def test_all_over_the_cap_finishes_on_the_next_run(self):
        self.listed("reel1", "reel2", "reel3")
        self.assertEqual(ig.cmd_transcribe("creator", None, 2, self.root), 0)
        self.assertIn("transcribed=2", self.printed[-1])
        self.assertIn("left=1", self.printed[-1])
        self.assertEqual(ig.cmd_transcribe("creator", None, 2, self.root), 0)
        self.assertIn("transcribed=1", self.printed[-1])
        self.assertIn("left=0", self.printed[-1])
        self.assertEqual(set(self.archive().values()), {"transcribed"})

    def test_all_comes_back_for_a_reel_that_failed(self):
        self.listed("reel1", "reel2")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": "ERROR: Unsupported URL"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "transcribed")

    def test_a_reel_with_no_audio_track_is_no_speech_and_never_tried_again(self):
        self.listed("reel1", "reel2")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": NO_AUDIO}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive(), {"reel1": "no_speech", "reel2": "transcribed"})
        self.assertIn("transcribed=1 no_speech=1 failed=0", self.printed[-1])
        with mock.patch.object(ig, "download_audio", wraps=ig.download_audio) as tries:
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(tries.call_count, 0)

    def test_no_audio_tracks_do_not_count_as_failures_in_a_row(self):
        codes = [f"reel{n}" for n in range(1, 6)]
        self.listed(*codes)
        with mock.patch.dict(os.environ, {"FAKE_BAD": ",".join(codes[:4]), "FAKE_YTDLP_ERROR": NO_AUDIO}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertIn("transcribed=1 no_speech=4 failed=0", self.printed[-1])

    def test_transcribe_needs_exactly_one_of_all_and_picks(self):
        for argv in (["transcribe", "--handle", "creator"],
                     ["transcribe", "--handle", "creator", "--all", "--picks", "reel1"]):
            with self.subTest(argv=argv), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                ig.main(argv)

    def test_all_on_the_command_line_reaches_the_run(self):
        with mock.patch.object(ig, "cmd_transcribe", return_value=0) as run:
            self.assertEqual(ig.main(["transcribe", "--handle", "creator", "--all"]), 0)
        run.assert_called_once_with("creator", None, 30)

    def transcript(self, code):
        return (self.root / "creator" / "transcripts" / f"{code}.md").read_text(encoding="utf-8")

    def forget_screen_text(self):
        """The state of a reel read before on-screen text existed."""
        db = sqlite3.connect(self.root / "archive.sqlite3")
        db.execute("UPDATE posts SET screen_at = NULL")
        db.commit()
        db.close()

    def test_on_screen_text_goes_under_the_speech(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        text = self.transcript("reel1")
        self.assertLess(text.index("[00:01] words from reel1"), text.index("## On screen"))
        self.assertIn("[00:02] shown in reel1", text)
        self.assertIn("screen=1", self.printed[-1])
        self.assertEqual(ig.media_files(self.root), [])

    def test_a_silent_reel_with_text_on_screen_gets_a_file(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_SILENT": "reel1", "FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "no_speech")
        self.assertIn("[00:02] shown in reel1", self.transcript("reel1"))

    def test_a_reel_with_no_sound_track_is_still_read_for_on_screen_text(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": NO_AUDIO, "FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "no_speech")
        self.assertIn("[00:02] shown in reel1", self.transcript("reel1"))

    def test_a_reel_read_earlier_only_gains_the_on_screen_text(self):
        self.listed("reel1")
        ig.cmd_transcribe("creator", None, 30, self.root)
        self.forget_screen_text()
        with mock.patch.object(ig, "run_media_digest") as speech, mock.patch.dict(os.environ, {"FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        speech.assert_not_called()
        text = self.transcript("reel1")
        self.assertIn("[00:01] words from reel1", text)
        self.assertEqual(text.count("## On screen"), 1)
        self.assertEqual(self.archive()["reel1"], "transcribed")

    def test_on_screen_text_is_never_written_twice(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_SCREEN": "reel1"}):
            ig.cmd_transcribe("creator", None, 30, self.root)
            self.forget_screen_text()
            ig.cmd_transcribe("creator", None, 30, self.root)
        self.assertEqual(self.transcript("reel1").count("## On screen"), 1)

    def test_a_reel_whose_screen_text_failed_comes_back_without_new_speech_work(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_SCREEN_BROKEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertEqual(self.archive()["reel1"], "transcribed")
        with mock.patch.object(ig, "run_media_digest") as speech, mock.patch.dict(os.environ, {"FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        speech.assert_not_called()
        self.assertIn("[00:02] shown in reel1", self.transcript("reel1"))

    def test_no_ocr_tool_means_no_instagram_requests(self):
        self.listed("reel1")
        with mock.patch.object(ig, "download_audio") as tries, mock.patch.dict(os.environ, {"FAKE_NO_OCR": "1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        tries.assert_not_called()
        self.assertTrue(any("rapidocr" in p for p in self.printed))

    def photo(self, *codes):
        posts = [meta(c, n + 1, type="post", post_url=f"https://www.instagram.com/p/{c}/") for n, c in enumerate(codes)]
        with self.lines(*posts):
            ig.cmd_list("creator", 180, self.root, NOW)

    def test_a_photo_post_is_read_for_its_text(self):
        self.photo("photo1", "photo2")
        with mock.patch.object(ig, "download_audio") as reel, mock.patch.dict(os.environ, {"FAKE_SCREEN": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        reel.assert_not_called()
        self.assertIn("[00:02] shown in photo1", self.transcript("photo1"))
        self.assertEqual(self.archive(), {"photo1": "no_speech", "photo2": "no_speech"})
        frames = self.root / "creator" / "frames"
        self.assertEqual([p for p in ig.media_files(self.root) if frames not in p.parents], [])  # only Claude's frames stay
        self.assertTrue((frames / "photo1" / "photo1_1.jpg").exists())
        (self.root / "creator" / "cards").mkdir(parents=True, exist_ok=True)
        (self.root / "creator" / "cards" / "photo1.md").write_text("card", encoding="utf-8")
        self.assertEqual(ig.cmd_drop_frames("creator", self.root), 0)
        self.assertFalse((frames / "photo1").exists())  # its card exists: frames gone
        self.assertTrue((frames / "photo2").exists())  # no card yet: frames wait

    def test_reels_go_first_when_the_cap_bites(self):
        with self.lines(meta("photo1", 1, type="post", post_url="https://www.instagram.com/p/photo1/"), meta("reel1", 2)):
            ig.cmd_list("creator", 180, self.root, NOW)
        ig.cmd_transcribe("creator", None, 1, self.root)
        self.assertEqual(self.archive()["reel1"], "transcribed")
        self.assertIn("left=1", self.printed[-1])

    def test_a_refused_post_download_stops_the_run(self):
        self.photo("photo1", "photo2")
        env = {"FAKE_BAD": "photo1", "FAKE_GALLERY_ERROR": "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"}
        with mock.patch.object(ig, "download_post", wraps=ig.download_post) as tries, mock.patch.dict(os.environ, env):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_LOGIN)
        self.assertEqual(tries.call_count, 1)

    def test_a_post_that_fails_stays_on_the_list(self):
        self.photo("photo1")
        with mock.patch.dict(os.environ, {"FAKE_BAD": "photo1", "FAKE_GALLERY_ERROR": "[instagram][error] NotFoundError"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["photo1"], "no_speech")

    def test_a_video_slide_in_a_carousel_is_transcribed(self):
        self.photo("photo1")
        with mock.patch.object(ig, "has_sound", return_value=True), mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        text = self.transcript("photo1")
        self.assertLess(text.index("### Slide 2"), text.index("### Slide 10"))
        self.assertIn("[00:01] words from photo1_2", text)
        self.assertEqual(self.archive()["photo1"], "transcribed")

    def test_a_carousel_with_silent_videos_never_reaches_the_transcriber(self):
        self.photo("photo1")
        with mock.patch.object(ig, "has_sound", return_value=False), mock.patch.object(ig, "run_media_digest") as speech, \
                mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        speech.assert_not_called()
        self.assertEqual(self.archive()["photo1"], "no_speech")

    def test_a_carousel_whose_speech_failed_stays_on_the_list(self):
        self.photo("photo1")
        with mock.patch.object(ig, "has_sound", return_value=True), mock.patch.object(ig, "run_media_digest"), \
                mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertNotIn(self.archive()["photo1"], ig.HAS_RESULT)
        self.assertIn("left=1", self.printed[-1])

    def test_gallery_dl_refusals_on_a_post_stop_the_run_at_once(self):
        api = "https://www.instagram.com/api/v1/media/1/info/"
        cases = ((f"[instagram][error] HttpError: '401 Unauthorized' for '{api}'", ig.EXIT_LOGIN),
                 (f"[instagram][error] HttpError: '403 Forbidden' for '{api}'", ig.EXIT_LOGIN),
                 (f"[instagram][error] HttpError: '429 Too Many Requests' for '{api}'", ig.EXIT_RATE))
        self.photo("photo1", "photo2", "photo3", "photo4")
        for message, code in cases:
            with self.subTest(message=message), mock.patch.object(ig, "download_post", wraps=ig.download_post) as tries, \
                    mock.patch.dict(os.environ, {"FAKE_BAD": "photo1,photo2,photo3,photo4", "FAKE_GALLERY_ERROR": message}):
                self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), code)
                self.assertEqual(tries.call_count, 1)

    def test_a_400_is_judged_by_the_reply_it_saved(self):
        api = "https://www.instagram.com/api/v1/media/1/info/"
        message = f"[instagram][error] HttpError: '400 Bad Request' for '{api}'"
        self.photo("photo1", "photo2", "photo3", "photo4")
        for body, code, attempts in (('{"message": "checkpoint_required", "status": "fail"}', ig.EXIT_LOGIN, 1),
                                     ('{"message": "feedback_required", "status": "fail"}', ig.EXIT_RATE, 1),
                                     ('{"message": "Invalid media_id", "status": "fail"}', ig.EXIT_FAIL, ig.MAX_FAILS)):
            env = {"FAKE_BAD": "photo1,photo2,photo3,photo4", "FAKE_GALLERY_ERROR": message, "FAKE_PAGE": body}
            with self.subTest(body=body), mock.patch.object(ig, "download_post", wraps=ig.download_post) as tries, \
                    mock.patch.dict(os.environ, env):
                self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), code)
                self.assertEqual(tries.call_count, attempts)

    def test_a_refused_slide_on_the_picture_server_is_only_a_failed_post(self):
        self.assertIsNone(ig.classify("[download][error] '403 Forbidden' for 'https://scontent-sin6-1.cdninstagram.com/v/t51/1.jpg'"))

    def test_a_post_with_an_unreadable_video_stays_on_the_list(self):
        self.photo("photo1")
        with mock.patch.object(ig, "has_sound", return_value=None), mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertNotIn(self.archive()["photo1"], ig.HAS_RESULT)
        with mock.patch.object(ig, "has_sound", return_value=True), mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["photo1"], "transcribed")

    def test_a_video_whose_sound_cannot_be_pulled_out_stays_on_the_list(self):
        self.photo("photo1")
        with mock.patch.object(ig, "has_sound", return_value=True), mock.patch.object(ig, "sound_of", return_value=None), \
                mock.patch.dict(os.environ, {"FAKE_CAROUSEL": "photo1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertNotIn(self.archive()["photo1"], ig.HAS_RESULT)

    def test_sound_of_keeps_the_slide_name_and_drops_the_picture(self):
        for code, expected in ((0, Path("photo1_2.wav")), (1, None)):
            with self.subTest(code=code), mock.patch.object(ig.subprocess, "run", return_value=mock.Mock(returncode=code)) as run:
                self.assertEqual(SOUND_OF(Path("photo1_2.mp4")), expected)
            self.assertIn("-vn", run.call_args[0][0])

    def test_a_broken_reel_is_a_failure_not_a_silent_reel(self):
        self.listed("reel1")
        with mock.patch.object(ig, "has_sound", return_value=None), \
                mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": NO_AUDIO}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertNotIn(self.archive()["reel1"], ig.HAS_RESULT)

    def test_has_sound_tells_a_broken_file_from_a_silent_one(self):
        for code, out, expected in ((1, "", None), (0, "", False), (0, "audio\n", True)):
            with self.subTest(code=code, out=out), mock.patch.object(ig.subprocess, "run", return_value=mock.Mock(returncode=code, stdout=out)):
                self.assertIs(HAS_SOUND(Path("x.mp4")), expected)

    def test_a_shortcode_ending_in_digits_gets_no_slide_heading(self):
        body = "[00:01] words"
        self.assertEqual(ig.spoken("Cabc_12", [Path("Cabc_12.m4a")], [body]), body)
        self.assertEqual(ig.spoken("Cabc", [Path("Cabc_12.mp4")], [body]), f"### Slide 12\n{body}")

    def test_a_missing_slide_is_said_out_loud(self):
        self.photo("photo1")
        warning = "[instagram][warning] Missing media in post photo1"
        with mock.patch.dict(os.environ, {"FAKE_GALLERY_WARNING": warning}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertTrue(any("Missing media" in p for p in self.printed))

    def test_dead_posts_cannot_hold_up_the_rest(self):
        codes = [f"reel{n}" for n in range(1, 7)]
        self.listed(*codes)
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1,reel2,reel3", "FAKE_YTDLP_ERROR": "ERROR: Unsupported URL"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
            self.assertIn("left=6", self.printed[-1])
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), ig.EXIT_FAIL)
        self.assertEqual({self.archive()[c] for c in codes[3:]}, {"transcribed"})
        self.assertIn("left=3", self.printed[-1])

    def test_a_reel_that_only_has_the_audio_only_format_still_gets_its_speech(self):
        self.listed("reel1")
        with mock.patch.dict(os.environ, {"FAKE_SPLIT": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "transcribed")

    def test_a_reel_that_lost_its_sound_keeps_its_status(self):
        self.listed("reel1")
        ig.cmd_transcribe("creator", None, 30, self.root)
        self.forget_screen_text()
        with mock.patch.dict(os.environ, {"FAKE_BAD": "reel1", "FAKE_YTDLP_ERROR": NO_AUDIO, "FAKE_SCREEN": "reel1"}):
            self.assertEqual(ig.cmd_transcribe("creator", None, 30, self.root), 0)
        self.assertEqual(self.archive()["reel1"], "transcribed")
        self.assertIn("[00:02] shown in reel1", self.transcript("reel1"))

    def test_speech_that_arrives_late_keeps_the_on_screen_text(self):
        path = self.root / "creator" / "transcripts" / "reel1.md"
        ig.add_screen_text(path, "reel1", "creator", {}, [["00:02", "shown first"]])
        ig.write_transcript(path, "reel1", "creator", {}, "[00:01] words later")
        text = path.read_text(encoding="utf-8")
        self.assertLess(text.index("[00:01] words later"), text.index("[00:02] shown first"))
        self.assertEqual(text.count("## On screen"), 1)

    def test_a_caption_with_the_heading_in_it_does_not_block_the_section(self):
        path = self.root / "creator" / "transcripts" / "reel1.md"
        ig.write_transcript(path, "reel1", "creator", {"caption": "see ## On screen for more"}, "[00:01] words")
        ig.add_screen_text(path, "reel1", "creator", {}, [["00:02", "shown"]])
        self.assertIn("[00:02] shown", path.read_text(encoding="utf-8"))

    def test_an_archive_from_before_screen_text_is_upgraded(self):
        db = sqlite3.connect(self.root / "archive.sqlite3")
        db.execute("""CREATE TABLE posts (shortcode TEXT PRIMARY KEY, handle TEXT NOT NULL, listed_at TEXT NOT NULL,
                                          status TEXT NOT NULL DEFAULT 'listed', transcribed_at TEXT)""")
        db.execute("INSERT INTO posts VALUES ('reel1', 'creator', '2026-11-04', 'transcribed', '2026-11-04')")
        db.commit()
        db.close()
        archive = ig.Archive(self.root / "archive.sqlite3")
        self.assertEqual(archive.screened(), set())
        archive.mark_screen("creator", "reel1", NOW)
        self.assertEqual(archive.screened(), {"reel1"})
        self.assertEqual(archive.statuses()["reel1"], "transcribed")
        archive.close()


class ReelsTab(Tmp):
    def tab(self, *metas, now=NOW):
        with self.lines(*metas), mock.patch.object(ig, "gallery_cmd", wraps=ig.gallery_cmd) as cmd:
            code = ig.cmd_list("creator", 180, self.root, now, tab="reels")
        self.assertEqual(cmd.call_args.args[-1], "reels")
        return code

    def test_the_url_is_the_tab(self):
        self.assertIn("https://www.instagram.com/creator/reels/", ig.gallery_cmd("creator", "c", "reels"))
        self.assertIn("https://www.instagram.com/creator/posts/", ig.gallery_cmd("creator", "c", "posts"))

    def test_a_first_pass_goes_past_what_the_grid_already_showed(self):
        with self.lines(*[meta(f"grid{n}", n) for n in range(1, 5)]):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertEqual(self.tab(*[meta(f"grid{n}", n) for n in range(1, 5)], meta("hidden1", 6)), 0)
        self.assertEqual(self.archive()["hidden1"], "new")
        self.assertEqual({r["shortcode"]: r["type"] for r in self.index()}["hidden1"], "reel")

    def test_a_rerun_stops_at_known_reels(self):
        self.tab(*[meta(f"reel{n}", n) for n in range(1, 5)])
        self.tab(meta("week1", 1), *[meta(f"reel{n}", n + 1) for n in range(1, 5)], now=NOW + timedelta(days=1))
        self.assertIn("3 already-known posts in a row", self.printed[-1])

    def test_a_hidden_reel_behind_new_grid_reels_is_still_found(self):
        self.tab(*[meta(f"reel{n}", n + 3) for n in range(1, 5)])
        later = NOW + timedelta(days=1)
        with self.lines(meta("week1", 1), meta("week2", 2), meta("week3", 3)):
            ig.cmd_list("creator", 180, self.root, later)
        grid = [meta("week1", 1), meta("week2", 2), meta("week3", 3)]
        self.tab(*grid, meta("hidden1", 3), *[meta(f"reel{n}", n + 3) for n in range(1, 5)], now=later)
        self.assertEqual(self.archive()["hidden1"], "new")

    def test_a_new_photo_behind_reels_the_other_tab_showed_is_still_found(self):
        with self.lines(meta("old1x", 9), meta("old2x", 10), meta("old3x", 11)):
            ig.cmd_list("creator", 180, self.root, NOW)
        later = NOW + timedelta(days=1)
        self.tab(meta("week1", 1), meta("week2", 2), meta("week3", 3), now=later)
        with self.lines(meta("week1", 1), meta("week2", 2), meta("week3", 3), meta("photo1", 4), meta("old1x", 9), meta("old2x", 10),
                        meta("old3x", 11)):
            ig.cmd_list("creator", 180, self.root, later)
        self.assertEqual(self.archive()["photo1"], "new")

    def test_everything_on_the_tab_is_a_reel(self):
        self.tab(meta("reel1", 1, type="post", post_url="https://www.instagram.com/p/reel1/"))
        row = self.index()[0]
        self.assertEqual((row["type"], row["url"]), ("reel", "https://www.instagram.com/reel/reel1/"))

    def test_the_tab_keeps_its_own_coverage(self):
        self.tab(meta("reel1", 1))
        archive = ig.Archive(self.root / "archive.sqlite3")
        self.assertIsNone(archive.covered_from("creator"))
        self.assertIsNotNone(archive.covered_from("creator/reels"))
        archive.close()


class Housekeeping(Tmp):
    def test_a_listing_clears_what_a_killed_transcribe_left(self):
        left = self.root / ".tmp" / "reel1" / "reel1.m4a"
        left.parent.mkdir(parents=True)
        left.write_bytes(b"a")
        with self.lines(meta("new1x")):
            ig.cmd_list("creator", 180, self.root, NOW)
        self.assertFalse(left.exists())

    def test_gallery_dl_is_stopped_when_reading_its_output_breaks(self):
        real, procs = subprocess.Popen, []

        def keep(*args, **kwargs):
            procs.append(real(*args, **kwargs))
            return procs[-1]

        with self.lines(meta("new1x")), mock.patch.dict(os.environ, {"FAKE_HANG": "1"}), \
                mock.patch.object(ig, "read_posts", side_effect=RuntimeError("boom")), \
                mock.patch.object(ig.subprocess, "Popen", side_effect=keep):
            with self.assertRaises(RuntimeError):
                ig.cmd_list("creator", 180, self.root, NOW)
        self.assertIsNotNone(procs[0].wait(timeout=10), "gallery-dl must not outlive the failed listing")
        procs[0].stdout.close()
        procs[0].stderr.close()

    def test_two_commands_cannot_run_at_once(self):
        with ig.one_at_a_time(self.root):
            with mock.patch.object(ig, "cmd_list") as run:
                self.assertEqual(ig.main(["list", "--handle", "creator"]), ig.EXIT_FAIL)
        run.assert_not_called()
        self.assertIn("already running", self.printed[-1])

    def test_the_lock_is_free_after_a_command(self):
        with mock.patch.object(ig, "cmd_list", return_value=0):
            self.assertEqual(ig.main(["list", "--handle", "creator"]), 0)
            self.assertEqual(ig.main(["list", "--handle", "creator"]), 0)

    def test_no_posts_says_the_profile_may_be_private(self):
        with self.lines():
            self.assertEqual(ig.cmd_list("creator", 180, self.root, NOW), 0)
        self.assertTrue(any("private" in p for p in self.printed))


class ScreenText(unittest.TestCase):
    """screen_text.py with a stand-in for the OCR engine: the model itself is checked by hand on real frames."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "code1"
        self.folder.mkdir()

    @staticmethod
    def ocr(by_name):
        def read(path):
            lines = by_name.get(Path(path).name, [])
            return mock.Mock(txts=tuple(t for t, _ in lines) or None, scores=tuple(s for _, s in lines) or None)
        return read

    def test_slides_keep_their_own_numbers_past_nine(self):
        for name in ("code1_10.jpg", "code1_2.jpg", "code1_1.jpg"):
            (self.folder / name).write_bytes(b"x")
        reads = {"code1_1.jpg": [("first slide", 0.99)], "code1_2.jpg": [("second slide", 0.99)], "code1_10.jpg": [("tenth slide", 0.99)]}
        self.assertEqual(st.read_folder(self.ocr(reads), self.folder),
                         [["slide 1", "first slide"], ["slide 2", "second slide"], ["slide 10", "tenth slide"]])

    def test_a_video_slide_says_which_slide_it_is(self):
        (self.folder / "code1_3.mp4").write_bytes(b"x")
        frame = self.folder / "frame_0.jpg"
        with mock.patch.object(st, "frames", return_value=[frame]):
            found = st.read_folder(self.ocr({"frame_0.jpg": [("on a video slide", 0.9)]}), self.folder)
        self.assertEqual(found, [["slide 3 00:00", "on a video slide"]])

    def test_a_folder_of_unreadable_media_is_a_failure_not_an_empty_result(self):
        (self.folder / "code1_1.heic").write_bytes(b"x")
        with self.assertRaises(ValueError):
            st.read_folder(self.ocr({}), self.folder)

    def test_one_unreadable_slide_beside_a_good_one_is_a_failure_too(self):
        (self.folder / "code1_1.heic").write_bytes(b"x")
        (self.folder / "code1_2.jpg").write_bytes(b"x")
        with self.assertRaises(ValueError):
            st.read_folder(self.ocr({}), self.folder)

    def test_a_reel_with_only_its_sound_left_is_an_empty_result(self):
        (self.folder / "code1.m4a").write_bytes(b"x")
        self.assertEqual(st.read_folder(self.ocr({}), self.folder), [])

    def test_video_frames_are_timed_and_a_caption_on_screen_for_long_counts_once(self):
        (self.folder / "code1.mp4").write_bytes(b"x")
        frames = [self.folder / f"frame_{n}.jpg" for n in range(3)]
        reads = {"frame_0.jpg": [("Claude Code", 0.99)], "frame_1.jpg": [("claude code!", 0.9)], "frame_2.jpg": [("npx skills", 0.95)]}
        with mock.patch.object(st, "frames", return_value=frames):
            found = st.read_folder(self.ocr(reads), self.folder)
        self.assertEqual(found, [["00:00", "Claude Code"], [st.mmss(2 * st.EVERY), "npx skills"]])

    def test_unsure_and_tiny_readings_are_dropped(self):
        (self.folder / "code1_1.jpg").write_bytes(b"x")
        reads = {"code1_1.jpg": [("a real line", 0.9), ("n0ise", 0.3), ("ok", 0.99), ("..", 0.99)]}
        self.assertEqual(st.read_folder(self.ocr(reads), self.folder), [["slide 1", "a real line"]])

    def test_a_blank_frame_gives_nothing(self):
        (self.folder / "code1_1.jpg").write_bytes(b"x")
        self.assertEqual(st.read_folder(self.ocr({}), self.folder), [])

    def test_a_broken_folder_loses_only_itself(self):
        good = Path(self._dir.name) / "code2"
        good.mkdir()
        (good / "code2_1.jpg").write_bytes(b"x")
        (self.folder / "code1.mp4").write_bytes(b"x")
        result = Path(self._dir.name) / "result.json"
        with mock.patch.object(st, "engine", return_value=self.ocr({"code2_1.jpg": [("still read", 0.9)]})), \
                mock.patch.object(st, "frames", side_effect=RuntimeError("bad video")), mock.patch("sys.stderr"):
            self.assertEqual(st.main([str(result), str(self.folder), str(good)]), 0)
        self.assertEqual(json.loads(result.read_text(encoding="utf-8")), {"code2": [["slide 1", "still read"]]})

    def test_mmss(self):
        self.assertEqual((st.mmss(4), st.mmss(62)), ("00:04", "01:02"))


WALL = "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"
REQ429 = '[urllib3.connectionpool][debug] https://www.instagram.com:443 "GET /api/v1/feed/ HTTP/1.1" 429 None'


class ConsolePython(unittest.TestCase):
    """Windows ignores CREATE_NO_WINDOW for pythonw.exe children, so they would flash windows for ffmpeg and the like."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def test_pythonw_with_python_beside_it_gives_python(self):
        (self.folder / "python.exe").write_bytes(b"x")
        for name in ("pythonw.exe", "PythonW.EXE"):
            with mock.patch.object(sys, "executable", str(self.folder / name)):
                self.assertEqual(ig.console_python(), str(self.folder / "python.exe"))

    def test_pythonw_alone_falls_back_to_itself(self):
        pythonw = str(self.folder / "pythonw.exe")
        with mock.patch.object(sys, "executable", pythonw):
            self.assertEqual(ig.console_python(), pythonw)

    def test_python_stays_as_it_is(self):
        (self.folder / "python.exe").write_bytes(b"x")
        (self.folder / "pythonw.exe").write_bytes(b"x")
        python = str(self.folder / "python.exe")
        with mock.patch.object(sys, "executable", python):
            self.assertEqual(ig.console_python(), python)

    def test_all_four_children_start_from_it(self):
        for command in (ig.GALLERY_DL, ig.YTDLP, ig.MEDIA_DIGEST, ig.SCREEN_TEXT):
            self.assertEqual(command[0], ig.PYTHON)


class LinkParsing(unittest.TestCase):
    def test_link_ids(self):
        self.assertEqual(ig.link_id("https://www.instagram.com/reel/DAbc123xyz/?igsh=x"), ("instagram", "DAbc123xyz"))
        self.assertEqual(ig.link_id("https://www.instagram.com/someone/p/CpPost12/"), ("instagram", "CpPost12"))
        self.assertEqual(ig.link_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10"), ("youtube", "dQw4w9WgXcQ"))
        self.assertEqual(ig.link_id("https://youtu.be/dQw4w9WgXcQ?si=x"), ("youtube", "dQw4w9WgXcQ"))
        self.assertEqual(ig.link_id("https://youtube.com/shorts/-bcdefghijk"), ("youtube", "-bcdefghijk"))
        self.assertEqual(ig.link_id("https://www.tiktok.com/@who/video/7301234567890"), ("tiktok", "7301234567890"))
        folder, code = ig.link_id("https://example.com/talk.mp4")
        self.assertEqual(folder, "web")
        self.assertRegex(code, r"^u[0-9a-f]{10}$")
        self.assertEqual(ig.link_id("https://example.com/talk.mp4"), (folder, code))

    def test_parse_links_drops_repeats_and_comments(self):
        text = ("# my links\nhttps://youtu.be/dQw4w9WgXcQ\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ, "
                "https://www.instagram.com/reel/DAbc123xyz/")
        self.assertEqual(ig.parse_links(text), [("youtube", "dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
                                                ("instagram", "DAbc123xyz", "https://www.instagram.com/reel/DAbc123xyz/")])

    def test_parse_links_reads_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "links.txt"
            f.write_text("https://youtu.be/dQw4w9WgXcQ\n\n", encoding="utf-8")
            self.assertEqual(ig.parse_links(str(f)), [("youtube", "dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ")])

    def test_a_word_is_not_a_link(self):
        with self.assertRaises(ValueError):
            ig.parse_links("https://youtu.be/dQw4w9WgXcQ hello")

    def test_info_meta(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ig.info_meta(d, "abc"), {})
            (Path(d) / "abc.info.json").write_text(json.dumps({"webpage_url": "https://x/y", "upload_date": "20261101",
                                                               "uploader": "Ann", "title": "T", "description": "D\nmore"}),
                                                   encoding="utf-8")
            self.assertEqual(ig.info_meta(d, "abc"), {"url": "https://x/y", "date": "2026-11-01", "caption": "Ann: T · D more"})


class LinkRuns(Tmp):
    IG = "https://www.instagram.com/reel/DAbc123xyz/"
    YT = "https://youtu.be/dQw4w9WgXcQ"

    def calls(self):
        f = self.root.parent / "ytdlp-args.jsonl"
        return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def run_links(self, value, **env):
        with mock.patch.dict(os.environ, {"FAKE_YTDLP_ARGS": str(self.root.parent / "ytdlp-args.jsonl"), **env}):
            return ig.cmd_run(value, 30, self.root)

    def test_instagram_and_youtube_in_one_run(self):
        self.assertEqual(self.run_links(f"{self.IG} {self.YT}"), 0)
        reel = (self.root / "instagram" / "transcripts" / "DAbc123xyz.md").read_text(encoding="utf-8")
        video = (self.root / "youtube" / "transcripts" / "dQw4w9WgXcQ.md").read_text(encoding="utf-8")
        self.assertIn("words from DAbc123xyz", reel)
        self.assertIn("- caption: someone: title of dQw4w9WgXcQ · the description", video)
        self.assertIn("- date: 2026-11-01", video)
        calls = self.calls()
        self.assertEqual([c[-1] for c in calls], [self.IG, self.YT])  # Instagram first
        self.assertIn("--cookies-from-browser", calls[0])
        self.assertNotIn("--cookies-from-browser", calls[1])
        self.assertTrue(all("--write-info-json" in c for c in calls))

    def test_a_rerun_skips_what_is_done(self):
        self.run_links(f"{self.IG} {self.YT}")
        before = len(self.calls())
        self.assertEqual(self.run_links(f"{self.YT} {self.IG}"), 0)
        self.assertEqual(len(self.calls()), before)

    def test_youtube_asking_for_a_login_fails_that_link_only(self):
        error = "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. " + HINT
        code = self.run_links(f"{self.YT} {self.IG}", FAKE_BAD="dQw4w9WgXcQ", FAKE_YTDLP_ERROR=error)
        self.assertEqual(code, ig.EXIT_FAIL)  # not EXIT_LOGIN: that would stop the user's Instagram too
        self.assertTrue((self.root / "instagram" / "transcripts" / "DAbc123xyz.md").exists())
        self.assertFalse((self.root / "youtube" / "transcripts" / "dQw4w9WgXcQ.md").exists())

    def test_an_instagram_wall_does_not_stop_youtube(self):
        code = self.run_links(f"{self.IG} {self.YT}", FAKE_BAD="DAbc123xyz", FAKE_YTDLP_ERROR=WALL_RATE)
        self.assertEqual(code, ig.EXIT_LOGIN)
        self.assertTrue((self.root / "youtube" / "transcripts" / "dQw4w9WgXcQ.md").exists())

    def test_a_photo_post_link_falls_back_to_gallery_dl(self):
        link = "https://www.instagram.com/p/CpPost12/"
        self.assertEqual(self.run_links(link, FAKE_NO_VIDEO="CpPost12"), 0)
        self.assertTrue((self.root / "instagram" / "frames" / "CpPost12").exists())

    def test_no_login_needed_for_youtube_only(self):
        (self.profile / "cookies.sqlite").unlink()
        self.assertEqual(self.run_links(self.YT), 0)
        self.assertTrue((self.root / "youtube" / "transcripts" / "dQw4w9WgXcQ.md").exists())


if __name__ == "__main__":
    unittest.main()
