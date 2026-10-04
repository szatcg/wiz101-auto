"""The one deck, kept: with deck adapting off (config `combat.adapt_deck:
false`), the in-game deck is put back to state/deck_general.json whenever it
differs, at a calm moment between steps.

The game adds a spell to the deck by itself when it's learned (Delusion and
Betrayal from Cyrus Drake, 4 copies each): those go. The deck file's
"when_learned" changes the deck once a spell is known, e.g.

    "when_learned": {"Orthrus": {"Orthrus": 5, "Humongofrog": 0, "ColossusStone_Trainable": 1}}

(the key is matched against known spell names, ignoring case; a count of 0
takes a card out). Spells not known yet are left out of the target.

With two deck items set up (`decks --setup`, state/deck_items.json), each
holds one deck: the everyday deck in the "aoe" item, the boss deck in the
"single" one. A switch equips the other item (the player's: adding and
removing cards before every dungeon took minutes); cards are changed only
when the worn item's own cards differ from its deck (filling it once).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

GENERAL_FILE = Path("state") / "deck_general.json"
PROGRESS_FILE = Path("state") / "progress.json"
CHECK_SECONDS = 60.0  # how often the deck file is compared with the game's
STALE_FILE = Path("state") / "deck_stale.flag"  # a fight drew a card the stored deck lacks
TRIES_PER_TARGET = 2  # set_deck runs for one target before leaving it (a card short of max copies)
WEAR_RETRY_SECONDS = 600.0  # a deck item that wouldn't go on: tried again after this
ALWAYS_KEPT = {"reshuffle"}  # the player's own cards, never taken out
PRISM_COPIES = 3  # our school's prism for a boss of our school (the player: 3, so one comes in time)
STATS_FILE = Path("state") / "enemy_stats.json"


def school_on_file(name: str, stats_file: Path = STATS_FILE) -> str:
    """An enemy's school as read in an earlier fight ("" if never fought)."""
    want = "".join(c for c in name.lower() if c.isalnum())
    for n, v in (_load(stats_file).get("enemies") or {}).items():
        if "".join(c for c in n.lower() if c.isalnum()) == want and v.get("school"):
            return str(v["school"]).lower()
    return ""


def boss_prism(enemy_school: str, my_school: str, known: set[str]) -> dict[str, int]:
    """The player's: a Myth boss for a Myth wizard: Myth Prisms go in the deck
    before the fight (our hits on it then land as the opposite school), and
    come out after. {} when the boss isn't of our school or the prism isn't
    known."""
    if not enemy_school or not my_school or enemy_school.lower() != my_school.lower():
        return {}
    want = f"{my_school.strip().lower()} prism"
    name = next((k for k in sorted(known) if k.lower() == want), None)
    return {name: PRISM_COPIES} if name else {}


def target_deck(general: dict, known: set[str], boss: bool = False,
                extra: dict[str, int] | None = None) -> dict[str, int]:
    """The deck to keep: the file's deck (its "boss_deck" in dungeons and
    before boss fights, when it has one) with its "when_learned" changes for
    spells already known, plus `extra` (a boss's prisms), limited to known
    spells."""
    deck = dict((general.get("boss_deck") if boss else None) or general.get("deck") or {})
    for key, change in (general.get("when_learned") or {}).items():
        learned = next((k for k in sorted(known) if key.lower() in k.lower()), None)
        if learned is None:
            continue
        for name, copies in change.items():
            name = learned if name == key else name
            if copies <= 0:
                deck.pop(name, None)
            else:
                deck[name] = copies
    for name, copies in (extra or {}).items():
        deck[name] = deck.get(name, 0) + copies
    return {n: c for n, c in deck.items() if n in known}


def item_role(boss: bool) -> str:
    """The deck item for a deck: the boss deck in the "single" item, the
    everyday deck in the "aoe" one."""
    return "single" if boss else "aoe"


