"""Loops: the same thing done over and over without getting anywhere.

The player (2026-10-06): an hour of relogs in the Olde Town Bazaar went
unnoticed. Every INFO/WARNING line the bot logs is counted here, its numbers
stripped ("teleport to (2860, 4750) didn't happen" and "... (2816, 4856)
..." are one line); the same line LOOP_REPEATS times within LOOP_WINDOW is a
loop. The quest loop takes it (`take`) and breaks out of it.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

LOOP_REPEATS = 8
LOOP_WINDOW = 600.0  # seconds
# Lines that repeat by design: fight rounds, pet games, waiting on a team.
IGNORED = ("[round", "plan (", "predict:", "crit:", "effects:", "resists", "item card", "pet:",
           "team up: still waiting", "following the team", "combat over", "class pips",
           "correcting clicks", "dance game", "loop:", "holding here", "watching for a teammate",
           "picking up", "learned the walk", "drinking",
           # the step's own status lines (every step on a long objective)
           "working on:", "quest priority", "the game is tracking", "read ", "to the objective at",
           "heading for the boss", "the quest moved on",
           # teleport retries and talks: routine noise in some zones
           "teleport to (", "teleport refused", "teleport was rejected", "interacting:")


def _status_line(message: str) -> bool:
    return message.startswith("[")  # "[Zone] Objective": each step's heading


_seen: dict[str, deque] = defaultdict(deque)
_found: list[str] = []


def key(message: str) -> str:
    """The line with its numbers taken out."""
    return re.sub(r"-?\d+(\.\d+)?", "#", message.strip())[:160]


def note(message: str, now: float | None = None) -> str | None:
    """Count a logged line; the line when it has just become a loop."""
    if any(w in message for w in IGNORED) or _status_line(message):
        return None
    now = time.monotonic() if now is None else now
    k = key(message)
    times = _seen[k]
    times.append(now)
    while times and now - times[0] > LOOP_WINDOW:
        times.popleft()
    if len(times) >= LOOP_REPEATS:
        times.clear()
        return k
    return None


def sink(message) -> None:
    """A loguru sink (INFO and up)."""
    record = message.record
    if record["level"].no < 20:
        return
    found = note(record["message"])
    if found:
        _found.append(found)


def take() -> str | None:
    """A loop seen since the last look (the line), else None."""
    return _found.pop(0) if _found else None


def reset() -> None:
    _seen.clear()
    _found.clear()
