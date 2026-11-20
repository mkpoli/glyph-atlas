"""Import the small seal forms of 説文解字 from CODH's 篆書字体データセット.

Book TE00039 of the dataset holds the seal forms of the 陳昌治 edition (同治十二年刊), one crop file
for each, filed under a Han 親字. `data/tensho/TE00039/characters.tsv`
(`scripts/build_tensho_shuowen.py`) places each crop on the small seal character of Unicode 18.0 it
shows, so a crop comes with its seal code point.

The book is one document. Every placed crop is a unit with no page and no box, placed by rule and
so `machine` until a reviewer confirms it: its image is the
file CODH cut, which is larger than the same box on the facsimile the 東方學デジタル圖書館 serves. The
unit's code point is the seal character's and its source string CODH's 親字; `upstream` keeps the
親字 again, the Han character Unicode
gives as its equivalent (`kSEAL_MCJK`), its place in the edition (`kSEAL_CCZSrc`), the page of the
facsimile and the box on CODH's page image, and the evidence that placed it. A crop the table places
on no seal character is left out and counted.

The crops are read from the book's zip, which must be the file the source record pins, and each
must be the file the table pins. They are extracted below `cache/crops`, where the exports look for
a standalone crop.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import registry, tables
from ..schema import Classification, Dating, Document, Licence, ReviewState, Rights, Script, Unit

SOURCE_ID = "codh-tensho"
UNICODE_ID = "unicode-ucd"
BOOK = "TE00039"
DEFAULT_SOURCE = registry.SOURCES / f"{SOURCE_ID}.yaml"
DEFAULT_DATA = registry.ROOT / "data" / "tensho"
DEFAULT_CROPS = registry.ROOT / "cache" / "crops"
COMMAND = "atlas import tensho"
SEAL = range(0x3D000, 0x3FC40)


class TenshoSourceError(RuntimeError):
    """A zip, a table or a source record that is not what the import expects."""


def read_tsv(path: Path) -> list[dict[str, str]]:
    """The rows of a table whose leading `#` lines say where it comes from."""
    if not path.is_file():
        raise TenshoSourceError(f"{path}: no such table")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t"))


def member_of(book: str, row: dict[str, str]) -> str:
    """The crop file of a row inside the book's zip, as the dataset names it."""
    parent = row["parent_code_point"]
    return f"{book}/characters/{parent}/{parent}_{row['image']}_X{int(row['x']):04d}_Y{int(row['y']):04d}.jpg"


def collect(
    archive: Path,
    out: Path = Path("work/tensho"),
    *,
    book: str = BOOK,
    data: Path | None = None,
    source: Path | None = None,
    crops: Path | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    """Check the zip and its crops against their pins, extract the crops and write the tables."""
    data, source, crops = data or DEFAULT_DATA, source or DEFAULT_SOURCE, crops or DEFAULT_CROPS
    config = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("id") != SOURCE_ID:
        raise TenshoSourceError(f"{source}: not the {SOURCE_ID} source record")
    entry = next((held for held in config.get("books") or [] if held.get("id") == book), None)
    if entry is None:
        raise TenshoSourceError(f"{source}: no book {book}")
    archive = Path(archive)
    if not archive.is_file():
        raise TenshoSourceError(f"{archive}: no such zip")
    if _sha256(archive) != entry["zip_sha256"]:
        raise TenshoSourceError(f"{archive}: not the zip that {source} pins for {book}")

    document = _document(config, entry)
    units, left_out = [], 0
    rows = read_tsv(Path(data) / book / "characters.tsv")
    if len(rows) != int(entry["crops"]):
        raise TenshoSourceError(f"{data}: {len(rows)} rows for the {entry['crops']} crops of {book}")
    with zipfile.ZipFile(archive) as held:
        for row in rows:
            if not row["placed"]:
                left_out += 1
                continue
            if len(row["char"]) != 1 or ord(row["char"]) not in SEAL:
                raise TenshoSourceError(f"row {row['seq']}: {row['char']!r} is not a small seal character")
            if row["code_point"] != f"U+{ord(row['char']):04X}":
                raise TenshoSourceError(f"row {row['seq']}: {row['code_point']} is not the code point of its character")
            member = member_of(book, row)
            try:
                content = held.read(member)
            except KeyError:
                raise TenshoSourceError(f"{archive}: no crop {member}") from None
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise TenshoSourceError(f"{member}: not the crop that the table pins")
            target = Path(crops) / member
            if not target.is_file() or target.read_bytes() != content:
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.with_name(f".{target.name}")
                staging.write_bytes(content)
                os.replace(staging, target)
            units.append(_unit(document, archive.name, member, row))

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = (("documents", [document], Document), ("units", units, Unit))
    for name, records, model in written:
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {"source": SOURCE_ID, "documents": [document.id], "units": len(units), "left_out": left_out}
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _unit(document: Document, archive: str, member: str, row: dict[str, str]) -> Unit:
    return Unit(
        id=f"{document.id}:{int(row['seq']):05d}",
        document_id=document.id,
        page_id=None,
        crop=f"{archive}!{member}",
        crop_sha256=row["sha256"],
        text_source=row["parent"],
        unicode=row["code_point"],
        classification=Classification.IDENTIFIED,
        script=Script.SEAL,
        style="seal",
        method="import",
        review=ReviewState.MACHINE,
        upstream={
            "source": SOURCE_ID,
            "ref": member.rsplit("/", 1)[-1].removesuffix(".jpg"),
            "parent": row["parent"],
            "equivalent": row["equivalent"],
            "ccz": row["ccz"],
            "placed": row["placed"],
            "page": row["canvas"],
            "box": f"{row['x']},{row['y']},{row['w']},{row['h']}",
        },
    )


def _checked(config: dict[str, Any]) -> date | None:
    value = config.get("checked")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if isinstance(value, str) else None


def _document(config: dict[str, Any], book: dict[str, Any]) -> Document:
    # The crops are CODH's, under the dataset's licence. The seal code points and their equivalents
    # are Unicode's; the 親字 are CODH's.
    rights = Rights(
        licence=Licence(config["licence"]),
        holder=book.get("holder"),
        # The dataset asks a derived release to name each book's image provider and holder; this
        # line reaches the release's ATTRIBUTION.md.
        attribution=f"{config['attribution']}; 『{book['work']}』 {book['provider']}（{book['holder']}） {book['url']}",
        evidence=config.get("licence_evidence") or config["url"],
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
