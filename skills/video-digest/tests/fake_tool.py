"""Stands in for gallery-dl, yt-dlp and media_digest.py in the tests, so nothing touches the network or the speech model.

    fake_tool.py gallery ...   prints FAKE_LOG and the lines in FAKE_LINES, writes the saved replies FAKE_PAGE (one) and
                               FAKE_PAGES (JSON [[number, body], ...]), then hangs (FAKE_HANG) or exits FAKE_RC
                               with -D it is a post's download instead: one image (three slides for FAKE_CAROUSEL), or
                               FAKE_GALLERY_ERROR for FAKE_BAD; FAKE_GALLERY_WARNING goes to stderr; FAKE_PAGE is saved too
    fake_tool.py ytdlp ...     writes a small audio file and, with -k, the video; fails with FAKE_YTDLP_ERROR for shortcodes
                               in FAKE_BAD; with FAKE_YTDLP_WARNING set, those shortcodes get the file, exit 0 and that warning
    fake_tool.py digest ...    writes <stem>_digest/transcript.md; shortcodes in FAKE_SILENT get no speech; stops at a video,
                               as the real one does on a clip too short for its frame step
    fake_tool.py screen ...    --check exits 1 with FAKE_NO_OCR set; otherwise writes the JSON result: text for FAKE_SCREEN,
                               nothing for the rest, and no entry at all for FAKE_SCREEN_BROKEN
"""
import json
import os
import re
import sys
import time
from pathlib import Path


def gallery(args):
    if "-D" in args:  # a post's files; a listing never passes -D
        code = re.search(r"/p/([^/]+)/", args[-1]).group(1)
        if "FAKE_PAGE" in os.environ:
            Path("01_https_www.instagram.com_api_v1_media_info.txt").write_text(os.environ["FAKE_PAGE"], encoding="utf-8")
        if code in os.environ.get("FAKE_BAD", "").split(","):
            print(os.environ.get("FAKE_GALLERY_ERROR", "[instagram][error] boom"), file=sys.stderr)
            sys.exit(4)
        print(os.environ.get("FAKE_GALLERY_WARNING", ""), file=sys.stderr)
        folder = Path(args[args.index("-D") + 1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{code}_1.jpg").write_bytes(b"image")
        if code in os.environ.get("FAKE_CAROUSEL", "").split(","):
            (folder / f"{code}_2.mp4").write_bytes(b"video")
            (folder / f"{code}_10.mp4").write_bytes(b"video")
        return
    log = os.environ.get("FAKE_LOG", "")
    for line in log.splitlines():
        print(line, file=sys.stderr, flush=True)
    lines = Path(os.environ["FAKE_LINES"]).read_text(encoding="utf-8")
    print(lines, flush=True)
    Path(os.environ["FAKE_RAN"]).write_text("ran", encoding="utf-8")
    if "FAKE_PAGE" in os.environ:  # what --write-pages leaves in the working folder
        Path("02_https_www.instagram.com_graphql_query.txt").write_text(os.environ["FAKE_PAGE"], encoding="utf-8")
    for number, body in json.loads(os.environ.get("FAKE_PAGES", "[]")):  # several, as [[number, body], ...]; the number may pass 99
        Path(f"{number:02}_https_www.instagram.com_graphql_query.txt").write_text(body, encoding="utf-8")
    if os.environ.get("FAKE_HANG"):
        time.sleep(30)
    sys.exit(int(os.environ.get("FAKE_RC", "0")))


def ytdlp(args):
    url = args[-1]
    template = args[args.index("-o") + 1]
    code = Path(template).name.split(".")[0]
    bad = code in os.environ.get("FAKE_BAD", "").split(",")
    if os.environ.get("FAKE_YTDLP_ARGS"):  # every call's arguments, one JSON line each
        with open(os.environ["FAKE_YTDLP_ARGS"], "a", encoding="utf-8") as f:
            f.write(json.dumps(args) + "\n")
    if "--write-info-json" in args:
        Path(template.replace("%(ext)s", "info.json")).write_text(json.dumps(
            {"webpage_url": url, "upload_date": "20261101", "uploader": "someone", "title": f"title of {code}",
             "description": "the description"}), encoding="utf-8")
    if code in os.environ.get("FAKE_NO_VIDEO", "").split(","):  # a photo post given as a link
        print(f"ERROR: [Instagram] {code}: There is no video in this post", file=sys.stderr)
        sys.exit(1)
    if bad and "FAKE_YTDLP_WARNING" not in os.environ:
        error = os.environ.get("FAKE_YTDLP_ERROR", "ERROR: boom")
        if "audio codec" in error:  # the reel came down; only pulling the sound out of it failed
            Path(template.replace("%(ext)s", "mp4")).write_bytes(b"video")
        print(error, file=sys.stderr)
        sys.exit(1)
    if bad:  # exit 0 with a file, but a warning on stderr: yt-dlp dropping a dead cookie and going on logged out
        print(os.environ["FAKE_YTDLP_WARNING"], file=sys.stderr)
    split = code in os.environ.get("FAKE_SPLIT", "").split(",")  # the audio-only format, left behind by -k
    Path(template.replace("%(ext)s", "fdash-a.m4a" if split else "m4a")).write_bytes(b"audio")
    if "-k" in args:
        Path(template.replace("%(ext)s", "mp4")).write_bytes(b"video")


def digest(args):
    out = Path(args[args.index("--out") + 1])
    assert args[args.index("--threads") + 1] == "2"
    silent = os.environ.get("FAKE_SILENT", "").split(",")
    for f in args[:args.index("--out")]:
        stem = Path(f).stem
        if Path(f).suffix == ".mp4":
            sys.exit(1)
        folder = out / f"{stem}_digest"
        folder.mkdir(parents=True)
        body = "# transcript - x\n\n" + ("" if stem in silent else f"[00:01] words from {stem}  <en>\n")
        (folder / "transcript.md").write_text(body, encoding="utf-8")


def screen(args):
    if args[0] == "--check":
        if os.environ.get("FAKE_NO_OCR"):
            print("pip install rapidocr==3.10.0")
            sys.exit(1)
        return
    shown = os.environ.get("FAKE_SCREEN", "").split(",")
    broken = os.environ.get("FAKE_SCREEN_BROKEN", "").split(",")
    names = [Path(a).name for a in args[1:]]
    result = {n: [["00:02", f"shown in {n}"]] if n in shown else [] for n in names if n not in broken}
    Path(args[0]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    {"gallery": gallery, "ytdlp": ytdlp, "digest": digest, "screen": screen}[sys.argv[1]](sys.argv[2:])
