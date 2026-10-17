"""Link each 振り仮名 of a transcription line to the units it stands over.

A transcriber who types `計（ばかり）` or `《振り仮名：鯡漁図｜ヘロキコイキ｜ニシントリ》` says how the
base was read at that place on the page. `marks` reads every ruby of a raw line with its base, its
side and where each field lies in the line; `link` joins a line's rubies to the units the aligner
placed for the base, which is how a crop learns the readings over it and over its word.

- The base is every document character inside the ruby, nested rubies included, without what a
  見せ消ち strikes out. A transcriber who did not cut a run of kanji gives a longer base, and the
  row keeps it.
- Each reading field is its own row: `right` is the 振り仮名, `left` the 左訓, which often glosses
  the meaning or gives a second language. The reading of `鯡漁図` above is ヘロキコイキ on the right
  and ニシントリ on the left.
- `span` is `mono` when the base is one character, so the reading is that character's, and `group`
  when one reading covers a run. A run is never split into per-character readings here: the markup
  does not say where one character's reading ends.
- A damage mark (■ □ 〓) belongs to the field it stands in, so の〓み is not のみ.

The transcription does not say whether a ruby stands on the page or was added by the transcriber
(decision 0004), so every row is `unchecked` until a reviewer looks.

Units are matched to the line's tokens by their transcribed text in reading order, as the review
context hints are (`review.context_suggestions`): `seq` counts placements, and a split, a merge or a
relabelled box moves it. A base token no unit matches is left out of `base_unit_ids`, so a ruby whose
base the aligner never placed has none.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import align, koji, tables
from .refs import to_hiragana
from .schema import Line, Ruby, Unit

#: Roles of a ruby's base: the document text without what a 見せ消ち strikes out.
BASE_ROLES = frozenset({"main", "warigaki", "gap", "unreadable", "inserted"})
#: Roles of a damage mark, which can stand in a reading as well as in a base.
DAMAGE_ROLES = frozenset({"gap", "unreadable"})
SIDES = {"ruby": "right", "ruby-left": "left"}


@dataclass
class Mark:
    """One ruby element of a raw line: its base and its reading fields, as typed."""

    element: int
    source: str
    base: str = ""
    base_offsets: list[int] = field(default_factory=list)
    readings: dict[str, str] = field(default_factory=dict)

    @property
    def span(self) -> str:
        return "mono" if len(self.base) == 1 else "group"

    @property
    def bounds(self) -> tuple[int | None, int | None]:
        """Where the base begins and ends in the raw line; unset for a ruby with no base."""
        if not self.base_offsets:
            return None, None
        return min(self.base_offsets), max(self.base_offsets) + 1


def marks(text: str) -> list[Mark]:
    """Every ruby of a raw line in document order, an enclosing ruby before the one it holds."""
    if "（" not in text and "《" not in text:
        return []
    parsed = koji.parse(text)
    elements = [element for element in koji.walk(parsed.nodes) if element.kind == "ruby"]
    if not elements:
        return []
    found = {element.id: Mark(element.id, _source(text, element)) for element in elements}
    fields = {element.id: _fields(text, element) for element in elements}
    for char in parsed.chars:
        mine = [node for node in char.path if node in found]
        if not mine:
            continue
        inner = mine[-1]
        side = SIDES.get(char.role)
        if side is None and char.role in DAMAGE_ROLES:
            side = _field_at(fields[inner], char.start)
        if side is not None:
            readings = found[inner].readings
            readings[side] = readings.get(side, "") + char.text
        elif char.role in BASE_ROLES:
            for ruby in mine:
                found[ruby].base += char.text
                found[ruby].base_offsets.append(char.start)
    return [found[element.id] for element in elements]


def _source(text: str, element: koji.Element) -> str:
    """The markup a ruby came from: the bracket's name, or `legacy` for `base（reading）`."""
    if element.attrs.get("form") == "legacy":
        return "legacy"
    inner = text[element.start + 1:element.end]
    return next(name for name in koji.BRACKET_KINDS if inner.startswith((f"{name}：", f"{name}:")))


def _fields(text: str, element: koji.Element) -> list[tuple[int, str]]:
    """Where the right and left reading fields of a ruby begin in the raw line.

    In `base（right｜left）` the fields follow the `（`, which a base never holds. In
    `《振り仮名：base｜right｜left》` each follows a `｜` outside the constructs nested in the base.
    """
    if element.attrs.get("form") == "legacy":
        start = text.index("（", element.start) + 1
        bounds = [start] + [at + 1 for at in range(start, element.end) if text[at] in "｜|"]
    else:
        depth, bounds = 0, []
        colon = min(at for at in (text.find("：", element.start), text.find(":", element.start)) if at >= 0)
        for at in range(colon + 1, element.end):
            char = text[at]
            if char in "《（":
                depth += 1
            elif char in "》）":
                depth -= 1
            elif char in "｜|" and depth == 0:
                bounds.append(at + 1)
    return list(zip(bounds, ("right", "left"), strict=False))


