"""Finding the deck that wins fastest, with the simulator (sim.py).

The deck is played against encounters: the fights logged recently in the
current world (a general deck: blades and Humongofrog for the groups), or one
group we lost to (a boss deck). A deck scores WIN_WEIGHT x its win rate minus
its mean rounds minus a little per card (everyday fights are all won: fewer
rounds and a thin deck whose combo comes up early tell decks apart; a loss
costs far more than a round). The search changes
one or two cards at a time (at most 3 copies of a spell, the game's limit)
and keeps what scores better on the same fights.

    python -m wiz101_auto.combat.deckopt                       # general deck for the current world
    python -m wiz101_auto.combat.deckopt --vs "Meowiarty,Agony Wraith,Clockwork Wizard"
    python -m wiz101_auto.combat.deckopt ... --out state/deck_advice.json
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

from . import sim

MAX_COPIES = 3
MIN_SIZE, MAX_SIZE = 12, 24
FIGHTS_PER_DECK = 1000  # simulated fights per deck compared (fewer: noise beats a tenth of a round)
WIN_WEIGHT = 30.0  # score = WIN_WEIGHT x win rate - mean rounds - SIZE_COST x cards: 1% of wins = 0.3 rounds
SIZE_COST = 0.03  # per card: a thin deck (the combo drawn early) wins a tie (the player's rule)
THIN_START = {"Humongofrog": 3, "Mythblade": 3, "Spirit Blade": 2, "Feint": 2, "Pixie": 3, "Minotaur": 2}
# The default deck (the player's rules): no prisms (a myth boss's niche), no minions (too slow for everyday
# fights; a boss deck may have them), a single-target hit, some heals.
# Prisms: only a boss deck (a myth boss). Blinding Light: the player keeps it out.
GENERAL_EXCLUDE = {"Troll Minion", "Cyclops Minion", "Myth Prism", "Blinding Light"}
GENERAL_NEEDS = {"single": 1, "heals": 2}
HEALS = {"Pixie"}
SINGLE_HITS = {"Minotaur", "Cyclops", "Troll", "Banshee", "Vampire", "Ghoul"}


def allowed(deck: dict[str, int], general: bool) -> bool:
    """A default deck keeps the player's rules; a boss deck is free."""
    if not general:
        return True
    return (sum(deck.get(c, 0) for c in SINGLE_HITS) >= GENERAL_NEEDS["single"]
            and sum(deck.get(c, 0) for c in HEALS) >= GENERAL_NEEDS["heals"])
BETTER_BY = 0.05  # a change is kept when it scores this much better (same fights)
ADVICE_FILE = Path("state") / "deck_advice.json"
GENERAL_FILE = Path("state") / "deck_general.json"

# The simulator's card names <-> the game's spell template names.
GAME_NAMES = {"Troll Minion": "Minion Myth 001", "Cyclops Minion": "Minion Myth 002"}
SIM_NAMES = {v: k for k, v in GAME_NAMES.items()}
TEAM_WORLDS = {"Aquila"}  # team dungeons: not what a solo deck faces
ONE_ONLY = {"Troll Minion", "Cyclops Minion"}  # one minion out at a time: a second copy adds nothing


def to_game(deck: dict[str, int]) -> dict[str, int]:
    return {GAME_NAMES.get(k, k): v for k, v in deck.items()}


def to_sim(deck: dict[str, int]) -> dict[str, int]:
    return {SIM_NAMES.get(k, k): v for k, v in deck.items() if SIM_NAMES.get(k, k) in sim.CARDS}


def available(known: list[str]) -> list[str]:
    """The spells the wizard knows that the simulator can play."""
    return sorted({SIM_NAMES.get(n, n) for n in known} & set(sim.CARDS) - {"Minor Fire Scorch"})


