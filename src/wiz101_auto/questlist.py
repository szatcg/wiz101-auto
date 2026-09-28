"""The quest order to follow (docs/QuestList.txt) and the log of completed quests.

QuestList.txt lists the story quests area by area, in order:

    Unicorn Way (10 quests)
    1.
    Unicorn Way
    INSTANCE
    SIDE ×1
    ...

Quests the bot finishes are appended to docs/CompletedQuests.txt, one name per
line, in the order they were completed. Parsing and matching are pure so they
can be unit tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

QUEST_LIST = Path("docs") / "QuestList.txt"
COMPLETED_LOG = Path("docs") / "CompletedQuests.txt"

_SECTION = re.compile(r"^(?P<area>.+?)\s*\(\d+ quests?\)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(\d+)\.\s*$")


def norm(name: str) -> str:
    """Compare quest names loosely: case, punctuation and spacing don't matter."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class ListedQuest:
    index: int  # 1-based position in the list
    name: str
    area: str
    tags: list[str] = field(default_factory=list)


def parse_quest_list(text: str) -> list[ListedQuest]:
    quests: list[ListedQuest] = []
    area = ""
    expect_name = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SECTION.match(line)
        if m and not expect_name:
            area = m.group("area").strip()
            continue
        m = _NUMBER.match(line)
        if m:
            quests.append(ListedQuest(int(m.group(1)), "", area))
            expect_name = True
            continue
        if expect_name:
            quests[-1].name = line
            expect_name = False
        elif quests:
            quests[-1].tags.append(line)
    return [q for q in quests if q.name]


def load_quest_list(path: Path = QUEST_LIST) -> dict[str, ListedQuest]:
    """Listed quests by normalised name ({} if the file is missing)."""
    try:
        return {norm(q.name): q for q in parse_quest_list(path.read_text(encoding="utf-8"))}
    except OSError:
        return {}


class CompletionTracker:
    """Spots quests that left the quest book (= completed). A quest must be
    missing from two readings in a row, so one partial read can't log it."""

    def __init__(self, path: Path = COMPLETED_LOG):
        self.path = path
        self._seen: set[str] | None = None  # names in the last full reading
        self._missing_once: set[str] = set()

    def update(self, names: set[str]) -> list[str]:
        """Feed the names in the book now; returns quests completed since."""
        if self._seen is None:
            self._seen = set(names)
            return []
        missing = self._seen - names
        done = sorted(missing & self._missing_once)
        self._missing_once = missing - set(done)
        self._seen = (self._seen - set(done)) | names
        return done

    def log(self, names: list[str]):
        if not names:
            return
        try:
            self.path.parent.mkdir(exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for n in names:
                    f.write(n + "\n")
        except OSError:
            pass
