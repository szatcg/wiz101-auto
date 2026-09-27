"""Keep the best gear equipped.

Item stats aren't readable from WizWalker, so gear is judged the way a player
would: put an item on and look at the wizard's real stats (health, damage and
accuracy for its school, resistance, power pips...). For each slot the bot
tries every item on the backpack tab and keeps the one that scores best.
Runs at startup and whenever the backpack gains an item.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger
from wizwalker import Keycode

from . import ui
from .combat.reader import SCHOOL_ORDER

PAGE = ["WorldView", "DeckConfiguration", "InventorySpellbookPage"]
EQUIP = [*PAGE, "windowForBtns", "Layout", "Equip_Item"]
NEXT_PAGE = [*PAGE, "rightscroll"]
# Slots worth optimising. The deck and wand stay as they are (a starter wand's
# item cards are the only spells some wizards have).
SLOT_TABS = ("Tab_Hat", "Tab_Robe", "Tab_Shoes", "Tab_Athame", "Tab_Amulet", "Tab_Ring")
ITEMS_PER_PAGE = 8
MAX_PAGES = 4


@dataclass
class StatSnapshot:
    health: float = 0
    mana: float = 0
    damage: float = 0  # own-school outgoing damage, fraction
    accuracy: float = 0
    resist: float = 0  # average incoming reduction over the main schools
    power_pip: float = 0
    critical: float = 0  # rating
    block: float = 0  # rating


def gear_score(s: StatSnapshot) -> float:
    """One number for 'how strong is the wizard'. Weights favour survival and
    damage for PvE questing: 1 point per health, 20 per 1% damage, 15 per 1%
    resist, 10 per 1% power pip chance, 5 per 1% accuracy."""
    return (
        s.health
        + s.mana * 0.2
        + s.damage * 2000
        + s.resist * 1500
        + s.power_pip * 1000
        + s.accuracy * 500
        + s.critical * 0.5
        + s.block * 0.5
    )


def _pick(values: list[float], school: str, all_schools: float = 0.0) -> float:
    try:
        return values[SCHOOL_ORDER.index(school)] + all_schools
    except (ValueError, IndexError):
        return all_schools


async def read_stats(client, school: str) -> StatSnapshot:
    st = client.stats
    school = school.lower()
    resists = await st.dmg_reduce_percent()
    main = resists[:7]  # fire..balance
    return StatSnapshot(
        health=await st.max_hitpoints(),
        mana=await st.max_mana(),
        damage=_pick(await st.dmg_bonus_percent(), school, await st.dmg_bonus_percent_all()),
        accuracy=_pick(await st.acc_bonus_percent(), school, await st.acc_bonus_percent_all()),
        resist=(sum(main) / len(main) if main else 0) + await st.dmg_reduce_percent_all(),
        power_pip=await st.power_pip_base() + await st.power_pip_bonus_percent_all(),
        critical=_pick(await st.critical_hit_rating_by_school(), school),
        block=_pick(await st.block_rating_by_school(), school),
    )


# Template name prefixes of wearable gear ("Hat-T1-018", "Shoe-T1-011"...).
WEARABLE_PREFIXES = ("hat-", "robe-", "shoe-", "athame-", "amulet-", "ring-")


def is_wearable(template_name: str) -> bool:
    return template_name.lower().startswith(WEARABLE_PREFIXES)


async def owned_item_ids(client) -> set[int]:
    """Ids of wearable gear in the backpack and equipped. Quest items, emotes and
    the like don't count, so collecting quest items doesn't trigger a gear check."""
    from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate

    ids: set[int] = set()
    co = client.client_object
    for behavior in (await co.try_get_inventory_behavior(), await co.try_get_equipment_behavior()):
        if behavior is None:
            continue
        for item in await behavior.item_list():
            try:
                core = await item.object_template()
                template = WizGameObjectTemplate(client.hook_handler, await core.read_base_address())
                name = await template.object_name()
                if is_wearable(name):
                    ids.add(await item.global_id_full())
            except Exception:
                pass
    return ids


class GearManager:
    def __init__(self, client, school: str):
        self.client = client
        self.school = school
        self._known_ids: set[int] | None = None

    async def _score(self) -> float:
        await asyncio.sleep(0.8)  # let the stats update after equipping
        return gear_score(await read_stats(self.client, self.school))

    async def _open(self) -> bool:
        for _ in range(3):
            if await ui.is_visible(self.client, PAGE):
                return True
            await self.client.send_key(Keycode.B, 0.1)
            await asyncio.sleep(1.2)
        return await ui.is_visible(self.client, PAGE)

    async def _close(self):
        for _ in range(3):
            if not await ui.is_visible(self.client, PAGE):
                return
            await self.client.send_key(Keycode.B, 0.1)
            await asyncio.sleep(0.8)

    async def _items_on_page(self) -> list[tuple[str, str, bool]]:
        """(window name, item name, equipped) for the items shown."""
        out = []
        for i in range(1, ITEMS_PER_PAGE + 1):
            path = [*PAGE, f"Item_{i}"]
            if not await ui.is_visible(self.client, path):
                continue
            name = (await ui.text_at(self.client, path)).strip()
            if not name:
                continue  # empty slot on a partly filled page
            equipped = await ui.is_visible(self.client, [*path, "fist"])
            out.append((f"Item_{i}", name, equipped))
        return out

    async def _equip(self, window: str) -> None:
        await ui.click(self.client, [*PAGE, window])
        await asyncio.sleep(0.4)
        await ui.click(self.client, EQUIP)

    async def _scan_tab(self, tab: str) -> list[tuple[str, bool]]:
        """(item name, equipped) for every item on the tab, from page 1."""
        await ui.click(self.client, [*PAGE, "ButtonLayout", tab])  # also resets to page 1
        await asyncio.sleep(0.8)
        found: list[tuple[str, bool]] = []
        for _ in range(MAX_PAGES):
            new = [(n, e) for _, n, e in await self._items_on_page() if n not in {f[0] for f in found}]
            if not new:
                break
            found += new
            if not await ui.click(self.client, NEXT_PAGE):
                break
            await asyncio.sleep(0.6)
        return found

    async def _equip_by_name(self, tab: str, name: str) -> bool:
        """Equip `name` (found by name: equipping reorders the list, and pressing
        Equip on an item that's already on takes it off). True if it's on after."""
        for attempt in range(2):
            await ui.click(self.client, [*PAGE, "ButtonLayout", tab])
            await asyncio.sleep(0.8)
            for _ in range(MAX_PAGES):
                for window, n, equipped in await self._items_on_page():
                    if n != name:
                        continue
                    if equipped:
                        return True
                    if attempt:
                        return False  # clicked Equip and it still isn't on: can't wear it
                    await self._equip(window)
                    await asyncio.sleep(0.8)
                    break
                else:
                    if not await ui.click(self.client, NEXT_PAGE):
                        break
                    await asyncio.sleep(0.6)
                    continue
                break
        return False

    async def _optimise_slot(self, tab: str) -> str | None:
        if not await ui.is_visible(self.client, [*PAGE, "ButtonLayout", tab]):
            return None
        items = await self._scan_tab(tab)
        if not items:
            return None
        current = next((n for n, e in items if e), None)
        # An empty slot loses to any item that can actually be worn.
        best_name, best_score = current, (await self._score() if current else float("-inf"))
        for name, equipped in items:
            if equipped:
                continue
            if not await self._equip_by_name(tab, name):
                logger.debug(f"gear: {tab[4:]} {name!r} can't be worn (level or school)")
                continue
            score = await self._score()
            logger.debug(f"gear: {tab[4:]} {name!r} scores {score:.0f} (best {best_score:.0f})")
            if score > best_score + 0.5:
                best_name, best_score = name, score
        if best_name:
            await self._equip_by_name(tab, best_name)
        return best_name if best_name != current else None

    async def optimise(self, reason: str) -> list[str]:
        """Equip the best item in every slot. Returns what changed."""
        logger.info(f"checking gear ({reason})")
        if not await self._open():
            logger.warning("could not open the backpack to check gear")
            return []
        changed = []
        try:
            for tab in SLOT_TABS:
                try:
                    new = await self._optimise_slot(tab)
                except Exception as exc:
                    logger.debug(f"gear: {tab} failed: {exc!r}")
                    continue
                if new:
                    changed.append(new)
        finally:
            await self._close()
        if changed:
            logger.success(f"equipped better gear: {', '.join(changed)}")
        else:
            logger.info("gear: already wearing the best items")
        return changed

    async def tick(self):
        """Call while the wizard is free: re-check when a genuinely new item shows up.
        Items move between the backpack and equipped lists when (un)equipped, so
        track ids across both rather than counting the backpack."""
        ids = await owned_item_ids(self.client)
        if self._known_ids is None:
            await self.optimise("startup")
        elif ids - self._known_ids:
            await self.optimise("new item")
        self._known_ids = await owned_item_ids(self.client)
