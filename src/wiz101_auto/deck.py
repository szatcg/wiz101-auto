"""Read the spellbook and rebuild the deck (game side of deck_plan.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger
from wizwalker import Keycode
from wizwalker.extensions.scripting.deck_builder import DeckBuilder

from . import ui
from .combat.model import Card
from .combat.reader import read_effects
from .deck_plan import (
    DeckPlan,
    DeckPolicy,
    SpellInfo,
    cards_to_add,
    cards_to_remove,
    plan_adds_cards,
    plan_deck,
    unknown_deck_spells,
)


async def _spell_info(entry) -> SpellInfo | None:
    """Build a planner SpellInfo from a list entry. Works whether the entry points
    at a GraphicalSpell (deck/item lists) or straight at a SpellTemplate."""
    try:
        template = await entry.template() if hasattr(entry, "template") else None
        gspell = None
        if not getattr(entry, "_template_ptr", False):
            gspell = await entry.graphical_spell()
            if gspell and template is None:
                template = await gspell.spell_template()
        if not template:
            return None
        name = await template.name()
        if not name:
            return None
        source = gspell or template  # both expose effects/accuracy
        effects = []
        raw_effects = await (gspell.spell_effects() if gspell else template.effects())
        for eff in raw_effects:
            effects.extend(await read_effects(eff))
        pip_cost = 0
        rank = await (gspell.pip_cost() if gspell else template.spell_rank())
        if rank is not None:
            pip_cost = await rank.spell_rank()
        school = ""
        try:
            school = await template.magic_school_name()
        except Exception:
            pass
        card = Card(
            index=0,
            name=name,
            school=school,
            pip_cost=pip_cost,
            accuracy=await source.accuracy(),
            effects=effects,
        )
        return SpellInfo(card=card, max_copies=await entry.max_copies())
    except Exception as exc:
        logger.debug(f"unreadable spellbook entry: {exc}")
        return None


SPELL_CARDS = Path("state") / "spell_cards.pkl"  # known spells as read from the game, for the simulator


def save_spell_cards(known: list[SpellInfo]) -> None:
    """Keep what each known spell does (pips, school, effects, as the game
    has it) so the simulator and deck search can weigh spells it has no
    values for (a newly trained Stone Colossus)."""
    import dataclasses
    import pickle

    if not known:
        return
    try:
        old = pickle.loads(SPELL_CARDS.read_bytes()) if SPELL_CARDS.exists() else {}
    except Exception:
        old = {}
    old.update({s.card.name: dataclasses.replace(s.card, index=-1) for s in known if s.card.effects})
    try:
        SPELL_CARDS.parent.mkdir(exist_ok=True)
        SPELL_CARDS.write_bytes(pickle.dumps(old))
    except OSError:
        pass


async def read_known_spells(builder: DeckBuilder) -> list[SpellInfo]:
    spells = []
    if hasattr(builder, "read_all_known"):
        entries = await builder.read_all_known()
    else:
        entries = await builder.get_spell_list()
    for entry in entries:
        info = await _spell_info(entry)
        if info:
            spells.append(info)
    return spells


async def current_school(client) -> str:
    """The wizard's primary school name, e.g. 'Myth', from its stats."""
    from wizwalker.memory.memory_objects.enums import MagicSchool

    try:
        return MagicSchool(await client.stats.school_id()).name.capitalize()
    except Exception:
        return ""


SPELLBOOK = ["WorldView", "DeckConfiguration"]
SPELLBOOK_CLOSE = ["WorldView", "DeckConfiguration", "Close_Button"]


async def _visible_spellbook(client):
    for w in await client.root_window.get_windows_with_name("DeckConfiguration"):
        try:
            if await w.is_visible():
                return w
        except Exception:
            pass
    return None


async def _spellbook_open(client) -> bool:
    return await _visible_spellbook(client) is not None


