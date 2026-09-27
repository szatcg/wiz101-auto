"""`wiz101-auto inspect`: dump what the bot can see, for debugging and bug reports."""

from __future__ import annotations

from loguru import logger
from wizwalker.combat import CombatHandler

from . import ui
from .bot import connect, new_handler
from .combat.brain import Strategy, decide
from .combat.reader import read_battle


async def inspect(show_windows: bool = False, show_battle: bool = True):
    handler = new_handler()
    try:
        client = await connect(handler)
        stats = client.stats
        lines = [
            f"zone:        {await client.zone_name()}",
            f"level:       {await stats.reference_level()}",
            f"health:      {await stats.current_hitpoints()}/{await stats.max_hitpoints()}",
            f"mana:        {await stats.current_mana()}/{await stats.max_mana()}",
            f"gold:        {await stats.current_gold()}",
            f"potions:     {await stats.potion_charge():.2f}",
            f"position:    {await client.body.position()}",
            f"quest pos:   {await client.quest_position.position()}",
            f"objective:   {await ui.text_at(client, ui.QUEST_GOAL_TEXT)!r}",
            f"in battle:   {await client.in_battle()}",
            f"loading:     {await client.is_loading()}",
            f"dialogue:    {await ui.is_visible(client, ui.ADVANCE_DIALOG)}",
            f"npc prompt:  {await ui.text_at(client, ui.NPC_RANGE_TEXT)!r}",
        ]
        print("\n".join(lines))

        if show_battle and await client.in_battle():
            snap = await read_battle(CombatHandler(client))
            b = snap.battle
            print(f"\n-- battle round {b.round}, pips {b.pips} + {b.power_pips} power --")
            for c in [b.me, *b.allies, *b.enemies]:
                tag = "ME" if c.is_client else ("ENEMY" if c.is_enemy else "ally")
                print(
                    f"  [{tag}] {c.name}: {c.health}/{c.max_health} boss={c.is_boss} "
                    f"blades={c.blade_count} traps={c.trap_count} shields={c.shield_count}"
                )
            for card in b.cards:
                effects = ", ".join(f"{e.kind.name}:{e.target.name}:{e.value:g}" for e in card.effects)
                print(
                    f"  card {card.index}: {card.name} [{card.school}] {card.pip_cost}p "
                    f"acc={card.accuracy} castable={card.castable} -> {effects}"
                )
            print(f"  decision: {decide(b, Strategy()).describe()}")

        if show_windows:
            print("\n-- visible window tree --")
            print("\n".join(await ui.dump_tree(client.root_window, max_depth=8)))
    except Exception as exc:
        logger.opt(exception=exc).error("inspect failed")
    finally:
        await handler.close()
