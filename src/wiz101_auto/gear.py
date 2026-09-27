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


async def backpack_count(client) -> int:
    inv = await client.client_object.try_get_inventory_behavior()
    return len(await inv.item_list()) if inv else 0


class GearManager:
    def __init__(self, client, school: str):
        self.client = client
        self.school = school
        self._last_count: int | None = None

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

    async def _optimise_slot(self, tab: str) -> str | None:
        if not await ui.click(self.client, [*PAGE, "ButtonLayout", tab]):
            return None
        await asyncio.sleep(0.8)
        # Gather every item on the tab (a few pages at most).
        candidates: list[tuple[int, str, str, bool]] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            items = [it for it in await self._items_on_page() if it[1] not in seen]
            if not items:
                break
            for window, name, equipped in items:
                seen.add(name)
                candidates.append((page, window, name, equipped))
            if not await ui.click(self.client, NEXT_PAGE):
                break
            await asyncio.sleep(0.6)
        if not candidates:
            return None

        async def goto_page(page: int):
            await ui.click(self.client, [*PAGE, "ButtonLayout", tab])  # back to page 1
            await asyncio.sleep(0.6)
            for _ in range(page):
                await ui.click(self.client, NEXT_PAGE)
                await asyncio.sleep(0.6)

        current = next((c for c in candidates if c[3]), None)
        best_name = current[2] if current else None
        best_score = await self._score()
        for page, window, name, equipped in candidates:
            if equipped:
                continue
            await goto_page(page)
            await self._equip(window)
            score = await self._score()
            logger.debug(f"gear: {tab[4:]} {name!r} scores {score:.0f} (best {best_score:.0f})")
            if score > best_score + 0.5:
                best_name, best_score = name, score
        # Leave the best item on.
        for page, window, name, _ in candidates:
            if name == best_name:
                await goto_page(page)
                await self._equip(window)
                await asyncio.sleep(0.5)
                break
        if best_name and (current is None or best_name != current[2]):
            return best_name
        return None

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
        """Call while the wizard is free: re-check when the backpack gains items."""
        count = await backpack_count(self.client)
        if self._last_count is None:
            self._last_count = count
            await self.optimise("startup")
        elif count > self._last_count:
            self._last_count = count
            await self.optimise("new item")
        else:
            self._last_count = count
