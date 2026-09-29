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
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .collect import (
    Collector,
    away_from,
    collect_item_name,
    floor_points,
    landmarks,
    matches_item,
    path_points,
    spread_points,
)
from .config import QuestConfig
from .deck import close_spellbook
from .dungeons import DungeonEntry, DungeonMemory
from .entitymap import DoorMemory, EntityMap
from .entitymap import scan as scan_entities
from .givers import QuestGivers
from .marks import RETURN_KINDS, Mark, load_mark, recall_is_faster, save_mark, should_travel_mark
from .npc import ServicesMenu
from .questlist import CompletionTracker, load_quest_list, norm
from .safe_teleport import allow_close_landing, allow_engage, teleport_aborted
from .setbacks import DEFEATS_TO_DEFER, MAIN_DEFEATS_TO_DEFER, Setbacks
from .travel_data import (
    find_zone_gate,
    gate_behind,
    gate_kind,
    gate_toward,
    hops_to_place,
    learn_gate,
    objective_zone,
    press_x_gate,
    quest_spots,
    ride_gate,
    zone_hops,
    zones_near,
)
from .upkeep import (
    clear_popups,
    health_mana,
    is_free,
    mob_positions,
    move_to_safety,
    recover,
    scan_wisps,
    unstick,
    wait_for_loading,
    wait_until_free,
    wisp_memory,
)
from .wisps import sweep_points

INTERACT_RANGE = 750.0
# Marks go down only before a dungeon (its sigil) and before a heal trip; the
# travel and fight marks kept replacing the one that mattered.
TRAVEL_AND_FIGHT_MARKS = False
DEFEAT_NO_MARK_SECONDS = 120.0  # right after a defeat the wizard stands in the hub: no heal marks
NPC_INCH_RANGE = 1500.0  # a refused teleport this close to an NPC: step closer; farther off, it's a door
EXPOSED_RADIUS = 1200.0  # standing still (menus, marking) this close to an enemy invites a fight
BOUNCE_DISTANCE = 20.0
WISP_SCAN_SECONDS = 30.0
STATUS_EVERY_SECONDS = 20.0
SWITCH_QUEST_AFTER = 4  # same objective, this many interactions without change
MAX_QUEST_SLOTS = 6
RANK_QUESTS_EVERY = 60.0  # at most this often: quest-book rankings (on objective changes)
# Quest book window paths (mapped by Deimos).
QUEST_LIST = ["WorldView", "DeckConfiguration", "wndQuestList"]
QUEST_BOOK_ALL = [*QUEST_LIST, "QuestLogAllButton"]
DOOR_RANGE = 300.0  # at the marker with no prompt: probably a doorway
DOOR_OVERSHOOT = 200.0
WALK_STEP_SECONDS = 0.25  # walking into a door in short steps, checking the zone after each
WALK_MAX_STEPS = 40
APPROACH_DISTANCES = (250.0, 450.0, 700.0)
SIGIL_RANGE = 150.0  # a dungeon sigil this close to the marker is the way in
# Standing at a "press X to enter" prompt: a sigil this close is the one (a
# 4-player sigil's circles spread far from its center).
SIGIL_NEAR_RANGE = 800.0
SIGIL_WAIT_TICKS = 30  # half-seconds to stand still after one X at an entry prompt
STUCK_CHECK_AFTER = 20.0  # seconds on one objective before checking we can still walk
STUCK_CHECK_EVERY = 30.0
UNREACHED_BEFORE_FIGHT = 2  # failed approaches to an in-dungeon marker before fighting to open a gate
FLEES_BEFORE_FIGHTING = 2  # after fleeing the same enemies this often on one objective, fight
WANTED_SCAN_SECONDS = 8.0  # how often to look for wanted collect items in view
WINS_COUNT_AS_PROGRESS = 5  # won fights without the objective moving that still count
STUCK_RETRY_SECONDS = 1800.0  # a quest set aside for being stuck (not beaten) is tried again after this
BOSS_CHEST_SETTLE_SECONDS = 3.0  # after a boss fight, before teleporting to its chest
GRIND_RERANK_SECONDS = 90.0  # while grinding, re-read the quest book this often
ALERT_REPEAT_SECONDS = 3600.0  # the same main quest is alerted about at most hourly
STALL_SWITCH_SECONDS = 180.0  # no objective change and no won fight: follow another quest
# Attempts at one approach (per objective and zone) before it's skipped for
# the next one; when every approach is used up the quest is set aside.
APPROACH_LIMITS = {"marker_x": 2, "walk": 2, "teleporter": 3, "sweep": 2, "inch": 2}
WALK_LEG = 1500.0  # teleport hops toward a far marker, a look for the target after each
WALK_LEGS = 25
RECALL_WAIT = 12.0  # seconds after clicking Recall for the zone to change
RECALL_RETRY_SECONDS = 600.0  # after a travel recall fails (cooldown, refused), walk for a while
MARKER_WALK_RANGE = 800.0  # this close to the marker with the named enemy missing: walk onto it
MARKER_WALK_BACK = 350.0  # how far to back off before walking onto the marker
SIGIL_WAIT = 25.0  # the countdown after pressing X is ~10s
SIGIL_LEAVE_MOB_DISTANCE = 1000.0  # re-arm spots must be this clear of mobs
SIGIL_LEAVE = 3000.0  # the prompt re-arms only after leaving this far (~20m in game)
FAR_SWEEP_SPACING = 3000.0  # pickups load within roughly this range
FAR_SWEEP_MAX = 25
ENTITY_SCAN_SECONDS = 30.0  # how often to note what's around (for the entity map)
KNOWN_SPOTS_FIRST = 6  # remembered spots tried before a zone sweep
MARKER_WAY_RANGE = 600.0  # at a fight's marker with the enemy absent: look for an X prompt there
PUZZLE_NEAR = 2000.0  # this close to the marker with its 'Use X' missing: a switch puzzle
PRESS_X_TRIES = 4  # X presses at a prompt before moving on
TRACK_TRIES = 3  # clicks on a quest's track button before giving up for this ranking
COLLECT_SEARCH_DEPTH = 2  # search zones up to this many gates from the objective's place
ENEMY_SWEEP_SPACING = 2500.0  # enemies load within roughly this range


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
    goal: str = ""  # current objective shown in the book, e.g. "Talk To Private Stillson"
    zone: str = ""  # the book's world name for the area, e.g. "Marleybone"
    target: str = ""  # the step's target as the book shows it ("Ms. Conrail"), no verb
    fight: bool = False  # the book shows the encounter icon: this step is a fight
    counted: bool = False  # the step has a counter ("0 of 3"): collect/defeat several


UNKNOWN_HOPS = 5  # an area we can't route to counts as fairly far


# Wizard City's street areas in the order the story opens them. Quests are
# cleared area by area, earliest first; hubs (Commons, Ravenwood, Shopping
# District, Olde Town...) aren't listed and count as the current area.
AREA_ORDER = (
    "unicorn way",
    "cyclops lane",
    "firecat alley",
    "triton avenue",
    "olde town",
    "haunted cave",
    "firefly forest",
    "colossus boulevard",
    "sunken city",
    "golem court",
    "storm tower",
    "lost city",
)


def area_rank(world: str) -> int | None:
    """Position of a quest's area in AREA_ORDER (None: a hub or unknown place)."""
    w = world.lower()
    return next((i for i, a in enumerate(AREA_ORDER) if a in w), None)


def _area_of(q: QuestEntry, order: dict) -> int | None:
    """A listed quest belongs to its list area (the book's "world" only shows
    where its current step is); others to the area the book shows."""
    listed = order.get(norm(q.name))
    return area_rank(listed.area if listed else q.world)


def quest_rank(q: QuestEntry, current_area: int = 0, order: dict | None = None) -> tuple:
    """Higher is better. Spell quests make the wizard stronger; then the earliest
    area is cleared first; within an area, quests without a fight (talk, go to,
    collect) are quick experience; quests from docs/QuestList.txt go in list
    order; nearer beats farther; the tracked quest wins ties so the bot doesn't
    flip between equals."""
    order = order or {}
    hops = UNKNOWN_HOPS if q.hops is None else q.hops
    area = _area_of(q, order)
    area = current_area if area is None else area
    easy = bool(q.goal) and not is_combat_objective(q.goal)
    listed = order.get(norm(q.name))
    position = -listed.index if listed else -10_000
    return (q.activity, -area, easy, position, -hops, q.active, q.mainline, q.reward)


def quest_world(q: QuestEntry) -> str | None:
    """The world ("Krokotopia") a quest's area is in, from the book's area name."""
    zone = objective_zone(q.world) if q.world else None
    if zone:
        return zone.split("/", 1)[0]
    return q.zone.replace(" ", "") or None


def same_world(a: str | None, b: str | None) -> bool:
    """World ids and the book's names differ in spaces/case ("WizardCity", "Wizard City")."""
    return bool(a and b) and a.replace(" ", "").lower() == b.replace(" ", "").lower()


def choose_quest(
    quests: list[QuestEntry],
    set_aside: set[str] = frozenset(),
    order: dict | None = None,
    world: str | None = None,
) -> QuestEntry | None:
    """Which quest to track. Only the main story (and spell/class quests, which
    teach spells) while one of those can be worked on; side quests only when
    every main quest is set aside (e.g. a boss that keeps winning), to gain a
    level before trying again. Within that pool: spell quests first, then the
    earliest area, easy objectives first, listed quests in list order."""
    order = order or {}
    available = [q for q in quests if q.name not in set_aside]
    # The main story: flagged in the book, spell/class quests, or on the quest list.
    main = [q for q in available if q.mainline or q.activity or norm(q.name) in order]
    if not main and available:
        # Filling in with side quests while the main story waits for a level:
        # stay in this world (no trips back to Wizard City), finish the tracked
        # one before picking another, and prefer the biggest reward (experience).
        here = [q for q in available if quest_world(q) == world] if world else available
        if not here:
            return None  # nothing worth doing in this world: the caller grinds there
        active = next((q for q in here if q.active), None)
        if active:
            return active
        return max(
            here,
            key=lambda q: (
                q.reward,
                bool(q.goal) and not is_combat_objective(q.goal),  # quick, no fight
                -(UNKNOWN_HOPS if q.hops is None else q.hops),
            ),
        )
    quests = main or available or quests
    if not quests:
        return None
    areas = [a for a in (_area_of(q, order) for q in quests) if a is not None]
    current = min(areas) if areas else 0
    return max(quests, key=lambda q: quest_rank(q, current, order))


# Objectives with no fight: talking to someone (a turn-in), going somewhere,
# using or finding something. "Collect" is left out (items often drop from
# enemies, and one can take many fights).
ERRAND_VERBS = (
    "talk", "speak", "go", "use", "find", "explore", "visit", "read", "locate", "return", "deliver",
)
ERRAND_MAX_HOPS = 3  # gates away at most: a detour, not a trip
# The book's area names ("Regent's Square Hat Shop") often have no known route:
# in the main quest's world such an errand counts as this far.
ERRAND_UNKNOWN_HOPS = ERRAND_MAX_HOPS


def is_errand(goal: str) -> bool:
    """A quick step with no fight ('Talk To Sergeant Major Talbot')."""
    words = (goal or "").strip().lower().split()
    return bool(words) and words[0] in ERRAND_VERBS and not is_combat_objective(goal)