async def open_spellbook(client, timeout: float = 6.0):
    """Open the spellbook and wait for it. DeckBuilder only taps P once and
    waits ~1s, which the game often misses."""
    if await _spellbook_open(client):
        return
    loop = asyncio.get_running_loop()
    for attempt in range(3):
        if attempt < 2:
            await client.send_key(Keycode.P, 0.1)
        else:
            # Last resort: click the spellbook button on the HUD.
            buttons = await client.root_window.get_windows_with_name("btnSpellbook")
            if buttons:
                await client.mouse_handler.click_window(buttons[0])
        end = loop.time() + timeout
        while loop.time() < end:
            if await _spellbook_open(client):
                await asyncio.sleep(0.5)  # let the pages finish building
                return
            await asyncio.sleep(0.25)
        logger.debug(f"spellbook did not open (attempt {attempt + 1})")
    raise RuntimeError(
        "Could not open the spellbook. Make sure the wizard is standing in the world "
        "(no menus or dialogue open) and that P opens the spellbook in the game's key bindings."
    )


async def close_spellbook(client) -> bool:
    """Close the spellbook. The close button's click doesn't always register,
    so fall back to the P toggle and then Esc."""
    attempts = (
        lambda: ui.click(client, SPELLBOOK_CLOSE),
        lambda: client.send_key(Keycode.P, 0.1),
        lambda: client.send_key(Keycode.ESC, 0.1),
    )
    for attempt in attempts:
        if not await _spellbook_open(client):
            return True
        await attempt()
        await asyncio.sleep(0.8)
    if not await _spellbook_open(client):
        return True
    where = "?"
    try:
        buttons = await client.root_window.get_windows_with_name("Close_Button")
        where = str(await buttons[0].scale_to_client()) if buttons else "not found"
    except Exception as exc:
        where = repr(exc)
    logger.warning(f"could not close the spellbook (close button at {where})")
    return False


CARDS_PER_PAGE = 6  # the spell list shows 2 x 3 cards a page
PAGE_UP, PAGE_DOWN = "PageUp", "PageDown"


