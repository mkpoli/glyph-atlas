"""Import a per-book zip of the 日本古典籍くずし字データセット.

The zip holds `{bid}/{bid}_coordinate.csv` (columns Unicode, Image, X, Y, Block ID, Char ID, Width,
Height), the corrected page images under `images/`, and crops under `characters/`. Only the CSV and the
image sizes are read; crops are addressed through CODH's IIIF server.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path

from PIL import Image

from ..schema import (
    Box, Classification, Document, Licence, Page, Production, ReviewState, Rights, Script, Unit, UnitKind,
)

SOURCE = "codh-char-shape"
ATTRIBUTION = "『日本古典籍くずし字データセット』（国文研ほか所蔵／CODH加工）doi:10.20676/00000340"
IIIF = "https://codh.rois.ac.jp/char-shape/iiif/{bid}/{image}.tif"
IMAGE_NAME = re.compile(r"^(?P<bid>[^_]+)_(?P<page>\d{5})_(?P<half>[12])$")
ITERATION_MARKS = {0x3005, 0x3031, 0x3032, 0x309D, 0x309E, 0x30FD, 0x30FE}
LIGATURES = {0x309F, 0x30FF}


def script_of(cp: int) -> Script:
    if 0x3041 <= cp <= 0x3096:
        return Script.HIRAGANA
    if 0x30A1 <= cp <= 0x30FA:
        return Script.KATAKANA
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x3134F or 0xF900 <= cp <= 0xFAFF:
        return Script.KANJI
    if 0x1B002 <= cp <= 0x1B122 or cp == 0x1B001:
        return Script.HENTAIGANA
    if 0x0020 <= cp <= 0x024F:
        return Script.LATIN
    return Script.SYMBOL


def kind_of(cp: int) -> UnitKind:
    if cp in ITERATION_MARKS:
        return UnitKind.ITERATION_MARK
    if cp in LIGATURES:
        return UnitKind.LIGATURE
    if cp in (0x3099, 0x309A, 0x309B, 0x309C):
        return UnitKind.VOICING_MARK
    if 0x3001 <= cp <= 0x3003 or cp in (0x30FB, 0xFF0C, 0xFF0E):
        return UnitKind.PUNCTUATION
    return UnitKind.CHAR


def read(zip_path: Path, title: str | None = None) -> tuple[Document, list[Page], list[Unit]]:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        csv_name = next(n for n in names if n.endswith("_coordinate.csv"))
        bid = Path(csv_name).name.split("_")[0]
        rights = Rights(licence=Licence.CC_BY_SA_4, holder="国文学研究資料館ほか", attribution=ATTRIBUTION,
                        evidence="http://codh.rois.ac.jp/char-shape/#license", checked=date.today())
        document = Document(
            id=f"codh:{bid}", title=title or bid, source_refs={SOURCE: bid, "nijl-bid": bid},
            production=Production.UNKNOWN, image_rights=rights, text_rights=rights,
        )
        sizes: dict[str, tuple[int, int]] = {}
        for name in names:
            if "/images/" in name and name.endswith(".jpg"):
                with archive.open(name) as handle:
                    sizes[Path(name).stem] = Image.open(io.BytesIO(handle.read())).size
        rows = list(csv.DictReader(io.TextIOWrapper(archive.open(csv_name), encoding="utf-8-sig")))
    pages: dict[str, Page] = {}
    units: list[Unit] = []
    for row in rows:
        image = row["Image"]
        if image not in pages:
            match = IMAGE_NAME.match(image)
            seq = int(match["page"]) * 2 - (2 - int(match["half"])) if match else len(pages) + 1
            width, height = sizes.get(image, (0, 0))
            pages[image] = Page(id=f"codh:{bid}:{image}", document_id=document.id, seq=seq,
                                image=IIIF.format(bid=bid, image=image), width=width, height=height,
                                transcription={"source": SOURCE, "entry": bid})
        cp = int(row["Unicode"].removeprefix("U+"), 16)
        char = chr(cp)
        block, char_id = row["Block ID"], row["Char ID"]
        units.append(Unit(
            id=f"codh:{bid}:{image}:{block}:{char_id}", page_id=pages[image].id, line_id=None, seq=None,
            box=Box(x=int(row["X"]), y=int(row["Y"]), w=int(row["Width"]), h=int(row["Height"])),
            kind=kind_of(cp), text_source=char, reading=char, unicode=f"U+{cp:04X}", script=script_of(cp),
            classification=Classification.IDENTIFIED, method="import", review=ReviewState.TRANSCRIBER,
            upstream={"source": SOURCE, "ref": f"{bid}/{image}/{block}/{char_id}", "block": block},
        ))
    return document, list(pages.values()), units
