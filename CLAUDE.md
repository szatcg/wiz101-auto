# wiz101-auto: operating and developing the bot

A Wizard101 bot (Python, Windows only) that hooks the game's memory through
WizWalker. This file tells a Claude Code session on the user's Windows PC how
to **run, watch, fix and restart** the bot without the user relaying anything.

## Environment

- Windows. Wizard101 must be running and **logged in with the wizard standing
  in the world** before the bot starts. You cannot log in for the user.
- Python venv at `.venv` (made by `wiz101.bat` or
  `py -3.14 -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev]"`).
- Bot entry point: `.venv\Scripts\python -m wiz101_auto <command>`
  (`.\wiz101.bat <command>` does the same after checking the install).
- User settings: `config.yaml` (git-ignored; created from `configs/myth.yaml`).

## Commands

| Command | Use |
|---|---|
| `start [--supervise]` | start in the background (also opens the live log window if none is open); `--supervise` auto-restarts after crashes |
| `dashboard` | progress dashboard at http://127.0.0.1:8101/ (world completion %, current quest ribbon); `start` launches it |
| `stop` | clean stop (unhooks the game); force-kills only after 30s |
| `restart [--supervise]` | stop + start |
| `status` | running? heartbeat age, zone, objective, health, fights/deaths, last log lines |
| `logs -n 200` | recent log; `logs -f` follows (blocks, so avoid in automation) |
| `inspect` | one-shot: what the bot reads from the game (needs the bot STOPPED) |
| `inspect --windows` | plus the visible UI window tree (for fixing UI paths) |
| `gear` | try every backpack item per slot and keep the best (needs the bot stopped) |
| `deck -c config.yaml` | known spells, current deck, planned deck (read-only) |
| `record [--minutes N]` | watches you walk a route: zone doors, NPC positions, trainer window layout → `state/route_record_*.txt`; end early by creating `state/stop.request` |
| `explore` | entities around the wizard with positions → `state/explore_*.txt` |
| `screenshot [-o path]` | game window → `state/screenshot.png` (Read it to see the screen; works while the bot runs) |

Only one process can hook the game at a time: run `inspect`/`deck`/`explore`
while the bot is stopped.

## The operating loop

1. `status`. If not running: `start --supervise`.
2. Every 30–60s: `status`. Healthy = heartbeat < 30s old and the objective or
   zone changes over time. Also watch `wiz101-auto.log` (DEBUG level) for
   WARNING/ERROR lines, `recovery step N/4` (the stall watchdog), deaths.
3. When it is stuck (same objective for minutes, watchdog cycling, repeated
   errors) or has crashed:
   1. `stop` (always clean; see rules).
   2. Diagnose from `wiz101-auto.log`, `state/status.json` and the saved UI or
      memory dumps in `state/` (list below). Use `inspect`/`inspect --windows`
      /`explore` for live facts about the current screen.
   3. Patch the code. Keep changes small and in the module that owns the
      behaviour (map below).
   4. Verify: `.venv\Scripts\python -m ruff check .` and
      `.venv\Scripts\python -m pytest -q` must pass. Add a unit test when the
      logic is pure (brain, deck_plan, collect matching, config…).
   5. `start --supervise` again and confirm the problem is gone in the log.
   6. Commit with a message that says what was stuck and why, then
      `git push origin main`.
4. Repeat. Summarise for the user what broke and what you changed.

## Rules

- **Always stop with `stop`.** Killing the Python process leaves hooks in the
  game; the next run then fails with "Could not hook into the game" (a WizWalker
  `PatternFailed`) until Wizard101 is fully restarted. If that happens, ask the
  user to restart the game and log back in; don't try to work around it.
- Don't send keystrokes or clicks to the game yourself (outside the bot), and
  don't close Wizard101.
- If the user presses Ctrl+Shift+Q or asks you to stop, stop and don't restart.
- The bot stops itself on safety limits (`safety.max_hours`,
  12 min without quest progress). Treat those as signals to investigate, not
  to blindly restart.
