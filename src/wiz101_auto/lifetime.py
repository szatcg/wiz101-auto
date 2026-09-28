"""Totals across every session (state/lifetime.json), e.g. deaths.

The first time, deaths are counted from the log's "wizard defeated" lines
(every death since the bot's first run is in wiz101-auto.log until it
rotates); after that each death adds one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .questlist import world_of_zone

LIFETIME = Path("state") / "lifetime.json"
LOG = Path("wiz101-auto.log")


_ZONE = re.compile(r"\[([A-Za-z]+/[^\]\s]*)\]")


def logged_deaths(log: Path = LOG) -> tuple[int, dict[str, int]]:
    """Deaths in the log, and per world: the world of the last zone logged
    before each "wizard defeated" line."""
    total, by_world, world = 0, {}, ""
    try:
        with log.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "wizard defeated" in line:
                    total += 1
                    key = world or "Unknown"
                    by_world[key] = by_world.get(key, 0) + 1
                    continue
                for m in _ZONE.finditer(line):
                    world = world_of_zone(m.group(1)) or world
    except OSError:
        pass
    return total, by_world


def count_logged_deaths(log: Path = LOG) -> int:
    return logged_deaths(log)[0]


def load(path: Path = LIFETIME, log: Path = LOG) -> dict:
    """The totals, seeded from the log when there is no file yet."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if "deaths" not in data or "deaths_by_world" not in data:
        total, by_world = logged_deaths(log)
        data.setdefault("deaths", total)
        data["deaths_by_world"] = by_world
        save(data, path)
    return data


def save(data: dict, path: Path = LIFETIME):
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def add_death(world: str = "", path: Path = LIFETIME, log: Path = LOG) -> int:
    data = load(path, log)
    data["deaths"] = int(data.get("deaths", 0)) + 1
    key = world or "Unknown"
    data["deaths_by_world"][key] = int(data["deaths_by_world"].get(key, 0)) + 1
    save(data, path)
    return data["deaths"]
