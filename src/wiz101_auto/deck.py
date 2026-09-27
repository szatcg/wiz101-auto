"""Read the spellbook and rebuild the deck (game side of deck_plan.py)."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker import Keycode
from wizwalker.extensions.scripting.deck_builder import DeckBuilder

from . import ui
from .combat.model import Card
from .combat.reader import read_effects
from .deck_plan import DeckPlan, DeckPolicy, SpellInfo, plan_deck, unknown_deck_spells


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


async def close_spellbook(client):
    for _ in range(3):
        if not await _spellbook_open(client):
            return
        if not await ui.click(client, SPELLBOOK_CLOSE):
            await client.send_key(Keycode.P, 0.1)
        await asyncio.sleep(0.8)


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

ENTRY_SIZES = (0xA8, 0xB0, 0xA0, 0xB8, 0x98, 0xC0, 0x90, 0xC8, 0x88, 0xD0, 0x28, 0x30, 0x20)
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

    for offset in range(0x200, 0x480, 8):
        for gap in (16, 8):
            p = await pair(offset, gap)
            if p is None:
                continue
            base = [(8, o, True) for o in SPELL_PTR_OFFSETS]  # vector of pointers
            base += [(size, o, False) for size in ENTRY_SIZES for o in SPELL_PTR_OFFSETS]
            candidates = [(*c, False) for c in base] + [(*c, True) for c in base]
            for size, ptr_off, indirect, template_ptr in candidates:
                found = await _check_layout(hook, *p, size, ptr_off, indirect, template_ptr)
                if found:
                    layout = (offset, gap, size, ptr_off, indirect, template_ptr)
                    if _layouts.get(kind) != layout:
                        logger.info(
                            f"{kind} layout found: vector at {offset:#x}, entry size {size:#x}, "
                            f"spell pointer at +{ptr_off:#x}{', indirect' if indirect else ''}"
                            f"{', template pointer' if template_ptr else ''}"
                        )
                    _layouts[kind] = layout
                    return found
    return []


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


async def _log_current_deck(client, builder) -> list[str] | None:
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
        logger.info("current deck: " + (", ".join(f"{n} x{c}" for n, c in counts.items()) or "(empty)"))
        return names
    except Exception as exc:
        logger.debug(f"could not read current deck: {exc!r}")
        return None


async def _rebuild_open(client, school: str, policy: DeckPolicy, *, dry_run: bool):
    builder = await _attach_builder(client)
    deck_names = await _log_current_deck(client, builder)
    known = await read_known_spells(builder)
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

    await builder.clear_deck()
    await asyncio.sleep(0.5)
    for name, copies in plan.steps:
        try:
            await builder.add_by_name(name, copies)
        except Exception as exc:  # max copies, deck full, or UI hiccup
            logger.debug(f"add {name} x{copies} stopped: {exc}")
        await asyncio.sleep(0.3)
    await asyncio.sleep(0.5)
    count = await builder.get_deck_count()
    if count == 0:
        raise DeckPageNotFound(
            "Deck rebuild left the deck EMPTY (card clicks did not register). "
            "Re-add your spells by hand in the spellbook (P) and send wiz101-auto.log to Claude."
        )
    logger.success(f"deck rebuilt ({count} cards)")
    return known, plan
