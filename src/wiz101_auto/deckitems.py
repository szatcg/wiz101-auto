"""Two deck items: one holds the single-target deck, the other the AoE deck.

Rebuilding the deck card by card took a minute or more per switch (and
went back and forth before a boss). With two deck items, each filled once,
a switch is equipping the other one from the backpack's deck tab: seconds.

`decks --setup` (bot stopped) lists the deck items, takes the worn one as
the AoE deck and another as the single-target deck (or the names given),
and fills each once with its cards (state/deck_general.json and
state/deck_single.json); state/deck_items.json remembers which is which.
Then the bot's deck switches (deck_adapt) equip the item instead.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from . import ui
from .gear import NEXT_PAGE, PAGE, GearManager

DECK_PAGE = ["WorldView", "DeckConfiguration", "DeckConfigurationWindow", "ControlSprite", "DeckPage"]
ITEMS_FILE = Path("state") / "deck_items.json"  # {"aoe": name, "single": name, "tab": window name}
BUTTONS = [*PAGE, "ButtonLayout"]
DECK_SLOTS = 8  # decks the arrows go through before giving up


def _same_item(shown: str, want: str) -> bool:
    """The item `want` names: its exact name, or a part of it ("feint" ->
    "Jewel of the Feint"), any case."""
    a = "".join(c for c in (shown or "").lower() if c.isalnum())
    b = "".join(c for c in (want or "").lower() if c.isalnum())
    return bool(a and b) and (a == b or b in a)


def load() -> dict:
    try:
        return json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict):
    try:
        ITEMS_FILE.parent.mkdir(exist_ok=True)
        ITEMS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def _note_cards(role: str):
    """The cards of the deck now worn, for fights (state/deck.json): its role's
    deck, plus what's kept in every deck (a stale count said 13 cards left
    with the deck spent, and Reshuffle was never played)."""
    from .combat.deckopt import GENERAL_FILE
    from .deck import save_deck_counts
    from .deck_adapt import SINGLE_FILE

    path = SINGLE_FILE if role == "single" else GENERAL_FILE
    try:
        cards = json.loads(path.read_text(encoding="utf-8"))["deck"]
    except (OSError, ValueError, KeyError):
        return
    names = [n for n, k in cards.items() for _ in range(k)] + ["Reshuffle"]
    save_deck_counts(names)


def ready() -> bool:
    d = load()
    return bool(d.get("aoe") and d.get("single") and d.get("tab") and d.get("filled") == ["aoe", "single"])


def pick_roles(items: list[tuple[str, bool]], aoe: str = "", single: str = "") -> tuple[str, str] | None:
    """(AoE item, single-target item) from the deck tab's (name, worn) list:
    the names given, else the worn one for AoE (it holds the AoE deck now)
    and another for single-target."""
    names = [n for n, _ in items]

    def find(want: str) -> str:  # "dragonfire" -> "Dragonfire Deck" (case, part of the name)
        key = "".join(c for c in want.lower() if c.isalnum())
        exact = [n for n in names if "".join(c for c in n.lower() if c.isalnum()) == key]
        part = [n for n in names if key and key in "".join(c for c in n.lower() if c.isalnum())]
        return (exact or part or [""])[0]

    if aoe and single:
        aoe, single = find(aoe), find(single)
        return (aoe, single) if aoe and single and aoe != single else None
    worn = next((n for n, w in items if w), None)
    aoe = aoe or worn or (names[0] if names else "")
    others = [n for n in names if n != aoe]
    single = single or (others[0] if others else "")
    return (aoe, single) if aoe and single and aoe != single else None


class DeckItems:
    def __init__(self, client):
        self.client = client
        self.gear = GearManager(client, "")

    async def deck_tab(self) -> str | None:
        """The backpack's deck tab button (its window name), found by name."""
        w = await ui.window_at(self.client, BUTTONS)
        if w is None:
            return None
        try:
            for child in await w.children():
                name = await child.name()
                if "deck" in name.lower():
                    return name
        except Exception:
            return None
        return None

    async def list_decks(self, tab: str) -> list[tuple[str, bool]]:
        return await self.gear._scan_tab(tab)

    async def equip_on_deck_page(self, name: str) -> bool:
        """The fast way (the player's): the spellbook's deck page, its arrows
        to the deck named `name`, Equip, close. True if it's worn after."""
        from .deck import close_spellbook, open_spellbook

        def norm(t: str) -> str:
            return "".join(c for c in ui._TAGS.sub("", t or "").lower() if c.isalnum())

        await open_spellbook(self.client)
        try:
            want = norm(name)
            for _ in range(DECK_SLOTS):
                shown = norm(await ui.text_at(self.client, [*DECK_PAGE, "DeckName"]))
                if shown and (want.startswith(shown) or shown.startswith(want)):
                    break  # (the page cuts long names short)
                if not await ui.click(self.client, [*DECK_PAGE, "NextDeck"]):
                    return False
                await asyncio.sleep(0.4)
            else:
                return False
            if await ui.is_visible(self.client, [*DECK_PAGE, "equipFist"]):
                return True  # worn already
            await ui.click(self.client, [*DECK_PAGE, "EquipButton"])
            await asyncio.sleep(0.8)
            box = await ui.modal_box(self.client)
            if box is not None and "copy" in (await ui.modal_text(box)).lower():
                await ui.modal_click(self.client, box, "rightButton")  # no: each deck has its cards
                await asyncio.sleep(0.6)
            return await ui.is_visible(self.client, [*DECK_PAGE, "equipFist"])
        finally:
            await close_spellbook(self.client)

    async def equip(self, role: str) -> bool:
        """Put on the deck item for `role` ('aoe' or 'single'). True if worn after."""
        d = load()
        name, tab = d.get(role), d.get("tab")
        if not name or not tab:
            return False
        try:
            if await self.equip_on_deck_page(name):
                logger.info(f"decks: wearing {name!r} ({role} deck)")
                _note_cards(role)
                return True
        except Exception as exc:
            logger.debug(f"decks: deck page switch failed: {exc!r}")
        logger.info("decks: the deck page didn't do it; through the backpack")
        if not await self.gear._open():
            logger.warning("decks: the backpack didn't open")
            return False
        try:
            ok = await self._put_on(tab, name)
        finally:
            await self.gear._close()
        logger.info(f"decks: {'wearing' if ok else 'could not put on'} {name!r} ({role} deck)")
        if ok:
            _note_cards(role)
        return ok

    async def amulet_tab(self) -> str | None:
        """The backpack's amulet tab button, found by name (like the deck tab)."""
        w = await ui.window_at(self.client, BUTTONS)
        if w is None:
            return None
        try:
            for child in await w.children():
                name = await child.name()
                if "amulet" in name.lower() or "necklace" in name.lower():
                    return name
        except Exception:
            return None
        return None

    async def equip_amulet(self, role: str) -> bool | None:
        """The amulet that goes with this deck (deck_items.json "amulets":
        {"aoe": "shango", "single": "feint"}, part of each name): the player's
        Shango's Mythblade amulet for questing, the Jewel of the Feint for
        bosses. True if worn after, None with none set for the role."""
        want = (load().get("amulets") or {}).get(role)
        if not want:
            return None
        if not await self.gear._open():
            return False
        try:
            tab = await self.amulet_tab()
            ok = bool(tab) and await self._put_on(tab, want)
        finally:
            await self.gear._close()
        logger.info(f"decks: {'wearing' if ok else 'could not put on'} the amulet {want!r} ({role})")
        return ok

    async def _put_on(self, tab: str, name: str) -> bool:
        """Equip the deck item `name`; an empty deck asks to copy the old
        deck's spells: no (each deck holds its own cards)."""
        await self.gear._open_tab(tab)
        for _ in range(3):
            for window, n, worn in await self.gear._items_on_page():
                if not _same_item(n, name):
                    continue
                if worn:
                    return True
                await self.gear._equip(window)
                await asyncio.sleep(1.2)
                box = await ui.modal_box(self.client)
                if box is not None and "copy" in (await ui.modal_text(box)).lower():
                    await ui.modal_click(self.client, box, "rightButton")  # no copying
                    await asyncio.sleep(1.0)
                break
            else:
                if not await ui.click(self.client, NEXT_PAGE):
                    break
                await asyncio.sleep(0.6)
                continue
        await self.gear._open_tab(tab)
        for _ in range(3):
            for _w, n, worn in await self.gear._items_on_page():
                if _same_item(n, name):
                    return worn
            if not await ui.click(self.client, NEXT_PAGE):
                break
            await asyncio.sleep(0.6)
        return False

    async def setup(self, aoe: str = "", single: str = "") -> bool:
        """Find the deck items, choose the roles, fill each once."""
        from .combat.deckopt import GENERAL_FILE
        from .deck import set_deck
        from .deck_adapt import SINGLE_FILE

        if not await self.gear._open():
            print("the backpack didn't open")
            return False
        try:
            tab = await self.deck_tab()
            if not tab:
                names = [await c.name() for c in await (await ui.window_at(self.client, BUTTONS)).children()]
                print(f"no deck tab found among the backpack's tabs: {names}")
                return False
            items = await self.list_decks(tab)
        finally:
            await self.gear._close()
        print(f"deck items ({tab}): " + ", ".join(f"{n}{' (worn)' if w else ''}" for n, w in items))
        roles = pick_roles(items, aoe, single)
        if roles is None:
            print("need two deck items (buy a second deck, or give --aoe/--single names that match)")
            return False
        data = {"tab": tab, "aoe": roles[0], "single": roles[1], "filled": []}
        save(data)
        decks = {role: json.loads(path.read_text(encoding="utf-8"))["deck"]
                 for role, path in (("aoe", GENERAL_FILE), ("single", SINGLE_FILE))}
        for cards in decks.values():
            cards.setdefault("Reshuffle", 1)  # (the player's: in every deck)
        for role in ("aoe", "single"):
            if not await self.equip(role):
                print(f"could not put on {data[role]!r}")
                return False
            await asyncio.sleep(1.0)
            got = await set_deck(self.client, decks[role])
            print(f"{role} deck in {data[role]!r}: {got}")
            data["filled"].append(role)
            save(data)
        await self.equip("aoe")
        print("done: switches now equip these deck items")
        return True


