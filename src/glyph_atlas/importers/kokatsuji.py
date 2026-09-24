"""Import the 古活字データセット (CODH OMT) into the tables.

The archive `{bid}.zip` holds `{bid}/dataset.csv` (columns
`ID,character,jibo,block,page,x1,y1,x2,y2,line,count,old_ID,OCR`), the corrected page images under
`{bid}/page/` and one crop per block under `{bid}/block/`. One row is one movable-type block: `page`
names the corrected page image, `x1,y1` is the top-left and `x2,y2` the bottom-right corner of the
block on it, `x2` and `y2` exclusive; `line` numbers the line down the page and `count` the block
along the line. `character` holds the transcription of the block, several characters where one
block carries a 連彫活字, and `jibo` holds one 字母 per character in the same order, the mark
itself where the character is 〱 or ゝ. `OCR` is a machine reading and is ignored.

Every block becomes a unit `codh-omt:{bid}:{ID}`: `granularity` `char` for one character and `block`
for a 連彫活字, `text_source` and `reading` the `character` column, `seq` the position of the block
along its line counted from zero, which is the 1-based `count` column less one and is kept as given
in `upstream["count"]`, `review=transcriber`. A block of one kana is classified from its 字母: the
code points of `refs.candidates(character)` whose 字母 is the same one, one candidate giving
`unicode` and `identified` and several giving `ambiguous` with equal `p`. Every other block keeps
the code points of its transcription and stays `unassessed`, and its 字母 column is kept in
`upstream["jibo_sequence"]`. The unit carries a code point and not a 字母: 字母 belongs to a
character, the character layer keeps it once per code point, and `refs.jibo_of(unit.unicode)` reads
it back. The blocks of one line become a line `{page_id}:L{line}` whose box is the union of theirs,
and the page images go into the image cache through
`images.register(path, url)` under a `file:` key, so a page is addressed by the key rather than by
the archive it came from.
"""

from __future__ import annotations

import csv
import io
import shutil
import warnings
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple

import yaml

from .. import images, refs, rights, tables
from ..registry import SOURCES
from ..schema import (
    Box,
    Candidate,
    Classification,
    Dating,
    Document,
    Licence,
    Line,
    Page,
    Production,
    Register,
    ReviewState,
    Rights,
    Script,
    Unit,
    UnitKind,
)

#: The source id of `data/sources/codh-kokatsuji.yaml`.
SOURCE = "codh-kokatsuji"
SOURCE_FILE = SOURCES / f"{SOURCE}.yaml"
#: Where the archive comes from, for the message of a missing file.
DATASET_URL = "https://codh.rois.ac.jp/omt/dataset/001.zip"
DEFAULT_ZIP = Path("cache/codh/001.zip")

DOCUMENT_ID = "codh-omt:001"
TITLE = "徒然草 2巻"
AUTHOR = "吉田兼好"
HOLDER = "国立国会図書館"
NDL_PID = "2544701"
NDL_URL = f"https://dl.ndl.go.jp/pid/{NDL_PID}"
#: 慶長 1596–1615 and 元和 1615–1624, the years the NDL item is dated to.
DATING_LITERAL = "慶長・元和年間"
DATING_START = 1596
DATING_END = 1624

CSV_NAME = "dataset.csv"
PAGE_DIR = "page"
#: The columns the importer reads; `old_ID` and `OCR` are not used.
FIELDS = ("ID", "character", "jibo", "block", "page", "x1", "y1", "x2", "y2", "line", "count")

CHAR = "char"
BLOCK = "block"
#: Repeat marks: 々〱〲ゝゞヽヾ.
ITERATION_MARKS = frozenset({0x3005, 0x3031, 0x3032, 0x309D, 0x309E, 0x30FD, 0x30FE})
#: Kana ligatures: ゟ and ヿ.
LIGATURES = frozenset({0x309F, 0x30FF})
VOICING_MARKS = frozenset({0x3099, 0x309A, 0x309B, 0x309C})
PUNCTUATION = frozenset({0x3001, 0x3002, 0x30FB, 0xFF0C, 0xFF0E})
HIRAGANA = frozenset({0x309D, 0x309E, 0x309F})
KATAKANA = frozenset({0x30FD, 0x30FE, 0x30FF})


class Labels(NamedTuple):
    """What the 字母 of one block say about it: its unit's labels, apart from the box and the text."""

    granularity: str
    unicode: str | None
    classification: Classification
    candidates: list[Candidate]
    jibo: list[str]
    script: Script


