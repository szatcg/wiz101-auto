"""Out-of-combat maintenance: potions, wisps, dialogue, stray popups."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker import Keycode
from wizwalker.extensions.wizsprinter import SprintyClient

from . import ui
from .config import QuestConfig, UpkeepConfig


async def is_free(client) -> bool:
    """Not loading, not fighting and not in a dialogue."""
    try:
        return not (
            await client.is_loading()
            or await client.in_battle()
            or await ui.is_visible(client, ui.ADVANCE_DIALOG)
        )
    except Exception:
        return False


async def wait_until_free(client, timeout: float = 60.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await is_free(client):
            return True
        await asyncio.sleep(0.25)
    return False


async def wait_for_loading(client, appear_timeout: float = 2.0):
    """Give a loading screen a moment to appear, then wait for it to finish."""
    loop = asyncio.get_running_loop()
    end = loop.time() + appear_timeout
    while loop.time() < end and not await client.is_loading():
        await asyncio.sleep(0.1)
    while await client.is_loading():
        await asyncio.sleep(0.2)


async def health_mana(client) -> tuple[float, float]:
    hp, max_hp = await client.stats.current_hitpoints(), await client.stats.max_hitpoints()
    mana, max_mana = await client.stats.current_mana(), await client.stats.max_mana()
    return (hp / max_hp if max_hp else 1.0), (mana / max_mana if max_mana else 1.0)


async def maintain(client, cfg: UpkeepConfig):
    hp, mana = await health_mana(client)

    if cfg.collect_wisps and hp < cfg.wisp_health_ratio:
        sprinter = SprintyClient(client)
        try:
            wisps = await sprinter.get_health_wisps()
            if mana < 0.5:
                wisps += await sprinter.get_mana_wisps()
            for wisp in await sprinter.find_safe_entities_from(wisps):
                await client.teleport(await wisp.location())
                await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug(f"wisp collection failed: {exc}")
        hp, mana = await health_mana(client)

    if cfg.use_potions and (hp < cfg.potion_health_ratio or mana < cfg.potion_mana_ratio):
        if await client.stats.potion_charge() >= 1.0:
            logger.info(f"drinking potion (hp {hp:.0%}, mana {mana:.0%})")
            await ui.click(client, ui.POTION_BUTTON)
            await asyncio.sleep(1.0)
        elif hp < 0.25:
            logger.warning("low health and no potions; the next fight may be lost")


async def clear_popups(client):
    await ui.click(client, ui.CANCEL_CHEST_REROLL)
    if await ui.is_visible(client, ui.MISSING_AREA_RETRY):
        await ui.click(client, ui.MISSING_AREA_RETRY)


async def dialogue_loop(client, cfg: QuestConfig, controller):
    """Advance NPC dialogue as it appears. Runs for the whole session."""
    while not controller.stopped.is_set():
        try:
            if not controller.paused and await ui.is_visible(client, ui.ADVANCE_DIALOG):
                if not cfg.accept_side_quests and await ui.is_visible(client, ui.DECLINE_QUEST):
                    text = await ui.text_at(client, ui.DIALOG_TEXT)
                    logger.info(f"declining side quest: {text[:80]}")
                    await client.send_key(Keycode.ESC)
                    await asyncio.sleep(0.1)
                    await client.send_key(Keycode.ESC)
                else:
                    await client.send_key(Keycode.SPACEBAR)
        except Exception as exc:
            logger.trace(f"dialogue loop: {exc}")
        await asyncio.sleep(0.15)
