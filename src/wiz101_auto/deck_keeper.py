"""The one deck, kept: with deck adapting off (config `combat.adapt_deck:
false`), the in-game deck is put back to state/deck_general.json whenever it
differs, at a calm moment between steps.

The game adds a spell to the deck by itself when it's learned (Delusion and
Betrayal from Cyrus Drake, 4 copies each): those go. The deck file's
"when_learned" changes the deck once a spell is known, e.g.

    "when_learned": {"Orthrus": {"Orthrus": 5, "Humongofrog": 0, "ColossusStone_Trainable": 1}}

(the key is matched against known spell names, ignoring case; a count of 0
takes a card out). Spells not known yet are left out of the target.
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
ALWAYS_KEPT = {"reshuffle"}  # the player's own cards, never taken out


def target_deck(general: dict, known: set[str]) -> dict[str, int]:
    """The deck to keep: the file's deck with its "when_learned" changes for
    spells already known, limited to known spells."""
    deck = dict(general.get("deck") or {})
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
    return {n: c for n, c in deck.items() if n in known}


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

    def due(self) -> tuple[dict[str, int], dict] | None:
        """(target deck, its changes) when the game's deck differs from it."""
        from .deck import load_deck_counts

        if time.monotonic() - self._checked < CHECK_SECONDS:
            return None
        self._checked = time.monotonic()
        general = _load(GENERAL_FILE)
        known = set(_load(PROGRESS_FILE).get("known_spells") or [])
        current = load_deck_counts()
        if not general.get("deck") or not known or not current:
            return None
        target = target_deck(general, known)
        changes = deck_changes(current, target)
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

    async def tick(self, client) -> bool:
        """Between steps: put the deck back if it differs. True if it did."""
        if STALE_FILE.exists():
            return await self.refresh(client)
        due = self.due()
        if due is None:
            return False
        target, changes = due
        from .deck import set_deck
        from .upkeep import move_to_safety

        text = ", ".join(f"{n} {a}->{b}" for n, (a, b) in sorted(changes.items()))
        logger.info(f"deck: back to the one deck ({text})")
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
