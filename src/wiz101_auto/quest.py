"""Follows the in-game quest helper (the quest arrow) until stopped.

Each `step()`:
  1. reads the quest objective text and the quest marker position,
  2. travels to the marker (teleport with bounce detection, or walking),
  3. interacts with whatever is there (NPC, door, dungeon sigil, object),
  4. for "Defeat ..." objectives, pulls the nearest mob if no fight started.

Combat itself is handled concurrently by the Fighter task.
"""

from __future__ import annotations

import asyncio
import math
import time

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .collect import Collector, collect_item_name
from .config import QuestConfig
from .npc import ServicesMenu
from .travel_data import find_zone_gate, objective_zone, quest_spots
from .upkeep import clear_popups, is_free, recover, scan_wisps, wait_for_loading, wait_until_free
from .wisps import sweep_points

INTERACT_RANGE = 750.0
BOUNCE_DISTANCE = 20.0
WISP_SCAN_SECONDS = 30.0
STATUS_EVERY_SECONDS = 20.0
SWITCH_QUEST_AFTER = 4  # same objective, this many interactions without change
MAX_QUEST_SLOTS = 6
# Quest book window paths (mapped by Deimos).
QUEST_LIST = ["WorldView", "DeckConfiguration", "wndQuestList"]
QUEST_BOOK_ALL = [*QUEST_LIST, "QuestLogAllButton"]
DOOR_RANGE = 300.0  # at the marker with no prompt: probably a doorway
DOOR_OVERSHOOT = 200.0
APPROACH_DISTANCES = (250.0, 450.0, 700.0)


def distance(a: XYZ, b: XYZ) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


