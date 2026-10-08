"""Team Up: enter a group dungeon with other players instead of alone.

Mount Olympus (Aquila) beat the wizard twice solo. The dungeon sigil's
"Press X to Enter" prompt has a TEAM UP! button: it queues the wizard for a
team, and when one forms the game offers to take everyone into the dungeon.

The windows aren't mapped yet, so buttons are found by the text they show
(like relog.py), and the visible window tree is saved at each stage to
state/teamup_<stage>.txt for mapping. Dungeons that need a team are listed
in TEAM_UP_DUNGEONS (zone ids of the dungeons' first rooms).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path

from loguru import logger

from . import ui
from .relog import _find_button
from .upkeep import wait_for_loading

TEAM_UP_DUNGEONS = {"Aquila/AQ_Z01_MountOlympus",
                    "WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_01"}  # the Waterworks
# Every zone of those dungeons (their rooms are separate interiors).
TEAM_UP_PREFIXES = ("Aquila/AQ_Z01_", "Aquila/Interiors/AQ_Z01_", "WizardCity/Gauntlets/WC_Triton_Gauntlet1/")
# How quest steps name those dungeons ("Defeat Zeus Sky Father in Mount Olympus").
TEAM_UP_NAMES = ("mount olympus", "waterworks")
TEAM_UP_WORDS = ("team up!", "team up")
# Queue with Team Up too (besides watching the sigil for a party gathering;
# a party on the sigil wins: the Team Up screen is closed to go in with them).
USE_QUEUE = False  # the player's choice: Team Up teams left mid-run; go in with players at the sigil
# Dungeons queued with Team Up after all (the player, 2026-10-04: the
# Waterworks; a team came in through Team Up at once).
QUEUE_DUNGEONS = {"WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_01"}
REQUEUE_EVERY = 60.0  # seconds between looks for the sigil's TEAM UP! (after its cooldown)
CANCEL_WORDS = ("cancel", "cancel team up", "leave", "leave queue", "stop", "yes", "ok")


# The rooms of a team dungeon in the order a run goes through them (the
# player's guide, docs/guides/Wizard City - Waterworks.md): with no teammate
# in the room, the bot goes on to the next one by its gate (teleported beside
# it, the last bit walked) instead of following tracks on foot.
_WW = "WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_"
ROOM_ORDER = {
    "WizardCity/Gauntlets/WC_Triton_Gauntlet1/": [
        _WW + "01",  # entrance chamber (Sylster at the end)
        _WW + "01a", _WW + "02",  # first passageway, first lever
        _WW + "01",
        _WW + "03", _WW + "04",  # second: the clams, Luska Charmbeak
        _WW + "01",
        _WW + "05", _WW + "06",  # third
        _WW + "01",
        _WW + "07", _WW + "08",  # fourth: the eel's levers
        _WW + "01",  # close the Drain Valve: Sylster Glowstorm
    ],
}


def room_order(zone: str) -> list[str]:
    """The run's room order for the dungeon `zone` is in ([] if none known)."""
    return next((order for prefix, order in ROOM_ORDER.items() if zone.startswith(prefix)), [])


def advance_room(order: list[str], pos: int, zone: str) -> int:
    """Where the run is after reaching `zone` (pos: index in `order`, -1
    before the first room, i.e. a new run or a restart: the zone's first
    place). Only one room on at a time; anywhere else (a room passed before)
    stays put."""
    if pos < 0:
        return order.index(zone) if zone in order else pos
    if pos + 1 < len(order) and order[pos + 1] == zone:
        return pos + 1
    return pos


def next_room(order: list[str], pos: int) -> str | None:
    """The room after `pos` in the run (None at the end)."""
    return order[pos + 1] if pos + 1 < len(order) else None


QUEUE_FILE = Path("state") / "team_queue.json"  # {"dungeon", "quest", "at"}: queued, questing meanwhile
QUEUE_RECHECK = 15 * 60  # queued: the main quest waits this long, then the sigil again (queue still on?)


