"""Wires everything together and runs the bot against one game client."""

from __future__ import annotations

import asyncio
import contextlib
import time

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed
from wizwalker.extensions.wizsprinter import SprintyClient

from . import lifetime
from .bossfarm import BossFarmer
from .combat.fighter import Fighter
from .config import Config
from .dungeon_heal import DungeonHealer
from .gear import GearManager
from .petdance import PetDancer
from .potions import PotionShopper
from .progression import Progression
from .quest import Quester
from .safety import BotStopped, Controller
from .trainer import SpellTrainer
from .upkeep import (
    DialoguePolicy,
    dialogue_loop,
    is_free,
    maintain,
    max_health,
    move_to_safety,
    recover,
    scan_wisps,
)
from .watchdog import Watchdog

HOOK_TIMEOUT = 90
DEATH_HEALTH_RATIO = 0.1
DEFEAT_MOVE_DISTANCE = 1500.0  # a defeat puts you back at the zone's start (or another zone)


def new_handler() -> ClientHandler:
    # Clients are created as SprintyClients so they also have WizSprinter's
    # entity helpers (closest mob, wisps, safe spots).
    return ClientHandler(client_cls=SprintyClient)


def release_mouse_buttons(client) -> None:
    """Send the game a left and right button-up. A click is button-down, a
    short wait, button-up: a stop landing in between left the game thinking
    the button was still held, and it ignored the player's own clicks."""
    import ctypes

    try:
        send = ctypes.windll.user32.SendMessageW
        send(client.window_handle, 0x0202, 0, 0)  # WM_LBUTTONUP
        send(client.window_handle, 0x0205, 0, 0)  # WM_RBUTTONUP
    except Exception as exc:
        logger.debug(f"could not release the mouse buttons: {exc!r}")


async def close_handler(handler: ClientHandler):
    """Unhook from the game. Each client is closed separately and failures are
    logged, so one bad unhook doesn't leave the rest of the game patched."""
    for client in list(handler.clients):
        release_mouse_buttons(client)
        try:
            await client.close()
        except Exception as exc:
            logger.opt(exception=exc).error(
                "unhooking failed; restart Wizard101 before running the bot again"
            )


WORLD_HOOKS = ("player_struct", "player_stat_struct", "current_client", "current_render_context")


async def connect(handler: ClientHandler):
    from .gamerestart import bot_game_pid, remember_game

    clients = handler.get_new_clients()
    if not clients:
        raise SystemExit("No Wizard101 window found. Start the game and log in to your wizard first.")
    # The player may play another copy (another account): only the bot's own
    # game is hooked (hooks patch the game's memory), never the focused one.
    pid = bot_game_pid()
    mine = [c for c in clients if c.process_id == pid]
    if mine:
        client = mine[0]
    elif len(clients) == 1:
        client = clients[0]
    else:
        raise SystemExit(
            f"{len(clients)} Wizard101 windows are open and none is known as the bot's "
            "(state/game_client.json).\nClose the other copy, or put the bot's game process id in "
            "state/game_client.json, then start again.")
    if len(clients) > 1:
        logger.info(f"{len(clients)} game windows open; using the bot's own (process {client.process_id})")
    remember_game(client.process_id, client.window_handle)
    logger.info("activating hooks (can take a few seconds; move your wizard a step if it stalls)")
    try:
        hooks = client.hook_handler
        await hooks.activate_all_hooks(wait_for_ready=False)
        _click_left_of_center(client)
        # The window hook fills in at character select too (the player hooks
        # only in the world): a relog cut short left it there, and every
        # restart then timed out. Press Play first, then wait for the rest.
        await asyncio.wait_for(hooks._wait_for_value(hooks._base_addrs["current_root_window"], None),
                               timeout=HOOK_TIMEOUT)
        from .relog import at_character_select, play_from_character_select
        from .upkeep import reconnect_if_asked

        async with client.mouse_handler:
            if await reconnect_if_asked(client):  # "Problem: Unable to find your zone" (lost connection)
                await asyncio.sleep(3.0)
        if await at_character_select(client):
            async with client.mouse_handler:
                await play_from_character_select(client)
        await asyncio.wait_for(
            asyncio.gather(*(
                hooks._wait_for_value(hooks._base_addrs[name], None)
                for name in WORLD_HOOKS
            )),
            timeout=HOOK_TIMEOUT,
        )
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
    from .safe_teleport import install

    install(client)  # every teleport lands clear of enemies (unless meant to start a fight)
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


