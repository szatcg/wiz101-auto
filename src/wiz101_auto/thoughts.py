"""The bot's recent reasoning, from activity.log, for the stream page.

Each log line becomes an event with a kind (cast, discard, pass, plan,
travel, heal, loot, mark, success, warning...), and the latest round line
gives the battle as it stands (round, pips, our health, enemies with their
health, the move chosen and why). Parsing is pure so it's unit tested.
"""

from __future__ import annotations

import re
from pathlib import Path

ACTIVITY = Path("activity.log")

_LINE = re.compile(r"^(\d\d:\d\d:\d\d) \| (\w+)\s*\| (.*)$")
_ROUND = re.compile(
    r"^\[round (\d+)\] pips=(\d+)\+(\d+)P hp=(\d+)/(\d+) vs (.*?) -> (\w+) ?(.*)$"
)
_ENEMY = re.compile(r"^(.*?)(\*?) (\d+)/(\d+)( dead)?$")
_ACTION = re.compile(r"^(.*?)(?: on (.*?))? \((.*)\)$")
_NOISE = ("resists", " effects:", "correcting clicks", "Pass went through", "saved state/",
          "message box button", "card windows:", "working on:")

# (kind, words in the message) in order: the first match wins.
_KINDS = (
    ("plan", ("plan (",)),
    ("flee", ("fleeing", "confirmed fleeing")),
    ("heal", ("wisp", "recover", "healed", "rest", "drinking", "going to the hub to heal")),
    ("hunt", ("going after", "looking around the zone", "found ")),
    ("mark", ("marked this spot", "marked the dungeon")),
    ("recall", ("recall",)),
    ("loot", ("picking up", "collecting", "collected", "new item", "equipped better gear")),
    ("talk", ("interacting", "NPC menu", "Talk To")),
    ("quest", ("quest priority", "objective done", "quest completed", "in the dungeon:")),
    ("travel", ("heading to", "teleport", "gate", "walking", "inching", "Go Home", "hub", "entered")),
    ("train", ("trained", "learned", "training", "Cyrus")),
)


def _kind(level: str, msg: str) -> str:
    for kind, words in _KINDS:
        if any(w in msg for w in words):
            return kind
    if level == "SUCCESS":
        return "success"
    if level in ("WARNING", "ERROR"):
        return "warning"
    return "info"


def parse_enemies(text: str) -> list[dict]:
    out = []
    for part in text.split(", "):
        m = _ENEMY.match(part.strip())
        if m:
            out.append({
                "name": m.group(1), "boss": bool(m.group(2)),
                "hp": int(m.group(3)), "max": int(m.group(4)), "dead": bool(m.group(5)),
            })
    return out


def parse_line(line: str) -> dict | None:
    """One activity.log line -> an event (None for noise)."""
    m = _LINE.match(line.rstrip("\n"))
    if not m:
        return None
    time_, level, msg = m.groups()
    if any(n in msg for n in _NOISE):
        return None
    event = {"time": time_, "level": level, "text": msg}
    r = _ROUND.match(msg)
    if r:
        verb, rest = r.group(7), r.group(8)
        a = _ACTION.match(rest)
        card, target, why = (a.group(1), a.group(2) or "", a.group(3)) if a else (rest, "", "")
        if verb == "pass":
            card, why = "", rest.strip("()")
        event.update(
            kind=verb if verb in ("cast", "discard", "pass", "enchant") else "cast",
            round=int(r.group(1)), pips=int(r.group(2)), power=int(r.group(3)),
            hp=int(r.group(4)), max_hp=int(r.group(5)), enemies=parse_enemies(r.group(6)),
            card=card, target=target, why=why,
        )
        return event
    event["kind"] = _kind(level, msg)
    if event["kind"] == "plan":
        event["text"] = msg.split("): ", 1)[-1] if "): " in msg else msg
    return event


def read_thoughts(path: Path = ACTIVITY, limit: int = 40, tail_bytes: int = 60_000) -> dict:
    """The latest events (newest last), and the battle as of the last round line."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            lines = f.read().decode("utf-8", errors="replace").splitlines()[1:]
    except OSError:
        lines = []
    events = []
    battle, plan = None, ""
    for line in lines:
        e = parse_line(line)
        if not e:
            continue
        if e["kind"] == "plan":
            plan = e["text"]
        if "round" in e:
            battle = {**e, "plan": plan}
        if "combat over" in e["text"] or "session over" in e["text"]:
            battle = None
        if events and events[-1]["text"] == e["text"]:
            continue  # the same step repeated: show it once
        events.append(e)
    return {"events": events[-limit:], "battle": battle}
