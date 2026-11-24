#!/usr/bin/env python3
"""media_digest.py — turn a recording (mp4/mkv/webm/mov/mp3/m4a/ogg/wav) into text Claude can read.

    python media_digest.py <file> [<file> ...] [--out DIR] [--frames scene|every|off] [--frames-every 30] [--max-frames 100]
                           [--no-dedup] [--at 4:32,7:10] [--frames-only] [--lang auto] [--threads 4] [--engine sensevoice|qwen3]

Writes, next to each input (or under --out): <name>_digest/
    audio.wav            16 kHz mono (temporary, deleted at the end unless --keep-audio)
    transcript.md        [mm:ss] one line per speech segment, language tag per segment
    transcript.json      the same with seconds and the model's language/emotion/event tags
    frames/frame_NNNN.jpg 800 px wide JPEGs (video inputs only): --frames scene (default) keeps the frame where the picture changed,
                         drops near-duplicates and thins to --max-frames; --frames every cuts one every --frames-every seconds
                         (a very short clip gives its first frame); --frames off cuts none
    frames/cue_HHMMSS.jpg the frame at each --at time (never counted against --max-frames)
    frames/index.csv     file,seconds,time,reason for every JPEG (reason: first-frame, scene-change, uniform, cue)
    DIGEST.md            header (file, duration, counts) + the summary outline Claude fills in

Engine: sherpa-onnx SenseVoice (zh / en / yue / ja / ko, offline, CPU) with Silero VAD. ~10x real time on a laptop CPU.
--engine qwen3 uses Qwen3-ASR-0.6B int8 instead (same VAD, same output shape, engine named in the transcript header). Its model
folder is never downloaded by this script: when it is missing the run stops with the folder and the download to fetch by hand.
Deps: pip install sherpa-onnx numpy (and imageio-ffmpeg when ffmpeg is not on PATH). Models (~1 GB once) go to
~/.cache/media-digest/models (override with MEDIA_DIGEST_HOME). Downloads come from GitHub releases only.
Windows-safe: ffmpeg's frame pattern is run from inside the frames folder, so a '%' in the file name cannot break it.
--frames-only skips the speech step: with --at it only adds the cue frames to an existing folder.
Scene detection, the 16x16 near-duplicate check and the cue frames are adapted from bradautomates/claude-video (MIT).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import wave
from collections import namedtuple
from functools import lru_cache
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = Path(os.environ.get("MEDIA_DIGEST_HOME", Path.home() / ".cache" / "media-digest"))
MODELS = HOME / "models"
SV_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SV_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SV_NAME}.tar.bz2"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
QW_NAME = "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
QW_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{QW_NAME}.tar.bz2"
QW_FILES = ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx", "tokenizer")
ENGINES = ("sensevoice", "qwen3")
ENGINE_LABEL = {"sensevoice": "SenseVoice", "qwen3": "Qwen3-ASR"}
AUDIO_ONLY = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".wma"}
SR = 16000
SCENE_THRESHOLD = 0.20   # ffmpeg scene score that counts as "the picture changed"
SCENE_MIN_FRAMES = 8     # fewer candidates than this: the video is static, fall back to one every --frames-every s
DEDUP_THUMB = 16         # near-duplicate check compares 16x16 RGB thumbnails
DEDUP_THRESHOLD = 2.0    # mean per-pixel difference (0-255) against the last kept frame at or under this: drop
MODES = ("scene", "every", "off")
# the frame options of this run. digest() keeps its old seven positional arguments (wa-tracker's tests call and fake it that way),
# so main() hands the new options over here instead.
FrameOptions = namedtuple("FrameOptions", "mode cap dedup at frames_only")
FRAME_OPTS = FrameOptions("scene", 100, True, (), False)

OUTLINE = """## Summary (Claude fills this in; delegate the frame reading to a cheaper model, keep the judgement here)
1. **Structure in order, with times** — one line per section: [mm:ss] what happens.
2. **Every number, price and claim** — as stated, with the slide or the speaker's words; mark which have a source on screen.
3. **Demo steps** — what was clicked/shown, in order; note anything that failed or never appeared on screen.
4. **Questions asked and whether they were answered** — chat or spoken; a table: time · who · question · answered? (quote the answer).
5. **Objections handled** — each objection and the reply used.
6. **Offer / ask / next step** — prices, bonuses, deadlines, how to pay or sign up, contact route.
7. **Checks** — arithmetic that does not add up, claims without sources, contradictions between slide and speech, gaps in the recording.
8. **What it means for us** — three lines at most; what to copy, what to avoid, what to confirm.
Read `transcript.md` for the words (English terms inside Chinese speech are often garbled — infer from context, flag names) and the `frames/` JPEGs for what was on screen (take each frame's time from `frames/index.csv`; write `frames-notes.md`: one line per frame — file, time, what the screen shows, readable text and numbers, visible chat lines with their timestamps). Then write `summary.md` with the eight sections above.
"""


def log(msg):
    print(msg, flush=True)


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # noqa: WPS433
    except ImportError:
        log("ffmpeg not on PATH; installing imageio-ffmpeg (bundles a binary) ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"])
        import imageio_ffmpeg  # noqa: WPS433,WPS440
    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_pip(module, package):
    try:
        __import__(module)
    except ImportError:
        log(f"installing {package} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", package])


def download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {url} -> {dest}")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done % (50 << 20) < (1 << 20):
                log(f"  {done / 1e6:.0f} / {total / 1e6:.0f} MB")
    tmp.replace(dest)


class ModelMissing(Exception):
    """A model that is fetched by hand; the message says where it goes and where it comes from."""


def qwen3_dir():
    d = MODELS / QW_NAME
    gone = [n for n in QW_FILES if not (d / n).exists()]
    if gone:
        raise ModelMissing(f"the qwen3 model is missing: {d} (not found: {', '.join(gone)}).\n"
                           f"Download it once (about 1 GB unpacked): {QW_URL}\n"
                           f"and extract it into {MODELS}. This script never downloads the qwen3 model itself.")
    return d


def ensure_models(engine="sensevoice"):
    if engine == "qwen3":
        qw_dir = qwen3_dir()
        vad = MODELS / "silero_vad.onnx"
        if not vad.exists():
            download(VAD_URL, vad)
        return qw_dir, vad
    sv_dir = MODELS / SV_NAME
    if not (sv_dir / "model.int8.onnx").exists():
        tar = MODELS / f"{SV_NAME}.tar.bz2"
        if not tar.exists():
            download(SV_URL, tar)
        log("extracting the SenseVoice model ...")
        with tarfile.open(tar, "r:bz2") as t:
            t.extractall(MODELS)
        tar.unlink(missing_ok=True)
    vad = MODELS / "silero_vad.onnx"
    if not vad.exists():
        download(VAD_URL, vad)
    return sv_dir, vad


def probe(ff, path):
    """(duration_s, has_video) via ffprobe when present, else ffmpeg's stderr."""
    ffprobe = shutil.which("ffprobe") or str(Path(ff).with_name("ffprobe" + (".exe" if ff.endswith(".exe") else "")))
    if Path(ffprobe).exists():
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                              "-of", "json", str(path)], capture_output=True, text=True).stdout
        try:
            j = json.loads(out)
            dur = float(j.get("format", {}).get("duration") or 0)
            has_video = any(s.get("codec_type") == "video" for s in j.get("streams", []))
            return dur, has_video
        except (ValueError, TypeError):
            pass
    err = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True).stderr
    has_video = "Video:" in err and path.suffix.lower() not in AUDIO_ONLY
    dur = 0.0
    for line in err.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            dur = int(h) * 3600 + int(m) * 60 + float(s)
    return dur, has_video


