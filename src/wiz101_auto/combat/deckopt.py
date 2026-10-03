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
from .model import EffectKind, Target

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


# A boss deck keeps heals too: the searched ones dropped every Pixie and
# the wizard fell to Plague Oni and Haru at 6-80 health with nothing to heal.
BOSS_NEEDS = {"heals": 2}


def allowed(deck: dict[str, int], general: bool) -> bool:
    """A default deck keeps the player's rules; a boss deck keeps its heals."""
    heals = sum(deck.get(c, 0) for c in HEALS)
    if not general:
        return heals >= BOSS_NEEDS["heals"]
    return (sum(deck.get(c, 0) for c in SINGLE_HITS) >= GENERAL_NEEDS["single"]
            and heals >= GENERAL_NEEDS["heals"])


def with_heals(start: dict[str, int], cards: list[str], needs: int) -> dict[str, int]:
    """The search's start with enough Pixies (one card at a time it could
    never reach a deck the rules allow)."""
    have = sum(start.get(c, 0) for c in HEALS)
    if have >= needs or "Pixie" not in cards:
        return start
    return {**start, "Pixie": start.get("Pixie", 0) + needs - have}
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


# Never in a searched deck (the player: they clutter it, and a thin deck draws
# the blade/trap/big-hit hand sooner). The player's list is config.yaml's
# deck_search: banned cards, banned schools (death: the simulator overrates
# its hits) with exceptions (Feint). Minion summons are never allowed.
EXCLUDE = {"Dark Sprite", "Vampire", "Blinding Light", "Earthquake"}
CONFIG = Path("config.yaml")


def deck_rules(path: Path = CONFIG) -> tuple[set[str], set[str], set[str]]:
    """(banned cards, banned schools, allowed exceptions) from config.yaml's
    deck_search section; the built-in EXCLUDE when there's none."""
    try:
        import yaml

        cfg = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("deck_search") or {}
    except Exception:
        cfg = {}
    banned = set(cfg.get("banned") or EXCLUDE)
    schools = {str(x).lower() for x in cfg.get("banned_schools") or []}
    allowed = set(cfg.get("allowed") or [])
    return banned, schools, allowed


def is_banned(name: str, rules: tuple[set[str], set[str], set[str]]) -> bool:
    banned, schools, allowed = rules
    if name in allowed:
        return False
    if name in banned:
        return True
    if name in sim.CARDS:
        c = sim.CARDS[name]()
        if EffectKind.SUMMON in c.kinds:
            return True  # no minions (the player: slow, and their worth is hard to judge)
        return bool(schools) and (c.school or "").lower() in schools
    return False


def available(known: list[str]) -> list[str]:
    """The spells the wizard knows that the simulator can play."""
    rules = deck_rules()
    pool = {SIM_NAMES.get(n, n) for n in known} & set(sim.CARDS) - {"Minor Fire Scorch"}
    return sorted(n for n in pool if not is_banned(n, rules))


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
    sc = WIN_WEIGHT * rate - rounds - SIZE_COST * sum(deck.values())
    if n == FIGHTS_PER_DECK:  # (not the confirmations)
        EVALS[_key(deck)] = {"deck": dict(deck), "win": rate, "rounds": rounds, "score": sc}
    return sc, rate, rounds


EVALS: dict[str, dict] = {}  # every deck scored this run (the visualizer's table)


def _key(deck: dict[str, int]) -> str:
    return ",".join(f"{k}x{v}" for k, v in sorted(deck.items()) if v)


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


def useful_against(cards: list[str], mix) -> list[str]:
    """The cards the brain would play against these enemies: not a school
    shield none of them hits with (Ether Shield vs Cyrus Drake, myth: the
    search kept two, the bot binned them on sight), not a prism none of them
    gains from. Same rules the brain discards by."""
    from .brain import _prism_useless, _shield_useless
    from .model import Battle, Combatant

    foes = [f for group, _w in mix for f in group]
    enemies = [Combatant(f.name, f.health, f.health, is_enemy=True, is_boss=f.boss, school=f.school,
                         resist=dict(f.resist)) for f in foes]
    me = Combatant("Me", 2000, 2000, is_client=True, school="myth")
    out = []
    for name in cards:
        c = sim.CARDS[name]()
        if EffectKind.SHIELD in c.kinds and not c.is_damage and _shield_useless(c, enemies):
            continue
        hits = [sim.CARDS[n]() for n in cards]
        fight = Battle(me=me, allies=[], enemies=enemies, cards=hits)
        if "prism" in name.lower() and _prism_useless(c, fight):
            continue
        out.append(name)
    return out


