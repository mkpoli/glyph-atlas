"""Write the component index the component search reads (`han_components`, `han_component_names`) into
the reference input (`glyph_atlas.reference_input`), which `scripts/atlas3/reference.py` loads onto the
site as one list per component.

    .venv/bin/python scripts/export_components_cloudflare.py

Both tables are replaced in one transaction.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from glyph_atlas import han_components, reference_input, refs


def component_rows():
    """(component, tier, size, code point, count, direct) for every character the character table holds."""
    for part, char, n, direct, tier, size in han_components.rows():
        code_point = refs.to_code_point(char)
        if refs.character(code_point) is not None:
            yield part, tier, size, code_point, n, direct


def name_rows(totals: Counter):
    """What a person may type for each component: the component itself, and each radical character
    Unicode unifies with it (⺡ for 氵), with how many characters hold it."""
    for component, total in sorted(totals.items()):
        if component not in han_components.unified():
            yield component, component, total
    for form, unified in sorted(han_components.unified().items()):
        if unified in totals:
            yield form, unified, totals[unified]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, help="the reference input to write (default: reference-input.sqlite in ATLAS3_DIR)")
    args = parser.parse_args(argv)
    rows = sorted(component_rows())
    if not rows:
        raise SystemExit("no character the character table holds has a component; the reference input is left as it was")
    totals = Counter(row[0] for row in rows)
    with reference_input.writing(args.input) as db:
        counts = {"han_components": reference_input.replace(db, "han_components", rows),
                  "han_component_names": reference_input.replace(db, "han_component_names", name_rows(totals)),
                  "characters": len({r[3] for r in rows})}
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
