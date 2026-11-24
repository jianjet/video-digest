#!/usr/bin/env python3
"""brain.py - the bridge between video-digest and the Brain (an Obsidian vault kept in a git repo).

    python brain.py index                     pull the vault, then list every topic note, and the tally of Jet's
                                              Approve / Reject choices, as JSON for Claude
    python brain.py propose <file.json> ...   check each proposal and send it to n8n for Telegram approval
    python brain.py propose --dry-run <file>  check and print, send nothing

Nothing here writes to the vault or GitHub: n8n commits a note only after the user taps Approve in Telegram.
Settings come from .env in the output folder (see vd_config.py): BRAIN_PATH, N8N_WEBHOOK_URL, IGDIGEST_SECRET.

A proposal file (Claude writes it):
    {"action": "create" | "update", "path": "Topics/<area>/<slug>.md", "title": "...",
     "summary": "3-6 short lines for Telegram", "content": "the whole note as it should read",
     "sources": ["https://www.instagram.com/reel/XXXX/ @ 01:23"], "id": "optional"}
n8n saves every Approve or Reject as video-digest/choices/<id>.json in the vault repo:
    {"at": "...", "decision": "approve" | "reject", "path": "Topics/<area>/<slug>.md", "title": "...", "summary": "..."}
index tallies them per area and per note, so a run can rank what Jet rejected before lower (never drop it).

base_sha is added here from the local file (git's blob hash, the same value GitHub reports), so n8n can refuse a
proposal whose note changed after it was written.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vd_config  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_CONTENT = 200_000
MAX_SUMMARY = 1500
MAX_TITLE = 120
ACTIONS = ("create", "update")
CHOICES_DIR = "video-digest/choices"
DECISIONS = ("approve", "reject")
RECENT = 40


class ProposalError(ValueError):
    pass


def git(repo, *args, check=True):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=NO_WINDOW)
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done


def topics_dir(cfg):
    path = (cfg.get("BRAIN_PATH") or "").strip()
    if not path:
        raise ProposalError("BRAIN_PATH is not set in .env (the Topics folder inside your vault)")
    folder = Path(path)
    if not folder.is_dir():
        raise ProposalError(f"BRAIN_PATH does not exist: {folder}")
    return folder


def repo_root(folder):
    return Path(git(folder, "rev-parse", "--show-toplevel").stdout.strip())


def pull(repo):
    """Brings in notes approved since the last run. A failed pull is reported, not fatal: proposals may then be stale,
    and n8n's stale check catches that."""
    done = git(repo, "pull", "--ff-only", check=False)
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()


def blob_sha(repo, file):
    return git(repo, "hash-object", str(file)).stdout.strip()


def first_line(text, prefix):
    return next((ln[len(prefix):].strip() for ln in text.splitlines() if ln.startswith(prefix)), "")


def describe(text):
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith(("#", "---", "-", "*", ">")):
            return s[:200]
    return ""


def choices(repo, topics_rel="Topics"):
    """Jet's Telegram decisions, read from the repo: counts per area and per note, the latest few with their titles, and
    a warning per file that could not be read (it is skipped, never fatal)."""
    folder = Path(repo) / CHOICES_DIR
    areas, notes, recent, warnings = {}, {}, [], []
    prefix = topics_rel.rstrip("/") + "/"
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            decision = str(c.get("decision", "")).lower() if isinstance(c, dict) else ""
            path = str(c.get("path", "")) if isinstance(c, dict) else ""
            if decision not in DECISIONS or not path:
                raise ValueError("needs decision approve or reject, and a path")
        except (OSError, ValueError) as exc:
            warnings.append(f"{f.name}: {exc}")
            continue
        rest = path[len(prefix):] if path.startswith(prefix) else ""
        area = rest.split("/")[0] if "/" in rest else "(top)"
        for bucket, key in ((areas, area), (notes, path)):
            bucket.setdefault(key, {"approve": 0, "reject": 0})[decision] += 1
        recent.append({"at": str(c.get("at", "")), "decision": decision, "path": path, "title": str(c.get("title", ""))})
    recent.sort(key=lambda r: r["at"], reverse=True)
    return {"areas": areas, "notes": notes, "recent": recent[:RECENT], "warnings": warnings}


