# Boss cheats (the player's notes) and how the bot plays them

Each boss's rule lives in `src/wiz101_auto/combat/brain.py` (with a test).
Add new bosses here and to the brain.

| Boss | Cheat | The bot's rule |
|---|---|---|
| Luska Charmbeak (Waterworks) | Answers single-target spells from un-inked wizards (1,200-2,100) | `NO_SINGLE_TARGET`: no single-target spells at him; hit-alls only |
| Sylster Glowstorm (Waterworks) | Doom and Gloom cycles: light (traps/prisms) / dark (blades) | `_sylster_rules` / `sylster_cycle` |
| Belloq (Zafaria, Waterfront) | Ra (1,200+, rising) at the start of any round after one he wasn't hit; minions triage DoTs off him; Frost Giant stuns | `HIT_EVERY_ROUND`: the cheapest hit that reaches him every round, no DoT while minions live |
| Nergal, the Burned Lion (Zafaria, Savannah) | Weakness on round 1 and every 4th; dispels the school of any hit that leaves him alive | `ONE_SHOT_ONLY`: no hit unless it kills him outright; blades/traps until then |
| Shaka Zebu (Zafaria, Zamunda) | Satyr to full whenever hurt while his minion lives; minion killed: Meteor (500) every 3 rounds | `ONE_SHOT_ONLY` + `MINION_LAST`: one-shot him from full, his minion never targeted while he lives |
| Spectral Elephant (Mirror Lake, after Tse-Tse Snaketail) | -90% Tower Shield, replaced every hit, until the Gorilla, Lion and Rhino are dead; guardians pierce/steal blades and traps | `SHIELDED_UNTIL_OTHERS_DIE`: single hits go to the others first (a Shatter, if packed, then the Elephant) |
| Tim-tim Snakeeye (Zafaria, Black Palace: 'Source of Corruption') | Keeps a -90% shield on himself (back right after it's broken); hits ~1,550 | Seen by the bot (2026-10-05): solo, a big hit after the shield breaks lands on a fresh one. 5 losses: the Black Palace goes to team play (one breaks, another hits) |
| Four Elephant Goliaths (Mirror Lake, final) | Turn order shuffled every 3rd round; Storm Lord / Leviathan stun chains and blade wipes | `_stun_block_first`: Stun Block / Conviction in hand goes up first; then blades and Orthrus |

The one-shot rule gives up after round 12 (`ONE_SHOT_GIVE_UP_ROUND`) so a
fight can't stall forever. Mirror Lake will likely need other players (the
team list takes any main-quest dungeon after 5 losses).

## Belloq (Under the Big Tent)

- **Ra**: unless at least one player damaged him the round before, he interrupts
  with Ra (1,200+, rising each round). Hit him every round (wands, cheap hits).
  A balance dispel works too, but going first it takes two a round.
- **Triage**: with damage-over-time on him, all his minions may triage it off
  at the start of a round (random). No DoTs while they live (or dispel/kill
  them, or spam DoTs).
- Tips: potion up, mark, team up. Stun shields/conviction for whoever hits
  Belloq. Kill the minions first (Lord of Winter, Frost Giant stuns, triage).
  The hitter in the last spot (feint the same round). A tank triggers the
  fight alone for one round: only one minion joins.
- The bot lost to him 5 times solo: his tent is on the team list
  (`state/team_dungeons.json`); it waits at the sigil and Teams Up.
