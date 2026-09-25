"""Import the basic dataset of 漢字字体規範史データセット (HNG, Hanzi Normative Glyphs).

The clone holds one folder per source, `{NN}_{title}`, with the crops under `glyphs/BMP/`, and at its
root `all_table_v.5.0_2019-01-15.csv`, the index. The index has two header lines: the first names the
source of every group of three columns (`jou_P2179`), the second names the columns, eleven about the
character (見出し文字, 異体字, 統合ID, 大字典, 大漢和, 部首, JIS文字, JIS包摂, UCS, 備考, 字体) and then
代表字形ID, 字体数 and 用例数 for each source. A row is one 字体 of a character: 統合ID `00350` is the
first, `00350b` the second. A cell's 代表字形ID names the crop, `{id}{glyph}.bmp` in most folders and
`{glyph}.bmp` in the rest; a crop stands for every occurrence of that 字体 in the source, and 用例数
counts them.

H80 and H81 are not in the index. Their folders' `char-card-info.csv` has the same shape (統合ID in
H80, 大字典番号 in H81; 文字, カード番号, 字体数, 用例数, 部首); a card number without a letter and
with 字体数 above one stands for crops `a`, `b`… of that card, filed under the index rows `{id}`,
`{id}b`… and counted by the card table's 用例数, 用例数2… columns. Their code point is looked up in the
index by 統合ID or 大字典, since some of their 文字 cells hold a number.

Every crop becomes a unit with no page and no box, its `crop` the file at the pinned commit of the
GitHub mirror and `crop_sha256` the checksum of the file in the clone. The code point is the index's
UCS, which is the character's standard form: the crop shows the 字体 of that source, which HNG
describes by the row it files it under rather than by a code point of its own. A cell whose crop is
not in its folder is counted under `missing` and skipped; a crop that no row names is counted under
`unused`.

The 63 folders become 63 documents. Title, category, date, 標準 and 公／私 come from HNG's source
list (`sources.ja.html`), copied into `data/sources/hng-basic-data.yaml`. The date is read into a
year interval: a year is itself; `a-b` spans both years; `N代` is a decade; `NC` a century, its
first or last third with `初` or `末`; `N-MC` spans the centuries; `y頃` is ten years either way;
初唐 is 618–712, 則天期 690–705, 高昌期 460–640 and 北宋期 960–1127. The literal stays as written.
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from .. import rights, tables
from ..registry import SOURCES
from ..schema import (
    Classification,
    Dating,
    Document,
    Licence,
    Production,
    Register,
    ReviewState,
    Rights,
    Script,
    Unit,
    UnitKind,
)
from .honkoku_data import cache_root, clone_revision

#: The source id of `data/sources/hng-basic-data.yaml`.
SOURCE = "hng-basic-data"
SOURCE_FILE = SOURCES / f"{SOURCE}.yaml"
HOLDER = "漢字字体規範史データセット保存会"

INDEX = "all_table_v.5.0_2019-01-15.csv"
FOLDER_INDEX = "char-card-info.csv"
CARD_TABLE = "card-char-info.csv"
DAIJITEN = "Daijiten_table_v.5.0_2019-01-15.csv"
#: The columns of the index before the first source group.
FIXED = 11
#: The 代表字形ID of a cell: a card number and the letter of the 字体, with stray spaces around it.
GLYPH = re.compile(r"^(?P<card>\d+)(?P<form>[a-z]?)$")

#: Holders of the Dunhuang manuscripts by the prefix of their shelfmark.
DUNHUANG = {"P": "Bibliothèque nationale de France", "S": "British Library"}

#: Named periods of HNG's 作成年代, as years.
PERIODS = {"初唐": (618, 712), "則天期": (690, 705), "高昌期": (460, 640), "北宋期": (960, 1127)}


class RevisionError(ValueError):
    """The clone is not at the commit the source file pins."""


@dataclass(frozen=True)
class Crop:
    """One representative crop: the source, the card and letter that name it, and what the index says."""

    source: str
    glyph: str
    path: str
    integrated_id: str
    headword: str
    ucs: str
    forms: str
    occurrences: str
    variant: str = ""
    daikanwa: str = ""
    mark: str = ""


def source_file() -> dict:
    """`data/sources/hng-basic-data.yaml`: the licence, the pinned commit and the 63 documents."""
    return yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))


def default_clone() -> Path:
    """`cache/hng-basic-data`, the clone `atlas import hng` reads without `--clone`."""
    return cache_root() / SOURCE


def interval(literal: str) -> tuple[int | None, int | None]:
    """The years an HNG 作成年代 covers, by the rules of the module docstring; (None, None) otherwise."""
    text = literal.strip().removesuffix("か")
    if text in PERIODS:
        return PERIODS[text]
    if m := re.fullmatch(r"(\d{3,4})", text):
        return int(m[1]), int(m[1])
    if m := re.fullmatch(r"(\d{3,4})-(\d{3,4})", text):
        return int(m[1]), int(m[2])
    if m := re.fullmatch(r"(\d{3,4})頃", text):
        return int(m[1]) - 10, int(m[1]) + 10
    if m := re.fullmatch(r"(\d{2,3})0代", text):
        return int(m[1]) * 10, int(m[1]) * 10 + 9
    if m := re.fullmatch(r"(\d{1,2})-(\d{1,2})C", text):
        return (int(m[1]) - 1) * 100 + 1, int(m[2]) * 100
    if m := re.fullmatch(r"(\d{1,2})C(初|末)?", text):
        start, end = (int(m[1]) - 1) * 100 + 1, int(m[1]) * 100
        if m[2] == "初":
            return start, start + 32
        if m[2] == "末":
            return end - 33, end
        return start, end
    return None, None


def production_of(category: str) -> Production:
    """写本 is a manuscript and 版 a woodblock print; 開成石経, cut in stone, is neither."""
    if "写本" in category or "寫本" in category:
        return Production.MANUSCRIPT
    if "版" in category or "刊本" in category or "印刻本" in category:
        return Production.WOODBLOCK
    return Production.UNKNOWN


def rights_of(raw: dict) -> Rights:
    """CC BY-SA 4.0 with the attribution the source file states."""
    return Rights(
        licence=Licence(raw["licence"]),
        holder=HOLDER,
        attribution=raw["attribution"],
        evidence=raw.get("licence_evidence"),
        checked=rights.resolve(licence=raw.get("licence"), url=raw.get("licence_evidence")).checked,
    )


def document_of(entry: dict, raw: dict) -> Document:
    """One source as a document: dated, with holder and shelfmark for a Dunhuang manuscript."""
    literal = str(entry.get("date") or "")
    start, end = interval(literal)
    production = production_of(entry["category"])
    kind = {Production.MANUSCRIPT: "copying", Production.WOODBLOCK: "publication"}.get(production, "unknown")
    dating = [
        Dating(literal=literal, start=start, end=end, kind=kind, evidence=raw["access"]["source_list"])
    ] if literal else []
    shelf = re.match(r"^(?P<collection>[PS])(?P<number>\d+)", str(entry["short"]))
    record_rights = rights_of(raw)
    return Document(
        id=f"hng:{entry['id']}",
        title=entry["title"],
        source_refs={SOURCE: entry["code"]},
        holder=DUNHUANG[shelf["collection"]] if shelf else None,
        shelfmark=f"{shelf['collection']}.{shelf['number']}" if shelf else None,
        production=production,
        genre=list(entry.get("genre") or []),
        text_register=Register.KANBUN,
        dating=dating,
        image_rights=record_rights,
        text_rights=record_rights,
        meta={
            "hng_code": entry["code"],
            "hng_id": entry["id"],
            "short": entry["short"],
            "category": entry["category"],
            "standard": bool(entry.get("standard")),
            "sphere": entry.get("sphere"),
            "folder": entry["folder"],
        },
    )


def read_index(clone: Path) -> tuple[list[str], list[list[str]]]:
    """The source label of every column and the body rows of the index."""
    with (clone / INDEX).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[2:]


def crops_of(clone: Path, entries: list[dict], counts: Counter[str]) -> Iterator[Crop]:
    """Every crop the index and the two folder tables name, source by source in the order of `entries`.

    `counts["missing"]` counts cells whose crop is not in the folder, `counts["unused"]` crops no cell
    names, and `counts["unlisted"]` index sources that have no folder in the dataset.
    """
    labels, body = read_index(clone)
    columns = {labels[i].split("_", 1)[0]: i for i in range(FIXED, len(labels), 3) if labels[i]}
    by_id = {row[2]: row for row in body}
    by_daijiten = {row[3]: row for row in body if row[3] and not row[2][-1:].isalpha()}
    daijiten = read_daijiten(clone)
    ids = {entry["id"] for entry in entries}
    counts["unlisted"] += sum(1 for code in columns if code not in ids)
    for entry in entries:
        folder = clone / entry["folder"] / "glyphs" / "BMP"
        present = {path.name for path in folder.iterdir()} if folder.is_dir() else set()
        named: set[str] = set()
        if entry["id"] in columns:
            cells = _index_cells(body, columns[entry["id"]])
        else:
            cells = _folder_cells(clone / entry["folder"] / FOLDER_INDEX, by_id, by_daijiten, daijiten)
        for glyph, row, forms, occurrences in _one_row_per_crop(list(cells), clone / entry["folder"], counts):
            name = next((n for n in (f"{entry['id']}{glyph}.bmp", f"{glyph}.bmp") if n in present), None)
            if name is None:
                counts["missing"] += 1
                continue
            named.add(name)
            yield Crop(
                source=entry["id"],
                glyph=glyph,
                path=f"{entry['folder']}/glyphs/BMP/{name}",
                integrated_id=row[2],
                headword=row[0],
                ucs=row[8],
                forms=forms,
                occurrences=occurrences,
                variant=row[1],
                daikanwa=row[4],
                mark=row[10],
            )
        counts["unused"] += len(present - named)


def _one_row_per_crop(
    cells: list[tuple[str, list[str], str, str]], folder: Path, counts: Counter[str]
) -> list[tuple[str, list[str], str, str]]:
    """The cells with one row per crop: where several rows name one crop, the row whose headword
    the folder's card table (`card-char-info.csv`, カード番号 then 文字) gives for that card.

    The others are counted under `conflicts`; a crop the card table does not settle is dropped
    with all its rows.
    """
    by_glyph: dict[str, list[tuple[str, list[str], str, str]]] = {}
    for cell in cells:
        by_glyph.setdefault(cell[0], []).append(cell)
    cards = _card_table(folder / CARD_TABLE) if any(len(v) > 1 for v in by_glyph.values()) else {}
    kept = []
    for glyph, rows in by_glyph.items():
        if len(rows) > 1:
            written = cards.get(GLYPH.match(glyph)["card"])
            match = [cell for cell in rows if cell[1][0] == written]
            counts["conflicts"] += len(rows) - len(match[:1])
            rows = match[:1]
        kept.extend(rows)
    return kept


def _card_table(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row[0].strip(): row[1].strip() for row in csv.reader(handle) if len(row) > 1}


def _index_cells(body: list[list[str]], column: int) -> Iterator[tuple[str, list[str], str, str]]:
    for row in body:
        glyph = row[column].strip() if len(row) > column else ""
        if GLYPH.match(glyph):
            yield glyph, row, row[column + 1].strip(), row[column + 2].strip()


def read_daijiten(clone: Path) -> dict[str, str]:
    """UCS by 大字典番号, from the Daijiten table beside the index."""
    path = clone / DAIJITEN
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["大字典番号"]: row["UCS"] for row in csv.DictReader(handle) if row["UCS"]}


def _folder_cells(
    path: Path, by_id: dict[str, list[str]], by_daijiten: dict[str, list[str]], daijiten: dict[str, str]
) -> Iterator[tuple[str, list[str], str, str]]:
    """The cells of a folder table, each with the index row its first column names.

    The first column is 統合ID in H80 and 大字典番号 in H81. A key the index lacks is looked up
    without its letter, then in the Daijiten table, and last read from 文字 when that is one character.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    by_key = by_daijiten if rows[0][0].startswith("大字典") else by_id
    per_form = _form_counts(path.parent / CARD_TABLE)
    for cells in rows[1:]:
        cells = [cell.strip() for cell in cells]
        if len(cells) < 5 or not GLYPH.match(cells[2]):
            continue
        row = by_key.get(cells[0]) or _unindexed(cells[0], cells[1], by_key, daijiten)
        card, forms = GLYPH.match(cells[2]), cells[3]
        if not card["form"] and forms.isdigit() and int(forms) > 1:
            counts = per_form.get(card["card"], [])
            for index, letter in enumerate("abcdefghij"[: int(forms)]):
                form_row = row if index == 0 else by_id.get(f"{row[2]}{letter}", row)
                yield f"{card['card']}{letter}", form_row, forms, counts[index] if index < len(counts) else ""
        else:
            yield cells[2], row, forms, cells[4]