def foes_for(names: list[str], stats: dict) -> list[sim.Foe]:
    """Enemies as logged: health, boss, school and resists as the game read
    them, their damage per round; known spell lists (shields) kept."""
    enemies = stats.get("enemies", {})
    out = []
    for n in names:
        e = enemies.get(n, {})
        hp = int(e.get("max_health", 800))
        boss = bool(e.get("boss", False))
        out.append(sim.Foe(n, hp, e.get("school", ""), dict(e.get("resist", {})),
                           sim.KNOWN_SPELLS.get(n, []), boss=boss,
                           samples=sim.samples_for(n, hp, boss, stats)))
    return out


def encounters(stats: dict, world: str | None = None, most: int = 12) -> list[tuple[list[sim.Foe], int]]:
    """The recent fights of `world`, as enemy groups with how often each came
    up: the mix a general deck must handle. A world with few fights yet (just
    arrived in MooShu) borrows the latest solo world's; team dungeons (Aquila:
    teammates did most of the damage) never count."""
    fights = [f for f in stats.get("fights", []) if f.get("world") not in TEAM_WORLDS]
    mine = [f for f in fights if world and f.get("world") == world]
    if len(mine) < 8:
        worlds = [f.get("world") for f in fights if f.get("world") and f.get("world") != world]
        prev = worlds[-1] if worlds else None
        mine = [f for f in fights if f.get("world") == prev] or fights[-120:]
    counts = Counter(tuple(f["enemies"]) for f in mine if f.get("enemies"))
    enemies = stats.get("enemies", {})
    out = []
    for group, n in counts.most_common(most):
        # A boss retried many times (Meowiarty x7) is still one fight of the world.
        boss = any(enemies.get(e, {}).get("boss") for e in group)
        out.append((foes_for(list(group), stats), 1 if boss else n))
    return out


def _job(args):
    deck, foes, seed, stats = args
    return sim.simulate(deck, foes, seed=seed, stats=stats)


def score(pool, deck: dict[str, int], mix, stats: dict, n: int = FIGHTS_PER_DECK, seed0: int = 0):
    """(score, win rate, mean rounds of the wins) over `n` fights spread over
    the encounters by how often they come up (the same fights for every deck)."""
    total = sum(w for _f, w in mix) or 1
    jobs = []
    for i, (foes, w) in enumerate(mix):
        k = max(4, round(n * w / total))
        jobs += [(deck, foes, seed0 + 1000 * i + s, stats) for s in range(k)]
    results = pool.map(_job, jobs, chunksize=8) if pool else [_job(j) for j in jobs]
    wins = [r for ok, r in results if ok]
    rate = len(wins) / len(results)
    rounds = sum(wins) / len(wins) if wins else float(sim.MAX_ROUNDS)
    return WIN_WEIGHT * rate - rounds - SIZE_COST * sum(deck.values()), rate, rounds


def neighbours(deck: dict[str, int], cards: list[str], rng: random.Random) -> list[dict[str, int]]:
    out = []
    size = sum(deck.values())

    def cap(c):
        return 1 if c in ONE_ONLY else MAX_COPIES

    for a in cards:
        if deck.get(a, 0) < cap(a) and size < MAX_SIZE:
            out.append({**deck, a: deck.get(a, 0) + 1})
        if deck.get(a, 0) > 0 and size > MIN_SIZE:
            out.append({**deck, a: deck[a] - 1})
        for b in cards:
            if a != b and deck.get(a, 0) > 0 and deck.get(b, 0) < cap(b):
                out.append({**deck, a: deck[a] - 1, b: deck.get(b, 0) + 1})
    rng.shuffle(out)
    return [{k: v for k, v in d.items() if v} for d in out]


