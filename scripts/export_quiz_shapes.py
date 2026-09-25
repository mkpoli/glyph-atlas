"""Write D1 SQL that replaces `unit_shapes` with a computed Quick review shape order.

The input is the `quiz-shapes.json` that `atlas review shapes <dataset>` writes into a dataset. Use
the dataset the published catalogue was exported from (its frozen `source/` copy), since crop ids
belong to their dataset. The output replaces the whole table, so a crop dropped from the collection
loses its row; run it with `wrangler d1 execute glyph-atlas --remote --file <out>` after migration
0005 is applied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def export(shapes: Path, out: Path) -> int:
    orders = json.loads(shapes.read_text())["orders"]
    lines = ["DELETE FROM unit_shapes;"]
    for identity, order in sorted(orders.items()):
        if not isinstance(order, int) or order < 0:
            raise ValueError(f"{identity}: shape order must be a non-negative integer")
        lines.append("INSERT INTO unit_shapes VALUES('{}',{});".format(identity.replace("'", "''"), order))
    out.write_text("\n".join(lines) + "\n")
    return len(orders)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("dataset", type=Path, help="dataset holding quiz-shapes.json, e.g. <export>/source")
    args = parser.parse_args()
    print(json.dumps({"unit_shapes": export(args.dataset / "quiz-shapes.json", args.out)}))
