"""Potion refills: low on potions, buy a full set from Hilda Brewer.

The bot drank the potions it had and then went on without (a low-health
start lost fights). At a free moment out of dungeons, below MIN_POTIONS:
mark, go to the Commons by the gates (Go Home first from another world),
open Hilda Brewer's shop, Fill All, Buy, close, Recall back. The shop's
window names and Hilda's spot are from Deimos (src/utils.py buy_potions,
GPL-3.0).
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .petdance import _click, _visible, _wait_for
from .upkeep import is_free, wait_for_loading

COMMONS = "WizardCity/WC_Hub"
HILDA = XYZ(-4398.71, 1016.20, 229.0)  # Hilda Brewer in the Commons
RETRY_MINUTES = 30.0  # after a failed trip (no gold, shop not found)
TRAVEL_RETRY_MINUTES = 3.0  # the Commons not reached (Go Home pressed just after the hub button did nothing)
SHOP_TRIES = 8
MIN_POTIONS = 3  # the player's: always at least this many (or the most the wizard holds)


def should_buy_potions(charge: float, most: float, in_team_dungeon: bool = False,
                       team_return: bool = False) -> bool:
    """A trip to Hilda Brewer now? Below MIN_POTIONS (or below the most the
    wizard can hold, if fewer), but never with a team: not from inside a team
    dungeon (the team moves on without us) and not on the way back to one
    after a defeat (heal and Recall straight back in: the player's rule)."""
    if in_team_dungeon or team_return or most < 1:
        return False
    return charge < min(MIN_POTIONS, most)


class PotionShopper:
    def __init__(self, quester, enabled: bool = True):
        self.q = quester
        self.client = quester.client
        self.enabled = enabled
        self._retry_at = 0.0
        self._travel_failed = False

    async def tick(self, team_return: bool = False) -> bool:
        """Call while free, outside team dungeons. True if it made the trip."""
        if not self.enabled or time.monotonic() < self._retry_at:
            return False
        try:
            before = await self.client.stats.potion_charge()
            most = await self.client.stats.potion_max()
        except Exception:
            return False
        if not should_buy_potions(before, most, team_return=team_return):
            return False
        self._travel_failed = False
        if not await self.trip():
            wait = TRAVEL_RETRY_MINUTES if self._travel_failed else RETRY_MINUTES
            self._retry_at = time.monotonic() + wait * 60
            return True
        after = await self.client.stats.potion_charge()
        if after <= before:
            logger.warning(f"potions: none bought (no gold?); trying again in {RETRY_MINUTES:.0f} min")
            self._retry_at = time.monotonic() + RETRY_MINUTES * 60
        else:
            logger.success(f"potions: refilled to {after:.0f}/{most:.0f}")
        return True

    async def trip(self) -> bool:
        from .trainer import home_to_ravenwood

        zone = await self.client.zone_name() or ""
        marked = False
        if zone != COMMONS:
            # (Not from Wizard City itself: after a pet trip the Pet Park's
            # mark replaced the one in Khrysalis the pet trip came from.)
            if not zone.startswith("WizardCity/"):
                marked = await self.q._mark_here("travel", require_clear=False)
            logger.info(f"potions: below {MIN_POTIONS}; going to Hilda Brewer in the Commons")
            if not zone.startswith("WizardCity/") or "/interiors/" in zone.lower():
                if not await home_to_ravenwood(self.q):
                    self._travel_failed = True
                    return False
            await self.q.go_to_zone(COMMONS)
            await wait_for_loading(self.client)
            if await self.client.zone_name() != COMMONS:
                logger.warning("potions: could not reach the Commons")
                self._travel_failed = True
                return False
        ok = await self.buy()
        if marked and self.q._mark and await is_free(self.client):
            await self.q._recall(self.q._mark.zone)
        return ok

    async def _shop_numbers(self) -> tuple[int, int | None]:
        """(the selected purchase's cost, the gold held) as the shop shows them."""
        async def number(name: str) -> int | None:
            digits = "".join(ch for ch in (await ui.named_text(self.client, name) or "") if ch.isdigit())
            return int(digits) if digits else None

        return (await number("costNumber") or 0), await number("totalCoins")

    async def _buy_one_by_one(self, root) -> bool:
        bought = 0
        for _ in range(12):
            await _click(self.client, "fillonepotion")
            await asyncio.sleep(0.4)
            cost, coins = await self._shop_numbers()
            if not cost or coins is None or cost > coins:
                break
            await _click(self.client, "buyAction")
            await asyncio.sleep(0.6)
            await ui.confirm_modal(self.client)
            await asyncio.sleep(0.6)
            bought += 1
        logger.info(f"potions: bought {bought} one at a time (the full set was out of reach)")
        for _ in range(10):
            if not await _visible(root, "fillallpotions"):
                break
            await _click(self.client, "main", "exit")
            await asyncio.sleep(0.3)
        return True

    async def _dump(self, stage: str) -> None:
        try:
            lines = await ui.dump_tree(self.client.root_window, max_depth=8)
            from pathlib import Path

            Path("state").mkdir(exist_ok=True)
            Path("state", f"potion_shop_{stage}.txt").write_text(chr(10).join(lines), encoding="utf-8")
        except Exception as exc:
            logger.debug(f"potion shop dump failed: {exc}")

    async def buy(self) -> bool:
        root = self.client.root_window
        for _ in range(SHOP_TRIES):
            if await _visible(root, "fillallpotions"):
                break
            if not await ui.is_visible(self.client, ui.NPC_RANGE):
                await self.client.teleport(HILDA)
                await asyncio.sleep(1.5)
            await self.client.send_key(Keycode.X, 0.1)
            await _wait_for(lambda: _visible(root, "fillallpotions"), 2.0)
        else:
            logger.warning("potions: Hilda Brewer's shop didn't open")
            return False
        await _click(self.client, "fillallpotions")
        await asyncio.sleep(0.3)
        await self._dump("before_buy")
        cost, coins = await self._shop_numbers()
        if cost and coins is not None and cost > coins:
            # Not enough gold for them all (18,480 for the set against 16,877):
            # one potion at a time, while the gold lasts.
            return await self._buy_one_by_one(root)
        await _click(self.client, "buyAction")
        await asyncio.sleep(0.5)
        box = await ui.modal_box(self.client)
        if box is not None:
            # (What the game says: "not enough gold", "are you sure"...: the
            # trips bought nothing for hours and the log only said "no gold?".)
            logger.info(f"potions: the shop says {(await ui.modal_text(box))!r}")
        await self._dump("after_buy")
        await ui.confirm_modal(self.client)
        for _ in range(10):
            if not await _visible(root, "fillallpotions"):
                break
            await _click(self.client, "main", "exit")
            await asyncio.sleep(0.3)
        return True