async def add_cards_by_clicks(client, builder, name: str, copies: int) -> int:
    """Add `copies` of `name` to the deck by clicking it in the All tab of the
    spell list. Pages are turned with the PageUp/PageDown buttons; nothing is
    written to the game's memory (WizWalker's add_by_name wrote a page index
    into the list control at an offset this client doesn't use, and the game
    closed twice). Every click is checked against the deck: a click that adds
    nothing, or the wrong card, stops it. Returns how many were added."""
    await builder.show_tab("Cards_All")
    entries = await builder.get_spell_list(attempts=3, diagnostics=False)
    names = []
    for entry in entries:
        info = await _spell_info(entry)
        names.append(info.name if info else None)
    if name not in names:
        logger.warning(f"deck: {name} isn't in the spell list ({len([n for n in names if n])} spells read)")
        return 0
    index = names.index(name)
    page, slot = divmod(index, CARDS_PER_PAGE)
    page_window = builder._deck_config_window
    up = await _first_visible(page_window, PAGE_UP)
    down = await _first_visible(page_window, PAGE_DOWN)
    if up is None or down is None:
        logger.warning("deck: no PageUp/PageDown buttons on the deck page")
        return 0
    for _ in range(len(names) // CARDS_PER_PAGE + 1):  # back to the first page
        await ui.click_center(client, up)
        await asyncio.sleep(0.25)
    for _ in range(page):
        await ui.click_center(client, down)
        await asyncio.sleep(0.35)
    cells = builder.divide_rectangle(await builder.get_spell_list_rectangle())
    x, y = cells[slot].center()
    added = 0
    for _ in range(copies):
        before = await _log_current_deck(client, builder, quiet=True) or []
        await ui.button_click(client, int(x), int(y))
        await asyncio.sleep(0.8)
        after = await _log_current_deck(client, builder, quiet=True) or []
        if after.count(name) == before.count(name) + 1:
            added += 1
            logger.info(f"deck: added {name} ({after.count(name)} now)")
            continue
        extra = [n for n in set(after) if after.count(n) > before.count(n)]
        if extra:
            logger.warning(f"deck: clicking for {name} added {', '.join(extra)} instead (page {page + 1}, "
                           f"slot {slot + 1}); stopping")
        else:
            logger.warning(f"deck: clicking {name} (page {page + 1}, slot {slot + 1}) added nothing "
                           "(max copies or deck full?); stopping")
        break
    return added


async def _remove_cards(client, builder, name: str, copies: int) -> int:
    """Take `copies` of `name` out of the deck by clicking them in the deck
    list, re-reading it after each click (the list shifts). Returns how many."""
    removed = 0
    for _ in range(copies):
        names = await _log_current_deck(client, builder, quiet=True) or []
        if name not in names:
            break
        cells = builder.divide_rectangle(await builder.get_deck_list_rectangle(), columns=8, rows=8)
        r = cells[names.index(name)]
        await client.mouse_handler.click(int(r.x1 + (r.x2 - r.x1) * 0.25), int((r.y1 + r.y2) / 2))
        await asyncio.sleep(0.6)
        after = await _log_current_deck(client, builder, quiet=True) or []
        if after.count(name) >= names.count(name):
            logger.warning(f"deck: removing {name} didn't take; leaving the rest")
            break
        removed += 1
        logger.info(f"deck: removed a {name}")
    return removed


def parse_deck_spec(spec: str) -> dict[str, int]:
    """"Minotaur=4, Myth Prism=5" -> {"Minotaur": 4, "Myth Prism": 5}."""
    out: dict[str, int] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        name, _, n = part.rpartition("=")
        out[name.strip()] = int(n)
    return out


KEEP_ALWAYS = {"reshuffle"}  # cards a deck switch never takes out (the player put them in)


async def set_deck(client, want: dict[str, int], keep=None) -> dict[str, int]:
    """Make the deck exactly `want` (spell -> copies; anything not listed goes,
    unless `keep(name)` says to leave it: cards the simulator can't judge, like
    the player's Blinding Light): removals first (room for the adds), then
    adds, all by clicks. Returns the deck as read at the end."""
    await open_spellbook(client)
    try:
        builder = await _attach_builder(client)
        names = await _log_current_deck(client, builder) or []
        for name in dict.fromkeys(names):
            kept = (keep is not None and keep(name)) or name.strip().lower() in KEEP_ALWAYS
            if name not in want and kept:
                continue  # (Reshuffle: the player's, in every deck)
            extra = names.count(name) - want.get(name, 0)
            if extra > 0:
                await _remove_cards(client, builder, name, extra)
        names = await _log_current_deck(client, builder, quiet=True) or []
        for name, n in want.items():
            if n > names.count(name):
                await add_cards_by_clicks(client, builder, name, n - names.count(name))
                await asyncio.sleep(0.3)
        await asyncio.sleep(1.5)
        final = await _log_current_deck(client, builder) or []
        if final:
            save_deck_counts(final)  # fights plan with what's in the deck now
        return {n: final.count(n) for n in dict.fromkeys(final)}
    finally:
        await close_spellbook(client)


async def add_to_deck(client, name: str, copies: int) -> int:
    """Open the spellbook's deck page, add `copies` of `name` by clicks, close it."""
    await open_spellbook(client)
    try:
        builder = await _attach_builder(client)
        return await add_cards_by_clicks(client, builder, name, copies)
    finally:
        await close_spellbook(client)


async def rebuild_deck(
    client, school: str, policy: DeckPolicy, *, dry_run: bool = False
) -> tuple[list[SpellInfo], DeckPlan]:
    """Open the spellbook, plan the deck from known spells and apply it."""
    await open_spellbook(client)
    try:
        return await _rebuild_open(client, school, policy, dry_run=dry_run)
    finally:
        await close_spellbook(client)


async def _first_visible(parent, name: str):
    for w in await parent.get_windows_with_name(name):
        try:
            if await w.is_visible():
                return w
        except Exception:
            pass
    return None


async def _dump_spellbook(window) -> str:
    from pathlib import Path

    Path("state").mkdir(exist_ok=True)
    path = Path("state") / "spellbook_window.txt"
    lines = await ui.dump_tree(window, max_depth=9, only_visible=False, with_types=True)
    path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    return str(path)


# Tabs/buttons that lead to the deck-editing page; the name has differed
# between game versions, so try a few.
DECK_PAGE_BUTTONS = ("Deck", "DeckTab", "btnDeck", "Decks", "DeckButton")
SPELL_TABS = (
    "Cards_All",
    "Cards_Myth",
    "Cards_Fire",
    "Cards_Ice",
    "Cards_Storm",
    "Cards_Life",
    "Cards_Death",
    "Cards_Balance",
    "Cards_Astral",
)
# The known-spells list. WizWalker expects "SpellList"; the current client
# calls it "AllPageSpellList" (with a hidden "SchoolPageSpellList" beside it).
SPELL_LIST_NAMES = ("AllPageSpellList", "SpellList", "SchoolPageSpellList")


async def _find_spell_list(parent):
    for name in SPELL_LIST_NAMES:
        found = await _first_visible(parent, name)
        if found:
            return found
    return None


async def _attach_builder(client) -> DeckBuilder:
    """Point DeckBuilder at the already-open spellbook and switch to the deck page.

    DeckBuilder.open() would search for the window itself, fail if a hidden
    copy also exists, and then press P, closing the book again.
    """
    window = await _visible_spellbook(client)
    if window is None:
        raise RuntimeError("spellbook closed unexpectedly")

    loop = asyncio.get_running_loop()
    spell_list = await _find_spell_list(window)
    for attempt in range(3):
        if spell_list:
            break
        for name in DECK_PAGE_BUTTONS:
            button = await _first_visible(window, name)
            if button:
                logger.debug(f"clicking spellbook button {name!r} (attempt {attempt + 1})")
                if attempt == 1:
                    # The left-shifted click missed (e.g. the book opened on the
                    # settings page): try the button's exact center.
                    from .ui import click_center

                    await click_center(client, button)
                else:
                    await client.mouse_handler.click_window(button)
                break
        end = loop.time() + 4
        while loop.time() < end and not spell_list:
            await asyncio.sleep(0.3)
            # Switching pages can rebuild the spellbook window, so search the
            # whole UI rather than the window we started with.
            spell_list = await _find_spell_list(client.root_window)
            if spell_list:
                window = await _visible_spellbook(client) or window

    if not spell_list:
        window = await _visible_spellbook(client) or window
        path = await _dump_spellbook(window)
        raise DeckPageNotFound(
            f"Opened the spellbook but could not find its deck page. Its layout was saved to {path}; "
            "send that file to Claude."
        )

    cards_all = await _first_visible(window, "Cards_All")
    if cards_all:
        await client.mouse_handler.click_window(cards_all)
        await asyncio.sleep(0.4)

    builder = _Builder(client, spell_list)
    builder._deck_config_window = window
    builder._deck_open = True
    return builder


class DeckPageNotFound(RuntimeError):
    pass


class _Builder(DeckBuilder):
    """DeckBuilder that reads the spell list window we already located, instead
    of a by-name search that fails when the game keeps a hidden duplicate."""

    def __init__(self, client, spell_list_window):
        super().__init__(client)
        self._spell_list_window = spell_list_window

    # WizWalker looks these windows up by name from the root, which breaks on
    # the renamed spell list and on hidden duplicates; use the visible ones.
    async def _visible(self, name: str):
        w = await _first_visible(self._deck_config_window, name)
        if w is None:
            raise ValueError(f"spellbook window {name!r} not visible")
        return w

    async def get_spell_list_rectangle(self):
        return await self._spell_list_window.scale_to_client()

    async def get_deck_list_rectangle(self):
        return await (await self._visible("CardsInDeck")).scale_to_client()

    async def get_item_spells_rectangle(self):
        return await (await self._visible("ItemSpells")).scale_to_client()

    async def set_page(self, page_number: int):
        from wizwalker.memory.memory_objects.window import DynamicSpellListControl

        control = DynamicSpellListControl(
            self.client.hook_handler, await self._spell_list_window.read_base_address()
        )
        await control.write_start_index(page_number * 6)

    async def clear_deck(self):
        """Remove every card by clicking the first deck slot.

        The Clear Deck button is hidden on small decks, and WizWalker's fallback
        retries by recursing forever if clicks don't register.
        """
        for _ in range(2):
            count = await self.get_deck_count()
            if count == 0:
                return
            x, y = await self.calculate_deck_card_position(1)
            for _ in range(count):
                await self.client.mouse_handler.click(x, y)
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.5)
        logger.warning("deck may not be fully cleared")

    async def get_spell_list(self, attempts: int = 6, diagnostics: bool = True):
        """Entries of the spell list currently shown, found by scanning memory.

        The list only holds the spells of the selected tab (e.g. All, Myth),
        so read_all_known() walks the tabs.
        """
        valid: list = []
        for attempt in range(attempts):
            current = await _find_spell_list(self._deck_config_window) or self._spell_list_window
            self._spell_list_window = current
            try:
                valid = await find_spell_entries(self.client, current)
            except Exception as exc:
                logger.debug(f"spell list scan failed: {exc!r}")
                valid = []
            logger.debug(f"spell list attempt {attempt + 1}: {len(valid)} spells")
            if valid:
                break
            await asyncio.sleep(0.5)
        if not valid and diagnostics:
            await self._log_list_diagnostics()
        return valid

    async def show_tab(self, name: str) -> bool:
        tab = await _first_visible(self._deck_config_window, name)
        if not tab:
            return False
        await self.client.mouse_handler.click_window(tab)
        await asyncio.sleep(0.8)  # let the list repopulate
        return True

    async def read_all_known(self) -> list:
        """Every known spell: the All tab if it fills, otherwise each school tab."""
        seen: dict[str, object] = {}
        for tab in SPELL_TABS:
            if not await self.show_tab(tab):
                continue
            entries = await self.get_spell_list(attempts=3, diagnostics=False)
            logger.debug(f"spellbook tab {tab}: {len(entries)} spells")
            for e in entries:
                t = await e.template()
                if t:
                    seen.setdefault(await t.name(), e)
            if tab == "Cards_All" and entries:
                break
        await self.show_tab("Cards_All")
        if not seen:
            await self._log_list_diagnostics()
        return list(seen.values())

    async def _log_list_diagnostics(self):
        lines = []
        for w in await self._deck_config_window.get_windows_with_predicate(_is_list_control):
            try:
                n = len(await find_spell_entries(self.client, w))
                lines.append(f"{await w.name()} visible={await w.is_visible()} spells={n}")
            except Exception as exc:
                lines.append(f"{await w.name()}: {exc!r}")
        logger.warning("could not find any spells; list windows seen: " + " | ".join(lines))
        try:
            path = await dump_list_memory(self.client, self._spell_list_window)
            logger.warning(f"raw spell list memory saved to {path}; send it to Claude")
        except Exception as exc:
            logger.debug(f"memory dump failed: {exc!r}")


