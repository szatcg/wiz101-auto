"""Farming a group dungeon: run it again and again with whatever team forms.

`state/farm.json` says which dungeon (its first room's zone id), which boss
ends a run, whether farming is on, and how many runs are done. The bot goes
to the dungeon's sigil, waits for a team (teamup.py), follows it through the
dungeon joining its fights, opens the final boss's chest, counts the run and
leaves by the world hub button for the next one. `farm` / `farm --stop` on the
command line turn it on and off; the run count shows on the overlay.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

FARM_FILE = Path("state") / "farm.json"


@dataclass
class Farm:
    dungeon: str = "Aquila/AQ_Z01_MountOlympus"
    name: str = "Mount Olympus"
    final_boss: str = "Zeus Sky Father"
    active: bool = False
    runs: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> Farm:
        try:
            data = json.loads((path or FARM_FILE).read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self, path: Path | None = None):
        path = path or FARM_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    def record_run(self, path: Path | None = None) -> int:
        self.runs += 1
        self.save(path)
        logger.success(f"{self.name} run {self.runs} done")
        return self.runs

    def ends_run(self, boss_names: list[str]) -> bool:
        """The fight just won had the boss that ends a run."""
        return any(self.final_boss.lower() == n.lower() for n in boss_names)
