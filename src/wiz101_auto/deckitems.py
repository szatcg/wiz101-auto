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
from .gear import PAGE, GearManager

ITEMS_FILE = Path("state") / "deck_items.json"  # {"aoe": name, "single": name, "tab": window name}
BUTTONS = [*PAGE, "ButtonLayout"]


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

    async def equip(self, role: str) -> bool:
        """Put on the deck item for `role` ('aoe' or 'single'). True if worn after."""
        d = load()
        name, tab = d.get(role), d.get("tab")
        if not name or not tab:
            return False
        if not await self.gear._open():
            logger.warning("decks: the backpack didn't open")
            return False
        try:
            ok = await self.gear._equip_by_name(tab, name)
        finally:
            await self.gear._close()
        logger.info(f"decks: {'wearing' if ok else 'could not put on'} {name!r} ({role} deck)")
        return ok

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