def to_wav(ff, src, wav):
    subprocess.check_call([ff, "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
                           "-c:a", "pcm_s16le", str(wav)])


class FramesError(Exception):
    """The frame step cannot be trusted (ffmpeg gave no usable timestamps); the message is the plain reason."""


def hms(s):
    """MM:SS under an hour, H:MM:SS from an hour."""
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def cue_name(t):
    t = int(t)
    return f"cue_{t // 3600:02d}{t % 3600 // 60:02d}{t % 60:02d}.jpg"


def parse_at(text):
    """'4:32,7:10' -> [272.0, 430.0]: SS, MM:SS or H:MM:SS, sorted, one per cue file name. ValueError on a bad token."""
    seen = {}
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"not a time: {token!r}") from None
        if len(parts) > 3 or any(not (0 <= n < float("inf")) for n in nums):
            raise ValueError(f"not a time: {token!r}")
        t = 0.0
        for n in nums:
            t = t * 60 + n
        seen.setdefault(cue_name(t), t)
    if not seen:
        raise ValueError("--at needs at least one time, like 4:32,7:10")
    return sorted(seen.values())


def pick_sync(help_text):
    """('-fps_mode','vfr') or ('-vsync','vfr') from ffmpeg's `-h full` text; ffmpeg 9 lists options with stream specifiers."""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    if re.search(r"(?m)^\s*-fps_mode\b", help_text):
        return ("-fps_mode", "vfr")
    if re.search(r"(?m)^\s*-vsync\b", help_text):
        return ("-vsync", "vfr")
    raise FramesError("this ffmpeg lists neither -fps_mode nor -vsync, so frame times cannot be trusted; update ffmpeg")


