"""Write what stands for a grapheme (字種) that the Jōyō table writes two ways: 假仮, 國国.

The 常用漢字表 (2010) prints the 旧字体 beside a 新字体 that differs from it markedly, as 仮（假）. Its pairs are
the `joyo/variant` edges of `data/vocab/kanji-variants.tsv`, which CJKVI names 異体字（常用漢字表）, and they are
those of the 文化庁 index of the table (`joyo-2010` in `graphemes.yaml`). A pair whose two characters are of
the grapheme the 新字体 heads labels it, the old form first. A grapheme keeps its head as its key, address and
place in a list: the label is what it is shown as. A pair whose older form is of another grapheme (状 狀, 弁 辨)
labels none, as the two are then counted apart, and neither does a compatibility ideograph.

    uv run scripts/build_grapheme_labels.py

Writes apps/cloudflare/src/graphemeLabels.json, each head with its 旧字体, which the Worker and the review
app read.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ROOT / "data" / "vocab" / "kanji-variants.tsv"
LABELS = ROOT / "apps" / "cloudflare" / "src" / "graphemeLabels.json"


def pairs() -> list[tuple[str, str]]:
    """The 常用漢字表's pairs, each 新字体 with a 旧字体 it prints beside it."""
    found = set()
    for line in VARIANTS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "joyo/variant" not in line:
            continue
        for edge in line.split("\t")[4].split(" | "):
            named, _, relation = edge.partition(" ")
            if relation == "joyo/variant":
                new, old = named.split("→")
                found.add((new, old))
    return sorted(found)


def labels() -> dict[str, str]:
    """Each head the table prints an older form of, in the same grapheme, with that form."""
    out: dict[str, str] = {}
    for new, old in pairs():
        head = refs.to_code_point(new)
        if unicodedata.normalize("NFC", old) != old or not (refs.grapheme(head) == refs.grapheme(refs.to_code_point(old)) == head):
            continue
        if new in out:
            raise SystemExit(f"{new}: two older forms in its grapheme, {out[new]} and {old}")
        out[new] = old
    return out


def main() -> int:
    found = labels()
    LABELS.write_text(json.dumps(found, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"{len(found)} graphemes shown by a 旧字体 and its 新字体")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
