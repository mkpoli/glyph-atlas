"""Build the component tables that let a search name a character by its parts (水骨 or 氵骨 finds 滑).

`data/vocab/han-ids.tsv`, one row per ideograph: its Ideographic Description Sequences from BabelStone's
IDS.TXT, every alternative the file gives, each with the source regions it describes (`⿰氵骨(GHTJKPV)`).
The maintainer's `*` notes on unifications and earlier sequences are left out.
Unencoded components keep BabelStone's numbers (`{108}`) and an unrepresentable one stays `？`.

`data/vocab/han-component-forms.tsv`, one row per component form that stands for another character:

`unified`   the CJK Radicals Supplement or Kangxi Radicals character and the unified ideograph Unicode
            names as its equivalent (EquivalentUnifiedIdeograph.txt: ⺡ is 氵, ⺼ is 肉)
`radical`   a radical's variant form and the radical it writes (cjkvi radical-variants.txt, tags
            radical-variant and radical-variant-simplified: 氵 is 水, 扌 is 手, 钅 is 金). A variant
            that is itself one of the 214 radicals keeps its own identity: 月 writes 肉 on the left of a
            character, and it is also the moon radical, so it is not listed as 肉.

    uv sync --extra data
    .venv/bin/python scripts/build_han_components.py

Downloads are cached under `cache/components/`. IDS.TXT is not versioned upstream, so its own `File Date`
header and the file's digest go into the table header; `--refresh` fetches everything again.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from pathlib import Path

import yaml

from glyph_atlas.net import download

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"
SOURCES = ROOT / "data" / "sources"

IDS_URL = "https://www.babelstone.co.uk/CJK/IDS.TXT"
UNICODE_VERSION = "18.0.0"
EQUIVALENT_URL = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/EquivalentUnifiedIdeograph.txt"
CJKVI_REVISION = "e4f1da248c9737a243f9930b5dc497cef5d5ae16"
RADICALS_URL = f"https://raw.githubusercontent.com/cjkvi/cjkvi-variants/{CJKVI_REVISION}/radical-variants.txt"
RADICAL_TAGS = {"cjkvi/radical-variant", "cjkvi/radical-variant-simplified"}

SEQUENCE = re.compile(r"^\^(?P<ids>[^$]+)\$(?:\((?P<regions>[^)]*)\))?$")


def ids_rows(text: str) -> list[tuple[str, str, str]]:
    """(code point, character, sequences) for every ideograph in IDS.TXT, in file order."""
    rows = []
    for line in text.lstrip("﻿").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        code_point, char, *sequences = line.rstrip("\r").split("\t")
        written = []
        for sequence in sequences:
            if sequence.startswith("*"):
                # The maintainer's notes on unifications and earlier sequences, not a description.
                continue
            match = SEQUENCE.match(sequence.strip())
            if not match:
                raise ValueError(f"unreadable IDS for {code_point}: {sequence!r}")
            regions = match["regions"]
            written.append(match["ids"] + (f"({regions})" if regions else ""))
        if f"U+{ord(char):04X}" != code_point:
            raise ValueError(f"{code_point} is written {char!r}")
        rows.append((code_point, char, " ".join(written)))
    return rows


def file_date(text: str) -> str:
    match = re.search(r"^# File Date: (\S+)", text.lstrip("﻿"), re.MULTILINE)
    if not match:
        raise ValueError("IDS.TXT has no File Date header")
    return match[1]


def unified_forms(text: str) -> list[tuple[str, str, str, str]]:
    """(form, character, source, detail) for each radical Unicode maps to a unified ideograph, and for
    each Kangxi Radicals character, whose compatibility decomposition names it."""
    rows = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        form, unified = (part.strip() for part in line.split(";"))
        firsts = [int(value, 16) for value in form.split("..")]
        for code in range(firsts[0], firsts[-1] + 1):
            rows.append((chr(code), chr(int(unified, 16)), "unicode-ucd", "EquivalentUnifiedIdeograph"))
    for code in range(0x2F00, 0x2FD6):
        unified = unicodedata.normalize("NFKC", chr(code))
        if unified != chr(code):
            rows.append((chr(code), unified, "unicode-ucd", "Kangxi Radicals decomposition"))
    return rows


def radical_forms(text: str) -> list[tuple[str, str, str, str]]:
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        proper, tag, variant, *position = line.split(",")
        if tag in RADICAL_TAGS:
            rows.append(
                (
                    variant,
                    proper,
                    "cjkvi-variants",
                    tag.split("/", 1)[1] + (f" {position[0]}" if position else ""),
                )
            )
    return rows


def component_forms(unified: list, radical: list) -> list[tuple[str, str, str, str]]:
    """Every form row, less the variants that are themselves radicals (月 for 肉); first row per form wins."""
    radicals = {unicodedata.normalize("NFKC", chr(code)) for code in range(0x2F00, 0x2FD6)}
    seen: dict[str, tuple] = {}
    for row in unified + [row for row in radical if row[0] not in radicals]:
        if row[0] != row[1]:
            seen.setdefault(row[0], row)
    return sorted(seen.values(), key=lambda row: (row[0], row[1]))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def header(title: str, identifiers: list[str], lines: list[str]) -> list[str]:
    out = [f"# {title}; see scripts/build_han_components.py."]
    for identifier in identifiers:
        record = yaml.safe_load((SOURCES / f"{identifier}.yaml").read_text(encoding="utf-8"))
        out.append(
            f"# source {identifier}: {record['name']}; {record['licence']} ({record['licence_evidence']}); "
            f"{record['attribution']}"
        )
    return out + lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("--out", type=Path, default=VOCAB)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)
    cache = args.cache / "components"

    def fetch(url: str, dest: Path) -> Path:
        return download(url, dest, expected="text", refresh=args.refresh, timeout=300)

    ids_path = fetch(IDS_URL, cache / "IDS.TXT")
    equivalent_path = fetch(EQUIVALENT_URL, cache / UNICODE_VERSION / "EquivalentUnifiedIdeograph.txt")
    radicals_path = fetch(RADICALS_URL, cache / "cjkvi" / "radical-variants.txt")

    ids_text = ids_path.read_text(encoding="utf-8")
    rows = ids_rows(ids_text)
    forms = component_forms(
        unified_forms(equivalent_path.read_text(encoding="utf-8")),
        radical_forms(radicals_path.read_text(encoding="utf-8")),
    )

    args.out.mkdir(parents=True, exist_ok=True)
    ids_lines = header(
        "Ideographic Description Sequences, one row per ideograph",
        ["babelstone-ids"],
        [
            f"#   babelstone-ids: {IDS_URL}, File Date {file_date(ids_text)}, sha256:{digest(ids_path)}",
            (
                "# sequences: space-separated, each with the source regions it describes; {n} is BabelStone's "
                "unencoded component n, ？ an unrepresentable one."
            ),
            "# columns: code_point, char, sequences",
            f"# rows: {len(rows)}",
            "code_point\tchar\tsequences",
        ],
    )
    (args.out / "han-ids.tsv").write_text(
        "\n".join(ids_lines + ["\t".join(row) for row in rows]) + "\n", encoding="utf-8"
    )
    form_lines = header(
        "Component forms that stand for another character",
        ["unicode-ucd", "cjkvi-variants"],
        [
            f"#   unicode-ucd: {EQUIVALENT_URL} sha256:{digest(equivalent_path)}; Kangxi Radicals by NFKC",
            f"#   cjkvi-variants: {RADICALS_URL}",
            "# columns: form, char, source, detail",
            f"# rows: {len(forms)}",
            "form\tchar\tsource\tdetail",
        ],
    )
    (args.out / "han-component-forms.tsv").write_text(
        "\n".join(form_lines + ["\t".join(row) for row in forms]) + "\n", encoding="utf-8"
    )
    print(f"{len(rows)} ideographs, {len(forms)} component forms -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
