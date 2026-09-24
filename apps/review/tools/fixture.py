"""Build the fixture dataset the interface is driven against.

    python apps/review/tools/fixture.py <directory>

Writes `documents.parquet`, `pages.parquet`, `lines.parquet` and `units.parquet`, and a synthetic
page image into `<directory>/cache/images/<sha256[:2]>/<sha256>.jpg`, so that the review service
serves both the records and the pictures of the page. The dataset is shaped like the one
`tests/test_review_server.py` builds, with three extras the interface has to survive:

* a page whose image is not cached (`doc-1:p2`, `doc-2:p1`): the view shows the IIIF URL and skips it;
* a 割書 line with two columns of five units (`doc-1:p3:line0`);
* a very long line of 240 units (`doc-1:p3:line1`), which the line view virtualises.

The last line of stdout is a JSON object with the ids and the cache directory, which
`tools/check.mjs` reads to drive the same endpoints the interface calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw

from glyph_atlas import tables
from glyph_atlas.schema import (
    Box,
    Candidate,
    Classification,
    Document,
    Line,
    LineRole,
    Page,
    PageText,
    Script,
    Unit,
)

KANA = ["あ", "い", "う", "え", "お", "か", "き", "く", "け", "こ"]
CODE_POINTS = [
    "U+3042",
    "U+3044",
    "U+3046",
    "U+3048",
    "U+304A",
    "U+304B",
    "U+304D",
    "U+304F",
    "U+3051",
    "U+3053",
]
PAPER = (238, 232, 219)
INK = (28, 24, 22)
RULE = (196, 184, 166)


def pseudo_glyph(draw: ImageDraw.ImageDraw, box: Box, seed: str) -> None:
    """A deterministic few strokes inside a unit box: enough to see a crop is aligned."""
    rng = random.Random(seed)
    left, top = box.x + box.w * 0.12, box.y + box.h * 0.12
    right, bottom = box.x + box.w * 0.88, box.y + box.h * 0.88
    width = max(2, int(box.w * 0.06))
    for _ in range(rng.randint(2, 4)):
        kind = rng.random()
        if kind < 0.45:
            draw.line(
                [(left, rng.uniform(top, bottom)), (right, rng.uniform(top, bottom))], fill=INK, width=width
            )
        elif kind < 0.8:
            draw.line(
                [(rng.uniform(left, right), top), (rng.uniform(left, right), bottom)], fill=INK, width=width
            )
        else:
            x1, x2 = sorted((rng.uniform(left, right), rng.uniform(left, right)))
            y1, y2 = sorted((rng.uniform(top, bottom), rng.uniform(top, bottom)))
            draw.line([(x1, y1), (x2, y2)], fill=INK, width=width)


def draw_page(path: Path, width: int, height: int, lines: list[Line], units: list[Unit]) -> str:
    """A synthetic page: column rules where the lines are, a pseudo glyph in every unit box."""
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle([8, 8, width - 9, height - 9], outline=RULE, width=3)
    for line in lines:
        if line.box is None:
            continue
        box = line.box
        draw.rectangle([box.x, box.y, box.x + box.w, box.y + box.h], outline=RULE, width=2)
    for unit in units:
        if unit.box is not None:
            pseudo_glyph(draw, unit.box, unit.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=82)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line_of(page_id: str, seq: int, box: Box, text: str, **extra) -> Line:
    return Line(
        id=f"{page_id}:line{seq}",
        page_id=page_id,
        seq=seq,
        box=box,
        text_raw=text,
        text=text,
        match_method="import",
        match_confidence=0.9,
        **extra,
    )


def units_of(line: Line, boxes: list[Box], offset: int = 0, kind: str = "char") -> list[Unit]:
    """Units for one line: readings walk the kana table, code points follow them."""
    made = []
    for number, box in enumerate(boxes):
        index = (number + offset) % len(KANA)
        script = Script.HENTAIGANA if index == 0 and number else Script.HIRAGANA
        made.append(
            Unit(
                id=f"{line.id}:u{number}",
                document_id=line.page_id.split(":")[0],
                page_id=line.page_id,
                line_id=line.id,
                seq=number,
                box=box,
                reading=KANA[index],
                text_source=KANA[index],
                unicode=CODE_POINTS[index],
                classification=Classification.IDENTIFIED,
                script=script,
                method="detect-align",
            )
        )
    return made


def build(directory: Path, *, long_units: int = 240) -> dict:
    """Write the fixture dataset and its cached page images; return the ids the checks use."""
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / "cache"
    documents = [
        Document(id="doc-1", title="Calibration book A", shelfmark="WA 7-1"),
        Document(id="doc-2", title="Calibration book B", shelfmark="WA 7-2"),
    ]
    pages = [
        Page(
            id="doc-1:p1",
            document_id="doc-1",
            seq=1,
            image="https://example.org/iiif/doc-1/1/full/full/0/default.jpg",
            width=1200,
            height=1800,
        ),
        Page(
            id="doc-1:p2",
            document_id="doc-1",
            seq=2,
            image="https://example.org/iiif/doc-1/2/full/full/0/default.jpg",
            width=1200,
            height=1800,
        ),
        Page(
            id="doc-1:p3",
            document_id="doc-1",
            seq=3,
            image="https://example.org/iiif/doc-1/3/full/full/0/default.jpg",
            width=1200,
            height=4000,
        ),
        Page(
            id="doc-2:p1",
            document_id="doc-2",
            seq=1,
            image="https://example.org/iiif/doc-2/1/full/full/0/default.jpg",
            width=1200,
            height=1800,
        ),
    ]
    for page in pages:
        page.seq -= 1
    pages.extend([
        Page(id="doc-2:p2", document_id="doc-2", seq=1,
             image="https://example.org/iiif/doc-2/2.jpg", width=1200, height=1800),
        Page(id="doc-2:p3", document_id="doc-2", seq=2,
             image="https://example.org/iiif/doc-2/3.jpg", width=1200, height=1800),
    ])

    lines: list[Line] = []
    units: list[Unit] = []
    images: dict[str, list[Unit]] = {}

    # doc-1:p1 — three plain vertical lines of four units.
    for seq in range(3):
        box = Box(x=140 + 320 * seq, y=120, w=220, h=1500)
        line = line_of("doc-1:p1", seq, box, "".join(KANA[(seq * 4 + n) % len(KANA)] for n in range(4)))
        lines.append(line)
        made = units_of(
            line, [Box(x=box.x + 30, y=box.y + 40 + 360 * n, w=160, h=300) for n in range(4)], offset=seq * 4
        )
        units.extend(made)
        images.setdefault("doc-1:p1", []).extend(made)

    # doc-1:p1 — two units written as one character and read as another. The supplementary-plane
    # 𪜈 (U+2A708) is the character the search box has to handle: Python indexes it as one character
    # and JavaScript as two UTF-16 units. No imported corpus records an occurrence, so this fixture
    # is the only place a search for it can find one; ゐ read as い is the case that tells a search
    # on the written character from a search on the reading.
    written_line = line_of("doc-1:p1", 3, Box(x=980, y=120, w=200, h=1500), "𪜈ゐ")
    lines.append(written_line)
    written = [
        Unit(id="doc-1:p1:l3:u0", document_id="doc-1", page_id="doc-1:p1", line_id=written_line.id,
             seq=0, box=Box(x=1000, y=160, w=160, h=300), reading="い", text_source="い",
             unicode="U+2A708", script=Script.HAN, classification=Classification.IDENTIFIED,
             method="detect-align"),
        Unit(id="doc-1:p1:l3:u1", document_id="doc-1", page_id="doc-1:p1", line_id=written_line.id,
             seq=1, box=Box(x=1000, y=560, w=160, h=300), reading="い", text_source="い",
             unicode="U+3090", script=Script.HIRAGANA, classification=Classification.IDENTIFIED,
             method="detect-align"),
        # The three layers on one record, which is what the reviewer has to keep apart: written ネ
        # (U+30CD), read ね. A correction of the character changes `unicode`; the reading stays ね
        # unless a reviewer changes the reading too, so the fixture makes the two visibly different.
        Unit(id="doc-1:p1:l3:u2", document_id="doc-1", page_id="doc-1:p1", line_id=written_line.id,
             seq=2, box=Box(x=1000, y=960, w=160, h=300), reading="ね", text_source="ね",
             unicode="U+30CD", script=Script.KATAKANA, classification=Classification.IDENTIFIED,
             method="detect-align"),
    ]
    units.extend(written)
    images.setdefault("doc-1:p1", []).extend(written)

    # doc-1:p2 — not cached: the interface must show the URL and skip the page.
    for seq in range(2):
        box = Box(x=180 + 400 * seq, y=150, w=220, h=1400)
        line = line_of("doc-1:p2", seq, box, "".join(KANA[n] for n in range(3)))
        lines.append(line)
        units.extend(
            units_of(line, [Box(x=box.x + 30, y=box.y + 60 + 420 * n, w=160, h=300) for n in range(3)])
        )

    # doc-2:p1 — not cached either.
    for seq in range(2):
        box = Box(x=200 + 400 * seq, y=160, w=220, h=1300)
        line = line_of("doc-2:p1", seq, box, "".join(KANA[2 + n] for n in range(3)))
        lines.append(line)
        units.extend(
            units_of(line, [Box(x=box.x + 30, y=box.y + 60 + 400 * n, w=160, h=300) for n in range(3)])
        )

    # doc-1:p3:line0 — 割書: one wide box holding two shorter columns.
    warigaki_box = Box(x=120, y=140, w=520, h=1100)
    warigaki = line_of("doc-1:p3", 0, warigaki_box, "あいうえおかきくけこ", role=LineRole.WARIGAKI)
    warigaki.meta = {"split": "割書", "columns": 2}
    lines.append(warigaki)
    # Reading order in a 割書 line runs down the right column first, so `seq` follows the boxes
    # the same way the review interface orders them.
    columns = [Box(x=400, y=180 + 380 * n, w=180, h=320) for n in range(5)] + [
        Box(x=160, y=180 + 380 * n, w=180, h=320) for n in range(5)
    ]
    made = units_of(warigaki, columns)
    units.extend(made)
    images.setdefault("doc-1:p3", []).extend(made)

    # doc-1:p3:line1 — very long: one column of 240 units, which the line view has to virtualise.
    long_box = Box(x=760, y=160, w=180, h=7200)
    long_line = line_of("doc-1:p3", 1, long_box, "".join(KANA[n % len(KANA)] for n in range(long_units)))
    lines.append(long_line)
    boxes = [Box(x=long_box.x + 30, y=long_box.y + 30 + 29 * n, w=110, h=26) for n in range(long_units)]
    made = units_of(long_line, boxes)
    units.extend(made)
    images.setdefault("doc-1:p3", []).extend(made)

    # One ambiguous unit with scored candidates, so the 字母 picker has something to choose from.
    ambiguous = next(unit for unit in units if unit.id == "doc-1:p1:line0:u0")
    units[units.index(ambiguous)] = ambiguous.model_copy(
        update={
            "classification": Classification.AMBIGUOUS,
            "candidates": [
                Candidate(unicode="U+3042", p=0.4),
                Candidate(unicode="U+1B002", p=0.35, jibo="安"),
                Candidate(unicode="U+1B003", p=0.25, jibo="阿"),
            ],
        }
    )

    by_page: dict[str, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page_id, []).append(line)
    for page in pages:
        if page.id in ("doc-1:p2", "doc-2:p1"):
            continue  # left out of the cache on purpose
        page_units = images.get(page.id, [])
        page_lines = by_page.get(page.id, [])
        height = max(
            page.height, max((unit.box.y + unit.box.h for unit in page_units if unit.box), default=0) + 80
        )
        digest = draw_page(
            cache / "images" / "00" / "placeholder.jpg", page.width, height, page_lines, page_units
        )
        target = cache / "images" / digest[:2] / f"{digest}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        (cache / "images" / "00" / "placeholder.jpg").replace(target)
        page.sha256 = digest

    for name, records, model in (
        ("documents", documents, Document),
        ("pages", pages, Page),
        ("lines", lines, Line),
        ("units", units, Unit),
    ):
        tables.write(directory / f"{name}.parquet", records, model)
    tables.write(directory / "page_texts.parquet", [
        PageText(page_id="doc-1:p1", source="fixture", text_raw="【右丁】\nあいうえ\nおかきく\nけこあい"),
        PageText(page_id="doc-2:p2", source="fixture", text_raw="【右丁】\n天　アイヌ\n地　モシリ"),
        PageText(page_id="doc-2:p3", source="fixture", text_raw=""),
    ], PageText)

    return {
        "directory": str(directory),
        "cache": str(cache),
        "documents": [document.id for document in documents],
        "pages": {page.id: page.sha256 for page in pages},
        "line": "doc-1:p1:line0",
        "line_with_units": "doc-1:p1:line1",
        "unit": "doc-1:p1:line0:u0",
        "ambiguous": "doc-1:p1:line0:u0",
        "warigaki": "doc-1:p3:line0",
        "long_line": "doc-1:p3:line1",
        "long_units": long_units,
        "uncached_page": "doc-1:p2",
        "cached_page": "doc-1:p1",
        "text_only_page": "doc-2:p2",
        "empty_page": "doc-2:p3",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="where the dataset is written")
    parser.add_argument("--long-units", type=int, default=240, help="units on the very long line")
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.directory, long_units=arguments.long_units), ensure_ascii=False))


if __name__ == "__main__":
    main()
