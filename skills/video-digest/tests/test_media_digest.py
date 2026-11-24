"""Tests for media_digest.py frames: scene changes, near-duplicates, the cap, cue frames, hours.

    python -m unittest discover -s skills/video-digest/tests -v

Clips are made with ffmpeg lavfi in a temp folder when the suite starts (no recordings, no binary fixtures, no network).
The speech step is patched out; the frame code runs against the real ffmpeg, which the suite requires.
"""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import media_digest as md  # noqa: E402

# 14 segments of 0.4 s = 5.6 s; neighbours differ a lot, so every join is a scene cut
COLOURS = ["red", "white", "blue", "black", "yellow", "cyan", "magenta", "green", "white", "black", "red", "blue", "yellow", "black"]
CUT_SECONDS = 0.4 * len(COLOURS)
TMP = None
FF = None
CLIPS = {}


def run_ffmpeg(*args):
    done = subprocess.run([FF, "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(f"ffmpeg could not build a test clip: {done.stderr.strip()}")


def setUpModule():
    global TMP, FF
    FF = shutil.which("ffmpeg")
    if not FF:
        raise AssertionError("ffmpeg is not on PATH: the media-digest tests build their clips with it. Install ffmpeg and run again.")
    TMP = tempfile.TemporaryDirectory()
    root = Path(TMP.name)
    src = []
    for c in COLOURS:
        src += ["-f", "lavfi", "-i", f"color=c={c}:s=320x240:d=0.4:r=25"]
    concat = "".join(f"[{i}:v]" for i in range(len(COLOURS))) + f"concat=n={len(COLOURS)}:v=1:a=0,format=yuv420p[v]"
    CLIPS["cut"] = root / "cut_clip.mp4"
    run_ffmpeg(*src, "-filter_complex", concat, "-map", "[v]", "-c:v", "libx264", "-g", "1", str(CLIPS["cut"]))
    CLIPS["static"] = root / "static_clip.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3:r=25", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(CLIPS["static"]))
    CLIPS["tiny"] = root / "tiny_clip.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x240:d=0.5:r=25", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(CLIPS["tiny"]))
    CLIPS["voice"] = root / "yk_voice.ogg"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libvorbis", str(CLIPS["voice"]))


def tearDownModule():
    TMP.cleanup()


def quiet(argv):
    """main(argv) with its output captured -> (exit code, output). SystemExit (argparse) becomes its code."""
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        try:
            rc = md.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, buf.getvalue() + err.getvalue()


