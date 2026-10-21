"""Streamportals (Empyrea's world teleporters): which stop of the list to take.

The list names regions ("Aeriel", "Mandalla", "Sepidious"), not zones, so the
bot learns where each stop lands (state/stream_stops.json) and chooses by that:
the stop that lands in the destination, else one whose landing zone has a known
way on to it, else one of the same area (EM_Z05_*), else a name that fits. The
stop we stand at is never chosen (it goes nowhere: Mandalla from Mandalla, over
and over).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

STOPS_FILE = Path("state") / "stream_stops.json"  # {world: {stop name: zone it lands in}}


def norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def area(zone: str) -> str:
    """'Empyrea/Interiors/EM_Z05_InkSack' -> 'EM_Z05' ('' if none)."""
    m = re.match(r"([A-Za-z]+_Z\d+)_", zone.split("/")[-1])
    return m.group(1) if m else ""


def load_stops(world: str, path: Path | None = None) -> dict[str, str]:
    try:
        data = json.loads((path or STOPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data.get(world, {}))


def save_stop(world: str, stop: str, zone: str, path: Path | None = None) -> None:
    path = path or STOPS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault(world, {})[stop] = zone
    try:
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def name_fits(option: str, hint: str) -> bool:
    """A list name and a hint for the same place ('Outer Athanor' and
    'outerathanor', 'Athanor' for 'outerathanor')."""
    a, b = norm(option), norm(hint)
    if not a or not b:
        return False
    return b in a or (len(a) >= 5 and a in b)


def choose_stop(
    options: list[str],
    dest: str,
    here: str,
    learned: dict[str, str],
    reaches: Callable[[str, str], bool],
    hints: list[str],
) -> str | None:
    """The list entry to take toward `dest` from `here` (see the module doc)."""
    here_stops = {s for s, z in learned.items() if z == here or (area(z) and area(z) == area(here)
                                                                   and "/interiors/" not in here.lower())}
    pool = [o for o in options if o and o not in here_stops]
    by_zone = [(o, learned[o]) for o in pool if o in learned]
    for o, z in by_zone:
        if z == dest:
            return o
    # A stop named for the place, not learned yet, before a learned one whose
    # doors merely reach it (toward the Candy Corn Farm: Sweetzburg, the way
    # through the Pipeline Cave, over the list's own 'Candy Corn Farm').
    for hint in hints:
        for o in pool:
            if o not in learned and norm(o) and norm(o) == norm(hint):
                return o
    for o, z in by_zone:
        if reaches(z, dest):
            return o
    for o, z in by_zone:
        if area(z) and area(z) == area(dest):
            return o
    for hint in hints:
        for o in pool:
            if o not in learned and name_fits(o, hint):
                return o
    for hint in hints:
        for o in pool:
            if name_fits(o, hint):
                return o
    # A stop not learned yet (Port Aero, added mid-story): taken to learn where
    # it lands rather than none (the bot looped at Aeriel's portal toward the
    # Southwest Aero Plains, which Port Aero's gate reaches).
    return next((o for o in pool if o not in learned), None)
