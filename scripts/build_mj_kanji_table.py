"""Build data/vocab/mj-kanji.tsv from the MJ文字情報一覧表, the kanji part of 文字情報基盤.

The 一覧表 lists every kanji figure the 文字情報技術促進協議会 registered for IPAmj明朝: an MJ文字
図形名, the UCS code point it corresponds to (対応するUCS), the UCS code point the font
implementation assigned it (実装したUCS, populated only for the one figure chosen as the default
glyph where several figures share a code point) and the IVS the implementation used
(実装したMoji_JohoコレクションIVS). Several MJ figures can carry the same 対応するUCS; the table
keeps every one of them, which is what lets `variants` on a character list them all.

The workbook is published as Strict Open XML (conformance="strict"), which openpyxl and most other
xlsx readers refuse. The workbook has one sheet, so this script reads its shared strings and its one
sheet XML with the standard library directly, without an xlsx dependency.

    uv run python scripts/build_mj_kanji_table.py

Network access happens here and nowhere else; the file is cached under `cache/moji/`.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "sources" / "mj-kanji.yaml"
TARGET = ROOT / "data" / "vocab" / "mj-kanji.tsv"
CACHE = ROOT / "cache" / "moji"
USER_AGENT = "glyph-atlas (+https://github.com/mkpoli/glyph-atlas)"
#: size of mji.00602.xlsx as published; the download is checked against it.
EXPECTED_BYTES = 7_290_011
#: the columns this table keeps, a subset of the workbook's own columns.
COLUMNS = ["mj", "code_point", "implemented_code_point", "ivs"]
#: the workbook's own header names for the columns this table keeps, in the workbook's order.
WANTED = {
    "MJ文字図形名": "mj",
    "対応するUCS": "code_point",
    "実装したUCS": "implemented_code_point",
    "実装したMoji_JohoコレクションIVS": "ivs",
}


def download(url: str, dest: Path, *, pause: float = 3.0, retries: int = 5) -> Path:
    """Fetch `url` to `dest` unless it is already there, backing off on failure."""
    if dest.exists() and dest.stat().st_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120) as client:
                response = client.get(url)
                response.raise_for_status()
            dest.write_bytes(response.content)
            return dest
        except httpx.HTTPError as error:
            if attempt == retries - 1:
                raise SystemExit(f"download failed after {retries} attempts: {url}: {error}") from error
            time.sleep(pause * 2**attempt)
    raise SystemExit(f"download failed: {url}")


def sheet_rows(path: Path) -> list[list[str | None]]:
    """Every row of the workbook's one sheet as a list of cell strings, header row included.

    The workbook is read at the XML level: `xl/sharedStrings.xml` for the string table and
    `xl/worksheets/sheet1.xml` for the cells. Column position comes from each cell's own `r`
    attribute (`"D137"` -> column D), because Strict Open XML sheets, like ordinary ones, omit an
    empty trailing cell rather than padding the row.
    """
    with zipfile.ZipFile(path) as archive:
        strings_xml = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        sheet_xml = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    string_ns = strings_xml.tag.split("}")[0] + "}"
    strings = [
        "".join(node.text or "" for node in item.findall(f".//{string_ns}t"))
        for item in strings_xml.findall(f"{string_ns}si")
    ]
    sheet_ns = sheet_xml.tag.split("}")[0] + "}"

    def cell_value(cell: ElementTree.Element) -> str | None:
        value = cell.find(f"{sheet_ns}v")
        text = value.text if value is not None else None
        if cell.get("t") == "s" and text is not None:
            return strings[int(text)]
        return text

    def column_of(reference: str) -> int:
        """`"D137"` -> the zero-based index of column D."""
        letters = "".join(char for char in reference if char.isalpha())
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index - 1

    rows = []
    for row in sheet_xml.findall(f".//{sheet_ns}row"):
        cells: dict[int, str | None] = {}
        for cell in row.findall(f"{sheet_ns}c"):
            cells[column_of(cell.get("r"))] = cell_value(cell)
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(index) for index in range(width)])
    return rows


def build(source: dict, rows: list[list[str | None]]) -> list[dict[str, str]]:
    """Map the sheet rows to the output columns through the 日本語 header names."""
    expected = list(source["format"]["columns"])
    if not rows:
        raise SystemExit("the MJ文字情報一覧表 sheet is empty")
    header = [(cell or "") for cell in rows[0][: len(expected)]]
    if header != expected:
        raise SystemExit(f"unexpected columns:\n  found:    {header}\n  expected: {expected}")
    index = {name: position for position, name in enumerate(expected)}
    records = []
    for row in rows[1:]:
        values = row + [None] * (len(expected) - len(row))
        records.append({key: (values[index[column]] or "") for column, key in WANTED.items()})
    return records


def provenance(source: dict, archive: Path, rows: list[dict[str, str]]) -> list[str]:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with_code_point = sum(1 for row in rows if row["code_point"])
    counted: dict[str, int] = {}
    for row in rows:
        if row["code_point"]:
            counted[row["code_point"]] = counted.get(row["code_point"], 0) + 1
    several = sum(1 for count in counted.values() if count > 1)
    return [
        f"# {source['name']} Ver.{source['version']}",
        f"# publisher: {source['publisher']}",
        f"# url: {source['url']}",
        f"# access: {source['access']['xlsx']}",
        f"# archive: {archive.name}, {archive.stat().st_size} bytes, sha256 {digest}",
        f"# licence: {source['licence']} ({source['licence_evidence']})",
        f"# attribution: {source['attribution']}",
        "# columns: " + ", ".join(COLUMNS),
        "# code_point is 対応するUCS, which several MJ figures can share; every figure is kept.",
        (
            "# implemented_code_point is 実装したUCS, the one default figure IPAmj明朝 picked per code "
            "point; empty on a non-default figure."
        ),
        "# ivs is 実装したMoji_JohoコレクションIVS.",
        (
            f"# rows: {len(rows)}, with a code point: {with_code_point}, without: "
            f"{len(rows) - with_code_point}, code points with several figures: {several}"
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)

    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    url = source["access"]["xlsx"]
    archive = args.cache / url.rsplit("/", 1)[-1]
    if args.refresh and archive.exists():
        archive.unlink()
    download(url, archive)
    size = archive.stat().st_size
    if size != EXPECTED_BYTES:
        print(f"warning: {archive.name} is {size} bytes, the pinned size is {EXPECTED_BYTES}", file=sys.stderr)

    rows = build(source, sheet_rows(archive))
    counts = source.get("counts", {})
    with_cp = sum(1 for row in rows if row["code_point"])
    counted: dict[str, int] = {}
    for row in rows:
        if row["code_point"]:
            counted[row["code_point"]] = counted.get(row["code_point"], 0) + 1
    several = sum(1 for count in counted.values() if count > 1)
    if counts and (
        len(rows) != counts["rows"]
        or with_cp != counts["with_code_point"]
        or several != counts["code_points_with_several_figures"]
    ):
        print(
            f"warning: the workbook has {len(rows)} rows, {with_cp} with a code point, "
            f"{several} code points with several figures; {SOURCE.name} records "
            f"{counts['rows']}, {counts['with_code_point']}, {counts['code_points_with_several_figures']}",
            file=sys.stderr,
        )

    lines = provenance(source, archive, rows)
    lines.append("\t".join(COLUMNS))
    lines += ["\t".join(row[column] for column in COLUMNS) for row in rows]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{len(rows)} rows, {with_cp} with a code point, {several} code points with several figures "
        f"-> {args.out.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
