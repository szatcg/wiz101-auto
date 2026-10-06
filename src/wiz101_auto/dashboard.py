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
import re
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from loguru import logger

from . import lifetime
from .questlist import (
    SIDE_WORLDS,
    WORLDS,
    load_completed,
    load_world_lists,
    norm,
    quest_status,
    world_of_zone,
)

PORT = 8101
ARCS = (
    ("Arc 1", ("Wizard City", "Krokotopia", "Marleybone", "MooShu", "Dragonspyre")),
    ("Arc 2", ("Celestia", "Zafaria", "Avalon", "Azteca", "Khrysalis")),
    ("Arc 3", ("Polaris", "Mirage", "Empyrea")),
    ("Arc 4", ("Karamelle", "Lemuria", "Novus", "Wallaru", "Selenopolis")),
)
PAGE = Path(__file__).with_name("dashboard.html")
STREAM = Path(__file__).with_name("stream.html")
SERVER_STARTED = time.time()  # "live for" on the stream page
STATUS = Path("state") / "status.json"
QUEST_BOOK = Path("state") / "quest_book.json"
STALE_SECONDS = 30  # heartbeat older than this: the bot isn't running


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


THOUGHTS_SHOWN = 12  # the bot's latest decisions on the page (plain English)


def _recent_thoughts() -> list[dict]:
    """The bot's latest decisions, in plain English (as on the stream page)."""
    from .thoughts import read_thoughts

    try:
        events = read_thoughts(limit=THOUGHTS_SHOWN)["events"]
    except Exception:
        return []
    keep = ("time", "tag", "say")
    return [{k: e.get(k, "") for k in keep} for e in events][::-1]


def _quest_steps(quest: str, objective: str, world: str | None) -> dict:
    """The tracked quest's steps: done (crossed off), now, next."""
    from .quest_steps import view

    try:
        return view(quest, objective, world or "")
    except Exception:
        return {"quest": quest, "done": [], "now": objective, "next": []}


_OBJ_DONE = re.compile(r"^(\d\d):(\d\d):(\d\d) \| \w+\s*\| objective done -> now: (['\"])(.*)\4\s*$")


def objective_times(lines: list[str], now: float) -> dict:
    """From activity.log's 'objective done -> now: X' lines: when each
    objective began (epoch, the latest time it did) and how long the ones
    after which another began took. {"started": {text: t}, "took": {text: s}}.
    The log has times only: today's date, a time later than `now` is
    yesterday's."""
    import datetime as dt

    day = dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    seen: list[tuple[str, float]] = []
    for line in lines:
        m = _OBJ_DONE.match(line)
        if not m:
            continue
        t = (day + dt.timedelta(hours=int(m[1]), minutes=int(m[2]), seconds=int(m[3]))).timestamp()
        if t > now + 60:
            t -= 86400
        seen.append((m[5], t))
    started, took = {}, {}
    for i, (text, t) in enumerate(seen):
        started[text] = t
        if i + 1 < len(seen):
            took[text] = max(0, seen[i + 1][1] - t)
        else:
            took.pop(text, None)
    return {"started": started, "took": took}


def _objective_times(steps: dict) -> dict:
    """Seconds each done step took, and when the current one began."""
    from .thoughts import ACTIVITY

    try:
        with ACTIVITY.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    times = objective_times(lines, time.time())
    return {
        "took": {t: times["took"][t] for t in steps.get("done", []) if t in times["took"]},
        "since": times["started"].get(steps.get("now") or ""),
    }


def _is_main(book: dict, listed) -> bool | None:
    """Is the tracked quest the story? On the world's story list, or the book
    says so (the book calls some story quests side quests)."""
    name = book.get("tracking", "")
    if not name:
        return None
    if any(norm(q.name) == norm(name) for q in listed):
        return True
    return any(q.get("name") == name and q.get("main") for q in book.get("quests", []))


_last_area = ""  # the last outdoor area the wizard was in (interiors have no name)


def _zone_sides(book: dict, zone: str, world: str | None, lists: dict, completed: list[str]) -> dict:
    """Side quests done in the area the wizard is in (inside a building or
    dungeon: the last area outside, else the tracked quest's area)."""
    global _last_area
    from .givers import load_guide
    from .side_progress import area_of_zone, area_progress, remember_book
    from .travel_data import _data

    try:
        known = remember_book(book)
        area = area_of_zone(zone, _data()[1]) if zone else ""
        if area:
            _last_area = area
        area = area or _last_area or book.get("tracking_area", "")
        if not area or not world:
            return {}
        return area_progress(area, load_guide(world), known, lists.get(world, []), completed)
    except Exception:
        return {}


