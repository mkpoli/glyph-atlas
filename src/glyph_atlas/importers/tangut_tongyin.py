"""Import the head characters of 同音 (甲種本, Инв. No. 207) from the plates of 俄藏黑水城文獻 vol. 7.

同音 lists the Tangut characters by initial, homophones together: a head character in large writing
with its note in small writing beside it. `data/tangut/tongyin/` places each head character on the
plate that shows it (`scripts/build_tangut_tongyin.py`), under the folio, side, column and position
that nkay0's rhyme-dictionary table gives it, so a box comes with the character it holds.

One document is the dictionary. A page is a printed page of the volume, which carries two plates and
so two folios. A line is one column of a half-folio; its text is the column's head characters, since
the notes are not transcribed. A unit is one head character. Only the places graded `ok` become
units: one that matched its glyph poorly, whose box is far from the folio's usual size, or that holds
little ink may sit on the wrong ink, and a wrong box under a character's name is worse than a missing
one. Those places stay in the table with their scores.

The volume is not online. The page images are read from a directory of scans and must be the files
`pages.tsv` pins by checksum; they are registered in the image cache under `file:` keys, as the
other collections of local page images are.
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
    Confidence,
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

SOURCE_ID = "ecang-heishuicheng"
TABLE_ID = "nkay0-tangut-rhyme"
ITEM = "inv-207"
DOCUMENT_ID = f"{SOURCE_ID}:{ITEM}"
DEFAULT_SOURCE = registry.SOURCES / f"{SOURCE_ID}.yaml"
DEFAULT_DATA = registry.ROOT / "data" / "tangut" / "tongyin"
COMMAND = "atlas import tangut-tongyin"
MODEL = "tongyin-glyph-match"


class TongyinSourceError(RuntimeError):
    """A table, a source record or a page image that is not what the import expects."""


def read_tsv(path: Path) -> list[dict[str, str]]:
    """The rows of a table whose leading `#` lines say where it comes from."""
    if not path.is_file():
        raise TongyinSourceError(f"{path}: no such table")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t"))


def is_ideograph(character: str) -> bool:
    """Whether the character is in Unicode's Tangut block or its supplement."""
    return len(character) == 1 and (0x17000 <= ord(character) <= 0x187FF or 0x18D00 <= ord(character) <= 0x18D7F)


def place_id(row: dict[str, str]) -> str:
    """A cell as the dictionaries cite it: folio, side, column, and position when the row has one."""
    return f"{int(row['folio']):02d}{row['side']}{row['column']}{row.get('position', '')}"


