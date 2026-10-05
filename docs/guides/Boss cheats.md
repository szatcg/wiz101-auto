# Boss cheats (the player's notes) and how the bot plays them

Each boss's rule lives in `src/wiz101_auto/combat/brain.py` (with a test).
Add new bosses here and to the brain.

| Boss | Cheat | The bot's rule |
|---|---|---|
| Luska Charmbeak (Waterworks) | Answers single-target spells from un-inked wizards (1,200-2,100) | `NO_SINGLE_TARGET`: no single-target spells at him; hit-alls only |
| Sylster Glowstorm (Waterworks) | Doom and Gloom cycles: light (traps/prisms) / dark (blades) | `_sylster_rules` / `sylster_cycle` |
| Belloq (Zafaria, Waterfront) | Ra (1,200+, rising) at the start of any round after one he wasn't hit; minions triage DoTs off him; Frost Giant stuns | `HIT_EVERY_ROUND`: the cheapest hit that reaches him every round, no DoT while minions live |

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