# --- spell list memory layout detection --------------------------------------
#
# A list control holds a vector (begin/end pointers) of entries. WizWalker's
# offsets for it are stale, so we search for it. Entries may be stored inline
# (fixed size) or as pointers, the GraphicalSpell pointer may sit at a small
# offset inside the entry, and lists can start with empty spacer slots.

ENTRY_SIZES = (0x78, 0xA8, 0xB0, 0xA0, 0xB8, 0x98, 0xC0, 0x90, 0xC8, 0x88, 0xD0, 0x28, 0x30, 0x20)
SPELL_PTR_OFFSETS = (0x0, 0x8, 0x10)
PROBE = 16
_layouts: dict[str, tuple] = {}  # control type name -> (offset, gap, size, ptr_off, indirect)


class SpellEntry:
    """A spell list entry: a GraphicalSpell pointer plus copy counts."""

    def __init__(self, hook_handler, address: int, spell_ptr_offset: int = 0, template_ptr: bool = False):
        from wizwalker.memory.memory_object import DynamicMemoryObject

        self._mem = DynamicMemoryObject(hook_handler, address)
        self._hook = hook_handler
        self._ptr_off = spell_ptr_offset
        self._template_ptr = template_ptr  # pointer is a SpellTemplate, not a GraphicalSpell
        self.base_address = address

    async def template(self):
        from wizwalker.memory.memory_objects.spell_template import DynamicSpellTemplate

        addr = await self._u(self._ptr_off, "q")
        if not addr:
            return None
        if self._template_ptr:
            return DynamicSpellTemplate(self._hook, addr)
        g = await self.graphical_spell()
        return await g.spell_template() if g else None

    async def _u(self, offset: int, size: str) -> int:
        from wizwalker.memory.memory_object import Primitive

        prim = Primitive.uint64 if size == "q" else Primitive.uint32
        return await self._mem.read_value_from_offset(offset, prim)

    async def graphical_spell(self):
        from wizwalker.memory.memory_objects.spell import DynamicGraphicalSpell

        addr = await self._u(self._ptr_off, "q")
        return DynamicGraphicalSpell(self._hook, addr) if addr else None

    async def max_copies(self) -> int:
        v = await self._u(self._ptr_off + 0x10, "d")
        return v if 1 <= v <= 12 else 4  # sane fallback if the field moved

    async def current_copies(self) -> int:
        v = await self._u(self._ptr_off + 0x14, "d")
        return v if 0 <= v <= 12 else 0