def _form_counts(path: Path) -> dict[str, list[str]]:
    """用例数 of each 字体 by card number, from the card table's 用例数, 用例数2… columns in order."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    columns = [i for i, name in enumerate(rows[0]) if name.strip().startswith("用例数")]
    return {row[0].strip(): [row[i].strip() for i in columns if i < len(row)] for row in rows[1:] if row}


def _unindexed(key: str, written: str, by_key: dict[str, list[str]], daijiten: dict[str, str]) -> list[str]:
    """An index-shaped row for a key the index lacks: headword, 統合ID and UCS filled where known."""
    base = by_key.get(key.rstrip("bcdefghij"))
    ucs = base[8] if base else daijiten.get(key, "")
    single = len(written) == 1 and written != "〓"
    if not ucs and single:
        ucs = f"{ord(written):04X}"
    headword = written if single else chr(int(ucs, 16)) if ucs else ""
    return [headword, "", key, "", "", "", "", "", ucs, "", ""]


def unit_of(crop: Crop, raw: dict, revision: str, sha256: str | None) -> Unit:
    """One crop as a unit: no page, no box, the code point of the index row it is filed under."""
    ucs = crop.ucs.strip().upper()
    code_point = f"U+{ucs}" if re.fullmatch(r"[0-9A-F]{4,6}", ucs) else None
    if code_point is None:
        classification = Classification.UNIDENTIFIED if "未詳" in crop.integrated_id else Classification.UNENCODED
    else:
        classification = Classification.IDENTIFIED
    upstream = {
        "source": SOURCE,
        "ref": f"{crop.source}{crop.glyph}",
        "integrated_id": crop.integrated_id,
        "headword": crop.headword,
        "forms": crop.forms,
        "occurrences": crop.occurrences,
    }
    upstream.update({key: value for key, value in (
        ("variant", crop.variant), ("daikanwa", crop.daikanwa), ("mark", crop.mark)) if value})
    return Unit(
        id=f"hng:{crop.source}:{crop.glyph}",
        document_id=f"hng:{crop.source}",
        page_id=None,
        crop=raw["access"]["crop"].format(revision=revision, path=quote(crop.path)),
        crop_sha256=sha256,
        kind=UnitKind.CHAR,
        granularity="char",
        text_source=crop.headword or None,
        reading=crop.headword or None,
        unicode=code_point,
        classification=classification,
        script=Script.HAN,
        method="import",
        review=ReviewState.TRANSCRIBER,
        upstream=upstream,
    )


def import_all(
    out: Path,
    *,
    clone: Path | None = None,
    sources: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Import the clone into `out` and return the row counts and what the tables left out.

    `out` gets `documents.parquet` and `units.parquet`. `sources` keeps the HNG ids named (`jou`,
    `keg`…) and `limit` the first that many crops. Raises `RevisionError` when the clone is a git
    checkout at another commit than the source file pins.
    """
    out, clone = Path(out), Path(clone) if clone is not None else default_clone()
    raw = source_file()
    revision = raw["revision"]
    found = clone_revision(clone)
    if found is not None and found != revision:
        raise RevisionError(f"{clone} is at {found}; {SOURCE_FILE.name} pins {revision}")
    entries = [e for e in raw["documents"] if sources is None or e["id"] in sources]
    counts: Counter[str] = Counter(missing=0, unused=0, unlisted=0, conflicts=0)
    units: list[Unit] = []
    for crop in crops_of(clone, entries, counts):
        if limit is not None and len(units) >= limit:
            break
        units.append(unit_of(crop, raw, revision, _sha256(clone / crop.path)))
    documents = [document_of(entry, raw) for entry in entries]
    tables.write(out / "documents.parquet", documents, Document)
    tables.write(out / "units.parquet", units, Unit)
    counts.update(tables.Dataset(out).merge([], out, command=_command(sources, limit)))
    counts["code_points"] = len({unit.unicode for unit in units if unit.unicode})
    counts["unencoded"] = sum(1 for unit in units if unit.unicode is None)
    return dict(sorted(counts.items()))


def _command(sources: list[str] | None, limit: int | None) -> str:
    parts = ["atlas import hng"]
    if sources:
        parts.append(f"--sources {','.join(sources)}")
    if limit is not None:
        parts.append(f"--limit {limit}")
    return " ".join(parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