class Quester:
    def __init__(self, client, cfg: QuestConfig, controller, progression=None, upkeep=None, dialogue=None):
        self.client = client
        self.dialogue = dialogue  # DialoguePolicy shared with the dialogue loop
        self.upkeep = upkeep
        self._last_wisp_scan = 0.0
        self.progression = progression
        self.services = ServicesMenu(client)
        self.lock = asyncio.Lock()  # one step (or watchdog action) at a time
        self._step_task: asyncio.Task | None = None
        self.collector = Collector(client)
        self.cfg = cfg
        self.controller = controller
        self.sprinter = client  # SprintyClient (bot.new_handler)
        self._last_progress = (None, None)
        self._last_progress_time = time.monotonic()
        self.objectives_completed = 0

    async def objective(self) -> str:
        return await ui.text_at(self.client, ui.QUEST_GOAL_TEXT)

    async def _note_progress(self, objective: str, zone: str | None):
        key = (objective, zone)
        if key != self._last_progress:
            if self._last_progress[0] and objective != self._last_progress[0]:
                self.objectives_completed += 1
                logger.success(f"objective done -> now: {objective!r}")
            self._last_progress = key
            self._last_progress_time = time.monotonic()
            self._attempts = 0
        elif time.monotonic() - self._last_progress_time > self.cfg.stuck_minutes * 60:
            self.controller.stop(f"no quest progress for {self.cfg.stuck_minutes} min on {objective!r}")

    # --- movement ------------------------------------------------------------

    async def _position(self) -> XYZ:
        return await self.client.body.position()

    async def _zone_changed(self, zone: str | None) -> bool:
        await wait_for_loading(self.client, appear_timeout=0.8)
        return await self.client.zone_name() != zone

    async def walk_through(self, target: XYZ, zone: str | None, overshoot: float = DOOR_OVERSHOOT) -> bool:
        """Walk straight at `target` and a little past it.

        Doors and zone exits only trigger when you walk into them; teleporting
        onto one gets rejected by the game and snaps you back.
        """
        pos = await self._position()
        dx, dy = target.x - pos.x, target.y - pos.y
        length = math.hypot(dx, dy)
        if length < 1:
            return False
        beyond = XYZ(target.x + dx / length * overshoot, target.y + dy / length * overshoot, target.z)
        logger.debug(f"walking through objective ({length:.0f} units + {overshoot:.0f} overshoot)")
        await self.client.goto(beyond.x, beyond.y)
        return await self._zone_changed(zone)

    async def approach_and_walk(self, target: XYZ, zone: str | None) -> bool:
        """Teleport to a spot in front of `target` (on the side we came from), then walk in."""
        pos = await self._position()
        dx, dy = pos.x - target.x, pos.y - target.y
        length = math.hypot(dx, dy)
        ux, uy = (dx / length, dy / length) if length > 1 else (1.0, 0.0)
        for back in APPROACH_DISTANCES:
            spot = XYZ(target.x + ux * back, target.y + uy * back, target.z)
            before = await self._position()
            await self.client.teleport(spot)
            await asyncio.sleep(0.8)
            if await self._zone_changed(zone):
                return True
            if distance(await self._position(), before) <= BOUNCE_DISTANCE and distance(before, spot) > 50:
                continue  # this spot was rejected too; try further back
            if await self.walk_through(target, zone):
                return True
        return False

    async def travel(self, target: XYZ) -> bool:
        """Get within interact range of `target`. Returns True on success."""
        start = await self._position()
        if distance(start, target) <= 5:
            return True
        zone = await self.client.zone_name()

        if not self.cfg.teleport:
            await self.client.goto(target.x, target.y)
            if await self._zone_changed(zone):
                return True
            return distance(await self._position(), target) < INTERACT_RANGE

        await self.client.teleport(target)
        await asyncio.sleep(0.8)
        if await self._zone_changed(zone):
            return True  # the teleport itself went through a zone transition
        if distance(await self._position(), start) > BOUNCE_DISTANCE:
            return True

        # Rejected: usually a door/zone exit, or a spot inside collision.
        logger.info("teleport was rejected (door or blocked spot); approaching on foot")
        if await self.approach_and_walk(target, zone):
            return True

        logger.debug("approach failed; trying points around the objective")
        for radius in (120, 250, 400):
            for i in range(8):
                ang = i * math.pi / 4
                p = XYZ(target.x + radius * math.cos(ang), target.y + radius * math.sin(ang), target.z)
                await self.client.teleport(p)
                await asyncio.sleep(0.5)
                if distance(await self._position(), start) > BOUNCE_DISTANCE:
                    return True
        logger.debug("walking toward objective")
        await self.client.goto(target.x, target.y)
        return distance(await self._position(), target) < INTERACT_RANGE

    # --- interaction ---------------------------------------------------------

    async def interact(self, objective: str = "") -> bool:
        """Press X on whatever prompt is showing. Returns True if something happened."""
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            return False
        prompt = (await ui.text_at(self.client, ui.NPC_RANGE_TEXT)).lower()
        logger.info(f"interacting: {prompt or '(no text)'}")

        if self.dialogue and "talk" in objective.lower():
            # This is the NPC the quest helper sent us to: accept what they offer.
            self.dialogue.accept_offers_for(30)
        await self.client.send_key(Keycode.X, 0.1)
        await asyncio.sleep(1.0)

        if "to enter" in prompt:
            # Dungeon warning ("you can't leave once you enter...")
            for _ in range(10):
                if await ui.click(self.client, ui.MODAL_CENTER_BUTTON):
                    break
                if await self.client.is_loading():
                    break
                await asyncio.sleep(0.3)
        elif "to talk" in prompt or await self.services.is_open():
            # The dialogue loop advances the conversation; wait for it to end,
            # including follow-up dialogues that open straight after. NPCs with
            # several quests first show a services menu to pick from.
            quiet_since = time.monotonic()
            picks = 0
            while time.monotonic() - quiet_since < 3.0:
                await self.controller.checkpoint()
                if picks < 3 and await self.services.is_open():
                    if self.dialogue:
                        self.dialogue.accept_offers_for(30)
                    if await self.services.choose(objective):
                        picks += 1
                        quiet_since = time.monotonic()
                        await asyncio.sleep(1.5)
                        continue
                    break  # nothing left to try; close_menus below shuts it
                if not await is_free(self.client):
                    quiet_since = time.monotonic()
                await asyncio.sleep(0.2)

        await wait_for_loading(self.client)
        await asyncio.sleep(0.5)

        if await ui.is_visible(self.client, ui.SPIRAL_DOOR_TELEPORT):
            # World gate: the quest destination is preselected, just go.
            while await ui.click(self.client, ui.SPIRAL_DOOR_TELEPORT):
                await asyncio.sleep(0.3)
            await wait_for_loading(self.client)

        if self.progression:
            await self.progression.handle_trainer()
        closed = await ui.close_menus(self.client)
        if closed:
            logger.debug(f"closed {closed} menu(s)")
        return True

    def cancel_step(self):
        """Abort the step in progress (used by the stall watchdog)."""
        if self._step_task and not self._step_task.done():
            self._step_task.cancel()

    def reset_objective_memory(self):
        """Forget per-objective assumptions so the next step starts fresh."""
        self.services._tried.clear()
        self._swept_for = None
        self._attempts = 0
        self._fallback_tried_for = None
        self.collector._taken.clear()
        self.collector._cache = None

    async def run_step(self):
        """Run one step as a cancellable task so the watchdog can abort a hung step."""
        async with self.lock:
            self._step_task = asyncio.create_task(self.step())
            try:
                await self._step_task
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise  # we're being shut down, not just the step
                logger.debug("quest step cancelled by the watchdog")
            finally:
                self._step_task = None

    async def _count_attempt(self):
        """Several tries on the same objective with no change: the tracked quest
        is probably blocked on another active quest, so switch to that one."""
        self._attempts = getattr(self, "_attempts", 0) + 1
        if self._attempts >= SWITCH_QUEST_AFTER:
            self._attempts = 0
            await asyncio.sleep(2.0)  # let a late objective update land first
            await self.switch_quest()

    async def switch_quest(self) -> bool:
        """Track the next quest in the quest book. True if the objective changed."""
        before = await self.objective()
        for _ in range(4):
            if await ui.is_visible(self.client, QUEST_BOOK_ALL):
                break
            await self.client.send_key(Keycode.Q, 0.1)
            await asyncio.sleep(0.8)
        else:
            logger.warning("could not open the quest book to switch quests")
            return False
        await ui.click(self.client, QUEST_BOOK_ALL)
        await asyncio.sleep(0.5)

        self._quest_index = getattr(self, "_quest_index", 0)
        clicked = False
        for _ in range(MAX_QUEST_SLOTS):
            self._quest_index = (self._quest_index + 1) % MAX_QUEST_SLOTS
            entry = f"wndQuestInfo{self._quest_index}"
            slot = [*QUEST_LIST, entry, "questInfoWindow", "wndQuestInfo", "txtGoal"]
            if await ui.click(self.client, slot):
                clicked = True
                await asyncio.sleep(0.6)
                break

        for _ in range(4):
            if not await ui.is_visible(self.client, QUEST_BOOK_ALL):
                break
            await self.client.send_key(Keycode.Q, 0.1)
            await asyncio.sleep(0.8)

        after = await self.objective()
        if clicked and after != before:
            logger.info(f"switched tracked quest: {before!r} -> {after!r}")
            return True
        logger.warning(f"tried to switch quests but the objective is still {after!r}")
        return False

    async def _press_collect(self):
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            return
        for _ in range(3):
            await self.client.send_key(Keycode.X, 0.1)
            await asyncio.sleep(0.2)
        await wait_until_free(self.client, timeout=15)

    async def collect(self, item: str, objective: str) -> bool:
        """Handle a collect objective. Returns True if it did something this step."""
        if await self.collector.collect_once(item, self._press_collect):
            await asyncio.sleep(0.5)
            if await self.objective() != objective:
                logger.success(f"collected {item}")
            return True
        # Nothing matching in view: look around the zone once per objective.
        if getattr(self, "_swept_for", None) != objective:
            self._swept_for = objective
            start = await self.client.body.position()
            points = sweep_points((start.x, start.y, start.z), [], 0)
            logger.info(f"searching the zone for {item!r} ({len(points)} spots)")
            for p in points:
                if not await is_free(self.client):
                    return True
                await self.client.teleport(XYZ(*p))
                await asyncio.sleep(0.8)
                if await self.collector.collect_once(item, self._press_collect):
                    return True
            await self.client.teleport(start)
            return True
        # Already searched: let the quest marker (if any) guide us, else wait for respawns.
        if distance(await self.client.quest_position.position(), XYZ(0, 0, 0)) < 1:
            logger.debug(f"no {item!r} found; waiting for respawns")
            self.controller.allow_idle(15)
            await asyncio.sleep(10)
            self._swept_for = None
            return True
        return False

    async def pull_mob(self):
        """For defeat objectives: teleport onto the closest mob to start a fight."""
        for _ in range(3):
            if await self.client.in_battle():
                return
            try:
                await self.sprinter.tp_to_closest_mob()
            except Exception as exc:
                logger.debug(f"no mob to pull: {exc}")
                return
            await asyncio.sleep(3.0)

    async def _no_marker_fallback(self, objective: str, zone: str) -> bool:
        """Walk through a known gate toward the place the objective names, else
        try the known hidden quest-target spots in this zone. True if it acted."""
        gate = find_zone_gate(objective, zone)
        if gate:
            pos, dest_zone = gate
            logger.info(f"no quest marker for {objective!r}; walking to {dest_zone} via a known gate")
            await self.controller.checkpoint()
            await self.travel(pos)
            if await self.client.zone_name() != zone:
                return True
        if objective_zone(objective) not in (None, zone):
            return False  # the target is in another zone; its spots here are someone else's
        for pos in quest_spots(zone):
            logger.info(f"no quest marker for {objective!r}; trying quest spot ({pos.x:.0f}, {pos.y:.0f})")
            await self.controller.checkpoint()
            await self.travel(pos)
            if not await wait_until_free(self.client, timeout=5):
                return True
            if distance(await self._position(), pos) < INTERACT_RANGE and await self.interact(objective):
                return True
        return False

    # --- main step -----------------------------------------------------------

    async def step(self):
        if not await is_free(self.client):
            return
        await clear_popups(self.client)
        if time.monotonic() - self._last_wisp_scan > WISP_SCAN_SECONDS:
            await scan_wisps(self.client)  # learn wisp spawn points while questing
            self._last_wisp_scan = time.monotonic()
        if self.upkeep and not await recover(self.client, self.upkeep, self.controller):
            return
        if self.progression:
            self.controller.allow_idle(90)  # spellbook work looks like "nothing happening"
            try:
                await self.progression.tick()
            finally:
                self.controller.end_idle()
            if not await is_free(self.client):
                return

        objective = await self.objective()
        zone = await self.client.zone_name()
        await self._note_progress(objective, zone)
        if time.monotonic() - getattr(self, "_last_status", 0.0) > STATUS_EVERY_SECONDS:
            self._last_status = time.monotonic()
            waited = time.monotonic() - self._last_progress_time
            logger.info(f"working on: {objective or '(no objective shown)'} [{zone}] for {waited:.0f}s")

        if await self.services.is_open():
            # A services menu left open (e.g. after an error) blocks the X prompt.
            if self.dialogue:
                self.dialogue.accept_offers_for(30)
            if not await self.services.choose(objective):
                await self.services.close()
            await asyncio.sleep(2.0)
            return

        item = collect_item_name(objective)
        if item and await self.collect(item, objective):
            return

        target = await self.client.quest_position.position()
        if distance(target, XYZ(0, 0, 0)) < 1:
            # No marker. Usually a zone change is in progress, or the objective
            # is a photomancy / collect task handled below.
            await asyncio.sleep(2.0)
            target = await self.client.quest_position.position()

        if self.cfg.photomancy and "photomance" in objective.lower():
            await self.client.send_key(Keycode.Z, 0.1)
            await asyncio.sleep(0.3)
            await self.client.send_key(Keycode.Z, 0.1)

        if distance(target, XYZ(0, 0, 0)) < 1:
            if getattr(self, "_fallback_tried_for", None) != (objective, zone):
                self._fallback_tried_for = (objective, zone)
                if await self._no_marker_fallback(objective, zone):
                    return
            logger.debug(f"no quest marker for {objective!r}; waiting")
            await asyncio.sleep(2.0)
            return

        logger.info(f"[{zone}] {objective}")
        await self.controller.checkpoint()
        await self.travel(target)
        if not await wait_until_free(self.client, timeout=5):
            return  # a fight or dialogue started on arrival

        dist = distance(await self.client.body.position(), target)
        if dist < INTERACT_RANGE and await self.interact(objective):
            await self._count_attempt()
            return

        if "defeat" in objective.lower():
            if not await self.client.in_battle():
                await self.pull_mob()
            return

        if dist < DOOR_RANGE and await self.client.zone_name() == zone:
            # Standing on the marker with nothing to interact with: it's most
            # likely a door or zone exit, which needs walking into.
            if await self.walk_through(target, zone):
                logger.info("walked through a door")