def script_of(text: str) -> Script:
    """The script of a string: the one script its characters share, `unknown` when they differ."""
    scripts = {_script_of_char(char) for char in text}
    return scripts.pop() if len(scripts) == 1 else Script.UNKNOWN


def _script_of_char(char: str) -> Script:
    """The script of one character: the movable-type marks, then the character layer.

    The dataset writes the iteration marks 〱〲 and ゝゞ as kana, so the two kana blocks and the 々
    marks are taken here as the source counts them. Everything else is the character layer's to
    answer, which reaches every kana Unicode assigns, hentaigana and the alternate katakana of
    Unicode 18.0 included. A kanji is labelled `kanji`, which is what the records already say.
    """
    code_point = ord(char)
    if 0x3041 <= code_point <= 0x3096 or code_point in HIRAGANA or code_point in (0x3031, 0x3032):
        return Script.HIRAGANA
    if 0x30A1 <= code_point <= 0x30FA or code_point in KATAKANA:
        return Script.KATAKANA
    if 0x3005 <= code_point <= 0x3007 or _is_ideograph(code_point):
        return Script.HAN
    script = refs.script_of(char)
    return Script.SYMBOL if script is Script.UNKNOWN else script


def _is_ideograph(code_point: int) -> bool:
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x3134F
    )


def kind_of(text: str) -> UnitKind:
    """What a block is: one character, a repeat mark, or a 連彫活字 of several characters."""
    if len(text) > 1:
        return UnitKind.LIGATURE
    code_point = ord(text)
    if code_point in ITERATION_MARKS:
        return UnitKind.ITERATION_MARK
    if code_point in LIGATURES:
        return UnitKind.LIGATURE
    if code_point in VOICING_MARKS:
        return UnitKind.VOICING_MARK
    if code_point in PUNCTUATION:
        return UnitKind.PUNCTUATION
    return UnitKind.CHAR


def sequence(text: str) -> str:
    """The code points of a string as one value, `"つれ〱"` -> `"U+3064 U+308C U+3031"`."""
    return " ".join(refs.to_code_points(text))


def labels_of(character: str, jibo: str) -> Labels:
    """The 字母 labels of one block.

    A 連彫活字 holds several characters, so no single code point follows from it: it keeps the code
    points of its transcription and stays `unassessed`, and its 字母 belong in `upstream`. A block
    of one kana whose 字母 names one code point of `refs.candidates(character)` is `identified`,
    several leave it `ambiguous` with equal `p`. A 字母 that the reference table does not carry (尓
    for に, 止 for と) leaves the form undecided, so the unit stays `unassessed` with the transcribed
    code point and the 字母 beside it. The 字母 column is read without its trailing filler.
    """
    aligned = jibo.strip()
    if len(character) > 1:
        return Labels(BLOCK, sequence(character), Classification.UNASSESSED, [], [], script_of(character))
    # A 字母 names a hiragana or hentaigana form. The alternate katakana of Unicode 18.0 share the
    # reading and can share the 字母 (𛄧 and 子), but a kana transcription never means one of them.
    matched = [code_point for code_point in refs.candidates(character)
               if aligned and refs.jibo(code_point) == aligned
               and refs.script_of(refs.to_char(code_point)) in (Script.HIRAGANA, Script.HENTAIGANA)]
    if len(matched) == 1:
        return Labels(
            CHAR,
            matched[0],
            Classification.IDENTIFIED,
            [Candidate(unicode=matched[0], p=1.0)],
            refs.jibo_of(matched[0]),
            script_of(refs.from_code_points(matched)),
        )
    if matched:
        equal = 1.0 / len(matched)
        return Labels(
            CHAR,
            None,
            Classification.AMBIGUOUS,
            [Candidate(unicode=code_point, p=equal) for code_point in matched],
            refs.jibo_of(matched[0]),
            script_of(refs.from_code_points(matched)),
        )
    kana = bool(refs.candidates(character))
    return Labels(
        CHAR,
        sequence(character),
        Classification.UNASSESSED if kana else Classification.IDENTIFIED,
        [],
        refs.jibo_of(sequence(character)) if kana else [],
        script_of(character),
    )


def box_of(x1: int, y1: int, x2: int, y2: int) -> Box:
    """The box of a block: `x2` and `y2` are exclusive, so they give the width and the height."""
    return Box(x=x1, y=y1, w=x2 - x1, h=y2 - y1)