def search(start: dict[str, int], cards: list[str], mix, stats: dict, pool=None, seconds: float = 600,
           log=print, general: bool = False) -> tuple[dict[str, int], float, float]:
    """Hill-climb from `start` for up to `seconds`. (deck, win rate, rounds).
    `general`: the default deck's rules (no minions, a single-target hit,
    heals)."""
    rng = random.Random(7)
    if general:
        cards = [c for c in cards if c not in GENERAL_EXCLUDE]
    deck = {k: v for k, v in start.items() if k in cards}
    best, rate, rounds = score(pool, deck, mix, stats)
    log(f"start: win {rate:.1%} in ~{rounds:.1f} rounds  {deck}")
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        improved = False
        for cand in neighbours(deck, cards, rng)[:50]:
            if time.monotonic() - t0 > seconds:
                break
            if not allowed(cand, general):
                continue
            sc, r, rd = score(pool, cand, mix, stats)
            if sc > best + BETTER_BY:
                deck, best, rate, rounds, improved = cand, sc, r, rd, True
                log(f"[{time.monotonic() - t0:.0f}s] win {rate:.1%} in ~{rounds:.1f} rounds, "
                    f"{sum(deck.values())} cards  {deck}")
                break
        if not improved:
            break
    # Confirm on fights the search never saw.
    _sc, rate, rounds = score(pool, deck, mix, stats, n=FIGHTS_PER_DECK * 3, seed0=500000)
    return deck, rate, rounds


PRISM_RESIST = 0.5  # a boss resisting our school this much: the search starts with prisms
PRISM_SEED = 3


def seed_prisms(start: dict[str, int], cards: list[str], mix, school: str = "myth") -> dict[str, int]:
    """Against a boss that resists our school (Haru, Meowiarty: myth 80%) the
    boss deck search starts with Myth Prisms in (a prism turns the next hit on
    it into the opposite school, which it's weak to). One card at a time, the
    hill climb never got there by itself. The search may still drop them."""
    prism = f"{school.title()} Prism"
    if prism not in cards or start.get(prism, 0) >= PRISM_SEED:
        return start
    resists = any(f.boss and f.resist.get(school, 0) >= PRISM_RESIST for foes, _w in mix for f in foes)
    if not resists:
        return start
    return {**start, prism: PRISM_SEED}


def main(argv=None):
    import multiprocessing as mp

    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", default=None,
                    help="comma-separated enemy names (default: this world's recent fights)")
    ap.add_argument("--world", default=None)
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--out", default=None, help="write the result here (json)")
    ap.add_argument("--thin", action="store_true", help="start from a thin blades + Humongofrog deck")
    ap.add_argument("--start", default=None, help="a deck json to start from (the current default deck)")
    args = ap.parse_args(argv)

    try:  # the fights logged up to now
        from .calibrate import write_stats

        write_stats([Path("activity.log")])
    except Exception:
        pass
    stats = sim.load_stats()
    progress = json.loads(Path("state", "progress.json").read_text(encoding="utf-8"))
    cards = available(progress.get("known_spells", []))
    try:
        current = to_sim(json.loads(Path("state", "deck.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        current = {}
    if args.vs:
        mix = [(foes_for([n.strip() for n in args.vs.split(",")], stats), 1)]
        what = f"vs {args.vs}"
    else:
        world = args.world or (stats.get("fights") or [{}])[-1].get("world")
        mix = encounters(stats, world)
        what = f"general ({world}: {len(mix)} kinds of fight)"
    print(f"deck search {what}; spells: {', '.join(cards)}", flush=True)
    with mp.Pool(max(2, (mp.cpu_count() or 4) - 2)) as pool:
        start = dict(THIN_START) if args.thin or not current else current
        if args.start:
            try:
                start = to_sim(json.loads(Path(args.start).read_text(encoding="utf-8")).get("deck", {}))
            except (OSError, ValueError):
                pass
        if args.vs:
            start = seed_prisms(start, cards, mix)
        deck, rate, rounds = search(start, cards, mix, stats, pool, general=not args.vs,
                                    seconds=args.minutes * 60, log=lambda s: print(s, flush=True))
    print(f"BEST {what}: win {rate:.1%} in ~{rounds:.1f} rounds  {deck}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({
            "vs": args.vs, "deck": to_game(deck), "win": rate, "rounds": rounds, "at": time.time(),
        }, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
