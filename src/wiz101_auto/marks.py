"""The game's single Mark / Recall teleport, used for quicker travel.

Two kinds of mark share it:
  - "dungeon": set on a dungeon's sigil; after a defeat, Recall saves walking
    back across the world to try the boss again.
  - "travel": set where an objective was worked on before heading several
    zones away (quests often send you back); when a later objective lies
    nearer the mark than the walk from here, Recall beats walking.

Decisions here are pure (zone hop counts in, yes/no out) so they're unit tested.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

MARK_FILE = Path("state") / "mark.json"
# Marks to Recall to after a defeat: a dungeon's sigil, or where a fight
# objective's zone was reached ("Defeat Sand Stalkers in Grand Arena").
RETURN_KINDS = ("dungeon", "fight")
MARK_MIN_HOPS = 2  # mark before a trip at least this many zones long
RECALL_HOPS = 1  # a Recall (animation + loading) costs about one zone change

Hops = Callable[[str, str], "int | None"]  # zone hops between two zones (None: unknown)


@dataclass
class Mark:
    zone: str
    objective: str  # what we were working on when we marked
    kind: str = "dungeon"  # "dungeon" or "travel"


def load_mark(path: Path = MARK_FILE) -> Mark | None:
    """The mark from an earlier run (the game keeps the mark itself)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):  # older files: [zone, objective], always a dungeon
            return Mark(raw[0] or "", raw[1])
        return Mark(raw["zone"], raw.get("objective", ""), raw.get("kind", "dungeon"))
    except Exception:
        return None


def save_mark(mark: Mark | None, path: Path = MARK_FILE):
    try:
        if mark is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(asdict(mark)), encoding="utf-8")
    except OSError as exc:
        logger.debug(f"could not save the mark: {exc}")


def should_travel_mark(
    here: str, dest: str | None, hops: Hops, current: Mark | None, keep_dungeon: bool, dungeons: set[str]
) -> bool:
    """Mark here before a long trip to `dest`? Not inside a dungeon (instances
    reset: a Recall there is refused), not over a dungeon mark still needed
    (`keep_dungeon`), and not when the mark already is in this zone."""
    if not here or not dest or dest == here or here in dungeons:
        return False
    if current and current.zone == here:
        return False
    if current and current.kind in RETURN_KINDS and keep_dungeon:
        return False
    n = hops(here, dest)
    return n is None or n >= MARK_MIN_HOPS


def recall_is_faster(
    here: str, dest: str | None, mark: Mark | None, hops: Hops, objective: str | None = None
) -> bool:
    """Recall to the mark, then walk on, beats walking from here to `dest`?
    With no known walking route from here (e.g. inside a building), only when
    the mark is in the destination zone itself. A mark placed while on this
    very objective ("Talk To Zan'ne", marked in her building) is the place."""
    if not mark or not mark.zone or mark.zone == here:
        return False
    from .travel_data import is_world_hub

    if is_world_hub(mark.zone):
        return False  # the hub button goes there without spending the mark
    other_world = bool(dest) and dest.split("/")[0] != mark.zone.split("/")[0]
    if objective and mark.objective == objective and not other_world and (
        not dest or hops(mark.zone, dest) in (0, None)
    ):
        # (Not a mark in Wizard City for a Marleybone objective, nor a heal
        # trip's mark in the Necropolis when the objective had just moved on
        # to the Academy, nearer on foot.)
        return True
    if not dest or dest == here:
        return False
    via = hops(mark.zone, dest)
    if via is None:
        return False
    walk = hops(here, dest)
    if walk is None:
        return via == 0
    return via + RECALL_HOPS < walk