def _field_at(fields: list[tuple[int, str]], offset: int) -> str | None:
    side = None
    for start, name in fields:
        if offset >= start:
            side = name
    return side


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def unit_tokens(tokens: Sequence[align.Token], units: Sequence[Unit]) -> dict[int, list[str]]:
    """The ids of the units placed for each of a line's tokens, by token index.

    Units are matched by their transcribed text in reading order. The members of one 連綿 group
    were placed for one token and count once; 振り仮名 units and retired units are left out.
    """
    texts: list[str] = []
    ids: list[list[str]] = []
    groups: dict[str, int] = {}
    for unit in sorted(units, key=lambda unit: (unit.seq is None, unit.seq or 0, unit.id)):
        if not unit.active or not unit.text_source or unit.upstream.get("role") in SIDES:
            continue
        if unit.group_id and unit.group_id in groups:
            ids[groups[unit.group_id]].append(unit.id)
            continue
        if unit.group_id:
            groups[unit.group_id] = len(texts)
        texts.append(_normalise(unit.text_source))
        ids.append([unit.id])
    found: dict[int, list[str]] = {}
    matcher = SequenceMatcher(None, texts, [_normalise(token.text) for token in tokens], autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            found[block.b + offset] = ids[block.a + offset]
    return found


def link(line: Line, units: Sequence[Unit]) -> list[Ruby]:
    """The rubies of one line, each with the units its base was placed on."""
    found = marks(line.text_raw)
    if not found:
        return []
    tokens = align.tokens_of(line)
    placed = unit_tokens(tokens, units)
    rows = []
    for mark in found:
        offsets = set(mark.base_offsets)
        base_units = [unit_id
                      for index, token in enumerate(tokens)
                      if any(token.start <= at < token.end for at in offsets)
                      for unit_id in placed.get(index, [])]
        start, end = mark.bounds
        for side, typed in mark.readings.items():
            rows.append(Ruby(
                id=f"{line.id}:ruby{mark.element}:{side}",
                page_id=line.page_id,
                line_id=line.id,
                base=mark.base,
                base_start=start,
                base_end=end,
                base_unit_ids=base_units,
                side=side,
                text=typed,
                reading=to_hiragana("".join(typed.split())),
                span=mark.span,
                source=mark.source,
            ))
    return rows


def build(lines: Iterable[Line], units: Iterable[Unit]) -> Iterator[Ruby]:
    """The rubies of every line, joined to the units of the same line."""
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.line_id is not None:
            by_line[unit.line_id].append(unit)
    for line in lines:
        yield from link(line, by_line.get(line.id, ()))


def build_dataset(directory: Path, *, command: str | None = None) -> int:
    """Write `rubies.parquet` for one dataset from its lines and units; return the row count.

    Only lines holding ruby markup are read. Lines and units sharded by document are read shard by
    shard, since a line's units share its shard; otherwise the units are read whole.
    """
    dataset = tables.Dataset(directory)
    lines_path, units_path = dataset.tables["lines"], dataset.tables["units"]
    if lines_path is None:
        raise FileNotFoundError(f"{directory} has no lines table")
    pairs: list[tuple[Path, Path | None]]
    if lines_path.is_dir() and units_path is not None and units_path.is_dir():
        pairs = [(shard, units_path / shard.name) for shard in tables.table_files(lines_path)]
    else:
        pairs = [(lines_path, units_path)]
    rows: list[Ruby] = []
    for lines_file, units_file in pairs:
        lines = [line for batch in tables.scan(lines_file, Line, keep=_has_markup) for line in batch]
        if not lines:
            continue
        units = ([unit for batch in tables.scan(units_file, Unit) for unit in batch]
                 if units_file is not None and units_file.exists() else [])
        rows.extend(build(lines, units))
    return tables.write(Path(directory) / "rubies.parquet", rows, Ruby, command=command)


def _has_markup(row: dict) -> bool:
    text = row.get("text_raw") or ""
    return "（" in text or "《" in text


def by_unit(rows: Iterable[Ruby]) -> dict[str, list[Ruby]]:
    """The rubies over each unit: its own (`mono`) and those over the run that holds it (`group`)."""
    found: dict[str, list[Ruby]] = defaultdict(list)
    for row in rows:
        for unit_id in row.base_unit_ids:
            found[unit_id].append(row)
    return dict(found)