def quest_is_errand(q: QuestEntry) -> bool:
    """The quest's current step needs no fight: a worded goal ("Talk To X")
    judged by its verb, "Complete" (just hand it in), or the book's bare
    target ("Ms. Conrail") with no encounter icon and no counter."""
    goal = (q.goal or "").strip()
    if goal.lower() == "complete":
        return True
    if goal:
        return is_errand(goal)
    return bool(q.target) and not q.fight and not q.counted


def errand_detour(
    quests: list[QuestEntry], chosen: QuestEntry | None, set_aside: set[str] = frozenset(),
    world: str | None = None, max_hops: int = ERRAND_MAX_HOPS,
) -> QuestEntry | None:
    """A side quest whose current step is a quick no-fight errand close by (a
    turn-in, a talk): worth doing before going on with the main quest. Only
    in `world`, the main quest's (furthest) world: no trips back to earlier
    worlds. The nearest first, then the biggest reward. None when there's none."""
    if chosen is None or not world:
        return None
    options = [
        q for q in quests
        if q is not chosen and not q.mainline and not q.activity and q.name not in set_aside
        and quest_is_errand(q) and _errand_hops(q) <= max_hops
        and same_world(quest_world(q), world)
    ]
    if not options:
        return None
    return min(options, key=lambda q: (_errand_hops(q), -q.reward, not q.active))


def _errand_hops(q: QuestEntry) -> int:
    return ERRAND_UNKNOWN_HOPS if q.hops is None else q.hops


def dungeon_quest(
    quests: list[QuestEntry], zone: str, zone_of, set_aside: set[str] = frozenset()
) -> QuestEntry | None:
    """Inside a dungeon, a side quest set there (its book area is this dungeon,
    e.g. one handed out on entering) comes before the main quest: the main
    objective usually waits on it (a gate, a puzzle, an NPC to free)."""
    local = [
        q for q in quests
        if not q.mainline and q.name not in set_aside and q.world and zone_of(q.world) == zone
    ]
    if not local:
        return None
    return next((q for q in local if q.active), local[0])


EVADE_DISTANCE = 600.0  # an enemy this close to where we landed: move before it engages
MOB_CLEARANCE = 700.0  # landing closer than this to an enemy tends to start a fight
LANDING_RADII = (350.0, 600.0, 900.0, 1300.0, 1800.0)


def clear_of(p: XYZ, mobs: list[XYZ], clearance: float) -> bool:
    return all(math.dist((p.x, p.y), (m.x, m.y)) > clearance for m in mobs)


def safe_landing(target: XYZ, start: XYZ, mobs: list[XYZ], clearance: float) -> XYZ | None:
    """The nearest spot around `target` with no enemy within `clearance`,
    preferring the side we come from (the walk in then passes fewer enemies)."""
    dx, dy = start.x - target.x, start.y - target.y
    home = math.atan2(dy, dx) if (dx or dy) else 0.0
    for radius in LANDING_RADII:
        options = []
        for i in range(16):
            ang = home + i * math.pi / 8
            p = XYZ(target.x + radius * math.cos(ang), target.y + radius * math.sin(ang), target.z)
            if clear_of(p, mobs, clearance):
                options.append((abs(math.remainder(ang - home, 2 * math.pi)), p))
        if options:
            return min(options, key=lambda o: o[0])[1]
    return None


def defeat_target(objective: str) -> str | None:
    """The enemy a "Defeat X in Place (0 of 2)" objective names, else None."""
    m = re.match(r"^\s*defeat\s+(.+?)(?:\s+in\s+[^()]+)?(?:\s*\(\d+ of \d+\))?\s*$", objective, re.I)
    if not m:
        return None
    target = re.split(r"\s+and\s+", m.group(1), maxsplit=1)[0].strip()
    target = re.sub(r"^(any|a|an|the)\s+", "", target, flags=re.I)  # "Defeat Any Nirini"
    return target or None


def talk_target(objective: str) -> str | None:
    """The one a "Talk To Willie Marks in Willie's Clocktower" objective names."""
    m = re.match(r"^\s*talk\s+to\s+(.+?)(?:\s+in\s+.+)?\s*$", objective, re.I)
    return m.group(1).strip() if m else None


def is_hub(zone: str) -> bool:
    """A world's hub (the Oasis, the Commons...): "Krokotopia/KT_Hub"."""
    return zone.split("/")[-1].endswith("_Hub")


def defeat_names(objective: str) -> list[str]:
    """Names an enemy may have to count for a "Defeat X" objective, most
    specific first. "Defeat Any Sphinx Sokkwi" takes any Sokkwi (a Sokkwi
    Crusher counts): the last word, the creature kind, matches too."""
    target = defeat_target(objective)
    if not target:
        return []
    names = [target]
    words = target.split()
    if re.match(r"^\s*defeat\s+any\s", objective, re.I) and len(words) > 1:
        names.append(words[-1])
    return names


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
    wanted = defeat_names(objective)
    if not wanted:
        return True  # "Summon/Cast ...": any fight does
    for name in wanted:
        target = _norm_name(name).removesuffix("s")
        if len(target) < 3:
            return True
        if any(target in _norm_name(n) or _norm_name(n) in target for n in enemy_names):
            return True
    return False


def distance(a: XYZ, b: XYZ) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


QUEST_BOOK_FILE = Path("state") / "quest_book.json"
PIN_FILE = Path("state") / "quest_pin.json"


def load_pin() -> str:
    try:
        return json.loads(PIN_FILE.read_text(encoding="utf-8")).get("quest", "")
    except (OSError, ValueError, AttributeError):
        return ""


def save_pin(name: str):
    try:
        PIN_FILE.parent.mkdir(exist_ok=True)
        PIN_FILE.write_text(json.dumps({"quest": name}), encoding="utf-8")
    except OSError:
        pass


