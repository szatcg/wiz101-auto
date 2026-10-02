"""The control page's actions (http://127.0.0.1:8101/control): start, stop,
pause, restart the bot; ask it for a pet dance trip; farming on or off; pin
a quest. Each is what the command line does, so the page and the terminal
stay interchangeable.
"""

from __future__ import annotations

import contextlib
import io
import json
import time
from pathlib import Path

PAUSE_REQUEST = Path("state") / "pause.request"  # the bot toggles pause when it sees this
CONFIG = "config.yaml"


def _quiet(fn, *args) -> tuple[int, str]:
    """Run a command-line function, returning (exit code, what it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(*args)
    return int(code or 0), out.getvalue().strip()


def status() -> dict:
    """What the page shows: running or not, paused, where, doing what."""
    from . import service
    from .farm import Farm
    from .quest import load_pin

    try:
        st = json.loads(service.STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    pid = service.running_pid()
    beat = st.get("time", 0)
    try:
        book = json.loads((Path("state") / "quest_book.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        book = {}
    farm = Farm.load()
    return {
        "running": bool(pid),
        "paused": st.get("state") == "paused",
        "heartbeat_age": round(time.time() - beat) if beat else None,
        "zone": st.get("zone", ""),
        "objective": st.get("objective", ""),
        "health": st.get("health", ""),
        "level": st.get("level"),
        "in_battle": bool(st.get("in_battle")),
        "farming": bool(farm.active),
        "farm_name": farm.name,
        "pin": load_pin(),
        "tracking": book.get("tracking", ""),
        "quests": [q.get("name") for q in book.get("quests", []) if q.get("name")],
        "pause_pending": PAUSE_REQUEST.exists(),
    }


def act(action: str, payload: dict | None = None) -> dict:
    """Do a button's action; {"ok": bool, "message": str}."""
    from . import service

    payload = payload or {}
    if action == "start":
        if service.running_pid():
            return {"ok": False, "message": "the bot is already running"}
        code, msg = _quiet(service.start, CONFIG, True)
        return {"ok": code == 0, "message": msg or "started"}
    if action == "stop":
        if not service.running_pid():
            return {"ok": False, "message": "the bot isn't running"}
        code, msg = _quiet(service.stop)
        return {"ok": code == 0, "message": msg or "stopped"}
    if action == "restart":
        if service.running_pid():
            _quiet(service.stop)
        code, msg = _quiet(service.start, CONFIG, True)
        return {"ok": code == 0, "message": msg or "restarted"}
    if action == "pause":
        if not service.running_pid():
            return {"ok": False, "message": "the bot isn't running"}
        PAUSE_REQUEST.parent.mkdir(exist_ok=True)
        PAUSE_REQUEST.write_text("1", encoding="utf-8")
        return {"ok": True, "message": "pause / resume asked for (within a second)"}
    if action == "pet":
        from .petdance import PET_REQUEST

        games = max(0, int(payload.get("games") or 0))
        PET_REQUEST.parent.mkdir(exist_ok=True)
        PET_REQUEST.write_text(str(games), encoding="utf-8")
        n = f"{games} game(s)" if games else "games until the pet is out of energy"
        return {"ok": True, "message": f"at its next free moment the bot goes to the Pet Pavilion for {n}"}
    if action == "farm":
        from .farm import Farm

        farm = Farm.load()
        farm.active = bool(payload.get("on", not farm.active))
        farm.save()
        return {"ok": True, "message": f"farming {farm.name}: {'on' if farm.active else 'off'}"}
    if action == "pin":
        from .quest import save_pin

        name = str(payload.get("quest") or "").strip()
        save_pin(name)
        note = " (takes effect at the bot's next start)" if service.running_pid() else ""
        return {"ok": True, "message": (f"pinned {name!r}" if name else "unpinned") + note}
    return {"ok": False, "message": f"unknown action {action!r}"}
