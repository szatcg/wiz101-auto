"""Read the spellbook and rebuild the deck (game side of deck_plan.py)."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker import Keycode
from wizwalker.extensions.scripting.deck_builder import DeckBuilder

from . import ui
from .combat.model import Card
from .combat.reader import read_effects
from .deck_plan import DeckPlan, DeckPolicy, SpellInfo, plan_deck


async def _spell_info(entry) -> SpellInfo | None:
    try:
        gspell = await entry.graphical_spell()
        if not gspell:
            return None
        template = await gspell.spell_template()
        if not template:
            return None
        name = await template.name()
        if not name:
            return None
        effects = []
        for eff in await gspell.spell_effects():
            effects.extend(await read_effects(eff))
        pip_cost = 0
        rank = await gspell.pip_cost()
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
            accuracy=await gspell.accuracy(),
            effects=effects,
        )
        return SpellInfo(card=card, max_copies=await entry.max_copies())
    except Exception as exc:
        logger.debug(f"unreadable spellbook entry: {exc}")
        return None


async def read_known_spells(builder: DeckBuilder) -> list[SpellInfo]:
    spells = []
    for entry in await builder.get_spell_list():
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

    async def get_spell_list(self):
        from wizwalker.memory.memory_objects.window import DynamicSpellListControl

        control = DynamicSpellListControl(
            self.client.hook_handler, await self._spell_list_window.read_base_address()
        )
        valid = []
        for entry in await control.spell_entries():
            try:
                graphical = await entry.graphical_spell()
                if graphical and (template := await graphical.spell_template()):
                    await template.name()
                    valid.append(entry)
            except Exception:
                pass
        return valid


async def _rebuild_open(client, school: str, policy: DeckPolicy, *, dry_run: bool):
    builder = await _attach_builder(client)
    known = await read_known_spells(builder)
    plan = plan_deck(known, school, policy)
    logger.info(f"known spells: {', '.join(s.name for s in known) or '(none)'}")
    logger.info(f"deck plan: {plan.describe()}")
    if dry_run or not plan.steps:
        return known, plan
    if not known:
        logger.warning("spellbook read returned no spells; leaving the deck alone")
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
