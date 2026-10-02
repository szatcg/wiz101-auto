"""Pet dance game grinding (the `pet` command).

`pet [--games N]` asks the running bot to go to the Pet Pavilion in Wizard
City at its next free moment, play the dance game on the Wizard City track
until the pet is out of energy (or N games), feed the first snack offered
after each win, then Recall back to where it was and go on questing.

The dance moves come straight from memory: a hook on the code that reads
the pet's dance sequence (from Deimos, github.com/Deimos-Wizard101,
GPL-3.0: the hook by peechez in src/dance_game_hook.py, the game flow from
src/auto_pet.py). The moves are 'a'..'d' for up/right/down/left and are
sent as W/D/S/A key presses.
"""

from __future__ import annotations

import asyncio
import ctypes
import re
import time
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode
from wizwalker.memory import HookHandler, SimpleHook

from . import ui
from .upkeep import is_free, wait_for_loading

PET_REQUEST = Path("state") / "pet.request"  # `pet`: games to play ("0": until the energy runs out)
PET_PARK = "WizardCity/WC_Streets/Interiors/WC_PET_Park"
DANCE_SIGIL = XYZ(-4450.58, -994.90, -8.04)  # the dance game's sigil in the Pet Pavilion
DANCE_TITLE = "Dance Game"  # the sigil's prompt title
NPC_TITLE = ["WorldView", "NPCRangeWin", "wndTitleBackground", "NPCRangeTxtTitle"]
MOVES = str.maketrans("abcd", "WDSA")
ROUNDS = 5  # rounds in a dance game
HOOK_SETTLE = 5.0  # the hook misses turns when a game starts right after it's placed
MAX_GAMES = 200  # a ceiling for "until the energy runs out"
WM_KEYDOWN, WM_KEYUP = 0x100, 0x101


class DanceGameMovesHook(SimpleHook):
    """Copies the dance game's move string pointer to an export (peechez, via Deimos)."""

    pattern = rb"\x48\x8B\xD8\x48\x39\x70\x10\x76.\x8B\xC6"
    instruction_length = 7
    exports = [("dance_game_moves", 8)]
    noops = 2

    async def bytecode_generator(self, packed_exports):
        return (
            b"\x48\x8B\xD8"  # mov rbx, rax
            b"\x48\x8B\x00"  # mov rax, [rax]
            b"\x48\xA3" + packed_exports[0][1]  # mov [export], rax
            + b"\x48\x8B\xC3"  # mov rax, rbx
            b"\x48\x39\x70\x10"  # cmp [rax+10], rsi (the instruction replaced)
        )


async def activate_dance_hook(handler: HookHandler):
    if handler._check_if_hook_active(DanceGameMovesHook):
        return
    await handler._check_for_autobot()
    hook = DanceGameMovesHook(handler)
    await hook.hook()
    handler._active_hooks[DanceGameMovesHook] = hook
    handler._base_addrs["dance_game_moves"] = hook.dance_game_moves


async def deactivate_dance_hook(handler: HookHandler):
    if not handler._check_if_hook_active(DanceGameMovesHook):
        return
    hook = handler._get_hook_by_type(DanceGameMovesHook)
    del handler._active_hooks[DanceGameMovesHook]
    await hook.unhook()
    handler._base_addrs.pop("dance_game_moves", None)


def decode_moves(raw: bytes) -> str:
    """The hook's 8 bytes -> the keys to press ('acbd' -> 'WSDA')."""
    return raw.partition(b"\0")[0].decode(errors="ignore").translate(MOVES)


async def read_moves(handler: HookHandler) -> str:
    addr = handler._base_addrs.get("dance_game_moves")
    if not addr:
        return ""
    try:
        return decode_moves(await handler.read_bytes(addr, 8))
    except Exception:
        return ""


