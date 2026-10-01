"""Each quest's steps, for the stream page's quest card.

The game shows only the current goal of a quest, so the steps are pieced
together:

- done: every objective the bot has seen for the quest, in order
  (state/quest_steps.json, kept as the objective changes; a count going up,
  "Burn Scout Tower (1 of 3)" -> "(2 of 3)", updates the same step);
- now: the current objective;
- next: the player's quest list's goal lines for the quest
  (docs/sidequests/<World>.txt) past the ones done, else the world quest
  list's step types (docs/quests/<World>.txt: "D&C", "TALK") in words.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

STEPS_FILE = Path("state") / "quest_steps.json"
MAX_QUESTS = 60
MAX_STEPS = 30

_COUNT = re.compile(r"\s*\(\d+\s+of\s+\d+\)\s*$")
TAG_WORDS = {
    "TALK": "Talk to someone", "MOB": "Defeat enemies", "BOSS": "Defeat a boss",
    "D&C": "Defeat and collect", "INTERACT": "Use something", "EXPLORE": "Go somewhere",
    "COLLECT": "Collect items", "DUNGEON": "A dungeon", "INSTANCE": "A dungeon",
    "SIDE": "Side quests", "PET": "A pet task", "SPELL": "Learn a spell",
}


def base(objective: str) -> str:
    """The objective without its count ("... (2 of 3)")."""
    return _COUNT.sub("", objective or "").strip()


def _load(path: Path = STEPS_FILE) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(quest: str, objective: str, path: Path = STEPS_FILE) -> None:
    """Note `objective` as the latest step of `quest` (a count update replaces
    the step it counts)."""
    if not quest or not objective:
        return
    data = _load(path)
    steps = data.pop(quest, [])
    if steps and base(steps[-1]) == base(objective):
        steps[-1] = objective
    elif objective not in steps:
        steps.append(objective)
    data[quest] = steps[-MAX_STEPS:]  # (re-inserted: most recent quests last)
    for old in list(data)[:-MAX_QUESTS]:
        data.pop(old, None)
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def _tag_words(tag: str) -> str:
    m = re.match(r"^\s*(\d+)\s*[x×]\s*(.+)$", tag)
    count, kind = (m.group(1), m.group(2)) if m else ("", tag)
    words = TAG_WORDS.get(kind.strip().upper(), kind.strip().title())
    return f"{words} ({count})" if count else words


def view(quest: str, objective: str, world: str = "", path: Path = STEPS_FILE) -> dict:
    """{"quest", "done": [...], "now": objective, "next": [...], "source"}."""
    steps = _load(path).get(quest, [])
    now_base = base(objective)
    done = [s for s in steps if base(s) != now_base]
    nxt: list[str] = []
    source = ""
    from .givers import load_guide, same_quest

    guide = load_guide(world) if world else None
    g = next((q for q in guide or [] if same_quest(q.name, quest)), None)
    if g and g.goals:
        nxt, source = g.goals[len(done) + 1:], "quest list"
    else:
        from .questlist import load_world_lists, norm

        listed = load_world_lists().get(world, []) if world else []
        lq = next((q for q in listed if norm(q.name) == norm(quest)), None)
        if lq and lq.tags:
            tags = [t for t in lq.tags if not t.upper().startswith("SIDE")]
            nxt, source = [_tag_words(t) for t in tags[len(done) + 1:]], "world list"
    return {"quest": quest, "done": done, "now": objective, "next": nxt, "source": source}


_FOLLOW = re.compile(
    r"quest priority: (?:tracking|continuing) [\"'](.+?)[\"']|quest line goes on: [\"'](.+?)[\"']"
)
_NOW = re.compile(r"objective done -> now: [\"'](.+?)[\"']\s*$")


def seed_from_log(log: Path = Path("activity.log"), path: Path = STEPS_FILE) -> int:
    """Fill state/quest_steps.json from the activity log (quests followed and
    every objective change). Returns how many steps were noted."""
    quest, n = "", 0
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for line in lines:
        m = _FOLLOW.search(line)
        if m:
            quest = m.group(1) or m.group(2)
            continue
        m = _NOW.search(line)
        if m and quest:
            record(quest, m.group(1), path)
            n += 1
    return n
