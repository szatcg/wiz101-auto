"""Publish the dashboard to GitHub Pages (a small public repo).

The page (dashboard.html) goes on the repo's `main` branch, which Pages
serves at https://<owner>.github.io/<repo>/. Progress data goes to a
separate `data` branch as data.json, pushed every couple of minutes while it
changes: pushes there don't trigger Pages builds (limited to ~10 an hour),
and the page reads it from raw.githubusercontent.com. Each data push replaces
the previous commit, so the repo stays tiny.

`python -m wiz101_auto publish-setup owner/repo` creates the repo, the page
and Pages; afterwards the dashboard server publishes on its own.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

from loguru import logger

from .dashboard import PAGE, build_data

CONFIG = Path("state") / "pages.json"
PAGE_DIR = Path("state") / "pages" / "site"
DATA_DIR = Path("state") / "pages" / "data"
PUBLISH_SECONDS = 120
_RAW = "https://raw.githubusercontent.com/{repo}/data/data.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def page_html(repo: str) -> str:
    """The dashboard page, reading its data from the repo's data branch."""
    html = PAGE.read_text(encoding="utf-8")
    return html.replace('const DATA_URL = "data.json";', f'const DATA_URL = "{_RAW.format(repo=repo)}";')


def _fresh_repo(path: Path, repo: str, branch: str):
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _git(path, "init", "-q", "-b", branch)
        _git(path, "remote", "add", "origin", f"https://github.com/{repo}.git")


def _push_single_commit(path: Path, branch: str, message: str):
    """Commit everything as the only commit on `branch` and force-push it."""
    _git(path, "add", "-A")
    has_head = _git(path, "rev-parse", "--verify", "HEAD", check=False).strip()
    if has_head:
        _git(path, "commit", "-q", "--amend", "-m", message)
    else:
        _git(path, "commit", "-q", "-m", message)
    _git(path, "push", "-q", "-f", "origin", f"HEAD:{branch}")


def publish_page(repo: str):
    _fresh_repo(PAGE_DIR, repo, "main")
    (PAGE_DIR / "index.html").write_text(page_html(repo), encoding="utf-8")
    (PAGE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    _push_single_commit(PAGE_DIR, "main", "Wizzbot tracker page")


def _public(data: dict) -> dict:
    """What goes on the public page (drop internal read errors)."""
    status = {k: v for k, v in data.get("status", {}).items() if k != "read_error"}
    return {**data, "status": status}


def publish_data(repo: str, last_digest: str = "") -> str:
    """Push data.json if it changed (ignoring timestamps). Returns its digest."""
    data = _public(build_data())
    stable = {k: v for k, v in data.items() if k != "updated"}
    stable["status"] = {k: v for k, v in data["status"].items() if k not in ("time", "uptime_s")}
    digest = hashlib.sha1(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    if digest == last_digest:
        return digest
    _fresh_repo(DATA_DIR, repo, "data")
    (DATA_DIR / "data.json").write_text(json.dumps(data), encoding="utf-8")
    _push_single_commit(DATA_DIR, "data", "progress data")
    return digest


def setup(repo: str):
    """Create the public repo, push the page and data, and turn on Pages."""
    owner, name = repo.split("/", 1)
    exists = subprocess.run(["gh", "repo", "view", repo], capture_output=True, text=True).returncode == 0
    if not exists:
        subprocess.run(
            ["gh", "repo", "create", repo, "--public", "--description", "Wizard101 bot progress tracker"],
            check=True,
        )
    publish_page(repo)
    publish_data(repo)
    enable = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/pages",
         "-f", "source[branch]=main", "-f", "source[path]=/"],
        capture_output=True, text=True,
    )
    if enable.returncode != 0 and "already" not in (enable.stderr + enable.stdout).lower():
        logger.warning(f"could not turn on GitHub Pages: {enable.stderr.strip() or enable.stdout.strip()}")
    CONFIG.parent.mkdir(exist_ok=True)
    CONFIG.write_text(json.dumps({"repo": repo}), encoding="utf-8")
    url = f"https://{owner}.github.io/{name}/"
    logger.success(f"published: {url} (the first build takes a minute or two)")
    return url


def start_publisher() -> threading.Thread | None:
    """Background publishing from the dashboard server, if set up."""
    repo = load_config().get("repo")
    if not repo:
        return None

    def loop():
        digest, page = "", ""
        while True:
            try:
                html = hashlib.sha1(page_html(repo).encode()).hexdigest()
                if html != page:
                    publish_page(repo)  # the page changed (a new version of the dashboard)
                    page = html
                digest = publish_data(repo, digest)
            except Exception as exc:
                logger.warning(f"publishing to GitHub Pages failed: {exc}")
            time.sleep(PUBLISH_SECONDS)

    t = threading.Thread(target=loop, name="pages-publisher", daemon=True)
    t.start()
    every = PUBLISH_SECONDS // 60
    logger.info(f"publishing to https://github.com/{repo} every {every} min while progress changes")
    return t
