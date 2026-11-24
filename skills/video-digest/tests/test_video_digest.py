"""Tests for the video-digest additions: config, key-frame picking, the reliability label and brain.py. No network:
the webhook is a local HTTP server, the vault a temporary git repo."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import brain  # noqa: E402
import video_digest as ig  # noqa: E402
import key_frames  # noqa: E402
import vd_config  # noqa: E402

vd_config.REPO_ENV = None  # the developer's own .env must not change test results


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in vd_config.DEFAULTS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_defaults_without_a_file(self):
        self.assertEqual(vd_config.load(self.root)["ASR_ENGINE"], "sensevoice")

    def test_file_overrides_defaults_and_environment_overrides_file(self):
        (self.root / ".env").write_text('# comment\nASR_ENGINE="qwen3"\nexport MAX_FRAMES=12\n\nBROKEN LINE\n', encoding="utf-8")
        self.assertEqual(vd_config.load(self.root)["ASR_ENGINE"], "qwen3")
        self.assertEqual(vd_config.load(self.root)["MAX_FRAMES"], "12")
        os.environ["ASR_ENGINE"] = "sensevoice"
        self.assertEqual(vd_config.engine(self.root), "sensevoice")

    def test_the_repo_env_is_read_and_the_output_folder_overrides_it(self):
        repo_env = self.root / "repo.env"
        repo_env.write_text("ASR_ENGINE=qwen3\nMAX_FRAMES=9\n", encoding="utf-8")
        with mock.patch.object(vd_config, "REPO_ENV", repo_env):
            self.assertEqual(vd_config.load(self.root)["ASR_ENGINE"], "qwen3")
            (self.root / ".env").write_text("MAX_FRAMES=12\n", encoding="utf-8")
            cfg = vd_config.load(self.root)
            self.assertEqual((cfg["ASR_ENGINE"], cfg["MAX_FRAMES"]), ("qwen3", "12"))

    def test_the_repo_env_sits_at_the_repo_root(self):
        self.assertTrue((Path(vd_config.__file__).resolve().parents[3] / ".env.example").exists())

    def test_a_wrong_engine_is_refused(self):
        (self.root / ".env").write_text("ASR_ENGINE=whisper\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            vd_config.engine(self.root)

    def test_home_prefers_the_new_name(self):
        with mock.patch.dict(os.environ, {"VIDEO_DIGEST_HOME": "/a", "IG_DIGEST_HOME": "/b"}):
            self.assertEqual(vd_config.home(), Path("/a"))
        with mock.patch.dict(os.environ, {"IG_DIGEST_HOME": "/b"}):
            os.environ.pop("VIDEO_DIGEST_HOME", None)
            self.assertEqual(vd_config.home(), Path("/b"))


class KeyFrames(unittest.TestCase):
    def test_scene_changes_and_coverage_frames_in_time_order(self):
        got = key_frames.pick(30, [4.2, 4.5, 12.0, 29.5], 3, 20)
        times = [t for t, _ in got]
        self.assertEqual(times, sorted(times))
        self.assertIn((4.2, "scene"), got)
        self.assertNotIn(4.5, times)  # closer than a second to 4.2
        self.assertIn((12.0, "scene"), got)  # a scene change wins over the coverage frame at the same moment
        self.assertEqual(got[0], (0.0, "start"))

    def test_the_cap_spreads_frames_over_the_whole_reel(self):
        got = key_frames.pick(60, [], 3, 5)
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0][0], 0.0)
        self.assertGreater(got[-1][0], 50)

    def test_nothing_for_an_empty_video(self):
        self.assertEqual(key_frames.pick(0, [], 3, 20), [])


class Reliability(unittest.TestCase):
    def test_a_normal_talking_reel_passes(self):
        body = "[00:00] " + "this is a normal amount of talking for a reel " * 20
        self.assertIsNone(ig.reliability_note(body, 60, 40))

    def test_few_words_over_long_audio_is_flagged(self):
        note = ig.reliability_note("# transcript\n[00:00] hello there", 60, 40)
        self.assertTrue(note.startswith(ig.UNRELIABLE))
        self.assertIn("2 words over 60 s", note)

    def test_short_clips_are_not_judged(self):
        self.assertIsNone(ig.reliability_note("hi", 10, 40))

    def test_the_label_goes_under_the_caption_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.md"
            p.write_text("# code (@h)\n\n- url: u\n- date: d\n- caption: c\n\n[00:00] hi\n", encoding="utf-8")
            ig.mark_unreliable(p, ig.UNRELIABLE + " (x)")
            ig.mark_unreliable(p, ig.UNRELIABLE + " (x)")
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[5], ig.UNRELIABLE + " (x)")
            self.assertEqual(sum(ln.startswith(ig.UNRELIABLE) for ln in lines), 1)


class Hook(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Hook.received.append(({k.lower(): v for k, v in self.headers.items()}, json.loads(body)))
        self.send_response(Hook.status)
        self.end_headers()
        self.wfile.write(b'{"message":"Workflow was started"}')

    def log_message(self, *args):
        pass


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class Brain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo, self.home = base / "vault", base / "home"
        (self.repo / "Topics" / "n8n").mkdir(parents=True)
        self.home.mkdir()
        (self.repo / "Topics" / "n8n" / "errors.md").write_text("# n8n errors\n\nRetry patterns.\n", encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.server = HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        Hook.received, Hook.status = [], 200
        self.cfg = {**vd_config.DEFAULTS, "BRAIN_PATH": str(self.repo / "Topics"),
                    "N8N_WEBHOOK_URL": f"http://127.0.0.1:{self.server.server_port}/webhook/video-digest/propose",
                    "IGDIGEST_SECRET": "s3cret"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def proposal(self, name="p.json", **over):
        data = {"action": "update", "path": "Topics/n8n/errors.md", "title": "n8n errors", "summary": "adds a retry tip",
                "content": "# n8n errors\n\nRetry patterns.\n\n- new tip\n", "sources": ["https://www.instagram.com/reel/X/ @ 00:10"]}
        data.update(over)
        f = self.home / name
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def test_index_lists_notes_with_git_blob_hashes(self):
        with mock.patch("builtins.print"):
            brain.cmd_index(self.cfg, self.home / "idx.json")
        idx = json.loads((self.home / "idx.json").read_text(encoding="utf-8"))
        self.assertEqual(idx["topics_dir"], "Topics")
        note = idx["notes"][0]
        self.assertEqual(note["path"], "Topics/n8n/errors.md")
        self.assertEqual(note["title"], "n8n errors")
        expected = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD:Topics/n8n/errors.md"],
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(note["sha"], expected)  # the same value GitHub reports for the file

    def test_index_tallies_telegram_choices(self):
        folder = self.repo / "video-digest" / "choices"
        folder.mkdir(parents=True)
        for name, decision, path, at in (("a", "approve", "Topics/ai/prompts.md", "2026-11-20"),
                                         ("b", "approve", "Topics/ai/prompts.md", "2026-11-21"),
                                         ("c", "reject", "Topics/trading/scalping.md", "2026-11-22")):
            (folder / f"{name}.json").write_text(json.dumps({"at": at, "decision": decision, "path": path, "title": name}),
                                                 encoding="utf-8")
        (folder / "broken.json").write_text("{not json", encoding="utf-8")
        with mock.patch("builtins.print"):
            brain.cmd_index(self.cfg, self.home / "idx.json")
        got = json.loads((self.home / "idx.json").read_text(encoding="utf-8"))["choices"]
        self.assertEqual(got["areas"], {"ai": {"approve": 2, "reject": 0}, "trading": {"approve": 0, "reject": 1}})
        self.assertEqual(got["notes"]["Topics/ai/prompts.md"], {"approve": 2, "reject": 0})
        self.assertEqual([r["title"] for r in got["recent"]], ["c", "b", "a"])
        self.assertEqual(len(got["warnings"]), 1)
        self.assertIn("broken.json", got["warnings"][0])

    def test_index_without_choices_is_empty_not_an_error(self):
        with mock.patch("builtins.print"):
            brain.cmd_index(self.cfg, self.home / "idx.json")
        got = json.loads((self.home / "idx.json").read_text(encoding="utf-8"))["choices"]
        self.assertEqual(got, {"areas": {}, "notes": {}, "recent": [], "warnings": []})

    def test_an_update_is_sent_with_the_secret_and_base_sha(self):
        f = self.proposal()
        with mock.patch("builtins.print"):
            self.assertEqual(brain.cmd_propose(self.cfg, [f], root=self.home), 0)
        headers, body = Hook.received[0]
        self.assertEqual(headers["x-igd-secret"], "s3cret")
        self.assertEqual(body["action"], "update")
        self.assertEqual(len(body["base_sha"]), 40)
        self.assertTrue((self.home / "proposals" / "sent" / "p.json").exists())
        self.assertFalse(f.exists())

    def test_bad_paths_are_refused_and_nothing_is_sent(self):
        for path in ("../etc/x.md", "Topics/../x.md", "Other/x.md", "Topics/x.txt", "Topics\\x.md", "/Topics/x.md"):
            f = self.proposal(action="create", path=path)
            with mock.patch("builtins.print"):
                self.assertEqual(brain.cmd_propose(self.cfg, [f], root=self.home), 1, path)
        self.assertEqual(Hook.received, [])

    def test_create_of_an_existing_note_must_be_an_update(self):
        f = self.proposal(action="create")
        with mock.patch("builtins.print") as out:
            self.assertEqual(brain.cmd_propose(self.cfg, [f], root=self.home), 1)
        self.assertIn("use update", str(out.call_args_list))
        self.assertTrue(f.exists())  # left in place to fix and resend

    def test_a_new_topic_is_created_without_base_sha(self):
        f = self.proposal(action="create", path="Topics/claude/skills.md", title="Claude skills")
        with mock.patch("builtins.print"):
            self.assertEqual(brain.cmd_propose(self.cfg, [f], root=self.home), 0)
        self.assertIsNone(Hook.received[0][1]["base_sha"])

    def test_dry_run_sends_nothing(self):
        f = self.proposal()
        with mock.patch("builtins.print"):
            self.assertEqual(brain.cmd_propose(self.cfg, [f], dry_run=True, root=self.home), 0)
        self.assertEqual(Hook.received, [])
        self.assertTrue(f.exists())

    def test_a_refusal_from_n8n_keeps_the_proposal(self):
        Hook.status = 403
        f = self.proposal()
        with mock.patch("builtins.print"):
            self.assertEqual(brain.cmd_propose(self.cfg, [f], root=self.home), 1)
        self.assertTrue(f.exists())


if __name__ == "__main__":
    unittest.main()
