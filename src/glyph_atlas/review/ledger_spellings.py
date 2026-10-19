"""Observed word spellings from standing writes_word claims on current crops.

A claim names the word, not where the word starts and ends, so crops at consecutive places on a line
that claim the same word join only where their characters make a spelling of several characters the
other sources give for that word (而 and 已 as 而已 for ばかり); every other crop is an occurrence of its
own, and two 扠 in a row are two occurrences of 扠. A gloss stays on its crop; only claims naming a word
enter the spelling table.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .. import evidence, refs
from ..schema import Unit
from . import characters, ledger
from .store import Store

COLUMNS = ("word", "spelling", "code_points", "documents", "occurrences", "crops", "probable", "methods", "examples")
METHODS = ledger.PREDICATES["writes_word"]["methods"]


@dataclass
class Spelling:
    documents: set[str] = field(default_factory=set)
    occurrences: int = 0
    crops: int = 0
    probable: int = 0
    methods: set[str] = field(default_factory=set)
    examples: set[str] = field(default_factory=set)


@dataclass
class Crop:
    unit: Unit
    document: str | None
    word: str
    spelling: str
    probable: bool
    methods: set[str]


def _crops(store: Store) -> list[Crop]:
    found = []
    with store._connection() as conn:
        query = (f"SELECT {','.join('c.' + name for name in ledger.COLUMNS)},u.data,u.document_id "
                 "FROM current_claims c JOIN units u ON u.id=c.subject "
                 "WHERE c.predicate='writes_word' AND c.scope='' AND c.slot='' "
                 "AND c.status IN ('asserted','accepted','adjudicated') AND c.object IS NOT NULL AND u.active=1")
        for row in conn.execute(query):
            claim = ledger.current_row(row[:len(ledger.COLUMNS)])
            unit = Unit.model_validate_json(row["data"])
            if not unit.active or not unit.unicode:
                continue
            version = evidence.crop_version(unit.id, characters._source_digest(store, unit), unit.box)
            if version is None or claim["crop_version"] != version:
                continue
            supporting = [ledger.assertion(conn, identity) for identity in claim["supporting"]]
            spelling = "".join(refs.to_char(point) for point in unit.unicode.split())
            found.append(Crop(unit, row["document_id"], claim["object"], spelling,
                              bool(supporting) and all(a["confidence"] == 0.5 and a["confidence_scheme"] == "form"
                                                       for a in supporting),
                              {a["method"] for a in supporting if a["method"]}))
    return sorted(found, key=lambda crop: (crop.document or "", crop.unit.line_id or "",
                                          crop.unit.seq if crop.unit.seq is not None else -1, crop.unit.id))


def _known() -> dict[str, frozenset[str]]:
    """The spellings of several characters each word has from sources other than this table."""
    found: dict[str, set[str]] = {}
    for row in refs.word_spellings():
        if row["source"] != "atlas-ledger" and len(row["spelling"]) > 1:
            found.setdefault(row["word"], set()).add(row["spelling"])
    return {word: frozenset(spellings) for word, spellings in found.items()}


def _split(run: list[Crop], known: frozenset[str]) -> Iterable[list[Crop]]:
    """A run of consecutive crops claiming one word, as its occurrences: the most crops a known spelling
    covers from each place, else one crop."""
    i = 0
    while i < len(run):
        width = next((w for w in range(len(run) - i, 1, -1)
                      if "".join(crop.spelling for crop in run[i:i + w]) in known), 1)
        yield run[i:i + width]
        i += width


def count(directories: Iterable[Path]) -> list[dict]:
    """One row per word and spelling across the datasets, read without changing their stores."""
    found: dict[tuple[str, str], Spelling] = {}
    known = _known()

    def occurrence(crops: list[Crop]) -> None:
        key = crops[0].word, "".join(crop.spelling for crop in crops)
        row = found.setdefault(key, Spelling())
        row.documents.update(crop.document for crop in crops if crop.document)
        row.occurrences += 1
        row.crops += len(crops)
        row.probable += any(crop.probable for crop in crops)
        row.methods.update(method for crop in crops for method in crop.methods)
        row.examples.update(crop.unit.id for crop in crops)

    for directory in sorted({Path(path).resolve() for path in directories}):
        group: list[Crop] = []
        for crop in _crops(Store(directory, readonly=True)):
            if group:
                previous = group[-1]
                consecutive = (crop.unit.line_id and crop.unit.line_id == previous.unit.line_id
                               and crop.document == previous.document and crop.word == previous.word
                               and previous.unit.seq is not None and crop.unit.seq == previous.unit.seq + 1)
                if not consecutive:
                    for crops in _split(group, known.get(previous.word, frozenset())):
                        occurrence(crops)
                    group = []
            group.append(crop)
        for crops in _split(group, known.get(group[0].word, frozenset()) if group else frozenset()):
            occurrence(crops)
    return [{"word": word, "spelling": spelling,
             "code_points": " ".join(f"U+{ord(char):04X}" for char in spelling),
             "documents": len(row.documents), "occurrences": row.occurrences, "crops": row.crops,
             "probable": row.probable, "methods": " ".join(method for method in METHODS if method in row.methods),
             "examples": " ".join(sorted(row.examples)[:5])}
            for (word, spelling), row in sorted(found.items())]


def write(rows: list[dict], out: Path) -> None:
    """Write the cited table, retaining its date when its contents have not changed."""
    body = io.StringIO()
    body.write(
        "# Observed spellings of words from current crop claims; see src/glyph_atlas/review/ledger_spellings.py.\n"
        "# Generated by: atlas review ledger-spellings DIR... --out data/vocab/ledger-spellings.tsv\n"
        "# source atlas-ledger: Glyph Atlas reviewers' writes_word claims (https://glyphatlas.org); "
        "a reviewer's reading of one crop; tier observed\n")
    writer = csv.DictWriter(body, COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    content = body.getvalue()
    if out.exists():
        previous = out.read_text(encoding="utf-8")
        if "".join(line for line in previous.splitlines(keepends=True) if not line.startswith("# written: ")) == content:
            return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# written: {datetime.now(UTC).date().isoformat()}\n" + content, encoding="utf-8")