def save_queue(dungeon: str, quest: str, sure: bool = False) -> None:
    """`sure`: the sigil itself showed we're queued (no TEAM UP! on it): the
    Waiting badge can't be read then, so no lapse checks (they sent the bot
    back to the sigil every 4 minutes while it was still queued)."""
    QUEUE_FILE.parent.mkdir(exist_ok=True)
    data = {"dungeon": dungeon, "quest": quest, "at": time.time(), "sure": sure}
    QUEUE_FILE.write_text(json.dumps(data), encoding="utf-8")


def load_queue() -> dict:
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clear_queue() -> None:
    QUEUE_FILE.unlink(missing_ok=True)


READY_WORDS = ("ready", "accept", "join", "teleport", "go", "yes", "enter")


async def accept_team_ready(client) -> bool:
    """Queued and the team is ready (the prompt comes wherever we are): say
    yes. Its window tree is saved the first time (state/teamup_ready_*.txt)
    to learn the exact prompt. True if it pressed something."""
    box = await ui.modal_box(client)
    if box is not None:
        text = (await ui.modal_text(box)).lower()
        if "team" in text or "dungeon" in text or "group" in text:
            await _dump(client, f"ready_{int(time.time())}")
            logger.success(f"team up: the team is ready ({text[:100]!r}); accepting")
            await ui.press_modal_button(client, box, "centerButton")
            return True
        return False
    for name in ("TeamUpReadyWindow", "TeamUpWindow", "TeamUpInviteWindow", "TeamUpConfirmWindow"):
        w = await ui._visible_named(client.root_window, name)
        if w is None:
            continue
        await _dump(client, f"ready_{int(time.time())}")
        button = await _find_button(w, READY_WORDS)
        if button is not None:
            logger.success(f"team up: the team is ready ({name}); pressing {await button.name()!r}")
            await ui.click_center(client, button)
            return True
    return False


START_EARLY_WORDS = ("start early", "start early!", "start now", "start")


async def start_early(client) -> bool:
    """Queued and another player has joined: the Waiting window's START EARLY
    (the player: go with whoever is found, don't wait for a full team). True
    if pressed."""
    found = await _path_to_text(client.root_window, START_EARLY_WORDS[:3], [])
    if not found:
        return False
    # The text may sit on the button itself or on a label inside it.
    target = found[-1]
    for w in reversed(found):
        try:
            kind = ((await w.maybe_read_type_name()) or "").lower()
            if "button" in kind or "btn" in (await w.name()).lower():
                target = w
                break
        except Exception:
            continue
    await _dump(client, f"start_early_{int(time.time())}")
    logger.success("team up: a player joined; pressing START EARLY")
    await ui.click_center(client, target)
    await asyncio.sleep(1.0)
    return True


TEAM_LIST_FILE = Path("state") / "team_dungeons.json"  # {dungeon: quest}: added after 5 losses