def _write_quest_book(quests: list[QuestEntry], chosen: QuestEntry | None, world: str | None):
    """The quest book as last read, for the dashboard (state/quest_book.json)."""
    try:
        data = {
            "time": time.time(),
            "world": world or "",
            "tracking": chosen.name if chosen else "",
            "tracking_area": chosen.world if chosen else "",
            "quests": [
                {"name": q.name, "area": q.world, "main": q.mainline, "spell": q.activity, "goal": q.goal}
                for q in quests
            ],
        }
        QUEST_BOOK_FILE.parent.mkdir(exist_ok=True)
        QUEST_BOOK_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


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
        self.givers = QuestGivers(self)  # talks to named NPCs nearby once, for their quests
        self.cfg = cfg
        self.controller = controller
        self.sprinter = client  # SprintyClient (bot.new_handler)
        self._last_progress = (None, None)
        self._last_progress_time = time.monotonic()
        self.objectives_completed = 0
        self.gear = None  # GearManager, set by the bot
        self.trainer = None  # SpellTrainer, set by the bot
        self._activity_quests: set[str] = set()  # spell quests seen in the book
        self._sigil_failed_at: XYZ | None = None  # sigil whose last try didn't start
        self._last_stuck_check = 0.0
        self.setbacks = Setbacks.load()
        self.quest_order = load_quest_list()  # docs/QuestList.txt
        self.completions = CompletionTracker()  # -> docs/CompletedQuests.txt
        self._active_quest: str | None = None  # tracked quest's name, from the quest book
        self._seen_deaths = 0
        self._recall_pending = False  # a defeat happened since we marked a dungeon entrance / fight spot
        self._last_defeat = -1e9
        self._last_win_zone = ""  # where a fight was last won (to gain experience there)
        self._book_dumped = False  # quest book slot layout saved (state/quest_book_window.txt)
        self._boss_fights_seen = 0  # fighter.boss_fights already checked for a loot chest
        self._alerted: dict[str, float] = {}  # main quest -> last ALERT (monotonic)
        self._boss_deaths_seen = 0
        self._grinding = False  # every quest set aside: fight for experience until a level-up
        self._main_world: str | None = None  # the world the main quest is in (side quests stay there)
        self._fled: dict[tuple, int] = {}  # (objective, enemy names) -> times fled
        self._mainline: set[str] = set()  # main-story quests in the book (from the last ranking)
        self._wanted_items: dict[str, str] = {}  # item -> quest, from "Collect X" goals in the book
        self._last_wanted_scan = 0.0
        self._last_loot_scan = 0.0
        self.entity_map = EntityMap()  # what was seen where (targeted searches)
        self.doors = DoorMemory()  # where walking into a door worked
        self._last_entity_scan = 0.0
        self._attempts_at: dict[tuple[str, str, str], int] = {}  # (objective, zone, approach) -> tries
        self._swept_spots: dict[tuple[str, str], list] = {}  # (objective, zone) -> sweep spots visited
        self._puzzles_tried: set[tuple[str, str]] = set()  # (objective, zone) switch puzzles tried
        self._teleporters_tried: dict[tuple[str, str], set[str]] = {}  # (objective, zone) -> labels
        self._accepted_seen = 0  # DialoguePolicy.accepted at the last ranking
        self._pin: str | None = None  # the player's picked quest (None: not read yet this session)
        self._zones_searched: dict[str, set[str]] = {}  # collect objective -> zones swept for it
        self.fighter = None  # set by the bot: its fight count tells won fights apart
        self._fights_seen = 0
        self._deaths_at_fight = 0
        self._stall_switched_for = ""  # objective whose quest was set aside for stalling
        self._wins_since_progress = 0
        self._unreached: dict[tuple[str, str], int] = {}  # (objective, zone) -> failed approaches
        self._zone_before = ""  # for learning gates on arrival
        self._deaths_before = 0
        self._teleported = False  # a recall moved us: not a gate
        self._mark: Mark | None = load_mark()  # where the game's Mark is (dungeon sigil or travel spot)
        self._recall_blocked_until = 0.0  # after a refused/failed travel recall
        self.healer = None  # DungeonHealer, set by the bot
        self._dungeon: tuple[str, str] | None = None  # (outside zone, first room) of the dungeon we're in
        self._recalled_for = ""  # objective a travel recall was used for (once each)
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
            self._wins_since_progress = 0
            return
        # A won fight counts as progress (drop hunts take many fights per item).
        fights = self.fighter.fights if self.fighter else 0
        if fights != self._fights_seen:
            won = self.controller.deaths == self._deaths_at_fight
            self._fights_seen, self._deaths_at_fight = fights, self.controller.deaths
            if won:
                self._last_win_zone = await self.client.zone_name() or self._last_win_zone
                self._wins_since_progress += 1
                # Up to a few won fights count as progress (a drop hunt needs
                # several); more without the objective moving means these
                # enemies aren't the ones that drop it.
                if self._wins_since_progress <= WINS_COUNT_AS_PROGRESS or self._grinding:
                    self._last_progress_time = time.monotonic()
                    return
        waited = time.monotonic() - self._last_progress_time
        if waited > STALL_SWITCH_SECONDS and self._stall_switched_for != objective:
            # Stuck without a way forward: follow the next best quest instead of
            # stalling; this one comes back after a level-up or an hour.
            self._stall_switched_for = objective
            quest = self._active_quest
            if quest:
                level = await self.client.stats.reference_level()
                self.setbacks.set_quest_aside(
                    quest, objective, level, main=quest in self._mainline, retry_after=STUCK_RETRY_SECONDS
                )
                self.setbacks.save()
                logger.warning(
                    f"no progress on {objective!r} for {waited / 60:.0f} min: setting {quest!r} aside "
                    "and following the next best quest"
                )
                if quest in self._mainline:
                    self._alert_main_stuck(quest, f"no progress on {objective!r} for {waited / 60:.0f} min")
                self._ranked_for = None
                self._last_rank = -1e9  # re-rank on this step
            return
        if waited > self.cfg.stuck_minutes * 60:
            self.controller.stop(f"no quest progress for {self.cfg.stuck_minutes} min on {objective!r}")

    # --- movement ------------------------------------------------------------

    async def _position(self) -> XYZ:
        return await self.client.body.position()

    async def _zone_changed(self, zone: str | None) -> bool:
        await wait_for_loading(self.client, appear_timeout=0.8)
        if await self.client.zone_name() != zone:
            # Doors drop us wherever the game likes, and the new zone's enemies
            # take a moment to load: look a few times before moving on.
            for _ in range(3):
                if await self._clear_of_enemies() or await self.client.in_battle():
                    break
                await asyncio.sleep(0.8)
            return True
        return False

    async def _clear_of_enemies(self) -> bool:
        """Check where we ended up (after a teleport or a door): if an enemy is
        right there and no fight has started yet, hop to the nearest clear spot
        before it engages (Desert Golems patrol the Palace of Fire's entrance).
        True if it moved."""
        try:
            if await self.client.in_battle():
                return False
            me = await self._position()
            mobs = [XYZ(*m) for m in await mob_positions(self.client)]
            if not mobs or clear_of(me, mobs, EVADE_DISTANCE):
                return False
            spot = safe_landing(me, me, mobs, MOB_CLEARANCE)
            if spot is None:
                return False
            logger.info(f"enemies right where we landed; moving {distance(spot, me):.0f} away")
            await self.client.teleport(spot)
            await asyncio.sleep(0.5)
            return True
        except Exception as exc:
            logger.debug(f"clear-of-enemies check failed: {exc!r}")
            return False

    async def walk_through(self, target: XYZ, zone: str | None, overshoot: float = DOOR_OVERSHOOT) -> bool:
        """Walk straight at `target` and a little past it.

        Doors and zone exits only trigger when you walk into them; teleporting
        onto one gets rejected by the game and snaps you back.
        """
        # A "press X" spot (the Balance School's ladder): use it rather than
        # walking past it.
        if await self._press_x_here(zone):
            return True
        pos = await self._position()
        dx, dy = target.x - pos.x, target.y - pos.y
        length = math.hypot(dx, dy)
        if length < 1:
            return False
        beyond = XYZ(target.x + dx / length * overshoot, target.y + dy / length * overshoot, target.z)
        logger.debug(f"walking through objective ({length:.0f} units + {overshoot:.0f} overshoot)")
        # Walk in short steps and stop the moment the zone changes: one long
        # key press kept walking on the far side of the door, straight into the
        # Desert Golems at the Palace of Fire's entrance.
        from wizwalker.utils import calculate_perfect_yaw

        await self.client.body.write_yaw(calculate_perfect_yaw(pos, beyond))
        last = pos
        for _ in range(WALK_MAX_STEPS):
            await self.client.send_key(Keycode.W, WALK_STEP_SECONDS)
            if await self.client.zone_name() != zone or await self.client.is_loading():
                break
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                return await self._press_x_here(zone, adjust=False)  # walked into a prompt: stop, use it
            now = await self._position()
            if distance(now, beyond) < 60 or distance(now, last) < 5:
                break  # there, or blocked by a wall
            last = now
        if await self._zone_changed(zone):
            if zone:
                self.doors.record(zone, (target.x, target.y, target.z), (pos.x, pos.y, pos.z))
            return True
        return False

    async def _press_x_here(self, zone: str | None, adjust: bool = True) -> bool:
        """Use a "press X" prompt at this spot (a ladder, a door that asks),
        pressing X a few times and waiting for the zone to change. With no
        prompt, `adjust` makes small moves first (a step back and forward, a
        small turn each way) looking for one. True once through."""
        nudges = (
            (Keycode.S, 0.15), (Keycode.W, 0.25), (Keycode.A, 0.12), (Keycode.D, 0.24), (Keycode.A, 0.12),
        ) if adjust else ()
        for nudge in (None, *nudges):
            if nudge is not None:
                await self.client.send_key(*nudge)
                await asyncio.sleep(0.25)
            if not await ui.is_visible(self.client, ui.NPC_RANGE):
                continue
            prompt = (await ui.text_at(self.client, ui.NPC_RANGE_TEXT)).lower()
            entering = "to enter" in prompt
            if entering:
                # A dungeon sigil (the Hyde Park safehouses): ONE press starts
                # a countdown that a second press or any step cancels.
                sigil = await self._sigil_at(await self._position(), SIGIL_NEAR_RANGE)
                if sigil is not None:
                    return await self._enter_by_sigil(sigil, zone)
            for _ in range(1 if entering else PRESS_X_TRIES):
                await self.client.send_key(Keycode.X, 0.1)
                for _ in range(SIGIL_WAIT_TICKS if entering else 6):
                    await asyncio.sleep(0.5)
                    if await self._zone_changed(zone) or await self.client.is_loading():
                        await wait_for_loading(self.client)
                        logger.info("used the 'press X' prompt here")
                        return True
                if not await ui.is_visible(self.client, ui.NPC_RANGE):
                    break
            if not await is_free(self.client):
                return True  # a dialogue or menu opened: the step takes it from here
        return await self._zone_changed(zone)

    async def approach_and_walk(self, target: XYZ, zone: str | None) -> bool:
        """Teleport to a spot in front of `target` (on the side we came from), then walk in."""
        pos = await self._position()
        dx, dy = pos.x - target.x, pos.y - target.y
        length = math.hypot(dx, dy)
        ux, uy = (dx / length, dy / length) if length > 1 else (1.0, 0.0)
        for back in APPROACH_DISTANCES:
            spot = XYZ(target.x + ux * back, target.y + uy * back, target.z)
            if not await self._clear_spot(spot):
                continue  # an enemy stands there: landing on it starts a fight
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
            kind = gate_kind(zone or "", next_zone)
            if press_x_gate(kind):
                # A boat, an NPC or a door that asks: stand there and press X.
                if not await self._use_x_gate(pos, zone or "", next_zone, ride_gate(kind)):
                    logger.warning(f"gate {zone} -> {next_zone} ({kind}) did not work; avoiding it")
                    self._bad_gates.add((zone, next_zone))
                await wait_for_loading(self.client)
                continue
            await self.travel(pos)
            if await self.client.zone_name() == zone and not await self.approach_and_walk(pos, zone):
                logger.warning(f"gate {zone} -> {next_zone} did not work; avoiding it")
                self._bad_gates.add((zone, next_zone))
            await wait_for_loading(self.client)
        return await self.client.zone_name() == dest

    async def _use_x_gate(self, pos: XYZ, zone: str, next_zone: str, ride: bool) -> bool:
        """Use a gate by pressing X at it (WizSprinter's xNoWait/xSkipRide
        types). A ride (the Krokotopia boat) goes through a ride zone first,
        where another X skips the ride. True once in `next_zone`."""
        await self.client.teleport(pos)
        await asyncio.sleep(1.5)  # a vendor stands by the boat: let the boat's prompt come up
        for _leg in range(2 if ride else 1):
            here = await self.client.zone_name()
            pressed = False
            for _ in range(12):
                if await ui.is_visible(self.client, ui.NPC_RANGE):
                    await self.client.send_key(Keycode.X, 0.1)
                    pressed = True
                    await asyncio.sleep(0.6)
                    continue
                if pressed or await self.client.zone_name() != here:
                    break
                await asyncio.sleep(0.5)
            await wait_for_loading(self.client, appear_timeout=8.0)
            self._teleported = True  # not a walk-through gate: don't learn it
            if await self.client.zone_name() == next_zone:
                return True
        return await self.client.zone_name() == next_zone

    async def _sigil_at(self, target: XYZ, within: float = SIGIL_RANGE) -> XYZ | None:
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
                if distance(pos, target) < within:
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
        if not await is_free(self.client):
            logger.info("pulled into a fight near the sigil; will try again after it")
            self._sigil_failed_at = sigil
            return False
        self.controller.allow_idle(SIGIL_WAIT + 10)
        try:
            # Mark the entrance: a solo dungeon resets the moment we're defeated,
            # so a mark inside is useless, but Recall to the sigil saves the walk.
            await self._mark_here()
            await asyncio.sleep(0.5)
            logger.info(f"on the dungeon sigil; pressing X once and waiting up to {SIGIL_WAIT:.0f}s")
            await self.client.send_key(Keycode.X, 0.1)
            started = time.monotonic()
            deadline = started + SIGIL_WAIT
            seen_box = ""
            while time.monotonic() < deadline:
                if await self.client.is_loading() or await self.client.zone_name() != zone:
                    await wait_for_loading(self.client)
                    logger.success("entered the dungeon")
                    self._dungeon = (zone or "", await self.client.zone_name() or "")
                    self._sigil_failed_at = None
                    await self._remember_dungeon(zone, sigil)
                    return True
                if await self.client.in_battle():
                    waited = time.monotonic() - started
                    logger.warning(f"a fight started {waited:.0f}s into the sigil countdown")
                    break
                # Don't click anything during the countdown (a blind click on a
                # message box could cancel it); just record what shows up.
                box = await ui.modal_box(self.client)
                text = (await ui.modal_text(box)) if box else ""
                if text and text != seen_box:
                    seen_box = text
                    waited = time.monotonic() - started
                    logger.warning(f"message box {waited:.0f}s into the sigil countdown: {text[:120]!r}")
                if text and "about to enter a dungeon" in text.lower():
                    # The dungeon notice waits for OK before the countdown goes on.
                    await ui.press_modal_button(self.client, box, "centerButton")
                await asyncio.sleep(0.5)
        finally:
            self.controller.end_idle()
        logger.warning("stood on the sigil but the dungeon did not start; will re-arm it")
        self._sigil_failed_at = sigil
        return False

    async def _mark_here(self, kind: str = "dungeon", objective: str | None = None) -> bool:
        """Mark this spot. A dungeon's sigil: after a defeat and healing, Recall
        brings us straight back instead of walking across the world again. A
        travel mark: a later objective near it is reached by Recall."""
        try:
            objective = await self.objective() if objective is None else objective
            zone = await self.client.zone_name() or ""
            if is_hub(zone):
                # Never mark a hub: a defeat or the hub button brings us here
                # anyway, and it would overwrite the mark that matters.
                logger.debug(f"not marking in the hub {zone}")
                return False
            if kind != "dungeon":  # a dungeon mark belongs on its sigil
                await move_to_safety(self.client, EXPOSED_RADIUS, "before marking")
            if not await ui.click_named(self.client, "MarkButton"):
                logger.debug("no Mark button to click")
                return False
            await asyncio.sleep(1.0)
            await ui.confirm_modal(self.client)
            self._mark = Mark(zone, objective, kind)
            save_mark(self._mark)
            if kind == "dungeon":
                logger.info(f"marked the dungeon entrance in {zone} (for a quick return after a defeat)")
            elif kind == "fight":
                logger.info(f"marked this spot in {zone} before the fight (Recall back here after a defeat)")
            elif kind == "room":
                logger.info(f"marked this spot in {zone} (Recall back here after healing)")
            else:
                logger.info(f"marked this spot in {zone} before a long trip (Recall when it's on the way)")
            return True
        except Exception as exc:
            logger.debug(f"marking failed: {exc!r}")
            return False

    async def _mark_for_fight(self, objective: str, zone: str):
        """Reaching a fight objective's zone: mark the spot once, so a defeat
        is followed by a Recall here instead of the long walk back.
        Off: marks are only placed before a dungeon and before a heal trip."""
        if not TRAVEL_AND_FIGHT_MARKS:
            return
        m = self._mark
        if m and m.kind in RETURN_KINDS and m.objective == objective and m.zone == zone:
            return
        if m and m.kind == "dungeon" and self._keep_dungeon_mark(objective):
            return  # a dungeon's sigil mark still matters more
        if self._recall_pending or await self._in_dungeon(zone):
            return  # inside a dungeon a defeat resets it: its sigil is marked instead
        await self._mark_here("fight", objective=objective)

    async def _heal_mark(self) -> bool:
        """Before healing: mark the spot, to Recall back after healing from
        the hub. Not while a dungeon mark waits for its Recall (a defeat)."""
        if not self.healer or self._recall_pending or self.healer.busy:
            return False  # (a heal trip is under way: its mark is placed)
        zone = await self.client.zone_name() or ""
        if time.monotonic() - self._last_defeat < DEFEAT_NO_MARK_SECONDS or is_hub(zone):
            return False  # just respawned in the hub: a mark here is useless
        if self._mark and self._mark.kind in RETURN_KINDS and self._keep_dungeon_mark(await self.objective()):
            return True  # a fight/dungeon mark for this objective waits: heal trips Recall to it
        if await self._fight_mark_here():
            return True  # the fight mark does the job: healing Recalls back to it
        return await self._mark_here("room")

    async def _fight_mark_here(self) -> bool:
        """Is the mark a fight mark for the current objective in this zone?"""
        m = self._mark
        if not m or m.kind != "fight":
            return False
        return m.objective == await self.objective() and m.zone == await self.client.zone_name()

    async def _heal_trip(self, force: bool = False, marked: bool = False) -> bool:
        """This zone lacks what recovery needs: heal from the hub and Recall
        back instead of walking out and back through the gates. Goes when the
        spot is marked already, the objective keeps us here, or `force`
        (resting here gave nothing at all)."""
        if not self.healer:
            return False
        zone = await self.client.zone_name() or ""
        if is_hub(zone) or time.monotonic() - self._last_defeat < DEFEAT_NO_MARK_SECONDS:
            # Already at the hub (a defeat sends us here): a trip there is
            # pointless; recovery goes to a zone with wisps instead.
            return False
        objective = await self.objective()
        dest = objective_zone(objective) if objective else None
        coming_back = dest == zone or (dest is None and is_combat_objective(objective or ""))
        if not zone or not (marked or coming_back or force):
            return False
        # Never over a fight or dungeon mark still wanted for this objective:
        # the trip Recalls to it instead. An old one (Willie Marks's, done) is
        # replaced by a mark here.
        keep = await self._fight_mark_here() or self._keep_dungeon_mark(objective or "")
        why = f"not enough wisps here for {objective!r}"
        return await self.healer.trip(zone, why, mark=not (marked or keep))

    async def _in_dungeon(self, zone: str) -> bool:
        """Still inside the dungeon we entered by its sigil? Leaving it (its
        outside zone, another world) ends that; a heal trip Recalls back first."""
        if not self._dungeon:
            entry = DungeonMemory.load().dungeons.get(zone)  # e.g. after a restart inside
            if entry is None:
                return False
            self._dungeon = (entry.outside, zone)
        outside, first_room = self._dungeon
        if is_hub(zone) or zone == outside or zone.split("/", 1)[0] != first_room.split("/", 1)[0]:
            self._dungeon = None
            return False
        return True

    def _keep_dungeon_mark(self, objective: str) -> bool:
        """The dungeon mark is still wanted: a defeat awaits a Recall, or we're
        still on the objective it was set for."""
        m = self._mark
        return bool(m and m.kind in RETURN_KINDS and (self._recall_pending or m.objective == objective))

    def _retire_dungeon_mark(self):
        """The dungeon mark has served (or can't any more); the game still holds
        it, so it stays on as a travel mark in the sigil's zone."""
        if self._mark and self._mark.kind in RETURN_KINDS:
            self._mark = Mark(self._mark.zone, self._mark.objective, "travel")
            save_mark(self._mark)

    async def _travel_mark(self, objective: str, zone: str):
        """A new objective several zones away: mark where we are first, so a
        later objective back here is a Recall instead of the same long walk.
        Off: marks are only placed before a dungeon and before a heal trip."""
        if not TRAVEL_AND_FIGHT_MARKS:
            return
        dest = objective_zone(objective)
        dungeons = set(DungeonMemory.load().dungeons)
        keep = self._keep_dungeon_mark(objective)
        if not should_travel_mark(zone, dest, zone_hops, self._mark, keep, dungeons):
            return
        if not await is_free(self.client):
            return
        await self._mark_here("travel", objective=self._last_progress[0] or "")

    async def _recall_if_faster(self, objective: str, zone: str) -> bool:
        """Recall to the mark when that plus the walk from it beats walking to
        the objective's zone from here. True if we recalled."""
        # Travel marks and heal marks (a spot in a dungeon left to heal) both
        # take us back; fight and dungeon marks have their own Recall rules.
        if not self._mark or self._mark.kind not in ("travel", "room") or self._recalled_for == objective:
            return False
        if time.monotonic() < self._recall_blocked_until:
            return False
        dest = objective_zone(objective)
        if not recall_is_faster(zone, dest, self._mark, zone_hops, objective):
            return False
        if not await is_free(self.client):
            return False
        self._recalled_for = objective
        walk, via = zone_hops(zone, dest), zone_hops(self._mark.zone, dest)
        logger.info(
            f"recalling to the mark in {self._mark.zone} for {dest}: "
            f"{via} zone(s) from there vs {walk if walk is not None else 'no known route'} from here"
        )
        if await self._recall(self._mark.zone):
            return True
        self._recall_blocked_until = time.monotonic() + RECALL_RETRY_SECONDS
        return False

    async def _learn_arrival_gate(self):
        """Walking from one outdoor zone into another leaves the wizard just in
        front of the gate back: remember it (the data files miss some gates)."""
        zone = await self.client.zone_name() or ""
        prev, deaths = self._zone_before, self.controller.deaths
        self._zone_before = zone
        if not prev or prev == zone or deaths != self._deaths_before or self._teleported:
            self._deaths_before, self._teleported = deaths, False
            return  # first look, same zone, or a defeat/recall moved us
        self._deaths_before = deaths
        if "interiors" in (prev + zone).lower() or prev.split("/")[0] != zone.split("/")[0]:
            return
        if gate_toward(zone, prev, self._bad_gates):
            # Already reachable; and a travel may cross several zones in one step,
            # so `prev` needn't even border this zone.
            return
        pos = await self._position()
        gate = gate_behind(pos, await self.client.body.yaw())
        if learn_gate(zone, prev, gate):
            logger.info(f"learned a gate {zone} -> {prev} at ({gate.x:.0f}, {gate.y:.0f})")

    async def _answer_dungeon_exit(self):
        """Walking into a dungeon's exit asks "If you leave a Dungeon you will lose
        all your progress...": leave when the objective is elsewhere (e.g. hand the
        quest in outside), otherwise stay. Unanswered, it blocks all movement."""
        box = await ui.modal_box(self.client)
        if box is None:
            return
        text = (await ui.modal_text(box)).lower()
        if "leave a dungeon" not in text and "leave this dungeon" not in text:
            return
        zone = await self.client.zone_name() or ""
        objective = await self.objective()
        target = objective_zone(objective)
        if target is not None:
            leave = target != zone
        else:
            # A place we can't map ("Talk To Sergeant Steeg in Knight's Court",
            # "Defeat Mikey the Brick in Knight's Court"): the marker led us to
            # the exit, so the step is outside; stay only when the enemy it
            # names is in here.
            leave = not await self._named_enemy_here(objective)
        logger.info(f"dungeon exit prompt: {'leaving' if leave else 'staying'} (objective {objective!r})")
        await ui.press_modal_button(self.client, box, "centerButton" if leave else "rightButton")
        if leave:
            await wait_for_loading(self.client, appear_timeout=5.0)

    async def _pick_up_loot(self) -> bool:
        """Every few seconds, grab a reagent or chest nearby (clear of enemies):
        a few seconds each, and reagents and chests pay off later."""
        if time.monotonic() - self._last_loot_scan < WANTED_SCAN_SECONDS:
            return False
        self._last_loot_scan = time.monotonic()
        if await self.client.in_battle():
            return False
        try:
            return await self.collector.collect_nearby(self._press_collect)
        except Exception as exc:
            logger.debug(f"loot pickup failed: {exc!r}")
            return False

    async def _loot_after_boss(self) -> bool:
        """A boss fight was just won: look for its loot chest and open it
        before anything else (a heal trip would leave the room). True if it
        looked."""
        if not self.fighter or self.fighter.boss_fights == self._boss_fights_seen:
            return False
        if time.monotonic() - self.fighter.combat_ended_at < BOSS_CHEST_SETTLE_SECONDS:
            return False  # let the defeat check see where the fight ended first
        won = self.controller.deaths == self._boss_deaths_seen
        self._boss_fights_seen, self._boss_deaths_seen = self.fighter.boss_fights, self.controller.deaths
        if not won or await self.client.in_battle():
            return False
        try:
            await self.collector.loot_boss_chest(self._press_collect)
        except Exception as exc:
            logger.debug(f"boss chest looting failed: {exc!r}")
        return True

    async def _leave_spiral_map(self) -> bool:
        """Walking into a world gate (not pressing X at it) opens the Spiral
        Map with the quest's world already ticked: press Go To World. True if
        the map was open."""
        if not await ui.is_visible(self.client, ui.SPIRAL_DOOR_TELEPORT):
            return False
        logger.info("on the Spiral Map: going to the world the quest leads to")
        for _ in range(5):
            if not await ui.click(self.client, ui.SPIRAL_DOOR_TELEPORT):
                break
            await asyncio.sleep(0.5)
        await wait_for_loading(self.client)
        return True

    async def _pick_up_wanted(self) -> bool:
        """Every few seconds, grab any wanted "Collect X" item in view (away from
        enemies) for any quest in the book, even one set aside. True if it did."""
        if not self._wanted_items or time.monotonic() - self._last_wanted_scan < WANTED_SCAN_SECONDS:
            return False
        self._last_wanted_scan = time.monotonic()
        for item, quest in self._wanted_items.items():
            if await self.collector.collect_once(item, self._press_collect):
                logger.success(f"picked up {item!r} on the way (for {quest!r})")
                return True
        return False

    async def _grind(self) -> bool:
        """Every quest is set aside (bosses too strong, the rest unreachable):
        gain the level that releases them by fighting enemies here, or where a
        fight was last won. True if it acted this step."""
        if await self.client.in_battle():
            return True
        if time.monotonic() - self._last_rank > GRIND_RERANK_SECONDS:
            # A quest may have come in (an NPC offered one, the next main
            # quest): read the book again before more grinding.
            self._ranked_for = None
            self._last_rank = -1e9
            return False
        zone = await self.client.zone_name() or ""
        in_main_world = not self._main_world or zone.split("/", 1)[0] == self._main_world
        if not in_main_world:
            return False  # the main quest's marker leads there (quest step)
        if await self.sprinter.get_mobs() and "interiors" not in zone.lower():
            await self.pull_mob("")
            return True
        if self._last_win_zone.split("/", 1)[0] == self._main_world and self._last_win_zone != zone:
            logger.info(f"no enemies here; going to {self._last_win_zone} to fight for experience")
            if await self.go_to_zone(self._last_win_zone):
                return True
        # Nowhere known yet: the quest step follows the main quest's marker, and
        # the first outdoor zone there with enemies (before its dungeon) is used.
        return False

    def _alert_main_stuck(self, quest: str, why: str):
        """The main quest can't go on for now: an ALERT line (activity.log) that
        the operator's watcher turns into a phone notification. The bot keeps
        going (side quests, experience); once per quest an hour."""
        now = time.monotonic()
        if now - self._alerted.get(quest, -1e9) < ALERT_REPEAT_SECONDS:
            return
        self._alerted[quest] = now
        logger.warning(f"ALERT: main quest {quest!r} stuck: {why}; doing side quests meanwhile")

    async def _set_current_aside(
        self, objective: str, retry_after: float | None = STUCK_RETRY_SECONDS
    ) -> bool:
        """Set the tracked quest aside now (it can't be progressed from here) and
        re-rank, so the next best quest is followed. Just tracking the next quest
        in the book let ranking pick the same one again."""
        quest = self._active_quest
        if not quest:
            return await self.switch_quest()
        level = await self.client.stats.reference_level()
        self.setbacks.set_quest_aside(
            quest, objective, level, main=quest in self._mainline, retry_after=retry_after
        )
        self.setbacks.save()
        if quest in self._mainline:
            self._alert_main_stuck(quest, f"no way to {objective!r} found")
        self._ranked_for = None
        self._last_rank = -1e9
        return True

    async def _note_defeats(self):
        """After a defeat, count it against the objective; the second one sets the
        quest aside for another questline (until a level-up or an hour passes)."""
        deaths = self.controller.deaths
        if deaths <= self._seen_deaths:
            return
        self._seen_deaths = deaths
        self._recall_pending = bool(self._mark and self._mark.kind in RETURN_KINDS)
        self._last_defeat = time.monotonic()
        objective = await self.objective()
        if not objective:
            return
        # Every loss counts, not only on "Defeat X": "Talk To Willie Marks" is
        # a boss fight too.
        level = await self.client.stats.reference_level()
        quest = self._active_quest
        main = quest in self._mainline
        if self.setbacks.record_defeat(objective, quest, level, main=main):
            tries = MAIN_DEFEATS_TO_DEFER if main else DEFEATS_TO_DEFER
            until = f"level {level + 1}" if main else f"level {level + 1} or an hour"
            logger.warning(
                f"lost {objective!r} {tries} times: setting {quest!r} aside until {until}; "
                "doing other quests meanwhile"
            )
            if main:
                self._alert_main_stuck(quest, f"lost {objective!r} {tries} times")
            self._recall_pending = False  # no point recalling to it now
            self._retire_dungeon_mark()
            self._ranked_for = None
            self._last_rank = -1e9  # re-rank quests on this step
        else:
            n = self.setbacks.defeats.get(objective, 0)
            logger.info(f"defeat {n} on {objective!r}; trying again")
        self.setbacks.save()

    async def _recall_to_mark(self) -> bool:
        """Back at full strength after a defeat, still on the same objective: use
        Recall to jump back to the marked dungeon entrance. True if we recalled."""
        if not self._mark or self._mark.kind not in RETURN_KINDS or not self._recall_pending:
            return False  # only after a defeat: otherwise we left on purpose (or are inside)
        marked_zone = self._mark.zone
        zone = await self.client.zone_name()
        if zone == marked_zone:
            return False
        if await self.objective() != self._mark.objective:
            self._recall_pending = False  # moved on: the dungeon mark is done with
            self._retire_dungeon_mark()
            return False
        if not await is_free(self.client):
            return False
        what = "the dungeon entrance" if self._mark.kind == "dungeon" else "the spot marked before the fight"
        logger.info(f"recalling to {what} in {marked_zone} instead of walking back")
        self._recall_pending = False  # one try per defeat: never loop on a failing recall
        if self._mark.kind == "dungeon":
            self._retire_dungeon_mark()  # the dungeon resets: its sigil is a fresh start
        return await self._recall(marked_zone, what)

    async def _recall(self, marked_zone: str, what: str = "the mark") -> bool:
        """Press Recall and wait to arrive in `marked_zone`. True if we did."""
        self.controller.allow_idle(40)
        try:
            for attempt in range(2):
                timer = await ui.named_text(self.client, "txtRecallTimer")
                if not await ui.click_named(self.client, "RecallButton"):
                    logger.warning("no Recall button to click")
                    return False
                logger.debug(f"clicked Recall (try {attempt + 1}; timer text {timer!r})")
                # The teleport plays a short animation before the loading screen.
                deadline = time.monotonic() + RECALL_WAIT
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    box = await ui.modal_box(self.client)
                    if box:
                        text = await ui.modal_text(box)
                        logger.info(f"recall message: {text[:120]!r}")
                        await ui.confirm_modal(self.client)
                        if "cannot teleport" in text.lower():
                            # e.g. the dungeon reset while we were away healing
                            logger.warning("the game refused the recall; walking back instead")
                            return False
                    if await self.client.is_loading():
                        await wait_for_loading(self.client)
                    if await self.client.zone_name() == marked_zone:
                        self._teleported = True
                        logger.success(f"recalled to {what}")
                        return True
                    if not await is_free(self.client):
                        return False
            timer = await ui.named_text(self.client, "txtRecallTimer")
            logger.warning(f"recall didn't take us to {what} (recall timer text {timer!r})")
            return False
        finally:
            self.controller.end_idle()

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
        # Landing next to a mob starts a fight (and cancels the whole attempt).
        candidates = away_from(candidates, await mob_positions(self.client), SIGIL_LEAVE_MOB_DISTANCE)
        far = [p for p in candidates if math.dist((p[0], p[1]), (sigil.x, sigil.y)) >= SIGIL_LEAVE]
        if far:
            p = min(far, key=lambda p: math.dist((p[0], p[1]), (sigil.x, sigil.y)))
            return XYZ(*p)
        return XYZ(sigil.x + SIGIL_LEAVE, sigil.y, sigil.z)

    async def _clear_spot(self, p: XYZ) -> bool:
        """No enemy within MOB_CLEARANCE of `p` right now (mobs patrol, so this
        is read fresh before each teleport)."""
        mobs = [XYZ(*m) for m in await mob_positions(self.client)]
        return clear_of(p, mobs, MOB_CLEARANCE)

    async def _walk_in_from_around(self, target: XYZ, zone: str | None) -> bool:
        """Standing on a door marker gives walk_through no direction; back off
        to each side in turn and walk through the marker from there."""
        for i in range(4):
            ang = i * math.pi / 2
            spot = XYZ(target.x + 300 * math.cos(ang), target.y + 300 * math.sin(ang), target.z)
            if not await self._clear_spot(spot):
                continue
            await self.client.teleport(spot)
            await asyncio.sleep(0.8)
            if await self._zone_changed(zone):
                return True
            if distance(await self._position(), target) < 50:
                continue  # teleport rejected; still on the marker
            if await self.walk_through(target, zone):
                return True
        # In a small room every spot 300 away is behind a wall (the Post
        # Office's exit): back up a step on foot in each direction and walk
        # through the marker from there.
        from wizwalker.utils import calculate_perfect_yaw

        logger.info("backing off the door marker on foot to walk through it")
        for i in range(8):
            ang = i * math.pi / 4
            away = XYZ(target.x + 100 * math.cos(ang), target.y + 100 * math.sin(ang), target.z)
            await self.client.body.write_yaw(calculate_perfect_yaw(target, away))
            await self.client.send_key(Keycode.W, 0.5)  # a step away from the marker
            if await self._zone_changed(zone):
                return True
            if distance(await self._position(), target) < 40:
                continue  # a wall on that side
            if await self.walk_through(target, zone):
                return True
        logger.warning("could not walk through the door marker from any side")
        return False

    async def travel(self, target: XYZ, avoid_mobs: bool = True, npc: bool = False) -> bool:
        """Get within interact range of `target`. Returns True on success.

        With `avoid_mobs`, a teleport never lands next to an enemy (that starts
        an unplanned fight): it lands at the nearest clear spot and walks in.
        `npc`: the target is someone to talk to, not a door: never walk
        "through" it; inch closer and look for the talk prompt instead."""
        start = await self._position()
        if distance(start, target) <= 5:
            return True
        zone = await self.client.zone_name()
        mobs = [XYZ(*m) for m in await mob_positions(self.client)] if avoid_mobs else []

        if not self.cfg.teleport:
            await self.client.goto(target.x, target.y)
            if await self._zone_changed(zone):
                return True
            return distance(await self._position(), target) < INTERACT_RANGE

        if mobs and not clear_of(target, mobs, MOB_CLEARANCE):
            spot = safe_landing(target, start, mobs, MOB_CLEARANCE)
            if spot is not None:
                logger.info(f"enemies near the destination; landing {distance(spot, target):.0f} away")
                await self.client.teleport(spot)
                await asyncio.sleep(0.8)
                if await self._zone_changed(zone):
                    return True
                await self.client.goto(target.x, target.y)
                if await self._zone_changed(zone) or await self.client.in_battle():
                    return True
                if distance(await self._position(), target) < INTERACT_RANGE:
                    return True
                # Didn't get there on foot (a wall, a door): fall back to the usual way.

        await self.client.teleport(target)
        await asyncio.sleep(0.8)
        if await self._zone_changed(zone):
            return True  # the teleport itself went through a zone transition
        if distance(await self._position(), start) > BOUNCE_DISTANCE:
            await self._clear_of_enemies()
            return True
        if distance(await self._position(), target) < INTERACT_RANGE:
            return True  # hardly moved because we were close already: not a rejection
        if teleport_aborted(self.client):
            # Held back (or jumped back) from enemies at the spot: walking there
            # instead would run through them. Try again next step.
            return False

        objective = self._last_progress[0] or ""
        if (
            npc and distance(await self._position(), target) < NPC_INCH_RANGE
            and self._may_try(objective, zone or "", "inch")
        ):
            return await self._inch_toward(target)
        # Far from the marker with the teleport refused: it's a door (Zan'ne's
        # building in the Oasis), whatever the objective says: walk through it.
        # A door walked through before: straight to where that walk started.
        known_door = self.doors.approach(zone or "", (target.x, target.y, target.z))
        if known_door is not None:
            logger.info("a door walked through before: going to where that walk started")
            await self.client.teleport(XYZ(*known_door))
            await asyncio.sleep(0.8)
            if await self._zone_changed(zone) or await self.walk_through(target, zone):
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
                # These spots are in this zone's coordinates: stop once a hop has
                # carried us through the door, and check enemies fresh each time
                # (they patrol; a stale list put the wizard on Desert Golems).
                if await self.client.zone_name() != zone:
                    await self._clear_of_enemies()
                    return True
                if avoid_mobs and not await self._clear_spot(p):
                    continue
                await self.client.teleport(p)
                await asyncio.sleep(0.5)
                if distance(await self._position(), start) > BOUNCE_DISTANCE:
                    await self._clear_of_enemies()
                    return True
        logger.debug("walking toward objective")
        await self.client.goto(target.x, target.y)
        return distance(await self._position(), target) < INTERACT_RANGE

    async def _inch_toward(self, target: XYZ, steps: int = 4, step: float = 150.0) -> bool:
        """Short walks toward an NPC (one on a raised platform rejects a
        teleport onto it), checking for the talk prompt after each: no long
        runs past it into enemies."""
        logger.info("teleport onto the NPC was rejected; inching closer")
        for _ in range(steps):
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                return True
            here = await self._position()
            gap = distance(here, target)
            if gap < 60:
                break
            f = min(1.0, step / gap)
            await self.client.goto(here.x + (target.x - here.x) * f, here.y + (target.y - here.y) * f)
            await asyncio.sleep(0.6)
            if not await is_free(self.client):
                return True
        if await ui.is_visible(self.client, ui.NPC_RANGE):
            return True
        return distance(await self._position(), target) < INTERACT_RANGE

    # --- interaction ---------------------------------------------------------

    async def interact(self, objective: str = "") -> bool:
        """Press X on whatever prompt is showing. Returns True if something happened."""
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            return False
        prompt = (await ui.text_at(self.client, ui.NPC_RANGE_TEXT)).lower()
        logger.info(f"interacting: {prompt or '(no text)'}")

        if "to enter" in prompt:
            # A dungeon sigil: X starts a countdown that any later movement
            # cancels, so let the sigil routine press it and stand still.
            sigil = await self._sigil_at(await self._position(), SIGIL_NEAR_RANGE)
            if sigil is not None:
                return await self._enter_by_sigil(sigil, await self.client.zone_name())

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
            goal = (await ui.text_at(self.client, [*base, "txtGoal"])).strip()
            target = (await ui.text_at(self.client, [*base, "txtGoalObjective1"])).strip()
            if not self._book_dumped and i == 0:
                # The goal text came back empty for most quests: save the
                # slot's layout once to find where it lives.
                self._book_dumped = True
                win = await ui.window_at(self.client, [*QUEST_LIST, f"wndQuestInfo{i}"])
                if win is not None:
                    lines = await ui.dump_tree(win, max_depth=10, only_visible=False, with_types=True)
                    Path("state", "quest_book_window.txt").write_text(
                        "\n".join(lines), encoding="utf-8", errors="replace"
                    )
            out.append(
                QuestEntry(
                    slot=i,
                    name=name,
                    world=world,
                    goal=goal,
                    zone=(await ui.text_at(self.client, [*base, "txtZone"])).strip(),
                    target=target,
                    fight=await ui.is_visible(self.client, [*base, "imgEncounter"]),
                    counted=await ui.is_visible(self.client, [*base, "txtGoalCounter"]),
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
            self._wanted_items = {  # main-story/spell quests only: side quests are ignored
                collect_item_name(q.goal): q.name
                for _, q in all_quests
                if collect_item_name(q.goal) and (q.mainline or q.activity)
            }
            done = self.completions.update({q.name for _, q in all_quests})
            for name in done:
                listed = self.quest_order.get(norm(name))
                where = f" (#{listed.index} on the quest list)" if listed else ""
                logger.success(f"quest completed: {name!r}{where}")
            self.completions.log(done)
            active = next((q for _, q in all_quests if q.active), None)
            self._active_quest = active.name if active else self._active_quest
            level = await self.client.stats.reference_level()
            set_aside = self.setbacks.set_aside(level)
            logger.debug(
                f"quest book: {[q.name for _, q in all_quests]}; set aside: {sorted(set_aside)}"
            )
            for _, q in all_quests:
                flags = "main" if q.mainline else "spell" if q.activity else "side"
                tracked = ", tracked" if q.active else ""
                step = f"{q.goal or q.target!r}{' (fight)' if q.fight else ''}"
                step += " (counted)" if q.counted else ""
                logger.debug(f"  {q.name!r} [{flags}{tracked}] {q.zone}/{q.world!r} {q.hops} hops: {step}")
            self._mainline = {q.name for _, q in all_quests if q.mainline}
            here = await self.client.zone_name() or ""
            # Side quests fill in only in the main quest's world (where it will be
            # picked up again at the next level), never a trip to another world.
            main_quests = [q for _, q in all_quests if q.mainline]
            main_world = next((w for w in map(quest_world, main_quests) if w), None)
            if main_world is None and main_quests:
                # The book's area name may be unknown; its objective can place it.
                zones = [objective_zone(q.goal) for q in main_quests if q.goal]
                main_world = next((z.split("/", 1)[0] for z in zones if z), None)
            world = main_world or self._main_world or (here.split("/", 1)[0] if here else None)
            self._main_world = world
            chosen = choose_quest([q for _, q in all_quests], set_aside, self.quest_order, world)
            grinding = chosen is None and bool(all_quests)
            if grinding and not self._grinding:
                logger.warning(f"nothing to do in {world}: fighting there for experience until a level-up")
                for q in main_quests:
                    self._alert_main_stuck(q.name, f"nothing left to do in {world}; grinding for a level")
            self._grinding = grinding
            if grinding and main_quests:
                # Track the main quest so its marker leads back into its world;
                # _grind fights outdoors there instead of taking on the boss.
                chosen = main_quests[0]
            if await self._in_dungeon(here):
                local = dungeon_quest([q for _, q in all_quests], here, objective_zone, set_aside)
                if local and local is not chosen:
                    logger.info(f"in the dungeon: {local.name!r} comes first (this dungeon's own quest)")
                    chosen, self._grinding = local, False
            chosen = self._apply_pin([q for _, q in all_quests], chosen, set_aside)
            if not self._grinding and not await self._in_dungeon(here):
                errand = errand_detour([q for _, q in all_quests], chosen, set_aside, world)
                if errand:
                    if not errand.active:
                        logger.info(f"quick errand first: {errand.name!r} ({errand.goal or errand.target}), "
                                    f"then back to {chosen.name!r}")
                    chosen = errand
            _write_quest_book([q for _, q in all_quests], chosen, world)
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
            for attempt in range(TRACK_TRIES):
                await ui.click(self.client, slot)
                await asyncio.sleep(0.6)
                if await ui.is_visible(self.client, [*info, "imgActiveQuest"]):
                    break
                if await ui.modal_box(self.client) is not None:
                    break  # a message about it: handled below
                logger.debug(f"tracking {entry.name!r} didn't take (try {attempt + 1}); clicking again")
            else:
                logger.warning(f"could not track {entry.name!r}; will try again at the next ranking")
                self._ranked_for = None
                self._last_rank = -1e9
                return False
            box = await ui.modal_box(self.client)
            if box and "quest helper is not allowed" in (await ui.modal_text(box)).lower():
                # e.g. a Duel Arena (PvP) quest: nothing the bot can follow.
                logger.warning(f"quest helper not allowed for {entry.name!r}; skipping that quest")
                self.setbacks.skipped.add(entry.name)
                self.setbacks.save()
                await ui.dismiss_notice(self.client)
                self._ranked_for = None
                self._last_rank = -1e9
                return False
            kind = "spell/activity" if entry.activity else "main story" if entry.mainline else "side"
            if entry.goal and not is_combat_objective(entry.goal):
                kind += ", no fight"
            where = f"{entry.world}, {entry.hops} hops" if entry.hops is not None else entry.world
            logger.success(f"quest priority: tracking {entry.name!r} ({kind} quest in {where})")
            self._active_quest = entry.name
            return True
        finally:
            await self._close_quest_book()

    def _apply_pin(self, quests: list[QuestEntry], chosen, set_aside: set[str]):
        """The player's pick wins: a pinned quest (state/quest_pin.json, or the
        main-story quest tracked when the bot starts) is followed while it's in
        the book and not set aside (a boss won 5 times, no progress for 5 min)."""
        if self._pin is None:  # first ranking this session: the player's current pick
            active = next((q for q in quests if q.active), None)
            self._pin = load_pin() or (active.name if active and active.mainline else "")
            if self._pin:
                logger.info(f"following the quest you picked: {self._pin!r}")
                save_pin(self._pin)
        if not self._pin:
            return chosen
        pinned = next((q for q in quests if q.name == self._pin), None)
        if pinned is None or pinned.name in set_aside:
            why = "done" if pinned is None else "set aside"
            logger.info(f"your pick {self._pin!r} is {why}; choosing quests again")
            self._pin = ""
            save_pin("")
            return chosen
        return pinned

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
            zone = await self.client.zone_name() or ""
            known = self.entity_map.spots(zone, lambda n: matches_item(item, n), (start.x, start.y, start.z))
            known = spread_points(known, (start.x, start.y, start.z), 800.0)[:KNOWN_SPOTS_FIRST]
            if known:
                logger.info(f"looking for {item!r} where it was seen before ({len(known)} spot(s))")
            for p in known:
                if not await is_free(self.client):
                    return True
                if not await self._clear_spot(XYZ(*p)):
                    continue
                await self.client.teleport(XYZ(p[0] + 200, p[1], p[2]))
                await asyncio.sleep(1.0)
                await scan_entities(self.client, zone, self.entity_map)
                if await self.collector.collect_once(item, self._press_collect):
                    return True
            points = sweep_points((start.x, start.y, start.z), [], 0)
            logger.info(f"searching the zone for {item!r} ({len(points)} spots)")
            for p in points:
                if not await is_free(self.client):
                    return True
                if not await self._clear_spot(XYZ(*p)):
                    continue  # an enemy is there: landing on it starts a fight
                await self.client.teleport(XYZ(*p))
                await asyncio.sleep(0.8)
                if await self.collector.collect_once(item, self._press_collect):
                    return True
            await self.client.teleport(start)
            return True
        # Nothing nearby: pickups only load close to the wizard, so hop across the
        # zone's landmarks and walkways (e.g. Triton's cogs are ~20k units from
        # the entrance).
        if getattr(self, "_far_swept_for", None) != objective:
            self._far_swept_for = objective
            start = await self.client.body.position()
            points = await self._landmarks() + floor_points(await path_points(self.client), start.z)
            spots = spread_points(points, (start.x, start.y, start.z), FAR_SWEEP_SPACING)
            logger.info(f"nothing near here; searching {len(spots)} landmarks across the zone for {item!r}")
            for p in spots[:FAR_SWEEP_MAX]:
                if not await is_free(self.client):
                    return True
                self.controller.allow_idle(10)
                if not await self._clear_spot(XYZ(*p)):
                    continue
                await self.client.teleport(XYZ(*p))
                await asyncio.sleep(1.5)  # let nearby objects stream in
                if await self.collector.collect_once(item, self._press_collect):
                    logger.info(f"found {item!r} near ({p[0]:.0f}, {p[1]:.0f})")
                    return True
            return True
        # Searched the whole zone: the items may lie in a neighbouring zone (the
        # Hall of Champions' gemstones are out on the Krokosphinx streets).
        # Search the zones around the one the objective names (or this one)
        # before giving up.
        if await self._search_next_zone(item, objective):
            return True
        # Nothing lying around anywhere near right now (they spawn over time):
        # follow the next best quest, and pick these up whenever they come into
        # view (see _pick_up_wanted).
        quest = self._active_quest
        if quest and self._stall_switched_for != objective:
            self._stall_switched_for = objective
            level = await self.client.stats.reference_level()
            self.setbacks.set_quest_aside(quest, objective, level, main=quest in self._mainline)
            self.setbacks.save()
            self._zones_searched.pop(objective, None)  # a fresh search next time
            logger.info(
                f"no {item!r} in this zone or the ones around it right now: setting {quest!r} aside; "
                "will pick them up if they show up"
            )
            self._ranked_for = None
            self._last_rank = -1e9
            return True
        # Nothing else to do: let the quest marker (if any) guide us, else wait for respawns.
        if distance(await self.client.quest_position.position(), XYZ(0, 0, 0)) < 1:
            logger.debug(f"no {item!r} found; waiting for respawns")
            self.controller.allow_idle(15)
            await asyncio.sleep(10)
            self._swept_for = None
            return True
        return False

    async def _search_next_zone(self, item: str, objective: str) -> bool:
        """Go to the next zone around the objective's place not searched yet
        for `objective`, to sweep it for `item`. True if it went."""
        zone = await self.client.zone_name() or ""
        searched = self._zones_searched.setdefault(objective, set())
        searched.add(zone)
        home = objective_zone(objective) or zone
        for candidate in zones_near(home, COLLECT_SEARCH_DEPTH):
            if candidate in searched:
                continue
            searched.add(candidate)  # one try each, even if the trip fails
            logger.info(f"no {item!r} in {zone}; searching {candidate} next")
            if await self.go_to_zone(candidate):
                self._swept_for = self._far_swept_for = None  # sweep the new zone
                return True
        return False

    def _may_try(self, objective: str, zone: str, approach: str) -> bool:
        """Count a try at `approach`; False once it has had its APPROACH_LIMITS
        tries for this objective here (so the next approach gets its turn)."""
        key = (objective, zone, approach)
        self._attempts_at[key] = self._attempts_at.get(key, 0) + 1
        return self._attempts_at[key] <= APPROACH_LIMITS.get(approach, 3)

    async def _all_approaches_used(self, objective: str, what: str) -> bool:
        """Every approach failed: set the quest aside now instead of looping.
        True if it did."""
        logger.warning(f"tried every way to {what} for {objective!r}; setting this quest aside for now")
        self._attempts_at = {k: v for k, v in self._attempts_at.items() if k[0] != objective}
        return await self._set_current_aside(objective)

    async def _walk_toward(self, marker: XYZ, target: str) -> bool:
        """Teleport toward a far marker in hops of WALK_LEG (enemies load only
        near the wizard: King Shemet was 26000 away), looking for `target`
        after each. Every landing is checked for enemies (safe_teleport); a hop
        the game refuses is tried 30 degrees to either side. Walking there ran
        into fights. True if it came into view (or a fight started)."""
        from .bossfarm import find_entity_named

        logger.info(f"teleporting toward the quest marker to find {target}")
        for _ in range(WALK_LEGS):
            here = await self._position()
            gap = distance(here, marker)
            if gap < INTERACT_RANGE:
                break
            base = math.atan2(marker.y - here.y, marker.x - here.x)
            leg = min(WALK_LEG, gap)
            moved = False
            for turn in (0.0, math.pi / 6, -math.pi / 6):
                a = base + turn
                z = marker.z if leg == gap else here.z
                hop = XYZ(here.x + math.cos(a) * leg, here.y + math.sin(a) * leg, z)
                await self.client.teleport(hop)
                await asyncio.sleep(0.6)
                if not await is_free(self.client):
                    return True
                if distance(await self._position(), here) > leg / 3:
                    moved = True
                    break
            if not moved:
                logger.info("no way on toward the marker from here")
                return False
            await asyncio.sleep(0.6)  # let what's around load
            if await find_entity_named(self.client, target) is not None:
                logger.info(f"{target} is in view")
                return True
        return False

    async def _use_zone_teleporter(self, objective: str) -> bool:
        """Take the next untried in-zone teleporter (an object labelled "To
        ...", like the Djeserit tomb's "To the Sarcophagus") toward a far quest
        marker. True if it used one."""
        from .names import lang_name

        zone = await self.client.zone_name() or ""
        tried = self._teleporters_tried.setdefault((objective, zone), set())
        here = await self._position()
        options = []
        for e in await self.client.get_base_entity_list():
            try:
                t = await e.object_template()
                code = await t.display_name() if t else None
                label = await lang_name(self.client, code) if code else ""
                if not label.lower().startswith("to ") or label in tried:
                    continue
                pos = await e.location()
                options.append((distance(pos, here), label, pos))
            except Exception:
                continue
        if not options:
            return False
        _d, label, pos = min(options, key=lambda o: o[0])
        tried.add(label)
        logger.info(f"the quest marker is out of reach: taking the {label!r} teleporter")
        dx, dy = here.x - pos.x, here.y - pos.y
        length = math.hypot(dx, dy) or 1.0
        await self.client.teleport(XYZ(pos.x + dx / length * 200, pos.y + dy / length * 200, pos.z))
        await asyncio.sleep(0.8)
        await self.client.goto(pos.x, pos.y)
        await self._press_x_here(zone, adjust=True)
        return True

    async def _try_switch_puzzle(self, objective: str, zone: str) -> bool:
        """'Use X' where X should be but isn't (a chest that appears when the
        room's switches are right): try every switch combination, once per
        objective and room. True if it tried."""
        from .bossfarm import find_entity_named
        from .puzzles import solve_by_trying, use_target

        target = use_target(objective)
        if not target or (objective, zone) in self._puzzles_tried:
            return False
        marker = await self.client.quest_position.position()
        if distance(marker, XYZ(0, 0, 0)) < 1 or distance(await self._position(), marker) > PUZZLE_NEAR:
            return False  # not there yet
        if await find_entity_named(self.client, target) is not None:
            return False
        self._puzzles_tried.add((objective, zone))
        await solve_by_trying(self, objective)
        return True

    async def _landmarks(self) -> list[tuple[float, float, float]]:
        return await landmarks(self.client)

    async def unneeded_fight(self, battle) -> bool:
        """True if the fight that just started isn't needed for the tracked quest."""
        try:
            # The quest goal text can read blank mid-battle: use the last one seen.
            objective = (await self.objective()).strip() or self._last_progress[0] or ""
            zone = await self.client.zone_name() or ""
        except Exception:
            return False
        names = [e.name for e in battle.enemies]
        has_boss = any(e.is_boss for e in battle.enemies)
        if fight_needed(objective, names, zone, has_boss):
            return False
        # Fleeing the same enemies again and again on one objective means they
        # stand in the way (Desert Golems on the road to Akori's Chamber): fight.
        key = (objective, frozenset(names))
        self._fled[key] = self._fled.get(key, 0) + 1
        if self._fled[key] > FLEES_BEFORE_FIGHTING:
            who = ', '.join(sorted(set(names)))
            hp, mana = await health_mana(self.client)
            if self.upkeep and self.upkeep.needs_recovery(hp, mana):
                # Fighting through at 0 mana (the O'Leary Nappers) only loses.
                logger.info(f"fled {who} {self._fled[key] - 1} times here, but at {hp:.0%} health, "
                            f"{mana:.0%} mana: fleeing again")
                return True
            logger.info(f"fled {who} {self._fled[key] - 1} times here; fighting through")
            return False
        return True

    async def _talk_target_enemy(self, objective: str):
        """The enemy a "Talk To X" objective names, if X is a mob here (Willie
        Marks before he's beaten), else None."""
        name = talk_target(objective)
        if not name:
            return None
        from .names import lang_name

        want = _norm_name(name)
        try:
            for mob in await self.client.get_mobs():
                t = await mob.object_template()
                code = await t.display_name() if t else ""
                label = await lang_name(self.client, code) if code else ""
                if want and want == _norm_name(label):
                    return mob
        except Exception as exc:
            logger.debug(f"talk-target enemy check failed: {exc!r}")
        return None

    async def _talk_means_fight(self, objective: str) -> bool:
        """"Talk To Willie Marks" where Willie Marks is an enemy (the talk comes
        after beating him, in the middle of his clocktower): go start the fight.
        True if it went for one."""
        mob = await self._talk_target_enemy(objective)
        if mob is None:
            return False
        logger.info(f"{talk_target(objective)} is an enemy here: fighting him to get on with {objective!r}")
        allow_engage(self.client)
        await self.client.teleport(await mob.location())
        await asyncio.sleep(3.0)
        return True

    async def _named_enemy_here(self, objective: str) -> bool:
        """Is the enemy the objective names (Defeat X, or Talk To an enemy) in this zone?"""
        if await self._talk_target_enemy(objective or "") is not None:
            return True
        from .bossfarm import find_entity_named

        for name in defeat_names(objective or ""):
            if await find_entity_named(self.client, name) is not None:
                return True
        return False

    async def _fight_ahead(self, objective: str) -> bool:
        """Is the next thing to do a fight (a Defeat objective, or a Talk To
        someone who is still an enemy here)?"""
        if is_combat_objective(objective or ""):
            return True
        return await self._talk_target_enemy(objective or "") is not None

    async def pull_mob(self, objective: str = ""):
        """For defeat objectives: teleport onto the enemy the objective names
        ("Defeat Gobbler Gorger ..."), else the closest mob, to start a fight."""
        target = defeat_target(objective)
        if target:
            from .bossfarm import find_entity_named

            pos = None
            for name in defeat_names(objective):  # "Any Sphinx Sokkwi": any Sokkwi
                pos = await find_entity_named(self.client, name)
                if pos is None and name.endswith("s"):
                    pos = await find_entity_named(self.client, name[:-1])  # "Lost Souls"
                if pos is not None:
                    target = name
                    break
            if pos is not None:
                logger.info(f"going after {target} for {objective!r}")
                allow_engage(self.client)  # this teleport is meant to start the fight
                await self.client.teleport(pos)
                await asyncio.sleep(3.0)
                if await self.client.in_battle():
                    return
            else:
                where = objective_zone(objective)
                if where and where != await self.client.zone_name():
                    return  # "... in Hall of Champions": not here; the quest marker leads there
                # At the marker with the enemy nowhere in the zone: the marker is
                # the way to it (a teleporter like the Djeserit tomb's "To the
                # Sarcophagus", a door): use its X prompt first.
                marker = await self.client.quest_position.position()
                zone_now = await self.client.zone_name() or ""
                at_marker = distance(await self._position(), marker) < MARKER_WAY_RANGE
                if distance(marker, XYZ(0, 0, 0)) > 1 and at_marker and self._may_try(
                    objective, zone_now, "marker_x"
                ):
                    if await self._press_x_here(zone_now, adjust=True):
                        return
                    if not await is_free(self.client):
                        return
                # Far from the marker: walk toward it (enemies only load nearby;
                # King Shemet was 26000 away, easy to reach on foot).
                if not at_marker and self._may_try(objective, zone_now, "walk"):
                    if await self._walk_toward(marker, target):
                        return
                # Far from an unreachable marker: an in-zone teleporter ("To the
                # Sarcophagus") is the way over to it.
                if not at_marker and self._may_try(objective, zone_now, "teleporter"):
                    if await self._use_zone_teleporter(objective):
                        return
                # A boss that isn't there yet usually appears when the wizard
                # walks into its spot (the marker); a teleport doesn't set that off.
                if await self._walk_onto_marker():
                    return
                # Fighting whatever is closest (Gobbler Scavengers instead of
                # Munchers) costs time and risk for nothing: look around the zone
                # for the named enemy, twice at most; then move on.
                if self._may_try(objective, zone_now, "sweep"):
                    await self._look_for(target)
                else:
                    await self._all_approaches_used(objective, f"find {target}")
                return
        for _ in range(3):
            if await self.client.in_battle():
                return
            try:
                allow_engage(self.client)  # "any enemy will do": going onto one on purpose
                await self.sprinter.tp_to_closest_mob()
            except Exception as exc:
                logger.debug(f"no mob to pull: {exc}")
                return
            await asyncio.sleep(3.0)

    async def _walk_onto_marker(self) -> bool:
        """Near the quest marker: back off in each direction in turn and walk
        onto it, which triggers boss spawns and cutscenes. True if a fight or
        dialogue started."""
        marker = await self.client.quest_position.position()
        if distance(marker, XYZ(0, 0, 0)) < 1 or distance(await self._position(), marker) > MARKER_WALK_RANGE:
            return False
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            start = XYZ(marker.x + dx * MARKER_WALK_BACK, marker.y + dy * MARKER_WALK_BACK, marker.z)
            await self.client.teleport(start)
            await asyncio.sleep(0.8)
            await self.client.goto(marker.x, marker.y)
            await asyncio.sleep(2.0)
            if not await is_free(self.client):
                logger.info("walking onto the quest marker started something")
                return True
        return False

    async def _look_for(self, target: str):
        """Hop across the zone's landmarks (clear of enemies) until an enemy
        named `target` is in view, then go after it."""
        from .bossfarm import find_entity_named

        start = await self._position()
        zone = await self.client.zone_name() or ""
        want = _norm_name(target).removesuffix("s")
        # Where it was seen before comes first (nearest first); then the sweep
        # over ground those visits haven't covered.
        here = (start.x, start.y, start.z)
        known = self.entity_map.spots(zone, lambda n: bool(want) and want in _norm_name(n), here)
        known = spread_points(known, (start.x, start.y, start.z), ENEMY_SWEEP_SPACING / 2)[:KNOWN_SPOTS_FIRST]
        # Wanderers patrol the walkways: search along the path markers as well
        # as the named landmarks, so the whole zone gets covered.
        points = await self._landmarks() + floor_points(await path_points(self.client), start.z)
        sweep = [
            p for p in spread_points(points, (start.x, start.y, start.z), ENEMY_SWEEP_SPACING)
            if all(math.dist(p[:2], k[:2]) > ENEMY_SWEEP_SPACING / 2 for k in known)
        ]
        visited = self._swept_spots.setdefault((self._last_progress[0] or "", zone), [])
        fresh = [p for p in sweep if all(math.dist(p[:2], v[:2]) > ENEMY_SWEEP_SPACING / 2 for v in visited)]
        spots = [k for k in known if k not in visited] + fresh[:FAR_SWEEP_MAX]
        where = f"{len(known)} spot(s) it was seen at, then " if known else ""
        n_new = len(fresh[:FAR_SWEEP_MAX])
        logger.info(f"no {target} in view; looking at {where}{n_new} new spots around the zone")
        for p in spots:
            if not await is_free(self.client):
                return
            visited.append(p)
            if not await self._clear_spot(XYZ(*p)):
                continue
            await self.client.teleport(XYZ(*p))
            await asyncio.sleep(1.5)  # let nearby entities stream in
            await scan_entities(self.client, zone, self.entity_map)
            pos = await find_entity_named(self.client, target)
            if pos is not None:
                logger.info(f"found {target} near ({p[0]:.0f}, {p[1]:.0f}); going after it")
                allow_engage(self.client)
                await self.client.teleport(pos)
                await asyncio.sleep(3.0)
                return

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
        if target_zone not in (None, zone) and gate_toward(zone, target_zone, self._bad_gates):
            # Not next door: go there through the known gates, several hops if needed.
            logger.info(f"no quest marker for {objective!r}; heading to {target_zone}")
            if await self.go_to_zone(target_zone) or await self.client.zone_name() != zone:
                return True
        if target_zone not in (None, zone):
            other_world = target_zone.split("/")[0] != (zone or "").split("/")[0]
            # No gate route: switch, unless we're just inside a building of the
            # same world (walking out may find one). Another world: no gates lead there.
            routable = gate_toward(zone, target_zone, self._bad_gates)
            if not routable and (other_world or "interiors" not in zone.lower()):
                logger.warning(f"no route from {zone} to {target_zone}; setting this quest aside")
                return await self._set_current_aside(objective)
            if any(to == target_zone for _frm, to in self._bad_gates) and not find_zone_gate(
                objective, zone, self._bad_gates
            ):
                # Every way in refused us: the zone is still locked by the story.
                logger.warning(f"{target_zone} looks locked (every gate refused); setting this quest aside")
                return await self._set_current_aside(objective)
            return False  # the target is in another zone; its spots here are someone else's
        # "Talk to Clockwork in Katzenstein's Lab" with no marker: look for
        # Clockwork around here and walk up to him.
        name = talk_target(objective)
        if name:
            from .bossfarm import find_entity_named

            pos = await find_entity_named(self.client, name)
            if pos is not None:
                logger.info(f"no quest marker for {objective!r}; {name} is here: going to talk")
                await self.controller.checkpoint()
                await self.travel(pos, npc=True)
                if not await wait_until_free(self.client, timeout=5):
                    return True
                if await self.interact(objective):
                    return True
                await self.client.send_key(Keycode.S, 0.3)
                await self.client.send_key(Keycode.W, 0.3)
                await asyncio.sleep(0.5)
                await self.interact(objective)
                return True
            if (objective, zone) not in self._puzzles_tried:
                # Nowhere to be seen (Clockwork in Katzenstein's Lab, powered
                # up by its three levers): try the room's switches.
                from .puzzles import find_switches, solve_by_trying

                if await find_switches(self.client):
                    self._puzzles_tried.add((objective, zone))
                    logger.info(f"{name} isn't here; trying the switches around to bring them out")
                    await solve_by_trying(self, objective, target=name)
                    return True
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
        if await self._leave_spiral_map():
            return
        if time.monotonic() - self._last_entity_scan > ENTITY_SCAN_SECONDS:
            self._last_entity_scan = time.monotonic()
            await scan_entities(self.client, await self.client.zone_name() or "", self.entity_map)
        if time.monotonic() - self._last_wisp_scan > WISP_SCAN_SECONDS:
            await scan_wisps(self.client)  # learn wisp spawn points while questing
            self._last_wisp_scan = time.monotonic()
        await self._note_defeats()
        # A patrol walked up while we stood still: step aside (outdoors, and not
        # when the objective is a fight, which means going onto enemies).
        zone_now = await self.client.zone_name() or ""
        if "interiors" not in zone_now.lower() and not is_combat_objective(await self.objective()):
            await self._clear_of_enemies()
        if await self._loot_after_boss():
            return
        if await self._pick_up_wanted():
            return
        if await self._pick_up_loot():
            return
        await self._answer_dungeon_exit()
        await self._learn_arrival_gate()
        in_dungeon = await self._in_dungeon(zone_now)
        # In a dungeon, leaving to heal resets it: first do everything that
        # needs no fight (the talk after beating Willie Marks), and heal only
        # when the next step is a fight.
        heal_now = not in_dungeon or await self._fight_ahead(await self.objective())
        if not heal_now:
            logger.debug("in the dungeon with no fight ahead: finishing the objective before healing")
        if heal_now and self.healer and in_dungeon and await self.healer.between_fights(zone_now):
            return
        if heal_now and self.upkeep and not await recover(
            self.client, self.upkeep, self.controller, self.go_to_zone,
            trip=self._heal_trip, mark=self._heal_mark,
        ):
            return
        if await self._recall_to_mark():
            return
        # Quests beat grinding for experience: ask the NPCs around first (the
        # next main quest may be waiting with one of them).
        if await self.givers.ask_nearby():
            return
        # Grinding comes after healing: right after a defeat it went looking for
        # fights at 0 mana and a third of its health.
        if self._grinding and await self._grind():
            return
        if self.gear:
            self.controller.allow_idle(600)  # a full check tries ~40 items (~5 min): not a stall
            try:
                await self.gear.tick()
            except Exception as exc:
                logger.opt(exception=exc).warning("gear check failed")
            finally:
                self.controller.end_idle()
            if not await is_free(self.client):
                return
        if self.trainer:
            self.controller.allow_idle(240)  # the trip crosses zones and a training window
            try:
                acted = await self.trainer.tick()
            except Exception as exc:
                logger.opt(exception=exc).warning("spell training trip failed")
                acted = True
            finally:
                self.controller.end_idle()
            if acted:
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
        accepted = self.dialogue.accepted if self.dialogue else 0
        if accepted != self._accepted_seen:
            # A newly accepted quest gets tracked by the game (Harold's side
            # quest took over from the main story): rank again now.
            self._accepted_seen = accepted
            self._ranked_for = None
            self._last_rank = -1e9
        # The game may auto-track a quest we set aside (e.g. after handing one in):
        # re-rank at once rather than walking back to the fight we keep losing.
        set_aside_objectives = {d.get("objective") for d in self.setbacks.deferred.values()}
        on_set_aside = objective in set_aside_objectives and bool(
            self.setbacks.set_aside(await self.client.stats.reference_level())
        )
        if objective != getattr(self, "_ranked_for", None) and (
            on_set_aside or time.monotonic() - getattr(self, "_last_rank", -1e9) > RANK_QUESTS_EVERY
        ):
            self._last_rank = time.monotonic()
            self.controller.allow_idle(30)
            try:
                # Reading the quest book stands still: not beside enemies.
                await move_to_safety(self.client, EXPOSED_RADIUS, "before reading the quest book")
                if await self.prioritize_quests():
                    await asyncio.sleep(1.0)
                    objective = await self.objective()
            finally:
                self.controller.end_idle()
            self._ranked_for = objective
        zone = await self.client.zone_name()
        # A new objective: Recall first if the mark gets us there sooner (the
        # game keeps one mark: marking here first would lose it); else mark
        # here before a long trip.
        if objective and await self._recall_if_faster(objective, zone or ""):
            await self._note_progress(objective, zone)
            return
        if objective and self._last_progress[0] and objective != self._last_progress[0]:
            await self._travel_mark(objective, zone or "")
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

        if await self._try_switch_puzzle(objective, zone or ""):
            return

        item = collect_item_name(objective)
        if item:
            # "Collect Cog in Triton Avenue": searching any other zone is pointless.
            where = objective_zone(objective)
            # Not while searching the zones around it (see _search_next_zone).
            searching_here = zone in self._zones_searched.get(objective, ())
            if where and where != zone and not searching_here:
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
        # Always land clear of enemies: the marker of a "Defeat X" objective can
        # sit beside other mobs (a Desert Golem by the Nirini Warriors), and
        # pull_mob goes after the named enemy on purpose afterwards.
        if is_combat_objective(objective) and objective_zone(objective) in (None, zone):
            if objective_zone(objective) == zone:  # an unknown place: no mark (it went in the Oasis)
                await self._mark_for_fight(objective, zone or "")
            allow_close_landing(self.client)  # enemies there are what we came for
        # An NPC here: inch toward it. Elsewhere the marker is a door on the way.
        npc_here = "talk" in objective.lower() and objective_zone(objective) in (None, zone)
        if npc_here and await self._talk_means_fight(objective):
            return
        await self.travel(target, npc=npc_here)
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
                await self.pull_mob(objective)
            return

        if dist >= INTERACT_RANGE and "interiors" in (zone or "").lower():
            # In a dungeon, a marker we can't reach is usually behind a gate that
            # opens once the enemies in front of it are beaten: go fight them.
            key = (objective, zone)
            self._unreached[key] = self._unreached.get(key, 0) + 1
            if self._unreached[key] >= UNREACHED_BEFORE_FIGHT and await self.sprinter.get_mobs():
                logger.info("can't reach the quest marker (a locked gate?); fighting nearby enemies")
                self._unreached[key] = 0
                await self.pull_mob()
                return

        doorish = dist < DOOR_RANGE or ("talk" not in objective.lower() and dist < INTERACT_RANGE)
        if doorish and await self.client.zone_name() == zone:
            # At (or near) the marker with nothing to interact with: it's most
            # likely a door or zone exit, which needs walking into. (The Post
            # Office's exit sat 590 away, beyond DOOR_RANGE, and a teleport to
            # it was rejected: nothing handled it.)
            if await self.walk_through(target, zone) or await self._walk_in_from_around(target, zone):
                logger.info("walked through a door")
