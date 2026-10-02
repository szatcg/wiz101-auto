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
| (stream) | http://127.0.0.1:8101/stream: a 1920x1080 stream layout (transparent 1440x810 game window at 20,20; sidebar stats; the bot's thoughts feed and battle card from activity.log via /thoughts.json; while farming, runs and loot targets) |
| (sim) | http://127.0.0.1:8101/sim: the combat simulator's visualizer: every deck search's report (`state/sim_runs/`): decks tried, the 5 fastest winners iterated on, the chosen deck, casts/moves per fight, and recorded sample fights on a battle board (step / auto-play; `#step=N&fight=I` opens a step); start a search against chosen enemies from the page |
| `farm` / `farm --stop` | farm Mount Olympus with teams (`state/farm.json`); turns on by itself when the main quest is stuck; run count and set pieces on /stream |
| `publish-setup owner/repo` | one-time: publish the dashboard on GitHub Pages (public repo; the dashboard server then pushes data every 2 min). Live: https://szatcg.github.io/wizzbot-tracker/ |
| `pet [--games N]` | (also by itself whenever the wizard's energy is full: `pet.auto`, until the pet reaches its goal stage, `pet.goals` e.g. bloodbat: adult, else `pet.default_goal` mega; `state/pet.json`) the running bot goes to the Pet Pavilion at its next free moment, plays the dance game (moves read from memory) until the pet is out of energy or snacks (or N games), feeds the first snack after each win, then Recalls back (`state/pet.request`) |
| `pin "Quest"` / `pin` | follow that quest until it's done or set aside / unpin (the main-story quest tracked at start is pinned too; `state/quest_pin.json`) |
| `stop` | clean stop (unhooks the game); force-kills only after 30s |
| `restart [--supervise]` | stop + start |
| `status` | running? heartbeat age, zone, objective, health, fights/deaths, last log lines |
| `logs -n 200` | recent log; `logs -f` follows (blocks, so avoid in automation) |
| `set-login` | once, by the player: saves the game login in Windows Credential Manager (never in a file) for automatic game restarts |
| `restart-game` | close Wizard101, start it and log in (bot stopped); `start --supervise` does it by itself when the game freezes or crashes |
| `relog` | quit to character select and Play again (bot stopped): frees a wizard the game won't move; the bot also does it by itself after 8 steps in a row fail on WizWalker's `should_update` |
| `inspect` | one-shot: what the bot reads from the game (needs the bot STOPPED) |
| `inspect --windows` | plus the visible UI window tree (for fixing UI paths) |
| `gear` | try every backpack item per slot and keep the best (needs the bot stopped) |
| `decks --setup [--aoe NAME] [--single NAME]` | once, bot stopped, with two deck items: the worn one becomes the AoE deck, another the single-target deck; each is filled once (`state/deck_items.json`); deck switches then just equip the item |
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
  don't close Wizard101 yourself. The exception (the player's request,
  2026-10-01): the supervisor restarts a frozen or crashed game by itself
  (`gamerestart.py`: a loading screen for 5 min, the window not responding for
  2 min, no game window, no hook), at most once every 15 minutes, retrying every 15 minutes until it gets in, logging in with
  the login saved by `set-login`. Never ask for, read or print the password.