ROUTE_STALE_SECONDS = 600.0  # a route not rewritten this long is old news


def _route(running: bool) -> dict:
    """The bot's planned way to the objective's zone (state/route.json), for
    the stream page's navigation graph; {} when not travelling."""
    if not running:
        return {}
    try:
        data = json.loads((Path("state") / "route.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if len(data.get("zones") or []) < 2 or time.time() - data.get("time", 0) > ROUTE_STALE_SECONDS:
        return {}
    return data


def build_data(docs: Path = Path("docs")) -> dict:
    status = _read_json(STATUS)
    book = _read_json(QUEST_BOOK)
    completed = load_completed(docs / "CompletedQuests.txt")
    lists = load_world_lists(docs)
    # Only the main-story quests in the book place us on a world's list: a
    # side quest of the same name (Eudora's crafting quest 'The Razor's
    # Edge' is also Avalon's #132) put Avalon done up to 'Step Down' while
    # the story was on #14, 'Dread of Knight'.
    book_names = [q.get("name", "") for q in book.get("quests", []) if q.get("main")]
    # Where the wizard is: the live zone; while stopped, the world at the last
    # quest-book reading; else the furthest world with a completed quest.
    here = world_of_zone(status.get("zone", "")) or world_of_zone(book.get("world", ""))
    if not here:
        done_names = {norm(n) for n in completed}
        for name in WORLDS:
            if any(norm(q.name) in done_names for q in lists.get(name, [])):
                here = name
    here_index = WORLDS.index(here) if here in WORLDS else -1

    deaths_by_world = lifetime.load().get("deaths_by_world", {})
    worlds = []
    for name in (*WORLDS, *SIDE_WORLDS):
        side = name in SIDE_WORLDS
        i = WORLDS.index(name) if not side else -1
        listed = lists.get(name, [])
        # Side worlds are optional: moving on in the story doesn't finish them.
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
            "side": side,
            "current": name == here,
            "deaths": int(deaths_by_world.get(name, 0)),
            "reached": (0 <= i <= here_index) or (side and (done > 0 or name == here)),
            "areas": areas,
        })

    arcs = []
    for arc, names in ARCS:
        members = [w for w in worlds if w["name"] in names]
        extras = [w for w in worlds if w["side"] and SIDE_WORLDS[w["name"]] == arc]
        listed = [w for w in members if w["has_list"]]
        total = sum(w["total"] for w in listed)
        done = sum(w["done"] for w in listed)
        arcs.append({
            "name": arc,
            "worlds": list(names),
            "total": total,
            "done": done,
            "pct": round(100 * done / total) if total else 0,
            "lists": len(listed),
            "side_worlds": [w["name"] for w in extras],
            "side_done": sum(w["done"] for w in extras),
            "side_total": sum(w["total"] for w in extras),
            "deaths": sum(w["deaths"] for w in members + extras),
            "current": any(w["current"] for w in members),
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
            "objective_age_s": status.get("objective_age_s"),
            "grinding": status.get("activity") == "grinding for experience",
            "main": _is_main(book, lists.get(here or "", [])),
        },
        "sides": _zone_sides(book, status.get("zone", ""), here, lists, completed),
        "steps": (steps := _quest_steps(book.get("tracking", ""), status.get("objective") or "", here)),
        "step_times": _objective_times(steps),
        "route": _route(running),
        "thoughts": _recent_thoughts(),
        "book": book.get("quests", []),
        "recent": completed[-12:][::-1],
        "worlds": worlds,
        "arcs": arcs,
        "deaths": {"total": lifetime.load().get("deaths", 0), "session": status.get("deaths", 0)},
        "live_since": SERVER_STARTED,
    }


BATTLE_PLAN = Path("state") / "battle_plan.json"
PLAN_STALE_SECONDS = 120  # an older plan (the bot stopped mid-fight) isn't shown


