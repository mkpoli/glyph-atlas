"""Write what stands for a grapheme (字種) that the Jōyō table writes two ways: 假仮, 國国.

A `shinjitai-kyujitai` family of `data/vocab/graphemes.yaml` is a 新字体 and the 旧字体 the 常用漢字表 (2010)
prints beside it, its older form where the two differ markedly (`joyo-2010`). Such a grapheme is labelled by
the two, the old form first. A grapheme keeps its head, the 新字体, as its key, address and place in a list:
the label is what it is shown as.

    uv run scripts/build_grapheme_labels.py

Writes apps/cloudflare/src/graphemeLabels.json, each head with its 旧字体, which the Worker and the review
app read.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ROOT / "data" / "vocab" / "graphemes.yaml"
LABELS = ROOT / "apps" / "cloudflare" / "src" / "graphemeLabels.json"


def labels() -> dict[str, str]:
    """Each head of a 新字体–旧字体 family with the 旧字体 shown before it."""
    out = {}
    for point, family in yaml.safe_load(FAMILIES.read_text(encoding="utf-8"))["families"].items():
        if family["relation"] != "shinjitai-kyujitai":
            continue
        new, old = family["members"]
        if f"U+{ord(new):04X}" != point:
            raise SystemExit(f"{point}: the family's first character is {new}")
        out[new] = old
    return dict(sorted(out.items()))


def main() -> int:
    found = labels()
    LABELS.write_text(json.dumps(found, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"{len(found)} graphemes shown by a 旧字体 and its 新字体")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
