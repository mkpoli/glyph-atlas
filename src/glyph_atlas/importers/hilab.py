"""Import the くずし字データセット of the 東京大学史料編纂所 (HI Lab).

The archive `all.zip` holds one JPEG per crop under `all/characters/U+XXXX/{id}.jpg`, with `{id}` the
record id of the 電子くずし字字典データベース; a `characters/` tree at the root repeats 297,734 of those
crops and is ignored. The archive carries no metadata: the folder names the code point, the
transcription is the character itself, and the record page of the database takes the numeric id. The
published dataset is one corpus, so it becomes one document, and every crop becomes one unit with no
page and no box.

`RemoteZip` reads the listing through range requests and caches it under `cache/zipindex`, so an
import without `download` reads the listing and no crop, and `crop_sha256` stays empty. With
`download` the members are extracted below `cache/hilab` in batches and every crop is measured:
`crop_sha256` on the unit, `width` and `height` in `upstream`, where a unit keeps the fields its
schema has no column for. A member already on disk with the size the central directory states is not
fetched again.

The archive holds 325,261 crops in 5,896 code-point folders, 47,477 of them kana: the counts
`data/sources/hi-lab-kuzushiji.yaml` records and the importer returns under `code_points` and `kana`.
The root `characters/` subtree holds 5,886 of those code points; the full tree adds ten more, the
hiragana digraph ゟ U+309F and nine kanji of a single crop each (U+5F06 弆, U+61AB 憫, U+622E 戮,
U+62F5 拵, U+6B53 歓, U+7B9A 箚, U+820C 舌, U+924B 鉋, U+959E 閞).

The kana count takes both kana blocks whole, the iteration marks ゝゞゟ counting with the hiragana
letters and ヽヾヿ with the katakana ones, which is how the archive files them and how its published
count of 47,477 is reached. A rule that reads only the kana letters, as the CODH importer's
`script_of` does, leaves those marks `symbol` and counts 47,198.

Two folders that hold the same numeric id stop the import: the id of a unit is `hi:{id}`, and a
second unit with the same id would leave the units table ambiguous.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

import yaml
from PIL import Image

from .. import refs, rights, tables
from ..registry import SOURCES
from ..remotezip import Entry, RemoteZip
from ..schema import (
    Classification,
    Document,
    Licence,
    Production,
    ReviewState,
    Rights,
    Script,
    Unit,
    UnitKind,
)

#: The source id of `data/sources/hi-lab-kuzushiji.yaml`.
SOURCE = "hi-lab-kuzushiji"
SOURCE_FILE = SOURCES / f"{SOURCE}.yaml"

DOCUMENT_ID = "hi:kuzushiji-2023"
TITLE = "くずし字データセット（東京大学史料編纂所）"
HOLDER = "東京大学史料編纂所"

#: The archive whose members are the crops, and the tree of it they sit in.
ARCHIVE = "all.zip"
PREFIX = "all/characters/"
#: Where `download` writes the extracted crops.
CROPS = Path("cache") / "hilab"

FOLDER = re.compile(r"^U\+(?P<code>[0-9A-Fa-f]{4,6})$")
CROP = re.compile(r"^(?P<id>\d+)\.jpg$", re.IGNORECASE)

#: The kana letters of the two kana blocks, with the iteration marks and ligatures filed beside them.
HIRAGANA = frozenset({0x309D, 0x309E, 0x309F})
KATAKANA = frozenset({0x30FD, 0x30FE, 0x30FF})
#: Repeat marks: 々〱〲ゝゞヽヾ.
ITERATION_MARKS = frozenset({0x3005, 0x3031, 0x3032, 0x309D, 0x309E, 0x30FD, 0x30FE})
#: Kana ligatures: ゟ and ヿ.
LIGATURES = frozenset({0x309F, 0x30FF})
VOICING_MARKS = frozenset({0x3099, 0x309A, 0x309B, 0x309C})
PUNCTUATION = frozenset({0x3001, 0x3002, 0x3003, 0x30FB, 0xFF0C, 0xFF0E})

KANA = (Script.HIRAGANA, Script.KATAKANA, Script.HENTAIGANA)


class DuplicateIdError(ValueError):
    """Two folders hold crops with the same numeric id."""

    def __init__(self, numeric_id: str, first: str, second: str) -> None:
        self.numeric_id = numeric_id
        self.names = (first, second)
        super().__init__(
            f"hi:{numeric_id} is in two folders, {first} and {second}: "
            "the ids of the units would not be unique"
        )


class Member(NamedTuple):
    """One crop of the archive: the code point of its folder, its record id and its member name."""

    code_point: int
    numeric_id: str
    name: str

    @property
    def character(self) -> str:
        return chr(self.code_point)


class Measured(NamedTuple):
    """What reading a crop off the disk says about it."""

    sha256: str
    width: int
    height: int


def source_file() -> dict:
    """`data/sources/hi-lab-kuzushiji.yaml`: the licence, the attribution and the archive."""
    return yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))


def script_of(code_point: int) -> Script:
    """The script of a code point, by the block it lies in, with the character layer for the rest.

    The archive files the iteration marks ゝゞゟ beside the hiragana letters and ヽヾヿ beside the
    katakana ones, and its kana crops are counted with them, so both kana blocks are taken whole
    apart from ・ and ー, which are punctuation and a sound mark rather than letters. What the two
    blocks do not cover is a question for the character layer, which reaches every kana Unicode
    assigns, a hentaigana and one of the alternate katakana of Unicode 18.0 alike. A kanji is
    labelled `kanji` here, which is what the records of this source already say.
    """
    if 0x3041 <= code_point <= 0x3096 or code_point in HIRAGANA:
        return Script.HIRAGANA
    if 0x30A1 <= code_point <= 0x30FA or code_point in KATAKANA:
        return Script.KATAKANA
    if _is_ideograph(code_point):
        return Script.HAN
    if 0x0020 <= code_point <= 0x024F:
        return Script.LATIN
    script = refs.script_of(chr(code_point))
    return Script.SYMBOL if script is Script.UNKNOWN else script


def _is_ideograph(code_point: int) -> bool:
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x3134F
    )


def kind_of(code_point: int) -> UnitKind:
    """What one crop is: a character, a repeat mark, a ligature, a voicing mark or punctuation.

    The rules are the ones the CODH importer uses, the two archives holding the same marks.
    """
    if code_point in ITERATION_MARKS:
        return UnitKind.ITERATION_MARK
    if code_point in LIGATURES:
        return UnitKind.LIGATURE
    if code_point in VOICING_MARKS:
        return UnitKind.VOICING_MARK
    if code_point in PUNCTUATION:
        return UnitKind.PUNCTUATION
    return UnitKind.CHAR


def rights_of(raw: dict) -> Rights:
    """CC BY 4.0 with the attribution the source file states, dated by the licence vocabulary."""
    return Rights(
        licence=Licence(raw["licence"]),
        holder=HOLDER,
        attribution=raw["attribution"],
        evidence=raw.get("licence_evidence"),
        checked=rights.resolve(licence=raw.get("licence"), url=raw.get("licence_evidence")).checked,
    )


def document_of(raw: dict) -> Document:
    """The one document: the dataset as published, which every standalone crop reaches rights through."""
    record_rights = rights_of(raw)
    return Document(
        id=DOCUMENT_ID,
        title=TITLE,
        source_refs={SOURCE: str(raw.get("released") or "")},
        holder=HOLDER,
        production=Production.UNKNOWN,
        image_rights=record_rights,
        text_rights=record_rights,
        meta={
            "kind": "dataset",
            "doi": raw.get("doi"),
            "released": raw.get("released"),
            "zip": raw["access"]["zip"],
            "zip_bytes": raw["access"]["zip_bytes"],
            "record_page": raw["access"]["record_page"],
            "counts": raw.get("counts"),
        },
    )


def record_url(raw: dict, numeric_id: str) -> str:
    """The 電子くずし字字典データベース page of one record, from the template of the source file."""
    return str(raw["access"]["record_page"]).format(id=numeric_id)


def member_of(name: str) -> Member | None:
    """The crop a member holds, or None when it is not `all/characters/U+XXXX/{id}.jpg`."""
    parts = name[len(PREFIX) :].split("/") if name.startswith(PREFIX) else []
    if len(parts) != 2:
        return None
    folder, crop = FOLDER.match(parts[0]), CROP.match(parts[1])
    if folder is None or crop is None:
        return None
    return Member(code_point=int(folder["code"], 16), numeric_id=crop["id"], name=name)


def in_unicode_folder(name: str) -> bool:
    """Whether a member sits in a `U+XXXX` folder, the folders the archive groups crops by."""
    parts = name[len(PREFIX) :].split("/") if name.startswith(PREFIX) else []
    return len(parts) > 1 and FOLDER.match(parts[0]) is not None


def members_of(entries: Iterable[Entry], counts: Counter[str]) -> list[Member]:
    """Every crop of the listing, in listing order, with the other members counted.

    `counts["skipped"]` counts the members below `all/characters/` that are not a crop; of those,
    `counts["non_unicode"]` counts the ones whose folder is missing or is not named `U+XXXX`, such as
    `.DS_Store`. Directory entries are not members and are not counted. Raises `DuplicateIdError`
    when a numeric id appears in two folders.
    """
    found: list[Member] = []
    seen: dict[str, str] = {}
    for entry in entries:
        if entry.directory:
            continue
        member = member_of(entry.name)
        if member is None:
            counts["skipped"] += 1
            if not in_unicode_folder(entry.name):
                counts["non_unicode"] += 1
            continue
        first = seen.setdefault(member.numeric_id, member.name)
        if first != member.name:
            raise DuplicateIdError(member.numeric_id, first, member.name)
        found.append(member)
    return found


def unit_of(member: Member, raw: dict, measured: Measured | None = None) -> Unit:
    """One crop as a unit: no page, no box, its code point from the folder it was filed in.

    A kanji is `identified`, the character being its own transcription; a kana form stays
    `unassessed`, since the code point of the folder says which kana the crop was filed as rather
    than which form it shows. `measured` fills `crop_sha256` and puts the size of the crop in
    `upstream`, which is the only place a unit has for it.
    """
    script = script_of(member.code_point)
    upstream = {
        "source": SOURCE,
        "ref": member.numeric_id,
        "url": record_url(raw, member.numeric_id),
    }
    if measured is not None:
        upstream["width"], upstream["height"] = str(measured.width), str(measured.height)
    return Unit(
        id=f"hi:{member.numeric_id}",
        document_id=DOCUMENT_ID,
        page_id=None,
        crop=f"{ARCHIVE}!{member.name}",
        crop_sha256=measured.sha256 if measured is not None else None,
        kind=kind_of(member.code_point),
        granularity="char",
        text_source=member.character,
        reading=member.character,
        unicode=f"U+{member.code_point:04X}",
        classification=Classification.UNASSESSED if script in KANA else Classification.IDENTIFIED,
        script=script,
        method="import",
        review=ReviewState.TRANSCRIBER,
        upstream=upstream,
    )


def measure(archive: RemoteZip, members: list[Member], dest: Path, batch: int) -> dict[str, Measured]:
    """Extract the crops below `dest` in batches, and measure every one of them by member name.

    A member whose file is already below `dest` with the size the central directory states is not
    fetched again, so a second run over the same directory reads the archive for nothing.
    """
    measured: dict[str, Measured] = {}
    for start in range(0, len(members), batch):
        group = members[start : start + batch]
        missing = [member.name for member in group if not _extracted(archive, member, dest)]
        if missing:
            archive.extract(missing, dest)
        for member in group:
            measured[member.name] = _measure(dest / relative(member.name))
    return measured


def relative(name: str) -> Path:
    """A member name as the path `RemoteZip.extract` writes below its destination."""
    return Path(*name.split("/"))


def import_all(
    out: Path,
    *,
    download: bool = False,
    limit: int | None = None,
    batch: int = 500,
    url: str | None = None,
    crops: Path | None = None,
    listing: Path | None = None,
) -> dict[str, int]:
    """Import the dataset into `out` and return the row counts and what the listing held.

    `out` gets `documents.parquet`, holding the one document, and `units.parquet`, holding one unit
    per crop. `limit` keeps the first that many crops in listing order. With `download` the crops are
    extracted below `crops`, `cache/hilab` by default, in batches of `batch` and measured; without it
    the listing is the only request and `crop_sha256` stays empty. `url` and `listing` stand in for
    the archive and its zip listing cache. The returned mapping holds the counts `tables.Dataset`
    reports, plus `code_points`, `kana`, `skipped`, `non_unicode` and `requests`.
    """
    out = Path(out)
    raw = source_file()
    dest = Path(crops) if crops is not None else CROPS
    if batch < 1:
        raise ValueError(f"batch counts members per extraction and must be at least 1, not {batch!r}")
    counts: Counter[str] = Counter(skipped=0, non_unicode=0)
    with RemoteZip(url or raw["access"]["zip"], cache_root=listing) as archive:
        members = members_of(archive.iter_prefix(PREFIX), counts)
        if limit is not None:
            members = members[: max(0, limit)]
        measured = measure(archive, members, dest, batch) if download else {}
        units = [unit_of(member, raw, measured.get(member.name)) for member in members]
        counts["requests"] = archive.requests_made()
    tables.write(out / "documents.parquet", [document_of(raw)], Document)
    tables.write(out / "units.parquet", units, Unit)
    counts.update(tables.Dataset(out).merge([], out, command=_command(download, limit)))
    counts["code_points"] = len({unit.unicode for unit in units})
    counts["kana"] = sum(1 for unit in units if unit.script in KANA)
    return dict(sorted(counts.items()))


def _command(download: bool, limit: int | None) -> str:
    """The command recorded in `MANIFEST.json`, as the CLI would spell it."""
    parts = ["atlas import hilab"]
    if download:
        parts.append("--download")
    if limit is not None:
        parts.append(f"--limit {limit}")
    return " ".join(parts)


def _extracted(archive: RemoteZip, member: Member, dest: Path) -> bool:
    """Whether the crop is already below `dest` with the size the central directory states."""
    path = dest / relative(member.name)
    return path.is_file() and path.stat().st_size == archive.entry(member.name).file_size


def _measure(path: Path) -> Measured:
    """The sha256, the width and the height of one extracted crop."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    with Image.open(path) as image:
        width, height = image.size
    return Measured(digest.hexdigest(), width, height)
