"""A detour through side worlds before the main story goes on (the player,
2026-10-03: Grizzleheim, then Wintertusk, before Celestia).

state/detour.json lists the worlds in order:

    {"worlds": [
      {"world": "Grizzleheim", "finish": "Blood Brother"},
      {"world": "Wintertusk", "finish": "Winter News",
       "start": {"quest": "Cold News", "npc": "Merle Ambrose",
                 "zone": "WizardCity/Interiors/WC_Headmistress_House"}}
    ]}

The first world whose `finish` quest isn't in docs/CompletedQuests.txt is the
detour's world: its story counts as the main story (a side world's normally
doesn't) and the arcs' main story waits. With none of its quests in the book
yet, its `start` NPC is visited for the first quest. Once every listed world
is finished the detour is over and the main story goes on.
"""

from __future__ import annotations

import json
from pathlib import Path

DETOUR_FILE = Path("state") / "detour.json"


def load(path: Path = DETOUR_FILE) -> list[dict]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("worlds") or [])
    except (OSError, ValueError, AttributeError):
        return []


def _norm(name: str) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def active(worlds: list[dict], completed: set[str]) -> dict | None:
    """The detour world now: the first whose finish quest isn't done."""
    done = {_norm(n) for n in completed}
    for i, w in enumerate(worlds):
        if not w.get("world") or _norm(w.get("finish", "")) in done:
            continue
        # A later world already started (its start quest done): this one is
        # over even if its finish quest was never logged ('Blood Brother'
        # wasn't, and Wintertusk's quests were set aside as side quests).
        later = worlds[i + 1:]
        if any(_norm((x.get("start") or {}).get("quest", "")) in done for x in later):
            continue
        return w
    return None


def same_world(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _norm(a) == _norm(b)


def needs_start(entry: dict, started: bool, completed: set[str]) -> dict | None:
    """The NPC to visit for the world's first quest: none of its quests in the
    book yet (`started` False) and the start quest not done. {"npc", "zone"}
    or None."""
    start = entry.get("start") or {}
    if started or not start.get("npc") or not start.get("zone"):
        return None
    if _norm(start.get("quest", "")) in {_norm(n) for n in completed}:
        return None
    return {"npc": start["npc"], "zone": start["zone"]}