def deck_changes(current: dict[str, int], target: dict[str, int]) -> dict[str, tuple[int, int]]:
    """{card: (now, wanted)} for every card whose count differs (the player's
    own cards like Reshuffle aside)."""
    out = {}
    for name in set(current) | set(target):
        if name.strip().lower() in ALWAYS_KEPT:
            continue
        now, want = current.get(name, 0), target.get(name, 0)
        if now != want:
            out[name] = (now, want)
    return out


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class DeckKeeper:
    def __init__(self):
        self._checked = 0.0
        self._tries: dict[str, int] = {}
        self._boss = False  # the boss deck was the one asked for last
        self._extra: dict[str, int] = {}  # cards asked for on top (a boss's prisms)
        self.last_boss = False
        self._worn: str | None = None  # deck item role worn ("aoe" everyday, "single" boss)
        self._wear_failed_at = -1e9

    def due(self, boss: bool = False,
            extra: dict[str, int] | None = None) -> tuple[dict[str, int], dict] | None:
        """(target deck, its changes) when the game's deck differs from it."""
        from .deck import load_deck_counts

        extra = extra or {}
        if time.monotonic() - self._checked < CHECK_SECONDS and boss == self._boss and extra == self._extra:
            return None
        self._boss = boss
        self._extra = extra
        self._checked = time.monotonic()
        general = _load(GENERAL_FILE)
        known = set(_load(PROGRESS_FILE).get("known_spells") or [])
        current = load_deck_counts()
        if not general.get("deck") or not known or not current:
            return None
        target = target_deck(general, known, boss, extra)
        # Cards that aren't learned spells (treasure cards: the player's Giant
        # sun enchants) are the player's own: never taken out or counted.
        changes = {n: c for n, c in deck_changes(current, target).items() if n in known}
        key = json.dumps(target, sort_keys=True)
        if not changes or self._tries.get(key, 0) >= TRIES_PER_TARGET:
            return None
        self._tries[key] = self._tries.get(key, 0) + 1
        return target, changes

    async def refresh(self, client) -> bool:
        """Re-read the deck and the known spells from the spellbook (a spell
        learned from a quest: Orthrus was in fights but neither the stored
        deck nor the known spells had it, so the deck was never fixed and the
        fights counted cards that weren't there). True if it read."""
        from .deck import (
            _attach_builder,
            _log_current_deck,
            close_spellbook,
            open_spellbook,
            read_known_spells,
            save_deck_counts,
        )
        from .upkeep import move_to_safety

        await move_to_safety(client, 1500.0, "before reading the spellbook")
        await open_spellbook(client)
        try:
            builder = await _attach_builder(client)
            names = await _log_current_deck(client, builder) or []
            known = [s.name for s in await read_known_spells(builder)]
        finally:
            await close_spellbook(client)
        if names:
            save_deck_counts(names)
        if known:
            progress = _load(PROGRESS_FILE)
            progress["known_spells"] = sorted(set(known))
            PROGRESS_FILE.write_text(json.dumps(progress, indent=1), encoding="utf-8")
        STALE_FILE.unlink(missing_ok=True)
        self._checked = 0.0  # compare at once
        logger.info(f"deck: re-read the spellbook ({len(names)} cards, {len(known)} spells known)")
        return True

    async def _wear(self, client, boss: bool) -> bool | None:
        """Deck items: the one for this deck on (then its cards re-read).
        True if it switched, False if it failed, None if worn already or no
        deck items."""
        from . import deckitems

        if not deckitems.ready():
            return None
        role = item_role(boss)
        if self._worn == role or time.monotonic() - self._wear_failed_at < WEAR_RETRY_SECONDS:
            return None
        from .upkeep import move_to_safety

        logger.info(f"deck: to the {'boss' if boss else 'everyday'} deck item ({deckitems.load().get(role)})")
        await move_to_safety(client, 1500.0, "before changing the deck")
        try:
            ok = await deckitems.DeckItems(client).equip(role)
        except Exception as exc:
            logger.warning(f"deck: couldn't put the deck item on ({exc!r})")
            ok = False
        if not ok:
            # (Not every step: cards are changed meanwhile, as before.)
            self._wear_failed_at = time.monotonic()
            return False
        self._worn = role
        await self.refresh(client)  # (its own cards: filled once, then just worn)
        return True

    async def tick(self, client, boss: bool = False, extra: dict[str, int] | None = None) -> bool:
        """Between steps: put the deck back if it differs (with `extra` on
        top: a boss's prisms, out again once they're not asked for). True if
        it did."""
        self.last_boss = boss  # (the deck asked for: kept while no fight decides)
        if STALE_FILE.exists():
            return await self.refresh(client)
        if await self._wear(client, boss):
            return True
        due = self.due(boss, extra)
        if due is None:
            return False
        target, changes = due
        from .deck import set_deck
        from .upkeep import move_to_safety

        text = ", ".join(f"{n} {a}->{b}" for n, (a, b) in sorted(changes.items()))
        logger.info(f"deck: to the {'boss' if boss else 'everyday'} deck ({text})")
        await move_to_safety(client, 1500.0, "before changing the deck")
        try:
            got = await set_deck(client, target)
        except Exception as exc:
            logger.warning(f"deck: couldn't change it ({exc!r})")
            return True
        short = {n: c - got.get(n, 0) for n, c in target.items() if got.get(n, 0) < c}
        if short:
            logger.warning(f"deck: short of the one deck: {short} (max copies, or not addable)")
        return True
