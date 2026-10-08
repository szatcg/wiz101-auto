"""Fishing ("Catch Vonda Fish in Pitch Black Lake (1 of 4)").

Read-only so far: at a fishing objective's marker the bot saves what the game
shows (the fish in the water, from WizWalker's fishing manager, and the UI
window tree) to state/fishing_probe_*.txt, to learn the fishing window and how
quest fish are told apart. No memory is ever written (the player: no edits,
for fear of a ban): fishing will be played with the game's own buttons.
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from . import ui

PROBE_DIR = Path("state")
PROBE_EVERY = 60.0  # seconds between probes while the objective waits


async def fish_report(client) -> list[str]:
    """One line per fish the fishing manager lists: position, school, rank,
    size, status, chest/sentinel."""
    lines: list[str] = []
    try:
        manager = await client.game_client.fishing_manager()
        fish = await manager.fish_list()
    except Exception as exc:
        return [f"fishing manager unreadable: {exc!r}"]
    for f in fish:
        try:
            body = await f.body()
            pos = await body.position()
            t = await f.template()
            size = f"{await f.size():.2f} ({await t.size_min():.2f}-{await t.size_max():.2f})"
            kinds = f"chest {await f.is_chest()} sentinel {await t.is_sentinel()}"
            lines.append(
                f"fish {await f.fish_id()} template {await f.template_id()} school {await t.school_name()!r} "
                f"rank {await t.rank()} size {size} status {(await f.status_code()).name} {kinds} "
                f"at ({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f})")
        except Exception as exc:
            lines.append(f"fish unreadable: {exc!r}")
    try:
        bob = await manager._bobber_pos()
        lines.append(f"bobber at ({bob.x:.0f}, {bob.y:.0f}, {bob.z:.0f})")
    except Exception:
        pass
    return lines


async def probe(client, objective: str, zone: str) -> Path:
    """Save the fish list and the visible UI tree for a fishing objective."""
    out = [f"objective: {objective}", f"zone: {zone}"]
    try:
        me = await client.body.position()
        out.append(f"me at ({me.x:.0f}, {me.y:.0f}, {me.z:.0f})")
    except Exception:
        pass
    try:
        out.append(f"fishing level {await client.stats.fishing_level()}")
    except Exception:
        pass
    out += await fish_report(client)
    try:
        out.append("--- UI ---")
        out += await ui.dump_tree(client.root_window, max_depth=10)
    except Exception as exc:
        out.append(f"ui dump failed: {exc!r}")
    path = PROBE_DIR / f"fishing_probe_{int(time.time())}.txt"
    path.write_text("\n".join(str(x) for x in out), encoding="utf-8", errors="replace")
    fish = sum(1 for x in out if str(x).startswith("fish "))
    logger.info(f"fishing: {fish} fish in view; saved {path.name} (no fishing yet: being learned)")
    return path