def cmd_index(cfg, out=None):
    topics = topics_dir(cfg)
    repo = repo_root(topics)
    warning = pull(repo)
    notes = []
    for f in sorted(topics.rglob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        notes.append({"path": f.relative_to(repo).as_posix(), "title": first_line(text, "# ") or f.stem,
                      "about": describe(text), "bytes": f.stat().st_size, "sha": blob_sha(repo, f)})
    topics_rel = topics.relative_to(repo).as_posix()
    result = {"topics_dir": topics_rel, "pull_warning": warning, "notes": notes, "choices": choices(repo, topics_rel)}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "note"


def check(proposal, topics_rel, repo):
    """The proposal as it will be sent, with base_sha, or ProposalError saying what is wrong."""
    if not isinstance(proposal, dict):
        raise ProposalError("a proposal must be a JSON object")
    action = str(proposal.get("action", "")).lower()
    if action not in ACTIONS:
        raise ProposalError(f"action must be create or update, not {proposal.get('action')!r}")
    path = str(proposal.get("path", "")).strip()
    parts = path.split("/")
    if ("\\" in path or path.startswith("/") or ".." in parts or not path.endswith(".md")
            or not path.startswith(topics_rel.rstrip("/") + "/") or any(not p for p in parts)):
        raise ProposalError(f"path must be a .md file under {topics_rel}/ using / separators: {path!r}")
    title = str(proposal.get("title", "")).strip()
    if not title or len(title) > MAX_TITLE:
        raise ProposalError(f"title must be 1-{MAX_TITLE} characters")
    summary = str(proposal.get("summary", "")).strip()
    if not summary or len(summary) > MAX_SUMMARY:
        raise ProposalError(f"summary must be 1-{MAX_SUMMARY} characters")
    content = str(proposal.get("content", ""))
    if not content.strip() or len(content) > MAX_CONTENT:
        raise ProposalError(f"content must be 1-{MAX_CONTENT} characters")
    sources = proposal.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        raise ProposalError("sources must be a list of strings")
    local = repo / path
    if action == "update" and not local.exists():
        raise ProposalError(f"update of a note that does not exist: {path} (use create)")
    if action == "create" and local.exists():
        raise ProposalError(f"create of a note that already exists: {path} (use update and merge into it)")
    return {"id": str(proposal.get("id") or f"{datetime.now():%Y-%m-%d}-{slug(title)}"), "action": action, "path": path,
            "base_sha": blob_sha(repo, local) if action == "update" else None, "title": title, "summary": summary,
            "content": content if content.endswith("\n") else content + "\n", "sources": sources}


def send(payload, url, secret, timeout=30):
    if not url or not secret:
        raise ProposalError("N8N_WEBHOOK_URL and IGDIGEST_SECRET must both be set in .env")
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-IGD-Secret": secret,
                                          "User-Agent": "video-digest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        raise ProposalError(f"n8n answered {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from None
    except urllib.error.URLError as exc:
        raise ProposalError(f"cannot reach n8n: {exc.reason}") from None


def cmd_propose(cfg, files, dry_run=False, root=None):
    root = Path(root) if root else vd_config.home()
    topics = topics_dir(cfg)
    repo = repo_root(topics)
    warning = pull(repo)
    if warning:
        print(f"warning: git pull failed, proposals may be stale: {warning}")
    topics_rel = topics.relative_to(repo).as_posix()
    sent_dir = root / "proposals" / "sent"
    failed = 0
    for name in files:
        f = Path(name)
        try:
            payload = check(json.loads(f.read_text(encoding="utf-8")), topics_rel, repo)
            if dry_run:
                print(json.dumps({**payload, "content": f"<{len(payload['content'])} characters>"}, ensure_ascii=False))
                continue
            status = send(payload, cfg.get("N8N_WEBHOOK_URL"), cfg.get("IGDIGEST_SECRET"))
            sent_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(sent_dir / f.name))
            print(f"sent {payload['action']} {payload['path']} (n8n {status}); approve it in Telegram")
        except (ProposalError, json.JSONDecodeError, OSError) as exc:
            failed += 1
            print(f"{f.name}: not sent: {exc}")
    return 1 if failed else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = vd_config.load()
    try:
        if argv[:1] == ["index"]:
            return cmd_index(cfg, argv[1] if len(argv) > 1 else None)
        if argv[:1] == ["propose"]:
            dry = "--dry-run" in argv
            files = [a for a in argv[1:] if a != "--dry-run"]
            if not files:
                print("usage: brain.py propose [--dry-run] <proposal.json> ...")
                return 2
            return cmd_propose(cfg, files, dry)
    except (ProposalError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