def single_target_seed(cards: list[str]) -> dict[str, int]:
    """A start for a boss: blades, traps, Feint, the strongest single hits
    (and lasting boosts like Vermin Virtuoso), heals; no hit-all spells. From
    the AoE deck one card at a time the search never got there (Jade Oni,
    alone: 16% to win)."""
    deck: dict[str, int] = {}
    singles, setup = [], []
    for name in cards:
        c = sim.CARDS[name]()
        if any(e.kind in (EffectKind.BLADE, EffectKind.TRAP) for e in c.effects) and not c.is_damage:
            setup.append(name)
        elif c.is_damage and not c.is_aoe and c.pip_cost >= 3:
            singles.append((c.base_damage() * (1.3 if _has_aura(c) else 1.0), name))
    for name in setup:
        deck[name] = 1 if name in ONE_ONLY else MAX_COPIES
    for _dmg, name in sorted(singles, reverse=True)[:3]:
        deck[name] = MAX_COPIES
    if "Pixie" in cards:
        deck["Pixie"] = 2
    while sum(deck.values()) > MAX_SIZE:
        biggest = max(deck, key=lambda k: deck[k])
        deck[biggest] -= 1
    return {k: v for k, v in deck.items() if v}


def fit_size(deck: dict[str, int]) -> dict[str, int]:
    """Within MAX_SIZE: a copy off the most-copied cards, never the last
    copy of anything, prisms and heals kept (a seed of 26 cards was chosen
    as it stood)."""
    deck = dict(deck)
    keep = {"Myth Prism", *HEALS}
    while sum(deck.values()) > MAX_SIZE:
        cut = [k for k in deck if deck[k] > 1 and k not in keep] or [k for k in deck if deck[k] > 1]
        if not cut:
            break
        deck[max(cut, key=lambda k: deck[k])] -= 1
    return deck


def _has_aura(c) -> bool:
    return any(e.kind is EffectKind.OTHER and e.target is Target.NONE and e.school and e.value > 0
               for e in c.effects)


WIN_FLOOR = 0.5  # a deck "wins" when it wins at least this share of the fights
TOP_N = 5  # the fastest winning decks iterated on
REFINE_SHARE = 0.4  # share of the search time for iterating on them
RUNS_DIR = Path("state") / "sim_runs"  # one report per search (the /sim page)


def refine_fastest(cards, mix, stats, pool, seconds: float, general: bool, log=print) -> list[dict]:
    """The TOP_N decks that win (WIN_FLOOR) in the fewest rounds, each
    improved one card at a time while it stays a winner and gets faster.
    Returns the improved ones (fastest first)."""
    rng = random.Random(11)
    winners = sorted((e for e in EVALS.values() if e["win"] >= WIN_FLOOR), key=lambda e: e["rounds"])
    if not winners:  # nothing wins half its fights: the best by score
        winners = sorted(EVALS.values(), key=lambda e: -e["score"])
    top = winners[:TOP_N]
    log(f"refining the {len(top)} fastest winners: " + "; ".join(
        f"{e['rounds']:.1f}r {e['win']:.0%}" for e in top))
    t0 = time.monotonic()
    out = []
    for i, e in enumerate(top):
        deck, win, rounds = dict(e["deck"]), e["win"], e["rounds"]
        budget = t0 + seconds * (i + 1) / len(top)
        improved = True
        while improved and time.monotonic() < budget:
            improved = False
            for cand in neighbours(deck, cards, rng)[:40]:
                if time.monotonic() > budget:
                    break
                if not allowed(cand, general):
                    continue
                _sc, w, r = score(pool, cand, mix, stats)
                if w >= min(WIN_FLOOR, win) and r < rounds - 0.2:
                    deck, win, rounds, improved = cand, w, r, True
                    log(f"[fastest #{i + 1}] {r:.1f} rounds, win {w:.0%}  {deck}")
                    break
        out.append({"deck": deck, "win": win, "rounds": rounds})
    return sorted(out, key=lambda e: e["rounds"])


def choose_fastest(finalists: list[dict], mix, stats, pool) -> dict:
    """Confirm the finalists on fights the search never saw; the one winning
    (WIN_FLOOR) in the fewest rounds, else the best win rate."""
    checked = []
    for e in finalists:
        _sc, w, r = score(pool, e["deck"], mix, stats, n=FIGHTS_PER_DECK * 3, seed0=500000)
        checked.append({"deck": e["deck"], "win": w, "rounds": r})
    winners = [e for e in checked if e["win"] >= WIN_FLOOR]
    if winners:
        return min(winners, key=lambda e: e["rounds"])
    return max(checked, key=lambda e: (e["win"], -e["rounds"]))


