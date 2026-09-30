"""The dashboard/stream pages' scripts must at least parse: a syntax
error (a duplicate `const`) left the published tracker page with no data."""

import shutil
import subprocess
from pathlib import Path

import pytest

PAGES = Path(__file__).parent.parent / "src" / "wiz101_auto"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node for `node --check`")
@pytest.mark.parametrize("page", ["dashboard.html", "stream.html"])
def test_page_script_parses(page, tmp_path):
    html = (PAGES / page).read_text(encoding="utf-8")
    start = html.index("<script>") + len("<script>")
    js = tmp_path / "page.js"
    js.write_text(html[start:html.rindex("</script>")], encoding="utf-8")
    result = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
