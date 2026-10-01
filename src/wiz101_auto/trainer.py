"""Trips to the school professor to learn new spells.

At the levels in `progression.train_levels` (school spells unlock then) the
bot goes to its professor and trains every listed spell it's high enough for
and doesn't know yet, then the deck is rebuilt so the new spell gets used.

The route was recorded with `wiz101-auto record` (see state/route_record_*):
the Go Home button lands in the dorm from any world, whenever; its door leads
to Ravenwood; each school's door is in Ravenwood; the professor stands inside.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .questlist import norm
from .upkeep import is_free, wait_for_loading, wait_until_free

RAVENWOOD = "WizardCity/WC_Ravenwood"
DORM = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
VISIT_REQUEST = Path("state") / "visit_professor.request"  # `visit-professor`: fetch the professor's quests
DORM_DOOR = XYZ(-205.0, 13.0, 0.0)

GUI = ["WorldView", "NPCTrainingGUI"]
SELECTION = [*GUI, "TrainingSelection"]
SPELLS = [*SELECTION, "SpellList"]
TRAIN = [*SELECTION, "TrainButton"]
EXIT = [*SELECTION, "ExitButton"]
PAGE_DOWN = [*SELECTION, "PageControls", "PageDown"]
REWARD = [*GUI, "wndSpellReward"]
REWARD_CLOSE = [*REWARD, "btnBackground"]
MAX_OPTIONS = 12
RETRY_MINUTES = 10
MAX_PAGES = 4


@dataclass(frozen=True)
class School:
    interior: str
    door: XYZ  # in Ravenwood
    professor: str
    spot: XYZ  # where a player stands to talk to the professor (the "Player Stand In")


SCHOOLS = {
    "myth": School(
        "WizardCity/Interiors/WC_SchoolMyth", XYZ(-3546.0, 3912.0, 2.0), "Cyrus Drake", XYZ(648.0, 14.0, 73.0)
    ),
}


def next_training(level: int, trained: int, schedule: list[int]) -> int | None:
    """The schedule level due for a trip (reached but not trained yet), if any."""
    due = [lv for lv in schedule if trained < lv <= level]
    return max(due) if due else None


def trainable(options: list[tuple[str, int]], level: int, known: set[str]) -> list[str]:
    """Spells from the trainer list we're high enough for and don't know yet."""
    return [name for name, lv in options if lv <= level and norm(name) not in known]


# The compass's teleport buttons: "GoHomeButton" goes to the current world's
# hub (the Oasis in Krokotopia); "GotoDormButton" goes to the dorm.
DORM_BUTTON = "GotoDormButton"


async def go_home(client) -> bool:
    """Press the dorm button (usable any time, from any world): lands in the
    dorm. A few tries: a click right after logging back in didn't take."""
    zone = await client.zone_name()
    for _ in range(3):
        if not await ui.click_named(client, DORM_BUTTON):
            logger.warning("no dorm button to click")
            return False
        await asyncio.sleep(1.0)
        await ui.confirm_modal(client)
        await wait_for_loading(client, appear_timeout=6.0)
        zone = await client.zone_name()
        if zone == DORM:
            return True
        await asyncio.sleep(2.0)
    logger.warning(f"the dorm button took us to {zone}, not the dorm")
    return False


async def home_to_ravenwood(q) -> bool:
    """Go Home lands in the dorm, whose door opens onto Ravenwood."""
    logger.info("using Go Home to get to Ravenwood")
    if await q.client.zone_name() != DORM and not await go_home(q.client):
        return False
    await q.approach_and_walk(DORM_DOOR, DORM)
    await wait_for_loading(q.client)
    return await q.client.zone_name() == RAVENWOOD


