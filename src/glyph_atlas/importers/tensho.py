"""Import the small seal forms of 説文解字 from CODH's 篆書字体データセット.

Book TE00039 of the dataset marks the seal forms of the 陳昌治 edition (同治十二年刊) on the pages of
the 東方學デジタル圖書館 facsimile, each box filed under a Han 親字. `data/tensho/TE00039/characters.tsv`
(`scripts/build_tensho_shuowen.py`) places each box on the small seal character of Unicode 18.0 it
holds, so a box comes with its seal code point.

The book is one document. A page is a page of the facsimile, an opening of the book. A line is one
column of a page that holds a marked form; its text is those forms, since the entries below them are
not transcribed. A unit is one marked form, with no position in its line: the entries stand between
the forms, so two of them are not neighbours in the text and form no run of crops. Its code point
is the seal character's and its source string CODH's 親字; `upstream` keeps the 親字 again, the Han
character Unicode gives as its equivalent (`kSEAL_MCJK`), its place in the edition
(`kSEAL_CCZSrc`), CODH's own crop and box, and the evidence that placed it. The placing is by rule,
so a unit is `machine` until a reviewer confirms it. A box the table places on no seal character is
left out and counted.

CODH drew its boxes on page images larger than the ones the library serves. The source record gives
the width of CODH's image (`codh_page_width`), and a box is brought to the page by the ratio of the
two widths.

The library's pages are read from a directory and must be the files `pages.tsv` pins by checksum;
they are registered in the image cache under their URLs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import images, registry, tables
from ..schema import (
    Box,
    Classification,
    Dating,
    Document,
    Licence,
    Line,
    LineRole,
    Page,
    ReviewState,
    Rights,
    Script,
    Unit,
)

SOURCE_ID = "codh-tensho"
UNICODE_ID = "unicode-ucd"
BOOK = "TE00039"
DEFAULT_SOURCE = registry.SOURCES / f"{SOURCE_ID}.yaml"
DEFAULT_DATA = registry.ROOT / "data" / "tensho"
COMMAND = "atlas import tensho"
SEAL = range(0x3D000, 0x3FC40)


class TenshoSourceError(RuntimeError):
    """A page image, a table or a source record that is not what the import expects."""


def read_tsv(path: Path) -> list[dict[str, str]]:
    """The rows of a table whose leading `#` lines say where it comes from."""
    if not path.is_file():
        raise TenshoSourceError(f"{path}: no such table")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t"))


def crop_name(row: dict[str, str]) -> str:
    """The crop file of a row as the dataset names it, without its extension."""
    return f"{row['parent_code_point']}_{row['image']}_X{int(row['x']):04d}_Y{int(row['y']):04d}"


def collect(
    scans: Path,
    out: Path = Path("work/tensho"),
    *,
    book: str = BOOK,
    data: Path | None = None,
    source: Path | None = None,
    cache: Path | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    """Check the page images against their pins and write the dataset's four tables."""
    data, source = data or DEFAULT_DATA, source or DEFAULT_SOURCE
    config = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("id") != SOURCE_ID:
        raise TenshoSourceError(f"{source}: not the {SOURCE_ID} source record")
    entry = next((held for held in config.get("books") or [] if held.get("id") == book), None)
    if entry is None:
        raise TenshoSourceError(f"{source}: no book {book}")
    rows = read_tsv(Path(data) / book / "characters.tsv")
    if len(rows) != int(entry["crops"]):
        raise TenshoSourceError(f"{data}: {len(rows)} rows for the {entry['crops']} crops of {book}")

    document = _document(config, entry)
    pages, scale = _pages(document, Path(scans), read_tsv(Path(data) / book / "pages.tsv"), entry, cache)
    lines, units, left_out = [], [], 0
    columns: dict[tuple[str, int], list[dict[str, str]]] = {}
    for row in rows:
        if not row["placed"]:
            left_out += 1
            continue
        if len(row["char"]) != 1 or ord(row["char"]) not in SEAL:
            raise TenshoSourceError(f"row {row['seq']}: {row['char']!r} is not a small seal character")
        if row["code_point"] != f"U+{ord(row['char']):04X}":
            raise TenshoSourceError(f"row {row['seq']}: {row['code_point']} is not the code point of its character")
        if row["canvas"] not in pages:
            raise TenshoSourceError(f"row {row['seq']}: its page {row['canvas']} is not in pages.tsv")
        columns.setdefault((row["canvas"], int(row["column"])), []).append(row)
    for (canvas, column), held in columns.items():
        page = pages[canvas]
        boxes = [_box(row, scale[canvas], page) for row in held]
        left, top = min(box.x for box in boxes), min(box.y for box in boxes)
        line = Line(
            id=f"{page.id}:c{column:02d}",
            page_id=page.id,
            seq=len(lines),
            box=Box(x=left, y=top, w=max(box.x + box.w for box in boxes) - left,
                    h=max(box.y + box.h for box in boxes) - top),
            vertical=True,
            role=LineRole.MAIN,
            text_raw="".join(row["char"] for row in held),
            text="".join(row["char"] for row in held),
            match_method="rule-lines",
            meta={"column": column, "text_scope": "marked seal forms; the entries below them are not transcribed"},
        )
        lines.append(line)
        units += [_unit(document, page, line, row, box) for row, box in zip(held, boxes, strict=True)]

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = (("documents", [document], Document), ("pages", list(pages.values()), Page),
               ("lines", lines, Line), ("units", units, Unit))
    for name, records, model in written:
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {"source": SOURCE_ID, "documents": [document.id], "pages": len(pages), "lines": len(lines),
               "units": len(units), "left_out": left_out}
    manifest = {
        "schema_version": tables.SCHEMA_VERSION,
        "tables": {name: len(records) for name, records, _ in written},
        "command": command or COMMAND,
        "collection": summary,
    }
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary


