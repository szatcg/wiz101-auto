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
| Tim-tim Snakeeye (Zafaria, Black Palace: 'Source of Corruption'; storm, 80% storm resist, +35% myth/life taken) | Interrupt -90% Tower Shield at the start and after every hit he takes; a heal makes him drop it for one round | `_heal_trick`: while shielded no hits into it (setup instead), a heal once set up; the round he's bare, the biggest hit. Pierce/Shatter cards would do too |
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

## Avalon (the player's list, 2026-10-06)

| Boss | Where | Cheats | The bot's rule |
|---|---|---|---|
| Matkis Axethief (Fire, 11,300) | Abbey Road, side quest "Coda" | Reshuffle on everyone round 1 and at random; a fizzle gets a Power Link; a single-target trap or prism on him wipes all his traps and prisms (mass traps don't) | `BOSS_BANS`: no single-target trap or prism on him |
| Black Annie | Dolores Tower | Dark Wind global round 1 (recast if replaced); a single-target spell of 3 pips or less gets a 0-pip Vampire that stuns | `BOSS_BANS`: no single-target spell costing 3 pips or less (no globals either) |
| Flevur Flave (Death, 13,250) | Castle Courtyard / Mysterious Well | Gnome! on the first player round 1; steals any single blade or shield | `BOSS_BANS`: no blades, no shields |
| Ridenhouer Delish (Ice, 12,300) | Mysterious Well | steals absorbs/shields, reacts to bubbles and rebirths | `BOSS_BANS`: no shields, no globals |
| Jabberwock (Fire, 25,000) | The Wild (end of the main line) | Meteor on everyone every 3rd round, then a +200% trap on himself; wipes traps at times | hits wait for his +200% trap (read from his effects like any trap) |
| Young Morganthe (Death, 15,400) | Ghost Avalon | Woolly Mammoth round 1; a shield or trap gets Power Nova + Earthquake; 8+ pips gets Mana Burn | `BOSS_BANS`: no traps, no shields; `PIP_CAP`: never sit on 8+ pips |
| The Pendragon (Death, ~16,000) | Keep of Ganelon (final boss) | Entangle on everyone before round 1; Fire Dragon DoT round 1; a single-target spell gets Scarecrow | `NO_SINGLE_TARGET`: hit-alls only, like Luska |

## Lamia (Aquila, Atlantea: House of Lost Sailors; Death, rank 11) — the player, 2026-10-06

- Round 1: a +35% Death global bubble ("You Are Not The Master Down Here!").
- While her bubble stands: every 4th round a 0-pip Scarecrow (~620-650 Death to all, heals her).
- Replacing her bubble: she puts hers straight back, but trying once (any global, round 1) stops the Scarecrow cheat for the rest of the fight (until round 30).
- The bot's decks have no global spell, so it can't break the cheat: fight her with a team or with a global in the deck.
