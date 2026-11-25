"""Write what stands for a grapheme (字種): its 旧字体, its head and its 简体字, each once (圖図图, 語语, 假仮).

The 旧字体 is the older form the 常用漢字表 (2010) prints beside the head, as 仮（假）. Its pairs are the
`joyo/variant` edges of `data/vocab/kanji-variants.tsv`, which CJKVI names 異体字（常用漢字表）, and they are
those of the 文化庁 index of the table (`joyo-2010` in `graphemes.yaml`). A pair whose older form is of another
grapheme (弁 辨) gives none, as the two are counted apart, and neither does a compatibility ideograph.

The 简体字 is a character of the same grapheme that the 通用规范汉字表 (2013) lists and that Unihan gives as the
simplified form of another of its characters (kSimplifiedVariant and kTraditionalVariant, the `simplified`
edges of source `unihan`), the first in the table's order where there are more (线 before 缐). There is none
where the table lists the head too (确), nor where the simplified form it lists of the head or of the 旧字体 is
of another grapheme (豊 豐: 丰; 発 發: 发): a 简体字 left in the grapheme is then some other member's.

A head the 常用漢字表 prints no older form of stands after the character Unihan simplifies to the 简体字 instead,
where the head is neither that character nor another that simplifies in the grapheme (線): 彎弯, whose head is
the 简体字, and 壺壷壶. One of the same 構形 as the head (`glyph_atlas.structures`: 說 説, 稅 税) differs from it by
a near-identical glyph, and is not added. A grapheme keeps its head as its key, address and place in a list:
the label is what it is shown as.

    uv run scripts/build_grapheme_labels.py [--unihan cache/variants/18.0.0/Unihan.zip]

Writes apps/cloudflare/src/graphemeLabels.json, each head with its label, which the Worker and the review app
read. `--unihan` first writes data/vocab/tongyong-guifan.tsv, the 通用规范汉字表 as Unihan's kTGH numbers it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import yaml

from glyph_atlas import refs, structures

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ROOT / "data" / "vocab" / "kanji-variants.tsv"
TONGYONG = ROOT / "data" / "vocab" / "tongyong-guifan.tsv"
UNIHAN = ROOT / "data" / "sources" / "unihan.yaml"
LABELS = ROOT / "apps" / "cloudflare" / "src" / "graphemeLabels.json"

#: The file `unihan.yaml` describes, as `ids-parts.yaml` records it too.
UNIHAN_SHA256 = "4c93ea9c1f636451729a840978f1667a53886af37ba854fdcce109721c63d43e"
#: The characters of the 通用规范汉字表.
TONGYONG_SIZE = 8105


def tongyong_table(archive: Path) -> str:
    """The 通用规范汉字表 in Unihan's archive, as `data/vocab/tongyong-guifan.tsv` holds it."""
    if (found := hashlib.sha256(archive.read_bytes()).hexdigest()) != UNIHAN_SHA256:
        raise SystemExit(f"{archive}: sha256 {found}, not the {UNIHAN_SHA256} of Unihan 18.0.0")
    positions: dict[int, str] = {}
    with zipfile.ZipFile(archive) as files:
        for line in files.read("Unihan_OtherMappings.txt").decode("utf-8").splitlines():
            cells = line.split("\t")
            if len(cells) == 3 and cells[1] == "kTGH":
                year, position = cells[2].split(":")
                if year != "2013" or int(position) in positions:
                    raise SystemExit(f"{cells[0]}: kTGH {cells[2]}")
                positions[int(position)] = chr(int(cells[0].removeprefix("U+"), 16))
    if sorted(positions) != list(range(1, TONGYONG_SIZE + 1)):
        raise SystemExit(f"kTGH numbers {len(positions)} characters, not 1 to {TONGYONG_SIZE}")
    record = yaml.safe_load(UNIHAN.read_text(encoding="utf-8"))
    origin = f"{record['name'].partition(',')[0]} {record['version']}, kTGH (Unihan_OtherMappings.txt, `2013:<position>`)"
    header = [
        "# The 通用规范汉字表 (2013): its 8,105 characters by their number in it; see scripts/build_grapheme_labels.py.",
        f"# source unihan: {origin}; {record['licence']} ({record['licence_evidence']}); {record['attribution']}",
        f"#   {record['access']['zip']} sha256:{UNIHAN_SHA256[:16]}",
        "position\tcode_point\tchar",
    ]
    rows = [f"{position}\t{refs.to_code_point(char)}\t{char}" for position, char in sorted(positions.items())]
    return "\n".join([*header, *rows]) + "\n"


def tongyong() -> dict[str, int]:
    """The characters of the 通用规范汉字表, each with its number in it."""
    rows = [line.split("\t") for line in TONGYONG.read_text(encoding="utf-8").splitlines() if line[:1].isdigit()]
    return {char: int(position) for position, _, char in rows}


def edges() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The 常用漢字表's pairs, each 新字体 with a 旧字体 it prints beside it, and Unihan's, each character with
    a simplified form of it."""
    joyo, simplified = set(), set()
    for line in VARIANTS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        a, b, relation, source, detail = line.split("\t")
        if relation == "simplified" and source == "unihan":
            simplified.add((a, b))
        if "joyo/variant" in detail:
            for edge in detail.split(" | "):
                named, _, kind = edge.partition(" ")
                if kind == "joyo/variant":
                    new, old = named.split("→")
                    joyo.add((new, old))
    return sorted(joyo), sorted(simplified)


def grapheme(char: str) -> str:
    """The head of the grapheme a character is of."""
    key = refs.grapheme(refs.to_code_point(char))
    return refs.to_char(key) if key else char


def older_forms(joyo: list[tuple[str, str]]) -> dict[str, str]:
    """Each head the 常用漢字表 prints an older form of, in the same grapheme, with that form."""
    out: dict[str, str] = {}
    for new, old in joyo:
        if unicodedata.normalize("NFC", old) != old or not (grapheme(new) == grapheme(old) == new):
            continue
        if new in out:
            raise SystemExit(f"{new}: two older forms in its grapheme, {out[new]} and {old}")
        out[new] = old
    return out


def labels() -> dict[str, str]:
    """Each head that is not a grapheme's whole label, with the label, in code point order."""
    joyo, pairs = edges()
    older, listed = older_forms(joyo), tongyong()
    simplified: dict[str, list[str]] = defaultdict(list)
    for char, simple in pairs:
        if simple in listed and simple != char:
            simplified[char].append(simple)
    within: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for char, forms in simplified.items():
        for simple in forms:
            if (head := grapheme(char)) == grapheme(simple):
                within[head].append((listed[simple], simple, char))
    out = {}
    for head in older.keys() | within.keys():
        old, found = older.get(head, ""), min(within.get(head, []), default=None)
        simple, full = found[1:] if found else ("", "")
        own = [form for char in (head, old) for form in simplified.get(char, [])]
        if simple != head and (head in listed or own and not any(grapheme(form) == head for form in own)):
            simple = full = ""
        if simplified.get(head) or full and structures.for_character(full) == structures.for_character(head):
            full = ""
        label = "".join(dict.fromkeys((old or full) + head + simple))
        if label != head:
            out[head] = label
    return dict(sorted(out.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unihan", type=Path, help="Unihan.zip, to write data/vocab/tongyong-guifan.tsv from")
    args = parser.parse_args()
    if args.unihan:
        TONGYONG.write_text(tongyong_table(args.unihan), encoding="utf-8")
    found = labels()
    LABELS.write_text(json.dumps(found, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    three = sum(len(label) == 3 for label in found.values())
    print(f"{len(found)} graphemes shown by more than their head, {three} of them by three characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