def post_keys(window_handle: int, keys: str):
    """Key presses straight to the game window (works with it in the background)."""
    user32 = ctypes.windll.user32
    for key in keys:
        user32.PostMessageW(window_handle, WM_KEYDOWN, ord(key), 0)
        user32.PostMessageW(window_handle, WM_KEYUP, ord(key), 0)


def first_number(text: str) -> int | None:
    """'Energy: 12/45' -> 12."""
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def games_requested() -> int | None:
    """None: no request; 0: until the energy runs out; N: N games."""
    try:
        text = PET_REQUEST.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    n = first_number(text)
    return n if n is not None else 0


async def _visible(root, *names):
    """The first visible window named names[-1] under one named names[-2]
    under ... (each searched anywhere below the one before), else None."""
    current = root
    for name in names:
        found = None
        try:
            for w in await current.get_windows_with_name(name):
                if await w.is_visible():
                    found = w
                    break
        except Exception:
            return None
        if found is None:
            return None
        current = found
    return current


async def _text(root, *names) -> str:
    w = await _visible(root, *names)
    if w is None:
        return ""
    try:
        return ui._TAGS.sub("", await w.maybe_text() or "").strip()
    except Exception:
        return ""


async def _click(client, *names) -> bool:
    w = await _visible(client.root_window, *names)
    if w is None:
        return False
    try:
        await client.mouse_handler.click_window(w)
        return True
    except Exception as exc:
        logger.debug(f"pet: click {names[-1]} failed: {exc}")
        return False


async def _wait_for(check, timeout: float, every: float = 0.15) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await check():
            return True
        await asyncio.sleep(every)
    return False


