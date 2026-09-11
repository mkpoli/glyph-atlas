"""Build data/vocab/mj-hentaigana.tsv from the MJ文字情報一覧表 変体仮名編.

The 変体仮名編 lists every 変体仮名 glyph the 文字情報技術促進協議会 registered: an MJ図形名, the
Unicode code point where one exists, the Unicode character name, the 字母 and its code point, the
音価 (up to three), the 戸籍統一文字番号, the 学術用変体仮名番号, the 国語研 URL and a note. The
thirteen rows without a code point name in 備考 the MJ図形名 they were unified into.

The workbook is downloaded as an ODS (`odfpy`), so the script needs the `data` extra:

    uv sync --extra data
    .venv/bin/python scripts/build_mj_table.py

Network access happens here and nowhere else; the file is cached under `cache/moji/`.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "sources" / "mj-hentaigana.yaml"
TARGET = ROOT / "data" / "vocab" / "mj-hentaigana.tsv"
CACHE = ROOT / "cache" / "moji"
USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
#: size of MJIH00201-ods.zip as published; the download is checked against it.
EXPECTED_BYTES = 240_803
COLUMNS = [
    "mj",
    "code_point",
    "name",
    "jibo",
    "jibo_code_point",
    "readings",
    "koseki",
    "gakujutsu",
    "ninjal_url",
    "note",
]


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


def odf_modules() -> tuple[Any, Any, Any]:
    """Import odfpy, or explain which dependency is missing."""
    try:
        from odf import opendocument, table, text
    except ImportError as error:  # pragma: no cover - only on a machine without the extra
        raise SystemExit(
            "odfpy is not installed; the 変体仮名編 is an ODS workbook. Install the data extra:\n"
            "    uv sync --extra data\n"
            "or:  .venv/bin/python -m pip install odfpy"
        ) from error
    return opendocument, table, text


def cell_text(cell: Any) -> str:
    """The text of one ODS cell with ruby annotations dropped.

    Header cells carry furigana as `text:ruby` (図形 with ズケイ); the ruby text is not part of
    the column name.
    """
    _, _, text = odf_modules()
    parts: list[str] = []

    def walk(node: Any) -> None:
        for child in node.childNodes:
            qname = getattr(child, "qname", None)
            if qname is not None:
                if qname[1] == "ruby-text":
                    continue
                walk(child)
            elif hasattr(child, "data"):
                parts.append(str(child))

    for paragraph in cell.getElementsByType(text.P):
        walk(paragraph)
        parts.append("\n")
    return "".join(parts).rstrip("\n")


def sheet_rows(path: Path) -> list[list[str]]:
    """Every row of the first sheet as a list of cell strings, repeats expanded."""
    opendocument, table, _ = odf_modules()
    document = opendocument.load(str(path))
    tables = document.spreadsheet.getElementsByType(table.Table)
    if not tables:
        raise SystemExit(f"no table in {path}")
    rows = []
    for row in tables[0].getElementsByType(table.TableRow):
        values: list[str] = []
        for cell in row.getElementsByType(table.TableCell):
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
            values.extend([cell_text(cell)] * repeat)
        while values and not values[-1]:
            values.pop()
        if values:
            rows.append(values)
    return rows


def build(source: dict, rows: list[list[str]]) -> list[dict[str, str]]:
    """Map the ODS rows to the output columns through the 日本語 header names."""
    expected = list(source["format"]["columns"])
    if not rows:
        raise SystemExit("the 変体仮名編 sheet is empty")
    header = rows[0][: len(expected)]
    if header != expected:
        raise SystemExit(f"unexpected columns:\n  found:    {header}\n  expected: {expected}")
    index = {name: position for position, name in enumerate(expected)}
    records = []
    for row in rows[1:]:
        values = row + [""] * (len(expected) - len(row))
        readings = [values[index[k]] for k in ("音価１", "音価２", "音価３") if values[index[k]]]
        records.append(
            {
                "mj": values[index["MJ文字図形名"]],
                "code_point": values[index["UCS符号位置"]],
                "name": values[index["CharacterName"]],
                "jibo": values[index["字母"]],
                "jibo_code_point": values[index["字母のUCS符号位置"]],
                "readings": "/".join(readings),
                "koseki": values[index["戸籍統一文字番号"]],
                "gakujutsu": values[index["学術用変体仮名番号"]],
                "ninjal_url": values[index["国語研URL"]],
                "note": values[index["備考"]],
            }
        )
    return records


def provenance(source: dict, archive: Path, rows: list[dict[str, str]]) -> list[str]:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with_code_point = sum(1 for row in rows if row["code_point"])
    return [
        f"# {source['name']} Ver.{source['version']}",
        f"# publisher: {source['publisher']}",
        f"# url: {source['url']}",
        f"# access: {source['access']['ods']}",
        f"# archive: {archive.name}, {archive.stat().st_size} bytes, sha256 {digest}",
        f"# licence: {source['licence']} ({source['licence_evidence']})",
        f"# attribution: {source['attribution']}",
        "# columns: " + ", ".join(COLUMNS),
        "# ODS columns read: " + ", ".join(source["format"]["columns"]),
        "# readings joins 音価１, 音価２ and 音価３ with /; note carries 備考 verbatim.",
        f"# rows: {len(rows)}, with a code point: {with_code_point}, without: {len(rows) - with_code_point}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)

    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    url = source["access"]["ods"]
    archive = args.cache / url.rsplit("/", 1)[-1]
    if args.refresh and archive.exists():
        archive.unlink()
    download(url, archive)
    size = archive.stat().st_size
    if size != EXPECTED_BYTES:
        print(f"warning: {archive.name} is {size} bytes, the pinned size is {EXPECTED_BYTES}", file=sys.stderr)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if name.lower().endswith(".ods")]
        if len(names) != 1:
            raise SystemExit(f"expected one ODS in {archive.name}, found {names}")
        workbook = args.cache / archive.stem / names[0]
        workbook.parent.mkdir(parents=True, exist_ok=True)
        if not workbook.exists():
            workbook.write_bytes(bundle.read(names[0]))

    rows = build(source, sheet_rows(workbook))
    counts = source.get("counts", {})
    with_cp = sum(1 for row in rows if row["code_point"])
    if counts and (len(rows) != counts["rows"] or with_cp != counts["with_code_point"]):
        print(
            f"warning: the workbook has {len(rows)} rows, {with_cp} with a code point; "
            f"{SOURCE.name} records {counts['rows']} and {counts['with_code_point']}",
            file=sys.stderr,
        )

    lines = provenance(source, archive, rows)
    lines.append("\t".join(COLUMNS))
    lines += ["\t".join(row[column] for column in COLUMNS) for row in rows]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} rows, {with_cp} with a code point -> {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
