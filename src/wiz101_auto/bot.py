"""Wires everything together and runs the bot against one game client."""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed
from wizwalker.extensions.wizsprinter import SprintyClient

from .combat.fighter import Fighter
from .config import Config
from .progression import Progression
from .quest import Quester
from .safety import BotStopped, Controller
from .upkeep import dialogue_loop, is_free, maintain, recover

HOOK_TIMEOUT = 90
DEATH_HEALTH_RATIO = 0.1


def new_handler() -> ClientHandler:
    # Clients are created as SprintyClients so they also have WizSprinter's
    # entity helpers (closest mob, wisps, safe spots).
    return ClientHandler(client_cls=SprintyClient)


async def close_handler(handler: ClientHandler):
    """Unhook from the game. Each client is closed separately and failures are
    logged, so one bad unhook doesn't leave the rest of the game patched."""
    for client in list(handler.clients):
        try:
            await client.close()
        except Exception as exc:
            logger.opt(exception=exc).error(
                "unhooking failed; restart Wizard101 before running the bot again"
            )


async def connect(handler: ClientHandler):
    clients = handler.get_new_clients()
    if not clients:
        raise SystemExit("No Wizard101 window found. Start the game and log in to your wizard first.")
    focused = handler.get_foreground_client()
    client = focused or clients[0]
    if len(clients) > 1:
        logger.info(f"{len(clients)} game windows found; using the {'focused' if focused else 'first'} one")
    logger.info("activating hooks (can take a few seconds; move your wizard a step if it stalls)")
    try:
        await asyncio.wait_for(client.activate_hooks(), timeout=HOOK_TIMEOUT)
    except PatternFailed:
        await close_handler(handler)
        raise SystemExit(
            "\nCould not hook into the game: its memory still holds changes from an earlier bot "
            "session that did not shut down cleanly.\n"
            "FIX: fully close Wizard101 (exit to desktop), start it again, log in, then rerun.\n"
            "To avoid this, stop the bot with Ctrl+Shift+Q (or Ctrl+C) instead of closing its window."
        ) from None
    except TimeoutError:
        raise SystemExit(
            f"Hooks did not activate within {HOOK_TIMEOUT}s. Make sure your wizard is loaded into the "
            "world (not the login or character screen), walk a step while it starts, and try running "
            "as Administrator."
        ) from None
    logger.success("connected to the game")
    return client


async def combat_loop(client, fighter: Fighter, cfg: Config, controller: Controller):
    while not controller.stopped.is_set():
        await controller.checkpoint()
        if await client.in_battle():
            await fighter.handle_combat()
            await asyncio.sleep(1.5)
            hp = await client.stats.current_hitpoints()
            max_hp = await client.stats.max_hitpoints()
            if hp <= 1 or (max_hp and hp / max_hp < DEATH_HEALTH_RATIO):
                # Losing a fight sends you back with a sliver of health.
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


async def farm_loop(client, cfg: Config, controller: Controller, progression: Progression):
    """Stay in the current area and fight the nearest mob, repeatedly."""
    sprinter = client  # a SprintyClient, see new_handler()
    while not controller.stopped.is_set():
        await controller.checkpoint()
        if await is_free(client):
            await maintain(client, cfg.upkeep)
            if not await recover(client, cfg.upkeep, controller):
                continue
            await progression.tick()
            try:
                await sprinter.tp_to_closest_mob()
            except Exception as exc:
                logger.debug(f"no mob nearby: {exc}")
        await asyncio.sleep(cfg.farm_seconds_between_fights)


async def run(cfg: Config):
    s = cfg.safety
    controller = Controller(s.stop_key, s.pause_key, s.max_hours, s.max_deaths)
    logger.info(f"mode={cfg.mode}; {s.stop_key}=stop, {s.pause_key}=pause/resume")

    handler = new_handler()
    client = None
    tasks: list[asyncio.Task] = []
    stack = contextlib.AsyncExitStack()
    try:
        client = await connect(handler)
        if s.mouseless:
            # Managed mode: helpers like DeckBuilder nest `async with mouse_handler`
            # and must not switch mouseless off underneath us.
            await stack.enter_async_context(client.mouse_handler)

        c = cfg.combat
        fighter = Fighter(client, c.strategy, max_discards=c.max_discards, flee_below=c.flee_below)
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(combat_loop(client, fighter, cfg, controller), name="combat"),
            asyncio.create_task(dialogue_loop(client, cfg.quest, controller), name="dialogue"),
        ]
        progression = Progression(client, cfg.progression)
        await progression.start()
        quester = None
        if cfg.mode == "quest":
            quester = Quester(client, cfg.quest, controller, progression, cfg.upkeep)
            tasks.append(asyncio.create_task(quest_loop(quester, controller), name="quest"))
        elif cfg.mode == "farm":
            tasks.append(asyncio.create_task(farm_loop(client, cfg, controller, progression), name="farm"))

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
        try:
            await stack.aclose()
        except Exception:
            pass
        await close_handler(handler)
