from wiz101_auto.wisps import WispMemory, sweep_points


def test_record_merges_nearby_sightings(tmp_path):
    mem = WispMemory(tmp_path / "w.json")
    assert mem.record("Z", [(0, 0, 0), (50, 0, 0), (1000, 0, 0)]) == 2
    assert mem.record("Z", [(10, 10, 0)]) == 0
    mem.save()
    assert WispMemory.load(tmp_path / "w.json").spots["Z"] == [(0, 0, 0), (1000, 0, 0)]


def test_next_spot_skips_mobs_and_recent_visits(tmp_path):
    mem = WispMemory(tmp_path / "w.json")
    mem.record("Z", [(0, 0, 0), (2000, 0, 0), (5000, 0, 0)])
    me = (0, 0, 0)
    mobs = [(100, 0, 0)]  # guards the closest spot
    assert mem.next_spot("Z", me, mobs, safe_distance=900, now=0) == (2000, 0, 0)
    mem.mark_visited("Z", (2000, 0, 0), now=0)
    assert mem.next_spot("Z", me, mobs, safe_distance=900, now=10) == (5000, 0, 0)
    assert mem.next_spot("Z", me, mobs, safe_distance=900, now=100) == (2000, 0, 0)  # cooldown over
    assert mem.next_spot("Other", me, [], safe_distance=900) is None


def test_sweep_points_avoid_mobs():
    pts = sweep_points((0, 0, 0), [(1500, 0, 0)], safe_distance=900)
    assert len(pts) == 15 and all(abs(p[0] - 1500) > 1 or abs(p[1]) > 1 for p in pts)


def test_best_wisp_zone_prefers_most_spots_in_the_same_world():
    from wiz101_auto.upkeep import best_wisp_zone

    spots = {
        "WizardCity/WC_Streets/WC_Cyclops": [(0, 0, 0)] * 46,
        "WizardCity/WC_Streets/WC_Triton": [(0, 0, 0)] * 3,
        "WizardCity/WC_Golem_Tower": [(0, 0, 0)],
        "Krokotopia/KT_Hub": [(0, 0, 0)] * 99,
    }
    assert best_wisp_zone("WizardCity/WC_Hub", spots) == "WizardCity/WC_Streets/WC_Cyclops"
    assert best_wisp_zone("WizardCity/WC_Streets/WC_Cyclops", spots) == "WizardCity/WC_Streets/WC_Triton"
    assert best_wisp_zone("WizardCity/WC_Hub", {}) == "WizardCity/WC_Streets/WC_Unicorn"
    assert best_wisp_zone("MooShu/MS_Hub", {}) is None
