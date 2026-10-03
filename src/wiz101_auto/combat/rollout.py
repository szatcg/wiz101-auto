"""Choosing a move by playing it out: every move this step (each castable card
on each target, or passing) is run forward in the simulator (sim.py) from the
live fight, the brain playing the rounds after, and scored by how the fight
stands HORIZON rounds later (won and how fast, enemy health taken, enemies
killed, our health; dying worst). The brain's own pick is replaced only when
another move scores clearly better.

The rollouts run in worker processes (one move per job) so a decision takes a
few seconds; past the time limit the brain's pick stands.
"""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ProcessPoolExecutor

from loguru import logger

from .model import Action, ActionKind, Battle


def hit_kills(card, battle, enemy) -> bool:
    from .brain import hit_damage

    return hit_damage(card, battle.me, enemy) >= enemy.health

ROLLOUTS = 32  # playouts per move (the same random draws for every move)
TIME_LIMIT = 12.0  # seconds for the whole decision
MARGIN = 0.03  # a move must beat the brain's by this much (rollout noise)
# Passing or discarding instead of the brain's cast needs a clear win: small
# edges for 'pass' over 6 rounds (2 Humongofrogs and 5 pips in hand) lost a
# fight to two Imitsu Defoulers.
IDLE_MARGIN = 0.15
MIN_ENEMY_HEALTH = 0  # all fights (raise it to leave easy ones to the brain)

_STATS: dict | None = None


def _eval_one(battle: Battle, index: int, strat, n: int, seed: int, discards: int, stats: dict | None = None):
    """In a worker: the value of candidates(battle)[index]."""
    global _STATS
    from . import sim

    if stats is None:
        if _STATS is None:
            _STATS = sim.load_stats()
        stats = _STATS
    action = sim.candidates(battle, discards)[index]
    out = sim.evaluate(battle, [action], strat, stats, n=n, seed0=seed, discards=discards)[0]
    return index, out.value, out.wins, out.deaths, out.damage


def worth_it(battle: Battle, brain: Action | None = None) -> bool:
    """Every fight (the player: the simulator picks the fastest win), except
    when the brain's move ends it now."""
    live = battle.live_enemies
    if not live or sum(e.health for e in live) < MIN_ENEMY_HEALTH:
        return False
    hits = brain is not None and brain.kind is ActionKind.CAST and brain.card is not None
    if hits and brain.card.is_damage:
        from .brain import _kills_all

        try:
            if _kills_all(battle, brain):
                return False
        except Exception:
            pass
    return True


def _same(a: Action, b: Action) -> bool:
    if a.kind is not b.kind:
        return False
    if a.kind is ActionKind.PASS:
        return bool(a.plan_cards) == bool(b.plan_cards)  # (a dig isn't a plain pass)
    same_card = a.card is not None and b.card is not None and a.card.index == b.card.index
    ta, tb = a.target, b.target
    same_target = (ta is None and tb is None) or (ta is not None and tb is not None and ta.name == tb.name)
    return same_card and same_target


HEAL_NEEDED_BELOW = 0.5  # a rollout's heal instead of the brain's move: only under half health...


def _free_move(move: Action) -> bool:
    """A discard (doesn't end the turn) or a 0-pip cast: nothing a pass saves."""
    if move.kind is ActionKind.DISCARD:
        return True
    return move.kind is ActionKind.CAST and move.card is not None and move.card.pip_cost == 0


def _early_heal(move: Action, battle: Battle, stats=None) -> bool:
    """A heal the simulator would play while we're well (1364 of 2189 against
    Boris Blackrock, over the brain's pass): not taken unless under half
    health or the enemies' worst logged hit could take the rest."""
    from . import sim

    card = move.card
    if move.kind is not ActionKind.CAST or card is None or not card.is_heal or card.is_damage:
        return False
    me = battle.me
    if me.health_ratio < HEAL_NEEDED_BELOW:
        return False
    stats = stats if stats is not None else sim.load_stats()
    worst = sum(max(sim.samples_for(e.name, e.max_health, e.is_boss, stats) or [0])
                for e in battle.live_enemies)
    return worst < me.health