def collect(
    scans: Path,
    out: Path = Path("work/tangut-tongyin"),
    *,
    data: Path = DEFAULT_DATA,
    source: Path = DEFAULT_SOURCE,
    cache: Path | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    """Check the page images against their pins and write the dataset's four tables."""
    config = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("id") != SOURCE_ID:
        raise TongyinSourceError(f"{source}: not the {SOURCE_ID} source record")
    item = next((item for volume in config.get("volumes") or [] for item in volume.get("items") or []
                 if item.get("id") == ITEM), None)
    if item is None:
        raise TongyinSourceError(f"{source}: no item {ITEM}")
    volume = next(volume for volume in config["volumes"] if item in volume["items"])
    page_rows, column_rows, character_rows = (read_tsv(Path(data) / f"{name}.tsv")
                                              for name in ("pages", "columns", "characters"))

    document = _document(config, volume, item)
    pages: list[Page] = []
    for seq, row in enumerate(page_rows):
        file = Path(scans) / row["file"]
        if not file.is_file():
            raise TongyinSourceError(f"{file}: the scan of printed page {row['page']} is not there")
        if hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]:
            raise TongyinSourceError(f"{file}: not the scan that {Path(data) / 'pages.tsv'} pins")
        held = images.register(file, f"file:{file.resolve().as_posix()}", root=cache)
        if (held.width, held.height) != (int(row["width"]), int(row["height"])):
            raise TongyinSourceError(f"{file}: {held.width} x {held.height}, pinned {row['width']} x {row['height']}")
        pages.append(Page(
            id=_page_id(volume, row["page"]),
            document_id=document.id,
            seq=seq,
            image=held.url,
            width=held.width,
            height=held.height,
            sha256=held.sha256,
            meta={"volume": str(volume["id"]), "printed_page": int(row["page"]), "file": row["file"],
                  "folios": [int(folio) for folio in row["folios"].split()],
                  "plates": [[int(value) for value in plate.split(",")] for plate in row["plates"].split()]},
        ))
    page_ids = {page.meta["printed_page"]: page.id for page in pages}

    lines = [Line(
        id=f"{document.id}:{place_id(row)}",
        page_id=_known_page(page_ids, row),
        seq=seq,
        box=_box(row),
        vertical=True,
        role=LineRole.MAIN,
        text_raw=row["text"],
        text=row["text"],
        match_method="rule-lines",
        meta={"folio": int(row["folio"]), "side": row["side"], "column": int(row["column"]),
              "text_scope": "head characters; the notes beside them are not transcribed"},
    ) for seq, row in enumerate(column_rows)]
    line_ids = {line.id for line in lines}

    units, left_out = [], {}
    for row in character_rows:
        if row["quality"] != "ok":
            left_out[row["quality"]] = left_out.get(row["quality"], 0) + 1
            continue
        if not is_ideograph(row["char"]):
            raise TongyinSourceError(f"{place_id(row)}: {row['char']!r} is not a Tangut ideograph")
        line_id = f"{document.id}:{place_id({**row, 'position': ''})}"
        if line_id not in line_ids:
            raise TongyinSourceError(f"{place_id(row)}: its column is not in columns.tsv")
        units.append(Unit(
            id=f"{document.id}:{place_id(row)}",
            document_id=document.id,
            page_id=_known_page(page_ids, row),
            line_id=line_id,
            seq=int(row["position"]) - 1,
            box=_box(row),
            text_source=row["char"],
            unicode=row["codepoint"],
            classification=Classification.IDENTIFIED,
            script=Script.TANGUT,
            method="detect-align",
            confidence=Confidence(text=float(row["score"]), model=MODEL),
            review=ReviewState.MACHINE,
            upstream={TABLE_ID: f"旧版同音 {place_id(row)}"},
            meta={"tongyin": place_id(row)},
        ))

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = (("documents", [document], Document), ("pages", pages, Page),
               ("lines", lines, Line), ("units", units, Unit))
    for name, records, model in written:
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {
        "source": SOURCE_ID,
        "document": document.id,
        "pages": len(pages),
        "lines": len(lines),
        "units": len(units),
        "characters": len(character_rows),
        "left_out": dict(sorted(left_out.items())),
    }
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


def _page_id(volume: dict[str, Any], printed_page: str) -> str:
    return f"{SOURCE_ID}:{volume['id']}:p{int(printed_page):03d}"


def _known_page(page_ids: dict[int, str], row: dict[str, str]) -> str:
    try:
        return page_ids[int(row["page"])]
    except KeyError:
        raise TongyinSourceError(f"{place_id(row)}: printed page {row['page']} is not in pages.tsv") from None


def _box(row: dict[str, str]) -> Box:
    return Box(x=int(row["x"]), y=int(row["y"]), w=int(row["w"]), h=int(row["h"]))


def _checked(config: dict[str, Any]) -> date | None:
    value = config.get("checked")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if isinstance(value, str) else None


def _document(config: dict[str, Any], volume: dict[str, Any], item: dict[str, Any]) -> Document:
    # The document validator turns the publisher's terms into `holder_terms` and records the plates,
    # flat reproductions of a Western Xia print, as public domain.
    rights = Rights(
        licence=Licence(config.get("image_licence") or config["licence"]),
        holder=config.get("holder"),
        attribution=f"{config['attribution']}; {volume['title']}, {item['shelfmark']}",
        evidence=config.get("licence_evidence") or config["url"],
        checked=_checked(config),
    )
    table = next((entry for entry in registry.load() if entry.id == TABLE_ID), None)
    if table is None:
        raise TongyinSourceError(f"{TABLE_ID}: no such source record")
    text_rights = Rights(licence=table.licence, attribution=table.attribution, evidence=table.url)
    return Document(
        id=DOCUMENT_ID,
        title=item["title"],
        source_refs={SOURCE_ID: f"{volume['id']}:{item['id']}", TABLE_ID: "旧版同音"},
        holder=config.get("holder"),
        shelfmark=item["shelfmark"],
        production="printed/woodblock",
        origin="china",
        genre=["dictionary"],
        dating=[Dating(literal="西夏", start=1038, end=1227, kind="publication",
                       evidence="The period of the Western Xia state; the edition's own date is not recorded here.")],
        image_rights=rights,
        text_rights=text_rights,
        meta={
            "work_title": item.get("work"),
            "volume": volume["title"],
            "volume_year": volume.get("year"),
            "printed_pages": item.get("printed_pages"),
            "plates": item.get("plates"),
            "scans": "supplied by Maxim Persikov, 2026-11-15",
            "source_notes": config.get("notes") or [],
        },
    )