def team_list() -> dict[str, str]:
    """Dungeons added to team play by the bot (a main-quest boss there won 5
    times: the player's "multiplayer mode"), each with the quest it's for."""
    try:
        return dict(json.loads(TEAM_LIST_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def add_team_dungeon(dungeon: str, quest: str) -> None:
    teams = team_list()
    teams[dungeon] = quest
    TEAM_LIST_FILE.parent.mkdir(exist_ok=True)
    TEAM_LIST_FILE.write_text(json.dumps(teams, indent=1), encoding="utf-8")


def drop_team_dungeons(open_quests: set[str]) -> list[str]:
    """Added dungeons whose quest is done (no longer in the book) go back to
    solo. The ones dropped."""
    teams = team_list()
    gone = [d for d, q in teams.items() if q not in open_quests]
    if gone:
        for d in gone:
            del teams[d]
        TEAM_LIST_FILE.write_text(json.dumps(teams, indent=1), encoding="utf-8")
    return gone


def is_team_dungeon(dungeon: str) -> bool:
    """A dungeon only entered with a team: the fixed ones, and those added."""
    return dungeon in TEAM_UP_DUNGEONS or dungeon in team_list()


def _dungeon_base(zone: str) -> str:
    return re.sub(r"_Room\d+$", "", zone or "")


def in_team_list(zone: str) -> bool:
    """`zone` or another room of the same dungeon is on the team list
    (PyramidMotherMoon_Room01 listed: Neza's Room02/03 too; it fought him solo
    there after the list said Team Up)."""
    base = _dungeon_base(zone)
    return bool(zone) and any(k == zone or (base != zone and _dungeon_base(k) == base) for k in team_list())


def is_team_up_zone(zone: str) -> bool:
    """A room of a dungeon that is only entered with a team."""
    teams = team_list()
    if zone in TEAM_UP_DUNGEONS or zone.startswith(TEAM_UP_PREFIXES) or in_team_list(zone):
        return True
    if zone and teams and in_team_run(zone):
        return True  # (a later room of a run on the team list: the team's still on)
    return any("@" in key and _sigil_key_is(key, zone) for key in teams)


def _sigil_key_is(key: str, zone: str) -> bool:
    """A team-list key "outside@x,y" (a sigil put on the list before its
    dungeon was learned) for the dungeon `zone`, as learned since."""
    from .dungeons import DungeonMemory

    entry = DungeonMemory.load().dungeons.get(zone)
    place, _, xy = key.partition("@")
    if entry is None or entry.outside != place or not entry.sigil:
        return False
    try:
        x, y = (float(v) for v in xy.split(","))
    except ValueError:
        return False
    return abs(entry.sigil[0] - x) < 400 and abs(entry.sigil[1] - y) < 400


# The form TEAM UP! opens (mapped from state/teamup_window.txt): farming
# runs, at least 4 players.
CONFIRM_WINDOW = "TeamUpConfirmationWindow"
TEAM_CHOICES = ("TeamTypeFarmingCheckBox", "TeamSize4CheckBox")
# A quest's dungeon (the team list: lost 5 times alone): questing, 2+ players
# (the player: farming and 4 was wrong for Belloq; 2+ is faster).
QUEST_TEAM_CHOICES = ("TeamTypeQuestingCheckBox", "TeamSize2CheckBox")
CONFIRM_WORDS = ("team up", "join", "join team", "find team", "search", "yes", "ok", "go", "accept", "ready")
TEAM_UP_WAIT = 15 * 60  # seconds to wait for a team before giving up for now
# Players gathering on the sigil to go in: with this many others on it, press
# X and stand still through the countdown to go in with them.
PARTY_ON_SIGIL = 3  # (the player: a full party of 4 before a Waterworks run)
SIGIL_RADIUS = 300.0  # a player this near the sigil's center is standing on it
SIGIL_FIND_RANGE = 800.0  # the sigil object nearest us within this is ours
SIGIL_COUNTDOWN_WAIT = 15.0
SIGIL_STEP_BACK = 300.0  # stepping onto the sigil: land this far off it, then walk on
SIGIL_CHECK_EVERY = 60.0  # while waiting: make sure we're still on the sigil this often
RESUME_AFTER_DEFEAT = 900.0  # defeated in the dungeon this recently: RESUME on the sigil rejoins the team
SIGIL_RETRY = 45.0  # seconds before pressing X again if they didn't take us in
NO_PLAYERS_SWITCH = 5 * 60  # nobody near the sigil this long while queued: switch realm
SIGIL_AREA = 1500.0  # "near the sigil": players around here may be coming to go in
IN_FIGHT_RANGE = 900.0  # a player this near a duel circle (or an enemy) is in its fight
# (Apollo's arena: the team stood ~500+ from the circle's center, fighting)
ME_RANGE = 60.0  # a Player Object this near us is our own wizard


async def teammates(client, me) -> list:
    """Where the other players in the zone stand (Player Objects not ours)."""
    from wizwalker import XYZ

    out = []
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t or (await t.object_name() or "") != "Player Object":
                continue
            pos = await e.location()
            if pos.distance(me) > ME_RANGE:
                out.append(XYZ(pos.x, pos.y, pos.z))
        except Exception:
            continue
    return out


def players_on_sigil(center, mates) -> int:
    """How many other players stand on the sigil at `center`."""
    return sum(1 for m in mates if m.distance(center) < SIGIL_RADIUS)


async def sigil_center(client, me):
    """The sigil we stand at (its object), or our own spot if none is listed."""
    best, best_d = me, SIGIL_FIND_RANGE
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t or "sigil" not in (await t.object_name() or "").lower():
                continue
            pos = await e.location()
            if pos.distance(me) < best_d:
                best, best_d = pos, pos.distance(me)
        except Exception:
            continue
    return best


async def on_sigil(client) -> bool:
    """The sigil's "Press X to Enter" prompt is up: we stand on it."""
    if not await ui.is_visible(client, ui.NPC_RANGE):
        return False
    return "enter" in (await ui.text_at(client, ui.NPC_RANGE_TEXT)).lower()


async def step_onto_sigil(client, center, far_spot=None) -> bool:
    """Not on the sigil (it came back from a run standing beside it, and every
    X for a party went nowhere): the prompt only comes back after leaving the
    sigil's (large) area, so go somewhere far (`far_spot`: the quester's), land
    short of the sigil and walk on, like a player. True once the prompt shows."""
    from wizwalker import XYZ

    if await on_sigil(client):
        return True
    logger.info("team up: not on the sigil (no prompt); leaving its area and stepping back on")
    away = await far_spot(center) if far_spot else XYZ(center.x + 2000, center.y, center.z)
    await client.teleport(away)
    await asyncio.sleep(1.5)
    dx, dy = away.x - center.x, away.y - center.y
    back = SIGIL_STEP_BACK / (math.hypot(dx, dy) or 1.0)
    await client.teleport(XYZ(center.x + dx * back, center.y + dy * back, center.z))
    await asyncio.sleep(1.0)
    await client.goto(center.x, center.y)
    for _ in range(10):
        await asyncio.sleep(0.3)
        if await on_sigil(client):
            return True
    shown = await ui.is_visible(client, ui.NPC_RANGE)
    text = await ui.text_at(client, ui.NPC_RANGE_TEXT) if shown else ""
    logger.warning(f"team up: still no sigil prompt (prompt window shown: {shown}, text {text!r})")
    return False


async def _join_party_on_sigil(client, zone: str, center, last_press: list[float], far_spot=None,
                               need: int = PARTY_ON_SIGIL) -> bool:
    """Two or more players on the sigil: press X once and stand still through
    the countdown so we go in with them. True once in the dungeon."""
    from wizwalker import Keycode

    if time.monotonic() - last_press[0] < SIGIL_RETRY:
        return False
    me = await client.body.position()
    on = players_on_sigil(center, await teammates(client, me))
    if on < need:
        return False
    last_press[0] = time.monotonic()
    if not await step_onto_sigil(client, center, far_spot):
        logger.warning("team up: couldn't get the sigil's prompt; not pressing X")
        return False
    # The sigil's party beats the queue: close the Team Up screen first.
    if await close_stray_forms(client) or await close_events_window(client):
        logger.info("team up: closed the Team Up screen to join the players on the sigil")
    logger.info(f"team up: {on} players on the sigil; pressing X to go in with them "
                f"(waiting {SIGIL_COUNTDOWN_WAIT:.0f}s)")
    await client.send_key(Keycode.X, 0.1)
    deadline = time.monotonic() + SIGIL_COUNTDOWN_WAIT + 10  # + the loading screen
    while time.monotonic() < deadline:
        if await client.is_loading() or await client.zone_name() != zone:
            await wait_for_loading(client)
            logger.success(f"team up: in {await client.zone_name()} with the players from the sigil")
            return True
        await asyncio.sleep(1.0)
    logger.info("team up: the sigil's party didn't take us in; back to waiting")
    return False


def team_fight_at(circles, mates):
    """The duel circle a teammate is fighting on (one of them stands in it),
    nearest first by the order of `circles`; None if none."""
    for c in circles:
        if any(c.distance(m) < IN_FIGHT_RANGE for m in mates):
            return c
    return None


async def queued(client) -> bool:
    """Already in the Team Up queue: the badge by the friends button says
    Waiting (and the sigil then shows no TEAM UP!, only Resume)."""
    badge = await ui._visible_named(client.root_window, "btnTeamUp")
    if badge is None:
        return False  # (its text keeps saying Waiting while hidden)
    return "waiting" in ui._TAGS.sub("", await ui.named_text(client, "txtTeamUp")).lower()


async def close_events_window(client) -> bool:
    """The events window (Fall Scroll of Fortune...) that a press on the
    Waiting badge's spot opened: close it."""
    close = await ui._visible_named(client.root_window, "CloseClassProjectLaunchButton")
    if close is None:
        return False
    logger.info("team up: closing the events window")
    await ui.click_center(client, close)
    await asyncio.sleep(1.0)
    return True


async def cancel_queue(client) -> bool:
    """Leave the Team Up queue: press the Waiting badge and say yes to what
    it asks. True once no longer queued."""
    if not await queued(client):
        return True
    badge = await ui._visible_named(client.root_window, "btnTeamUp")
    if badge is None:
        logger.warning("team up: queued but no Waiting badge to press")
        return False
    logger.info("team up: leaving the queue (pressing the Waiting badge)")
    await ui.click_center(client, badge)
    await asyncio.sleep(1.5)
    await _dump(client, "cancel")
    if await close_events_window(client):
        return not await queued(client)
    box = await ui.modal_box(client)
    if box is not None:
        logger.info(f"team up: the game asks: {(await ui.modal_text(box))[:120]!r}; yes")
        await ui.press_modal_button(client, box, "centerButton")
    else:
        await _click(client, CANCEL_WORDS, "cancel")
    await asyncio.sleep(1.5)
    if await queued(client):
        logger.warning("team up: still queued; windows saved to state/teamup_cancel.txt")
        return False
    logger.success("team up: left the queue")
    return True


async def _fill_form(client, choices: tuple[str, ...] = TEAM_CHOICES) -> bool:
    """Tick the team type and minimum size (`choices`: Farming and 4, or for a
    quest Questing and 2) on the Team Up form, then press its TEAM UP!. True
    if the form was there."""
    form = await ui._visible_named(client.root_window, CONFIRM_WINDOW)
    if form is None:
        return False
    for name in choices:
        box = await ui._visible_named(form, name)
        if box is None:
            logger.warning(f"team up: no {name} on the form")
            continue
        logger.info(f"team up: ticking {name}")
        await ui.click_center(client, box)
        await asyncio.sleep(0.8)
    await _dump(client, "form_filled")
    button = await ui._visible_named(form, "TeamUpButton")
    if button is None:
        logger.warning("team up: no TEAM UP! button on the form")
        return True
    kind = "questing, 2+" if choices == QUEST_TEAM_CHOICES else "farming, 4+"
    logger.info(f"team up: pressing TEAM UP! on the form ({kind} players)")
    await ui.click_center(client, button)
    await asyncio.sleep(1.5)
    # (No closing of "other" forms here: after the press the one still on
    # screen can be ours, and closing it dropped the queue: twice the bot was
    # out of it three minutes after queuing, and the sigil offered TEAM UP!
    # again; the one run without that close found a team.)
    return True


async def close_waiting_window(client) -> bool:
    """The "<Dungeon> - Waiting for players to join.. / CANCEL TEAM UP!"
    window over a fight hid the cards: every cast missed for rounds (Fomori
    Giants while queued for the Keep of Ganelon). Close it by its X, never
    the cancel button (the queue stays: the Waiting badge). True if closed."""
    found = await _path_to_text(client.root_window, ("cancel team up!", "cancel team up"), [])
    if not found:
        return False
    cancel = found[-1]
    for holder in reversed(found[:-1]):
        close = await _named_like(holder, "close", skip=cancel)
        if close is not None:
            logger.info("team up: closing the Waiting-for-players window (still queued)")
            await ui.click_center(client, close)
            await asyncio.sleep(0.5)
            q = load_queue()
            if q:
                # (With it closed the Waiting badge reads as not queued: a lapse
                # was taken twice, minutes after, with the sigil still showing
                # us queued. No badge checks for this queue.)
                save_queue(q.get("dungeon", ""), q.get("quest", ""), sure=True)
            return True
    return False


async def _path_to_text(window, words: tuple[str, ...], path: list, depth: int = 0) -> list | None:
    """Root-to-window path of a visible window whose text is one of `words`."""
    try:
        if depth and not await window.is_visible():
            return None
        text = ui._TAGS.sub("", await window.maybe_text() or "").strip().lower()
    except Exception:
        text = ""
    here = path + [window]
    if text in words:
        return here
    if depth > 12:
        return None
    try:
        kids = await window.children()
    except Exception:
        return None
    for kid in kids:
        got = await _path_to_text(kid, words, here, depth + 1)
        if got:
            return got
    return None


async def _named_like(window, part: str, skip=None, depth: int = 0):
    """A visible window under `window` whose name contains `part`."""
    if depth > 6:
        return None
    try:
        kids = await window.children()
    except Exception:
        return None
    for kid in kids:
        try:
            if kid is skip or not await kid.is_visible():
                continue
            if part in (await kid.name() or "").lower():
                return kid
        except Exception:
            continue
        got = await _named_like(kid, part, skip, depth + 1)
        if got is not None:
            return got
    return None


async def close_stray_forms(client) -> int:
    """Close Team Up forms left open once ours is sent (one opened by the run
    before a restart stayed on screen over the queue). How many it closed."""
    closed = 0
    for _ in range(3):
        form = await ui._visible_named(client.root_window, CONFIRM_WINDOW)
        if form is None:
            break
        close = await ui._visible_named(form, "CloseTeamUpConfirmationWindow")
        if close is None:
            break
        logger.info("team up: closing another Team Up form still open")
        await ui.click_center(client, close)
        await asyncio.sleep(1.0)
        closed += 1
    return closed


async def _dump(client, stage: str):
    try:
        lines = await ui.dump_tree(client.root_window, max_depth=12)
        path = Path("state") / f"teamup_{stage}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"team up: could not save the {stage} windows: {exc!r}")


async def _click(client, words, stage: str) -> bool:
    button = await _find_button(client.root_window, words)
    if button is None:
        return False
    logger.info(f"team up: clicking {await button.name() or words[0]!r} ({stage})")
    await ui.click_center(client, button)
    await asyncio.sleep(1.5)
    return True


def defeated_inside_recently(last_death, hub_zone: str, now: float) -> bool:
    """Our last defeat was in a dungeon room of this world (not the hub itself)
    within RESUME_AFTER_DEFEAT: the team is likely still in there."""
    if not last_death:
        return False
    at, zone = last_death
    return (bool(zone) and zone != hub_zone and zone.split("/")[0] == hub_zone.split("/")[0]
            and now - at < RESUME_AFTER_DEFEAT)


TEAM_RUN_FILE = Path("state") / "team_run.json"  # the dungeon a team took us into, and when
TEAM_RUN_HOURS = 2.0


def _outdoors(zone: str) -> bool:
    """The open world (Saltmeadow Swamp), not a dungeon room: no "Interiors"
    in its path, not a known dungeon, not a Team Up dungeon."""
    from .dungeons import DungeonMemory

    if not zone or "/interiors/" in zone.lower():
        return False
    if zone in TEAM_UP_DUNGEONS or zone.startswith(TEAM_UP_PREFIXES):
        return False
    try:
        return zone not in DungeonMemory.load().dungeons
    except Exception:
        return True


def note_team_run(zone: str, new_run: bool = True) -> None:
    """In `zone` with a team: the run's first room (`new_run`), or a later
    room we followed the team into (the Keep of Ganelon's Crystal Cave)."""
    try:
        run = {} if new_run else json.loads(TEAM_RUN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        run = {}
    zones = [z for z in run.get("zones", [run.get("zone", "")]) if z]
    if _outdoors(zone) and not new_run:
        # Out in the open world (they left the Tree of Secrets for Saltmeadow
        # Swamp): the run is over; never a team room (it followed a teammate
        # round the swamp after the win).
        return
    if zone not in zones:
        zones.append(zone)
    try:
        TEAM_RUN_FILE.write_text(json.dumps({"zones": zones, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def in_team_run(zone: str) -> bool:
    """`zone` is a room of the team run of the last TEAM_RUN_HOURS."""
    try:
        run = json.loads(TEAM_RUN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    zones = run.get("zones", [run.get("zone", "")])
    if _outdoors(zone) and zone != zones[0]:
        return False  # (the open world after a run: not a team room)
    return zone in zones and time.time() - run.get("at", 0) < TEAM_RUN_HOURS * 3600


async def _resume_after_defeat(quester, zone: str) -> bool:
    """Defeated in the dungeon, the team fights on inside: the sigil's RESUME
    takes us back into that run (waiting for new players left it for good)."""
    client = quester.client
    if not defeated_inside_recently(quester.controller.last_death, zone or "", time.monotonic()):
        return False
    if not in_team_run(quester.controller.last_death[1]):
        # A solo loss (no team ever came): RESUME put us back in our own copy
        # of the Keep of Ganelon alone, where team mode never starts a fight.
        return False
    quester.controller.last_death = None  # one try per defeat
    if not await _click(client, ("resume",), "sigil"):
        logger.info("team up: no RESUME on the sigil after the defeat; waiting for players")
        return False
    logger.info("team up: pressed RESUME to rejoin the team's run after the defeat")
    for _ in range(15):
        box = await ui.modal_box(client)
        if box is not None:
            logger.info(f"team up: the game asks: {(await ui.modal_text(box))[:120]!r}")
            await ui.press_modal_button(client, box, "centerButton")
        if await client.is_loading() or await client.zone_name() != zone:
            await wait_for_loading(client)
            logger.success(f"team up: back in {await client.zone_name()} (resumed)")
            return True
        await asyncio.sleep(1.0)
    logger.warning("team up: RESUME didn't take us back in")
    await _dump(client, "resume")
    return False


async def team_up(quester, dungeon: str) -> str:
    """On the dungeon's sigil (prompt showing): press TEAM UP!, confirm what
    the game asks, and wait for a team to form and take us in. "in" once in
    the dungeon, "switched" after moving to another realm (nobody around for
    NO_PLAYERS_SWITCH: queue again there), "none" with no team."""
    client = quester.client
    zone = await client.zone_name()
    await _dump(client, "sigil")
    use_queue = USE_QUEUE or dungeon in QUEUE_DUNGEONS or dungeon in team_list()
    choices = QUEST_TEAM_CHOICES if dungeon in team_list() else TEAM_CHOICES
    if not use_queue:
        # No queue: wait on the sigil for players to gather (and go in with them).
        await close_stray_forms(client)
        # (No cancel_queue here: the Waiting badge's spot is the events button
        # when not queued, and pressing it opened the events window.)
        await close_events_window(client)
        logger.info("team up: waiting on the sigil for players to gather (not queueing)")
    if await _resume_after_defeat(quester, zone):
        return "in"
    # The form may still be open from before (a restart): fill that one in.
    already = False
    form_done = not use_queue or await _fill_form(client, choices)
    if not form_done and await queued(client):
        logger.info("team up: already in the queue (Waiting); waiting on")
        form_done = True
        already = True  # (back here on a "lapse" that wasn't one: the badge reads badly)
    if not form_done and not await _click(client, TEAM_UP_WORDS, "sigil"):
        # The sigil shows only Resume while we're queued (after a restart the
        # Waiting badge wasn't always readable): wait for the team to take us.
        logger.info("team up: no TEAM UP! button on the sigil (already queued?); waiting on")
        form_done = True
        already = True
    await _dump(client, "window")
    if dungeon in team_list():
        # A main-quest boss too hard alone (the player): queued, don't stand
        # at the sigil; side quests fill the wait and the team-ready prompt
        # is accepted wherever we are (team_ready_watch). 2+ players.
        for _ in range(0 if form_done or await _fill_form(client, choices) else 3):
            if not await _click(client, CONFIRM_WORDS, "window"):
                break
        save_queue(dungeon, team_list()[dungeon], sure=already)
        logger.info(f"team up: queued for {dungeon.split('/')[-1]} (questing, 2+); "
                    "side quests meanwhile, the main quest comes back when the team is ready")
        return "queued"
    quester.controller.allow_idle(TEAM_UP_WAIT + 60)
    try:
        # Whatever the Team Up window asks (join / search / confirm): accept.
        for _ in range(0 if form_done or await _fill_form(client, choices) else 3):
            if not await _click(client, CONFIRM_WORDS, "window"):
                break
            await _dump(client, "window_after")
        box = await ui.modal_box(client)
        if box is not None:
            await ui.press_modal_button(client, box, "centerButton")
        logger.info(f"team up: waiting for a team for {dungeon} (up to {TEAM_UP_WAIT // 60} min)")
        started = time.monotonic()
        last_report = started
        center = await sigil_center(client, await client.body.position())
        last_press = [0.0]
        last_player = time.monotonic()
        last_requeue = time.monotonic()
        last_sigil_check = 0.0
        while time.monotonic() - started < TEAM_UP_WAIT:
            await close_stray_forms(client)
            if await _join_party_on_sigil(client, zone, center, last_press, quester._far_spot,
                                          need=1 if dungeon in team_list() else PARTY_ON_SIGIL):
                return "in"
            if time.monotonic() - last_sigil_check > SIGIL_CHECK_EVERY:
                last_sigil_check = time.monotonic()
                await step_onto_sigil(client, center, quester._far_spot)
            if use_queue and time.monotonic() - last_requeue > REQUEUE_EVERY:
                # Not queued (a cooldown showed "TEAM UP! IN 05:51"): once the
                # sigil offers TEAM UP! again, queue.
                last_requeue = time.monotonic()
                if await _click(client, ("team up!",), "sigil") and await _fill_form(client, choices):
                    logger.info("team up: queued again")
            mates = await teammates(client, await client.body.position())
            if any(m.distance(center) < SIGIL_AREA for m in mates):
                last_player = time.monotonic()
            elif time.monotonic() - last_player > NO_PLAYERS_SWITCH:
                from .realm import switch_realm

                logger.info(f"team up: no other players near the sigil for {NO_PLAYERS_SWITCH // 60} min; "
                            "switching realm")
                if await switch_realm(client):
                    return "switched"
                last_player = time.monotonic()  # it didn't work: wait another spell
            if await client.is_loading() or await client.zone_name() != zone:
                await wait_for_loading(client)
                logger.success(f"team up: in {await client.zone_name()} with a team")
                return "in"
            box = await ui.modal_box(client)
            if box is not None:
                text = (await ui.modal_text(box)).lower()
                logger.info(f"team up: the game asks: {text[:120]!r}")
                await _dump(client, "ready")
                await ui.press_modal_button(client, box, "centerButton")  # yes / go
            elif await _click(client, ("go", "teleport", "enter", "ready", "accept"), "ready"):
                pass
            if time.monotonic() - last_report > 60:
                last_report = time.monotonic()
                logger.info(f"team up: still waiting ({(time.monotonic() - started) / 60:.0f} min)")
            await asyncio.sleep(2.0)
        logger.warning(f"team up: no team within {TEAM_UP_WAIT // 60} min")
        return "none"
    finally:
        quester.controller.end_idle()
