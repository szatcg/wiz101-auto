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

Before a fight the bot heads into (a Defeat objective's enemy; the player's
rule): one enemy alone, boss or not, always gets the single-target deck (the
one found for it, else state/deck_single.json); an enemy with company gets
the AoE (general) deck, and after AOE_LOSSES_BEFORE_SINGLE losses with it to
that boss, the single-target one (losses per boss in state/boss_tactics.json).
A switch to the deck that's already in is skipped. A search after a loss
still runs and is remembered, but goes in only when single-target is due.
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
SEARCH_WAIT_MINUTES = 12  # longest wait for a boss's search before fighting it anyway
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


PROGRESS_FILE = Path("state") / "progress.json"
# Boss decks found so far, per enemy group ("Haru,Ronin Blademaster"): put in
# before that boss's fight, or right after a loss to it, instead of a new
# search (Shirataki Temple went Haru, Plague Oni, Haru: the Plague Oni deck
# lost to Haru while the deck that beat Haru was forgotten).
BOSS_DECKS = Path("state") / "boss_decks.json"
SWITCH_RETRIES = 2  # an interrupted deck switch is tried again this often
CACHED_LOSSES_BEFORE_SEARCH = 2  # a remembered deck that loses this often: search again


def _usable(entry: dict | None) -> bool:
    """A remembered boss deck that keeps the rules (2 heals: the ones from
    before the rule had none, and lost)."""
    from .combat.deckopt import BOSS_NEEDS, HEALS

    deck = (entry or {}).get("deck") or {}
    return bool(deck) and sum(deck.get(c, 0) for c in HEALS) >= BOSS_NEEDS["heals"]


SINGLE_FILE = Path("state") / "deck_single.json"  # the single-target deck for a lone boss
TACTICS_FILE = Path("state") / "boss_tactics.json"  # boss -> {"aoe_losses": n}
AOE_LOSSES_BEFORE_SINGLE = 3
DECISION_HOLD = 600.0  # seconds a deck call for an enemy holds (no flip-flopping)


LADDER_LOSSES = 3  # losses with one deck before the next rung (the player's rule)


def _tactic(key: str) -> dict:
    return _read(TACTICS_FILE).get(key, {})


def _save_tactic(key: str, entry: dict):
    data = _read(TACTICS_FILE)
    data[key] = entry
    try:
        TACTICS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def aoe_losses(boss: str) -> int:
    """Losses with the AoE deck in encounters with `boss` (any group)."""
    data = _read(TACTICS_FILE)
    n = 0
    for key, entry in data.items():
        if boss in key.split(","):
            n = max(n, int(entry.get("aoe_losses", 0)), int(entry.get("by_role", {}).get("aoe", 0)))
    return n


def note_fight(enemies: list[str]):
    """Remember how many enemies a fight with each of them had (the next
    fight with them is judged by it, not by who stands near in view: an
    enemy 420 away from Valerik Brightsword never joined)."""
    group = sorted(set(enemies))
    if not group:
        return
    data = _read(TACTICS_FILE)
    seen = data.setdefault("_seen", {})
    for name in group:
        seen[name] = len(group)
    try:
        TACTICS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def fought_alone(name: str) -> bool | None:
    """Was `name` alone in its last fight (None: never fought)."""
    n = _read(TACTICS_FILE).get("_seen", {}).get(name)
    return None if n is None else n == 1


def ladder_role(name: str) -> str | None:
    """The deck the loss ladder settled on for an encounter with `name`."""
    for key, entry in _read(TACTICS_FILE).items():
        if name in key.split(",") and entry.get("role"):
            return entry["role"]
    return None


def next_rung(by_role: dict, role: str) -> str | None:
    """After a loss with `role`: the deck to try next ('aoe', 'single', or
    'search' for a simulator search), or None to keep the same deck."""
    if by_role.get(role, 0) < LADDER_LOSSES:
        return None
    if role in ("aoe", "single"):
        other = "single" if role == "aoe" else "aoe"
        if by_role.get(other, 0) < LADDER_LOSSES:
            return other
    return "search"


