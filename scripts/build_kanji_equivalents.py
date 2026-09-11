"""Build data/vocab/kanji-equivalents.tsv, the character equivalence rows.

Three kinds, each from one upstream table and each with its own licence:

`compatibility`
    A CJK compatibility ideograph and the single CJK unified ideograph its decomposition names
    (UnicodeData.txt, Unicode License v3). The decomposition of a CJK compatibility ideograph is
    canonical in the current UCD; every other row is a `<compat>` decomposition that names one
    CJK unified ideograph.

`itaiji`
    Two characters that JIS X 0213:2012 places at one 面区点, so the standard's 包摂規準 read one
    for the other (MJ文字情報一覧表 Ver.006.02, CC BY-SA 2.1 JP). Pairs are ordered by code point.

`shinji-kyuji`
    A 旧字 and the 常用漢字 新字 it is unified into. The relation is the 法務省戸籍法関連通達・通知
    (種別 戸籍統一文字情報 親字・正字) row of the MJ縮退マップ Ver.1.2.0 (CC BY-SA 2.1 JP), kept
    where the 新字 carries 漢字施策 常用漢字 in the MJ文字情報一覧表, the 旧字 is a BMP CJK unified
    ideograph, and the two sit at different JIS X 0213 面区点, which is what separates a separately
    encoded 旧字 from a glyph variant unified at one 面区点.

    uv sync --extra data
    .venv/bin/python scripts/build_kanji_equivalents.py

Network access happens here and nowhere else; the downloads are cached under `cache/ucd/` and
`cache/moji/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
UCD_SOURCE = ROOT / "data" / "sources" / "unicode-ucd.yaml"
TARGET = ROOT / "data" / "vocab" / "kanji-equivalents.tsv"
USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
UNICODEDATA_URL = "https://www.unicode.org/Public/UCD/latest/ucd/UnicodeData.txt"
MJ_TABLE_URL = "https://moji.or.jp/wp-content/uploads/2024/01/mji.00602.xlsx"
MJ_TABLE_NAME = "MJ文字情報一覧表"
MJ_TABLE_VERSION = "006.02"
MJ_TABLE_LICENCE = "CC-BY-SA-2.1-JP"
MJ_TABLE_LICENCE_URL = "https://moji.or.jp/mojikiban/mjlist/"
MJ_TABLE_ATTRIBUTION = "文字情報基盤 MJ文字情報一覧表（IPA）, CC BY-SA 2.1 JP"
SHRINK_MAP_URL = "https://moji.or.jp/wp-content/mojikiban/oscdl/MJShrinkMap.1.2.0.json"
SHRINK_MAP_VERSION = "1.2.0"
COLUMNS = ["a", "b", "kind", "source"]
KINDS = ["compatibility", "shinji-kyuji", "itaiji"]
SOURCE_IDS = {
    "compatibility": "unicode-ucd",
    "shinji-kyuji": "mj-shrink-map",
    "itaiji": "mj-main-table",
}
#: columns of the MJ文字情報一覧表 main table that this script reads
MJ_COLUMNS = ["MJ文字図形名", "対応するUCS", "漢字施策", "X0213"]
MJ_COLUMNS_ITAIJI = ["MJ文字図形名", "対応するUCS", "X0213"]
#: fields of the MJ縮退マップ that this script reads
SHRINK_FIELDS = ["MJ文字図形名", "法務省戸籍法関連通達・通知", "種別", "UCS", "JIS X 0213"]
SHRINK_KIND = "戸籍統一文字情報 親字・正字"
STRICT = "{http://purl.oclc.org/ooxml/spreadsheetml/main}"
TRANSITIONAL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def download(url: str, dest: Path, *, pause: float = 3.0, retries: int = 5) -> Path:
    """Fetch `url` to `dest` unless it is already there, backing off on failure."""
    if dest.exists() and dest.stat().st_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=300) as client:
                response = client.get(url)
                response.raise_for_status()
            dest.write_bytes(response.content)
            return dest
        except httpx.HTTPError as error:
            if attempt == retries - 1:
                raise SystemExit(f"download failed after {retries} attempts: {url}: {error}") from error
            time.sleep(pause * 2**attempt)
    raise SystemExit(f"download failed: {url}")


def is_unified_ideograph(code_point: int) -> bool:
    """True for the CJK unified ideograph blocks (URO, Extension A, Extensions B and up)."""
    return 0x3400 <= code_point <= 0x4DBF or 0x4E00 <= code_point <= 0x9FFF or 0x20000 <= code_point <= 0x323AF


def is_bmp_ideograph(code_point: int) -> bool:
    return 0x4E00 <= code_point <= 0x9FFF


HEX = re.compile(r"^U\+([0-9A-Fa-f]{4,6})$")


def character(code_point: str) -> str:
    """`"U+56FD"` -> `"国"`; anything else comes back unchanged."""
    match = HEX.match(code_point)
    return chr(int(match.group(1), 16)) if match else code_point


def compatibility_rows(text: str) -> list[tuple[str, str]]:
    """(compatibility character, unified ideograph) from UnicodeData.txt fields 1 and 5."""
    rows = []
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) < 6:
            continue
        codepoint, name, decomposition = int(fields[0], 16), fields[1], fields[5]
        parts = decomposition.split()
        if not parts:
            continue
        if name.startswith("CJK COMPATIBILITY IDEOGRAPH-"):
            target = parts[-1] if len(parts) == 1 or parts[0] == "<compat>" else None
        elif parts[0] == "<compat>" and len(parts) == 2:
            target = parts[1]
        else:
            continue
        if target is None or not re.fullmatch(r"[0-9A-F]{4,6}", target):
            continue
        if not is_unified_ideograph(int(target, 16)):
            continue
        rows.append((chr(codepoint), chr(int(target, 16))))
    return rows


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    number = 0
    for letter in letters.group(0) if letters else "":
        number = number * 26 + (ord(letter) - 64)
    return number - 1


def shared_strings(archive: zipfile.ZipFile, namespace: str) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    strings = []
    with archive.open("xl/sharedStrings.xml") as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag == namespace + "si":
                strings.append("".join(node.text or "" for node in element.iter(namespace + "t")))
                element.clear()
    return strings


def first_sheet(archive: zipfile.ZipFile, namespace: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f"{namespace}sheets/{namespace}sheet")
    if sheet is None:
        raise SystemExit("the MJ workbook has no sheet")
    identifier = sheet.get(f"{REL_NS}id")
    if not identifier:
        return "xl/worksheets/sheet1.xml"
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in rels:
        if relation.get("Id") == identifier:
            return "xl/" + relation.get("Target").lstrip("/").removeprefix("xl/")
    return "xl/worksheets/sheet1.xml"


def read_xlsx(path: Path) -> list[list[str]]:
    """Read the first worksheet of an xlsx (strict or transitional OOXML) as text rows."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        namespace = STRICT if root.tag.startswith(STRICT) else TRANSITIONAL
        strings = shared_strings(archive, namespace)
        rows: list[list[str]] = []
        with archive.open(first_sheet(archive, namespace)) as handle:
            row: dict[int, str] = {}
            for _, element in ET.iterparse(handle, events=("end",)):
                if element.tag == namespace + "c":
                    kind, value = element.get("t"), element.find(namespace + "v")
                    inline = element.find(namespace + "is")
                    if kind == "s" and value is not None:
                        cell = strings[int(value.text)]
                    elif kind == "inlineStr" and inline is not None:
                        cell = "".join(node.text or "" for node in inline.iter(namespace + "t"))
                    else:
                        cell = value.text if value is not None else ""
                    row[column_index(element.get("r", "A1"))] = cell or ""
                    element.clear()
                elif element.tag == namespace + "row":
                    width = max(row) + 1 if row else 0
                    rows.append([row.get(index, "") for index in range(width)])
                    row = {}
                    element.clear()
    return rows


