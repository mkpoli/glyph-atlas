"""Build data/vocab/hentaigana.tsv from Unicode's NamesList.txt.

Columns: code point, character, Unicode name, readings (kana, several joined by /), 字母, 字母 code
point. Every code point with a `derived from` note is included: the 285 HENTAIGANA LETTER characters,
U+1B001 (alias HENTAIGANA LETTER E-1) and U+1B11F (archaic WU).

    uv run python scripts/build_hentaigana_table.py cache/NamesList.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

KANA = {
    "A": "あ", "I": "い", "U": "う", "E": "え", "O": "お",
    "KA": "か", "KI": "き", "KU": "く", "KE": "け", "KO": "こ",
    "SA": "さ", "SI": "し", "SU": "す", "SE": "せ", "SO": "そ",
    "TA": "た", "TI": "ち", "TU": "つ", "TE": "て", "TO": "と",
    "NA": "な", "NI": "に", "NU": "ぬ", "NE": "ね", "NO": "の",
    "HA": "は", "HI": "ひ", "HU": "ふ", "HE": "へ", "HO": "ほ",
    "MA": "ま", "MI": "み", "MU": "む", "ME": "め", "MO": "も",
    "YA": "や", "YU": "ゆ", "YO": "よ",
    "RA": "ら", "RI": "り", "RU": "る", "RE": "れ", "RO": "ろ",
    "WA": "わ", "WI": "ゐ", "WE": "ゑ", "WO": "を", "N": "ん",
    "YE": "𛀁", "WU": "𛄟",
}
NAME = re.compile(r"^(?P<cp>[0-9A-F]{4,6})\t(?P<name>.+)$")


def readings(name: str) -> str:
    body = name.removeprefix("HENTAIGANA LETTER ").removeprefix("HIRAGANA LETTER ARCHAIC ")
    body = re.sub(r"-\d+$", "", body)
    return "/".join(KANA[part] for part in body.split("-"))


def rows(text: str):
    current = None
    for line in text.splitlines():
        match = NAME.match(line)
        if match:
            current = (match["cp"], match["name"])
            continue
        if current and line.startswith("\t* derived from "):
            jibo = line.split()[-1]
            cp, name = current
            yield cp, chr(int(cp, 16)), name, readings(name), chr(int(jibo, 16)), jibo
            current = None


def main(source: Path, target: Path) -> None:
    out = ["code_point\tchar\tname\treadings\tjibo\tjibo_code_point"]
    out += ["\t".join(("U+" + r[0],) + r[1:5] + ("U+" + r[5],)) for r in rows(source.read_text(encoding="utf-8"))]
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(out) - 1} rows -> {target}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(__file__).resolve().parents[1] / "data" / "vocab" / "hentaigana.tsv")