@lru_cache(maxsize=4)
def sync_args(ff):
    out = subprocess.run([ff, "-hide_banner", "-h", "full"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return pick_sync(out.stdout + out.stderr)


def showinfo_times(stderr, count, capped=False):
    """The pts_time of each frame ffmpeg wrote; FramesError when the count or the order is off (never mislabel a frame).
    capped: the run stopped at -frames:v, and ffmpeg may log one more filtered frame than it wrote; the first `count` count."""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    stamps = []
    for line in stderr.splitlines():
        m = re.search(r"pts_time:\s*(\S+)", line) if "showinfo" in line else None
        if m:
            try:
                stamps.append(float(m.group(1)))
            except ValueError:
                raise FramesError("ffmpeg gave an unreadable frame time; no frame can be labelled") from None
    stamps = stamps[:count] if capped and len(stamps) > count else stamps
    if len(stamps) != count or any(t != t for t in stamps) or any(b < a for a, b in zip(stamps, stamps[1:])):
        raise FramesError(f"ffmpeg wrote {count} frames but gave {len(stamps)} usable frame times (or they were out of order); "
                          f"refusing to label frames with wrong times")
    return stamps


def cut(ff, src, folder, vf, before_input, pattern, after_input=()):
    """Run ffmpeg inside `folder` with a relative output pattern -> (returncode, stderr text)."""
    # run inside folder with a relative pattern: a '%' in src's folder name must never reach the pattern
    cmd = [ff, "-hide_banner", "-loglevel", "info", "-nostats", "-y", *before_input, "-i", str(src.resolve()),
           "-vf", vf, *after_input, "-q:v", "5", pattern]
    done = subprocess.run(cmd, cwd=str(folder), stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stderr


def frame_files(folder: Path):
    """frame_N.jpg files in numeric order (frame_10000 comes after frame_1001, which a plain sort gets wrong)."""
    return sorted(folder.glob("frame_*.jpg"), key=lambda f: int(re.sub(r"\D", "", f.stem) or 0))


def clear_frames(folder: Path, cues=True):
    for pat in ("frame_*.jpg", "cue_*.jpg") if cues else ("frame_*.jpg",):
        for f in folder.glob(pat):
            f.unlink()


def every_frames(ff, src, folder: Path, every):
    """One frame every `every` seconds; a clip under every/2 s gives its first frame. -> [{file, seconds, reason}]"""
    folder.mkdir(parents=True, exist_ok=True)
    clear_frames(folder, cues=False)
    code, err = cut(ff, src, folder, f"fps=1/{every},scale=800:-2,showinfo", [], "frame_%04d.jpg")
    first_only = not any(folder.glob("frame_*.jpg"))
    if first_only:
        # a clip under every/2 seconds gives no frame, and ffmpeg 9 calls that an error: take its first frame.
        # A broken file fails here too, so it still stops the run.
        code, err = cut(ff, src, folder, "scale=800:-2,showinfo", [], "frame_0001.jpg", ["-frames:v", "1"])
        if code:
            sys.stderr.write(err)
            raise subprocess.CalledProcessError(code, ff)
    elif code:
        sys.stderr.write(err)
        raise subprocess.CalledProcessError(code, ff)
    files = frame_files(folder)
    stamps = showinfo_times(err, len(files), capped=first_only)
    return [{"file": f.name, "seconds": t, "reason": "first-frame" if i == 0 else "uniform"}
            for i, (f, t) in enumerate(zip(files, stamps))]


def scene_frames(ff, src, folder: Path, threshold=SCENE_THRESHOLD):
    """The first frame plus every frame where ffmpeg's scene score passes `threshold`. -> [{file, seconds, reason}]"""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    folder.mkdir(parents=True, exist_ok=True)
    clear_frames(folder, cues=False)
    vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',scale=800:-2,showinfo"
    code, err = cut(ff, src, folder, vf, [], "frame_%04d.jpg", list(sync_args(ff)))
    if code:
        sys.stderr.write(err)
        raise subprocess.CalledProcessError(code, ff)
    files = frame_files(folder)
    if not files:
        raise FramesError("ffmpeg wrote no frames from this video")
    stamps = showinfo_times(err, len(files))
    rows = [{"file": f.name, "seconds": t, "reason": "scene-change"} for f, t in zip(files, stamps)]
    rows[0]["reason"] = "first-frame"
    return rows


def thumbnails(ff, folder: Path, count):
    """One 16x16 RGB thumbnail per frame_NNNN.jpg (1..count) from one ffmpeg pass; [] on any trouble (dedup then does nothing)."""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-start_number", "1", "-i", "frame_%04d.jpg",
           "-vf", f"scale={DEDUP_THUMB}:{DEDUP_THUMB},format=rgb24", "-f", "rawvideo", "-"]
    try:
        done = subprocess.run(cmd, cwd=str(folder), capture_output=True)
    except OSError:
        return []
    chunk = DEDUP_THUMB * DEDUP_THUMB * 3
    if done.returncode or len(done.stdout) != chunk * count:
        return []
    return [done.stdout[i * chunk:(i + 1) * chunk] for i in range(count)]


def dedupe(rows, thumbs, folder: Path, threshold=DEDUP_THRESHOLD):
    """Drop frames within `threshold` mean difference of the last kept one and delete their JPEGs. -> (kept, dropped count).
    Fail-open: thumbnails that do not line up with the rows change nothing."""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    if len(thumbs) != len(rows) or len(rows) <= 1:
        return rows, 0
    kept, last = [rows[0]], thumbs[0]
    for row, thumb in zip(rows[1:], thumbs[1:]):
        if sum(abs(x - y) for x, y in zip(thumb, last)) / len(thumb) <= threshold:
            (folder / row["file"]).unlink(missing_ok=True)
        else:
            kept.append(row)
            last = thumb
    return kept, len(rows) - len(kept)


def even_indices(count, n):
    """n evenly spaced indices out of count, first and last kept."""
    if n >= count:
        return list(range(count))
    if n <= 1:
        return [0]
    return [round(i * (count - 1) / (n - 1)) for i in range(n)]


def thin_and_renumber(rows, folder: Path, cap):
    """Even-sample to `cap` (first and last kept), delete the rest, renumber frame_0001.jpg... in time order."""
    keep = [rows[i] for i in even_indices(len(rows), cap)]
    keep_files = {r["file"] for r in keep}
    for r in rows:
        if r["file"] not in keep_files:
            (folder / r["file"]).unlink(missing_ok=True)
    for i, r in enumerate(keep, 1):
        new = f"frame_{i:04d}.jpg"
        if r["file"] != new:
            (folder / r["file"]).replace(folder / new)  # new <= old and ascending: never lands on a kept file
            r["file"] = new
    return keep


def frames(ff, src, folder: Path, mode, every, cap, dedup=True):
    """The frames of a video in `folder`. -> (rows [{file, seconds, reason}], info {mode, cuts, dropped, kept}).
    scene: scene cuts (one every `every` s when under SCENE_MIN_FRAMES), then near-duplicates out, then the cap."""
    if mode == "every":
        rows = every_frames(ff, src, folder, every)
        return rows, {"mode": "every", "cuts": 0, "dropped": 0, "kept": len(rows)}
    rows = scene_frames(ff, src, folder)
    info = {"mode": "scene", "cuts": len(rows) - 1}
    if len(rows) < SCENE_MIN_FRAMES:
        rows = every_frames(ff, src, folder, every)
        info["mode"] = "uniform"
    dropped = 0
    if dedup and len(rows) > 1:
        rows, dropped = dedupe(rows, thumbnails(ff, folder, len(rows)), folder)
        if dropped:  # the survivors are not numbered without gaps any more
            rows = thin_and_renumber(rows, folder, len(rows))
    info.update(dropped=dropped, kept=len(rows))
    if len(rows) > cap:
        rows = thin_and_renumber(rows, folder, cap)
    return rows, info


def cue_frames(ff, src, folder: Path, times, dur):
    """One frame at each time in `times` -> (rows, notes). A time past the end is a note, not a file."""
    # adapted from bradautomates/claude-video skills/watch/scripts/frames.py @ 03ceb42 (MIT)
    folder.mkdir(parents=True, exist_ok=True)
    rows, notes = [], []
    for t in times:
        name = cue_name(t)
        (folder / name).unlink(missing_ok=True)
        past = f"cue {hms(t)}: past the end ({hms(dur)}), no frame"
        if dur and t >= dur:
            notes.append(past)
            continue
        code, err = cut(ff, src, folder, "scale=800:-2,showinfo", ["-ss", f"{t:.3f}"], name, ["-frames:v", "1", *sync_args(ff)])
        if code or not (folder / name).exists():
            (folder / name).unlink(missing_ok=True)
            tail = [ln.strip() for ln in err.splitlines() if ln.strip()]
            notes.append(f"cue {hms(t)}: ffmpeg gave no frame ({tail[-1] if tail else 'no message'})")
            continue
        rows.append({"file": name, "seconds": t + showinfo_times(err, 1, capped=True)[0], "reason": "cue"})
    return rows, notes


def write_index(folder: Path, rows):
    """frames/index.csv: file,seconds,time,reason, in time order (a cue after the scene frame at the same second)."""
    rows = sorted(rows, key=lambda r: (round(r["seconds"], 2), r["reason"] == "cue"))
    lines = ["file,seconds,time,reason"] + [f"{r['file']},{r['seconds']:.2f},{hms(r['seconds'])},{r['reason']}" for r in rows]
    (folder / "index.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_index(folder: Path):
    """The rows of an existing frames/index.csv whose JPEG is still there."""
    path = folder / "index.csv"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split(",")
            try:
                rows.append({"file": parts[0], "seconds": float(parts[1]), "reason": parts[3]})
            except (ValueError, IndexError):
                pass
    return [r for r in rows if (folder / r["file"]).exists()]


def frames_line(info, every, cap):
    """The words in the brackets after the frame count in DIGEST.md."""
    if info["mode"] == "every":
        return f"one every {every} s"
    if info["mode"] == "uniform":
        return (f"uniform fallback: only {info['cuts']} scene cuts found, one every {every} s, "
                f"{info['dropped']} near-duplicates dropped, cap {cap}")
    return f"scene-aware: {info['cuts']} cuts, {info['dropped']} near-duplicates dropped, cap {cap}"


def read_wave(path):
    import numpy as np
    with wave.open(str(path), "rb") as f:
        assert f.getnchannels() == 1 and f.getsampwidth() == 2
        sr = f.getframerate()
        data = f.readframes(f.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0, sr


def make_recognizer(sherpa_onnx, engine, model_dir, lang, threads):
    if engine == "qwen3":
        return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(conv_frontend=str(model_dir / "conv_frontend.onnx"),
                                                            encoder=str(model_dir / "encoder.int8.onnx"),
                                                            decoder=str(model_dir / "decoder.int8.onnx"),
                                                            tokenizer=str(model_dir / "tokenizer"), num_threads=threads)
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model_dir / "model.int8.onnx"),
                                                          tokens=str(model_dir / "tokens.txt"), num_threads=threads,
                                                          use_itn=True, language=lang, debug=False)


def transcribe(sv_dir, vad_model, wav, lang, threads, engine="sensevoice"):
    import numpy as np
    import sherpa_onnx
    rec = make_recognizer(sherpa_onnx, engine, sv_dir, lang, threads)
    samples, sr = read_wave(wav)
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(vad_model)
    cfg.silero_vad.threshold = 0.45
    cfg.silero_vad.min_silence_duration = 0.45
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 20.0
    cfg.sample_rate = sr
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
    w = cfg.silero_vad.window_size
    pad = np.zeros(int(0.25 * sr), dtype=np.float32)
    rows = []

    def decode(start, seg):
        s = rec.create_stream()
        s.accept_waveform(sr, np.concatenate([pad, seg, pad]))
        rec.decode_stream(s)
        r = s.result
        rows.append({"start": round(start, 2), "end": round(start + len(seg) / sr, 2), "text": r.text.strip(),
                     "lang": getattr(r, "lang", ""), "emotion": getattr(r, "emotion", ""), "event": getattr(r, "event", "")})

    def drain():
        while not vad.empty():
            seg = vad.front
            decode(seg.start / sr, np.array(seg.samples, dtype=np.float32))
            vad.pop()

    i = 0
    while i + w <= len(samples):
        vad.accept_waveform(samples[i:i + w])
        i += w
        drain()
    vad.flush()
    drain()
    return rows, len(samples) / sr


def frame_notes(rows, cues, info, notes, every, cap):
    """The frame lines of DIGEST.md (also the section --frames-only adds)."""
    out = []
    if info:
        out.append(f"- {len(rows)} frames ({frames_line(info, every, cap)}) · times and reasons: frames/index.csv")
        if info["mode"] != "every" and info["kept"] > cap:
            what = f"{info['cuts']} cuts" if info["mode"] == "scene" else f"{info['kept']} frames"
            out.append(f"- capped at {cap} of {what}: raise --max-frames or use --at")
    if cues:
        out.append("- cue frames (--at): " + ", ".join(f"{c['file']} ({hms(c['seconds'])})" for c in cues))
    out += [f"- {n}" for n in notes]
    return out


def digest(path: Path, out_root, every, lang, threads, keep_audio, engine="sensevoice"):
    mode, cap, dedup, at, frames_only = FRAME_OPTS
    ff = ffmpeg_exe()
    sv_dir, vad_model = (None, None) if frames_only else ensure_models(engine)
    out = Path(out_root) / f"{path.stem}_digest" if out_root else path.with_name(f"{path.stem}_digest")
    t0 = time.time()
    dur, has_video = probe(ff, path)
    if frames_only and not has_video:
        raise FramesError(f"{path.name} has no video stream, so --frames-only has nothing to cut")
    out.mkdir(parents=True, exist_ok=True)
    log(f"== {path.name}: {hms(dur)}, {'video' if has_video else 'audio only'} -> {out}")
    fdir = out / "frames"
    rows, cues, notes, info = [], [], [], None
    frame_error = None   # a frame problem never costs the transcript: it is noted, the run goes on, and the exit code is 1
    if has_video:
        if mode != "off":
            if fdir.exists():
                clear_frames(fdir)  # an old run's cues are not part of this one
            try:
                rows, info = frames(ff, path, fdir, mode, every, cap, dedup)
            except FramesError as e:
                frame_error = e
                rows, info = [], None
                clear_frames(fdir)
                (fdir / "index.csv").unlink(missing_ok=True)
        if at:
            try:
                cues, notes = cue_frames(ff, path, fdir, at, dur)
            except FramesError as e:
                frame_error = frame_error or e
                cues = []
                known = {r["file"] for r in read_index(fdir)} if mode == "off" else set()
                for f in fdir.glob("cue_*.jpg"):
                    if f.name not in known:
                        f.unlink()
        if frame_error:
            notes.append(f"frames failed: {frame_error}. Everything else was made; rerun with --frames off to skip the frames")
        if rows or cues:
            keep = [] if mode != "off" else [r for r in read_index(fdir) if r["file"] not in {c["file"] for c in cues}]
            write_index(fdir, keep + rows + cues)
        log(f"   {len(rows)} frames" + (f", {len(cues)} cue frames" if at else "") if mode != "off" or at else "   frames off")
    else:
        if at:
            notes.append("--at ignored: no video stream")
        log("   no video stream, no frames")
    if frames_only:
        block = frame_notes(rows, cues, info, notes, every, cap)
        digest_md = out / "DIGEST.md"
        if digest_md.exists():
            text = re.sub(r"(?ms)^## Frames added [^\n]*\n.*?(?=^## |\Z)", "", digest_md.read_text(encoding="utf-8")).rstrip("\n")
            digest_md.write_text(text + f"\n\n## Frames added {time.strftime('%Y-%m-%d %H:%M')} (--frames-only)\n" + "\n".join(block) + "\n",
                                 encoding="utf-8")
        else:
            digest_md.write_text("\n".join([f"# DIGEST — {path.name}", "", f"- duration {hms(dur)} · frames only, no transcript", *block]) + "\n",
                                 encoding="utf-8")
        log(f"   done in {time.time() - t0:.0f} s: {fdir}")
        if frame_error:
            raise FramesError(str(frame_error))
        return out
    wav = out / "audio.wav"
    to_wav(ff, path, wav)
    rows_t, dur_wav = transcribe(sv_dir, vad_model, wav, lang, threads, engine)
    shown_lang = lang if engine == "sensevoice" else "auto"
    lines = [f"# transcript — {path.name} ({hms(dur_wav)}, {len(rows_t)} segments, {ENGINE_LABEL[engine]} {shown_lang})", ""]
    for r in rows_t:
        tag = r["lang"].strip("<|>")
        if r["text"]:
            lines.append(f"[{hms(r['start'])}] {r['text']}" + (f"  <{tag}>" if tag or engine == "sensevoice" else ""))
    (out / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "transcript.json").write_text(json.dumps({"file": path.name, "duration": dur_wav, "segments": rows_t},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    seg = f"- duration {hms(dur_wav)} · {len(rows_t)} speech segments"
    if not has_video:
        first = [seg + " · audio only"]
    elif info:
        first = [seg + f" · {len(rows)} frames ({frames_line(info, every, cap)})"]
    elif frame_error:
        first = [seg + " · no frames (the frame step failed, see below)"]
    else:
        first = [seg + " · no frames (--frames off)"]
    extra = frame_notes(rows, cues, info, notes, every, cap)[1:] if info else frame_notes(rows, cues, None, notes, every, cap)
    head = [f"# DIGEST — {path.name}", "", *first,
            f"- transcript: transcript.md (language tag per line) · raw: transcript.json" + (" · frames: frames/ (times: frames/index.csv)" if rows or cues else ""),
            *extra, f"- made {time.strftime('%Y-%m-%d %H:%M')} in {time.time() - t0:.0f} s", "", OUTLINE]
    (out / "DIGEST.md").write_text("\n".join(head), encoding="utf-8")
    if not keep_audio:
        wav.unlink(missing_ok=True)
    log(f"   done in {time.time() - t0:.0f} s: {out / 'DIGEST.md'}")
    if frame_error:
        raise FramesError(f"{frame_error} (the transcript and DIGEST.md were still written; rerun with --frames off to skip the frames)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=None, help="parent folder for the *_digest folders (default: beside each input)")
    ap.add_argument("--frames", default=None, choices=MODES,
                    help="scene (default): frames where the picture changed, near-duplicates dropped, capped; every: one every "
                         "--frames-every s; off: none. With --frames-only and --at the default is off (cue frames only)")
    ap.add_argument("--frames-every", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=100, help="cap for --frames scene (cue frames never count)")
    ap.add_argument("--no-dedup", action="store_true", help="keep near-duplicate frames")
    ap.add_argument("--at", default=None, metavar="T1,T2", help="also cut the frame at each time (SS, MM:SS or H:MM:SS), e.g. 4:32,7:10")
    ap.add_argument("--frames-only", action="store_true", help="cut frames only: no audio, no models, no transcript")
    ap.add_argument("--lang", default="auto", choices=["auto", "zh", "en", "yue", "ja", "ko"])
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--engine", default="sensevoice", choices=ENGINES,
                    help="sensevoice (default) or qwen3 (its model folder must already be in the models folder)")
    a = ap.parse_args(argv)
    if a.max_frames < 1:
        ap.error("--max-frames must be 1 or more")
    at = ()
    if a.at is not None:
        try:
            at = parse_at(a.at)
        except ValueError as e:
            ap.error(str(e))
    mode = a.frames or ("off" if a.frames_only and at else "scene")
    if a.frames_only and mode == "off" and not at:
        ap.error("--frames-only needs frames to cut: use --frames scene|every or --at T1,T2")
    if a.engine == "qwen3" and not a.frames_only:
        try:
            qwen3_dir()
        except ModelMissing as e:
            log(f"!! {e}")
            return 1
    if not a.frames_only:
        ensure_pip("numpy", "numpy")
        ensure_pip("sherpa_onnx", "sherpa-onnx")
    global FRAME_OPTS
    FRAME_OPTS = FrameOptions(mode, a.max_frames, not a.no_dedup, at, a.frames_only)
    rc = 0
    try:
        for f in a.files:
            p = Path(f)
            if not p.exists():
                log(f"!! missing: {p}")
                continue
            try:
                digest(p, a.out, a.frames_every, a.lang, a.threads, a.keep_audio, a.engine)
            except FramesError as e:
                log(f"!! {p.name}: {e}")
                rc = 1
    finally:
        FRAME_OPTS = FrameOptions("scene", 100, True, (), False)
    return rc


if __name__ == "__main__":
    sys.exit(main())