def main_table(rows: list[list[str]]) -> tuple[list[str], list[dict[str, str]]]:
    """The MJ文字情報一覧表 rows keyed by the Japanese column names."""
    if not rows:
        raise SystemExit("the MJ main table is empty")
    header = rows[0]
    missing = [name for name in MJ_COLUMNS if name not in header]
    if missing:
        raise SystemExit(f"the MJ main table has no {missing} column; found {header}")
    records = []
    for row in rows[1:]:
        values = row + [""] * (len(header) - len(row))
        record = dict(zip(header, values, strict=False))
        if record["MJ文字図形名"]:
            records.append(record)
    return header, records


def itaiji_rows(records: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Characters that share one JIS X 0213 面区点, ordered by code point."""
    groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record["X0213"] and record["対応するUCS"]:
            groups[record["X0213"]].add(record["対応するUCS"])
    pairs = set()
    for code_points in groups.values():
        ordered = sorted(character(code_point) for code_point in code_points)
        for first in range(len(ordered)):
            for second in range(first + 1, len(ordered)):
                pairs.add((ordered[first], ordered[second]))
    return sorted(pairs)


def shinji_kyuji_rows(shrunk: dict, records: list[dict[str, str]]) -> list[tuple[str, str]]:
    """(旧字, 常用漢字 新字) pairs from the MJ縮退マップ 親字・正字 relation."""
    by_name = {record["MJ文字図形名"]: record for record in records}
    by_code_point = {}
    for record in records:
        by_code_point.setdefault(record["対応するUCS"], record)
    pairs = set()
    for entry in shrunk["content"]:
        old = by_name.get(entry["MJ文字図形名"])
        if old is None or not old["対応するUCS"] or not old["X0213"]:
            continue
        if not is_bmp_ideograph(ord(character(old["対応するUCS"]))):
            continue
        for target in entry.get("法務省戸籍法関連通達・通知", []):
            if target.get("種別") != SHRINK_KIND:
                continue
            new = by_code_point.get(target["UCS"])
            if new is None or new["漢字施策"] != "常用漢字" or new["対応するUCS"] == old["対応するUCS"]:
                continue
            if not new["X0213"] or new["X0213"] == old["X0213"]:
                continue
            pairs.add((character(old["対応するUCS"]), character(new["対応するUCS"])))
    return sorted(pairs)


def provenance(ucd: dict, shrink: dict, paths: dict[str, Path], counts: dict[str, int]) -> list[str]:
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    return [
        "# Character equivalences for the alignment and export policies.",
        (
            f"# source unicode-ucd (compatibility): {ucd['name']} (UnicodeData.txt), fields Name and "
            f"Decomposition, {ucd['licence']} ({ucd['licence_evidence']}); {ucd['attribution']}"
        ),
        f"#   {UNICODEDATA_URL} sha256:{digest(paths['ucd'])}",
        (
            "#   a: a CJK compatibility ideograph or another character whose field 5 is a <compat> "
            "decomposition naming one CJK unified ideograph; b: that ideograph."
        ),
        (
            f"# source mj-main-table (itaiji): {MJ_TABLE_NAME} Ver.{MJ_TABLE_VERSION}, columns "
            + ", ".join(MJ_COLUMNS_ITAIJI)
            + f"; {MJ_TABLE_LICENCE} ({MJ_TABLE_LICENCE_URL}); {MJ_TABLE_ATTRIBUTION}"
        ),
        f"#   {MJ_TABLE_URL} sha256:{digest(paths['mj'])}",
        "#   a, b: two characters the table places at one X0213 面区点, ordered by code point.",
        (
            f"# source mj-shrink-map (shinji-kyuji): MJ縮退マップ Ver.{SHRINK_MAP_VERSION}, fields "
            + ", ".join(SHRINK_FIELDS)
            + f"; {shrink['meta']['cc:license']} (CC BY-SA 2.1 JP), corresponds to "
            f"{shrink['meta']['MJ文字情報一覧表']}"
        ),
        f"#   {SHRINK_MAP_URL} sha256:{digest(paths['shrink'])}",
        (
            "#   a: a 旧字 whose 親字・正字 is b, a 常用漢字 (漢字施策 column of the main table); "
            "both are BMP CJK unified ideographs at different X0213 面区点."
        ),
        "# columns: " + ", ".join(COLUMNS),
        "# rows: " + ", ".join(f"{kind} {counts[kind]}" for kind in KINDS),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)

    ucd_source = yaml.safe_load(UCD_SOURCE.read_text(encoding="utf-8"))
    paths = {
        "ucd": args.cache / "ucd" / "UnicodeData.txt",
        "mj": args.cache / "moji" / MJ_TABLE_URL.rsplit("/", 1)[-1],
        "shrink": args.cache / "moji" / SHRINK_MAP_URL.rsplit("/", 1)[-1],
    }
    if args.refresh:
        for path in paths.values():
            path.unlink(missing_ok=True)
    host = None
    for url, path in ((UNICODEDATA_URL, paths["ucd"]), (MJ_TABLE_URL, paths["mj"]), (SHRINK_MAP_URL, paths["shrink"])):
        if path.exists():
            continue
        if host is not None and host == urlparse(url).netloc:
            time.sleep(3.0)
        download(url, path)
        host = urlparse(url).netloc

    compat = sorted(set(compatibility_rows(paths["ucd"].read_text(encoding="utf-8"))))
    _, records = main_table(read_xlsx(paths["mj"]))
    shrink = json.loads(paths["shrink"].read_text(encoding="utf-8"))
    pairs = {
        "compatibility": compat,
        "shinji-kyuji": shinji_kyuji_rows(shrink, records),
        "itaiji": itaiji_rows(records),
    }
    counts = {kind: len(rows) for kind, rows in pairs.items()}
    for kind in KINDS:
        if not pairs[kind]:
            raise SystemExit(f"no {kind} rows; nothing is written rather than a kind without rows")

    lines = provenance(ucd_source, shrink, paths, counts)
    lines.append("\t".join(COLUMNS))
    for kind in KINDS:
        lines += [f"{a}\t{b}\t{kind}\t{SOURCE_IDS[kind]}" for a, b in pairs[kind]]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{counts} -> {args.out.relative_to(ROOT)}")
    print(f"main table columns read: {', '.join(MJ_COLUMNS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