def wants_single(alone: bool | None, losses: int) -> bool | None:
    """Single-target deck (True), the AoE deck (False) or no call (None:
    the boss isn't in view and hasn't beaten the AoE deck often enough)."""
    if losses >= AOE_LOSSES_BEFORE_SINGLE:
        return True
    return alone


def _known_spells() -> set[str]:
    return set(_read(PROGRESS_FILE).get("known_spells", []))


class DeckAdapter:
    def __init__(self):
        self.mode = _read(MODE_FILE) or {"deck": "general", "vs": None}
        self._search: subprocess.Popen | None = None
        self._search_vs: list[str] | None = None
        self._search_started = 0.0
        self._improve: subprocess.Popen | None = None
        self._wins = 0
        # Spells already weighed for the default deck (kept across restarts).
        self._known = set(self.mode.get("weighed") or _known_spells())
        self._pending: tuple[dict[str, int], str, list[str]] | None = None  # (deck, why, vs)
        self._bosses: set[str] | None = None  # boss names in the stats (read once)
        self._switch_retries = 0
        self._equip_search = True  # put the search's deck in when it's done (single-target due)
        self._decided: dict[str, tuple[bool, float]] = {}  # boss -> (single-target?, when decided)

    def _save(self):
        try:
            MODE_FILE.write_text(json.dumps(self.mode), encoding="utf-8")
        except OSError:
            pass

    def on_fight(self, enemies: list[str]):
        note_fight(enemies)

    def on_defeat(self, enemies: list[str], bosses: set[str] = frozenset()):
        """A lost fight (the player's ladder, instead of setting the quest
        aside): each loss counts against the deck that lost; after
        LADDER_LOSSES with one deck the other one goes in; after that many
        with both, a simulator search for exactly this group, its deck in
        the third deck item ('custom'); losing with that too, search again."""
        group = sorted(set(enemies))
        if not group:
            return
        key = ",".join(group)
        role = self.mode.get("role") or ("single" if self.mode.get("deck") == "boss" else "aoe")
        entry = _tactic(key)
        by_role = entry.setdefault("by_role", {})
        by_role[role] = by_role.get(role, 0) + 1
        entry["at"] = time.time()
        rung = next_rung(by_role, role)
        logger.info(f"deck: lost to {', '.join(group)} with the {role} deck "
                    f"({by_role[role]}/{LADDER_LOSSES} before the next deck)")
        if rung in ("aoe", "single"):
            entry["role"] = rung
            _save_tactic(key, entry)
            deck = self.single_deck(group[0]) if rung == "single" else None
            if rung == "single" and deck:
                self._pending = (deck[0], f"boss deck: single-target vs {key} (after {LADDER_LOSSES} losses)",
                                 group)
            elif rung == "aoe" and _read(GENERAL_FILE).get("deck"):
                self._pending = (_read(GENERAL_FILE)["deck"], "general deck", [])
            logger.info(f"deck: switching to the {rung} deck for {key}")
            return
        _save_tactic(key, entry)
        if rung != "search" or (self._search is not None and self._search.poll() is None):
            return
        entry["searches"] = entry.get("searches", 0) + 1
        by_role["custom"] = 0  # (a fresh deck: its own count)
        _save_tactic(key, entry)
        self._equip_search = True
        ADVICE_FILE.unlink(missing_ok=True)
        try:  # the search simulates from the stats: with this fight in them (a new boss)
            from .combat.calibrate import write_stats

            write_stats([Path("activity.log")])
        except Exception as exc:
            logger.debug(f"deck: stats rebuild failed: {exc}")
        logger.info(f"deck: both decks lost to {', '.join(group)}; searching a deck for them "
                    f"({SEARCH_MINUTES:.0f} min, search {entry['searches']})")
        self._search_vs = group
        self._search_started = time.time()
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
        # (A one-enemy win keeps the single-target deck: the next encounter
        # decides; switching back after each lone kill cost a minute a time.)
        if (self.mode.get("deck") == "boss" and set(self.mode.get("vs") or []) <= set(enemies)
                and len(set(enemies)) >= 2):
            self.mode = {"deck": "general-pending", "vs": None}
            self._save()
            logger.info(f"deck: beat {', '.join(enemies)}; back to the general deck at the next calm moment")

    def searching_for(self, boss: str) -> bool:
        """A deck search against `boss`'s group is running (and hasn't run
        past SEARCH_WAIT_MINUTES): wait for it rather than fight again with
        the deck that lost (the Sea Lord, fought again a minute after the
        first loss with the search 1 minute into its 6)."""
        if self._search is None or self._search.poll() is not None or boss not in (self._search_vs or []):
            return False
        return time.time() - self._search_started < SEARCH_WAIT_MINUTES * 60

    def single_deck(self, boss: str) -> tuple[dict[str, int], str, list[str]] | None:
        """The single-target deck for `boss`: the one found for its group,
        else the general single-target deck."""
        for key, entry in _read(BOSS_DECKS).items():
            group = key.split(",")
            if boss in group and _usable(entry):
                return entry["deck"], f"boss deck vs {key} (remembered)", group
        deck = _read(SINGLE_FILE).get("deck")
        return (deck, f"boss deck: single-target for {boss}", [boss]) if deck else None

    def prepare_for(self, boss: str, alone: bool | None = None):
        """A fight with `boss` is next (a Defeat objective; `alone`: no other
        enemy near it, None when it isn't in view): the single-target deck
        or the AoE deck before it (see the module notes)."""
        if not boss or self._pending:
            return
        if self._bosses is None:
            from .combat.sim import load_stats

            self._bosses = {n for n, e in load_stats().get("enemies", {}).items() if e.get("boss")}
        if boss not in self._bosses and alone is None:
            return  # (an everyday enemy of a boss's group: Imitsu Defouler with Plague Oni)
        past = fought_alone(boss)
        if past is not None:
            alone = past  # (its last fight says better than who stands near now)
        settled = ladder_role(boss)
        if settled == "custom":
            cached = next((e for k, e in _read(BOSS_DECKS).items() if boss in k.split(",") and e.get("deck")),
                          None)
            if cached and self.mode.get("role") != "custom":
                logger.info(f"deck: {boss} is next: the custom deck found for it")
                self._pending = (cached["deck"], f"boss deck: custom vs {boss} (remembered)", [boss])
            return
        if settled in ("aoe", "single"):
            alone = settled == "single"
        single = wants_single(alone, aoe_losses(boss))
        if single is None:
            return
        # One call per encounter: the step's look and the look before engaging
        # disagreed on Iona Pyrelance's company (enemies wander in and out of
        # range), and the deck went back and forth, a rebuild each time.
        now = time.time()
        held = self._decided.get(boss)
        if held is not None and now - held[1] < DECISION_HOLD and not (
                single and aoe_losses(boss) >= AOE_LOSSES_BEFORE_SINGLE):
            single = held[0]
        else:
            self._decided[boss] = (single, now)
        if single:
            found = self.single_deck(boss)
            if found is None:
                return
            deck, why, group = found
            if self.mode.get("cards") == deck or self.mode.get("role") == "single" or (
                    self.mode.get("deck") == "boss" and boss in (self.mode.get("vs") or [])):
                return  # (in already)
            how = "alone" if alone else f"after {aoe_losses(boss)} losses with the AoE deck"
            logger.info(f"deck: {boss} is next ({how}): putting in the single-target deck")
            self._pending = (deck, why, group)
            return
        if self.mode.get("deck", "general") not in ("general", "general-pending"):
            general = _read(GENERAL_FILE).get("deck")
            if general and self.mode.get("cards") != general and self.mode.get("role") != "aoe":
                logger.info(f"deck: {boss} has company: putting in the AoE deck")
                self._pending = (general, "general deck", [])

    def wanted(self) -> tuple[dict[str, int], str] | None:
        """The deck to put in now, if any: (spell -> copies, why)."""
        if self._pending:
            deck, why, vs = self._pending
            self._pending = None
            self._search_vs = vs
            return deck, why
        if self._search is not None and self._search.poll() is not None:
            self._search = None
            advice = _read(ADVICE_FILE)
            if advice.get("deck") and sorted(advice.get("vs", "").split(",")) == self._search_vs:
                cache = _read(BOSS_DECKS)
                cache[",".join(self._search_vs)] = advice
                try:
                    BOSS_DECKS.write_text(json.dumps(cache, indent=1), encoding="utf-8")
                except OSError:
                    pass
                if not self._equip_search:
                    logger.info(f"deck: a deck for {advice['vs']} is ready; the AoE deck stays until "
                                f"{AOE_LOSSES_BEFORE_SINGLE} losses with it")
                    return None
                for k in (",".join(self._search_vs),):
                    entry = _tactic(k)
                    entry["role"] = "custom"
                    _save_tactic(k, entry)
                return advice["deck"], (f"boss deck: custom vs {advice['vs']} (simulated: win "
                                        f"{advice['win']:.0%} in ~{advice['rounds']:.1f} rounds)")
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

    def _check_new_spells(self):
        """Spells just learned (a trainer trip, a quest reward): search the
        default deck again with them in the pool; they go in, or replace
        cards, only if the simulated fights come out better."""
        known = _known_spells()
        new = known - self._known
        if not new or not self._known:
            self._known = known or self._known
            return
        self._known = known
        self.mode["weighed"] = sorted(known)
        self._save()
        logger.info(f"deck: new spells {', '.join(sorted(new))}: weighing them for the default deck")
        if self._improve is not None and self._improve.poll() is None:
            self._improve.kill()  # (its pool didn't have them)
            self._improve = None
        self._improve_default()

    async def tick(self, client) -> bool:
        """Between fights: put in the deck that's due. True if it did."""
        self._check_new_spells()
        want = self.wanted()
        if not want:
            return False
        deck, why = want
        from .deck import set_deck
        from .upkeep import is_free, move_to_safety

        # A fight starting mid-switch (an Imitsu Defouler walked up while the
        # spellbook was open) left the deck half built: move clear first.
        await move_to_safety(client, 1500.0, "before changing the deck")
        # Everything outside the plan goes, spells the game put in by itself
        # when learned (Blinding Light) included: the player's call.
        from . import deckitems

        role = "custom" if "custom" in why else ("single" if why.startswith("boss") else "aoe")
        if deckitems.ready() and (role != "custom" or deckitems.load().get("custom")):
            # Two deck items, filled once: wear the other one (seconds, not a
            # card-by-card rebuild). The custom one (a search's deck for one
            # encounter, in the third item) is filled when it goes on.
            logger.info(f"deck: switching to the {role} deck item ({why})")
            if await deckitems.DeckItems(client).equip(role):
                if role == "custom":
                    await move_to_safety(client, 1500.0, "before filling the custom deck")
                    got = await set_deck(client, deck)
                    logger.info(f"deck: custom deck now {got}")
                self._switch_retries = 0
                self.mode = {"deck": "boss" if role == "single" else "general",
                             "vs": self._search_vs if role == "single" else None,
                             "role": role, "at": time.time()}
                self._save()
                return True
            logger.warning("deck: the deck item didn't go on; rebuilding the deck instead")
        logger.info(f"deck: switching to the {why}: {deck}")
        try:
            got = await set_deck(client, deck)
        except Exception as exc:
            logger.warning(f"deck: couldn't switch ({exc!r})")
            got = {}
        short = {n: c - got.get(n, 0) for n, c in deck.items() if got.get(n, 0) < c}
        if short:
            logger.warning(f"deck: short of the plan: {short}")
        if (not got or len(short) > 1 or not await is_free(client)) and self._switch_retries < SWITCH_RETRIES:
            # Interrupted (a fight, nothing read): again at the next calm moment.
            self._switch_retries += 1
            self._pending = (deck, why, self._search_vs or [])
            logger.info(f"deck: the switch didn't finish; trying again "
                        f"({self._switch_retries}/{SWITCH_RETRIES})")
            return True
        self._switch_retries = 0
        if why.startswith("boss"):
            self.mode = {"deck": "boss", "vs": self._search_vs, "at": time.time()}
        else:
            self.mode = {"deck": "general", "vs": None, "at": time.time()}
        self.mode["cards"] = dict(deck)  # what's in: the same deck again is no switch
        self._save()
        logger.success(f"deck: now {got}")
        return True
