"""key_frames.py - the frames of a reel that Claude looks at, kept after the video itself is deleted.

Picks every frame where the picture changed (ffmpeg scene score above SCENE_THRESHOLD) plus one frame every
FRAME_EVERY_SEC so a talking head still gets covered, drops frames closer than a second apart, and thins the rest to
MAX_FRAMES spread over the whole reel (first one kept). Writes <dest>/<nn>_<mm>m<ss>s.jpg and <dest>/index.csv
(file,seconds,time,reason). OCR (screen_text.py) still reads its own frames; these are for Claude's eyes.
"""
import csv
import re
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def duration(video):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def scene_times(video, threshold):
    """Seconds at which the picture changed by more than threshold (0..1)."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
                           f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return [float(t) for t in PTS_RE.findall(done.stderr or "")]


def pick(length, scenes, every, cap, gap=1.0):
    """[(seconds, reason)] sorted by time: the first frame, scene changes, coverage frames; none closer than gap s
    (a scene change wins over a coverage frame at the same moment); at most cap, spread evenly, first one kept."""
    if length <= 0 or cap < 1:
        return []
    every = max(float(every), 0.5)
    cands = [(0.0, "start")] + [(t, "scene") for t in scenes if 0 < t < length]
    k = 1
    while k * every < length:
        cands.append((round(k * every, 3), "every"))
        k += 1
    rank = {"start": 0, "scene": 1, "every": 2}
    cands.sort(key=lambda c: (c[0], rank[c[1]]))
    kept = []
    for t, why in cands:
        if kept and t - kept[-1][0] < gap:
            if rank[why] < rank[kept[-1][1]] and kept[-1][1] != "start":
                kept[-1] = (t, why)
            continue
        kept.append((t, why))
    if len(kept) > cap:
        if cap == 1:
            return kept[:1]
        step = (len(kept) - 1) / (cap - 1)
        kept = [kept[round(i * step)] for i in range(cap)]
    return kept


def mmss(seconds):
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def cut(video, seconds, folder, name, width):
    """One frame; relative output name so no '%' in a path can reach ffmpeg's image writer."""
    done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(Path(video).resolve()),
                           "-frames:v", "1", "-vf", f"scale='min(iw,{int(width)})':-2", "-q:v", "3", name],
                          cwd=str(folder), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW)
    return done.returncode == 0 and (Path(folder) / name).exists()


def keep(videos, dest, threshold=0.30, every=3.0, cap=20, width=1080):
    """Key frames of one post's videos (several for a carousel) into dest. Returns the number of frames written."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for old in list(dest.glob("*.jpg")) + list(dest.glob("index.csv")):
        old.unlink()
    videos = [Path(v) for v in videos]
    if not videos:
        return 0
    per_video = max(1, cap // len(videos))
    rows, n = [], 0
    for slide, video in enumerate(videos, 1):
        length = duration(video)
        for seconds, why in pick(length, scene_times(video, threshold), every, per_video):
            n += 1
            tag = f"s{slide}_" if len(videos) > 1 else ""
            name = f"{n:02d}_{tag}{mmss(seconds).replace(':', 'm')}s.jpg"
            if cut(video, seconds, dest, name, width):
                label = f"slide {slide} {mmss(seconds)}" if len(videos) > 1 else mmss(seconds)
                rows.append([name, f"{seconds:.2f}", label, why])
    with open(dest / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "seconds", "time", "reason"])
        w.writerows(rows)
    return len(rows)
