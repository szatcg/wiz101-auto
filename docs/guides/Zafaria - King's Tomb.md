# Zafaria — King's Tomb (Zamunda)

The player's notes (2026-10-05), for 'Into the Zebra Tomb' / 'Tomb of the Zebra Kings'.

1. Defeat 2 Iklaw Ghost Warriors (rank 10, 1,915 health) on entry.
2. Explore further inside the tomb.
3. Defeat the minions (Horned Monkey Spiders), then the ghost boss, Zanga Zebu.

## Known game bugs

- **Zanga Zebu doesn't spawn**: after the spiders the boss can be invisible or
  never load. Leave and re-enter (or redo) the dungeon until he does. The bot
  does this by itself: after 6 tries at a boss that start nothing, or an
  objective in a dungeon stalled, it leaves and enters a fresh copy
  (`_reenter_for_npc`).
- **Instance reset**: teleporting out or losing a fight may reset the copy.
  The bot's heal trips out and back in (dungeon-return button) are when it
  broke here.