def battle_plan() -> dict:
    """The fighter's plan (state/battle_plan.json) while a fight is on."""
    plan = _read_json(BATTLE_PLAN)
    fresh = time.time() - plan.get("time", 0) < PLAN_STALE_SECONDS
    if not (plan.get("active") and fresh and _read_json(STATUS).get("in_battle")):
        return {"active": False}
    return plan


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path.split("?")[0].endswith("data.json"):
            body = json.dumps(build_data()).encode("utf-8")
            kind = "application/json"
        elif self.path.split("?")[0] in ("/", "/index.html"):
            body = PAGE.read_bytes()
            kind = "text/html; charset=utf-8"
        elif self.path.split("?")[0].endswith("thoughts.json"):
            from .thoughts import read_thoughts

            body = json.dumps(read_thoughts()).encode("utf-8")
            kind = "application/json"
        elif self.path.split("?")[0].endswith("battle.json"):
            body = json.dumps(battle_plan()).encode("utf-8")
            kind = "application/json"
        elif self.path.split("?")[0].rstrip("/") == "/control":
            body = CONTROL.read_bytes()  # buttons: start, stop, pause, pet, farm, pin
            kind = "text/html; charset=utf-8"
        elif self.path.split("?")[0] == "/api/status":
            from .control import status

            body = json.dumps(status()).encode("utf-8")
            kind = "application/json"
        elif self.path.split("?")[0].rstrip("/") == "/stream":
            body = STREAM.read_bytes()  # the 1920x1080 stream layout
            kind = "text/html; charset=utf-8"
        elif self.path.split("?")[0].startswith(("/cards/", "/pips/", "/ui/")):
            path = self.path.split("?")[0]
            if path.startswith("/pips/"):
                got = _card_file(path[len("/pips/"):], PIPS)  # the overlay's pip images
            elif path.startswith("/ui/"):
                got = _card_file(path[len("/ui/"):], UI_IMAGES)  # the overlay's sky, portrait
            else:
                got = _card_file(path[len("/cards/"):])
            if got is None:
                self.send_error(404)
                return
            body, kind = got
        elif self.path.split("?")[0].startswith("/sim"):
            got = _sim_route(self.path)
            if got is None:
                self.send_error(404)
                return
            body, kind = got
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (http.server API)
        """The control page's buttons: /api/<action>. Only from the page itself:
        it sends a header another site's page can't (no CORS here), so a
        website can't start or stop the bot through the browser."""
        path = self.path.split("?")[0]
        if not path.startswith("/api/") or self.headers.get("X-Wizzbot") != "control":
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except (ValueError, OSError):
            payload = {}
        from .control import act

        try:
            result = act(path[len("/api/"):], payload)
        except Exception as exc:  # a button must never take the server down
            result = {"ok": False, "message": f"failed: {exc!r}"}
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console quiet
        pass


CONTROL = Path(__file__).with_name("control.html")  # the control page
UI_IMAGES = Path("docs") / "ui_images"  # the stream page's Wizard101 look: sky.jpg, portrait.png
PIPS = Path("docs") / "pip_images"  # Pip.png, Power_Pip.png, <School>_School_Pip.png
CARDS = Path("docs") / "spell_images"  # card art: <school>/<name>_spell.png, index.json (name -> file)


def _card_file(rel: str, folder: Path | None = None) -> tuple[bytes, str] | None:
    """A file of the card art folder (the stream's deck tracker), or of
    `folder` (the pip images), never outside it."""
    from urllib.parse import unquote

    base = (folder or CARDS).resolve()
    f = (base / unquote(rel)).resolve()
    if base not in f.parents or not f.is_file():
        return None
    kind = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
            ".json": "application/json"}.get(f.suffix.lower())
    return (f.read_bytes(), kind) if kind else None


def _sim_route(path: str) -> tuple[bytes, str] | None:
    """The combat simulator's visualizer: /sim and its JSON (simviz.py)."""
    from urllib.parse import parse_qs, urlparse

    from . import simviz

    url = urlparse(path)
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    route = url.path.rstrip("/")
    js = "application/json"
    if route == "/sim":
        return simviz.PAGE.read_bytes(), "text/html; charset=utf-8"
    if route == "/sim/runs.json":
        return json.dumps(simviz.runs()).encode("utf-8"), js
    if route == "/sim/run.json":
        data = simviz.run(q.get("f", ""))
        return (json.dumps(data).encode("utf-8"), js) if data is not None else None
    if route == "/sim/enemies.json":
        return json.dumps(simviz.enemies()).encode("utf-8"), js
    if route == "/sim/start.json":
        return json.dumps(simviz.start(q.get("vs", ""), float(q.get("minutes", 5) or 5))).encode("utf-8"), js
    return None


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
    from .pages import start_publisher

    start_publisher()  # also keep the GitHub Pages copy up to date, if set up
    logger.info(f"dashboard at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
