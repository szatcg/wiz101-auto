"""`wiz101-auto explore`: record the current zone's entities (NPCs, doors, mobs)
and their positions. Used to build routes, e.g. to your school professor.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from loguru import logger

from . import ui
from .bot import connect, new_handler


async def explore(out_dir: str = "state") -> Path | None:
    handler = new_handler()
    try:
        client = await connect(handler)
        zone = await client.zone_name() or "unknown"
        me = await client.body.position()
        rows = []
        for entity in await client.get_base_entity_list():
            try:
                template = await entity.object_template()
                name = await template.object_name() if template else "?"
                display = ""
                try:
                    code = await template.display_name()
                    display = await client.cache_handler.get_langcode_name(code) if code else ""
                except Exception:
                    pass
                pos = await entity.location()
                dist = math.dist((me.x, me.y, me.z), (pos.x, pos.y, pos.z))
                rows.append((dist, name, display, pos))
            except Exception:
                continue
        rows.sort(key=lambda r: r[0])

        lines = [
            f"zone: {zone}",
            f"me: {me}",
            f"quest marker: {await client.quest_position.position()}",
            f"objective: {await ui.text_at(client, ui.QUEST_GOAL_TEXT)!r}",
            f"npc prompt: {await ui.text_at(client, ui.NPC_RANGE_TEXT)!r}",
            "",
            f"{'dist':>7}  {'object name':<45} {'display name':<30} position",
        ]
        for dist, name, display, pos in rows:
            where = f"({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f})"
            lines.append(f"{dist:7.0f}  {name[:45]:<45} {display[:30]:<30} {where}")

        Path(out_dir).mkdir(exist_ok=True)
        safe_zone = zone.replace("/", "_")
        path = Path(out_dir) / f"explore_{safe_zone}_{int(time.time())}.txt"
        path.write_text("\n".join(lines))
        print("\n".join(lines[:40]))
        print(f"\n... {len(rows)} entities saved to {path}")
        return path
    except Exception as exc:
        logger.opt(exception=exc).error("explore failed")
        return None
    finally:
        await handler.close()
