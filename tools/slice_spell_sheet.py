"""Cut a sheet of Wizard101 cards (a grid, row by row) into one image per card.

    .venv/Scripts/python tools/slice_spell_sheet.py docs/spell_images/myth_spells.png myth
        docs/spell_images/myth_names.txt --cols 10 --rows 10

Each card goes to docs/spell_images/<folder>/<name>_spell.png (lower case,
underscores) and docs/spell_images/index.json maps the card's name (letters
and digits only, lower case) to its file: the stream's deck tracker shows
that art instead of the card's name. The names file lists the cards in the
sheet's order, one per line. Needs Pillow (pip install pillow).
"""

import argparse
import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path("docs") / "spell_images"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("folder", help="the category: a school (myth), treasure, ...")
    ap.add_argument("names", help="a text file: one card name per line, in the sheet's order")
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--rows", type=int, default=10)
    a = ap.parse_args()
    names = [n.strip() for n in Path(a.names).read_text(encoding="utf-8").splitlines() if n.strip()]
    src = Image.open(a.sheet).convert("RGBA")
    w, h = src.size[0] // a.cols, src.size[1] // a.rows
    out = ROOT / a.folder
    out.mkdir(parents=True, exist_ok=True)
    index_path = ROOT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for i, name in enumerate(names):
        r, c = divmod(i, a.cols)
        f = out / f"{slug(name)}_spell.png"
        src.crop((c * w, r * h, (c + 1) * w, (r + 1) * h)).save(f)
        index[key(name)] = {"name": name, "school": a.folder, "file": f"{a.folder}/{f.name}"}
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(names)} cards ({w}x{h}) -> {out}")


if __name__ == "__main__":
    main()
