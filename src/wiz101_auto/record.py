"""`wiz101-auto record`: watch the player walk a route and write down what the
bot needs to repeat it: every zone change (the last spot before it = the door,
the first spot after it = the arrival point and facing), every NPC whose talk
prompt shows (name and position), and the spell trainer's window layout when it
opens. Nothing is clicked or pressed.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from . import ui
from .progression import TRAINER

STATE_DIR = Path("state")


async def record(client, seconds: float = 900.0) -> Path:
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"route_record_{int(time.time())}.txt"
    lines: list[str] = []

    def note(text: str):
        stamp = time.strftime("%H:%M:%S")
        lines.append(f"{stamp} {text}")
        logger.info(text)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    zone = await client.zone_name()
    pos = await client.body.position()
    note(f"start in {zone} at ({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f})")
    last_pos, last_npc, trainer_saved = pos, "", False
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await asyncio.sleep(0.3)
        try:
            if await client.is_loading():
                continue
            now_zone = await client.zone_name()
            pos = await client.body.position()
            if now_zone != zone:
                yaw = await client.body.yaw()
                note(
                    f"zone {zone} -> {now_zone}: door near ({last_pos.x:.0f}, {last_pos.y:.0f}, "
                    f"{last_pos.z:.0f}); arrived at ({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f}) yaw {yaw:.3f}"
                )
                zone = now_zone
            last_pos = pos
            if await ui.is_visible(client, ui.NPC_RANGE):
                npc = (await ui.text_at(client, ["WorldView", "NPCRangeWin", "wndTitleBackground",
                                                 "NPCRangeTxtTitle"])).strip()
                if npc and npc != last_npc:
                    note(f"npc {npc!r} in {zone} at ({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f})")
                last_npc = npc
            else:
                last_npc = ""
            if not trainer_saved and await ui.is_visible(client, TRAINER):
                gui = await ui.window_at(client, TRAINER)
                dump = STATE_DIR / f"trainer_window_{int(time.time())}.txt"
                tree = await ui.dump_tree(gui, max_depth=12, only_visible=False, with_types=True)
                dump.write_text("\n".join(tree), encoding="utf-8", errors="replace")
                note(f"trainer window open; layout saved to {dump}")
                trainer_saved = True
        except Exception as exc:
            logger.debug(f"record: {exc!r}")
    note("done")
    return path
