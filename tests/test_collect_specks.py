from wiz101_auto.collect import matches_item


def test_wisp_specks_are_wisp_dandruff():
    assert matches_item("Wisp Specks", "LM_WispDandruff_Blue")
    assert matches_item("Wisp Specks", "LM_WispDandruff_Red")


def test_wisp_specks_not_health_wisps():
    assert not matches_item("Wisp Specks", "WC_WispHealth")
