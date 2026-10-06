from wiz101_auto.relog import RELOG_LOOP_WINDOW, relog_loop

BAZAAR = "WizardCity/WC_Streets/Interiors/WC_OldeTown_AuctionHouse"


def test_a_second_relog_in_the_same_zone_soon_after_is_a_loop():
    assert not relog_loop([], BAZAAR, 100.0)
    assert relog_loop([(60.0, BAZAAR)], BAZAAR, 100.0)
    assert not relog_loop([(60.0, "WizardCity/WC_Hub")], BAZAAR, 100.0)
    assert not relog_loop([(0.0, BAZAAR)], BAZAAR, RELOG_LOOP_WINDOW + 1)