class PetDancer:
    """Runs the `pet` request from inside the bot (the one process hooked in)."""

    def __init__(self, quester, feed: bool = True):
        self.q = quester
        self.client = quester.client
        self.feed = feed

    async def tick(self) -> bool:
        """Call while free. True if it made the trip."""
        wanted = games_requested()
        if wanted is None:
            return False
        try:
            await self.trip(wanted)
        finally:
            PET_REQUEST.unlink(missing_ok=True)
        return True

    async def trip(self, wanted: int):
        start = await self.client.zone_name() or ""
        if start != PET_PARK:
            marked = await self.q._mark_here("travel", require_clear=False)
            note = " (marked here)" if marked else ""
            logger.info(f"pet: going to the Pet Pavilion for the dance game{note}")
            if not await self._go_to_pavilion():
                logger.warning("pet: could not reach the Pet Pavilion")
                return
        else:
            marked = False
        games, why = 0, "done"
        await activate_dance_hook(self.client.hook_handler)
        await asyncio.sleep(HOOK_SETTLE)
        try:
            limit = wanted or MAX_GAMES
            while games < limit:
                result = await self.play_one()
                if result != "won":
                    why = result
                    break
                games += 1
                logger.success(f"pet: dance game {games} won")
        finally:
            await self._close_all()
            await deactivate_dance_hook(self.client.hook_handler)
        logger.info(f"pet: {games} game(s) played; stopped: {why}")
        if marked and self.q._mark and await is_free(self.client):
            await self.q._recall(self.q._mark.zone)

    async def _go_to_pavilion(self) -> bool:
        from .trainer import home_to_ravenwood

        zone = await self.client.zone_name() or ""
        # Another world, or a Wizard City room no gate leads out of: Go Home,
        # out of the dorm into Ravenwood; then the gates (Commons, Pavilion).
        if not zone.startswith("WizardCity/") or "/interiors/" in zone.lower():
            if not await home_to_ravenwood(self.q):
                return False
        await self.q.go_to_zone(PET_PARK)
        await wait_for_loading(self.client)
        return await self.client.zone_name() == PET_PARK

    async def _on_sigil(self) -> bool:
        """Stand on the sigil until its prompt shows."""
        for _ in range(4):
            if await ui.text_at(self.client, NPC_TITLE) == DANCE_TITLE:
                return True
            await self.client.teleport(DANCE_SIGIL)
            await asyncio.sleep(1.0)
            if await ui.text_at(self.client, NPC_TITLE) == DANCE_TITLE:
                return True
            await self.client.send_key(Keycode.S, 0.2)
            await self.client.send_key(Keycode.W, 0.3)
            await asyncio.sleep(0.6)
        return False

    async def play_one(self) -> str:
        """One game, from the sigil to the reward screen closed: 'won', or
        why it stopped ('no energy', 'no snacks', 'no sigil', 'no game')."""
        root = self.client.root_window
        if not await _visible(root, "PetGameTracks"):
            if not await self._on_sigil():
                return "no sigil"
            for _ in range(10):
                await self.client.send_key(Keycode.X, 0.1)
                if await _wait_for(lambda: _visible(root, "PetGameTracks"), 1.5):
                    break
            else:
                return "no game"
        cost = first_number(await _text(root, "PetGameTracks", "txtEnergyCost"))
        have = first_number(await _text(root, "PetGameTracks", "txtYourEnergy"))
        logger.info(f"pet: energy {have} (a game costs {cost})")
        if cost is not None and have is not None and have < cost:
            return "no energy"
        await _click(self.client, "PetGameTracks", "btnTrack0")  # the Wizard City dance track
        await asyncio.sleep(0.3)
        await _click(self.client, "PetGameTracks", "btnNext")  # Play
        if not await self.dance():
            return "no game"
        return await self.collect()

    async def dance(self) -> bool:
        root = self.client.root_window
        if not await _wait_for(lambda: _visible(root, "PetGameDance", "txtAction"), 30):
            logger.warning("pet: the dance game didn't start")
            return False
        for n in range(ROUNDS):
            # The pet shows the moves, then "Go!": the player's turn.
            async def go():
                return "Go!" in await _text(root, "PetGameDance", "txtAction")

            async def not_go():
                return not await go()

            await _wait_for(not_go, 15)
            if not await _wait_for(go, 30):
                logger.warning(f"pet: no 'Go!' in round {n + 1}")
                return False
            await asyncio.sleep(1.5)
            moves = await read_moves(self.client.hook_handler)
            logger.info(f"pet: round {n + 1}: {moves or '(no moves read)'}")
            post_keys(self.client.window_handle, moves)
        await asyncio.sleep(3.0)
        return True

    async def collect(self) -> str:
        """The reward screen: Next, the first snack and Feed Pet, Finish."""
        root = self.client.root_window
        if not await _wait_for(lambda: _visible(root, "PetGameRewards"), 30):
            return "no game"
        await _click(self.client, "PetGameRewards", "btnNext")  # Next
        await asyncio.sleep(1.5)
        await self._close_level_up()
        result = "won"
        if self.feed:
            if await _click(self.client, "PetGameRewards", "chkSnackCard0"):
                await asyncio.sleep(0.6)
                await _click(self.client, "PetGameRewards", "btnNext")  # Feed Pet
                await asyncio.sleep(1.0)
                await self._close_level_up()
            else:
                result = "no snacks"
        await _wait_for(lambda: _visible(root, "PetGameRewards", "btnBack"), 10)
        for _ in range(20):
            if not await _visible(root, "PetGameRewards"):
                break
            await _click(self.client, "PetGameRewards", "btnBack")  # Finish
            await asyncio.sleep(0.3)
        return result

    async def _close_level_up(self):
        root = self.client.root_window
        for _ in range(10):
            if not await _visible(root, "PetLevelUpWindow"):
                return
            logger.success("pet: the pet leveled up")
            await _click(self.client, "PetLevelUpWindow", "btnPetLevelClose")
            await asyncio.sleep(0.3)

    async def _close_all(self):
        for _ in range(10):
            root = self.client.root_window
            if await _visible(root, "PetGameTracks"):
                await _click(self.client, "PetGameTracks", "btnBack")
            elif await _visible(root, "PetGameRewards"):
                await _click(self.client, "PetGameRewards", "btnBack")
            else:
                return
            await asyncio.sleep(0.4)
