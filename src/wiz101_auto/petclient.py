"""A second, pet-only bot on the player's other Wizard101 window (`pet-alt`).

The main bot hooks its own game (state/game_client.json); this one hooks the
other window, which stands in the Pet Pavilion, and plays the dance game
whenever the pet has the energy, waiting for it to refill between sessions.
Its files are its own (state/pet_alt.*, wiz101-pet.log): the main bot's
pid, status, mark and pet files are never touched.

    pet-alt start [--pid N]   start in the background (N: that game process)
    pet-alt stop              clean stop (unhooks the game)
    pet-alt status            running? last log lines
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

STATE = Path("state")
PID_FILE = STATE / "pet_alt.pid"
STOP_FILE = STATE / "pet_alt.stop"
OUT_FILE = STATE / "pet_alt.out"
PET_FILE = STATE / "pet_alt.json"  # this wizard's pet (kind, stage)
LOG_FILE = Path("wiz101-pet.log")
ENERGY_WAIT = 300.0  # seconds between energy checks while it refills
NUDGE_EVERY = 600.0  # a step now and then while waiting (the game logs idle players out)
RETRY_WAIT = 30.0  # after a game that didn't start
MAX_FAILS = 6  # games in a row that didn't start: stop


def _alive(pid: int) -> bool:
    from .service import pid_alive

    return pid_alive(pid)


def running_pid() -> int:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if _alive(pid) else 0


def pick_client(clients, main_pid: int, want_pid: int = 0):
    """The window to hook: the one asked for, else the only one that isn't
    the main bot's. None (with why) when that's not clear."""
    if want_pid:
        hit = [c for c in clients if c.process_id == want_pid]
        return (hit[0], "") if hit else (None, f"no Wizard101 process {want_pid}")
    others = [c for c in clients if c.process_id != main_pid]
    if len(others) == 1 and (main_pid or len(clients) == 2):
        return others[0], ""
    if not main_pid and len(clients) == 2:
        return None, "the main bot's window isn't known yet (start the main bot first)"
    if not others:
        return None, "only the main bot's Wizard101 window is open: start the other copy and log in"
    pids = ", ".join(str(c.process_id) for c in others)
    return None, f"several other Wizard101 windows ({pids}): pick one with --pid"


async def _hook(client):
    from .bot import HOOK_TIMEOUT, WORLD_HOOKS

    hooks = client.hook_handler
    await hooks.activate_all_hooks(wait_for_ready=False)
    await asyncio.wait_for(
        asyncio.gather(*(hooks._wait_for_value(hooks._base_addrs[n], None)
                         for n in ("current_root_window", *WORLD_HOOKS))),
        timeout=HOOK_TIMEOUT,
    )


async def _wait(seconds: float, client) -> bool:
    """Wait, nudging the wizard now and then; False if a stop was asked."""
    from wizwalker import Keycode

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if STOP_FILE.exists():
            return False
        await asyncio.sleep(min(5.0, end - time.monotonic()))
        last = getattr(_wait, "nudged", 0.0)
        if time.monotonic() - last > NUDGE_EVERY:
            _wait.nudged = time.monotonic()
            await client.send_key(Keycode.S, 0.1)
            await client.send_key(Keycode.W, 0.1)
    return not STOP_FILE.exists()


async def _dance_forever(client):
    from . import petdance

    dancer = petdance.PetDancer(_NoQuester(client))
    fails = 0
    while not STOP_FILE.exists():
        zone = await client.zone_name() or ""
        if zone != petdance.PET_PARK:
            logger.error(f"pet-alt: this wizard is in {zone or '?'}; put it in the Pet Pavilion "
                         f"(Wizard City), then start again")
            return
        await petdance.activate_dance_hook(client.hook_handler)
        await asyncio.sleep(petdance.HOOK_SETTLE)
        why, games = "", 0
        try:
            while not STOP_FILE.exists():
                why = await dancer.play_one()
                if why == "no snacks" and dancer.feed:
                    logger.info("pet-alt: out of snacks: playing on without feeding")
                    dancer.feed = False
                    why = "won"
                if why != "won":
                    break
                games, fails = games + 1, 0
                logger.success(f"pet-alt: dance game {games} won")
        finally:
            await dancer._close_all()
            await petdance.deactivate_dance_hook(client.hook_handler)
        if why == "no energy" or not why:
            now, most = await dancer.energy()
            logger.info(f"pet-alt: {games} game(s) this session; out of energy ({now}/{most}): "
                        f"checking again in {ENERGY_WAIT / 60:.0f} min")
            wait = ENERGY_WAIT
        else:
            fails += 1
            logger.warning(f"pet-alt: stopped after {games} game(s): {why} ({fails}/{MAX_FAILS} in a row)")
            if fails >= MAX_FAILS:
                logger.error("pet-alt: the game keeps not starting; stopping")
                return
            wait = RETRY_WAIT
        if not await _wait(wait, client):
            return


class _NoQuester:
    """PetDancer's quester: no travel (the wizard stays in the Pavilion)."""

    def __init__(self, client):
        self.client = client
        self._mark = None

    async def _mark_here(self, *_a, **_k):
        return False


