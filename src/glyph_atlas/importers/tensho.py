"""Import seal forms marked by CODH's 篆書字体データセット.

The dataset cuts the seal forms of twelve titles and files each under a Han 親字. A book of it is one
document here, and `data/tensho/<book>/characters.tsv` says what each of its boxes is placed on:

- a small seal character of Unicode 18.0. For TE00039, the 陳昌治 edition of 説文解字 (同治十二年刊),
  `scripts/build_tensho_shuowen.py` matches the order of the book with the order of the edition's
  characters in Unicode. For the other books `scripts/build_tensho_equivalents.py` takes the one seal
  character whose equivalent (`kSEAL_MCJK`) is the 親字, where the title files no other crop under
  that 親字, or the seal character of the 親字 the crop is drawn as, or the one its drawing was read as.
  The unit's code point is the seal character's and its source string the 親字. The placing is by rule, so the unit is `machine` until a reviewer
  confirms it.
- the 親字 itself (`placed` is `parent`), where no seal character answers it or the crop is drawn as none
  that does.
  The unit is a Han character in seal style, on CODH's own reading.
- nothing (`placed` is empty). The box is left out and counted.

A box is also left out, and counted apart, when the holder has replaced the image of its page since
CODH cut it: the book's `replaced_pages` in the source record names those pages, and such a page is
not imported.

A page is a page of the holder's reproduction, an opening of the book. `pages.tsv` names it in one of
two ways. Where the holder serves it through IIIF the table gives its canvas, image service and size
as the holder's manifest states them, and the import reaches for no image. Where it is a plain file (TE00039, on the 東方學デジタル圖書館
facsimile) the table pins the file by checksum; the files are read from a directory and registered in
the image cache under their URLs. CODH drew the boxes of that book on images larger than the library
serves: the source record gives the width of CODH's image (`codh_page_width`), and a box is brought to
the page by the ratio of the two widths.

A line is one column of a page that holds a marked form; its text is those forms, since the rest of
the column is not transcribed. A unit is one marked form, with no position in its line: other
writing stands between the forms, so two of them are not neighbours in the text and form no run of
crops. `upstream` keeps the 親字, CODH's own crop and box, what placed the unit, and for a seal
character its equivalent and, in 説文解字, its place in the edition (`kSEAL_CCZSrc`).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import images, page_sources, registry, tables
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
DEFAULT_SOURCE = registry.SOURCES / f"{SOURCE_ID}.yaml"
DEFAULT_DATA = registry.ROOT / "data" / "tensho"
COMMAND = "atlas import tensho"
SEAL = range(0x3D000, 0x3FC40)
#: The value of `placed` on a box that stays on its 親字; any other value names the evidence for a seal
#: character.
PARENT = "parent"


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
    books: Sequence[str],
    out: Path = Path("work/codh-tensho"),
    *,
    scans: Path | None = None,
    data: Path | None = None,
    source: Path | None = None,
    cache: Path | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    """Write the four tables of `books`. `scans` holds the page images of a book whose pages are files."""
    data, source = Path(data or DEFAULT_DATA), Path(source or DEFAULT_SOURCE)
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("id") != SOURCE_ID:
        raise TenshoSourceError(f"{source}: not the {SOURCE_ID} source record")
    if not books or len(set(books)) != len(books):
        raise TenshoSourceError("name each book to import once")

    documents: list[Document] = []
    pages: list[Page] = []
    lines: list[Line] = []
    units: list[Unit] = []
    left_out = replaced = 0
    for book in books:
        entry = next((held for held in config.get("books") or [] if held.get("id") == book), None)
        if entry is None:
            raise TenshoSourceError(f"{source}: no book {book}")
        rows = read_tsv(data / book / "characters.tsv")
        if len(rows) != int(entry["crops"]):
            raise TenshoSourceError(f"{data}: {len(rows)} rows for the {entry['crops']} crops of {book}")
        document = _document(config, entry)
        listed = read_tsv(data / book / "pages.tsv")
        gone = {int(number) for number in entry.get("replaced_pages") or []}
        if not gone <= {int(row["page"]) for row in listed}:
            raise TenshoSourceError(f"{source}: {book} names a replaced page that pages.tsv does not hold")
        other = {row["canvas"] for row in listed if int(row["page"]) in gone}
        held, scale = _pages(document, [row for row in listed if row["canvas"] not in other], entry, scans, cache)
        columns: dict[tuple[str, int], list[dict[str, str]]] = {}
        first = len(lines)
        for row in rows:
            if row["canvas"] in other:
                replaced += 1
                continue
            if not row["placed"]:
                left_out += 1
                continue
            _check(row)
            if row["canvas"] not in held:
                raise TenshoSourceError(f"row {row['seq']}: its page {row['canvas']} is not in pages.tsv")
            columns.setdefault((row["canvas"], int(row["column"])), []).append(row)
        for (canvas, column), marked in columns.items():
            page = held[canvas]
            boxes = [_box(row, scale[canvas], page) for row in marked]
            left, top = min(box.x for box in boxes), min(box.y for box in boxes)
            text = "".join(row["char"] or row["parent"] for row in marked)
            line = Line(
                id=f"{page.id}:c{column:02d}",
                page_id=page.id,
                seq=len(lines) - first,
                box=Box(x=left, y=top, w=max(box.x + box.w for box in boxes) - left,
                        h=max(box.y + box.h for box in boxes) - top),
                vertical=True,
                role=LineRole.MAIN,
                text_raw=text,
                text=text,
                match_method="rule-lines",
                meta={"column": column, "text_scope": entry["text_scope"]},
            )
            lines.append(line)
            units += [_unit(document, page, line, row, box) for row, box in zip(marked, boxes, strict=True)]
        documents.append(document)
        pages += held.values()

    out = Path(out)
    _keeps(out, documents)
    out.mkdir(parents=True, exist_ok=True)
    written = (("documents", documents, Document), ("pages", pages, Page), ("lines", lines, Line),
               ("units", units, Unit))
    for name, records, model in written:
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    seal = sum(unit.script is Script.SEAL for unit in units)
    summary = {"source": SOURCE_ID, "documents": [document.id for document in documents], "pages": len(pages),
               "lines": len(lines), "units": len(units), "seal": seal, "han": len(units) - seal,
               "left_out": left_out, "replaced": replaced}
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


def _keeps(out: Path, documents: Sequence[Document]) -> None:
    """Refuse to write over a dataset that holds a book the import leaves out: what reads the dataset
    (`atlas dates`) would take the book's facts back."""
    held = out / "documents.parquet"
    if not held.exists():
        return
    if lost := sorted({document.id for document in tables.read(held, Document)} - {document.id for document in documents}):
        raise TenshoSourceError(f"{out} holds {', '.join(lost)}: import those too, or write elsewhere")


