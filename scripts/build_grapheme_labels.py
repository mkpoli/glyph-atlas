"""Write what stands for a grapheme (字種) whose head is a 新字体 beside the 旧字体 it writes: 假仮, 國国.

A `shinjitai` edge of `data/vocab/kanji-variants.tsv` is directed: `b` is the 新字体 of `a`. Where `b` heads
a grapheme of `data/vocab/characters.tsv` and `a` is one of its characters, the grapheme is labelled by the
two, the old form first. A grapheme keeps its head as its key, address and place in a list: the label is
what it is shown as. A compatibility ideograph is no 字体 (docs/decisions/0013-finer-forms.md), so it labels
nothing. Of several old forms the one most sources state is shown: 鄕郷, 鷄鶏 and 麵麺 are stated by Unihan,
cjkvi-variants and OpenCC, and 鄉, 雞 and 麪 by OpenCC alone.

    uv run scripts/build_grapheme_labels.py

Writes apps/cloudflare/src/graphemeLabels.json, each head with its 旧字体, which the Worker and the review
app read.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"
LABELS = ROOT / "apps" / "cloudflare" / "src" / "graphemeLabels.json"


def heads() -> dict[str, str]:
    """Each character of the table and the head of its grapheme."""
    lines = (VOCAB / "characters.tsv").read_text(encoding="utf-8").splitlines()
    columns = lines[0].split("\t")
    char, grapheme = columns.index("char"), columns.index("grapheme")
    out = {}
    for line in lines[1:]:
        row = line.split("\t")
        points = row[grapheme].split() if len(row) > grapheme else []
        out[row[char]] = "".join(chr(int(point.replace("U+", "0x"), 0)) for point in points) or row[char]
    return out


def labels() -> dict[str, str]:
    """Each head that is a 新字体 with the 旧字体 shown before it."""
    head, stated = heads(), defaultdict(lambda: defaultdict(set))
    for line in (VOCAB / "kanji-variants.tsv").read_text(encoding="utf-8").splitlines():
        row = line.split("\t")
        if line.startswith("#") or row[2] != "shinjitai":
            continue
        old, new, _, source = row[:4]
        if old != new and head.get(new) == new and head.get(old) == new and unicodedata.normalize("NFC", old) == old:
            stated[new][old].add(source)
    out = {}
    for new in sorted(stated):
        sources = stated[new]
        ranked = sorted(sources, key=lambda old, sources=sources: (-len(sources[old]), old))
        if len(ranked) > 1 and len(sources[ranked[0]]) == len(sources[ranked[1]]):
            raise SystemExit(f"{new}: as many sources state {ranked[0]} as {ranked[1]}")
        out[new] = ranked[0]
    return out


def main() -> int:
    found = labels()
    LABELS.write_text(json.dumps(found, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"{len(found)} graphemes shown by a 旧字体 and its 新字体")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