async def run(want_pid: int = 0) -> int:
    from wizwalker.errors import PatternFailed

    from . import petdance
    from .bot import _click_left_of_center, close_handler, new_handler
    from .gamerestart import bot_game_pid

    petdance.PET_STATE = PET_FILE  # (this wizard's pet, not the main one's)
    handler = new_handler()
    client, why = pick_client(handler.get_new_clients(), bot_game_pid(), want_pid)
    if client is None:
        logger.error(f"pet-alt: {why}")
        return 1
    logger.info(f"pet-alt: hooking game process {client.process_id} (main bot: {bot_game_pid()})")
    try:
        await _hook(client)
    except PatternFailed:
        await close_handler(handler)
        logger.error("pet-alt: could not hook that game (left patched by a session that didn't stop "
                     "cleanly): restart that Wizard101 and log in")
        return 1
    except TimeoutError:
        await close_handler(handler)
        logger.error("pet-alt: hooks did not activate: is that wizard logged in and in the world?")
        return 1
    logger.success("pet-alt: connected")
    # (Clicks aimed past display scaling, as in the main bot: this window sat
    # on another monitor and Play! never registered.)
    _click_left_of_center(client)
    try:
        async with client.mouse_handler:
            await _dance_forever(client)
    except Exception as exc:
        logger.opt(exception=exc).error("pet-alt crashed")
    finally:
        await close_handler(handler)
        logger.info("pet-alt: unhooked; stopped")
    return 0


def main_run(want_pid: int = 0) -> int:
    logger.add(LOG_FILE, level="DEBUG", rotation="5 MB", retention=2, encoding="utf-8")
    STOP_FILE.unlink(missing_ok=True)
    try:
        return asyncio.run(run(want_pid))
    finally:
        PID_FILE.unlink(missing_ok=True)


def start(want_pid: int = 0) -> int:
    STATE.mkdir(exist_ok=True)
    if pid := running_pid():
        print(f"pet-alt already running (PID {pid}). Use 'pet-alt stop'.")
        return 1
    STOP_FILE.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "wiz101_auto", "pet-alt", "run", "--pid", str(want_pid)]
    flags = 0x00000008 | 0x00000200 | 0x08000000 if sys.platform == "win32" else 0
    out = open(OUT_FILE, "a", encoding="utf-8")
    out.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    out.flush()
    proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=flags, cwd=os.getcwd())
    PID_FILE.write_text(str(proc.pid))
    time.sleep(5)
    if proc.poll() is not None:
        print("pet-alt exited right away:\n" + status_text())
        return 1
    print(f"pet-alt started (PID {proc.pid}); log: {LOG_FILE}. 'pet-alt stop' stops it.")
    return 0


def stop() -> int:
    pid = running_pid()
    if not pid:
        print("pet-alt isn't running")
        return 0
    STOP_FILE.write_text("stop", encoding="utf-8")
    for _ in range(120):  # (a game in progress finishes first)
        if not _alive(pid):
            print("pet-alt stopped (unhooked)")
            return 0
        time.sleep(1)
    print(f"pet-alt (PID {pid}) didn't stop within 2 min; check {LOG_FILE}")
    return 1


def status_text() -> str:
    pid = running_pid()
    head = f"pet-alt: running (PID {pid})" if pid else "pet-alt: not running"
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    except OSError:
        lines = []
    return "\n".join([head, *lines])
