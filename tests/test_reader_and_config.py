from pathlib import Path

import pytest

from wiz101_auto.combat.model import EffectKind, Target
from wiz101_auto.combat.reader import average_effects, map_effect
from wiz101_auto.config import load_config


@pytest.mark.parametrize(
    "effect,target,param,kind,tgt",
    [
        ("damage", "enemy_single", 100, EffectKind.DAMAGE, Target.ENEMY_SINGLE),
        ("damage", "enemy_team", 90, EffectKind.DAMAGE, Target.ENEMY_ALL),
        ("steal_health", "enemy_single", 80, EffectKind.STEAL, Target.ENEMY_SINGLE),
        ("heal", "self", 400, EffectKind.HEAL, Target.SELF),
        ("heal_over_time", "friendly_team", 300, EffectKind.HOT, Target.ALLY_ALL),
        ("modify_outgoing_damage", "self", 35, EffectKind.BLADE, Target.SELF),
        ("modify_outgoing_damage", "friendly_single", 35, EffectKind.BLADE, Target.ALLY_SINGLE),
        ("modify_outgoing_damage", "enemy_single", -25, EffectKind.WEAKNESS, Target.ENEMY_SINGLE),
        ("modify_incoming_damage", "enemy_single", 30, EffectKind.TRAP, Target.ENEMY_SINGLE),
        ("modify_incoming_damage", "self", -50, EffectKind.SHIELD, Target.SELF),
        ("modify_card_damage", "spell", 100, EffectKind.ENCHANT_DAMAGE, Target.SPELL),
        ("modify_card_accuracy", "spell", 10, EffectKind.ENCHANT_ACCURACY, Target.SPELL),
        ("stun", "enemy_single", 1, EffectKind.STUN, Target.ENEMY_SINGLE),
        ("reshuffle", "self", 0, EffectKind.OTHER, Target.SELF),
    ],
)
def test_map_effect(effect, target, param, kind, tgt):
    e = map_effect(effect, target, param)
    assert e.kind is kind and e.target is tgt


def test_average_effects_for_random_spells():
    groups = [[map_effect("damage", "enemy_single", v)] for v in (80, 100, 120)]
    (avg,) = average_effects(groups)
    assert avg.value == 100 and avg.kind is EffectKind.DAMAGE


def test_example_config_loads():
    cfg = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert cfg.mode == "quest"
    assert 0 < cfg.combat.strategy.heal_threshold < 1


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("combat:\n  strategy:\n    heal_treshold: 0.5\n")
    with pytest.raises(ValueError, match="heal_treshold"):
        load_config(p)


def test_wrong_type_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("quest:\n  teleport: maybe\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_myth_preset_loads():
    cfg = load_config(Path(__file__).parent.parent / "configs" / "myth.yaml")
    assert cfg.progression.school == "Myth"


@pytest.mark.parametrize(
    "text,codes",
    [
        ("ctrl+shift+q", (0x11, 0x10, ord("Q"))),
        ("Ctrl + ]", (0x11, 0xDD)),
        ("F9", (0x78,)),
        ("alt+1", (0x12, ord("1"))),
        ("ctrl+backtick", (0x11, 0xC0)),
    ],
)
def test_parse_hotkey(text, codes):
    from wiz101_auto.safety import parse_hotkey

    assert parse_hotkey(text) == codes


@pytest.mark.parametrize("bad", ["", "ctrl+", "ctrl+shift", "hyper+q", "ctrl+nope"])
def test_parse_hotkey_rejects(bad):
    from wiz101_auto.safety import parse_hotkey

    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_bad_hotkey_in_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("safety:\n  stop_key: ctrl+nope\n")
    with pytest.raises(ValueError, match="nope"):
        load_config(p)


def test_npc_menu_ranking_prefers_objective_words():
    from wiz101_auto.npc import rank_options

    labels = ["Unicorn Way Bounty", "Sergeant Muldoon: Olde Town", "Shop"]
    assert rank_options(labels, "Talk to Sergeant Muldoon in Olde Town")[0] == 1
    assert rank_options(["A", "B"], "whatever") == [0, 1]  # stable when nothing matches
