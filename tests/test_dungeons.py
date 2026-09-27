from wiz101_auto.dungeons import DungeonEntry, DungeonMemory


def test_learns_where_a_boss_lives(tmp_path):
    mem = DungeonMemory(tmp_path / "d.json")
    theater = "WizardCity/WC_Streets/Interiors/WC_Firecat_Theatre"
    mem.record_entry(theater, DungeonEntry("WizardCity/WC_Streets/WC_Firecat", (1, 2, 3), (4, 5, 6), 1.5))
    assert mem.record_boss("Alicane Swiftarrow", theater)
    assert not mem.record_boss("Alicane Swiftarrow", theater)
    again = DungeonMemory.load(tmp_path / "d.json")
    interior, entry = again.find_for_boss("alicane swiftarrow")
    assert interior == theater and entry.sigil == (1, 2, 3) and entry.yaw == 1.5
    assert again.find_for_boss("Rattlebones") is None


def test_boss_mode_needs_a_boss_name(tmp_path):
    import pytest

    from wiz101_auto.config import load_config

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("mode: boss\n")
    with pytest.raises(ValueError):
        load_config(cfg_file)
    cfg_file.write_text("mode: boss\nboss_farm:\n  boss: Alicane Swiftarrow\n  until_item: Humongofrog\n")
    assert load_config(cfg_file).boss_farm.until_item == "Humongofrog"