def _check(row: dict[str, str]) -> None:
    """Refuse a row whose characters are not what its `placed` says."""
    if row["placed"] == PARENT:
        if row["char"] or row["code_point"]:
            raise TenshoSourceError(f"row {row['seq']}: on its 親字 and on {row['code_point']} at once")
        if len(row["parent"]) != 1 or row["parent_code_point"] != f"U+{ord(row['parent']):04X}":
            raise TenshoSourceError(f"row {row['seq']}: {row['parent_code_point']} is not the code point of its 親字")
        return
    if len(row["char"]) != 1 or ord(row["char"]) not in SEAL:
        raise TenshoSourceError(f"row {row['seq']}: {row['char']!r} is not a small seal character")
    if row["code_point"] != f"U+{ord(row['char']):04X}":
        raise TenshoSourceError(f"row {row['seq']}: {row['code_point']} is not the code point of its character")


def _pages(document: Document, rows: list[dict[str, str]], book: dict[str, Any], scans: Path | None,
           cache: Path | None) -> tuple[dict[str, Page], dict[str, float]]:
    """The pages by their canvas, and for each the size of its image over the size of CODH's."""
    pages: dict[str, Page] = {}
    scale: dict[str, float] = {}
    for row in rows:
        number = int(row["page"])
        identity = {"id": f"{document.id}:p{number:04d}", "document_id": document.id,
                    "seq": number - 1}    # the page number shown is the reproduction's own image number
        if "image" in row:
            if images.service_of(row["image"]) is None:
                raise TenshoSourceError(f"page {number}: {row['image']} is no image service")
            page = Page(**identity, image=row["image"], width=int(row["width"]), height=int(row["height"]),
                        canvas=row["canvas"])
        else:
            page = _scanned(identity, row, scans, cache)
        pages[row["canvas"]] = page
        scale[row["canvas"]] = page.width / int(book["codh_page_width"]) if "codh_page_width" in book else 1.0
    return pages, scale


