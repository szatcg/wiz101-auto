"""Keep the best gear equipped.

Item stats aren't readable from WizWalker, so gear is judged the way a player
would: put an item on and look at the wizard's real stats (health, damage and
accuracy for its school, resistance, power pips...). For each slot the bot
tries every item on the backpack tab and keeps the one that scores best.
Runs after a level-up (every slot: new level requirements are met), and when
a new item lands in the backpack (only that item's slot).
"""

from __future__ import annotations

import asyncio
import re
import time
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
BACKPACK_CHECK_SECONDS = 20.0  # how often to look for new items (a memory read, no UI)

# Words in an item's template (adjectives, object name, icon, display name)
# that tell its slot. The first matching slot wins, so the check order matters
# (e.g. "Ring" before a name that merely contains "ring").
SLOT_WORDS = {
    "Tab_Hat": ("hat", "helm", "helmet", "hood", "cowl", "cap", "mask", "crown", "circlet", "headgear"),
    "Tab_Robe": ("robe", "cloak", "tunic", "vest", "garb", "jacket", "coat", "gown",
                 "armor", "mantle", "cape"),
    "Tab_Shoes": ("shoes", "shoe", "boots", "boot", "slippers", "sandals", "footwear", "treads", "greaves"),
    "Tab_Athame": ("athame", "dagger", "knife", "dirk", "blade", "sword"),
    "Tab_Amulet": ("amulet", "necklace", "pendant", "talisman", "locket"),
    "Tab_Ring": ("ring", "band"),
}


def item_slot(texts: list[str]) -> str | None:
    """The backpack tab for an item, from the strings its template carries, or
    None when it isn't wearable gear we manage (a deck, wand, housing item...)."""
    words = set()
    for t in texts:
        # "WC_Hat_Adventurer", "IconHatMyth", "Graven Boots" -> words
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", t or "")
        words.update(w for w in re.split(r"[^a-z]+", spaced.lower()) if w)
    for tab, keys in SLOT_WORDS.items():
        if words & set(keys):
            return tab
    return None


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


class GearManager:
    def __init__(self, client, school: str):
        self.client = client
        self.school = school
        self._level: int | None = None
        self._items: set[int] | None = None  # backpack item ids seen so far
        self._last_backpack_check = 0.0

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
            # Never leave the slot worse than we found it: equipping sometimes
            # doesn't take, so check the score and try again.
            for _ in range(3):
                await self._equip_by_name(tab, best_name)
                if await self._score() >= best_score - 0.5:
                    break
            else:
                logger.warning(f"gear: could not put {best_name!r} back on ({tab[4:]}); check that slot")
        return best_name if best_name != current else None

    async def optimise(self, reason: str, tabs=SLOT_TABS) -> list[str]:
        """Equip the best item in each of `tabs` (every slot by default). Returns what changed."""
        logger.info(f"checking gear ({reason})")
        if not await self._open():
            logger.warning("could not open the backpack to check gear")
            return []
        changed = []
        try:
            for tab in tabs:
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

    async def _backpack(self) -> dict[int, list[str]]:
        """Backpack items: id -> strings from its template (for item_slot)."""
        from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate

        from .names import lang_name

        behavior = await self.client.client_object.try_get_inventory_behavior()
        if behavior is None:
            return {}
        items = {}
        for obj in await behavior.item_list():
            try:
                gid = await obj.global_id_full()
                core = await obj.object_template()
                t = WizGameObjectTemplate(self.client.hook_handler, await core.read_base_address())
                texts = [await t.object_name() or "", await t.adjective_list() or "", await t.icon() or ""]
                code = await t.display_name()
                if code:
                    texts.append(await lang_name(self.client, code) or "")
                items[gid] = texts
            except Exception:
                continue
        return items

    async def _new_items_tabs(self) -> tuple[set[str], list[str]]:
        """Slots of items that appeared in the backpack since the last look (and
        their names). The first look only takes a snapshot."""
        items = await self._backpack()
        if not items:
            return set(), []
        new = [] if self._items is None else [texts for gid, texts in items.items() if gid not in self._items]
        self._items = set(items)
        tabs, names = set(), []
        for texts in new:
            tab = item_slot(texts)
            name = texts[-1] if len(texts) > 3 else texts[0]
            logger.info(f"new item: {name!r} -> {tab[4:] if tab else 'not gear'} ({' | '.join(texts[:3])})")
            if tab:
                tabs.add(tab)
                names.append(name)
        return tabs, names

    async def tick(self):
        """Call while the wizard is free. After a level-up (new level requirements
        are met) every slot is re-checked; when a new item shows up in the backpack
        only its slot is. Startups don't re-check."""
        level = await self.client.stats.reference_level()
        if self._level is not None and level > self._level:
            self._level = level
            await self.optimise(f"level {level}")
            self._items = None  # re-snapshot: swapped-out items land in the backpack
            return
        self._level = level
        if time.monotonic() - self._last_backpack_check < BACKPACK_CHECK_SECONDS:
            return
        self._last_backpack_check = time.monotonic()
        try:
            tabs, names = await self._new_items_tabs()
        except Exception as exc:
            logger.debug(f"backpack read failed: {exc!r}")
            return
        if tabs:
            order = [t for t in SLOT_TABS if t in tabs]
            await self.optimise(f"new {', '.join(names)}", order)
            self._items = None  # re-snapshot: swapped-out items land in the backpack
