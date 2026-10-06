"""Trace one seeded fight with the brain alone and with the planner, side by side.

python scripts/trace_planner.py "scenario filter" seed [NAME=VALUE ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_planner as cp  # noqa: E402
from compare_planner import sim  # noqa: E402


def show(look, foes, seed, sets):
    cp.init(look, sets)
    from wiz101_auto.combat import lookahead
    lookahead._CACHE.clear()
    r = sim.replay(cp.DECK, foes, stats=sim.load_stats(), seed=seed)
    print("=== planner" if look else "=== brain", "won", r["won"], "rounds", r["rounds"])
    for st in r["steps"]:
        who = st.get("who")
        if who == "Wizard":
            print(f"  r{st.get('round')} {st.get('act')} {st.get('card', '')} -> {st.get('target', '')} "
                  f"{st.get('dmg', '')} | {(st.get('reason') or '')[:170]}")
        elif st.get("act") == "hit":
            print(f"      {who} {st.get('card')} {st.get('dmg')}")


if __name__ == "__main__":
    flt, seed = sys.argv[1], int(sys.argv[2])
    sets = {k: cp.parse(v) for k, v in (a.split("=", 1) for a in sys.argv[3:])}
    foes = next(f for n, f in cp.SCEN.items() if flt in n)
    show(False, foes, seed, {})
    show(True, foes, seed, sets)
