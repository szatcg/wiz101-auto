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


def test_forget_drops_an_unreachable_spot(tmp_path):
    mem = WispMemory(tmp_path / "w.json")
    mem.record("Z", [(0, 0, 0), (1000, 0, 0)])
    assert mem.forget("Z", (10, 0, 0))
    assert mem.spots["Z"] == [(1000, 0, 0)]
    assert not mem.forget("Z", (5000, 0, 0))


def test_preferred_heal_zone_wins():
    from wiz101_auto.upkeep import best_wisp_zone

    spots = {"WizardCity/WC_Streets/WC_Cyclops": [(0, 0, 0)] * 46}
    unicorn = ["WizardCity/WC_Streets/WC_Unicorn"]
    assert best_wisp_zone("WizardCity/WC_Hub", spots, unicorn) == "WizardCity/WC_Streets/WC_Unicorn"
    here = "WizardCity/WC_Streets/WC_Unicorn"
    assert best_wisp_zone(here, spots, unicorn) == "WizardCity/WC_Streets/WC_Cyclops"


def test_spots_remember_their_wisp_kind(tmp_path):
    mem = WispMemory(tmp_path / "w.json")
    mem.record("Z", [(0, 0, 0)], "health")
    mem.record("Z", [(1000, 0, 0)], "mana")
    mem.record("Z", [(3000, 0, 0)])  # a stand-in of unknown kind
    assert mem.next_spot("Z", (0, 0, 0), [], safe_distance=900, need={"mana"}) == (1000, 0, 0)
    assert mem.count("Z", {"mana"}) == 2 and mem.count("Z", {"health"}) == 2
    assert mem.record("Z", [(3010, 0, 0)], "mana") == 1  # a sighting names the unknown one
    mem.save()
    loaded = WispMemory.load(tmp_path / "w.json")
    assert loaded.count("Z", {"health"}) == 1 and loaded.kind_of("Z", (1000, 0, 0)) == "mana"


def test_mana_only_recovery_goes_where_mana_wisps_are():
    from wiz101_auto.upkeep import best_wisp_zone

    unicorn = ["WizardCity/WC_Streets/WC_Unicorn"]
    spots = {"WizardCity/WC_Streets/WC_Triton": [(i * 500, 0, 0) for i in range(4)]}
    kinds = {"WizardCity/WC_Streets/WC_Triton": {(i * 500, 0, 0): "mana" for i in range(4)}}
    here = "WizardCity/WC_Hub"
    assert best_wisp_zone(here, spots, unicorn, need={"mana"}, kinds=kinds) == next(iter(spots))
    assert best_wisp_zone(here, spots, unicorn, need={"health"}, kinds=kinds) == unicorn[0]


def test_close_enough_to_fight_without_wisps():
    from wiz101_auto.config import UpkeepConfig
    from wiz101_auto.upkeep import close_enough

    cfg = UpkeepConfig(min_health_to_fight=0.85, min_mana_to_fight=0.5)
    assert close_enough(cfg, 0.83, 0.9)
    assert not close_enough(cfg, 0.6, 0.9)
    assert not close_enough(cfg, 0.9, 0.3)


def test_heal_zone_skips_zones_that_gave_nothing():
    from wiz101_auto.upkeep import barren_zones, best_wisp_zone, note_barren

    spots = {
        "Krokotopia/KT_Pyramid/KT_PalaceOfFire": [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
        "Krokotopia/KT_Pyramid/KT_Hall": [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)],
    }
    here = "Krokotopia/KT_Krokosphinx/KT_EntranceHall"
    assert best_wisp_zone(here, spots) == "Krokotopia/KT_Pyramid/KT_Hall"
    avoid = {"Krokotopia/KT_Pyramid/KT_Hall"}
    assert best_wisp_zone(here, spots, avoid=avoid) == "Krokotopia/KT_Pyramid/KT_PalaceOfFire"
    note_barren(here, now=100.0)
    assert here in barren_zones(now=200.0)
    assert here not in barren_zones(now=100.0 + 3600)


def test_empty_spot_forgotten_after_three_misses(tmp_path):
    from wiz101_auto.wisps import WispMemory

    m = WispMemory(tmp_path / "w.json")
    m.record("K/Palace", [(100.0, 100.0, 0.0)])
    assert not m.missed("K/Palace", (100.0, 100.0, 0.0))
    assert not m.missed("K/Palace", (100.0, 100.0, 0.0))
    assert m.missed("K/Palace", (100.0, 100.0, 0.0))  # third miss: dropped
    assert m.count("K/Palace") == 0


def test_heal_zone_is_the_easiest_to_reach():
    from wiz101_auto.upkeep import best_wisp_zone

    spots = {
        "K/Far": [(i, 0, 0) for i in range(0, 5000, 500)],  # many wisps, 3 gates away
        "K/Near": [(i, 0, 0) for i in range(0, 1500, 500)],  # 3 wisps, next door
        "K/KT_Hub": [(i, 0, 0) for i in range(0, 5000, 500)],
    }
    hops = {("K/KT_Hub", "K/Far"): 3, ("K/KT_Hub", "K/Near"): 1}
    assert best_wisp_zone("K/KT_Hub", spots, hops=lambda a, b: hops.get((a, b))) == "K/Near"


def test_world_hub_from_gates():
    from wiz101_auto.travel_data import world_hub

    gates = {"Krokotopia/KT_Hub": [], "Krokotopia/KT_Palace": [], "WizardCity/WC_Hub": []}
    assert world_hub("Krokotopia/KT_Pyramid/Room1", gates) == "Krokotopia/KT_Hub"
    assert world_hub("MooShu/MS_Hub2", gates) is None