def index_rows(folder):
    lines = (Path(folder) / "frames" / "index.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "file,seconds,time,reason", lines[0]
    return [dict(zip(("file", "seconds", "time", "reason"), line.split(","))) for line in lines[1:]]


def jpgs(folder):
    return sorted(p.name for p in (Path(folder) / "frames").glob("*.jpg"))


DURATIONS = {"cut_clip.mp4": CUT_SECONDS, "static_clip.mp4": 3.0, "tiny_clip.mp4": 0.5}


def fake_probe(ff, path):
    """The known length of a test clip without paying for an ffprobe start (the real probe runs in the tests marked real_probe)."""
    return DURATIONS[Path(path).name], True


def fake_wav(ff, src, wav):
    Path(wav).write_bytes(b"RIFF yk")


def go(out, clip, *flags, real_probe=False, speech=True):
    """main() on one test clip with the output captured and the speech models out of reach -> (exit code, output, digest folder).
    speech=False makes any use of the speech step a failure (the --frames-only promise)."""
    if speech:
        patches = [mock.patch.object(md, "ensure_models", return_value=(None, None)), mock.patch.object(md, "to_wav", fake_wav),
                   mock.patch.object(md, "transcribe", return_value=(FullRun.ROWS, 3800.0))]
    else:
        boom = AssertionError("the speech step ran")
        patches = [mock.patch.object(md, "ensure_models", side_effect=boom), mock.patch.object(md, "to_wav", side_effect=boom),
                   mock.patch.object(md, "transcribe", side_effect=boom)]
    if not real_probe and Path(clip).name in DURATIONS:
        patches.append(mock.patch.object(md, "probe", fake_probe))
    patches += [mock.patch.object(md.urllib.request, "urlopen", side_effect=AssertionError("network")),
                mock.patch.object(md, "ensure_pip", lambda *a: None)]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        rc, text = quiet([str(clip), "--out", str(out), *flags])
    return rc, text, Path(out) / f"{Path(clip).stem}_digest"


class Base(unittest.TestCase):
    """Each test class owns one temp folder; runs that several tests read happen once, in setUpClass."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.root = Path(cls._tmp.name)

    def fresh(self, name):
        d = self.root / name
        shutil.rmtree(d, ignore_errors=True)
        return d


class Times(unittest.TestCase):
    def test_hms(self):
        self.assertEqual((md.hms(0), md.hms(65), md.hms(3599), md.hms(3600), md.hms(3725), md.hms(59.9)),
                         ("00:00", "01:05", "59:59", "1:00:00", "1:02:05", "00:59"))

    def test_parse_at(self):
        self.assertEqual(md.parse_at("4:32, 7:10"), [272.0, 430.0])
        self.assertEqual(md.parse_at("1:02:05,90,12:05"), [90.0, 725.0, 3725.0])
        self.assertEqual(md.parse_at("0:01,0:01"), [1.0])
        for bad in ("abc", "1:2:3:4", "-5", "", "1:xx"):
            with self.assertRaises(ValueError, msg=bad):
                md.parse_at(bad)

    def test_cue_name(self):
        self.assertEqual((md.cue_name(725), md.cue_name(3725), md.cue_name(1.9)), ("cue_001205.jpg", "cue_010205.jpg", "cue_000001.jpg"))

    def test_pick_sync_from_help_text(self):
        self.assertEqual(md.pick_sync("  -fps_mode[:<stream_spec>] <mode>  set video sync\n"), ("-fps_mode", "vfr"))
        self.assertEqual(md.pick_sync("  -vsync <int>  old name\n"), ("-vsync", "vfr"))
        self.assertEqual(md.pick_sync("  -vsync x\n  -fps_mode y\n"), ("-fps_mode", "vfr"))
        with self.assertRaises(md.FramesError):
            md.pick_sync("-threads 4\n")

    def test_showinfo_times(self):
        ok = "[Parsed_showinfo_2 @ 0x1] n:   0 pts:      0 pts_time:0 duration:1\n[Parsed_showinfo_2 @ 0x1] n:   1 pts: 80 pts_time:3.2\nnoise\n"
        self.assertEqual(md.showinfo_times(ok, 2), [0.0, 3.2])
        with self.assertRaises(md.FramesError):
            md.showinfo_times(ok, 3)                       # a count mismatch refuses to label
        with self.assertRaises(md.FramesError):
            md.showinfo_times(ok.replace("pts_time:0 ", "pts_time:9 "), 2)   # out of order
        with self.assertRaises(md.FramesError):
            md.showinfo_times("[Parsed_showinfo_2 @ 0x1] pts_time:abc\n", 1)


class Helpers(unittest.TestCase):
    def test_even_indices_keep_first_and_last(self):
        idx = md.even_indices(14, 5)
        self.assertEqual((len(idx), idx[0], idx[-1]), (5, 0, 13))
        self.assertEqual(md.even_indices(3, 10), [0, 1, 2])
        self.assertEqual(md.even_indices(9, 1), [0])

    def test_dedupe_by_thumbnails_and_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rows = [{"file": f"frame_{i:04d}.jpg", "seconds": i, "reason": "x"} for i in range(1, 5)]
            for r in rows:
                (d / r["file"]).write_bytes(b"x")
            a, b = bytes([10]) * 768, bytes([200]) * 768
            near = bytes([11]) * 768                        # mean difference 1 from a: a near-duplicate
            kept, dropped = md.dedupe(rows, [a, near, b, bytes([201]) * 768], d)
            self.assertEqual(([r["file"] for r in kept], dropped), (["frame_0001.jpg", "frame_0003.jpg"], 2))
            self.assertFalse((d / "frame_0002.jpg").exists())
            self.assertTrue((d / "frame_0003.jpg").exists())
            # thumbnails that do not line up with the rows: nothing is dropped
            same, none = md.dedupe(kept, [a], d)
            self.assertEqual((same, none), (kept, 0))

    def test_frame_files_sort_by_number_not_by_text(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ("frame_0999.jpg", "frame_1000.jpg", "frame_1001.jpg", "frame_10000.jpg", "frame_0002.jpg", "cue_000001.jpg"):
                (d / n).write_bytes(b"x")
            self.assertEqual([f.name for f in md.frame_files(d)],
                             ["frame_0002.jpg", "frame_0999.jpg", "frame_1000.jpg", "frame_1001.jpg", "frame_10000.jpg"])
            self.assertEqual(sorted(f.name for f in d.glob("frame_*.jpg"))[-1], "frame_1001.jpg")   # what a plain sort would do

    def test_thin_and_renumber(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rows = [{"file": f"frame_{i:04d}.jpg", "seconds": i, "reason": "x"} for i in range(1, 11)]
            for r in rows:
                (d / r["file"]).write_text(r["file"])
            kept = md.thin_and_renumber(rows, d, 4)
            self.assertEqual([r["file"] for r in kept], [f"frame_{i:04d}.jpg" for i in range(1, 5)])
            self.assertEqual(sorted(p.name for p in d.glob("*.jpg")), [r["file"] for r in kept])
            self.assertEqual((d / "frame_0004.jpg").read_text(), "frame_0010.jpg")   # the last frame survived
            self.assertEqual([r["seconds"] for r in kept][0], 1)


class SceneCut(Base):
    """The 14-colour clip: a default run (real probe), then a capped run with cue frames on top of a copy of the same folder."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.out = cls.root / "out"
        cls.rc, cls.text, cls.folder = go(cls.out, CLIPS["cut"], "--frames-only", real_probe=True, speech=False)
        cls.rows = index_rows(cls.folder)
        cls.digest = (cls.folder / "DIGEST.md").read_text(encoding="utf-8")
        cls.stale = cls.root / "stale"
        shutil.copytree(cls.folder, cls.stale / cls.folder.name)
        cls.crc, cls.ctext, cls.cfolder = go(cls.stale, CLIPS["cut"], "--frames-only", "--frames", "scene", "--max-frames", "5", "--at", "0:01,0:02",
                                             speech=False)

    def test_a_frame_per_cut(self):
        self.assertEqual(self.rc, 0, self.text)
        self.assertGreaterEqual(len(self.rows), 8)
        self.assertEqual(self.rows[0]["reason"], "first-frame")
        self.assertEqual({r["reason"] for r in self.rows[1:]}, {"scene-change"})
        secs = [float(r["seconds"]) for r in self.rows]
        self.assertEqual(secs, sorted(secs))
        self.assertGreaterEqual(secs[0], 0)
        self.assertLessEqual(secs[-1], CUT_SECONDS)
        self.assertEqual(self.rows[1]["time"], md.hms(float(self.rows[1]["seconds"])))

    def test_index_rows_match_the_files(self):
        self.assertEqual(sorted(r["file"] for r in self.rows), jpgs(self.folder))
        self.assertEqual(jpgs(self.folder)[0], "frame_0001.jpg")

    def test_digest_says_scene_aware_and_nothing_was_dropped(self):
        self.assertIn("scene-aware", self.digest)
        self.assertRegex(self.digest, r"0 near-duplicates dropped, cap 100")
        self.assertFalse((self.folder / "audio.wav").exists())
        self.assertFalse((self.folder / "transcript.md").exists())      # --frames-only wrote no transcript

    def test_cap_keeps_first_and_last_and_replaces_the_old_frames(self):
        self.assertEqual(self.crc, 0, self.ctext)
        rows = index_rows(self.cfolder)
        frames = [r for r in rows if r["reason"] != "cue"]
        self.assertEqual(len(frames), 5)
        self.assertEqual([r["file"] for r in frames], [f"frame_{i:04d}.jpg" for i in range(1, 6)])
        self.assertEqual((frames[0]["seconds"], frames[-1]["seconds"]), (self.rows[0]["seconds"], self.rows[-1]["seconds"]))
        self.assertEqual(sorted(p.name for p in (self.cfolder / "frames").glob("frame_*.jpg")), [r["file"] for r in frames])
        self.assertIn(f"capped at 5 of {len(self.rows) - 1} cuts: raise --max-frames or use --at",
                      (self.cfolder / "DIGEST.md").read_text(encoding="utf-8"))

    def test_cues_land_at_the_asked_time_and_do_not_count_against_the_cap(self):
        rows = [r for r in index_rows(self.cfolder) if r["reason"] == "cue"]
        self.assertEqual([r["file"] for r in rows], ["cue_000001.jpg", "cue_000002.jpg"])
        for row, asked in zip(rows, (1.0, 2.0)):
            self.assertLess(abs(float(row["seconds"]) - asked), 0.15)
        self.assertEqual(len(jpgs(self.cfolder)), 7)
        self.assertEqual(len(index_rows(self.cfolder)), 7)

    def test_cues_alone_join_a_finished_folder_without_touching_it(self):
        keep = self.root / "keep"
        shutil.copytree(self.folder, keep / self.folder.name)
        (keep / self.folder.name / "transcript.md").write_text("[00:01] yk\n", encoding="utf-8")
        rc, text, folder = go(keep, CLIPS["cut"], "--frames-only", "--at", "0:03", speech=False)
        self.assertEqual(rc, 0, text)
        self.assertEqual(jpgs(folder), sorted(jpgs(self.folder) + ["cue_000003.jpg"]))
        rows = index_rows(folder)
        self.assertEqual(([r["reason"] for r in rows].count("cue"), len(rows)), (1, len(self.rows) + 1))
        self.assertEqual((folder / "transcript.md").read_text(encoding="utf-8"), "[00:01] yk\n")
        self.assertIn("## Frames added", (folder / "DIGEST.md").read_text(encoding="utf-8"))
        rc, text, folder = go(keep, CLIPS["cut"], "--frames-only", "--at", "0:04", speech=False)      # a rerun replaces its own section
        self.assertEqual(rc, 0, text)
        digest = (folder / "DIGEST.md").read_text(encoding="utf-8")
        self.assertEqual(digest.count("## Frames added"), 1)
        self.assertIn("cue_000004.jpg", digest)
        self.assertNotIn("cue_000003.jpg", digest)
        self.assertEqual(jpgs(folder).count("cue_000003.jpg") + jpgs(folder).count("cue_000004.jpg"), 2)   # both cue files stay


class CueEdges(Base):
    def test_past_the_end_is_a_note_not_a_file_and_the_speech_step_never_runs(self):
        rc, text, folder = go(self.root / "o", CLIPS["cut"], "--frames-only", "--at", "9:00,0:01", speech=False)
        self.assertEqual(rc, 0, text)
        self.assertEqual(jpgs(folder), ["cue_000001.jpg"])
        self.assertIn("cue 09:00: past the end (00:05)", (folder / "DIGEST.md").read_text(encoding="utf-8"))

    def test_an_ffmpeg_miss_inside_the_clip_says_the_ffmpeg_error(self):
        with mock.patch.object(md, "cut", return_value=(1, "Parsed\nUnknown encoder 'yk'\n")), \
                mock.patch.object(md, "sync_args", return_value=("-fps_mode", "vfr")):
            rc, text, folder = go(self.root / "x", CLIPS["cut"], "--frames-only", "--at", "0:01", speech=False)
        self.assertEqual(rc, 0, text)
        digest = (folder / "DIGEST.md").read_text(encoding="utf-8")
        self.assertIn("cue 00:01: ffmpeg gave no frame (Unknown encoder 'yk')", digest)
        self.assertNotIn("past the end", digest)

    def test_a_time_equal_to_the_length_is_past_the_end(self):
        rc, text, folder = go(self.root / "y", CLIPS["static"], "--frames-only", "--at", "0:03", speech=False)
        self.assertEqual(rc, 0, text)
        self.assertIn("cue 00:03: past the end (00:03)", (folder / "DIGEST.md").read_text(encoding="utf-8"))

    def test_audio_only_input_ignores_at(self):
        rc, text, folder = go(self.root / "a", CLIPS["voice"], "--at", "0:01")
        self.assertEqual(rc, 0, text)
        self.assertFalse((folder / "frames").exists())
        self.assertIn("--at ignored: no video stream", (folder / "DIGEST.md").read_text(encoding="utf-8"))

    def test_frames_only_on_audio_is_a_plain_failure(self):
        rc, text, _ = go(self.root / "b", CLIPS["voice"], "--frames-only", "--at", "0:01", speech=False)
        self.assertEqual(rc, 1)
        self.assertIn("no video stream", text)


class StaticClip(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rc, cls.text, cls.folder = go(cls.root / "d", CLIPS["static"], "--frames-only", "--frames-every", "1", speech=False)
        cls.nrc, cls.ntext, cls.nfolder = go(cls.root / "n", CLIPS["static"], "--frames-only", "--frames-every", "1", "--no-dedup",
                                             speech=False)

    def test_uniform_fallback_then_dedup_leaves_one_frame(self):
        self.assertEqual(self.rc, 0, self.text)
        self.assertEqual(len(index_rows(self.folder)), 1)
        digest = (self.folder / "DIGEST.md").read_text(encoding="utf-8")
        self.assertIn("uniform fallback", digest)
        self.assertRegex(digest, r"[1-9]\d* near-duplicates dropped")

    def test_no_dedup_keeps_the_copies(self):
        self.assertEqual(self.nrc, 0, self.ntext)
        rows = index_rows(self.nfolder)
        self.assertEqual([r["reason"] for r in rows], ["first-frame", "uniform", "uniform"])
        self.assertEqual([r["time"] for r in rows], ["00:00", "00:01", "00:02"])


class EveryMode(Base):
    def test_every_second_on_a_three_second_clip_gives_three_frames(self):
        rc, text, folder = go(self.root / "e3", CLIPS["static"], "--frames-only", "--frames", "every", "--frames-every", "1", speech=False)
        self.assertEqual(rc, 0, text)
        rows = index_rows(folder)
        self.assertEqual([r["file"] for r in rows], ["frame_0001.jpg", "frame_0002.jpg", "frame_0003.jpg"])
        self.assertEqual([r["reason"] for r in rows], ["first-frame", "uniform", "uniform"])
        self.assertEqual(jpgs(folder), [r["file"] for r in rows])
        self.assertIn("3 frames (one every 1 s)", (folder / "DIGEST.md").read_text(encoding="utf-8"))


class TinyClip(Base):
    def test_half_second_clip_gives_its_first_frame(self):
        rc, text, folder = go(self.root / "t", CLIPS["tiny"], "--frames-only")
        self.assertEqual(rc, 0, text)
        self.assertEqual([(r["file"], r["reason"]) for r in index_rows(folder)], [("frame_0001.jpg", "first-frame")])

    def test_every_mode_and_a_percent_in_the_path(self):
        d = self.root / "50% done"
        d.mkdir()
        clip = d / "tiny%05d clip.mp4"
        shutil.copy(CLIPS["tiny"], clip)
        DURATIONS[clip.name] = 0.5
        self.addCleanup(DURATIONS.pop, clip.name, None)
        rc, text, folder = go(self.root / "e", clip, "--frames-only", "--frames", "every")
        self.assertEqual(rc, 0, text)
        self.assertEqual([(r["file"], r["reason"]) for r in index_rows(folder)], [("frame_0001.jpg", "first-frame")])

    def test_showinfo_mismatch_refuses_to_label(self):
        real = md.cut

        def fewer_stamps(*a, **k):
            code, err = real(*a, **k)
            return code, "\n".join(line for line in err.splitlines() if "pts_time" not in line)
        with mock.patch.object(md, "cut", fewer_stamps):
            rc, text, folder = go(self.root / "m", CLIPS["tiny"], "--frames-only", speech=False)
        self.assertEqual(rc, 1)
        self.assertIn("refusing to label", text)
        self.assertFalse((folder / "frames" / "index.csv").exists())

    def test_a_frame_failure_keeps_the_transcript_and_the_exit_code_is_1(self):
        real = md.cut

        def fewer_stamps(*a, **k):
            code, err = real(*a, **k)
            return code, "\n".join(line for line in err.splitlines() if "pts_time" not in line)
        with mock.patch.object(md, "cut", fewer_stamps):
            rc, text, folder = go(self.root / "ft", CLIPS["tiny"], "--keep-audio")
        self.assertEqual(rc, 1)
        self.assertIn("rerun with --frames off", text)
        self.assertTrue((folder / "transcript.md").exists())
        self.assertTrue((folder / "transcript.json").exists())
        digest = (folder / "DIGEST.md").read_text(encoding="utf-8")
        self.assertIn("frames failed: ", digest)
        self.assertIn("--frames off", digest)
        self.assertIn("## Summary", digest)
        self.assertEqual(list((folder / "frames").glob("*")), [])      # no half-labelled frames left behind

    def test_frames_off_makes_no_frames_folder(self):
        rc, text, folder = go(self.root / "f", CLIPS["tiny"], "--frames", "off")
        self.assertEqual(rc, 0, text)
        self.assertFalse((folder / "frames").exists())
        self.assertIn("no frames (--frames off)", (folder / "DIGEST.md").read_text(encoding="utf-8"))


class Arguments(Base):
    def test_frames_only_needs_something_to_cut(self):
        rc, text = quiet([str(CLIPS["cut"]), "--frames", "off", "--frames-only"])
        self.assertEqual(rc, 2)
        self.assertIn("--frames-only needs frames to cut", text)

    def test_bad_values_are_argparse_errors(self):
        for argv in (["x.mp4", "--at", "soon"], ["x.mp4", "--max-frames", "0"], ["x.mp4", "--frames", "keyframes"]):
            with self.subTest(argv=argv):
                self.assertEqual(quiet(argv)[0], 2)

    def test_old_flags_still_parse(self):
        seen = []
        with mock.patch.object(md, "digest", side_effect=lambda *a: seen.append(md.FRAME_OPTS)) as dg, mock.patch.object(md, "ensure_pip"):
            rc, _ = quiet([str(CLIPS["cut"]), "--out", "o", "--frames-every", "10", "--lang", "en", "--threads", "2", "--keep-audio",
                           "--engine", "sensevoice"])
        self.assertEqual(rc, 0)
        args, kw = dg.call_args
        self.assertEqual((args[2:], kw), ((10, "en", 2, True, "sensevoice"), {}))     # the seven positional arguments wa-tracker fakes
        self.assertEqual(seen[0], md.FrameOptions("scene", 100, True, (), False))
        self.assertEqual(md.FRAME_OPTS, md.FrameOptions("scene", 100, True, (), False))   # reset after the run

    def test_qwen3_still_stops_at_a_missing_model(self):
        with mock.patch.object(md, "MODELS", self.root / "models"):
            rc, text = quiet([str(CLIPS["cut"]), "--engine", "qwen3"])
        self.assertEqual(rc, 1)
        self.assertIn("the qwen3 model is missing", text)


class FullRun(Base):
    ROWS = [{"start": 65.0, "end": 66.0, "text": "yk one", "lang": "<|en|>", "emotion": "", "event": ""},
            {"start": 3725.0, "end": 3726.0, "text": "yk two", "lang": "<|en|>", "emotion": "", "event": ""}]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rc, cls.text, cls.folder = go(cls.root / "c", CLIPS["cut"])
        cls.vrc, cls.vtext, cls.vfolder = go(cls.root / "v", CLIPS["voice"], real_probe=True)

    def test_folder_holds_transcript_frames_and_digest(self):
        self.assertEqual(self.rc, 0, self.text)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["DIGEST.md", "frames", "transcript.json", "transcript.md"])
        self.assertTrue((self.folder / "frames" / "index.csv").exists())    # audio.wav is gone too: not in the list
        digest = (self.folder / "DIGEST.md").read_text(encoding="utf-8")
        self.assertRegex(digest, r"\d+ frames \(scene-aware: \d+ cuts, 0 near-duplicates dropped, cap 100\)")
        self.assertIn("frames/index.csv", md.OUTLINE)
        self.assertNotIn("one frame every N seconds", digest)

    def test_keep_audio_keeps_the_wav(self):
        rc, text, folder = go(self.root / "k", CLIPS["tiny"], "--frames", "every", "--keep-audio")
        self.assertEqual(rc, 0, text)
        self.assertTrue((folder / "audio.wav").exists())
        self.assertIn("1 frames (one every 30 s)", (folder / "DIGEST.md").read_text(encoding="utf-8"))   # today's wording

    def test_transcript_marks_use_hours_only_past_an_hour(self):
        text = (self.folder / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("[01:05] yk one  <en>", text)
        self.assertIn("[1:02:05] yk two  <en>", text)
        self.assertIn("(1:03:20, 2 segments", text)

    def test_voice_note_gives_todays_files(self):
        self.assertEqual(self.vrc, 0, self.vtext)
        self.assertEqual(sorted(p.name for p in self.vfolder.iterdir()), ["DIGEST.md", "transcript.json", "transcript.md"])
        self.assertIn("audio only", (self.vfolder / "DIGEST.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
