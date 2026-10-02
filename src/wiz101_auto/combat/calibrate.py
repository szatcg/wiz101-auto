"""What the logged fights say about the combat model (activity.log).

Each round line holds our health, every enemy's health, our action and (next
line) the damage we predicted per enemy. Two things are measured from
consecutive rounds of one fight:

- our hits: the health the target really lost vs. the prediction (fizzles show
  as nothing lost), per spell;
- the enemies' hits: the health we lost in a round (plus what our heal gave),
  per enemy, from rounds where that enemy was the only one standing.

Rounds with several enemies are shared out between them by max health (a
boss hits harder than its guards; enemies that never fought alone still get
numbers). `write_stats` saves it all to
state/enemy_stats.json for the simulator (sim.py): each enemy's damage per
round as observed, our hit rate per spell.

    python -m wiz101_auto.combat.calibrate [activity.log ...]   # report + stats file
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROUND = re.compile(
    r"\[round (?P<n>\d+)\] pips=(?P<p>\d+)\+(?P<pp>\d+)P hp=(?P<hp>\d+)/(?P<max>\d+) "
    r"(?:minion=(?P<mhp>\d+)/(?P<mmax>\d+) )?vs (?P<foes>.*?) -> (?P<act>.*)$"
)
FOE = re.compile(r"(?P<name>.+?)(?P<boss>\*)? (?P<hp>\d+)/(?P<max>\d+)(?P<dead> dead)?$")
PREDICT = re.compile(r"predict: (?P<m>.*)$")
CAST = re.compile(r"cast (?P<spell>.+?)(?: on (?P<target>.+?))? \(")
HEAL_SPELLS = {"Pixie": 420, "Sprite": 200, "Fairy": 400}
STATS_FILE = Path("state") / "enemy_stats.json"
RECENT_FIGHTS = 300  # fights kept in the stats (with their world and enemies)


@dataclass
class Round:
    n: int
    hp: int
    max_hp: int
    foes: list[tuple[str, int, int]]  # name, health, max
    action: str
    predict: dict[int, int] = field(default_factory=dict)
    bosses: set[str] = field(default_factory=set)
    world: str = ""  # the world of the last zone logged before it ("Marleybone")
    minion_hp: int | None = None  # our minion's health, when one is out


RESISTS = re.compile(
    r"\| INFO\s+\| (?P<name>[^|]+?) \((?P<school>[a-z]*)\): resists (?P<r>\{[^}]*\}|nothing)"
)
ZONE = re.compile(r"\| INFO\s+\| \[(?P<world>[A-Za-z]+)/[^\]]+\]")


@dataclass
class LogRead:
    fights: list[list[Round]]
    lost: list[bool]  # per fight: ended in our defeat
    profiles: dict[str, dict]  # enemy -> {"school", "resist"} as last read


def _foes(text: str) -> list[tuple[str, int, int]]:
    out = []
    for part in re.split(r", (?=[^,]+ \d+/\d+)", text):
        m = FOE.match(part.strip())
        if m and not m["dead"]:
            out.append((m["name"].strip(), int(m["hp"]), int(m["max"])))
    return out


def read_fights(paths: list[Path]) -> list[list[Round]]:
    """Rounds grouped into fights (see read_log)."""
    return read_log(paths).fights


def read_log(paths: list[Path]) -> LogRead:
    """Rounds grouped into fights (a new fight when the round number drops or
    the enemies change completely; several decisions logged for one round
    keep the last), whether each was lost, and each enemy's school and
    resists as the game reported them."""
    import ast

    fights: list[list[Round]] = []
    lost: list[bool] = []
    profiles: dict[str, dict] = {}
    cur: list[Round] = []
    world = ""

    def close(defeated: bool = False):
        nonlocal cur
        if cur:
            fights.append(cur)
            lost.append(defeated)
        cur = []

    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = ROUND.search(line)
            if m:
                r = Round(int(m["n"]), int(m["hp"]), int(m["max"]), _foes(m["foes"]), m["act"],
                          bosses={b.strip() for b in re.findall(r"([^,]+?)\* \d+/\d+", m["foes"])},
                          world=world, minion_hp=int(m["mhp"]) if m["mhp"] else None)
                if cur and r.n == cur[-1].n:
                    cur[-1] = r
                    continue
                names = {f[0] for f in r.foes}
                if cur and (r.n < cur[-1].n or not names & {f[0] for f in cur[-1].foes}):
                    close()
                cur.append(r)
                continue
            z = ZONE.search(line)
            if z:
                world = z["world"]
                continue
            res = RESISTS.search(line)
            if res:
                try:
                    resist = {} if res["r"] == "nothing" else ast.literal_eval(res["r"])
                except (ValueError, SyntaxError):
                    resist = {}
                profiles[res["name"].strip()] = {"school": res["school"], "resist": resist}
                continue
            p = PREDICT.search(line)
            if p and cur:
                for kv in p["m"].split(","):
                    if "=" in kv:
                        i, d = kv.split("=")
                        cur[-1].predict[int(i)] = int(d)
            if "wizard defeated" in line:
                if cur:
                    close(True)
                elif lost:
                    lost[-1] = True  # "combat over" came first
            elif "combat over" in line:
                close()
    close()
    return LogRead(fights, lost, profiles)


def measure(fights: list[list[Round]], defeats: list[bool] | None = None):
    ours: dict[str, list[float]] = {}  # spell -> actual / predicted
    fizzles: dict[str, list[int]] = {}
    theirs: dict[str, list[int]] = {}  # enemy -> damage to us in a round it was alone
    shared: dict[str, list[float]] = {}  # enemy -> its even share of a round with others
    for fight in fights:
        for a, b in zip(fight, fight[1:], strict=False):
            if b.n != a.n + 1:
                continue
            cast = CAST.search(a.action)
            spell = cast["spell"] if cast else None
            # Our hit: health each predicted enemy lost by the next round.
            if spell and a.predict:
                for i, want in a.predict.items():
                    if i >= len(a.foes) or want <= 0:
                        continue
                    name, before, _mx = a.foes[i]
                    after = next((h for n, h, _ in b.foes if n == name), 0)
                    lost = before - after
                    fizzles.setdefault(spell, []).append(1 if lost <= 0 else 0)
                    if lost > 0 and after > 0:  # a kill hides the real damage
                        ours.setdefault(spell, []).append(lost / want)
            # Their hits: our health lost (a heal counts up to our max health).
            healed = HEAL_SPELLS.get(spell or "", 0)
            taken = max(0, min(a.max_hp, a.hp + healed) - b.hp)
            if len(a.foes) == 1:
                theirs.setdefault(a.foes[0][0], []).append(taken)
            elif a.foes:
                # Shared by max health, not evenly: a boss hits harder than its
                # guards (Plague Oni with two Defoulers was credited a third,
                # ~150 a round, and took the wizard from 666 to 202 alone).
                total = sum(max(m, 1) for _n, _h, m in a.foes)
                for name, _h, m in a.foes:
                    shared.setdefault(name, []).append(taken * max(m, 1) / total)
    # A lost fight's last round: what finished us was at least the health we
    # had (Malistaire Drake took 938 in one round; only hits we lived through
    # were counted, his worst read 534 and the bot stayed in at 938).
    for fight, defeated in zip(fights, defeats or [], strict=False):
        if not defeated or not fight:
            continue
        a = fight[-1]
        cast = CAST.search(a.action)
        taken = min(a.max_hp, a.hp + HEAL_SPELLS.get(cast["spell"] if cast else "", 0))
        foes = [f for f in a.foes if f[1] > 0]
        if len(foes) == 1:
            theirs.setdefault(foes[0][0], []).append(taken)
        elif foes:
            total = sum(max(m, 1) for _n, _h, m in foes)
            for name, _h, m in foes:
                shared.setdefault(name, []).append(taken * max(m, 1) / total)
    return ours, fizzles, theirs, shared


def minion_share(fights: list[list[Round]]) -> dict:
    """How much of the enemies' damage our minion takes: its health lost vs.
    ours, over rounds it was out in both (a heal of ours counted back)."""
    to_minion = to_us = rounds = 0
    for fight in fights:
        for a, b in zip(fight, fight[1:], strict=False):
            if b.n != a.n + 1 or a.minion_hp is None or b.minion_hp is None:
                continue
            cast = CAST.search(a.action)
            healed = HEAL_SPELLS.get(cast["spell"] if cast else "", 0)
            to_us += max(0, min(a.max_hp, a.hp + healed) - b.hp)
            to_minion += max(0, a.minion_hp - b.minion_hp)
            rounds += 1
    total = to_minion + to_us
    return {"share": round(to_minion / total, 3) if total else 0.0, "rounds": rounds}


def write_stats(paths: list[Path], out: Path = STATS_FILE) -> dict:
    """Save what the simulator needs: per enemy its max health, boss flag,
    per-round damage alone and shared; per spell our hit rate."""
    log = read_log(paths)
    fights = log.fights
    _ours, fizzles, theirs, shared = measure(fights, log.lost)
    info: dict[str, dict] = {}
    for fight in fights:
        for r in fight:
            for name, _h, mx in r.foes:
                e = info.setdefault(name, {"max_health": mx, "boss": False})
                e["max_health"] = max(e["max_health"], mx)
                e["boss"] = e["boss"] or name in r.bosses
    for name, e in info.items():
        e["alone"] = theirs.get(name, [])
        e["shared"] = [round(x) for x in shared.get(name, [])]
        e.update(log.profiles.get(name, {}))  # school, resist
    hit_rate = {s: round(1 - sum(xs) / len(xs), 3) for s, xs in fizzles.items() if len(xs) >= 5}
    # The recent fights (the deck search plays against a mix of them).
    recent = [
        {"world": f[0].world, "enemies": sorted({n for r in f for n, _h, _m in r.foes}), "lost": lost,
         "rounds": len(f)}
        for f, lost in list(zip(fights, log.lost, strict=False))[-RECENT_FIGHTS:]
    ]
    data = {"enemies": info, "hit_rate": hit_rate, "fights": recent, "minion": minion_share(fights)}
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=0), encoding="utf-8")
    return data


def report(paths: list[Path]) -> str:
    fights = read_fights(paths)
    ours, fizzles, theirs, _shared = measure(fights)
    out = [f"{len(fights)} fights, {sum(len(f) for f in fights)} rounds"]
    out.append("\nour hits: actual / predicted (median, n), missed")
    for spell, xs in sorted(ours.items(), key=lambda kv: -len(kv[1])):
        miss = fizzles.get(spell, [])
        out.append(f"  {spell:24s} x{statistics.median(xs):.2f}  n={len(xs):3d}  "
                   f"missed {sum(miss)}/{len(miss)}")
    out.append("\nenemies alone: our health lost per round (mean, median, max, n)")
    for name, xs in sorted(theirs.items(), key=lambda kv: -len(kv[1])):
        if len(xs) < 3:
            continue
        out.append(f"  {name:28s} {statistics.mean(xs):6.0f} {statistics.median(xs):6.0f} "
                   f"{max(xs):6d}  n={len(xs)}  zero-rounds {sum(1 for x in xs if x == 0)}")
    return "\n".join(out)


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or [Path("activity.log")]
    print(report(args))
    data = write_stats(args)
    print(f"\nsaved {len(data['enemies'])} enemies to {STATS_FILE}")