class SpellTrainer:
    def __init__(self, quester, progression, schedule: list[int]):
        self.q = quester
        self.client = quester.client
        self.progression = progression
        self.schedule = sorted(schedule)
        self._retry_at = 0.0  # after a failed trip, wait before the next

    def due(self, level: int) -> int | None:
        return next_training(level, self.progression.state.get("trained_level", 0), self.schedule)

    async def tick(self) -> bool:
        """Call while free. Makes the trip when one is due. True if it acted."""
        if VISIT_REQUEST.exists():
            return await self.visit_for_quests()
        level = await self.client.stats.reference_level()
        target = self.due(level)
        school = SCHOOLS.get((self.progression.school or "").lower())
        if target is None or school is None or time.monotonic() < self._retry_at:
            return False
        logger.info(f"level {level}: going to {school.professor} to learn new spells (level {target} spells)")
        before = set(self.q._book_names)
        if not await self._go_to_professor(school):
            logger.warning(f"could not reach {school.professor}; trying again in {RETRY_MINUTES} min")
            self._retry_at = time.monotonic() + RETRY_MINUTES * 60
            return True
        learned = await self._train(level)
        self.progression.state["trained_level"] = target
        self.progression._save()
        if learned:
            logger.success(f"learned: {', '.join(learned)}")
            self.progression.request_check(f"trained {', '.join(learned)}")
        else:
            logger.info("nothing new to learn at the trainer")
        # A quest he gave on the way (a spell quest): follow it next.
        if before:  # (the book read before the trip: else every quest looks new)
            self.q.pin_new_quest_after(before)
        return True

    async def visit_for_quests(self) -> bool:
        """`visit-professor`: go to the school professor and take every quest
        they offer (Cyrus Drake's quest to Aquila), then pin the new quest so
        the bot follows it. True (it acted)."""
        school = SCHOOLS.get((self.progression.school or "").lower())
        if school is None:
            VISIT_REQUEST.unlink(missing_ok=True)
            return False
        logger.info(f"visiting {school.professor} for quests")
        before = set(self.q._book_names)
        if not await self._go_to_professor_zone(school):
            logger.warning(f"could not reach {school.professor}; trying again in {RETRY_MINUTES} min")
            self._retry_at = time.monotonic() + RETRY_MINUTES * 60
            return True
        await self.client.teleport(school.spot)
        await asyncio.sleep(1.0)
        if self.q.dialogue:
            self.q.dialogue.accept_offers_for(60)
        for _ in range(4):
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                await self.client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(1.5)
                break
            await self.client.send_key(Keycode.S, 0.3)
            await self.client.send_key(Keycode.W, 0.4)
            await asyncio.sleep(0.6)
        # A menu (quests + training): anything but training and shops.
        for _ in range(3):
            if not await self.q.services.is_open():
                break
            if not await self.q.services.choose_quest():
                break
            await asyncio.sleep(2.0)
            await wait_until_free(self.client, timeout=40)
        await wait_until_free(self.client, timeout=40)
        await ui.close_menus(self.client)
        VISIT_REQUEST.unlink(missing_ok=True)
        self.q.pin_new_quest_after(before)
        return True

    async def _go_to_professor_zone(self, school: School) -> bool:
        zone = await self.client.zone_name() or ""
        if zone not in (school.interior, RAVENWOOD) and not await self._home_to_ravenwood():
            return False
        if await self.client.zone_name() == RAVENWOOD:
            await self.q.travel(school.door)
            if await self.client.zone_name() == RAVENWOOD:
                await self.q.approach_and_walk(school.door, RAVENWOOD)
            await wait_for_loading(self.client)
        return await self.client.zone_name() == school.interior

    # --- route ---------------------------------------------------------------

    async def _go_to_professor(self, school: School) -> bool:
        zone = await self.client.zone_name() or ""
        if zone not in (school.interior, RAVENWOOD) and not await self._home_to_ravenwood():
            return False
        zone = await self.client.zone_name() or ""
        if zone == RAVENWOOD:
            logger.info(f"walking into the {school.interior.rsplit('_', 1)[-1]} building")
            await self.q.travel(school.door)
            if await self.client.zone_name() == RAVENWOOD:
                await self.q.approach_and_walk(school.door, RAVENWOOD)
            await wait_for_loading(self.client)
        zone = await self.client.zone_name() or ""
        if zone != school.interior or not await is_free(self.client):
            logger.warning(f"trainer: not inside the school (in {zone}, free: {await is_free(self.client)})")
            return False
        if await self._talk_to(school):
            return True
        logger.warning(f"trainer: no training window from {school.professor} at {school.spot}")
        try:
            lines = await ui.dump_tree(self.client.root_window, max_depth=8)
            (Path("state") / "trainer_fail_windows.txt").write_text("\n".join(lines), encoding="utf-8")
            me = await self.client.body.position()
            logger.info(f"trainer: standing at ({me.x:.0f}, {me.y:.0f}); windows saved to "
                        "state/trainer_fail_windows.txt")
        except Exception:
            pass
        return False

    async def _home_to_ravenwood(self) -> bool:
        return await home_to_ravenwood(self.q)

    async def _talk_to(self, school: School) -> bool:
        # Not a lookup by name: "WC_StandIn_CyrusDrake_01" by the entrance
        # matches too, and nothing happens there.
        await self.client.teleport(school.spot)
        await asyncio.sleep(1.0)
        menu = self.q.services
        for _ in range(6):
            if await ui.is_visible(self.client, GUI):
                return True
            if await menu.is_open():
                # A menu: his quests first ('Mything Link', a spell quest:
                # accepted, then pinned after the trip), then training.
                if self.q.dialogue:
                    self.q.dialogue.accept_offers_for(60)
                if await menu.choose_quest():
                    await asyncio.sleep(2.0)
                    await wait_until_free(self.client, timeout=40)
                    await ui.close_menus(self.client)
                    continue  # talk again: the menu comes back with training
                if await menu.choose_training():
                    await asyncio.sleep(1.5)
                    continue
                await menu.close()
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                await self.client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(1.5)
                continue
            # Prompts appear on walking into range, not on teleporting there.
            await self.client.send_key(Keycode.S, 0.3)
            await self.client.send_key(Keycode.W, 0.4)
            await asyncio.sleep(0.6)
        return await ui.is_visible(self.client, GUI)

    # --- trainer window --------------------------------------------------------

    async def _options(self) -> list[tuple[str, int, list[str]]]:
        out = []
        for i in range(1, MAX_OPTIONS + 1):
            path = [*SPELLS, f"Option_{i}"]
            if not await ui.is_visible(self.client, path):
                continue
            name = (await ui.text_at(self.client, [*path, "NameBox", "Name"])).strip()
            level = (await ui.text_at(self.client, [*path, "LevelBox", "Level"])).strip()
            if name and level.isdigit():
                out.append((name, int(level), path))
        return out

    async def _press(self, path: list[str]) -> bool:
        w = await ui.window_at(self.client, path)
        if w is None:
            return False
        await ui.click_center(self.client, w)
        return True

    async def _train(self, level: int) -> list[str]:
        known = {norm(s) for s in self.progression.state.get("known_spells", [])}
        learned: list[str] = []
        try:
            for _ in range(MAX_PAGES):
                options = await self._options()
                have = known | {norm(x) for x in learned}
                wanted = trainable([(n, lv) for n, lv, _ in options], level, have)
                for name, _lv, path in options:
                    if name not in wanted:
                        continue
                    await self._press(path)
                    await asyncio.sleep(0.5)
                    await self._press(TRAIN)
                    await asyncio.sleep(1.5)
                    await ui.confirm_modal(self.client, ("leftButton", "centerButton"))
                    if await ui.is_visible(self.client, REWARD):
                        await self._press(REWARD_CLOSE)
                        await asyncio.sleep(0.8)
                    learned.append(name)
                    logger.info(f"trained {name}")
                if any(lv > level for _n, lv, _p in options) or not await self._press(PAGE_DOWN):
                    break  # the list is sorted by level: nothing further is trainable
                await asyncio.sleep(0.6)
        finally:
            await self._press(EXIT)
            await asyncio.sleep(0.8)
        return learned
