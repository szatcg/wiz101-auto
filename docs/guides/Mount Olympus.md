# Mount Olympus (Aquila)

Player-supplied walkthrough, in dungeon order. Zone ids are filled in as the
bot visits (it learns dungeons, bosses and entity spots in `state/`).

| # | Step | Where | What | Notes for the bot |
|---|---|---|---|---|
| 1 | Arrive & acknowledge | Garden of Hesperides | Talk to **Silenus**, then step through the gateway into the clouds | Silenus is also the final hand-in (step 10) |
| 2 | Token of Light | Sun Chamber | Defeat **Apollo Bright One** + his guardian | 1st metallic mark (quest item) |
| 3 | Token of Shadows | Moon Chamber | Defeat the **Crescent Moon Centaurs** | 2nd mark |
| 4 | Token of Sky | Sky Balcony | Defeat the **Praetorian Guards** (stone birds) | 3rd mark |
| 5 | The Discordant Riddle | — | Present the 3 marks to **Eris Golden Apple**; answer her 3-part riddle | Answers below |
| 6 | The Gated Watch | Hall of the Watchful Eye | Unlock the threshold; defeat the single giant sentinel | |
| 7 | The Blacksmith's Request | Courtyard + nearby paths | Talk to **Hephaestus Coppersmith**; secure his 4 **Bronze Eagles** (they wander) | Search the courtyard and paths; don't give up after one sweep |
| 8 | The War Sovereign | — | Defeat **Ares Savage Spear** | Drops the **Sky Iron Hasta** (farm target: wand) |
| 9 | The Summit | Throne Room | Defeat **Zeus Sky Father** + his bovine guardians | Zeus War Eagle set pieces (farm target) |
| 10 | The Inscription | Garden of Hesperides | Talk to **Silenus** again | Quest hand-in |

## Eris's riddle

Rock-paper-scissors between three creatures:

- the **pachyderm** (elephant) beats the **half-bull** (minotaur)
- the **half-bull** (minotaur) beats the **serpentine archer** (naga / snake archer)
- the **serpentine archer** beats the **pachyderm** (elephant)

So for each of her three questions, answer with the creature that beats the
one she names. The exact on-screen wording and UI is still to be mapped the
first time the bot reaches her (it saves unknown menus to `state/`).

## Farming

- Bosses to repeat: **Ares Savage Spear** (Sky Iron Hasta) and **Zeus Sky
  Father** (Zeus War Eagle set). Exact item names of every set piece are
  still needed for `boss_farm.until_item`.
- Group fights: the minion first, blades and a trap on each enemy, then
  Humongofrog (see `combat/brain.py`).
- Team Up: the TEAM UP! button sits on the dungeon sigil's prompt; its window
  isn't mapped yet.
