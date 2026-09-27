"""Wires everything together and runs the bot against one game client."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.extensions.wizsprinter import SprintyClient

from .combat.fighter import Fighter
from .config import Config
from .quest import Quester
from .safety import BotStopped, Controller
from .upkeep import dialogue_loop, is_free, maintain


async def connect(handler: ClientHandler):
    clients = handler.get_new_clients()
    if not clients:
        raise SystemExit("No Wizard101 window found. Start the game and log in to your wizard first.")
    focused = handler.get_foreground_client()
    client = focused or clients[0]
    if len(clients) > 1:
        logger.info(f"{len(clients)} game windows found; using the {'focused' if focused else 'first'} one")
    logger.info("activating hooks (can take a few seconds; move your wizard a step if it stalls)")
    await client.activate_hooks()
    return client


async def combat_loop(client, fighter: Fighter, cfg: Config, controller: Controller):
    while not controller.stopped.is_set():
        await controller.checkpoint()
        if await client.in_battle():
            await fighter.handle_combat()
            await asyncio.sleep(1.5)
            hp = await client.stats.current_hitpoints()
            if hp <= 1:
                controller.record_death()
            elif await is_free(client):
                await maintain(client, cfg.upkeep)
        await asyncio.sleep(0.3)


async def quest_loop(quester: Quester, controller: Controller):
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            await quester.step()
        except BotStopped:
            raise
        except Exception as exc:
            logger.opt(exception=exc).warning("quest step failed; retrying")
            await asyncio.sleep(2.0)
        await asyncio.sleep(0.5)


async def farm_loop(client, cfg: Config, controller: Controller):
    """Stay in the current area and fight the nearest mob, repeatedly."""
    sprinter = SprintyClient(client)
    while not controller.stopped.is_set():
        await controller.checkpoint()
        if await is_free(client):
            await maintain(client, cfg.upkeep)
            try:
                await sprinter.tp_to_closest_mob()
            except Exception as exc:
                logger.debug(f"no mob nearby: {exc}")
        await asyncio.sleep(cfg.farm_seconds_between_fights)


async def run(cfg: Config):
    s = cfg.safety
    controller = Controller(s.stop_key, s.pause_key, s.max_hours, s.max_deaths)
    logger.info(f"mode={cfg.mode}; {s.stop_key}=stop, {s.pause_key}=pause/resume")

    handler = ClientHandler()
    client = None
    tasks: list[asyncio.Task] = []
    try:
        client = await connect(handler)
        if s.mouseless:
            await client.mouse_handler.activate_mouseless()

        c = cfg.combat
        fighter = Fighter(client, c.strategy, max_discards=c.max_discards, flee_below=c.flee_below)
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(combat_loop(client, fighter, cfg, controller), name="combat"),
            asyncio.create_task(dialogue_loop(client, cfg.quest, controller), name="dialogue"),
        ]
        quester = None
        if cfg.mode == "quest":
            quester = Quester(client, cfg.quest, controller)
            tasks.append(asyncio.create_task(quest_loop(quester, controller), name="quest"))
        elif cfg.mode == "farm":
            tasks.append(asyncio.create_task(farm_loop(client, cfg, controller), name="farm"))

        stop_waiter = asyncio.create_task(controller.stopped.wait())
        done, _ = await asyncio.wait([*tasks, stop_waiter], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is not stop_waiter and t.exception() and not isinstance(t.exception(), BotStopped):
                logger.opt(exception=t.exception()).error(f"{t.get_name()} task crashed")
                controller.stop(f"{t.get_name()} crashed")
        controller.stop(controller.stop_reason or "finished")

        summary = f"fights: {fighter.fights}, deaths: {controller.deaths}"
        if quester:
            summary += f", objectives completed: {quester.objectives_completed}"
        logger.info(f"session over ({controller.stop_reason}). {summary}")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if client and s.mouseless:
            try:
                await client.mouse_handler.deactivate_mouseless()
            except Exception:
                pass
        await handler.close()