class RolloutPlanner:
    def __init__(self, workers: int | None = None, time_limit: float = TIME_LIMIT, stats: dict | None = None):
        self.workers = workers or max(2, min(8, (os.cpu_count() or 4) - 2))
        self.time_limit = time_limit
        self.stats = stats  # None: each worker reads state/enemy_stats.json
        self._pool: ProcessPoolExecutor | None = None
        self._seed = int(time.time())

    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=self.workers)
        return self._pool

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    async def choose(self, battle: Battle, brain: Action, strat=None, discards: int = 2) -> Action:
        """The brain's move, or a move that plays out clearly better."""
        from . import sim

        kinds = (ActionKind.CAST, ActionKind.PASS, ActionKind.DISCARD)
        if brain.kind not in kinds or not worth_it(battle, brain):
            return brain
        from .brain import wasted_setup

        # (Never a copy of a blade/trap already up: a second Mythblade adds
        # nothing in the game, though the simulator stacked it.)
        moves = [m for m in sim.candidates(battle, discards)
                 if _same(m, brain) or not (_early_heal(m, battle, self.stats) or wasted_setup(m, battle))]
        try:  # the last battle planned, to replay offline (state/rollout_battle.pkl); not from tests
            if self.stats is not None:
                raise RuntimeError("test stats")
            import pickle
            from pathlib import Path

            Path("state", "rollout_battle.pkl").write_bytes(pickle.dumps((battle, brain, discards)))
        except Exception:
            pass
        mine = next((i for i, m in enumerate(moves) if _same(m, brain)), None)
        if mine is None or len(moves) < 2:
            return brain
        self._seed += 1
        loop = asyncio.get_running_loop()
        pool = self._executor()
        jobs = [
            loop.run_in_executor(
                pool, _eval_one, battle, i, strat, ROLLOUTS, self._seed, discards, self.stats
            )
            for i in range(len(moves))
        ]
        started = time.monotonic()
        try:
            results = await asyncio.wait_for(asyncio.gather(*jobs), self.time_limit)
        except TimeoutError:
            logger.info(f"rollouts: no answer within {self.time_limit:.0f}s; the brain's move stands")
            return brain
        except Exception as exc:  # a broken worker must not cost the round
            logger.warning(f"rollouts failed ({exc!r}); the brain's move stands")
            self.close()
            return brain
        values = {i: (v, w, d, dmg) for i, v, w, d, dmg in results}
        best = max(values, key=lambda i: values[i][0])
        took = time.monotonic() - started

        def line(i):
            v, w, d, dmg = values[i]
            return f"{moves[i].describe()[:60]} (value {v:+.2f}, won {w:.0%}, died {d:.0%}, dmg {dmg:.0%})"

        if moves[best].kind is ActionKind.PASS and _free_move(brain):
            # A pass ends the turn; the brain's discard or 0-pip cast doesn't
            # spend anything (a discard of Humongofrog, then Mythblade, became
            # a pass and the blade never went up).
            logger.debug(f"rollouts ({took:.1f}s): the brain's free {line(mine)} over a pass")
            return brain
        idle = moves[best].kind in (ActionKind.PASS, ActionKind.DISCARD) and brain.kind is ActionKind.CAST
        margin = IDLE_MARGIN if idle else MARGIN
        heal = brain.kind is ActionKind.CAST and brain.card is not None and brain.card.is_heal
        if heal and not brain.card.is_damage:
            # A heal has no head start (the player: low health and a fast win
            # often beats 4 pips and 4 rounds for 500 health): whatever plays
            # out better, deaths counted, goes instead.
            margin = 0.0
        if (best != mine and brain.kind is ActionKind.CAST and brain.card is not None and brain.card.is_aoe
                and brain.card.is_damage and not (moves[best].card is not None and moves[best].card.is_damage)
                and any(hit_kills(brain.card, battle, e) for e in battle.live_enemies)):
            # The player: the brain's hit-all that kills enemies goes (Orthrus
            # with 12 pips lost to a Myth Trap, then a second Mythblade).
            logger.debug(f"rollouts ({took:.1f}s): the brain's killing {line(mine)} holds over {line(best)}")
            return brain
        if best != mine and moves[best].kind is ActionKind.CAST and brain.kind is ActionKind.CAST:
            from .brain import Strategy, _hit_all_waits

            if _hit_all_waits(battle, moves[best], strat or Strategy()):
                # The player's rule: a hit-all that won't kill them all waits
                # for the setup that lets it (an unbuffed Humongofrog left two
                # Moonstriders at 156 and 193 and the fight ran on).
                logger.debug(f"rollouts ({took:.1f}s): {line(best)} would leave enemies up; "
                             f"the brain's setup {line(mine)} holds")
                return brain
        if best != mine and values[best][0] > values[mine][0] + margin:
            logger.info(f"rollouts ({took:.1f}s): {line(best)} beats the brain's {line(mine)}")
            chosen = moves[best]
            return Action(chosen.kind, chosen.card, chosen.target,
                          reason=f"rollouts: value {values[best][0]:+.2f} vs {values[mine][0]:+.2f}")
        logger.debug(f"rollouts ({took:.1f}s): the brain's {line(mine)} holds (best other: {line(best)})")
        return brain
