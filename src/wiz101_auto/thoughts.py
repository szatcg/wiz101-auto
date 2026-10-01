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
_PREDICT = re.compile(r"\| predict: (.*)$")
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


def place(zone: str) -> str:
    """"Krokotopia/KT_Pyramid/KT_Chamber" -> "Chamber" (the last part, readable)."""
    last = zone.strip().split("/")[-1]
    last = re.sub(r"^[A-Z]{2}_", "", last).replace("_", " ")
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", last)


def _places(text: str) -> str:
    return re.sub(r"\b[A-Z][A-Za-z]+(?:/[A-Za-z0-9_]+)+", lambda m: place(m.group(0)), text)


def _steps(text: str) -> str:
    return text.replace(" > ", " → ")


def humanize(e: dict) -> tuple[str, str]:
    """(tag, plain words) for what the bot does and why."""
    kind, why = e.get("kind", ""), e.get("why", "")
    card, target = e.get("card", ""), e.get("target", "")
    if "round" in e:
        on = f" on {target}" if target and target != "Marcello" else ""
        if kind == "pass":
            if why.startswith("waiting: kills"):
                plan = why.split(": ", 2)[-1]
                return "WAIT", f"Waiting for pips — plan: {_steps(plan)}"
            m = re.match(r"saving pips (?:to cast|for) (.+?)(?: next round| \(|$)", why)
            if m:
                return "WAIT", f"Saving pips for {m.group(1)}"
            m = re.match(r"holding (.+?) until (.+?) is trapped", why)
            if m:
                return "WAIT", f"Holding {m.group(1)} until {m.group(2)} is trapped"
            if "bigger hit" in why:
                return "WAIT", "Holding the blade and traps for a bigger hit"
            return "WAIT", "Waiting for more pips"
        if kind == "discard":
            if "off-school" in why:
                return "TOSS", f"Tossing {card}: off-school card, drawing better ones"
            if "spare 0-pip" in why:
                return "TOSS", f"Tossing a spare {card} (keeping one)"
            if "shield for schools" in why:
                return "TOSS", f"Tossing {card}: nobody here hits with its schools"
            if why.startswith("prism"):
                return "TOSS", f"Tossing {card}: no enemy here is weak to it"
            if "hand full" in why:
                return "TOSS", f"Tossing {card}: hand is full"
            m = re.search(r"digging for (?:a |an )?(.+)", why)
            if m:  # (what the brain is after: "a blade / trap", "a hit-all spell")
                wants = m.group(1).replace(" / ", " or ").rstrip(")")
                return "TOSS", f"Tossing {card}: drawing for a {wants}"
            if why.startswith("rollouts"):
                return "TOSS", f"Tossing {card}: the simulations say a fresh draw beats it"
            return "TOSS", f"Tossing {card} for a fresh draw"
        if kind == "enchant":
            return "BOOST", f"Enchanting a spell with {card}"
        m = re.match(r"finish (.+?): ~(\d+)", why)
        if m:
            return "FINISH", f"Finishing off {m.group(1)} with {card} (~{m.group(2)} dmg)"
        m = re.match(r"kills (.+?) in (\d+) round\(s\)(?:: (.*))?", why)
        if m:
            if m.group(2) == "1":
                return "KILL", f"Killing {m.group(1)} with {card}"
            line = _steps(m.group(3) or card)
            return "KILL", f"Going for the kill on {m.group(1)}: {line} ({m.group(2)} rounds)"
        if "blade" in why:
            return "SETUP", f"Buffing up with {card} (stronger next hit)"
        if why.startswith("trap") or "free trap" in why:
            who = target or why.split("on ")[-1].split(" while")[0]
            return "SETUP", f"Setting a {card} on {who} to boost the next hit"
        if "shield" in why and "breaking" in why:
            return "BREAK", f"Breaking {target}'s shield with a free {card}"
        if why.startswith("shield"):
            m = re.search(r"against (.+?) \((\w+)\)", why)
            foe = f"{m.group(1)}'s {m.group(2)} attacks" if m else "incoming hits"
            return "SHIELD", f"Shielding against {foe}"
        if why.startswith("health") or "ally" in why and "low" in why:
            return "HEAL", f"Healing with {card} ({why.replace('health ', 'health ')})"
        if "summon" in why:
            return "SUMMON", f"Summoning {card} to fight alongside"
        if "harder than" in why:
            m = re.search(r"x([\d.]+)", why)
            return "PRISM", f"Prism{on}: our hits hit {m.group(1) if m else 'much'}× harder"
        if "instead of passing" in why:
            return "FREE", f"Free hit with {card}{on} instead of passing"
        m = re.match(r"~(\d+) dmg", why)
        if m:
            who = f" {target}" if target else ""
            return "ATTACK", f"Hitting{who} with {card} (~{m.group(1)} dmg)"
        return "CAST", f"Casting {card}{on}"
    text = _places(e.get("text", ""))
    if kind == "plan":
        plan = re.sub(r"; skipping the minion.*$", "", _steps(text)).replace(" | ", "  ·  ")
        return "PLAN", plan
    m = re.match(r"going after (.+?) for", text)
    if m:
        return "HUNT", f"Hunting {m.group(1)}"
    if text.startswith("marked this spot"):
        why = "to Recall back after a defeat" if "before the fight" in text else (
            "to Recall back after healing" if "healing" in text else "to Recall back to later")
        return "MARK", f"Leaving a mark here {why}"
    if text.startswith("recalled"):
        return "RECALL", "Recalled back to the mark"
    if text.startswith("picking up"):
        return "LOOT", text.replace("picking up", "Grabbing").split(" on the way")[0]
    if text.startswith("collecting"):
        return "COLLECT", text.replace("collecting", "Collecting")
    if "recovering before" in text:
        return "HEAL", "Low on health or mana: healing up first"
    if text.startswith("objective done"):
        return "DONE", "Objective done! Next: " + text.split("now: ", 1)[-1].strip("'\"")
    if text.startswith("quest completed"):
        return "DONE", "Quest complete: " + text.split(": ", 1)[-1].strip("'\"")
    if text.startswith("quest priority"):
        what = text.split(": ", 1)[-1].replace("tracking ", "").replace("continuing ", "")
        return "QUEST", "Working on " + what
    if text.startswith("heading to"):
        return "TRAVEL", "Heading to " + text.split(": ")[0].replace("heading to ", "")
    if "fleeing" in text:
        return "FLEE", "Fleeing a fight the quest doesn't need"
    if text.startswith("interacting"):
        return "TALK", "Talking / interacting"
    tag = {"warning": "HMM", "success": "YES", "heal": "HEAL", "travel": "TRAVEL", "loot": "LOOT",
           "talk": "TALK", "quest": "QUEST", "train": "TRAIN", "hunt": "HUNT", "recall": "RECALL",
           "mark": "MARK"}.get(kind, "")
    return tag, text[:1].upper() + text[1:]


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
        m = _PREDICT.search(line)
        if m:
            if battle is not None:
                battle["predict"] = {int(i): int(d) for i, d in re.findall(r"(\d+)=(\d+)", m.group(1))}
            continue
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
        e["tag"], e["say"] = humanize(e)
        if battle is not None and "round" in e:
            battle.update(tag=e["tag"], say=e["say"])
        events.append(e)
    return {"events": events[-limit:], "battle": battle}
