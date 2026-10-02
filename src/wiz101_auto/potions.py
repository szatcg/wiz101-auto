"""Potion refills: out of potions, buy a full set from Hilda Brewer.

The bot drank the potions it had and then went on without (a low-health
start lost fights). At a free moment out of dungeons, with no potion left:
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
SHOP_TRIES = 8


class PotionShopper:
    def __init__(self, quester, enabled: bool = True):
        self.q = quester
        self.client = quester.client
        self.enabled = enabled
        self._retry_at = 0.0

    async def tick(self) -> bool:
        """Call while free, outside dungeons. True if it made the trip."""
        if not self.enabled or time.monotonic() < self._retry_at:
            return False
        try:
            if await self.client.stats.potion_charge() >= 1.0:
                return False
            most = await self.client.stats.potion_max()
        except Exception:
            return False
        if most < 1:
            return False
        before = await self.client.stats.potion_charge()
        if not await self.trip():
            self._retry_at = time.monotonic() + RETRY_MINUTES * 60
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
            marked = await self.q._mark_here("travel", require_clear=False)
            logger.info("potions: out of potions; going to Hilda Brewer in the Commons")
            if not zone.startswith("WizardCity/") or "/interiors/" in zone.lower():
                if not await home_to_ravenwood(self.q):
                    return False
            await self.q.go_to_zone(COMMONS)
            await wait_for_loading(self.client)
            if await self.client.zone_name() != COMMONS:
                logger.warning("potions: could not reach the Commons")
                return False
        ok = await self.buy()
        if marked and self.q._mark and await is_free(self.client):
            await self.q._recall(self.q._mark.zone)
        return ok

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
        await _click(self.client, "buyAction")
        await asyncio.sleep(0.5)
        await ui.confirm_modal(self.client)
        for _ in range(10):
            if not await _visible(root, "fillallpotions"):
                break
            await _click(self.client, "main", "exit")
            await asyncio.sleep(0.3)
        return True
