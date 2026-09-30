"""Build data/vocab/han-component-variants.tsv: the component substitutions the variant graph attests.

Two written variant pairs whose Ideographic Description Sequences differ in exactly one component
attest that substitution for the pair and for everything built the same way: 強 is ⿰弓𧈧 and 强 is
⿰弓虽, and inside it 𧈧 is 厶 where 虽 is 口, so 強 and 强 attest 口→厶. The decomposition comes from
`data/vocab/han-ids.tsv`, the pairs from every edge of `data/vocab/kanji-variants.tsv` under which one
ideograph may be written for another (`glyph_atlas.refs.WRITTEN_FOR`), read through
`han_component_variants.Descriptions` so that equal shapes are equal trees (⺡ is 氵, ⿱X⿱YZ is ⿳XYZ).

A substitution is kept once it has `han_component_variants.THRESHOLD` distinct attesting pairs and
`THRESHOLD` distinct attesting contexts (`han_component_variants.kept`): the pairs themselves when
the one differing part sits at the top of a character, the enclosing substitutions when it sits
inside a component (𧈧→虽 for 厶→口). One pair is one source's word for two characters, and one
position replicated across a thousand pairs is one claim, so neither predicts alone. The run prints
the count distribution the threshold was chosen against.

Each row records the substitution with every pair that attests it and the sources that state each
pair; a substitution is undirected. The header cites every corpus the rows come through, and
`derived-ids` names the tier of predictions the substitutions are later applied to.

    .venv/bin/python scripts/build_han_component_variants.py
    .venv/bin/python scripts/build_han_component_variants.py --out /tmp/x.tsv   # somewhere else
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from glyph_atlas import han_component_variants as v
from glyph_atlas import han_components as hc
from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"
TARGET = VOCAB / "han-component-variants.tsv"
#: The tables the substitutions are read through, in citation order; their own `# source` and `#   `
#: header lines are copied so a citation never drifts from the table it names.
FEEDS = ("han-ids.tsv", "kanji-variants.tsv", "han-component-forms.tsv")
COLUMNS = ("a", "b", "count", "pairs")


def feed_header() -> list[str]:
    """The `# source …` and its `#   …` detail lines of every table the rows come through, once each."""
    lines: list[str] = []
    for name in FEEDS:
        for line in (VOCAB / name).read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break
            if line.startswith(("# source ", "#   ")) and line not in lines:
                lines.append(line)
    return lines


def derived_tier() -> str:
    """The citation of the predictions tier (`derived-ids`) as a `# source` line of the same shape."""
    record = (
        "Predicted component variants (derived, not attested); derived from the rows of this table "
        "over BabelStone IDS; mkpoli, Glyph Atlas, CC BY-SA 4.0 (LICENSE-DATA)"
    )
    return f"# source derived-ids: {record}"


def header(rows: int) -> list[str]:
    return [
        (
            "# Component substitutions two written variant pairs attest, each with the pairs behind "
            "it and their sources; see scripts/build_han_component_variants.py."
        ),
        *feed_header(),
        derived_tier(),
        (
            f"# threshold: {v.THRESHOLD} distinct attesting pairs seen in {v.THRESHOLD} distinct "
            "contexts: the pairs themselves at the top of a character, the enclosing substitutions "
            "inside a component; one pair or one repeated position alone never predicts."
        ),
        (
            "# substitution: undirected, a before b by length then code point; pairs are "
            "`A:B=source+source`, a pair and b in code point order, sources sorted."
        ),
        "# columns: " + ", ".join(COLUMNS),
        f"# rows: {rows}",
    ]


def attested_pairs() -> dict[tuple[str, str], set[str]]:
    """Every written pair of the 異体字 graph with the sources that state it, each pair once."""
    pairs: dict[tuple[str, str], set[str]] = {}
    for edge in refs.variant_edges():
        if edge["written"] and edge["a"] != edge["b"]:
            pair = tuple(sorted((edge["a"], edge["b"]), key=ord))
            pairs.setdefault(pair, set()).add(edge["source"])
    return pairs


def describe() -> v.Descriptions:
    raw = {row[1]: row[2].split(" ") for row in hc._read("han-ids.tsv")}
    return v.Descriptions(raw, hc.unified())


def rows() -> tuple[list[tuple[str, str, int, str]], dict[str, v.Attested]]:
    """The kept rows (a, b, count, pairs) in code point order, and every substitution found."""
    desc = describe()
    found = v.attest(desc, ((a, b, sources) for (a, b), sources in attested_pairs().items()))
    kept = v.kept(found)
    out = []
    for item in sorted(kept, key=lambda item: (item.a, item.b)):
        pairs = " ".join(
            f"{p}:{q}={'+'.join(sources)}" for p, q, sources in item.pairs
        )
        out.append((item.a, item.b, item.count, pairs))
    return out, found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=TARGET)
    args = parser.parse_args(argv)

    table, found = rows()
    distribution = Counter(item.count for item in found.values())
    print(f"substitutions found: {len(found)}  kept at threshold {v.THRESHOLD}: {len(table)}")
    print("attesting contexts each:",
          "  ".join(f"{count}×{distribution[count]}" for count in sorted(distribution)))
    pair_distribution = Counter(len(item.pairs) for item in found.values())
    print("attesting pairs each (first eight):",
          "  ".join(f"{count}×{pair_distribution[count]}" for count in sorted(pair_distribution)[:8]))
    for pair in (("睘", "𦊷"), ("厶", "口"), ("𧈧", "虽")):
        key = v._ordered(*pair)
        item = found.get(key)
        print(f"  {key[0]}↔{key[1]}: count {item.count if item else 0} over "
              f"{len(item.pairs) if item else 0} pairs")

    args.out.write_text(
        "\n".join(header(len(table)) + ["\t".join(COLUMNS)]
                  + ["\t".join(str(value) for value in row) for row in table]) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
