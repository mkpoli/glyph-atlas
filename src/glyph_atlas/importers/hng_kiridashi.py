"""Import the HNG 切り出しデータ: character boxes on the Gallica images of two Dunhuang manuscripts.

The clone holds one TSV per source, `H08_myz_P2334.tsv` and `H12_fhs_P2195.tsv`. Header lines start
with `#` and give the IIIF manifest, the HNG code and the HNG source id; each body row is one box:
osid, sid, page (the Gallica folio, `f2`), ln (the column on the page, from the right), cn (the
character in the column), x, y, w, h, char, hng-gid (the 代表字形ID of the source in the HNG basic
dataset), flg (0 unspecified, 1 = a, 2 = b…), remarks, img (the IIIF info.json), mx, my (the image
size the box is measured on) and canvas.

Each file becomes one document, the manuscript as Gallica shows it, with title, date and category
from the HNG source list in `data/sources/hng-basic-data.yaml`. Its image rights are Gallica's
conditions of use, which the licence vocabulary resolves to `restricted`; its text rights are the
CC BY-SA 4.0 of the HNG data. Each folio becomes a page, each column a line whose box encloses its
characters, and each row a unit with its box. A row that repeats the page, box and character of an
earlier row is a second entry of the same box and is counted under `duplicates`; a row with no
character is counted under `blank`. Where two characters share one box, one of them was saved with
its neighbour's box: both units are `disputed` and name each other in `upstream["shares_box_with"]`,
and `shared_boxes` counts them. A box drawn past the edge of its page is cut back to the page, the
drawn box kept in `upstream["box_drawn"]`, and counted under `clamped`.

A unit whose hng-gid is set keeps it in `upstream["hng_gid"]` and names the crops of the basic dataset
it was filed under in `upstream["hng_unit"]`, comma-separated. A gid may list several cards
(`0642a/0642b`); a card with a letter is taken as it stands, and one without takes the letter of flg
(1 = a, 2 = b…). Only crops the basic dataset holds are named: a card without a letter on a
character with several 字体 and flg 0 does not say which crop it is, and is counted under
`unlinked` with every other gid that names no crop.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .. import rights, tables
from ..registry import SOURCES
from ..schema import Box, Classification, Document, Line, Page, ReviewState, Rights, Script, Unit
from . import hng
from .honkoku_data import cache_root, clone_revision

SOURCE = "hng-kiridashi-data"
SOURCE_FILE = SOURCES / f"{SOURCE}.yaml"
SOURCE_PREFIX = "hng-kiridashi"
BNF = "Bibliothèque nationale de France"
COLUMNS = ("osid", "sid", "page", "ln", "cn", "x", "y", "w", "h", "char", "hng-gid", "flg", "remarks",
           "img", "mx", "my", "canvas")
HEADER = re.compile(r"^#\s*(?P<key>[^\t=]+?)\s*\t=\s*(?P<value>.*)$")
FOLIO = re.compile(r"^f(?P<n>\d+)$")


class RevisionError(ValueError):
    """The clone is not at the commit the source file pins."""


@dataclass(frozen=True)
class Sheet:
    """One TSV: its header fields and its rows as dicts keyed by `COLUMNS`."""

    name: str
    manifest: str
    code: str
    source: str
    rows: list[dict[str, str]]


def source_file() -> dict:
    """`data/sources/hng-kiridashi-data.yaml`: the licence, the pinned commit and the image terms."""
    return yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))


def default_clone() -> Path:
    """`cache/hng-kiridashi-data`, the clone `atlas import hng-kiridashi` reads without `--clone`."""
    return cache_root() / SOURCE


def read_sheet(path: Path) -> Sheet:
    """The header fields and the rows of one TSV."""
    header: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            if match := HEADER.match(line):
                header[match["key"]] = match["value"].strip()
            continue
        if line.strip():
            cells = line.split("\t")
            rows.append(dict(zip(COLUMNS, [cell.strip() for cell in cells], strict=False)))
    return Sheet(path.name, header["manifest"], header["HNG code"], header["HNG source ID"], rows)


def document_of(sheet: Sheet, entry: dict, raw: dict) -> Document:
    """The manuscript on Gallica: the HNG basic dataset's description, Gallica's image rights."""
    basic = hng.document_of(entry, hng.source_file())
    image_rights = rights.resolve(url=raw["image_terms"], holder=BNF)
    image_rights = Rights(
        licence=image_rights.licence,
        holder=BNF,
        attribution=raw["image_attribution"],
        evidence=raw["image_terms"],
        checked=image_rights.checked,
    )
    text_rights = Rights(
        licence=basic.text_rights.licence,
        holder=hng.HOLDER,
        attribution=raw["attribution"],
        evidence=raw["licence_evidence"],
        checked=basic.text_rights.checked,
    )
    return basic.model_copy(update={
        "id": f"{SOURCE_PREFIX}:{sheet.source}",
        "source_refs": {SOURCE: sheet.code, hng.SOURCE: entry["code"], "iiif-manifest": sheet.manifest},
        "image_rights": image_rights,
        "text_rights": text_rights,
        "meta": {**basic.meta, "manifest": sheet.manifest, "hng_document": basic.id},
    })



def records_of(
    sheet: Sheet, document: Document, counts: Counter[str], glyphs: set[str]
) -> tuple[list[Page], list[Line], list[Unit]]:
    """Pages, lines and units of one sheet, duplicates of an earlier box counted and left out."""
    pages: dict[str, Page] = {}
    columns: dict[tuple[str, int], list[Unit]] = {}
    units: list[Unit] = []
    seen: set[tuple[str, ...]] = set()
    boxes: dict[tuple[str, ...], list[Unit]] = {}
    for row in sheet.rows:
        if not row["char"]:
            counts["blank"] += 1
            continue
        key = (row["page"], row["x"], row["y"], row["w"], row["h"], row["char"])
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)
        page = pages.get(row["page"])
        if page is None:
            folio = FOLIO.match(row["page"])
            page = Page(
                id=f"{document.id}:{row['page']}",
                document_id=document.id,
                seq=int(folio["n"]) if folio else len(pages) + 1,
                canvas=row["canvas"] or None,
                image=row["img"].removesuffix("/info.json"),
                width=int(row["mx"]),
                height=int(row["my"]),
                transcription={"source": SOURCE, "entry": sheet.code},
            )
            pages[row["page"]] = page
        column = int(row["ln"])
        unit = unit_of(row, sheet, page, column, glyphs)
        if clamp(unit, page):
            counts["clamped"] += 1
        columns.setdefault((row["page"], column), []).append(unit)
        boxes.setdefault(key[:5], []).append(unit)
        units.append(unit)
    for sharing in boxes.values():
        if len(sharing) > 1:
            counts["shared_boxes"] += len(sharing)
            for unit in sharing:
                others = ",".join(other.upstream["sid"] for other in sharing if other is not unit)
                unit.upstream["shares_box_with"] = others
                unit.review = ReviewState.DISPUTED
    lines = [line_of(pages[folio], column, members) for (folio, column), members in sorted(
        columns.items(), key=lambda item: (pages[item[0][0]].seq, item[0][1]))]
    return list(pages.values()), lines, units


def targets(source: str, gid: str, flag: str, glyphs: set[str]) -> list[str]:
    """The basic-dataset unit ids a gid names, by the rules of the module docstring."""
    number = int(flag or 0)
    letter = "abcdefghij"[number - 1] if 0 < number <= 10 else ""
    found = []
    for card in (part.strip() for part in gid.split("/")):
        glyph = card if card[-1:].isalpha() else f"{card}{letter}"
        if glyph in glyphs:
            found.append(f"hng:{source}:{glyph}")
    return found


def unit_of(row: dict[str, str], sheet: Sheet, page: Page, column: int, glyphs: set[str]) -> Unit:
    """One box as a unit on its page and in its column."""
    char = row["char"]
    code_point = f"U+{ord(char):04X}" if len(char) == 1 else None
    upstream = {"source": SOURCE, "ref": f"{sheet.code}:{row['sid']}", "osid": row["osid"], "sid": row["sid"]}
    if row["hng-gid"]:
        upstream["hng_gid"] = row["hng-gid"]
        if found := targets(sheet.source, row["hng-gid"], row["flg"], glyphs):
            upstream["hng_unit"] = ",".join(found)
    if row["remarks"]:
        upstream["remarks"] = row["remarks"]
    return Unit(
        id=f"{SOURCE_PREFIX}:{sheet.source}:{row['sid']}",
        document_id=page.document_id,
        page_id=page.id,
        line_id=f"{page.id}:l{column}",
        seq=int(row["cn"]),
        box=Box(x=int(row["x"]), y=int(row["y"]), w=int(row["w"]), h=int(row["h"])),
        text_source=char,
        reading=char,
        unicode=code_point,
        classification=Classification.IDENTIFIED if code_point else Classification.UNIDENTIFIED,
        script=Script.HAN,
        method="import",
        review=ReviewState.TRANSCRIBER,
        upstream=upstream,
    )


def clamp(unit: Unit, page: Page) -> bool:
    """Cut a box that runs past the page edge back to the page, keeping the drawn box in upstream."""
    box = unit.box
    left, top = max(0, box.x), max(0, box.y)
    right, bottom = min(page.width, box.x + box.w), min(page.height, box.y + box.h)
    if (left, top, right, bottom) == (box.x, box.y, box.x + box.w, box.y + box.h):
        return False
    unit.upstream["box_drawn"] = f"{box.x},{box.y},{box.w},{box.h}"
    unit.box = Box(x=left, y=top, w=right - left, h=bottom - top)
    return True


def line_of(page: Page, column: int, units: list[Unit]) -> Line:
    """A column: the box that encloses its characters and their text in reading order."""
    ordered = sorted(units, key=lambda unit: (unit.seq, unit.box.y))
    left = min(unit.box.x for unit in units)
    top = min(unit.box.y for unit in units)
    right = max(unit.box.x + unit.box.w for unit in units)
    bottom = max(unit.box.y + unit.box.h for unit in units)
    text = "".join(unit.text_source or "" for unit in ordered)
    return Line(
        id=f"{page.id}:l{column}",
        page_id=page.id,
        seq=column,
        box=Box(x=left, y=top, w=right - left, h=bottom - top),
        vertical=True,
        text_raw=text,
        text=text,
        match_method="import",
    )


def basic_glyphs(basic: Path, sources: list[str]) -> dict[str, set[str]]:
    """The crops the HNG basic dataset holds for each source, as its importer names them."""
    entries = [entry for entry in hng.source_file()["documents"] if entry["id"] in sources]
    found: dict[str, set[str]] = {source: set() for source in sources}
    for crop in hng.crops_of(Path(basic), entries, Counter()):
        found[crop.source].add(crop.glyph)
    return found


def import_all(
    out: Path, *, clone: Path | None = None, basic: Path | None = None, limit: int | None = None
) -> dict[str, int]:
    """Import every TSV of the clone into `out` and return the row counts and the duplicates.

    `basic` is a clone of the HNG basic dataset, `cache/hng-basic-data` by default, read to name the
    crops each box was filed under. Raises `RevisionError` when the clone is a git checkout at another
    commit than the source file pins, and `KeyError` when a sheet names an HNG source the basic
    dataset's source file lacks.
    """
    out, clone = Path(out), Path(clone) if clone is not None else default_clone()
    raw = source_file()
    found = clone_revision(clone)
    if found is not None and found != raw["revision"]:
        raise RevisionError(f"{clone} is at {found}; {SOURCE_FILE.name} pins {raw['revision']}")
    entries = {entry["id"]: entry for entry in hng.source_file()["documents"]}
    counts: Counter[str] = Counter(duplicates=0, blank=0, shared_boxes=0, clamped=0)
    documents: list[Document] = []
    pages: list[Page] = []
    lines: list[Line] = []
    units: list[Unit] = []
    sheets = [read_sheet(path) for path in sorted(clone.glob("H*.tsv"))]
    glyphs = basic_glyphs(basic if basic is not None else hng.default_clone(), [sheet.source for sheet in sheets])
    for sheet in sheets:
        if limit is not None:
            sheet = Sheet(sheet.name, sheet.manifest, sheet.code, sheet.source, sheet.rows[: max(0, limit - len(units))])
        document = document_of(sheet, entries[sheet.source], raw)
        sheet_pages, sheet_lines, sheet_units = records_of(sheet, document, counts, glyphs[sheet.source])
        documents.append(document)
        pages += sheet_pages
        lines += sheet_lines
        units += sheet_units
    tables.write(out / "documents.parquet", documents, Document)
    tables.write(out / "pages.parquet", pages, Page)
    tables.write(out / "lines.parquet", lines, Line)
    tables.write(out / "units.parquet", units, Unit)
    command = "atlas import hng-kiridashi" + (f" --limit {limit}" if limit is not None else "")
    counts.update(tables.Dataset(out).merge([], out, command=command))
    counts["linked"] = sum(1 for unit in units if "hng_unit" in unit.upstream)
    counts["unlinked"] = sum(1 for unit in units if "hng_gid" in unit.upstream and "hng_unit" not in unit.upstream)
    return dict(sorted(counts.items()))