def _scanned(identity: dict[str, Any], row: dict[str, str], scans: Path | None, cache: Path | None) -> Page:
    """A page whose image is a file of `scans`, the one the table pins."""
    if scans is None:
        raise TenshoSourceError(f"{identity['document_id']}: its pages are files; name their directory")
    file = Path(scans) / row["file"]
    if not file.is_file():
        raise TenshoSourceError(f"{file}: the image of page {row['page']} is not there")
    if hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]:
        raise TenshoSourceError(f"{file}: not the image that pages.tsv pins")
    held = images.register(file, row["canvas"], root=cache)
    if (held.width, held.height) != (int(row["width"]), int(row["height"])):
        raise TenshoSourceError(f"{file}: {held.width} x {held.height}, pinned {row['width']} x {row['height']}")
    return Page(**identity, image=held.url, width=held.width, height=held.height, sha256=held.sha256,
                meta={"file": row["file"], "facsimile_page": int(row["page"])})


def _box(row: dict[str, str], scale: float, page: Page) -> Box:
    """CODH's box in the pixels of the page image, kept inside the page."""
    left, top = max(0, round(int(row["x"]) * scale)), max(0, round(int(row["y"]) * scale))
    right = min(page.width, round((int(row["x"]) + int(row["w"])) * scale))
    bottom = min(page.height, round((int(row["y"]) + int(row["h"])) * scale))
    if right <= left or bottom <= top:
        raise TenshoSourceError(f"row {row['seq']}: its box lies off the page")
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def _unit(document: Document, page: Page, line: Line, row: dict[str, str], box: Box) -> Unit:
    on_parent = row["placed"] == PARENT
    placing = {name: row[name] for name in ("equivalent", "ccz") if row[name]}
    return Unit(
        id=f"{document.id}:{int(row['seq']):05d}",
        document_id=document.id,
        page_id=page.id,
        line_id=line.id,
        seq=None,
        box=box,
        text_source=row["parent"],
        unicode=row["parent_code_point"] if on_parent else row["code_point"],
        classification=Classification.IDENTIFIED,
        script=Script.HAN if on_parent else Script.SEAL,
        style="seal",
        method="import",
        # The 親字 is CODH's reading of the form; a seal character is an inference from it, the
        # order of the book or the drawing.
        review=ReviewState.TRANSCRIBER if on_parent else ReviewState.MACHINE,
        upstream={
            "source": SOURCE_ID,
            "ref": crop_name(row),
            "parent": row["parent"],
            **placing,
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
    # The page images are the holder's. `image_licence` is what the holder states for them; a holder
    # that states nothing leaves `unknown`, which the document validator turns into public domain for
    # the flat reproduction of a print older than 1900, keeping `unknown` as the holder's terms. The
    # boxes and 親字 are CODH's, under the dataset's licence; the seal code points and their
    # equivalents are Unicode's.
    rights = Rights(
        licence=Licence(book.get("image_licence", Licence.UNKNOWN.value)),
        holder=book.get("holder"),
        # The dataset asks a derived release to name each book's image provider and holder; this
        # line reaches the release's ATTRIBUTION.md.
        attribution=(f"{config['attribution']}; 『{book['work']}』 {book['provider']}（{book['holder']}） "
                     + " ".join(book.get("provider_urls") or [book["url"]])),
        # A book names the page that states its holder's terms and the day it was read, where that is
        # not the book's own record and the day the dataset was read.
        evidence=book.get("rights_evidence") or book["url"],
        checked=_checked(book) or _checked(config),
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
        source_refs={SOURCE_ID: book["id"], UNICODE_ID: "SealSources.txt 18.0.0",
                     **({page_sources.MANIFEST_REF: book["manifest"]} if book.get("manifest") else {})},
        holder=book.get("holder"),
        production=book["production"],
        origin=book["origin"],
        style="mixed",
        genre=["dictionary"],
        # `year` is the printing's; a book whose printing no source dates has none. The date of a book with
        # a manifest is the manifest's own statement, which `atlas dates` reads there and credits to its
        # holder, so it names no evidence here.
        dating=[Dating(literal=book.get("dated") or book["edition"], start=int(book["year"]), end=int(book["year"]),
                       kind="publication", evidence=None if book.get("manifest") else book["url"])]
        if "year" in book else [],
        image_rights=rights,
        text_rights=text_rights,
        meta={
            "work_title": book.get("work"),
            "author": book.get("author"),
            **({"publisher": book["publisher"]} if book.get("publisher") else {}),
            "edition": book.get("edition"),
            "provider": book.get("provider"),
            "facsimile": book.get("url"),
            "source_notes": config.get("notes") or [],
        },
    )