async def _spell_name_at(entry: SpellEntry) -> str | None:
    """Spell name, '' for an empty slot, None if this isn't a spell entry at all."""
    try:
        addr = await entry._u(entry._ptr_off, "q")
    except Exception:
        return None
    if addr == 0:
        return ""
    try:
        t = await entry.template()
        name = await t.name() if t else ""
    except Exception:
        return None
    return name if name and name.isprintable() and len(name) < 80 else None


async def _make_entries(hook, start, count, size, ptr_off, indirect, template_ptr=False) -> list[SpellEntry]:
    from wizwalker.memory.memory_object import DynamicMemoryObject, Primitive

    out = []
    for i in range(count):
        addr = start + i * size
        if indirect:
            addr = await DynamicMemoryObject(hook, addr).read_value_from_offset(0, Primitive.uint64)
            if not addr:
                continue
        out.append(SpellEntry(hook, addr, ptr_off, template_ptr))
    return out


async def _check_layout(
    hook, start, end, size, ptr_off, indirect, template_ptr=False
) -> list[SpellEntry] | None:
    if (end - start) % size:
        return None
    count = (end - start) // size
    if count == 0 or count > 500:
        return None
    probe = await _make_entries(hook, start, min(count, PROBE), size, ptr_off, indirect, template_ptr)
    names = [await _spell_name_at(e) for e in probe]
    if not names or any(n is None for n in names) or not any(names):
        return None  # something unreadable, or nothing but empty slots
    entries = await _make_entries(hook, start, count, size, ptr_off, indirect, template_ptr)
    return [e for e in entries if await _spell_name_at(e)]


