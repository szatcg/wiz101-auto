from types import SimpleNamespace

from wiz101_auto.petclient import pick_client


def _c(pid):
    return SimpleNamespace(process_id=pid)


def test_hooks_the_window_that_isnt_the_main_bots():
    main, alt = _c(100), _c(200)
    assert pick_client([main, alt], 100)[0] is alt
    assert pick_client([alt, main], 100)[0] is alt


def test_never_the_main_bots_window():
    assert pick_client([_c(100)], 100)[0] is None
    assert pick_client([_c(100), _c(200)], 0)[0] is None  # (which is the main one isn't known)
    assert pick_client([_c(100), _c(200), _c(300)], 100)[0] is None
    assert pick_client([_c(100), _c(200), _c(300)], 100, want_pid=300)[0].process_id == 300
