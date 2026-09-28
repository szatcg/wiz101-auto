"""Wires everything together and runs the bot against one game client."""

from __future__ import annotations

import asyncio
import contextlib
import time

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed
from wizwalker.extensions.wizsprinter import SprintyClient

from .bossfarm import BossFarmer
from .combat.fighter import Fighter
from .config import Config
from .gear import GearManager
from .progression import Progression
from .quest import Quester
from .safety import BotStopped, Controller
from .trainer import SpellTrainer
from .upkeep import DialoguePolicy, dialogue_loop, is_free, maintain, recover, scan_wisps
from .watchdog import Watchdog

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
    _click_left_of_center(client)
    return client


def _client_origin(hwnd: int, awareness: int):
    """The client area's top-left in screen coordinates as a thread with the
    given DPI awareness context sees it (-1 unaware, -4 per-monitor v2)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pt = wintypes.POINT(0, 0)
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(awareness))
    try:
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    return pt.x, pt.y


def dpi_click_offset(hwnd: int) -> tuple[int, int]:
    """What to add to a client position so the game sees the click there.

    WizWalker makes the bot DPI-aware, so the cursor position it writes is in
    real pixels; the game is DPI-unaware and turns it back into client
    coordinates with its *scaled* origin. On a monitor not at 100% (the game
    sat on a 125% monitor left of a 100% main one) the two origins differ:
    (-2384, 120) real vs (-2419, 96) scaled, so the game saw every click 35px
    right and 24px down. That missed thin buttons (Pass, Flee, message-box
    Yes/No: 41px tall) and made cards need a click "left of center". The fix is
    the scaled origin minus the real one, measured per click (windows move)."""
    try:
        sx, sy = _client_origin(hwnd, -1)
        rx, ry = _client_origin(hwnd, -4)
    except Exception:
        return 0, 0
    return sx - rx, sy - ry


def _click_left_of_center(client):
    """Aim clicks where the game will see them: shift every cursor position by
    the DPI offset (see dpi_click_offset), and click windows at their center."""
    mouse = client.mouse_handler
    set_position = mouse.set_mouse_position
    offset_logged: list = []

    async def set_mouse_position(x, y, *args, **kwargs):
        if x >= 0 and y >= 0:  # (-100, -100) parks the cursor outside the window
            try:
                dx, dy = dpi_click_offset(client.window_handle)
            except Exception:
                dx, dy = 0, 0
            if (dx, dy) != (0, 0) and offset_logged != [(dx, dy)]:
                offset_logged[:] = [(dx, dy)]
                logger.info(f"correcting clicks by ({dx}, {dy}) px for display scaling")
            x, y = x + dx, y + dy
        return await set_position(x, y, *args, **kwargs)

    async def click_window(window, **kwargs):
        r = await window.scale_to_client()
        await mouse.click(int((r.x1 + r.x2) / 2), int((r.y1 + r.y2) / 2), **kwargs)

    mouse.set_mouse_position = set_mouse_position
    mouse.click_window = click_window


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
                await scan_wisps(client)
                await maintain(client, cfg.upkeep)
        await asyncio.sleep(0.3)


async def status_loop(client, controller: Controller, fighter: Fighter, quester, watchdog):
    """Publish a heartbeat to state/status.json for `wiz101-auto status`."""
    from .service import write_status

    started = time.time()
    while not controller.stopped.is_set():
        info = {"state": "paused" if controller.paused else "running", "uptime_s": int(time.time() - started)}
        try:
            info.update(
                zone=await client.zone_name(),
                level=await client.stats.reference_level(),
                health=f"{await client.stats.current_hitpoints()}/{await client.stats.max_hitpoints()}",
                in_battle=await client.in_battle(),
            )
        except Exception as exc:
            info["read_error"] = repr(exc)
        info.update(fights=fighter.fights, deaths=controller.deaths)
        if quester:
            info.update(
                objective=quester._last_progress[0],
                objective_age_s=int(time.monotonic() - quester._last_progress_time),
                objectives_completed=quester.objectives_completed,
            )
        if watchdog:
            info["watchdog_nudges"] = watchdog.nudges
        write_status(**info)
        await asyncio.sleep(5)


async def quest_loop(quester: Quester, controller: Controller):
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            await quester.run_step()
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
        dialogue = DialoguePolicy()
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(combat_loop(client, fighter, cfg, controller), name="combat"),
            asyncio.create_task(dialogue_loop(client, cfg.quest, controller, dialogue), name="dialogue"),
        ]
        progression = Progression(client, cfg.progression)
        await progression.start()
        quester = None
        if cfg.mode == "quest":
            quester = Quester(client, cfg.quest, controller, progression, cfg.upkeep, dialogue)
            if cfg.gear_checks:
                quester.gear = GearManager(client, progression.school or "")
            else:
                logger.info("gear checks are off (gear_checks: false)")
            if cfg.progression.enabled:
                quester.trainer = SpellTrainer(quester, progression, cfg.progression.train_levels)
            quester.fighter = fighter
            if cfg.quest.flee_unneeded_fights:
                fighter.unneeded_fight = quester.unneeded_fight
            tasks.append(asyncio.create_task(quest_loop(quester, controller), name="quest"))
        watchdog = None
        if s.stall_seconds > 0 and cfg.mode in ("quest", "farm"):
            watchdog = Watchdog(
                client,
                controller,
                quester,
                stall_seconds=s.stall_seconds,
                battle_stall_seconds=s.battle_stall_seconds,
            )
            tasks.append(asyncio.create_task(watchdog.run(), name="watchdog"))
        if cfg.mode == "boss":
            boss_quester = Quester(client, cfg.quest, controller, progression, cfg.upkeep, dialogue)
            farmer = BossFarmer(boss_quester, cfg.boss_farm, cfg.upkeep, controller)
            tasks.append(asyncio.create_task(farmer.run(), name="boss"))
        if cfg.mode == "farm":
            tasks.append(asyncio.create_task(farm_loop(client, cfg, controller, progression), name="farm"))

        tasks.append(
            asyncio.create_task(status_loop(client, controller, fighter, quester, watchdog), name="status")
        )
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
        from .service import write_status

        write_status(state="stopped", reason=controller.stop_reason, summary=summary)
        return controller.stop_reason
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await stack.aclose()
        except Exception:
            pass
        await close_handler(handler)
