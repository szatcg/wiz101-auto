"""Fast display-name lookups.

WizWalker's cache_handler.get_langcode_name() lists the game's Root.wad and
re-reads + JSON-parses its whole (multi-megabyte) langmap cache file on every
call. Called per entity or per card that hammers the disk and CPU hard enough
to stall the whole PC, so we load the map once and keep it in memory.
"""

from __future__ import annotations

from loguru import logger

_maps: dict[str, dict[str, str]] | None = None
_loaded_files: set[str] = set()
_failed_files: set[str] = set()


async def lang_name(client, langcode: str) -> str:
    """'Spells_00123' -> 'Blood Bat'. Empty string if unknown."""
    global _maps
    if not langcode or "_" not in langcode:
        return ""
    file, code = langcode.split("_", 1)
    if file in _failed_files:
        return ""
    if file not in _loaded_files:
        try:
            # First use of this lang file: let WizWalker extract it to its
            # on-disk cache once, then load the map into memory once.
            await client.cache_handler.get_langcode_name(langcode)
        except Exception:
            pass
        try:
            _maps = await client.cache_handler.get_langcode_map()
        except Exception as exc:
            logger.debug(f"lang map unavailable: {exc}")
            _maps = _maps or {}
        if file not in (_maps or {}):
            _failed_files.add(file)
            return ""
        _loaded_files.add(file)
    return (_maps or {}).get(file, {}).get(code, "")
