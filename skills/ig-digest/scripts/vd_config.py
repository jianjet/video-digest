"""vd_config.py - video-digest settings: real environment variables first, then the .env file in the output folder.

The .env file lives in the output folder (VIDEO_DIGEST_HOME, else IG_DIGEST_HOME, else ~/video-digest), never in the
repo, because it holds the webhook secret. A value already set in the environment always wins over the file.
"""
import os
from pathlib import Path

DEFAULTS = {
    "ASR_ENGINE": "sensevoice",       # sensevoice | qwen3 (qwen3's model folder must be downloaded first)
    "SCENE_THRESHOLD": "0.30",         # how big a picture change counts as a new scene
    "FRAME_EVERY_SEC": "3",            # a coverage frame this often, even when nothing changes
    "MAX_FRAMES": "20",                # frames kept per reel for Claude to look at
    "FRAME_WIDTH": "1080",             # small on-screen text stays readable at full reel width
    "MIN_WORDS_PER_MIN": "40",         # below this, a transcript is labelled unreliable
    "BRAIN_PATH": "",                  # the Topics folder inside the vault, e.g. C:/Users/<you>/Documents/Brain/Topics
    "N8N_WEBHOOK_URL": "",             # https://<your-n8n-domain>/webhook/video-digest/propose
    "IGDIGEST_SECRET": "",             # the X-IGD-Secret header value
}
ENGINES = ("sensevoice", "qwen3")


def home():
    return Path(os.environ.get("VIDEO_DIGEST_HOME") or os.environ.get("IG_DIGEST_HOME") or Path.home() / "video-digest")


def read_env_file(path):
    """KEY=VALUE lines; # comments and blank lines ignored; surrounding quotes stripped. A missing file is empty."""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load(root=None):
    """All settings as strings: environment > root/.env > defaults."""
    root = Path(root) if root else home()
    merged = {**DEFAULTS, **read_env_file(root / ".env")}
    for key in set(merged) | set(DEFAULTS):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def get(key, root=None):
    return load(root).get(key, DEFAULTS.get(key, ""))


def number(key, root=None, cast=float):
    value = get(key, root)
    try:
        return cast(value)
    except (TypeError, ValueError):
        return cast(DEFAULTS[key])


def engine(root=None):
    value = get("ASR_ENGINE", root).strip().lower()
    if value not in ENGINES:
        raise ValueError(f"ASR_ENGINE must be one of {', '.join(ENGINES)}, not {value!r}")
    return value