async def find_spell_entries(client, list_window) -> list[SpellEntry]:
    """Locate the entry vector inside a list window and return its spell entries."""
    from wizwalker.memory.memory_object import DynamicMemoryObject, Primitive

    hook = client.hook_handler
    ctrl = DynamicMemoryObject(hook, await list_window.read_base_address())
    kind = await list_window.maybe_read_type_name() or "?"

    async def pair(offset, gap):
        try:
            start = await ctrl.read_value_from_offset(offset, Primitive.uint64)
            end = await ctrl.read_value_from_offset(offset + gap, Primitive.uint64)
        except Exception:
            return None
        if 0x10000 < start < end and end - start <= 0xD0 * 500:
            return start, end
        return None

    known = _layouts.get(kind)
    if known:
        offset, gap, size, ptr_off, indirect, template_ptr = known
        p = await pair(offset, gap)
        if p is None:
            return []  # layout known, list currently empty
        found = await _check_layout(hook, *p, size, ptr_off, indirect, template_ptr)
        if found is not None:
            return found

    # Several layouts can "fit" one vector (a 0x78 stride over 0x28-byte deck
    # entries reads every third card), so keep the one yielding the most spells.
    best: tuple[list[SpellEntry], tuple] | None = None
    for offset in range(0x200, 0x480, 8):
        # A vector is start, end, capacity: start..end is the list. Start..capacity
        # (gap 16) also "works" but reads stale slots past the end: after
        # removals the last spell counted again (Vampire x3 for one). The end
        # pointer (gap 8) wins whenever it's valid.
        for gap in (8, 16):
            p = await pair(offset, gap)
            if p is None:
                continue
            if gap == 16 and await pair(offset, 8) is not None:
                continue
            base = [(8, o, True) for o in SPELL_PTR_OFFSETS]  # vector of pointers
            base += [(size, o, False) for size in ENTRY_SIZES for o in SPELL_PTR_OFFSETS]
            candidates = [(*c, False) for c in base] + [(*c, True) for c in base]
            for size, ptr_off, indirect, template_ptr in candidates:
                found = await _check_layout(hook, *p, size, ptr_off, indirect, template_ptr)
                if found and (best is None or len(found) > len(best[0])):
                    best = (found, (offset, gap, size, ptr_off, indirect, template_ptr))
        if best:
            break  # the first offset holding a spell vector is the list
    if best is None:
        return []
    found, layout = best
    if _layouts.get(kind) != layout:
        offset, _gap, size, ptr_off, indirect, template_ptr = layout
        logger.info(
            f"{kind} layout found: vector at {offset:#x}, entry size {size:#x}, "
            f"spell pointer at +{ptr_off:#x}{', indirect' if indirect else ''}"
            f"{', template pointer' if template_ptr else ''}"
        )
    _layouts[kind] = layout
    return found


