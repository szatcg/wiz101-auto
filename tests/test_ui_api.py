def test_ui_helpers_used_elsewhere_exist():
    """Names other modules call on `ui` must exist (one was lost in an edit once)."""
    import re
    from pathlib import Path

    from wiz101_auto import ui

    src = Path(__file__).resolve().parents[1] / "src" / "wiz101_auto"
    used = set()
    for f in src.rglob("*.py"):
        used |= set(re.findall(r"\bui\.([a-z_]+)\(", f.read_text(encoding="utf-8")))
    missing = sorted(n for n in used if not hasattr(ui, n))
    assert not missing, f"ui is missing: {missing}"
