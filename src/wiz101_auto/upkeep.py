"""Out-of-combat maintenance: potions, wisps, dialogue, stray popups."""

from __future__ import annotations

import asyncio
import math
import time

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .collect import away_from, landmarks, spread_points
from .config import QuestConfig, UpkeepConfig
from .wisps import ANY, BOTH, HEALTH, MANA, WispMemory, usable, wisp_kind


async def is_free(client) -> bool:
    """Not loading, not fighting and not in a dialogue."""
    try:
        return not (
            await client.is_loading()
            or await client.in_battle()
            or await ui.is_visible(client, ui.ADVANCE_DIALOG)
        )
    except Exception:
        return False


async def wait_until_free(client, timeout: float = 60.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await is_free(client):
            return True
        await asyncio.sleep(0.25)
    return False


async def wait_for_loading(client, appear_timeout: float = 2.0):
    """Give a loading screen a moment to appear, then wait for it to finish."""
    loop = asyncio.get_running_loop()
    end = loop.time() + appear_timeout
    while loop.time() < end and not await client.is_loading():
        await asyncio.sleep(0.1)
    while await client.is_loading():
        await asyncio.sleep(0.2)


async def health_mana(client) -> tuple[float, float]:
    hp, max_hp = await client.stats.current_hitpoints(), await client.stats.max_hitpoints()
    mana, max_mana = await client.stats.current_mana(), await client.stats.max_mana()
    return (hp / max_hp if max_hp else 1.0), (mana / max_mana if max_mana else 1.0)


async def maintain(client, cfg: UpkeepConfig):
    hp, mana = await health_mana(client)

    if cfg.collect_wisps and hp < cfg.wisp_health_ratio:
        sprinter = client  # SprintyClient (bot.new_handler)
        try:
            wisps = await sprinter.get_health_wisps()
            if mana < 0.5:
                wisps += await sprinter.get_mana_wisps()
            for wisp in await sprinter.find_safe_entities_from(wisps):
                await client.teleport(await wisp.location())
                await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug(f"wisp collection failed: {exc}")
        hp, mana = await health_mana(client)

    if cfg.use_potions and (hp < cfg.potion_health_ratio or mana < cfg.potion_mana_ratio):
        if await client.stats.potion_charge() >= 1.0:
            logger.info(f"drinking potion (hp {hp:.0%}, mana {mana:.0%})")
            await ui.click(client, ui.POTION_BUTTON)
            await asyncio.sleep(1.0)
        elif hp < 0.25:
            logger.warning("low health and no potions; the next fight may be lost")


_memory: WispMemory | None = None


def wisp_memory() -> WispMemory:
    global _memory
    if _memory is None:
        _memory = WispMemory.load()
    return _memory


def _pt(xyz) -> tuple[float, float, float]:
    return (xyz.x, xyz.y, xyz.z)


async def scan_wisps(client) -> list:
    """Visible health and mana wisps; their positions are remembered for later,
    along with wisp stand-ins (fixed markers where a taken wisp respawns)."""
    try:
        health = await client.get_health_wisps()
        mana = await client.get_mana_wisps()
        wisps = health + mana
        seen = [(w, HEALTH) for w in health] + [(w, MANA) for w in mana]
        for e in await client.get_base_entities_with_vague_name("StandIn"):
            name = (await (await e.object_template()).object_name()) or ""
            if "wisp" in name.lower():
                seen.append((e, wisp_kind(name)))
        if seen:
            zone = await client.zone_name() or "?"
            added = 0
            for w, kind in seen:
                added += wisp_memory().record(zone, [_pt(await w.location())], kind)
            if added:
                logger.debug(f"remembered {added} new wisp spot(s) in {zone}")
                wisp_memory().save()
        return wisps
    except Exception as exc:
        logger.debug(f"wisp scan failed: {exc}")
        return []


async def mob_positions(client) -> list[tuple[float, float, float]]:
    """Places to keep clear of: enemies, and fights already going on (duel
    circles: another player's fight pulls in whoever lands beside it)."""
    from .collect import duel_circles

    try:
        mobs = [_pt(await m.location()) for m in await client.get_mobs()]
    except Exception:
        mobs = []
    return mobs + await duel_circles(client)


UNREACHABLE_WISP_MINUTES = 15.0
_unreachable: dict[tuple[str, tuple[float, float, float]], float] = {}  # (zone, spot) -> when


def _is_unreachable(zone: str, p: tuple[float, float, float]) -> bool:
    now = time.monotonic()
    return any(
        z == zone and now - t < UNREACHABLE_WISP_MINUTES * 60 and math.dist(p, q) < 60
        for (z, q), t in _unreachable.items()
    )


async def collect_wisps(client, cfg: UpkeepConfig, *, limit: int = 6) -> int:
    """Teleport onto nearby health/mana wisps that aren't close to mobs. Returns how many were taken.

    A wisp still there after landing on it can't be picked up (e.g. outside the
    playable map): it's skipped for a while, forgotten as a spawn spot, and the
    wizard goes back to where it was."""
    try:
        zone = await client.zone_name() or "?"
        await scan_wisps(client)  # remember every spawn spot seen
        # The game won't let a full-health wizard take a health wisp (or a
        # full-mana one a mana wisp), so only go for the kinds we can use.
        hp, mana = await health_mana(client)
        wisps = []
        if hp < 0.99:
            wisps += await client.get_health_wisps()
        if mana < 0.99:
            wisps += await client.get_mana_wisps()
        if not wisps:
            return 0
        safe = await client.find_safe_entities_from(wisps, safe_distance=cfg.wisp_safe_distance)
        if not safe:
            return 0
        start = await client.body.position()
        spots = [_pt(await w.location()) for w in safe]
        spots = [p for p in spots if not _is_unreachable(zone, p)]
        spots = sorted(spots, key=lambda p: math.dist(p, _pt(start)))[:limit]
        taken = 0
        stranded = False
        for spot in spots:
            before = await health_mana(client)
            await client.teleport(XYZ(*spot))
            await asyncio.sleep(0.8)
            remaining = [_pt(await w.location()) for w in await scan_wisps(client)]
            after = await health_mana(client)
            if after[0] >= 0.99 and after[1] >= 0.99:
                break  # topped up; whatever is left isn't unreachable, just unneeded
            # Only judge a wisp unreachable if both kinds were still needed (a
            # full-health wizard can't take a health wisp even when it's reachable).
            needed_both = before[0] < 0.99 and before[1] < 0.99
            gained = after[0] > before[0] or after[1] > before[1]
            if needed_both and not gained and any(math.dist(spot, r) < 60 for r in remaining):
                logger.info(f"wisp at ({spot[0]:.0f}, {spot[1]:.0f}) can't be collected; skipping it")
                _unreachable[(zone, spot)] = time.monotonic()
                if wisp_memory().forget(zone, spot):
                    wisp_memory().save()
                stranded = True
            else:
                taken += 1
        if stranded:
            await client.teleport(start)
            await asyncio.sleep(0.5)
        return taken
    except Exception as exc:
        logger.debug(f"wisp collection failed: {exc}")
        return 0


async def visit_known_spot(client, cfg: UpkeepConfig, zone: str, need=BOTH) -> bool:
    """Teleport to a remembered spawn point of a wisp kind we need (away from
    mobs) and grab what's there."""
    try:
        return await _visit_known_spot(client, cfg, zone, need)
    except Exception as exc:  # e.g. WizWalker's ExceptionalTimeout while a popup blocks the game
        logger.debug(f"wisp spot visit failed: {exc!r}")
        return False


async def _visit_known_spot(client, cfg: UpkeepConfig, zone: str, need) -> bool:
    me = _pt(await client.body.position())
    spot = wisp_memory().next_spot(
        zone, me, await mob_positions(client), safe_distance=cfg.wisp_safe_distance, need=need
    )
    if spot is None:
        return False
    wisp_memory().mark_visited(zone, spot)
    logger.info(f"checking a known {wisp_memory().kind_of(zone, spot)} wisp spawn point")
    await client.teleport(XYZ(*spot))
    await asyncio.sleep(1.0)
    await collect_wisps(client, cfg)
    return True


async def sweep_for_wisps(client, cfg: UpkeepConfig) -> int:
    """Hop across the zone's landmarks (on the map, away from mobs) to discover
    its wisp spawns; wisps only load near the wizard."""
    start = await client.body.position()
    # Only skip landmarks right next to mobs: busy streets (Unicorn Way) would
    # otherwise leave nothing to search. Wisps near mobs are still skipped.
    spots = away_from(await landmarks(client), await mob_positions(client), SWEEP_MOB_DISTANCE)
    points = spread_points(spots, _pt(start), WISP_SWEEP_SPACING)[:WISP_SWEEP_MAX]
    zone = await client.zone_name() or "?"
    before = len(wisp_memory().spots.get(zone, []))
    logger.info(f"searching {zone} for wisp spawn points ({len(points)} spots)")
    for p in points:
        if not await is_free(client):
            break
        await client.teleport(XYZ(*p))
        await asyncio.sleep(0.8)
        await scan_wisps(client)
    found = len(wisp_memory().spots.get(zone, [])) - before
    logger.info(f"found {found} new wisp spawn point(s)")
    return found


async def can_move(client) -> bool:
    """Tap forward and back: a wizard wedged in a wall or building barely moves."""
    start = _pt(await client.body.position())
    moved = 0.0
    for key in (Keycode.W, Keycode.S):
        await client.send_key(key, 0.5)
        await asyncio.sleep(0.2)
        moved = max(moved, math.dist(start, _pt(await client.body.position())))
    return moved > STUCK_MOVE_DISTANCE


async def unstick(client) -> bool:
    """If the wizard can't walk (clipped into geometry after a teleport), move it
    to the nearest on-map landmark it can walk from. True if it was stuck."""
    try:
        if await can_move(client):
            return False
        me = _pt(await client.body.position())
        logger.warning(f"wizard seems stuck at ({me[0]:.0f}, {me[1]:.0f}): can't walk; moving to a landmark")
        spots = away_from(await landmarks(client), await mob_positions(client), 400.0)
        spots = sorted((p for p in spots if math.dist(p, me) > 150), key=lambda p: math.dist(p, me))
        for p in spots[:8]:
            await client.teleport(XYZ(*p))
            await asyncio.sleep(1.0)
            if await can_move(client):
                logger.success(f"unstuck: now at ({p[0]:.0f}, {p[1]:.0f})")
                return True
        logger.warning("still stuck after trying nearby landmarks")
        return True
    except Exception as exc:
        logger.debug(f"unstick failed: {exc!r}")
        return False


async def move_to_safety(client, safe_distance: float = 1500.0, why: str = "to rest") -> bool:
    """If an enemy (or a fight going on) is close, teleport to the nearest
    spot with none around: landmarks, or walkway points at floor height
    (cameras and other path markers float off the walkable map)."""
    from .collect import floor_points, path_points

    try:
        me = await client.body.position()
        hazards = await mob_positions(client)
        if all(math.dist(p, _pt(me)) > safe_distance for p in hazards):
            return False
        spots = await landmarks(client) + floor_points(await path_points(client), me.z)
        candidates = away_from(spots, hazards, safe_distance)
        if not candidates:
            return False
        spot = min(candidates, key=lambda p: math.dist(p, _pt(me)))
        logger.info(f"enemies close by: moving somewhere clear {why}")
        await client.teleport(XYZ(*spot))
        await asyncio.sleep(1.0)
        return True
    except Exception as exc:
        logger.debug(f"could not find a safe spot: {exc}")
        return False


SWEEP_MOB_DISTANCE = 1000.0  # hopping next to a mob starts a fight
WISP_SWEEP_SPACING = 2500.0  # wisps load within roughly this range
WISP_SWEEP_MAX = 16
STUCK_MOVE_DISTANCE = 25.0  # walking 0.5s moves ~100+; less means wedged in geometry
WISP_GAIN = 0.03  # smallest health/mana ratio gain that means a wisp was taken
FRUITLESS_VISITS = 3  # empty wisp spots in a row before going elsewhere to heal
# Interiors recovery gave up on (walk out on the quest path instead).
_leaving_interior: set[str] = set()
REST_PROBE_SECONDS = 60.0  # resting this long without gaining anything: no wisps here, heal elsewhere
BARREN_SECONDS = 1800.0  # how long a zone that gave nothing is skipped as a place to heal
_barren: dict[str, float] = {}  # zone -> when resting there gave nothing


def note_barren(zone: str, now: float | None = None):
    import time as _time

    _barren[zone] = _time.monotonic() if now is None else now


def barren_zones(now: float | None = None) -> set[str]:
    import time as _time

    now = _time.monotonic() if now is None else now
    return {z for z, t in _barren.items() if now - t < BARREN_SECONDS}


def best_wisp_zone(
    current_zone: str,
    spots: dict | None = None,
    preferred: list[str] = (),
    need=BOTH,
    kinds: dict | None = None,
    avoid: set[str] = frozenset(),
) -> str | None:
    """Where to recover: a preferred heal zone in the same world when health is
    needed, else the zone with the most remembered spots of the needed wisp
    kind (Unicorn Way as a Wizard City fallback)."""
    if spots is None:
        spots, kinds = wisp_memory().spots, wisp_memory().kinds
    kinds = kinds or {}
    world = current_zone.split("/", 1)[0]
    in_world = [z for z in preferred if z.split("/", 1)[0] == world and z != current_zone and z not in avoid]
    if HEALTH in need and in_world:
        return in_world[0]
    same_world = [
        (sum(usable(kinds.get(z, {}).get(tuple(p), ANY), need) for p in pts), z)
        for z, pts in spots.items()
        if z.split("/", 1)[0] == world
    ]
    zones = sorted(((n, z) for n, z in same_world if n >= 3), reverse=True)
    for _, z in zones:
        if z != current_zone and z not in avoid:
            return z
    if in_world:
        return in_world[0]
    unicorn = "WizardCity/WC_Streets/WC_Unicorn"
    if world == "WizardCity" and current_zone != unicorn and unicorn not in avoid:
        return "WizardCity/WC_Streets/WC_Unicorn"
    return None


CLOSE_ENOUGH = 0.10  # within this much of the fight thresholds counts when no wisps help


def close_enough(cfg: UpkeepConfig, hp: float, mana: float) -> bool:
    return hp >= cfg.min_health_to_fight - CLOSE_ENOUGH and mana >= cfg.min_mana_to_fight - CLOSE_ENOUGH


def needed_wisps(cfg: UpkeepConfig, hp: float, mana: float) -> frozenset[str]:
    """Which wisp kinds recovery still needs."""
    need = set()
    if hp < cfg.rest_until_health:
        need.add(HEALTH)
    if mana < cfg.rest_until_mana:
        need.add(MANA)
    return frozenset(need or BOTH)


async def recover(client, cfg: UpkeepConfig, controller, go_to_zone=None, trip=None, mark=None) -> bool:
    """Make sure the wizard is healthy before engaging anything.

    With `mark` (async, True if it marked the spot): mark first, then heal in
    this zone (wisps in view, remembered spots, a sweep) and teleport back to
    where it started. If this zone lacks what is needed (no health wisps, or
    mana still short), `trip(marked=..., force=...)` heals from the world hub
    and Recalls to the mark (True if it went).

    Returns True when it's fine to carry on questing, False if something
    (a fight, dialogue, loading) interrupted the recovery.
    """
    hp, mana = await health_mana(client)
    if not cfg.needs_recovery(hp, mana):
        return True
    zone_now = await client.zone_name() or ""
    if zone_now in _leaving_interior:
        return True  # already found nothing here; the quest is walking us out
    _leaving_interior.clear()
    logger.info(f"health {hp:.0%}, mana {mana:.0%}: recovering before going on")
    marked = bool(mark and await mark())
    start_pos = await client.body.position()

    async def back_to_start():
        """Healed in this zone: go back to where healing began."""
        here = await client.body.position()
        if await client.zone_name() == zone_now and math.dist(_pt(start_pos), _pt(here)) > 400:
            await client.teleport(start_pos)
            await asyncio.sleep(0.5)

    loop = asyncio.get_running_loop()
    started = loop.time()
    last_report = started
    rested = False
    rest_start: tuple[float, float, float] | None = None  # (when, hp, mana) resting began
    moved_on = False  # already left a zone that gave nothing
    tripped = False  # tried a heal trip through the hub
    travelled = False
    fruitless = 0  # remembered spots visited in a row without gaining anything
    swept: set[str] = set()
    while True:
        await controller.checkpoint()
        if not await is_free(client):
            return False
        hp, mana = await health_mana(client)
        if cfg.recovered(hp, mana):
            logger.success(f"recovered to {hp:.0%} health, {mana:.0%} mana")
            await back_to_start()
            return True

        low = hp < cfg.potion_health_ratio or mana < cfg.potion_mana_ratio
        if cfg.use_potions and low and await client.stats.potion_charge() >= 1.0:
            logger.info(f"drinking potion (hp {hp:.0%}, mana {mana:.0%})")
            await ui.click(client, ui.POTION_BUTTON)
            await asyncio.sleep(1.5)
            continue

        if cfg.collect_wisps:
            # 1. wisps in view  2. remembered spawn points  3. search the zone once
            if await collect_wisps(client, cfg):
                await asyncio.sleep(0.5)
                now_hp, now_mana = await health_mana(client)
                if now_hp > hp or now_mana > mana:
                    continue
            zone = await client.zone_name() or "?"
            need = needed_wisps(cfg, hp, mana)
            if await visit_known_spot(client, cfg, zone, need):
                rested = False
                now_hp, now_mana = await health_mana(client)
                # Passive regeneration ticks up a little on every visit; only a real
                # wisp (a few % at once) counts as finding something.
                gained = now_hp - hp >= WISP_GAIN or now_mana - mana >= WISP_GAIN
                fruitless = 0 if gained else fruitless + 1
                if fruitless < FRUITLESS_VISITS:
                    continue
            if zone not in swept:
                swept.add(zone)
                if await sweep_for_wisps(client, cfg):
                    continue
            poor_zone = wisp_memory().count(zone, need) < 3 or fruitless >= FRUITLESS_VISITS
            if trip and not tripped and poor_zone:
                # This zone lacks what is needed (health wisps, or mana after
                # healing here): the world hub, then Recall to the mark.
                tripped = True
                if await trip(marked=marked):
                    return True
            if go_to_zone and not travelled and poor_zone:
                # No wisps to be had here right now (e.g. the hub after a defeat):
                # go heal where they spawn instead of waiting for respawns.
                travelled = True
                fruitless = 0
                dest = best_wisp_zone(zone, preferred=cfg.heal_zones, need=need, avoid=barren_zones())
                if dest:
                    what = " and ".join(sorted(need))
                    logger.info(f"no {what} wisps in {zone}; going to {dest} for them")
                    if await go_to_zone(dest):
                        continue
                if "interiors" in zone.lower():
                    # A dungeon/building with no wisps and no known way out: resting
                    # here takes minutes. Let the quest path walk out, heal outside.
                    logger.info(f"no wisps or route out of {zone}; following the quest out to heal")
                    _leaving_interior.add(zone)
                    return True
            if fruitless >= FRUITLESS_VISITS and close_enough(cfg, hp, mana):
                # Nothing to be had around here and we're nearly there: waiting for
                # regeneration costs minutes that questing puts to better use.
                logger.info(f"no wisps here; {hp:.0%} health, {mana:.0%} mana is enough to go on")
                await back_to_start()
                return True

        if close_enough(cfg, hp, mana):
            # Nothing to pick up nearby, and resting regenerates little or
            # nothing: this close to the threshold, questing on is better.
            logger.info(f"nothing to heal with here; {hp:.0%} health, {mana:.0%} mana is enough to go on")
            await back_to_start()
            return True
        if not rested:
            await move_to_safety(client)
            rested = True
            rest_start = (loop.time(), hp, mana)
        elif rest_start and loop.time() - rest_start[0] > REST_PROBE_SECONDS and not moved_on:
            gained = hp - rest_start[1] >= 0.01 or mana - rest_start[2] >= 0.01
            if not gained:
                # A minute of rest gave nothing: no wisps (and no regeneration)
                # here. Heal where we know we can instead of waiting.
                zone = await client.zone_name() or "?"
                note_barren(zone)
                moved_on = True
                waited = f"{REST_PROBE_SECONDS:.0f}s"
                logger.info(f"nothing recovered in {waited} in {zone}; going somewhere to heal")
                if trip and await trip(force=True, marked=marked):
                    return True
                dest = best_wisp_zone(zone, preferred=cfg.heal_zones, need=needed_wisps(cfg, hp, mana),
                                      avoid=barren_zones())
                if dest and go_to_zone and await go_to_zone(dest):
                    logger.info(f"went to {dest} to heal")
                    rested, rest_start = False, None
                    continue
                logger.info("no known place to heal; carrying on")
                return True

        elapsed = loop.time() - started
        if elapsed > cfg.rest_max_minutes * 60:
            if not cfg.needs_recovery(hp, mana):
                return True
            # Never end the session over it: carry on and heal at the next
            # chance (wisps on the way, a heal trip, a level-up).
            logger.warning(
                f"could not recover (health {hp:.0%}, mana {mana:.0%}) within "
                f"{cfg.rest_max_minutes:g} min; carrying on"
            )
            return True
        if loop.time() - last_report > 60:
            logger.info(f"resting: health {hp:.0%}, mana {mana:.0%}; waiting for regeneration or wisps")
            last_report = loop.time()
        controller.allow_idle(10)
        await asyncio.sleep(5)


_CLOSE_NAMES = (
    "Exit",
    "exit",
    "Close",
    "close",
    "btnClose",
    "CloseButton",
    "Close_Button",
    "btnExit",
    "Cancel",
)


async def close_crowns_shop(client) -> bool:
    """Close any open Crowns shop / offer window. Only top-level windows are
    checked, so this is cheap enough to run every step."""
    for parent in (client.root_window, await client.get_world_view_window()):
        try:
            children = await parent.children()
        except Exception:
            continue
        for w in children:
            try:
                name = await w.name() or ""
                if "crown" not in name.lower() or not await w.is_visible():
                    continue
            except Exception:
                continue
            logger.warning(f"crowns window {name!r} is open; closing it")
            for close_name in _CLOSE_NAMES:
                for btn in await w.get_windows_with_name(close_name):
                    try:
                        if await btn.is_visible():
                            await client.mouse_handler.click_window(btn)
                            await asyncio.sleep(0.5)
                            return True
                    except Exception:
                        pass
            await client.send_key(Keycode.ESC, 0.1)
            await asyncio.sleep(0.5)
            return True
    return False


async def clear_popups(client):
    await close_crowns_shop(client)
    await ui.close_chat(client)
    await ui.dismiss_notice(client)
    if await ui.is_visible(client, ui.ENDORSEMENT):
        logger.info("endorsing the wizard we fought with (Friendly) to close the window")
        if not await ui.click(client, ui.ENDORSE_FRIENDLY):
            await ui.click(client, ui.ENDORSE_CLOSE)
        await asyncio.sleep(0.5)
        if await ui.is_visible(client, ui.ENDORSEMENT):
            await ui.click(client, ui.ENDORSE_CLOSE)
    await ui.click(client, ui.CANCEL_CHEST_REROLL)
    if await ui.is_visible(client, ui.MISSING_AREA_RETRY):
        await ui.click(client, ui.MISSING_AREA_RETRY)


class DialoguePolicy:
    """Whether quest offers should be accepted right now.

    Offers from the NPC the quest helper sent us to are the story line and
    must be accepted; offers from anyone else are side quests.
    """

    def __init__(self):
        self._accept_until = 0.0

    def accept_offers_for(self, seconds: float = 30.0):
        self._accept_until = time.monotonic() + seconds

    @property
    def accepting(self) -> bool:
        return time.monotonic() < self._accept_until


async def dialogue_loop(client, cfg: QuestConfig, controller, policy: DialoguePolicy | None = None):
    """Advance NPC dialogue as it appears. Runs for the whole session."""
    policy = policy or DialoguePolicy()
    last_offer, tries = "", 0
    while not controller.stopped.is_set():
        try:
            if not controller.paused and await ui.is_visible(client, ui.ADVANCE_DIALOG):
                offer = await ui.is_visible(client, ui.DECLINE_QUEST)
                if offer and (cfg.accept_side_quests or policy.accepting):
                    text = await ui.text_at(client, ui.DIALOG_TEXT)
                    tries = tries + 1 if text == last_offer else 0
                    last_offer = text
                    if tries == 0:
                        logger.info(f"accepting quest: {text[:80]}")
                    # The offer's accept button doesn't always take the usual
                    # (left-shifted) click: cycle through other ways of pressing it.
                    # (Never Enter: it opens the chat box and swallows later keys.)
                    how = tries % 3
                    if how == 0:
                        if not await ui.click(client, ui.ADVANCE_DIALOG):
                            await client.send_key(Keycode.SPACEBAR)
                    elif how == 1:
                        w = await ui.window_at(client, ui.ADVANCE_DIALOG)
                        if w is not None:
                            await ui.click_center(client, w)
                    else:
                        await client.send_key(Keycode.SPACEBAR)
                    if tries in (1, 2):
                        logger.debug(f"quest offer still open; accepting another way ({how})")
                    await asyncio.sleep(0.6)
                elif offer:
                    text = await ui.text_at(client, ui.DIALOG_TEXT)
                    logger.info(f"declining side quest: {text[:80]}")
                    await client.send_key(Keycode.ESC)
                    await asyncio.sleep(0.1)
                    await client.send_key(Keycode.ESC)
                else:
                    await client.send_key(Keycode.SPACEBAR)
        except Exception as exc:
            logger.trace(f"dialogue loop: {exc}")
        await asyncio.sleep(0.3)
