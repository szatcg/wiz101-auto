"""A local progress dashboard: completion per world and what the bot is doing.

`python -m wiz101_auto dashboard` serves http://127.0.0.1:8101/ (the page is
`dashboard.html` next to this file; `/data.json` is rebuilt on every request
from the files the bot keeps up to date):

  - docs/QuestList.txt (Wizard City) and docs/quests/<World>.txt: the quest
    lists, in story order, in Spiral Tracker's copy-paste format
  - docs/CompletedQuests.txt: quests the bot saw leave the quest book
  - state/quest_book.json: the quest book at the last ranking (what's in
    progress, what's tracked)
  - state/status.json: heartbeat (zone, objective, level, health, fights)
"""

from __future__ import annotations

import json
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from loguru import logger

from .questlist import WORLDS, load_completed, load_world_lists, norm, quest_status

PORT = 8101
PAGE = Path(__file__).with_name("dashboard.html")
STATUS = Path("state") / "status.json"
QUEST_BOOK = Path("state") / "quest_book.json"
STALE_SECONDS = 30  # heartbeat older than this: the bot isn't running
# Zone ids start with the world's internal name ("WizardCity/WC_Hub").
_ZONE_WORLDS = {"wizardcity": "Wizard City", "dragonspire": "Dragonspyre"}


def world_of_zone(zone: str) -> str:
    """"Krokotopia/KT_Hub" -> "Krokotopia"."""
    head = norm((zone or "").split("/", 1)[0])
    if head in _ZONE_WORLDS:
        return _ZONE_WORLDS[head]
    return next((w for w in WORLDS if norm(w) == head), "")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_data(docs: Path = Path("docs")) -> dict:
    status = _read_json(STATUS)
    book = _read_json(QUEST_BOOK)
    completed = load_completed(docs / "CompletedQuests.txt")
    lists = load_world_lists(docs)
    book_names = [q.get("name", "") for q in book.get("quests", [])]
    # Where the wizard is: the live zone; while stopped, the world at the last
    # quest-book reading; else the furthest world with a completed quest.
    here = world_of_zone(status.get("zone", "")) or world_of_zone(book.get("world", ""))
    if not here:
        done_names = {norm(n) for n in completed}
        for name in WORLDS:
            if any(norm(q.name) in done_names for q in lists.get(name, [])):
                here = name
    here_index = WORLDS.index(here) if here in WORLDS else -1

    worlds = []
    for i, name in enumerate(WORLDS):
        listed = lists.get(name, [])
        st = quest_status(listed, completed, book_names, later_world_reached=0 <= i < here_index)
        # Areas in story order; an area visited again later (Marleybone's Royal
        # Museum) is its own group there.
        areas: list[dict] = []
        for q in listed:
            if not areas or areas[-1]["name"] != q.area:
                areas.append({"name": q.area, "quests": []})
            row = {"index": q.index, "name": q.name, "tags": q.tags, "status": st[q.name]}
            areas[-1]["quests"].append(row)
        for a in areas:
            a["total"] = len(a["quests"])
            a["done"] = sum(q["status"] == "done" for q in a["quests"])
        done = sum(v == "done" for v in st.values())
        worlds.append({
            "name": name,
            "has_list": bool(listed),
            "total": len(listed),
            "done": done,
            "pct": round(100 * done / len(listed)) if listed else (100 if 0 <= i < here_index else 0),
            "current": name == here,
            "reached": 0 <= i <= here_index,
            "areas": areas,
        })

    beat = status.get("time", 0)
    running = bool(beat) and time.time() - beat < STALE_SECONDS and status.get("state") != "stopped"
    return {
        "updated": time.time(),
        "running": running,
        "status": status,
        "current": {
            "world": here,
            "quest": book.get("tracking", ""),
            "area": book.get("tracking_area", ""),
            "objective": status.get("objective") or "",
        },
        "book": book.get("quests", []),
        "recent": completed[-12:][::-1],
        "worlds": worlds,
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path.startswith("/data.json"):
            body = json.dumps(build_data()).encode("utf-8")
            kind = "application/json"
        elif self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            kind = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console quiet
        pass


def is_serving(port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int = PORT, open_browser: bool = True):
    """Serve the dashboard until interrupted (blocks)."""
    url = f"http://127.0.0.1:{port}/"
    if is_serving(port):
        logger.info(f"dashboard already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    logger.info(f"dashboard at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