def window_on_screen(hwnd: int) -> float:
    """Share of the game window's area that lies on some monitor (1.0 if it
    can't be read). The window once sat below the left monitor (3% on
    screen): out of the player's sight."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return 1.0
        area = max(1, (r.right - r.left) * (r.bottom - r.top))
        monitors: list[tuple[int, int, int, int]] = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def add(_h, _dc, m, _lp):
            monitors.append((m.contents.left, m.contents.top, m.contents.right, m.contents.bottom))
            return 1

        user32.EnumDisplayMonitors(None, None, proc(add), 0)
        covered = 0
        for left, top, right, bottom in monitors:
            w = min(r.right, right) - max(r.left, left)
            h = min(r.bottom, bottom) - max(r.top, top)
            if w > 0 and h > 0:
                covered += w * h
        return min(1.0, covered / area)
    except Exception:
        return 1.0


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


def _team_zone(zone: str) -> bool:
    from .teamup import is_team_up_zone

    return is_team_up_zone(zone or "")


async def combat_loop(client, fighter: Fighter, cfg: Config, controller: Controller, adapter=None):
    while not controller.stopped.is_set():
        await controller.checkpoint()
        if await client.in_battle():
            fight_zone = await client.zone_name() or ""  # a defeat moves us elsewhere
            fight_spot = await client.body.position()
            await fighter.handle_combat()
            await asyncio.sleep(1.5)
            hp = await client.stats.current_hitpoints()
            max_hp = await max_health(client)
            moved = await client.zone_name() != fight_zone or (
                (await client.body.position()).distance(fight_spot) > DEFEAT_MOVE_DISTANCE
            )
            # (Fleeing moves us away too, but isn't a defeat.)
            if not fighter.fled and (hp <= 1 or (max_hp and hp / max_hp < DEATH_HEALTH_RATIO and moved)):
                # Losing a fight sends you back (elsewhere) with a sliver of
                # health; winning on a sliver leaves you standing where you fought.
                controller.record_death(fight_zone)
                if adapter is not None and not _team_zone(fight_zone):
                    adapter.on_defeat(list(fighter.last_enemy_names), fighter.last_bosses)
            elif await is_free(client):
                if adapter is not None and not fighter.fled:
                    adapter.on_win(list(fighter.last_enemy_names))
                await scan_wisps(client)
                await maintain(client, cfg.upkeep)
        await asyncio.sleep(0.3)


async def status_loop(client, controller: Controller, fighter: Fighter, quester, watchdog):
    """Publish a heartbeat to state/status.json for `wiz101-auto status`."""
    from . import gamerestart
    from .service import write_status

    started = time.time()
    last_offscreen_alert = 0.0
    loading_since = hung_since = 0.0  # a loading screen / "Not Responding" window since
    while not controller.stopped.is_set():
        # A frozen game (a loading screen for minutes, the window not
        # responding): the supervisor restarts it and logs back in.
        now = time.monotonic()
        try:
            loading = await client.is_loading()
        except Exception:
            loading = False
        loading_since = (loading_since or now) if loading else 0.0
        hung_since = (hung_since or now) if gamerestart.window_hung(client.window_handle) else 0.0
        frozen = ""
        if loading_since and now - loading_since > gamerestart.FREEZE_SECONDS:
            frozen = f"a loading screen for {now - loading_since:.0f}s"
        elif hung_since and now - hung_since > gamerestart.HUNG_SECONDS:
            frozen = f"the game window not responding for {now - hung_since:.0f}s"
        if frozen:
            logger.warning(f"ALERT: the game looks frozen ({frozen}); stopping for a game restart")
            gamerestart.request(f"game frozen: {frozen}")
            controller.stop(f"game frozen ({frozen})")
            break
        info = {"state": "paused" if controller.paused else "running", "uptime_s": int(time.time() - started)}
        shown = window_on_screen(client.window_handle)
        info["window_on_screen"] = round(shown, 2)
        if shown < 0.5 and time.monotonic() - last_offscreen_alert > 600:
            last_offscreen_alert = time.monotonic()
            logger.warning(f"ALERT: the game window is {100 - shown * 100:.0f}% off screen (you may not see "
                           "it); the bot still plays, but move it back onto a monitor to watch it")
        try:
            info.update(
                zone=await client.zone_name(),
                level=await client.stats.reference_level(),
                health=f"{await client.stats.current_hitpoints()}/{await max_health(client)}",
                in_battle=await client.in_battle(),
            )
        except Exception as exc:
            info["read_error"] = repr(exc)
        info.update(fights=fighter.fights, deaths=controller.deaths)
        info["deaths_total"] = lifetime.load().get("deaths", 0)
        from .farm import Farm

        farm = Farm.load()
        if farm.active or farm.runs:
            from .farm import load_looted, target_status

            info["farm"] = {
                "dungeon": farm.name, "runs": farm.runs, "active": farm.active,
                "targets": target_status(farm.name, load_looted()),
            }
        if quester:
            info.update(
                objective=quester._last_progress[0],
                objective_age_s=int(time.monotonic() - quester._last_progress_time),
                objectives_completed=quester.objectives_completed,
                activity="grinding for experience" if quester._grinding else "questing",
            )
        if watchdog:
            info["watchdog_nudges"] = watchdog.nudges
        write_status(**info)
        await asyncio.sleep(5)


STUCK_TIMEOUTS_BEFORE_RELOG = 3  # quest steps failing in a row on WizWalker's should_update


async def quest_loop(quester: Quester, controller: Controller):
    stuck = 0  # steps in a row that failed because the game ignored our moves
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            await quester.run_step()
            stuck = 0
        except BotStopped:
            raise
        except Exception as exc:
            logger.opt(exception=exc).warning("quest step failed; retrying")
            if "should_update" in str(exc):
                # Standing on a duel circle whose fight never started, the game
                # stopped taking moves: log out to character select and back.
                stuck += 1
                if stuck >= STUCK_TIMEOUTS_BEFORE_RELOG and not await quester.client.in_battle():
                    from .relog import relog

                    stuck = 0
                    controller.allow_idle(180)
                    try:
                        if not await relog(quester.client):
                            logger.warning("ALERT: main quest stuck: the wizard can't move; relog failed")
                    finally:
                        controller.end_idle()
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
    controller = Controller(s.stop_key, s.pause_key, s.max_hours)
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
        try:
            # The simulator's enemies and hit rates, from every fight logged so far (~0.1 s).
            from pathlib import Path as _Path

            from .combat.calibrate import write_stats

            write_stats([_Path("activity.log")])
        except Exception as exc:
            logger.debug(f"enemy stats not refreshed: {exc!r}")
        fighter = Fighter(client, c.strategy, max_discards=c.max_discards, flee_below=c.flee_below,
                          rollouts=c.rollouts)
        dialogue = DialoguePolicy()
        adapter = None
        if c.adapt_deck:
            from .deck_adapt import DeckAdapter

            adapter = DeckAdapter()  # a boss deck after a loss, the general deck after the win
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(combat_loop(client, fighter, cfg, controller, adapter), name="combat"),
            asyncio.create_task(dialogue_loop(client, cfg.quest, controller, dialogue), name="dialogue"),
        ]
        progression = Progression(client, cfg.progression)
        await progression.start()
        quester = None
        if cfg.mode == "quest":
            quester = Quester(client, cfg.quest, controller, progression, cfg.upkeep, dialogue)
            quester.deck_adapter = adapter
            if cfg.gear_checks:
                quester.gear = GearManager(client, progression.school or "")
                quester.gear.before_check = lambda: move_to_safety(client, 1200.0, "before checking gear")
            else:
                logger.info("gear checks are off (gear_checks: false)")
            if cfg.progression.enabled:
                quester.trainer = SpellTrainer(quester, progression, cfg.progression.train_levels)
            quester.healer = DungeonHealer(quester, cfg.upkeep)
            quester.pet = PetDancer(quester, cfg.pet)
            quester.potions = PotionShopper(quester, cfg.upkeep.use_potions and cfg.upkeep.buy_potions)
            quester.fighter = fighter
            if cfg.quest.flee_unneeded_fights:
                fighter.unneeded_fight = quester.unneeded_fight
            fighter.may_flee = quester.may_flee
            tasks.append(asyncio.create_task(quest_loop(quester, controller), name="quest"))
            from .prompt_watch import prompt_loop

            # The right person's talk prompt: X at once, not at the step's next look.
            tasks.append(asyncio.create_task(prompt_loop(client, quester, controller), name="prompt"))
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
