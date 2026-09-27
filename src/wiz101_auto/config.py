"""Bot configuration, loaded from YAML (see config.example.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .combat.brain import Strategy
from .deck_plan import DeckPolicy


@dataclass
class QuestConfig:
    enabled: bool = True
    teleport: bool = True  # teleport to objectives; False = walk (slower, lower detection risk)
    accept_side_quests: bool = False
    photomancy: bool = True
    # Abort questing if the objective hasn't changed for this long.
    stuck_minutes: float = 12.0
    # Flee fights the tracked quest doesn't need (never bosses or fights indoors).
    flee_unneeded_fights: bool = True


@dataclass
class UpkeepConfig:
    use_potions: bool = True
    potion_health_ratio: float = 0.5
    potion_mana_ratio: float = 0.2
    collect_wisps: bool = True
    wisp_health_ratio: float = 0.8  # grab nearby wisps below this after a fight
    # Before questing on: recover (potion, wisps, resting away from mobs)
    # whenever health is below min_health_to_fight, until rest_until_health.
    min_health_to_fight: float = 0.8
    rest_until_health: float = 0.95
    # Spells cost mana: at 0 every card is grayed out, so top mana up too.
    min_mana_to_fight: float = 0.5
    rest_until_mana: float = 0.85
    rest_max_minutes: float = 8.0  # give up and stop the bot if still too low after this
    wisp_safe_distance: float = 900.0  # skip wisps closer than this to a mob

    def needs_recovery(self, health_ratio: float, mana_ratio: float = 1.0) -> bool:
        return health_ratio < self.min_health_to_fight or mana_ratio < self.min_mana_to_fight

    def recovered(self, health_ratio: float, mana_ratio: float) -> bool:
        return health_ratio >= self.rest_until_health and mana_ratio >= self.rest_until_mana


@dataclass
class SafetyConfig:
    stop_key: str = "ctrl+shift+q"
    pause_key: str = "ctrl+shift+p"
    max_hours: float = 4.0
    max_deaths: int = 5
    mouseless: bool = True  # clicks through a memory hook so your real mouse stays free
    stall_seconds: float = 15.0  # nothing changes for this long -> escalating recovery (0 = off)
    battle_stall_seconds: float = 120.0


@dataclass
class CombatConfig:
    strategy: Strategy = field(default_factory=Strategy)
    max_discards: int = 2
    flee_below: float = 0.0  # 0 disables fleeing


@dataclass
class ProgressionConfig:
    enabled: bool = True
    school: str = ""  # blank = read from the game
    rebuild_on_start: bool = True
    check_minutes: float = 30.0  # re-read the spellbook this often (0 = only on events)
    auto_train: bool = True  # experimental: train spells when the trainer window opens
    remind_to_train: bool = True
    deck: DeckPolicy = field(default_factory=DeckPolicy)


@dataclass
class Config:
    mode: str = "quest"  # quest | fight | farm
    farm_seconds_between_fights: float = 2.0
    log_file: str = "wiz101-auto.log"
    quest: QuestConfig = field(default_factory=QuestConfig)
    upkeep: UpkeepConfig = field(default_factory=UpkeepConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    combat: CombatConfig = field(default_factory=CombatConfig)
    progression: ProgressionConfig = field(default_factory=ProgressionConfig)


def _merge(obj: Any, data: dict[str, Any], path: str = "") -> Any:
    known = {f.name: f for f in fields(obj)}
    for key, value in (data or {}).items():
        if key not in known:
            raise ValueError(f"unknown config key: {path}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{path}{key} must be a mapping")
            _merge(current, value, f"{path}{key}.")
        else:
            if isinstance(current, float) and isinstance(value, int):
                value = float(value)
            if current is not None and not isinstance(value, type(current)):
                raise ValueError(f"{path}{key} should be {type(current).__name__}, got {value!r}")
            setattr(obj, key, value)
    return obj


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path:
        p = Path(path)
        if p.exists():
            _merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        else:
            raise FileNotFoundError(p)
    from .safety import parse_hotkey

    parse_hotkey(cfg.safety.stop_key)  # fail early on a typo, not mid-session
    parse_hotkey(cfg.safety.pause_key)
    if cfg.mode not in ("quest", "fight", "farm"):
        raise ValueError(f"mode must be quest, fight or farm, not {cfg.mode!r}")
    return cfg