def union(boxes: list[Box]) -> Box:
    """The smallest box that holds every box of a line."""
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.w for box in boxes)
    bottom = max(box.y + box.h for box in boxes)
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def page_seq(stem: str) -> int:
    """The reading order of a page: `001_002_1` -> 3.

    The two halves of a photographed spread are numbered together, so `_1` takes the odd and `_2`
    the even place; where the archive holds one half only, the place of the other stays empty.
    """
    _, number, half = stem.split("_")
    return int(number) * 2 - (2 - int(half))


def page_id(stem: str) -> str:
    """The id of a page: `001_002_1` -> `codh-omt:001:001_002_1`."""
    return f"{DOCUMENT_ID}:{stem}"


def line_id(stem: str, line: str) -> str:
    """The id of a line: `codh-omt:001:001_002_1:L3`."""
    return f"{page_id(stem)}:L{line}"


def source_file() -> dict:
    """`data/sources/codh-kokatsuji.yaml`: the licence, attribution and evidence of the archive."""
    return yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))


def source_rights(raw: dict) -> Rights:
    """The rights of the records, from the source file, dated by the licence vocabulary."""
    checked = rights.resolve(licence=raw.get("licence"), url=raw.get("licence_evidence")).checked
    return Rights(
        licence=Licence(raw["licence"]),
        holder=HOLDER,
        attribution=raw["attribution"],
        evidence=raw.get("licence_evidence"),
        checked=checked,
    )


def document_of(record_rights: Rights) -> Document:
    """The one work of the archive, 『徒然草 2巻』 of the 国立国会図書館."""
    return Document(
        id=DOCUMENT_ID,
        title=TITLE,
        source_refs={SOURCE: "001", "ndl-pid": NDL_PID},
        holder=HOLDER,
        production=Production.MOVABLE_TYPE,
        text_register=Register.WABUN,
        dating=[Dating(literal=DATING_LITERAL, start=DATING_START, end=DATING_END, kind="publication")],
        image_rights=record_rights,
        text_rights=record_rights,
        meta={"author": AUTHOR, "ndl_url": NDL_URL},
    )


def unit_of(row: dict) -> Unit:
    """One block row of `dataset.csv` as a unit.

    `seq` counts from zero along the line, so the 1-based `count` column is kept in `upstream`.
    """
    character = row["character"]
    stem = Path(row["page"]).stem
    labels = labels_of(character, row["jibo"])
    return Unit(
        id=f"{DOCUMENT_ID}:{row['ID']}",
        document_id=DOCUMENT_ID,
        page_id=page_id(stem),
        line_id=line_id(stem, row["line"]),
        seq=int(row["count"]) - 1,
        box=box_of(int(row["x1"]), int(row["y1"]), int(row["x2"]), int(row["y2"])),
        kind=kind_of(character),
        granularity=labels.granularity,
        text_source=character,
        reading=character,
        unicode=labels.unicode,
        classification=labels.classification,
        script=labels.script,
        candidates=labels.candidates,
        method="import",
        review=ReviewState.TRANSCRIBER,
        upstream={
            "source": SOURCE,
            "ref": row["ID"],
            "block": row["block"],
            "count": row["count"],
            "jibo_sequence": row["jibo"],
        },
    )


def line_of(stem: str, number: str, rows: list[dict]) -> Line:
    """The line that holds the blocks of one `page` and `line` value, in `count` order."""
    ordered = sorted(rows, key=lambda row: int(row["count"]))
    text = "".join(row["character"] for row in ordered)
    boxes = [box_of(int(row["x1"]), int(row["y1"]), int(row["x2"]), int(row["y2"])) for row in ordered]
    return Line(
        id=line_id(stem, number),
        page_id=page_id(stem),
        seq=int(number),
        box=union(boxes),
        text_raw=text,
        text=text,
    )


