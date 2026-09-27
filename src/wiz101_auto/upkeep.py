"""Out-of-combat maintenance: potions, wisps, dialogue, stray popups."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker import Keycode

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
        sprinter = client  # SprintyClient (bot.new_handler)
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


async def collect_wisps(client, cfg: UpkeepConfig, *, mana_too: bool = False, limit: int = 6) -> int:
    """Teleport onto nearby wisps that aren't close to mobs. Returns how many were taken."""
    try:
        wisps = await client.get_health_wisps()
        if mana_too:
            wisps += await client.get_mana_wisps()
        safe = await client.find_safe_entities_from(wisps, safe_distance=cfg.wisp_safe_distance)
        if not safe:
            return 0
        me = await client.body.position()
        spots = sorted([await w.location() for w in safe], key=lambda p: p.distance(me))[:limit]
        for spot in spots:
            await client.teleport(spot)
            await asyncio.sleep(0.6)
        return len(spots)
    except Exception as exc:
        logger.debug(f"wisp collection failed: {exc}")
        return 0


async def move_to_safety(client, safe_distance: float = 1500.0) -> bool:
    """If a mob is close, teleport to the nearest spot with no mob around."""
    try:
        me = await client.body.position()
        mobs = [await m.location() for m in await client.get_mobs()]
        if all(p.distance(me) > safe_distance for p in mobs):
            return False
        candidates = await client.find_safe_entities_from(
            await client.get_base_entity_list(), safe_distance=safe_distance
        )
        if not candidates:
            return False
        spot = min([await c.location() for c in candidates], key=lambda p: p.distance(me))
        logger.info("moving away from mobs to rest")
        await client.teleport(spot)
        await asyncio.sleep(1.0)
        return True
    except Exception as exc:
        logger.debug(f"could not find a safe spot: {exc}")
        return False


async def recover(client, cfg: UpkeepConfig, controller) -> bool:
    """Make sure the wizard is healthy before engaging anything.

    Returns True when it's fine to carry on questing, False if something
    (a fight, dialogue, loading) interrupted the recovery.
    """
    hp, mana = await health_mana(client)
    if not cfg.needs_recovery(hp):
        return True
    logger.info(f"health {hp:.0%} is below {cfg.min_health_to_fight:.0%}; recovering before going on")

    loop = asyncio.get_running_loop()
    started = loop.time()
    last_report = started
    rested = False
    while True:
        await controller.checkpoint()
        if not await is_free(client):
            return False
        hp, mana = await health_mana(client)
        if hp >= cfg.rest_until_health:
            logger.success(f"recovered to {hp:.0%} health")
            return True

        if cfg.use_potions and hp < cfg.potion_health_ratio and await client.stats.potion_charge() >= 1.0:
            logger.info(f"drinking potion (hp {hp:.0%})")
            await ui.click(client, ui.POTION_BUTTON)
            await asyncio.sleep(1.5)
            continue

        if cfg.collect_wisps and await collect_wisps(client, cfg, mana_too=mana < 0.5):
            continue

        if not rested:
            await move_to_safety(client)
            rested = True

        elapsed = loop.time() - started
        if elapsed > cfg.rest_max_minutes * 60:
            if hp >= cfg.min_health_to_fight:
                return True
            controller.stop(
                f"could not recover health ({hp:.0%}) within {cfg.rest_max_minutes:g} min: "
                "no potions and no safe wisps nearby"
            )
            return False
        if loop.time() - last_report > 60:
            logger.info(f"resting: health {hp:.0%}, waiting for regeneration or wisps to respawn")
            last_report = loop.time()
        await asyncio.sleep(5)


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
