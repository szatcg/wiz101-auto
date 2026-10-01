"""Side quests done in the area the wizard is in (the stream page's bar).

Which side quests an area has is pieced together:

- the player's quest list (docs/sidequests/<World>.txt): its side quests
  under the area's heading;
- every quest seen in the quest book with its area (state/quest_areas.json,
  kept by the dashboard at each reading, as completed quests leave the book);
- the world list's "SIDE xN" tags (docs/quests/<World>.txt): how many side
  quests the area's story quests open, a floor for the total.

Done: the quest is in docs/CompletedQuests.txt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

AREAS_FILE = Path("state") / "quest_areas.json"
_SIDE_TAG = re.compile(r"^\s*SIDE\s*\D?\s*(\d+)", re.I)


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def remember_book(book: dict, path: Path = AREAS_FILE) -> dict[str, dict]:
    """Merge the quest book's quests (name -> area, main) into the area log."""
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    changed = False
    for q in book.get("quests", []):
        name, area = q.get("name", ""), q.get("area", "")
        if not name or not area:
            continue
        row = {"area": area, "main": bool(q.get("main"))}
        if known.get(name) != row:
            known[name], changed = row, True
    if changed:
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(known, indent=1), encoding="utf-8")
        except OSError:
            pass
    return known


def side_tag_count(tags: list[str]) -> int:
    return sum(int(m.group(1)) for t in tags if (m := _SIDE_TAG.match(t)))


def area_progress(area: str, guide, known: dict[str, dict], listed, completed: list[str]) -> dict:
    """{"area", "done", "total", "pct"} for the side quests of `area`.

    guide: GuideQuest list (or None); known: quest_areas.json rows; listed:
    the world list's ListedQuest rows; completed: completed quest names."""
    want = _key(area)
    names: dict[str, str] = {}
    for q in guide or []:
        if not q.main and _key(q.area) == want:
            names.setdefault(_key(q.name), q.name)
    for name, row in known.items():
        if not row.get("main") and _key(row.get("area", "")) == want:
            names.setdefault(_key(name), name)
    # Story quests in the world list aren't side quests (the book can call
    # one "side" before it counts as the story).
    story = {_key(q.name) for q in listed or []}
    names = {k: n for k, n in names.items() if k not in story}
    done_keys = {_key(n) for n in completed}
    done = sum(k in done_keys for k in names)
    floor = sum(side_tag_count(q.tags) for q in listed or [] if _key(q.area) == want)
    total = max(len(names), floor)
    return {"area": area, "done": done, "total": total, "pct": round(100 * done / total) if total else 0}


def area_of_zone(zone: str, display_zones: list[tuple[str, str]]) -> str:
    """The place name of a zone id ("MooShu/MS_War/MS_War_BattlefieldA" ->
    "Crimson Fields"); "" for zones without one (interiors, dungeons)."""
    name = next((n for n, z in display_zones if z == zone), "")
    return name.title().replace("'S", "'s") if name else ""
