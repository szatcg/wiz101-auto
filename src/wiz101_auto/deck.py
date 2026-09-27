"""Read the spellbook and rebuild the deck (game side of deck_plan.py)."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker.extensions.scripting.deck_builder import DeckBuilder

from .combat.model import Card
from .combat.reader import read_effects
from .deck_plan import DeckPlan, DeckPolicy, SpellInfo, plan_deck


async def _spell_info(entry) -> SpellInfo | None:
    try:
        gspell = await entry.graphical_spell()
        if not gspell:
            return None
        template = await gspell.spell_template()
        if not template:
            return None
        name = await template.name()
        if not name:
            return None
        effects = []
        for eff in await gspell.spell_effects():
            effects.extend(await read_effects(eff))
        pip_cost = 0
        rank = await gspell.pip_cost()
        if rank is not None:
            pip_cost = await rank.spell_rank()
        school = ""
        try:
            school = await template.magic_school_name()
        except Exception:
            pass
        card = Card(
            index=0,
            name=name,
            school=school,
            pip_cost=pip_cost,
            accuracy=await gspell.accuracy(),
            effects=effects,
        )
        return SpellInfo(card=card, max_copies=await entry.max_copies())
    except Exception as exc:
        logger.debug(f"unreadable spellbook entry: {exc}")
        return None


async def read_known_spells(builder: DeckBuilder) -> list[SpellInfo]:
    spells = []
    for entry in await builder.get_spell_list():
        info = await _spell_info(entry)
        if info:
            spells.append(info)
    return spells


async def current_school(client) -> str:
    """The wizard's primary school name, e.g. 'Myth', from its stats."""
    from wizwalker.memory.memory_objects.enums import MagicSchool

    try:
        return MagicSchool(await client.stats.school_id()).name.capitalize()
    except Exception:
        return ""


async def rebuild_deck(
    client, school: str, policy: DeckPolicy, *, dry_run: bool = False
) -> tuple[list[SpellInfo], DeckPlan]:
    """Open the spellbook, plan the deck from known spells and apply it."""
    async with DeckBuilder(client) as builder:
        known = await read_known_spells(builder)
        plan = plan_deck(known, school, policy)
        logger.info(f"known spells: {', '.join(s.name for s in known) or '(none)'}")
        logger.info(f"deck plan: {plan.describe()}")
        if dry_run or not plan.steps:
            return known, plan

        await builder.clear_deck()
        await asyncio.sleep(0.5)
        for name, copies in plan.steps:
            try:
                await builder.add_by_name(name, copies)
            except Exception as exc:  # max copies, deck full, or UI hiccup
                logger.debug(f"add {name} x{copies} stopped: {exc}")
            await asyncio.sleep(0.3)
        logger.success("deck rebuilt")
        return known, plan