- If the user presses Ctrl+Shift+Q or asks you to stop, stop and don't restart.
- The bot runs until stopped (`safety.max_hours: 0` = no limit). When it
  can't progress it keeps going on side quests, or grinds for experience, and
  logs `ALERT: main quest ... stuck` to `activity.log`: send the user a phone
  notification for those so they can help get it back on track.
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
| `state/deck.json` | the in-game deck (spell -> copies) as last read; fights plan with what's left of it |
| `state/looted_gear.json` | every piece of gear looted (slot, first looted, count); a new item already listed is a duplicate and isn't gear-checked |
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
| `safe_teleport.py` | wraps `client.teleport`: re-reads enemies and duel circles before every jump and lands clear of them; `allow_engage` for teleports meant to start a fight |
| `entitymap.py` | what was seen where (state/entity_map.json) and where door walks worked (state/doors.json): searches visit known spots first |
| `puzzles.py` | switch puzzles ("Use X" with X hidden until the right obelisks/braziers are lit): flips the room's switches through every combination (Gray code) until X appears |
| `teamup.py` | group dungeons (`TEAM_UP_DUNGEONS`, e.g. Mount Olympus): never solo; press TEAM UP! on the sigil, accept, wait up to 15 min for a team (window trees saved to `state/teamup_*.txt`) |
| `bring_out.py` | a "Talk to X" with no marker and X nowhere (Clockwork in Katzenstein's Lab): beat the room's boss, collect pick-ups, talk to NPCs, try switches, one per step, until X appears |
| `givers.py` | talks once to each named NPC nearby (in the main world) to pick up quests |
| `marks.py` | Mark/Recall decisions: mark before trips of 2+ zones, recall when mark + walk beats walking |
| `setbacks.py` | side quests set aside after losing the same fight twice, or 5 min without progress (no objective change, no won fight); main-story fights are never set aside for losses: the deck ladder in `deck_adapt.py` (3 losses with one deck item -> the other, 3 with both -> a simulator search whose deck goes in the third deck item, `custom`; `state/boss_tactics.json`) |
| `questlist.py` | the quest order to follow (`docs/QuestList.txt`) and the completed-quest log |
| `npc.py` | NPC multi-quest menu (`NPCServicesWin` / `NPCServicesOption*`) |
| `collect.py` | "Collect X" objectives (entity name matching) |
| `travel_data.py` | no-marker fallback: zone gates/names and Zeke/Eloise quest spots from WizSprinter's `traversalData` |
| `upkeep.py` | dialogue loop, quest-offer policy, potions, wisps, recovery, popups, Crowns window |
| `wisps.py` | remembered wisp spawn points |
| `watchdog.py` | 15 s stall detection and escalating recovery |
| `combat/brain.py`, `combat/model.py` | pure turn logic (unit tested) |
| `combat/reader.py`, `combat/fighter.py` | game ↔ model, playing rounds |
| `combat/calibrate.py` | reads `activity.log`: our predicted vs real damage, our hit rates, each enemy's damage per round → `state/enemy_stats.json` (rebuilt at every start; `python -m wiz101_auto.combat.calibrate` prints the report) |
| `combat/sim.py` | Monte Carlo fights with the real brain; enemies hit as logged; can start from the live battle. Compare decks with `win_rate` |
| `combat/deckopt.py` | deck search: hill climb from the current deck and (bosses) a single-target start, then the 5 fastest winners (>=50% wins) iterated on; the fewest-rounds winner is chosen; bans from config.yaml `deck_search`; report per run in `state/sim_runs/` |
| `simviz.py`, `sim.html` | the /sim page (reports list, stats, battle-board replays from `sim.replay`) |
| `combat/rollout.py` | boss/hard fights: each move played out in the simulator in worker processes (~3 s), the brain's move replaced when another is clearly better (`combat.rollouts`) |
| `gear.py` | new backpack item: tries just it against what is worn; level-up: retries items that could not be worn before. `state/gear.json` remembers items already beaten (never retried) |
| `potions.py` | out of potions (`upkeep.buy_potions`): Hilda Brewer in the Commons, Fill All, Buy, Recall back (window names from Deimos) |
| `walkmap.py`, `geo/` | collision-aware teleports from the zone's collision.bcd (vendored from Deimos): every landing snapped to walkable ground; refused jumps step back; zone.nav squares for scouting a zone from under the map (collect) |
| `petdance.py` | `pet` trips: the dance-game moves hook (from Deimos, by peechez; GPL-3.0), the Pet Pavilion route, playing, feeding, rewards |
| `trainer.py` | trips to the school professor (Go Home, dorm door, school door) at `progression.train_levels`; trains new spells |
| `bossfarm.py`, `dungeons.py` | `mode: boss` (`boss_farm.boss`, `until_item`, `max_runs`): repeat a learned dungeon boss |
| `deck.py`, `deck_plan.py`, `progression.py` | spellbook reading, deck planning, level-ups, trainer |
| `ui.py` | UI window paths and helpers |
| `names.py` | cached display-name lookups (WizWalker's are very slow) |
| `gamerestart.py` | frozen/crashed game: the bot's request, the supervisor closes and starts the game and logs in (Credential Manager login) |
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