- Never commit `config.yaml`, `state/`, logs or `.venv` (they're git-ignored).

## Files the bot writes

| File | What |
|---|---|
| `wiz101-auto.log` | full DEBUG log (rotates at 10 MB) |
| `activity.log` | INFO+ only, short format: what the bot decides/does (read this first) |
| `docs/CompletedQuests.txt` | names of quests the bot completed, in order (a quest that leaves the quest book on two readings) |
| `state/learned_gates.json` | zone gates learned while playing (missing from the data files, e.g. Haunted Cave) |
| `state/setbacks.json` | defeats per objective and quests set aside after 2 losses (until a level-up or 1 hour) |
| `state/mark.json` | where the game's Mark is: a dungeon sigil (Recall after a defeat) or a travel mark (Recall when nearer the next objective than walking) |
| `state/dungeons.json` | learned dungeons (outside zone, sigil, arrival point/facing) and which boss is in which |
| `state/status.json` | heartbeat every 5s |
| `state/lifetime.json` | totals across sessions (deaths; seeded from the log's "wizard defeated" lines) |
| `state/quest_book.json` | the quest book at the last ranking (for the dashboard) |
| `docs/quests/<World>.txt` | quest lists for worlds after Wizard City (Spiral Tracker format), shown on the dashboard |
| `state/bot.out` | stdout/stderr of the background process (crash tracebacks) |
| `state/bot.pid`, `state/stop.request` | service bookkeeping |
| `state/progress.json` | level, known spells, last deck plan |
| `state/wisps.json` | learned health-wisp spawn points per zone |
| `state/npc_services_window_*.txt` | layout of an NPC's multi-quest menu |
| `state/spellbook_window.txt`, `state/spell_list_memory.txt` | spellbook UI / memory dumps |
| `state/trainer_window_*.txt` | spell trainer UI layout |

## Code map (`src/wiz101_auto/`)

| Module | Owns |
|---|---|
| `cli.py`, `service.py` | commands; background start/stop/status/supervise |
| `bot.py` | connects, runs the concurrent loops (combat, dialogue, quest, watchdog, status) |
| `quest.py` | quest step: objective, travel (teleport, doors), interact, collect, defeat, quest switching |
| `dungeon_heal.py` | heal trips: low in a dungeon, or no wisps near an objective in this zone: mark, world hub button, heal nearby, Recall back |
| `marks.py` | Mark/Recall decisions: mark before trips of 2+ zones, recall when mark + walk beats walking |
| `setbacks.py` | quests set aside after losing the same fight twice, or 5 min without progress (no objective change, no won fight) |
| `questlist.py` | the quest order to follow (`docs/QuestList.txt`) and the completed-quest log |
| `npc.py` | NPC multi-quest menu (`NPCServicesWin` / `NPCServicesOption*`) |
| `collect.py` | "Collect X" objectives (entity name matching) |
| `travel_data.py` | no-marker fallback: zone gates/names and Zeke/Eloise quest spots from WizSprinter's `traversalData` |
| `upkeep.py` | dialogue loop, quest-offer policy, potions, wisps, recovery, popups, Crowns window |
| `wisps.py` | remembered wisp spawn points |
| `watchdog.py` | 15 s stall detection and escalating recovery |
| `combat/brain.py`, `combat/model.py` | pure turn logic (unit tested) |
| `combat/reader.py`, `combat/fighter.py` | game ↔ model, playing rounds |
| `gear.py` | new backpack item: tries just it against what is worn; level-up: retries items that could not be worn before. `state/gear.json` remembers items already beaten (never retried) |
| `trainer.py` | trips to the school professor (Go Home, dorm door, school door) at `progression.train_levels`; trains new spells |
| `bossfarm.py`, `dungeons.py` | `mode: boss` (`boss_farm.boss`, `until_item`, `max_runs`): repeat a learned dungeon boss |
| `deck.py`, `deck_plan.py`, `progression.py` | spellbook reading, deck planning, level-ups, trainer |
| `ui.py` | UI window paths and helpers |
| `names.py` | cached display-name lookups (WizWalker's are very slow) |
| `safety.py`, `config.py` | hotkeys, stop requests, limits; YAML config |

WizWalker/WizSprinter come from the Deimos project (`libs/` in
github.com/Deimos-Wizard101/Deimos-Wizard101) and are installed into `.venv`;
read their source there when you need the memory API.

## Known gaps / open work

- Clicks and display scaling: WizWalker makes the bot DPI-aware but the game
  isn't, so on a monitor not at 100% the game sees the cursor shifted by
  (real client origin - scaled origin), e.g. 35px right, 24px down on the
  125% left monitor. That broke thin buttons
  (Pass, Flee, Yes/No). `bot.dpi_click_offset` measures it per click and
  `_click_left_of_center` shifts every cursor position by it; windows are
  clicked at their center (the old 25%-of-width hack is gone).
- Spell lists: `SpellListControl` entries are 0x78 bytes, `DeckListControl`
  0x28 (layout picked by most spells found). Hands also hold gear item cards
  ("X - Starter Wand", Heartbeat). Rebuilds are skipped when the read looks
  incomplete or the plan adds nothing. Adding cards (WizWalker's
  `add_by_name`) crashed the game twice, so `deck.auto_add` is off: missing cards
  are logged for adding by hand. The planner drops minions; the brain summons one (after urgent
  heals, before attacks) whenever none of ours is alive.
- Prospector Zeke's "Go To <place> Smith in <place>" quests have no quest
  marker; `travel_data.py` routes through gates and to the known Zeke spot.
  Quest-text place names missing from `displayZones.txt` go in
  `EXTRA_DISPLAY_ZONES`.
- Spell trainer automation is heuristic until `state/trainer_window_*.txt`
  has been captured and mapped.
- Minion spells are excluded from decks (the brain can't use them yet).