async def dump_list_memory(client, list_window) -> str:
    """Hex dump of a list control's fields and whatever its pointer pairs point at."""
    from pathlib import Path

    from wizwalker.memory.memory_object import DynamicMemoryObject, Primitive

    hook = client.hook_handler
    base = await list_window.read_base_address()
    ctrl = DynamicMemoryObject(hook, base)
    lines = [f"{await list_window.name()} [{await list_window.maybe_read_type_name()}] base={base:#x}"]
    for off in range(0x200, 0x480, 8):
        try:
            v = await ctrl.read_value_from_offset(off, Primitive.uint64)
        except Exception:
            continue
        line = f"+{off:#05x}: {v:#018x}"
        if 0x10000 < v < 0x7FFFFFFFFFFF:
            try:
                raw = await ctrl.read_bytes(v, 0x40)
                line += "  -> " + raw.hex(" ", 8)
            except Exception:
                pass
        lines.append(line)
    Path("state").mkdir(exist_ok=True)
    path = Path("state") / "spell_list_memory.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


async def _is_list_control(window) -> bool:
    try:
        return "ListControl" in (await window.maybe_read_type_name() or "")
    except Exception:
        return False


DECK_FILE = Path("state") / "deck.json"


def save_deck_counts(names: list[str], path: Path = DECK_FILE):
    """The in-game deck as spell (template) name -> copies."""
    try:
        path.parent.mkdir(exist_ok=True)
        counts = {n: names.count(n) for n in dict.fromkeys(names)}
        path.write_text(json.dumps(counts, indent=1), encoding="utf-8")
    except OSError:
        pass


