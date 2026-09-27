# wiz101-auto

An autonomous Wizard101 bot. It reads the game's memory through
[WizWalker](https://github.com/StarrFox/wizwalker), follows the quest arrow,
talks to NPCs, goes through doors and dungeons, and plays battles with its own
card-evaluation logic.

> **Warning.** Automation breaks KingsIsle's Terms of Use and accounts do get
> banned. Use a throwaway account, don't run it unattended for long, and
> accept the risk yourself.

## What it does

| Area | Behaviour |
|---|---|
| **Questing** | Reads the objective text and the quest marker position, teleports there (walks if the server bounces the teleport), presses X on NPCs, doors, sigils and objects, confirms dungeon entry, uses world gates, closes shops and training menus it opens, and pulls the nearest mob for "Defeat…" objectives. |
| **Combat** | Reads every card in hand (damage, target, pips, accuracy, enchants), every combatant (health, blades, traps, shields, boss flag) and your pips. Each step it heals when low, enchants its attack, stacks blades or traps against bosses, picks the spell and target that removes the most enemy health (kills weighted heavily), sets up while waiting for pips, and discards dead cards. |
| **Upkeep** | Advances dialogue, declines side quests (configurable), drinks potions, picks up health and mana wisps after fights, and retries areas that haven't downloaded. |
| **Safety** | **F9** stops the bot and **F10** pauses or resumes it. It also stops after a maximum run time, too many deaths, or no quest progress for N minutes. |

Modes: `quest` (default), `fight` (you walk, it fights) and `farm` (fights
the nearest mob over and over).

## Setup (Windows)

1. Install **Python 3.13+** from python.org and tick "Add python.exe to PATH".
   Also install **Git for Windows**.
2. Clone and install:
   ```bat
   git clone https://github.com/szatcg/wiz101-auto.git
   cd wiz101-auto
   py -3.13 -m venv .venv
   .venv\Scripts\activate
   pip install -e .
   copy config.example.yaml config.yaml
   ```
   Or just run `setup.bat`, which does the same thing.
3. Start Wizard101, log in, and load into the world with your wizard.
4. Check that the bot can read the game:
   ```bat
   wiz101-auto inspect
   ```
   You should see your zone, health, quest objective and marker position.
   Start a fight and run it again to see the cards it reads and the move
   it would make.
5. Run it:
   ```bat
   wiz101-auto run -c config.yaml
   ```
   (or `run.bat`). Press **F9** to stop.

If hooks fail to activate, run the terminal **as Administrator** and move
your wizard one step (some hooks only fire on movement).

## Starting a brand-new wizard

The bot starts once your wizard is standing in the world, so do these by hand:

1. Create the character (the school quiz and appearance).
2. Optional, but a good idea: play the short opening tutorial. It's scripted
   and occasionally asks for specific clicks. If the bot gets stuck there,
   finish that part yourself and restart it.
3. From Wizard City onward, run `wiz101-auto run`.

**Spells and deck:** the bot fights with whatever is in your deck. When you
level up, visit your school's professor, train the new spells, and put them
in your deck. Automatic training and deck building are on the roadmap. A
good early deck is 2-3 copies of each attack spell plus a heal.

**Progress limits:** Wizard City is free to play. Areas after it need a
membership or crown-purchased zones. Without either, the quest line
eventually hits a locked area and the bot stops with "no quest progress".

## Configuration

All options are documented in `config.example.yaml`. The most useful ones:

- `quest.teleport: false`: walk instead of teleporting. It's slower but looks
  less bot-like.
- `combat.strategy.heal_threshold`: heal below this share of your health.
- `combat.flee_below`: flee when health drops below this share.
- `safety.max_hours`, `safety.max_deaths`: hard limits on a session.

## Reporting problems

Logs go to `wiz101-auto.log` (at DEBUG level). When something goes wrong, the
most useful things to send are:

- the last ~100 lines of the log,
- the output of `wiz101-auto inspect` taken at the moment the bot is stuck,
- for UI problems, `wiz101-auto inspect --windows` (the live window tree).

Game patches can move memory offsets or rename UI windows. Offsets are handled
by updating WizWalker (`pip install -e . --upgrade --force-reinstall`). UI
window paths live in `src/wiz101_auto/ui.py`.

## Layout

```
src/wiz101_auto/
  cli.py            wiz101-auto run | inspect
  bot.py            connects to the client and runs the concurrent loops
  quest.py          quest-arrow following, travel, interaction
  upkeep.py         dialogue, potions, wisps, popups
  safety.py         F9/F10 keys, run limits, death counter
  ui.py             UI window paths and helpers
  combat/
    model.py        pure data model (cards, combatants, actions)
    brain.py        turn decision logic (unit tested)
    reader.py       WizWalker memory -> model
    fighter.py      executes decisions each round
tests/              run with `pytest` on any OS
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The combat brain and effect mapping are pure Python, so they can be tested on
Linux or macOS. Everything under `bot`, `quest`, `upkeep` and `fighter` needs
the Windows game client.

## Roadmap

- Automatic spell training on level-up and deck building (WizWalker ships a
  `DeckBuilder` helper)
- Smarter collision-aware teleporting (see Deimos' `collision_tp`)
- School-aware pip accounting, and handling of shadow magic and
  multi-target spells
- Buying potions when out, and selling or clearing a full backpack
- Pet training

## Credits and license

- [WizWalker](https://github.com/StarrFox/wizwalker) by StarrFox: memory hooks
  and game object model.
- [Deimos](https://github.com/Deimos-Wizard101/Deimos-Wizard101): maintained
  WizWalker and WizSprinter forks (used as dependencies) and much of the UI
  window-path mapping.

Both are GPL-3.0, so this project is licensed **GPL-3.0-or-later**.
Not affiliated with or endorsed by KingsIsle Entertainment.
