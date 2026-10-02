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
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from wizwalker import Keycode

from . import ui
from .combat.reader import SCHOOL_ORDER

PAGE = ["WorldView", "DeckConfiguration", "InventorySpellbookPage"]
EQUIP = [*PAGE, "windowForBtns", "Layout", "Equip_Item"]
NEXT_PAGE = [*PAGE, "rightscroll"]
PREV_PAGE = [*PAGE, "leftscroll"]
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
                 "armor", "mantle", "cape", "raiment", "toga"),
    "Tab_Shoes": ("shoes", "shoe", "boots", "boot", "slippers", "sandals", "footwear", "treads", "greaves"),
    "Tab_Athame": ("athame", "dagger", "knife", "dirk", "blade", "sword"),
    "Tab_Amulet": ("amulet", "necklace", "pendant", "talisman", "locket"),
    "Tab_Ring": ("ring", "band"),
}


def norm_item(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Wands aren't gear-checked (their item cards matter) but are logged as loot
# (the Sky Iron Hasta farm target).
WAND_WORDS = ("wand", "staff", "hasta", "spear", "sceptre", "scepter", "rod", "stave", "trident")


def is_wand(texts: list[str]) -> bool:
    words = set()
    for t in texts:
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", t or "")
        words.update(w for w in re.split(r"[^a-z]+", spaced.lower()) if w)
    return bool(words & set(WAND_WORDS))


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
    flat_damage: float = 0  # own-school "+N damage" (early gear gives this, not %)
    accuracy: float = 0
    resist: float = 0  # average incoming reduction over the main schools
    power_pip: float = 0
    critical: float = 0  # rating
    block: float = 0  # rating


def gear_score(s: StatSnapshot) -> float:
    """One number for 'how strong is the wizard'. Weights favour survival and
    damage for PvE questing: 1 point per health, 20 per 1% damage, 10 per +1
    flat damage, 15 per 1% resist, 10 per 1% power pip chance, 5 per 1% accuracy."""
    return (
        s.health
        + s.mana * 0.2
        + s.damage * 2000
        + s.flat_damage * 10
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
        flat_damage=_pick(await st.dmg_bonus_flat(), school, await st.dmg_bonus_flat_all()),
        accuracy=_pick(await st.acc_bonus_percent(), school, await st.acc_bonus_percent_all()),
        resist=(sum(main) / len(main) if main else 0) + await st.dmg_reduce_percent_all(),
        power_pip=await st.power_pip_base() + await st.power_pip_bonus_percent_all(),
        critical=_pick(await st.critical_hit_rating_by_school(), school),
        block=_pick(await st.block_rating_by_school(), school),
    )


GEAR_MEMORY = Path("state") / "gear.json"
LOOT_LOG = Path("state") / "looted_gear.json"


class LootLog:
    """Every piece of gear looted, by name: slot, when first looted, how many
    times. A new item already in the log is a duplicate: nothing to check."""

    def __init__(self, path: Path = LOOT_LOG, history: Path | None = Path("wiz101-auto.log")):
        self.path = path
        self.items: dict[str, dict] = {}
        try:
            self.items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if history is not None:
                self._seed(history)

    def _seed(self, log: Path):
        """First run: gear looted so far, from the log's "new item" lines."""
        pattern = re.compile(r"^(\S+ \S+).*new item: (['\"])(.+?)\2 -> ([A-Za-z]+)")
        try:
            with log.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.search(line)
                    if not m or m.group(4) == "not":
                        continue
                    when, name, slot = m.group(1)[:16], m.group(3), m.group(4)
                    if self.record(name, f"Tab_{slot}"):
                        self.items[name]["first_looted"] = when
        except OSError:
            return
        self.save()

    def seen(self, name: str) -> bool:
        return norm_item(name) in {norm_item(n) for n in self.items}

    def record(self, name: str, slot: str | None) -> bool:
        """Add a looted item. True if it's the first of its name."""
        key = next((n for n in self.items if norm_item(n) == norm_item(name)), None)
        if key is not None:
            self.items[key]["count"] = int(self.items[key].get("count", 1)) + 1
            return False
        self.items[name] = {
            "slot": slot[4:] if slot else "",
            "first_looted": time.strftime("%Y-%m-%d %H:%M"),
            "count": 1,
        }
        return True

    def save(self):
        try:
            self.path.parent.mkdir(exist_ok=True)
            data = dict(sorted(self.items.items(), key=lambda kv: (kv[1].get("slot", ""), kv[0])))
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


class GearInterrupted(Exception):
    """A fight started (or the backpack closed) in the middle of a gear check."""


class GearMemory:
    """What each backpack item turned out to be, per slot tab, so a check only
    tries items that could be better: "worse" items (beaten by what's worn, and
    gear only gets better with level) are never tried again; "locked" ones
    (couldn't be worn: level or school) are retried after a level-up."""

    def __init__(self, path: Path = GEAR_MEMORY):
        self.path = path
        self.worse: dict[str, set[str]] = {}
        self.locked: dict[str, set[str]] = {}
        # tab -> the item to wear there while a check is trying others on: if the
        # check is cut short (a fight starts, the backpack closes, the bot stops)
        # it is put back on at the next chance instead of leaving a worse item on.
        self.restore: dict[str, str] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.worse = {t: set(v) for t, v in raw.get("worse", {}).items()}
            self.locked = {t: set(v) for t, v in raw.get("locked", {}).items()}
            self.restore = dict(raw.get("restore", {}))
        except Exception:
            pass

    def save(self):
        try:
            self.path.parent.mkdir(exist_ok=True)
            data = {
                "worse": {t: sorted(v) for t, v in self.worse.items()},
                "locked": {t: sorted(v) for t, v in self.locked.items()},
                "restore": self.restore,
            }
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def candidates(self, tab: str, names: list[str], level_up: bool) -> list[str]:
        """Items worth trying on: never judged, plus level-locked ones after a level-up."""
        worse, locked = self.worse.get(tab, set()), self.locked.get(tab, set())
        return [n for n in names if n not in worse and (level_up or n not in locked)]

    def mark(self, tab: str, name: str, verdict: str):
        for kind in (self.worse, self.locked):
            kind.setdefault(tab, set()).discard(name)
        if verdict in ("worse", "locked"):
            getattr(self, verdict).setdefault(tab, set()).add(name)


class GearManager:
    def __init__(self, client, school: str):
        self.client = client
        self.school = school
        self._level: int | None = None
        self.memory = GearMemory()
        self.loot = LootLog()
        self.before_check = None  # async callable: step away from enemies first (set by the bot)
        self._items: set[int] | None = None  # backpack item ids seen so far
        self._last_backpack_check = 0.0
        self.level_up_only = False  # (gear_checks_new_items: false) no checks for new loot

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

    async def _open_tab(self, tab: str):
        """Show `tab` from its first page. Clicking the tab that's already
        selected keeps the page it was on (e.g. the last one after a scan), so
        scroll back explicitly."""
        other = next(t for t in SLOT_TABS if t != tab)
        await ui.click(self.client, [*PAGE, "ButtonLayout", other])  # switching tabs starts at page 1
        await asyncio.sleep(0.5)
        await ui.click(self.client, [*PAGE, "ButtonLayout", tab])
        await asyncio.sleep(0.8)
        for _ in range(MAX_PAGES):
            if not await ui.click(self.client, PREV_PAGE):
                break
            await asyncio.sleep(0.4)

    async def _scan_tab(self, tab: str) -> list[tuple[str, bool]]:
        """(item name, equipped) for every item on the tab, from page 1."""
        await self._open_tab(tab)
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
            await self._open_tab(tab)
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

    async def _optimise_slot(
        self, tab: str, only: set[str] | None = None, level_up: bool = False
    ) -> str | None:
        """Try on the items that could beat what's worn: `only` (new items) if
        given, else those the memory hasn't ruled out. Keeps the best."""
        if not await ui.is_visible(self.client, PAGE) and not await self._open():
            return None  # something closed the backpack (e.g. the stall watchdog)
        if not await ui.is_visible(self.client, [*PAGE, "ButtonLayout", tab]):
            return None
        items = await self._scan_tab(tab)
        if not items:
            return None
        current = next((n for n, e in items if e), None)
        others = [n for n, e in items if not e]
        if only is not None:
            to_try = [n for n in others if norm_item(n) in {norm_item(o) for o in only}]
        else:
            to_try = self.memory.candidates(tab, others, level_up)
        if not to_try:
            return None
        logger.debug(f"gear: {tab[4:]}: trying {to_try} against {current!r}")
        # An empty slot loses to any item that can actually be worn.
        best_name, best_score = current, (await self._score() if current else float("-inf"))
        if best_name:
            self.memory.restore[tab] = best_name
            self.memory.save()
        for name in to_try:
            if await self.client.in_battle() or not await ui.is_visible(self.client, PAGE):
                logger.warning(f"gear: {tab[4:]} check cut short; {best_name!r} goes back on when free")
                self.memory.save()
                raise GearInterrupted
            if not await self._equip_by_name(tab, name):
                logger.debug(f"gear: {tab[4:]} {name!r} can't be worn (level or school)")
                self.memory.mark(tab, name, "locked")
                continue
            score = await self._score()
            logger.debug(f"gear: {tab[4:]} {name!r} scores {score:.0f} (best {best_score:.0f})")
            if score > best_score + 0.5:
                if best_name:
                    self.memory.mark(tab, best_name, "worse")
                best_name, best_score = name, score
            else:
                self.memory.mark(tab, name, "worse")
            if best_name:
                self.memory.restore[tab] = best_name
            self.memory.save()
        if best_name:
            # Never leave the slot worse than we found it: equipping sometimes
            # doesn't take, so check the score and try again.
            await self._put_on(tab, best_name, best_score)
        return best_name if best_name != current else None

    async def _put_on(self, tab: str, name: str, score: float | None = None) -> bool:
        """Wear `name` in `tab` (verified by `score` when known, else by the
        item's equipped mark) and clear its pending restore. False if it
        couldn't be done now (a fight, the backpack closed); it stays pending."""
        for _ in range(3):
            if await self.client.in_battle():
                raise GearInterrupted
            if not await ui.is_visible(self.client, PAGE) and not await self._open():
                continue
            on = await self._equip_by_name(tab, name)
            if (score is None and on) or (score is not None and await self._score() >= score - 0.5):
                self.memory.restore.pop(tab, None)
                self.memory.save()
                return True
        logger.warning(f"gear: could not put {name!r} back on ({tab[4:]}); will retry when free")
        return False

    async def restore_pending(self) -> bool:
        """Put back items a cut-short check left off. True if there were any."""
        if not self.memory.restore:
            return False
        logger.info(f"gear: putting back {', '.join(self.memory.restore.values())} (a check was cut short)")
        if not await self._open():
            return True
        try:
            for tab, name in list(self.memory.restore.items()):
                await self._put_on(tab, name)
                # One go outside a fight: an item that's gone (sold) isn't retried forever.
                self.memory.restore.pop(tab, None)
                self.memory.save()
        except GearInterrupted:
            return True
        finally:
            if not await self.client.in_battle():
                await self._close()
        return True

    async def optimise(
        self, reason: str, tabs=SLOT_TABS, only: dict | None = None, level_up: bool = False
    ) -> list[str]:
        """Equip the best item in each of `tabs`: for new items (`only`: tab ->
        names) just those against what's worn; otherwise every item the memory
        hasn't ruled out. Returns what changed."""
        logger.info(f"checking gear ({reason})")
        if self.before_check:
            await self.before_check()  # the check takes a while standing still
        if not await self._open():
            logger.warning("could not open the backpack to check gear")
            return []
        changed = []
        try:
            for tab in tabs:
                try:
                    names = only.get(tab) if only is not None else None
                    new = await self._optimise_slot(tab, names, level_up)
                except GearInterrupted:
                    break
                except Exception as exc:
                    logger.debug(f"gear: {tab} failed: {exc!r}")
                    continue
                if new:
                    changed.append(new)
        finally:
            if not await self.client.in_battle():
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

    async def _new_items_tabs(self) -> dict[str, set[str]]:
        """Slots of items that appeared in the backpack since the last look (and
        their names). The first look only takes a snapshot."""
        items = await self._backpack()
        if not items:
            return {}
        first_look = self._items is None
        new = [texts for gid, texts in items.items() if self._items is None or gid not in self._items]
        self._items = set(items)
        by_tab: dict[str, set[str]] = {}
        for texts in new:
            tab = item_slot(texts)
            name = texts[-1] if len(texts) > 3 else texts[0]
            if not tab:
                if is_wand(texts) and not self.loot.seen(name):
                    self.loot.record(name, "Tab_Wand")
                    if not first_look:
                        logger.info(f"new item: {name!r} -> Wand (logged; wands aren't gear-checked)")
                continue
            if first_look:
                # Already in the backpack: judged before (or by hand). Just list it.
                if not self.loot.seen(name):
                    self.loot.record(name, tab)
                continue
            if not self.loot.record(name, tab):
                logger.info(f"new item: {name!r} ({tab[4:]}) was looted before; a duplicate, not checking it")
                continue
            logger.info(f"new item: {name!r} -> {tab[4:]} ({' | '.join(texts[:3])})")
            by_tab.setdefault(tab, set()).add(name)
        self.loot.save()
        return by_tab

    async def tick(self):
        """Call while the wizard is free. A new item is tried against what's worn
        in its slot only. After a level-up, items that couldn't be worn before
        (and any never tried) are tried; ones already beaten are not. Startups
        don't check."""
        if await self.restore_pending():
            return
        level = await self.client.stats.reference_level()
        if self._level is not None and level > self._level:
            self._level = level
            await self.optimise(f"level {level}", level_up=True)
            self._items = None  # re-snapshot: swapped-out items land in the backpack
            return
        self._level = level
        if time.monotonic() - self._last_backpack_check < BACKPACK_CHECK_SECONDS:
            return
        self._last_backpack_check = time.monotonic()
        try:
            new = await self._new_items_tabs()
        except Exception as exc:
            logger.debug(f"backpack read failed: {exc!r}")
            return
        if new and not self.level_up_only:
            order = [t for t in SLOT_TABS if t in new]
            names = sorted(n for v in new.values() for n in v)
            await self.optimise(f"new {', '.join(names)}", order, only=new)
            self._items = None  # re-snapshot: swapped-out items land in the backpack
