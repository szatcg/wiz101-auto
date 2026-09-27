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
import re
import time
from dataclasses import dataclass

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .collect import Collector, collect_item_name, landmarks, spread_points
from .config import QuestConfig
from .deck import close_spellbook
from .dungeons import DungeonEntry, DungeonMemory
from .npc import ServicesMenu
from .travel_data import find_zone_gate, gate_toward, hops_to_place, objective_zone, quest_spots
from .upkeep import (
    clear_popups,
    is_free,
    recover,
    scan_wisps,
    unstick,
    wait_for_loading,
    wait_until_free,
    wisp_memory,
)
from .wisps import sweep_points

INTERACT_RANGE = 750.0
BOUNCE_DISTANCE = 20.0
WISP_SCAN_SECONDS = 30.0
STATUS_EVERY_SECONDS = 20.0
SWITCH_QUEST_AFTER = 4  # same objective, this many interactions without change
MAX_QUEST_SLOTS = 6
RANK_QUESTS_EVERY = 300.0  # seconds between quest-book rankings
# Quest book window paths (mapped by Deimos).
QUEST_LIST = ["WorldView", "DeckConfiguration", "wndQuestList"]
QUEST_BOOK_ALL = [*QUEST_LIST, "QuestLogAllButton"]
DOOR_RANGE = 300.0  # at the marker with no prompt: probably a doorway
DOOR_OVERSHOOT = 200.0
APPROACH_DISTANCES = (250.0, 450.0, 700.0)
SIGIL_RANGE = 150.0  # a dungeon sigil this close to the marker is the way in
STUCK_CHECK_AFTER = 20.0  # seconds on one objective before checking we can still walk
STUCK_CHECK_EVERY = 30.0
SIGIL_WAIT = 25.0  # the countdown after pressing X is ~10s
SIGIL_LEAVE = 3000.0  # the prompt re-arms only after leaving this far (~20m in game)
FAR_SWEEP_SPACING = 3000.0  # pickups load within roughly this range
FAR_SWEEP_MAX = 25


@dataclass
class QuestEntry:
    slot: int
    name: str
    activity: bool = False  # spell/activity quest (new spells, training)
    mainline: bool = False
    active: bool = False
    reward: int = 0  # first reward amount shown in the quest book
    world: str = ""  # area shown in the book, e.g. "Triton Avenue"
    hops: int | None = None  # gate hops from where the wizard is (None = unknown)


UNKNOWN_HOPS = 5  # an area we can't route to counts as fairly far


def quest_rank(q: QuestEntry) -> tuple:
    """Higher is better: activity quests make the wizard stronger, the main story
    unlocks content, side quests only give their reward; nearer beats farther."""
    hops = UNKNOWN_HOPS if q.hops is None else q.hops
    return (q.activity, q.mainline, -hops, q.reward)


def choose_quest(quests: list[QuestEntry]) -> QuestEntry | None:
    """Which quest to track. Stick with the tracked questline until it's done;
    only a spell/class quest may interrupt it."""
    if not quests:
        return None
    active = next((q for q in quests if q.active), None)
    if active and active.activity:
        return active
    spell_quests = [q for q in quests if q.activity]
    if spell_quests:
        return max(spell_quests, key=quest_rank)
    if active:
        return active
    return max(quests, key=quest_rank)


def is_combat_objective(objective: str) -> bool:
    """Objectives met by fighting: 'Defeat X', 'Summon Myth Minion', 'Cast ...'."""
    first = objective.strip().lower().split(" ", 1)[0]
    return first in ("defeat", "summon", "cast")


def _norm_name(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalpha())


