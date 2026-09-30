"""Quests set aside after repeated defeats.

Losing the same fight twice usually means the wizard isn't strong enough yet
(e.g. a dungeon boss a level or two early). The quest is put aside until the
wizard levels up or some time has passed, and another questline is followed
meanwhile. Pure bookkeeping (plus a JSON file) so it can be unit tested.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFEATS_TO_DEFER = 2  # side quests
MAIN_DEFEATS_TO_DEFER = 5  # main-story objectives: fights have variance, keep trying
DEFER_SECONDS = 3600.0  # come back after this long even without a level-up
# Never done, whatever the state files say: the Ironworks dungeon (Marleybone)
# never ends for the bot and gives no XP; Prospector Zeke's hidden cats can't
# be found (nor interacted with).
ALWAYS_SKIP = frozenset({"Gate Crashers", "No Entry", "Stray Cat Strut"})


@dataclass
class Setbacks:
    path: Path = Path("state") / "setbacks.json"
    defeats: dict[str, int] = field(default_factory=dict)  # objective -> defeats
    deferred: dict[str, dict] = field(default_factory=dict)  # quest -> {"level", "at", "objective"}
    skipped: set[str] = field(default_factory=set)  # quests the bot can't do (no Quest Helper, PvP)

    @classmethod
    def load(cls, path: Path | None = None) -> Setbacks:
        s = cls(path or cls.path)
        try:
            raw = json.loads(s.path.read_text(encoding="utf-8"))
            s.defeats = dict(raw.get("defeats", {}))
            s.deferred = dict(raw.get("deferred", {}))
            s.skipped = set(raw.get("skipped", []))
        except Exception:
            pass
        return s

    def save(self):
        try:
            self.path.parent.mkdir(exist_ok=True)
            data = {"defeats": self.defeats, "deferred": self.deferred, "skipped": sorted(self.skipped)}
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def record_defeat(
        self, objective: str, quest: str | None, level: int, now: float | None = None, main: bool = False
    ) -> bool:
        """Count a defeat on `objective`. True when its quest is now set aside."""
        n = self.defeats.get(objective, 0) + 1
        self.defeats[objective] = n
        if n < (MAIN_DEFEATS_TO_DEFER if main else DEFEATS_TO_DEFER) or not quest:
            return False
        self.defeats.pop(objective, None)  # a fresh count when we come back
        now = time.time() if now is None else now
        self.deferred[quest] = {"level": level, "at": now, "objective": objective, "main": main}
        return True

    def set_quest_aside(
        self, quest: str, objective: str, level: int, now: float | None = None, main: bool = False,
        retry_after: float | None = None,
    ):
        """Put `quest` aside now (e.g. no way to progress it), like two defeats do.
        A main-story quest comes back only with a level-up (side quests fill in
        until then); others also after DEFER_SECONDS. `retry_after`: stuck
        rather than beaten, so try again after that many seconds anyway."""
        now = time.time() if now is None else now
        self.deferred[quest] = {"level": level, "at": now, "objective": objective, "main": main}
        if retry_after is not None:
            self.deferred[quest]["until"] = now + retry_after

    def set_aside(self, level: int, now: float | None = None) -> set[str]:
        """Quests still set aside; those whose time is up (a level gained, or
        DEFER_SECONDS passed) are released."""
        now = time.time() if now is None else now
        done = [
            q for q, d in self.deferred.items()
            if level > d.get("level", 0)
            or (not d.get("main") and now - d.get("at", 0) > DEFER_SECONDS)
            or ("until" in d and now > d["until"])
        ]
        for q in done:
            del self.deferred[q]
        return set(self.deferred) | self.skipped | ALWAYS_SKIP