def load_deck_counts(path: Path = DECK_FILE) -> dict[str, int]:
    """The deck as last read; else the deck plan in state/progress.json."""
    for f, key in ((path, None), (Path("state") / "progress.json", "deck")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data = data.get(key, {}) if key else data
            if data:
                return {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


async def _log_current_deck(client, builder, *, quiet: bool = False) -> list[str] | None:
    """Log and return the spell names in the current deck (None if unreadable)."""
    try:
        deck_window = await _first_visible(builder._deck_config_window, "CardsInDeck")
        if not deck_window:
            return None
        names = []
        for e in await find_spell_entries(client, deck_window):
            t = await e.template()
            if t:
                names.append(await t.name())
        counts = {n: names.count(n) for n in dict.fromkeys(names)}
        if not quiet:
            logger.info("current deck: " + (", ".join(f"{n} x{c}" for n, c in counts.items()) or "(empty)"))
        return names
    except Exception as exc:
        logger.debug(f"could not read current deck: {exc!r}")
        return None


async def _dump_deck_page(client) -> None:
    """The deck page's windows (its spell list, page arrows, deck list) to
    state/deck_config_window.txt: adding cards by clicks needs their names."""
    from pathlib import Path

    try:
        page = (await client.root_window.get_windows_with_name("DeckConfiguration") or [None])[0]
        if page is None:
            return
        lines = await ui.dump_tree(page, max_depth=8, only_visible=False, with_types=True)
        Path("state").mkdir(exist_ok=True)
        out = Path("state") / "deck_config_window.txt"
        out.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"could not save the deck page layout: {exc!r}")


async def _rebuild_open(client, school: str, policy: DeckPolicy, *, dry_run: bool):
    builder = await _attach_builder(client)
    await _dump_deck_page(client)
    deck_names = await _log_current_deck(client, builder)
    if deck_names:
        save_deck_counts(deck_names)  # the fighter plans with what's left of it
    known = await read_known_spells(builder)
    save_spell_cards(known)
    plan = plan_deck(known, school, policy)
    logger.info(f"known spells: {', '.join(s.name for s in known) or '(none)'}")
    logger.info(f"deck plan: {plan.describe()}")
    if dry_run or not plan.steps:
        return known, plan
    if not known:
        logger.warning("spellbook read returned no spells; leaving the deck alone")
        return known, plan
    if deck_names is None:
        logger.warning("could not read the current deck; leaving it alone")
        return known, plan
    missing = unknown_deck_spells(deck_names, [s.name for s in known])
    if missing:
        logger.warning(
            f"spellbook read looks incomplete (deck has {', '.join(missing)} but they weren't read "
            "as known spells); leaving the deck alone"
        )
        return known, plan
    removals = cards_to_remove(plan.totals, deck_names)
    if removals and not policy.auto_remove:
        extra = ", ".join(f"{name} x{copies}" for name, copies in removals)
        logger.info(f"deck: the plan would drop {extra}; leaving your deck as it is (auto_remove is off)")
        removals = []
    if not plan_adds_cards(plan.totals, deck_names) and not removals:
        logger.info("deck already matches the plan; leaving it alone")
        return known, plan

    # Remove what the plan dropped (e.g. an older minion) one card at a time,
    # re-reading the deck after each click since the list shifts.
    for name, copies in removals:
        if await _remove_cards(client, builder, name, copies) < copies:
            break
    deck_names = await _log_current_deck(client, builder, quiet=True) or deck_names

    # Add only what's missing (new spells, extra copies). Never clear first: if
    # the add clicks fail, the deck must not end up empty.
    before = len(deck_names)
    missing = cards_to_add(plan.totals, deck_names)
    if missing and not policy.auto_add:
        wanted = ", ".join(f"{name} x{copies}" for name, copies in missing)
        logger.warning(f"deck: please add by hand (automatic adding is off): {wanted}")
        missing = []
    for name, copies in missing:
        # Clicks only (WizWalker's add_by_name wrote to game memory and crashed it).
        if await add_cards_by_clicks(client, builder, name, copies) < copies:
            break  # a click went wrong: leave the rest for a look
        await asyncio.sleep(0.3)
    if not missing and not removals:
        logger.info("deck: nothing changed (adding and removing are off)")
        return known, plan
    await asyncio.sleep(1.5)  # the deck list lags a moment behind removals
    final = await _log_current_deck(client, builder) or []
    logger.success(f"deck updated ({before} -> {len(final)} cards)")
    return known, plan
