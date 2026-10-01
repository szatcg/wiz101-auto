"""The combat simulator's visualizer (the dashboard's /sim page).

Every deck search (combat/deckopt.py, started by the bot after a loss or from
this page) writes a report to state/sim_runs/: the decks it tried, the
fastest winners it iterated on, the deck chosen, what that deck did over a
batch of fights, and a few recorded fights step by step. The page lists the
reports, shows their stats and replays the fights on a battle board.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

RUNS_DIR = Path("state") / "sim_runs"
STATS_FILE = Path("state") / "enemy_stats.json"
PAGE = Path(__file__).with_name("sim.html")
_SAFE = re.compile(r"^[\w.-]+\.json$")


def runs(limit: int = 60) -> list[dict]:
    """The latest reports, newest first: file, what, stage, chosen deck."""
    out = []
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        chosen = d.get("chosen") or {}
        out.append({
            "file": f.name, "what": d.get("what", ""), "stage": d.get("stage", ""),
            "started": d.get("started"), "finished": d.get("finished"),
            "decks_tried": d.get("decks_tried", 0),
            "win": chosen.get("win"), "rounds": chosen.get("rounds"),
        })
    return out


def run(name: str) -> dict | None:
    """One report by file name (no paths)."""
    if not _SAFE.match(name or ""):
        return None
    f = RUNS_DIR / name
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def enemies() -> list[dict]:
    """Enemies the logs know (for picking a scenario), bosses first."""
    try:
        known = json.loads(STATS_FILE.read_text(encoding="utf-8")).get("enemies", {})
    except (OSError, ValueError):
        return []
    rows = [{"name": n, "boss": bool(e.get("boss")), "hp": e.get("max_health"), "school": e.get("school")}
            for n, e in known.items()]
    return sorted(rows, key=lambda r: (not r["boss"], r["name"]))


_started: list[subprocess.Popen] = []


def start(vs: str, minutes: float) -> dict:
    """Start a deck search against `vs` (comma-separated enemy names) in the
    background; its report appears in the list."""
    names = [n.strip() for n in (vs or "").split(",") if n.strip()]
    if not names:
        return {"ok": False, "error": "no enemies given"}
    if any(p.poll() is None for p in _started):
        return {"ok": False, "error": "a search started here is still running"}
    minutes = max(0.5, min(30.0, float(minutes or 5)))
    _started.append(subprocess.Popen(
        [sys.executable, "-m", "wiz101_auto.combat.deckopt",
         "--vs", ",".join(names), "--minutes", str(minutes)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
    ))
    return {"ok": True, "vs": names, "minutes": minutes, "at": time.time()}
