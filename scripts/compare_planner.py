"""Brain alone vs brain + whole-deck planner, the same seeded fights.

python scripts/compare_planner.py N "filter1,filter2" [NAME=VALUE ...]

NAME=VALUE sets a lookahead module constant in the planner arm (e.g.
HORIZON=8 SAMPLES=16). The brain arm is cached in state/compare_brain.json.
Planner decisions run with a large budget (results don't depend on CPU load);
the time per planned round is reported (median / 95th percentile / max).
"""
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

# SRC: a snapshot of src/ to test (edits to the tree then can't reach a running comparison)
sys.path.insert(0, os.environ.get("SRC") or str(Path(__file__).resolve().parents[1] / "src"))

from wiz101_auto.combat import sim  # noqa: E402
from wiz101_auto.combat.sim import Foe, _s  # noqa: E402


def spells(school):
    return [_s("Blade", 0, "blade", school, 35), _s("Trap", 0, "trap", school, 30),
            _s("Hit", 3, "hit", school, 300), _s("Big", 5, "hit", school, 500),
            _s("All", 4, "aoe", school, 300)]


DECK = {"Gargantuan": 2, "Humongofrog": 1, "Myth Prism": 3, "Mythblade": 2, "Orthrus - T02 - A": 2,
        "Feint": 1, "Spirit Blade": 1}
SCEN = {
    "Porrich alone (6000, 80% myth resist)": [
        Foe("Porrich", 6000, "myth", {"myth": 0.8, "storm": -0.35, "death": -0.35}, spells("myth"),
            boss=True)],
    "Boss 5000": [Foe("Harlequin Knight", 5000, "fire", {}, spells("fire"), boss=True)],
    "Boss 3500": [Foe("Red Thorn Knight", 3500, "fire", {}, spells("fire"), boss=True)],
    "4 mobs (~2000)": [Foe(f"Rivershell Shaman {i}", 1945, "storm", {}, spells("storm")) for i in range(2)]
    + [Foe(f"Stormtide Elemental {i}", 2165, "storm", {}, spells("storm")) for i in range(2)],
    "2 mobs (~2000)": [Foe(f"Rivershell Shaman {i}", 1945, "storm", {}, spells("storm")) for i in range(2)],
    "Mob + boss 4000": [Foe("Night Goblin", 1695, "death", {}, spells("death")),
                        Foe("Red Thorn Knight", 4000, "fire", {}, spells("fire"), boss=True)],
    "Add + boss 5000": [Foe("Night Goblin", 1695, "death", {}, spells("death")),
                         Foe("Harlequin Knight", 5000, "fire", {}, spells("fire"), boss=True)],
}

TIMES: list = []
CRIT = bool(os.environ.get("CRIT"))  # CRIT=1: our hits crit by the odds (sim.ROLL_CRITS)


def init(look, sets):
    sim.USE_LOOKAHEAD = look
    sim.POWER_PIP_CHANCE = 0.8
    sim.LOOKAHEAD_BUDGET = 1000.0
    if CRIT:  # a level-75 wizard: 75% crit chance, enemies with no block (as live in Wysteria)
        sim.ROLL_CRITS = True
        mine = sim.load_my_stats()
        sim.load_my_stats = lambda *a, **k: {**mine, "level": 75, "crit": {"myth": 300.0}}
    from wiz101_auto.combat import lookahead
    lookahead.SAMPLES = 10
    for k, v in sets.items():
        setattr(lookahead, k, v)
    orig = lookahead.plan_round
    orig_choose = lookahead.choose

    def timed_choose(*a, **kw):
        t = time.perf_counter()
        out = orig_choose(*a, **kw)
        dt = time.perf_counter() - t
        if dt > 0.01:  # (a plan worked out, not a cached one)
            TIMES.append(dt)
        return out

    lookahead.plan_round = orig
    lookahead.choose = timed_choose
    sim.__dict__.setdefault("_patched", True)


def one(args):
    foes, seed = args
    from wiz101_auto.combat import lookahead
    lookahead._CACHE.clear()
    TIMES.clear()
    # (sim imports choose inside _round: patch the module attribute it reads)
    won, rounds = sim.simulate(DECK, foes, seed=seed, stats=sim.load_stats())
    return won, rounds, list(TIMES)


def run(foes, n, look, sets, procs):
    with Pool(procs, initializer=init, initargs=(look, sets)) as pool:
        return pool.map(one, [(foes, s) for s in range(n)])


def parse(v):
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return {"True": True, "False": False}.get(v, v)


def summary(res):
    wins = [r for ok, r, _t in res if ok]
    return len(wins), (sum(wins) / len(wins) if wins else 0.0)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    only = [o for o in sys.argv[2].split(",") if o and o != "all"] if len(sys.argv) > 2 else None
    sets = dict(a.split("=", 1) for a in sys.argv[3:])
    sets = {k: parse(v) for k, v in sets.items()}
    procs = int(os.environ.get("PROCS", "14"))
    cache_file = Path("state") / "compare_brain.json"
    try:
        cache = json.loads(cache_file.read_text())
    except Exception:
        cache = {}
    print("planner settings:", sets or "(defaults)", flush=True)
    for name, foes in SCEN.items():
        if only and not any(o in name for o in only):
            continue
        key = f"{name}|{n}" + ("|crit" if CRIT else "")
        if key not in cache:
            cache[key] = [(ok, r) for ok, r, _t in run(foes, n, False, {}, procs)]
            cache_file.write_text(json.dumps(cache))
        brain = [(ok, r, []) for ok, r in cache[key]]
        t = time.time()
        plan = run(foes, n, True, sets, procs)
        took = time.time() - t
        bw, br = summary(brain)
        pw, pr = summary(plan)
        only_p = [i for i, (b, p) in enumerate(zip(brain, plan, strict=True)) if p[0] and not b[0]]
        only_b = [i for i, (b, p) in enumerate(zip(brain, plan, strict=True)) if b[0] and not p[0]]
        both = [(b[1], p[1]) for b, p in zip(brain, plan, strict=True) if b[0] and p[0]]
        dr = sum(p - b for b, p in both) / len(both) if both else 0.0
        ts = sorted(x for _ok, _r, tt in plan for x in tt)
        tinfo = (f"t med {ts[len(ts) // 2]:.2f}s p95 {ts[int(len(ts) * .95)]:.2f}s max {ts[-1]:.2f}s"
                 if ts else "")
        print(f"{name:40s} brain {bw:3d}/{n} {br:5.2f}r | planner {pw:3d}/{n} {pr:5.2f}r | "
              f"only-planner {len(only_p)} only-brain {len(only_b)} | "
              f"paired d-rounds {dr:+.2f} (n={len(both)}) | "
              f"{tinfo} ({took:.0f}s)", flush=True)
        if os.environ.get("SEEDS"):
            print("   only-planner seeds", only_p, "only-brain seeds", only_b)
