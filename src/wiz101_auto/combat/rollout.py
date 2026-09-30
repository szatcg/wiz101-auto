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

ROLLOUTS = 32  # playouts per move (the same random draws for every move)
TIME_LIMIT = 12.0  # seconds for the whole decision
MARGIN = 0.03  # a move must beat the brain's by this much (rollout noise)
MIN_ENEMY_HEALTH = 1500  # fights easier than this (all enemies' health) are left to the brain

_STATS: dict | None = None


def _eval_one(battle: Battle, index: int, strat, n: int, seed: int, discards: int, stats: dict | None = None):
    """In a worker: the value of candidates(battle)[index]."""
    global _STATS
    from . import sim

    if stats is None:
        if _STATS is None:
            _STATS = sim.load_stats()
        stats = _STATS
    action = sim.candidates(battle)[index]
    out = sim.evaluate(battle, [action], strat, stats, n=n, seed0=seed, discards=discards)[0]
    return index, out.value, out.wins, out.deaths, out.damage


def worth_it(battle: Battle) -> bool:
    """A fight worth the seconds: a boss, or a lot of health to get through."""
    live = battle.live_enemies
    return bool(live) and (any(e.is_boss for e in live) or sum(e.health for e in live) >= MIN_ENEMY_HEALTH)


def _same(a: Action, b: Action) -> bool:
    if a.kind is not b.kind:
        return False
    if a.kind is ActionKind.PASS:
        return True
    same_card = a.card is not None and b.card is not None and a.card.index == b.card.index
    ta, tb = a.target, b.target
    same_target = (ta is None and tb is None) or (ta is not None and tb is not None and ta.name == tb.name)
    return same_card and same_target


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

        if brain.kind not in (ActionKind.CAST, ActionKind.PASS) or not worth_it(battle):
            return brain
        moves = sim.candidates(battle)
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

        if best != mine and values[best][0] > values[mine][0] + MARGIN:
            logger.info(f"rollouts ({took:.1f}s): {line(best)} beats the brain's {line(mine)}")
            chosen = moves[best]
            return Action(chosen.kind, chosen.card, chosen.target,
                          reason=f"rollouts: value {values[best][0]:+.2f} vs {values[mine][0]:+.2f}")
        logger.debug(f"rollouts ({took:.1f}s): the brain's {line(mine)} holds (best other: {line(best)})")
        return brain
