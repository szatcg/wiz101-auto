"""The deck follows the fights: a boss deck after a loss, the general deck after.

- Lost a fight: a deck search against exactly those enemies starts in the
  background (combat/deckopt.py, a separate process: the bot plays on). When
  it's done, the next calm moment (not in a fight) the deck is rebuilt to
  it by clicks (deck.set_deck).
- Won against the enemies the boss deck was made for: back to the general
  deck (state/deck_general.json: blades and Humongofrog for everyday groups).
- Every IMPROVE_EVERY wins the default deck's search runs again from it on
  the latest fights (slowly improving it); a better result replaces it.

state/deck_mode.json remembers which deck is in and what it was for.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

from .combat.deckopt import ADVICE_FILE, GENERAL_FILE, SIZE_COST, WIN_WEIGHT

MODE_FILE = Path("state") / "deck_mode.json"
SEARCH_MINUTES = 6.0
IMPROVE_EVERY = 25  # wins between searches improving the default deck
CANDIDATE_FILE = Path("state") / "deck_general_candidate.json"


def deck_score(d: dict) -> float:
    """The search's score of a saved result (win rate, rounds, size)."""
    return WIN_WEIGHT * d.get("win", 0) - d.get("rounds", 99) - SIZE_COST * sum(d.get("deck", {}).values())


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class DeckAdapter:
    def __init__(self):
        self.mode = _read(MODE_FILE) or {"deck": "general", "vs": None}
        self._search: subprocess.Popen | None = None
        self._search_vs: list[str] | None = None
        self._improve: subprocess.Popen | None = None
        self._wins = 0

    def _save(self):
        try:
            MODE_FILE.write_text(json.dumps(self.mode), encoding="utf-8")
        except OSError:
            pass

    def on_defeat(self, enemies: list[str]):
        """A lost fight: search a deck for exactly this group (once per group
        at a time; the search runs as its own process)."""
        group = sorted(set(enemies))
        if not group or (self._search is not None and self._search.poll() is None):
            return
        from .combat.sim import load_stats

        known = load_stats().get("enemies", {})
        if not any(known.get(n, {}).get("boss") for n in group):
            # Everyday enemies (Otomo Courier, Sanzoku Outlaw): the default deck
            # stays; a boss deck is for bosses (the player's rule).
            logger.info(f"deck: lost to {', '.join(group)} (no boss): keeping the default deck")
            return
        if self.mode.get("deck") == "boss" and sorted(self.mode.get("vs") or []) == group:
            return  # already on the deck made for them (the next loss is the fight's variance)
        ADVICE_FILE.unlink(missing_ok=True)
        logger.info(f"deck: lost to {', '.join(group)}; searching a deck for them ({SEARCH_MINUTES:.0f} min)")
        self._search_vs = group
        self._search = subprocess.Popen(
            [sys.executable, "-m", "wiz101_auto.combat.deckopt", "--vs", ",".join(group),
             "--minutes", str(SEARCH_MINUTES), "--out", str(ADVICE_FILE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        )

    def _improve_default(self):
        if self._improve is not None and self._improve.poll() is None:
            return
        CANDIDATE_FILE.unlink(missing_ok=True)
        start = ["--start", str(GENERAL_FILE)] if GENERAL_FILE.exists() else ["--thin"]
        logger.info("deck: improving the default deck on the latest fights (background)")
        self._improve = subprocess.Popen(
            [sys.executable, "-m", "wiz101_auto.combat.deckopt", *start, "--minutes", str(SEARCH_MINUTES),
             "--out", str(CANDIDATE_FILE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        )

    def on_win(self, enemies: list[str]):
        """Beat the group the boss deck was for: the general deck comes back.
        Every IMPROVE_EVERY wins: improve the default deck."""
        self._wins += 1
        if self._wins % IMPROVE_EVERY == 0:
            self._improve_default()
        if self.mode.get("deck") == "boss" and set(self.mode.get("vs") or []) <= set(enemies):
            self.mode = {"deck": "general-pending", "vs": None}
            self._save()
            logger.info(f"deck: beat {', '.join(enemies)}; back to the general deck at the next calm moment")

    def wanted(self) -> tuple[dict[str, int], str] | None:
        """The deck to put in now, if any: (spell -> copies, why)."""
        if self._search is not None and self._search.poll() is not None:
            self._search = None
            advice = _read(ADVICE_FILE)
            if advice.get("deck") and sorted(advice.get("vs", "").split(",")) == self._search_vs:
                return advice["deck"], (f"boss deck vs {advice['vs']} (simulated: win {advice['win']:.0%} in "
                                        f"~{advice['rounds']:.1f} rounds)")
        if self._improve is not None and self._improve.poll() is not None:
            self._improve = None
            cand, cur = _read(CANDIDATE_FILE), _read(GENERAL_FILE)
            if cand.get("deck") and (not cur.get("deck") or deck_score(cand) > deck_score(cur)):
                GENERAL_FILE.write_text(json.dumps(cand, indent=1), encoding="utf-8")
                logger.info(f"deck: a better default deck (simulated win {cand['win']:.0%} in "
                            f"~{cand['rounds']:.1f} rounds)")
                if self.mode.get("deck", "general") == "general":
                    return cand["deck"], "general deck (improved)"
        if self.mode.get("deck") == "general-pending":
            general = _read(GENERAL_FILE).get("deck")
            if general:
                return general, "general deck"
            self.mode = {"deck": "general", "vs": None}
            self._save()
        return None

    async def tick(self, client) -> bool:
        """Between fights: put in the deck that's due. True if it did."""
        want = self.wanted()
        if not want:
            return False
        deck, why = want
        from .deck import set_deck

        # Everything outside the plan goes, spells the game put in by itself
        # when learned (Blinding Light) included: the player's call.
        logger.info(f"deck: switching to the {why}: {deck}")
        try:
            got = await set_deck(client, deck)
        except Exception as exc:
            logger.warning(f"deck: couldn't switch ({exc!r})")
            return False
        short = {n: c - got.get(n, 0) for n, c in deck.items() if got.get(n, 0) < c}
        if short:
            logger.warning(f"deck: short of the plan: {short}")
        if why.startswith("boss"):
            self.mode = {"deck": "boss", "vs": self._search_vs, "at": time.time()}
        else:
            self.mode = {"deck": "general", "vs": None, "at": time.time()}
        self._save()
        logger.success(f"deck: now {got}")
        return True