def read(zip_path: Path, *, pages_dir: Path) -> tuple[Document, list[Page], list[Line], list[Unit]]:
    """Read the archive into records, with its page images registered in the image cache.

    The corrected page images are written under `pages_dir` and registered under the key
    `file:{path}`, so that the pages table addresses them without a further request and a second
    import over the same directory writes nothing. Raises `FileNotFoundError` for a missing archive.
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"{zip_path}: the 古活字データセット archive is not there; get it from {DATASET_URL}")
    with zipfile.ZipFile(zip_path) as archive:
        prefix = _prefix(archive)
        rows = _rows(archive, prefix)
        files = _extract_pages(archive, prefix, pages_dir)
    raw = source_file()
    record_rights = source_rights(raw)
    document = document_of(record_rights)
    registered = {stem: images.register(path, f"file:{path.as_posix()}") for stem, path in sorted(files.items())}
    _warn_on_jibo(rows)
    pages = _pages(rows, registered, pages_dir)
    lines = _lines(rows)
    units = [unit_of(row) for row in rows]
    return document, pages, lines, units


def import_all(out: Path, *, zip_path: Path | None = None) -> dict[str, int]:
    """Import the archive into `out` and return the row count of every table written.

    `zip_path` defaults to `cache/codh/001.zip`. The four tables are written and read again through
    `tables.Dataset.merge`, which sorts them and writes `MANIFEST.json`.
    """
    out = Path(out)
    source = Path(zip_path) if zip_path is not None else DEFAULT_ZIP
    document, pages, lines, units = read(source, pages_dir=out / "images")
    tables.write(out / "documents.parquet", [document], Document)
    tables.write(out / "pages.parquet", pages, Page)
    tables.write(out / "lines.parquet", lines, Line)
    tables.write(out / "units.parquet", units, Unit)
    return tables.Dataset(out).merge([], out, command=f"atlas import kokatsuji --zip {source}")


def _prefix(archive: zipfile.ZipFile) -> str:
    """The directory of the archive that holds `dataset.csv`, `001/` as it is published."""
    found = next((name for name in archive.namelist() if name.endswith(f"/{CSV_NAME}")), None)
    if found is None:
        raise ValueError(f"{archive.filename}: no {CSV_NAME} in the archive")
    return found[: -len(CSV_NAME)]


def _rows(archive: zipfile.ZipFile, prefix: str) -> list[dict]:
    """Every row of `dataset.csv`, in file order."""
    with archive.open(f"{prefix}{CSV_NAME}") as handle:
        return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig")))


def _extract_pages(archive: zipfile.ZipFile, prefix: str, pages_dir: Path) -> dict[str, Path]:
    """Write the corrected page images under `pages_dir` and return them by page stem.

    A file that is already there with the size the archive states is left alone, so a second import
    writes no image again.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for entry in archive.infolist():
        if entry.is_dir() or not entry.filename.startswith(f"{prefix}{PAGE_DIR}/"):
            continue
        name = Path(entry.filename).name
        if not name.lower().endswith(".jpg"):
            continue
        target = pages_dir / name
        if not target.is_file() or target.stat().st_size != entry.file_size:
            with archive.open(entry) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
        found[Path(name).stem] = target
    return found


def _pages(rows: list[dict], registered: dict[str, images.ImageRecord], pages_dir: Path) -> list[Page]:
    """One page per corrected page image, plus a sizeless page for a page the CSV names without one."""
    stems = set(registered) | {Path(row["page"]).stem for row in rows}
    missing = sorted(stem for stem in stems if stem not in registered)
    if missing:
        warnings.warn(
            f"{len(missing)} pages of {CSV_NAME} have no image in the archive: {', '.join(missing)}",
            UserWarning,
            stacklevel=3,
        )
    pages = []
    for stem in sorted(stems):
        record = registered.get(stem)
        _, number, half = stem.split("_")
        pages.append(
            Page(
                id=page_id(stem),
                document_id=DOCUMENT_ID,
                seq=page_seq(stem),
                image=record.url if record else f"file:{(pages_dir / f'{stem}.jpg').as_posix()}",
                width=record.width if record else 0,
                height=record.height if record else 0,
                sha256=record.sha256 if record else None,
                transcription={"source": SOURCE, "entry": "001"},
                meta={"spread": f"{DOCUMENT_ID}:{stem.split('_')[0]}_{number}", "half": int(half)},
            )
        )
    return pages


def _lines(rows: list[dict]) -> list[Line]:
    """One line per `page` and `line` value, in the order the CSV lists them."""
    groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    for row in rows:
        groups.setdefault((row["page"], row["line"]), []).append(row)
    return [line_of(Path(page).stem, number, group) for (page, number), group in groups.items()]


def _warn_on_jibo(rows: list[dict]) -> None:
    """Warn about the rows whose 字母 column does not line up with their characters."""
    shorter = sum(1 for row in rows if len(row["jibo"].strip()) < len(row["character"]))
    longer = sum(1 for row in rows if len(row["jibo"].strip()) > len(row["character"]))
    if not shorter and not longer:
        return
    warnings.warn(
        f"{shorter + longer} of {len(rows)} rows of {CSV_NAME} state a jibo of a different length "
        f"than character ({shorter} shorter, {longer} longer); upstream jibo_sequence is kept as given",
        UserWarning,
        stacklevel=3,
    )