class Report:
    """state/sim_runs/<time>_<what>.json, rewritten as the search goes."""

    def __init__(self, what: str, mix, cards: list[str]):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch if ch.isalnum() else "_" for ch in what)[:60]
        self.path = RUNS_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{slug}.json"
        self.data = {
            "what": what, "started": time.time(), "stage": "starting", "cards": cards,
            "foes": [[{"name": f.name, "hp": f.health, "school": f.school, "resist": f.resist, "boss": f.boss}
                      for f in group] + [{"weight": w}] for group, w in mix],
            "log": [],
        }
        self.write()

    def note(self, line: str):
        self.data["log"].append(line)
        self.data["log"] = self.data["log"][-200:]
        self.write()

    def stage(self, name: str, **more):
        self.data.update(stage=name, **more)
        self.data["evaluated"] = sorted(EVALS.values(), key=lambda e: -e["score"])[:60]
        self.data["fastest"] = sorted((e for e in EVALS.values() if e["win"] >= WIN_FLOOR),
                                      key=lambda e: e["rounds"])[:TOP_N]
        self.data["decks_tried"] = len(EVALS)
        self.write()

    def write(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


def replays_for(deck: dict[str, int], mix, stats, n: int = 24) -> tuple[list[dict], dict]:
    """A few recorded fights of `deck` (the fastest win, the slowest win, a
    loss if any) and what it did over `n` fights: casts per card, moves per
    kind."""
    foes = max(mix, key=lambda m: m[1])[0]
    fights = [sim.replay(deck, foes, stats, seed=900000 + i) for i in range(n)]
    usage: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for f in fights:
        for st in f["steps"]:
            if st.get("who") == "Wizard" and st.get("act") == "cast":
                usage[st["card"]] = usage.get(st["card"], 0) + 1
                kinds[st.get("kind", "other")] = kinds.get(st.get("kind", "other"), 0) + 1
            elif st.get("who") == "Wizard" and st.get("act") in ("discard", "pass"):
                kinds[st["act"]] = kinds.get(st["act"], 0) + 1
    wins = sorted((f for f in fights if f["won"]), key=lambda f: f["rounds"])
    picked = []
    if wins:
        picked += [dict(wins[0], label="fastest win"), dict(wins[-1], label="slowest win")]
    losses = [f for f in fights if not f["won"]]
    if losses:
        picked.append(dict(losses[0], label="a loss"))
    stats_out = {"fights": n, "won": len(wins), "mean_rounds": (sum(f["rounds"] for f in wins) / len(wins))
                 if wins else None, "casts": usage, "moves": kinds}
    return picked, stats_out


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
        from .calibrate import activity_logs, write_stats

        write_stats(activity_logs())
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
        cards = useful_against(cards, mix)  # (what the brain would bin on sight never goes in)
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
        start = with_heals(start, cards, BOSS_NEEDS["heals"] if args.vs else GENERAL_NEEDS["heals"])
        starts = [start]
        if args.vs:
            seed = fit_size(seed_prisms(with_heals(single_target_seed(cards), cards, BOSS_NEEDS["heals"]),
                                        cards, mix))
            if seed and seed != start:
                starts.append(seed)
        report = Report(what, mix, cards)

        def say(line: str):
            print(line, flush=True)
            report.note(line)

        climb = args.minutes * 60 * (1 - REFINE_SHARE)
        for i, st in enumerate(starts):  # each start gets its share of the time
            report.stage(f"searching from start {i + 1} of {len(starts)}", starts=starts)
            search(st, cards, mix, stats, pool, general=not args.vs, seconds=climb / len(starts), log=say)
        report.stage("refining the fastest winners")
        finalists = refine_fastest(cards, mix, stats, pool, args.minutes * 60 * REFINE_SHARE,
                                   general=not args.vs, log=say)
        report.stage("confirming the finalists", finalists=finalists)
        chosen = choose_fastest(finalists, mix, stats, pool)
        deck, rate, rounds = chosen["deck"], chosen["win"], chosen["rounds"]
        report.stage("recording sample fights", chosen=chosen)
        replays, usage = replays_for(deck, mix, stats)
        report.stage("done", chosen=chosen, replays=replays, usage=usage, finished=time.time())
    print(f"BEST {what}: win {rate:.1%} in ~{rounds:.1f} rounds  {deck}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({
            "vs": args.vs, "deck": to_game(deck), "win": rate, "rounds": rounds, "at": time.time(),
        }, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
