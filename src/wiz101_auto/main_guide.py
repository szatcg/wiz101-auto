"""The player's main-quest guides (docs/guides/<World>.txt): every story quest
in order with its objectives, e.g.

    45. Take the Low Road - Talk to Dulin Helmsplitter + Defeat ... + Talk to Dulin Helmsplitter
    46. Hammer Don't Hurt 'Em - Talk to Dulin Helmsplitter + Use Dulin's Hammer + ...

Some story quests stay open over several later ones ("umbrella" quests):
Wintertusk's 'Bones of the Earth' (#44) is handed in only after #45-#50
('Use Golden Seals in Nastrond' at the very end), so following it alone the
bot stood at the seals for minutes, set it aside and ground for experience.
From the guide: the next story quest to pick up and who gives it, and which
quest in the book to follow first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GUIDE_DIR = Path("docs") / "guides"

_LINE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s+-\s+(.+)$")
_GIVEN = re.compile(r"\(given by ([^)]+)\)", re.I)
_TALK = re.compile(r"talk to ([^+;]+)", re.I)
_PLACE = re.compile(r"\s+(in|at|on)\s+.*$", re.I)


@dataclass
class GuideQuest:
    index: int
    name: str
    steps: str
    given_by: str = ""  # "(given by X)" in the guide

    def talks(self) -> list[str]:
        return [_who(m) for m in _TALK.findall(self.steps)]


def _who(text: str) -> str:
    """'Dulin Helmsplitter in Nastrond' -> 'Dulin Helmsplitter'."""
    text = _PLACE.sub("", text.strip())
    return re.sub(r"^(headmaster|headmistress)\s+", "", text, flags=re.I).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("…", "..."))


def parse(text: str) -> list[GuideQuest]:
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        index, name, steps = int(m.group(1)), m.group(2).strip(), m.group(3)
        given = _GIVEN.search(name)
        name = _GIVEN.sub("", name).strip()
        out.append(GuideQuest(index, name, steps, _who(given.group(1)) if given else ""))
    return out


def load(world: str, guide_dir: Path = GUIDE_DIR) -> list[GuideQuest]:
    try:
        return parse((guide_dir / f"{world}.txt").read_text(encoding="utf-8"))
    except OSError:
        return []


def giver(guide: list[GuideQuest], quest: GuideQuest) -> str:
    """Who hands out `quest`: the guide's "(given by X)", else the last person
    the quest before it sends us to (they hand in one and give the next),
    else the first person the quest itself names."""
    if quest.given_by:
        return quest.given_by
    before = [q for q in guide if q.index < quest.index]
    prev = max(before, key=lambda q: q.index) if before else None
    if prev is not None and prev.talks():
        return prev.talks()[-1]
    return quest.talks()[0] if quest.talks() else ""


def _find(guide: list[GuideQuest], name: str) -> GuideQuest | None:
    n = _norm(name)
    return next((q for q in guide if _norm(q.name) == n), None)


def next_to_pick_up(guide: list[GuideQuest], done: set[str], book: set[str],
                    alone: bool = False) -> GuideQuest | None:
    """The guide quest to fetch now: the one after the last one done, when it
    isn't in the book yet and an earlier story quest is (that one waits on
    it), or (`alone`: the main story's world) when no quest of the guide is
    in the book at all. None otherwise."""
    done_n = {_norm(n) for n in done}
    book_n = {_norm(n) for n in book}
    finished = [q.index for q in guide if _norm(q.name) in done_n]
    if not finished:
        return None
    after = [q for q in guide if q.index > max(finished)]
    if not after:
        return None
    nxt = after[0]
    if _norm(nxt.name) in book_n or _norm(nxt.name) in done_n:
        return None
    waiting = [q for q in guide
               if q.index < nxt.index and _norm(q.name) in book_n and _norm(q.name) not in done_n]
    if alone and not any(_norm(q.name) in book_n for q in guide):
        return nxt
    return nxt if waiting else None


def who_to_ask(guide: list[GuideQuest], quest: GuideQuest) -> list[str]:
    """The NPCs to ask for `quest`, most likely first: its giver, then the
    people the quest itself names (Thornton Lewis had nothing after Turning
    Tiles; 'Archivist, Revisited' is only 'Talk to The Archivist')."""
    return [n for n in dict.fromkeys([giver(guide, quest), *quest.talks()]) if n]


def later_in_book(guide: list[GuideQuest], quest: str, book: list[str]) -> str | None:
    """`quest` is an earlier story quest and a later one of the same guide is
    in the book: that later one first (the earlier one is handed in after
    it). The latest such quest's name as the book spells it, else None."""
    mine = _find(guide, quest)
    if mine is None:
        return None
    later = [(g.index, b) for b in book if (g := _find(guide, b)) is not None and g.index > mine.index]
    return max(later)[1] if later else None


_ALL: list[tuple[str, list[GuideQuest]]] | None = None


def all_guides(guide_dir: Path = GUIDE_DIR) -> list[tuple[str, list[GuideQuest]]]:
    """Every world's guide (read once), with its world's name."""
    global _ALL
    if _ALL is None:
        _ALL = [(p.stem, g) for p in sorted(guide_dir.glob("*.txt")) if (g := load(p.stem, guide_dir))]
    return _ALL