def _pages(document: Document, scans: Path, rows: list[dict[str, str]], book: dict[str, Any],
           cache: Path | None) -> tuple[dict[str, Page], dict[str, float]]:
    """The pages by their URL, and for each the size of its image over the size of CODH's."""
    pages: dict[str, Page] = {}
    scale: dict[str, float] = {}
    for seq, row in enumerate(rows):
        file = scans / row["file"]
        if not file.is_file():
            raise TenshoSourceError(f"{file}: the image of page {row['page']} is not there")
        if hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]:
            raise TenshoSourceError(f"{file}: not the image that pages.tsv pins")
        held = images.register(file, row["canvas"], root=cache)
        if (held.width, held.height) != (int(row["width"]), int(row["height"])):
            raise TenshoSourceError(f"{file}: {held.width} x {held.height}, pinned {row['width']} x {row['height']}")
        pages[row["canvas"]] = Page(
            id=f"{document.id}:p{int(row['page']):04d}",
            document_id=document.id,
            seq=seq,
            image=held.url,
            width=held.width,
            height=held.height,
            sha256=held.sha256,
            meta={"file": row["file"], "facsimile_page": int(row["page"])},
        )
        scale[row["canvas"]] = held.width / int(book["codh_page_width"])
    return pages, scale


def _box(row: dict[str, str], scale: float, page: Page) -> Box:
    """CODH's box in the pixels of the page image, kept inside the page."""
    left, top = round(int(row["x"]) * scale), round(int(row["y"]) * scale)
    right = min(page.width, round((int(row["x"]) + int(row["w"])) * scale))
    bottom = min(page.height, round((int(row["y"]) + int(row["h"])) * scale))
    if right <= left or bottom <= top:
        raise TenshoSourceError(f"row {row['seq']}: its box lies off the page")
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def _unit(document: Document, page: Page, line: Line, row: dict[str, str], box: Box) -> Unit:
    return Unit(
        id=f"{document.id}:{int(row['seq']):05d}",
        document_id=document.id,
        page_id=page.id,
        line_id=line.id,
        seq=None,
        box=box,
        text_source=row["parent"],
        unicode=row["code_point"],
        classification=Classification.IDENTIFIED,
        script=Script.SEAL,
        style="seal",
        method="import",
        review=ReviewState.MACHINE,
        upstream={
            "source": SOURCE_ID,
            "ref": crop_name(row),
            "parent": row["parent"],
            "equivalent": row["equivalent"],
            "ccz": row["ccz"],
            "placed": row["placed"],
            "box": f"{row['x']},{row['y']},{row['w']},{row['h']}",
        },
    )


def _checked(config: dict[str, Any]) -> date | None:
    value = config.get("checked")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if isinstance(value, str) else None


def _document(config: dict[str, Any], book: dict[str, Any]) -> Document:
    # The page images are the library's, which states no terms for them; the document validator
    # records the flat reproduction of an 1873 print as public domain and keeps `unknown` as the
    # holder's terms. The boxes and 親字 are CODH's, under the dataset's licence; the seal code points
    # and their equivalents are Unicode's.
    rights = Rights(
        licence=Licence.UNKNOWN,
        holder=book.get("holder"),
        # The dataset asks a derived release to name each book's image provider and holder; this
        # line reaches the release's ATTRIBUTION.md.
        attribution=f"{config['attribution']}; 『{book['work']}』 {book['provider']}（{book['holder']}） {book['url']}",
        evidence=book["url"],
        checked=_checked(config),
    )
    table = next((entry for entry in registry.load() if entry.id == UNICODE_ID), None)
    if table is None:
        raise TenshoSourceError(f"{UNICODE_ID}: no such source record")
    text_rights = Rights(
        licence=Licence(config["licence"]),
        attribution=f"{config['attribution']}; {table.attribution} (SealSources.txt 18.0.0)",
        evidence=config.get("licence_evidence") or config["url"],
        checked=_checked(config),
    )
    return Document(
        id=f"{SOURCE_ID}:{book['id']}",
        title=book["title"],
        source_refs={SOURCE_ID: book["id"], UNICODE_ID: "SealSources.txt 18.0.0"},
        holder=book.get("holder"),
        production="printed/woodblock",
        origin="china",
        style="mixed",
        genre=["dictionary"],
        dating=[Dating(literal=book["edition"], start=int(book["year"]), end=int(book["year"]), kind="publication",
                       evidence=book["url"])],
        image_rights=rights,
        text_rights=text_rights,
        meta={
            "work_title": book.get("work"),
            "author": book.get("author"),
            "edition": book.get("edition"),
            "provider": book.get("provider"),
            "facsimile": book.get("url"),
            "source_notes": config.get("notes") or [],
        },
    )
