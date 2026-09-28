"""Totals across every session (state/lifetime.json), e.g. deaths.

The first time, deaths are counted from the log's "wizard defeated" lines
(every death since the bot's first run is in wiz101-auto.log until it
rotates); after that each death adds one.
"""

from __future__ import annotations

import json
from pathlib import Path

LIFETIME = Path("state") / "lifetime.json"
LOG = Path("wiz101-auto.log")


def count_logged_deaths(log: Path = LOG) -> int:
    try:
        with log.open(encoding="utf-8", errors="replace") as f:
            return sum("wizard defeated" in line for line in f)
    except OSError:
        return 0


def load(path: Path = LIFETIME, log: Path = LOG) -> dict:
    """The totals, seeded from the log when there is no file yet."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"deaths": count_logged_deaths(log)}
        save(data, path)
        return data


def save(data: dict, path: Path = LIFETIME):
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def add_death(path: Path = LIFETIME, log: Path = LOG) -> int:
    data = load(path, log)
    data["deaths"] = int(data.get("deaths", 0)) + 1
    save(data, path)
    return data["deaths"]
