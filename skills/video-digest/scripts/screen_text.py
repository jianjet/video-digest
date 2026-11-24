#!/usr/bin/env python3
"""screen_text.py - the words on screen in downloaded reels and posts, read on this machine. video_digest.py runs it.

    python screen_text.py --check
    python screen_text.py <result.json> <folder> [<folder> ...]

One folder per post, named after its shortcode. A video gives one frame every EVERY seconds. A post's files are named
<code>_<slide>. The result is {shortcode: [[label, text], ...]} with labels such as "00:04", "slide 2" or "slide 3 00:04",
each line once, in order. A folder that breaks, or holds a file none of this can read, gets no entry, so video_digest.py comes back.

RapidOCR (Apache-2.0) is pinned because its models ship inside the wheel, so nothing is downloaded at run time.
The text costs no tokens. Sonnet reading the same frames was the other choice, at roughly 1,500 tokens a frame.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PIN = "rapidocr==3.10.0"
EVERY = 2            # seconds between frames: a tool's name is often on screen for only a few seconds
LONG_EVERY = 10      # for a video over LONG_SECONDS: an hour at 2 s would be 1,800 frames to read
LONG_SECONDS = 600
WIDTH = 720
MIN_SCORE = 0.7      # below this the lines were mostly noise from faces and backgrounds
THREADS = 2          # leaves the rest of the CPU free for other work
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mkv", ".mov"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SOUND_EXT = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac"}
SKIP_EXT = {".json"}  # yt-dlp's info file about a link, kept beside the video


def engine():
    from rapidocr import RapidOCR
    return RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
                            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                            "Global.log_level": "error"})


def length(video):
    """Seconds of video, or 0 when ffprobe cannot tell."""
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def step(video):
    return LONG_EVERY if length(video) > LONG_SECONDS else EVERY


def frames(video, folder, every=EVERY):
    """Frames of one video into its own folder, relative pattern so that no '%' in a path can reach ffmpeg."""
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video.resolve()), "-vf",
                           f"fps=1/{every},scale={WIDTH}:-2", "-q:v", "4", "frame_%05d.jpg"], cwd=str(folder))
    return sorted(folder.glob("frame_*.jpg"))


def mmss(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def read_one(ocr, image):
    """The lines in one image that OCR is sure of."""
    result = ocr(str(image))
    return [text.strip() for text, score in zip(result.txts or (), result.scores or ()) if score >= MIN_SCORE]


def key(line):
    """Two readings of one caption differ in case and punctuation; only letters and digits decide."""
    return re.sub(r"\W+", "", line).lower()


def slide_of(path, folder):
    m = re.fullmatch(re.escape(folder.name) + r"_(\d+)", path.stem)
    return int(m.group(1)) if m else None


def read_folder(ocr, folder):
    found, seen = [], set()

    def add(label, image):
        for line in read_one(ocr, image):
            k = key(line)
            if len(k) >= 3 and k not in seen:
                seen.add(k)
                found.append([label, line])

    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() not in SOUND_EXT | SKIP_EXT]
    readable = [p for p in files if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]
    if len(readable) < len(files):  # a slide this cannot read must not pass for a slide with no text
        raise ValueError(f"cannot read {sorted(p.name for p in files if p not in readable)}")
    for path in sorted(readable, key=lambda p: (slide_of(p, folder) or 0, p.name)):
        slide = slide_of(path, folder)
        if path.suffix.lower() in IMAGE_EXT:
            add(f"slide {slide or 1}", path)
            continue
        every = step(path)
        for n, image in enumerate(frames(path, folder / f"frames_{path.stem}", every)):
            add(f"slide {slide} {mmss(n * every)}" if slide else mmss(n * every), image)
    return found


def main(argv):
    if argv[:1] == ["--check"]:
        if shutil.which("ffmpeg") is None:
            print("ffmpeg is not on PATH")
            return 1
        try:
            import rapidocr  # noqa: F401
        except ImportError:
            print(f"pip install {PIN}")
            return 1
        try:
            import onnxruntime  # noqa: F401  rapidocr's engine; its wheel does not pull it in
        except ImportError:
            print("pip install onnxruntime")
            return 1
        return 0
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = str(THREADS)
    ocr, result = engine(), {}
    for folder in map(Path, argv[1:]):
        try:
            result[folder.name] = read_folder(ocr, folder)
        except Exception as exc:  # one bad video must not cost the others their text
            print(f"{folder.name}: {exc}", file=sys.stderr)
    Path(argv[0]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