def fight_needed(objective: str, enemy_names: list[str], zone: str, has_boss: bool) -> bool:
    """Is this fight part of the quest? Conservative: bosses, fights inside
    buildings/dungeons and unclear cases count as needed."""
    if has_boss or "interiors" in zone.lower() or not objective.strip():
        return True
    if not is_combat_objective(objective):
        return False
    text = objective.strip()
    if not text.lower().startswith("defeat "):
        return True  # "Summon/Cast ...": any fight does
    wanted = re.split(r"\s+(?:and|in)\s+|\s*\(", text[len("defeat ") :], maxsplit=1)[0]
    target = _norm_name(wanted).removesuffix("s")
    if len(target) < 3:
        return True
    return any(target in _norm_name(n) or _norm_name(n) in target for n in enemy_names)


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
        self.gear = None  # GearManager, set by the bot
        self._activity_quests: set[str] = set()  # spell quests seen in the book
        self._sigil_failed_at: XYZ | None = None  # sigil whose last try didn't start
        self._last_stuck_check = 0.0
        self._bad_gates: set[tuple[str, str]] = set()

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

    async def go_to_zone(self, dest: str, max_hops: int = 6) -> bool:
        """Walk the known gates to `dest`. True once there."""
        for _ in range(max_hops):
            zone = await self.client.zone_name()
            if zone == dest:
                return True
            if not await is_free(self.client):
                return False  # a fight started on the way
            gate = gate_toward(zone, dest, self._bad_gates)
            if not gate:
                return False
            pos, next_zone = gate
            logger.info(f"heading to {dest}: gate to {next_zone}")
            await self.travel(pos)
            if await self.client.zone_name() == zone and not await self.approach_and_walk(pos, zone):
                logger.warning(f"gate {zone} -> {next_zone} did not work; avoiding it")
                self._bad_gates.add((zone, next_zone))
            await wait_for_loading(self.client)
        return await self.client.zone_name() == dest

    async def _sigil_at(self, target: XYZ) -> XYZ | None:
        """Position of a dungeon sigil ("Teleport Semi Circle") at the marker, if any."""
        try:
            entities = await self.client.get_base_entity_list()
        except Exception:
            return None
        for e in entities:
            try:
                template = await e.object_template()
                name = (await template.object_name()).lower() if template else ""
                if "semi circle" not in name and "sigil" not in name:
                    continue
                pos = await e.location()
                if distance(pos, target) < SIGIL_RANGE:
                    return pos
            except Exception:
                continue
        return None

    async def _enter_by_sigil(self, sigil: XYZ, zone: str | None) -> bool:
        """Dungeons start with ONE press of X on the sigil, then a ~10s countdown
        that any movement or another X press cancels. After a failed try the
        prompt won't restart until we step off the sigil and back on."""
        # An open menu (e.g. the spellbook) hides the "press X" prompt.
        await close_spellbook(self.client)
        await ui.close_menus(self.client)
        if self._sigil_failed_at is not None and distance(self._sigil_failed_at, sigil) < SIGIL_RANGE:
            # The prompt only comes back after leaving the sigil's (large) area
            # entirely: go somewhere far on the map, then come back.
            away = await self._far_spot(sigil)
            logger.info(f"re-arming the dungeon sigil: leaving to ({away.x:.0f}, {away.y:.0f}), then back")
            await self.client.teleport(away)
            await asyncio.sleep(1.5)
            # Come back like a player would: land short and walk onto it.
            dx, dy = away.x - sigil.x, away.y - sigil.y
            length = math.hypot(dx, dy) or 1.0
            await self.client.teleport(XYZ(sigil.x + dx / length * 300, sigil.y + dy / length * 300, sigil.z))
            await asyncio.sleep(1.0)
            await self.client.goto(sigil.x, sigil.y)
            await asyncio.sleep(1.0)
        elif distance(await self._position(), sigil) > SIGIL_RANGE:
            await self.client.teleport(sigil)
            await asyncio.sleep(0.8)
        await wait_for_loading(self.client)
        self.controller.allow_idle(SIGIL_WAIT + 10)
        try:
            await asyncio.sleep(0.5)
            logger.info(f"on the dungeon sigil; pressing X once and waiting up to {SIGIL_WAIT:.0f}s")
            await self.client.send_key(Keycode.X, 0.1)
            deadline = time.monotonic() + SIGIL_WAIT
            while time.monotonic() < deadline:
                if await self.client.is_loading() or await self.client.zone_name() != zone:
                    await wait_for_loading(self.client)
                    logger.success("entered the dungeon")
                    self._sigil_failed_at = None
                    await self._remember_dungeon(zone, sigil)
                    return True
                await ui.confirm_modal(self.client)  # "enter alone?" confirmation
                await asyncio.sleep(0.5)
        finally:
            self.controller.end_idle()
        logger.warning("stood on the sigil but the dungeon did not start; will re-arm it")
        self._sigil_failed_at = sigil
        return False

    async def _remember_dungeon(self, outside: str | None, sigil: XYZ):
        """Note where this dungeon's sigil is and where/which way we arrive inside
        (the exit is behind the arrival point): boss farming reuses it."""
        try:
            interior = await self.client.zone_name()
            pos = await self._position()
            yaw = await self.client.body.yaw()
            DungeonMemory.load().record_entry(
                interior,
                DungeonEntry(outside or "", (sigil.x, sigil.y, sigil.z), (pos.x, pos.y, pos.z), yaw),
            )
        except Exception as exc:
            logger.debug(f"could not remember the dungeon: {exc!r}")

    async def _far_spot(self, sigil: XYZ) -> XYZ:
        """An on-map spot well outside the sigil's area: a remembered wisp spot or
        a landmark at least SIGIL_LEAVE away (nearest such), else straight back."""
        zone = await self.client.zone_name() or ""
        candidates = list(wisp_memory().spots.get(zone, [])) + await landmarks(self.client)
        far = [p for p in candidates if math.dist((p[0], p[1]), (sigil.x, sigil.y)) >= SIGIL_LEAVE]
        if far:
            p = min(far, key=lambda p: math.dist((p[0], p[1]), (sigil.x, sigil.y)))
            return XYZ(*p)
        return XYZ(sigil.x + SIGIL_LEAVE, sigil.y, sigil.z)

    async def _walk_in_from_around(self, target: XYZ, zone: str | None) -> bool:
        """Standing on a door marker gives walk_through no direction; back off
        to each side in turn and walk through the marker from there."""
        for i in range(4):
            ang = i * math.pi / 2
            spot = XYZ(target.x + 300 * math.cos(ang), target.y + 300 * math.sin(ang), target.z)
            await self.client.teleport(spot)
            await asyncio.sleep(0.8)
            if await self._zone_changed(zone):
                return True
            if distance(await self._position(), target) < 50:
                continue  # teleport rejected; still on the marker
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
                if await ui.confirm_modal(self.client):
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

    async def _open_quest_book(self) -> bool:
        for _ in range(4):
            if await ui.is_visible(self.client, QUEST_BOOK_ALL):
                break
            await self.client.send_key(Keycode.Q, 0.1)
            await asyncio.sleep(0.8)
        else:
            return False
        await ui.click(self.client, QUEST_BOOK_ALL)
        await asyncio.sleep(0.5)
        return True

    async def _close_quest_book(self):
        for _ in range(4):
            if not await ui.is_visible(self.client, QUEST_BOOK_ALL):
                return
            await self.client.send_key(Keycode.Q, 0.1)
            await asyncio.sleep(0.8)

    async def _read_quest_page(self) -> list[QuestEntry]:
        zone = await self.client.zone_name() or ""
        out = []
        for i in range(MAX_QUEST_SLOTS):
            base = [*QUEST_LIST, f"wndQuestInfo{i}", "questInfoWindow", "wndQuestInfo"]
            name = (await ui.text_at(self.client, [*base, "txtName"])).strip()
            if not name:
                continue
            reward_path = [*base, "wndReward1", "imgReward1Scroll", "txtReward1Amount"]
            reward = await ui.text_at(self.client, reward_path)
            world = (await ui.text_at(self.client, [*base, "txtWorld"])).strip()
            out.append(
                QuestEntry(
                    slot=i,
                    name=name,
                    world=world,
                    hops=hops_to_place(zone, world) if world else None,
                    activity=await ui.is_visible(self.client, [*base, "imgActivityQuestType"]),
                    mainline=await ui.is_visible(self.client, [*base, "LeftMainline"]),
                    active=await ui.is_visible(self.client, [*base, "imgActiveQuest"]),
                    reward=int(reward) if reward.strip().isdigit() else 0,
                )
            )
        return out

    async def prioritize_quests(self) -> bool:
        """Track the quest `choose_quest` picks (keep the current questline unless a
        spell quest is waiting). True if it switched."""
        if not await self._open_quest_book():
            logger.warning("could not open the quest book to rank quests")
            return False
        page_button = [*QUEST_LIST, "btnNextPage"]
        back_button = [*QUEST_LIST, "btnPrevPage"]
        all_quests: list[tuple[int, QuestEntry]] = []
        pages = 0
        try:
            for page in range(5):
                known = {q.name for _, q in all_quests}
                entries = [e for e in await self._read_quest_page() if e.name not in known]
                if not entries:
                    break
                pages = page + 1
                all_quests += [(page, e) for e in entries]
                if not await ui.click(self.client, page_button):
                    break
                await asyncio.sleep(0.6)
            activities = {q.name for _, q in all_quests if q.activity}
            chosen = choose_quest([q for _, q in all_quests])
            best = next(((p, q) for p, q in all_quests if q is chosen), None)
            # A spell quest that left the book was completed: it usually taught a spell.
            finished = self._activity_quests - activities
            if finished and self.progression:
                self.progression.request_check(f"finished {', '.join(sorted(finished))}")
            self._activity_quests = activities
            if best is None:
                return False
            page, entry = best
            if entry.active:
                logger.info(f"quest priority: continuing {entry.name!r}")
                return False
            for _ in range(pages):
                await ui.click(self.client, back_button)
                await asyncio.sleep(0.4)
            for _ in range(page):
                await ui.click(self.client, page_button)
                await asyncio.sleep(0.6)
            info = [*QUEST_LIST, f"wndQuestInfo{entry.slot}", "questInfoWindow", "wndQuestInfo"]
            slot = [*info, "btnActivate"]
            await ui.click(self.client, slot)
            await asyncio.sleep(0.6)
            kind = "spell/activity" if entry.activity else "main story" if entry.mainline else "side"
            where = f"{entry.world}, {entry.hops} hops" if entry.hops is not None else entry.world
            logger.success(f"quest priority: tracking {entry.name!r} ({kind} quest in {where})")
            return True
        finally:
            await self._close_quest_book()

    async def switch_quest(self) -> bool:
        """Track the next quest in the quest book. True if the objective changed."""
        before = await self.objective()
        if not await self._open_quest_book():
            logger.warning("could not open the quest book to switch quests")
            return False

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

        await self._close_quest_book()

        after = await self.objective()
        if clicked and after != before:
            logger.info(f"switched tracked quest: {before!r} -> {after!r}")
            return True
        logger.warning(f"tried to switch quests but the objective is still {after!r}")
        return False

    async def _press_collect(self):
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            # Like NPCs and sigils, the prompt appears on walking into range, not teleporting.
            await self.client.send_key(Keycode.S, 0.3)
            await self.client.send_key(Keycode.W, 0.3)
            await asyncio.sleep(0.5)
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            logger.debug("no collect prompt at the item")
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
        # Nothing nearby: pickups only load close to the wizard, so hop across the
        # zone's landmarks (e.g. Triton's cogs are ~20k units from the entrance).
        if getattr(self, "_far_swept_for", None) != objective:
            self._far_swept_for = objective
            start = await self.client.body.position()
            spots = spread_points(await self._landmarks(), (start.x, start.y, start.z), FAR_SWEEP_SPACING)
            logger.info(f"nothing near here; searching {len(spots)} landmarks across the zone for {item!r}")
            for p in spots[:FAR_SWEEP_MAX]:
                if not await is_free(self.client):
                    return True
                self.controller.allow_idle(10)
                await self.client.teleport(XYZ(*p))
                await asyncio.sleep(1.5)  # let nearby objects stream in
                if await self.collector.collect_once(item, self._press_collect):
                    logger.info(f"found {item!r} near ({p[0]:.0f}, {p[1]:.0f})")
                    return True
            return True
        # Already searched: let the quest marker (if any) guide us, else wait for respawns.
        if distance(await self.client.quest_position.position(), XYZ(0, 0, 0)) < 1:
            logger.debug(f"no {item!r} found; waiting for respawns")
            self.controller.allow_idle(15)
            await asyncio.sleep(10)
            self._swept_for = None
            return True
        return False

    async def _landmarks(self) -> list[tuple[float, float, float]]:
        return await landmarks(self.client)

    async def unneeded_fight(self, battle) -> bool:
        """True if the fight that just started isn't needed for the tracked quest."""
        try:
            objective = await self.objective()
            zone = await self.client.zone_name() or ""
        except Exception:
            return False
        names = [e.name for e in battle.enemies]
        has_boss = any(e.is_boss for e in battle.enemies)
        return not fight_needed(objective, names, zone, has_boss)

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
        for _ in range(3):
            gate = find_zone_gate(objective, zone, self._bad_gates)
            if not gate:
                break
            pos, dest_zone = gate
            logger.info(f"no quest marker for {objective!r}; walking to {dest_zone} via a known gate")
            await self.controller.checkpoint()
            await self.travel(pos)
            if await self.client.zone_name() != zone:
                return True
            # Landing on the gate point doesn't always cross the trigger; walk into it.
            if await self.approach_and_walk(pos, zone):
                return True
            # Some gate entries in the data are wrong; route around this one from now on.
            logger.warning(f"gate {zone} -> {dest_zone} did not work; avoiding it")
            self._bad_gates.add((zone, dest_zone))
        target_zone = objective_zone(objective)
        if target_zone not in (None, zone):
            if any(to == target_zone for _frm, to in self._bad_gates) and not find_zone_gate(
                objective, zone, self._bad_gates
            ):
                # Every way in refused us: the zone is still locked by the story.
                logger.warning(f"{target_zone} looks locked (every gate refused); switching quests")
                return await self.switch_quest()
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
        if self.upkeep and not await recover(self.client, self.upkeep, self.controller, self.go_to_zone):
            return
        if self.gear:
            self.controller.allow_idle(180)  # trying gear on looks like "nothing happening"
            try:
                await self.gear.tick()
            except Exception as exc:
                logger.opt(exception=exc).warning("gear check failed")
            finally:
                self.controller.end_idle()
            if not await is_free(self.client):
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
        if objective != getattr(self, "_ranked_for", None) and (
            time.monotonic() - getattr(self, "_last_rank", -1e9) > RANK_QUESTS_EVERY
        ):
            self._last_rank = time.monotonic()
            self.controller.allow_idle(30)
            try:
                if await self.prioritize_quests():
                    await asyncio.sleep(1.0)
                    objective = await self.objective()
            finally:
                self.controller.end_idle()
            self._ranked_for = objective
        zone = await self.client.zone_name()
        await self._note_progress(objective, zone)
        # Stalled on one objective: make sure we aren't wedged inside a building
        # or wall from a teleport (walking then does nothing).
        now = time.monotonic()
        stalled = now - self._last_progress_time > STUCK_CHECK_AFTER
        if stalled and now - self._last_stuck_check > STUCK_CHECK_EVERY:
            self._last_stuck_check = now
            if await unstick(self.client):
                return
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
        if item:
            # "Collect Cog in Triton Avenue": searching any other zone is pointless.
            where = objective_zone(objective)
            if where and where != zone:
                if gate_toward(zone, where, self._bad_gates):
                    logger.info(f"{objective!r} is in {where}; going there first")
                    await self.go_to_zone(where)
                    return
                # No known route (e.g. inside a building): let the quest marker lead out.
            elif await self.collect(item, objective):
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
            if is_combat_objective(objective) and objective_zone(objective) in (None, zone):
                # "Summon Myth Minion in Unicorn Way": any fight here will do
                # (the brain summons/casts what the objective asks for).
                if not await self.client.in_battle():
                    await self.pull_mob()
                return
            logger.debug(f"no quest marker for {objective!r}; waiting")
            await asyncio.sleep(2.0)
            return

        logger.info(f"[{zone}] {objective}")
        await self.controller.checkpoint()
        near = distance(await self._position(), target) < 3000
        sigil = await self._sigil_at(target) if near else None
        if sigil is not None:
            await self._enter_by_sigil(sigil, zone)
            return
        await self.travel(target)
        if not await wait_until_free(self.client, timeout=5):
            return  # a fight or dialogue started on arrival

        dist = distance(await self.client.body.position(), target)
        if dist < INTERACT_RANGE and await self.interact(objective):
            await self._count_attempt()
            return
        if dist < INTERACT_RANGE and "talk" in objective.lower():
            # NPC prompts appear on walking into range, not on teleporting there.
            await self.client.send_key(Keycode.S, 0.3)
            await self.client.send_key(Keycode.W, 0.3)
            await asyncio.sleep(0.5)
            if await self.interact(objective):
                await self._count_attempt()
                return

        if is_combat_objective(objective):
            if not await self.client.in_battle():
                await self.pull_mob()
            return

        if dist < DOOR_RANGE and await self.client.zone_name() == zone:
            # Standing on the marker with nothing to interact with: it's most
            # likely a door or zone exit, which needs walking into.
            if await self.walk_through(target, zone) or await self._walk_in_from_around(target, zone):
                logger.info("walked through a door")
