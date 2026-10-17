"""Crops whose character differs from the one their source text gives: candidates for a sense.

A transcriber who typed 鯡 where a reviewer reads the crop as 𩹉 shows that the page writes 𩹉 for
the word the transcriber knew as 鯡's: the herring. Dictionaries give 𩹉 as a fish like 鮒, so such a
pair is where a character's attested senses and its dictionary senses part, and where a
`writes_word` claim (`data/ledger.json`) is worth making. `candidates` lists them from a dataset's
review store, one row per crop, and `pairs` counts them by character and source text.

What a row holds:

- `character` is the crop's current character in the store; `source` is the text the dataset gave
  it (`Unit.text_source`), and `basis` says where that text came from: `transcription`, a person's
  transcription of the line; `ocr`, a machine reading (the Ainu records' OCR blocks); or the
  upstream source the crop was imported from. A machine reading that differs is more often a misread
  than a second sense.
- `relation` names the 異体字 relations the variant graph states between the two (`refs.variants`),
  so 鯡 and 𩹉 read `variant`; empty when the graph relates them in neither direction.
- `rubies` are the readings a 振り仮名 gives over the crop or its word (`rubies.parquet`, when the
  dataset has one), the strongest evidence of the sense the transcription itself holds.

Only Han characters on both sides are listed, and only crops a person has looked at (`review` past
`machine`), since a machine label that differs from the transcription is the aligner's guess.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import refs, tables
from ..schema import ReviewState, Ruby, Script

#: Review states that mean a person looked at the crop.
LOOKED = frozenset(state.value for state in ReviewState) - {ReviewState.MACHINE.value, ReviewState.TRANSCRIBER.value}
COLUMNS = ("character", "code_point", "source", "source_code_point", "basis", "relation", "rubies", "unit", "review",
           "document", "line", "context")


@dataclass(frozen=True)
class Candidate:
    character: str
    code_point: str
    source: str
    source_code_point: str
    basis: str
    relation: str
    rubies: str
    unit: str
    review: str
    document: str
    line: str
    context: str


def _han(char: str) -> bool:
    return len(char) == 1 and refs.script_of(char) is Script.HAN


def _basis(data: dict) -> str:
    """Where a crop's source text came from."""
    origin = (data.get("meta") or {}).get("ainu_records", {}).get("origin")
    if origin:
        return str(origin)
    if data.get("method") == "detect-align" or data.get("line_id"):
        return "transcription"
    return (data.get("upstream") or {}).get("source") or "unknown"


def _relation(a: str, b: str) -> str:
    """The relations the variant graph states between two characters, in either direction."""
    found = sorted({edge["relation"] for edge in refs.variant_edges_of(a) if b in (edge["a"], edge["b"])})
    return ",".join(found)


def _rubies(directory: Path) -> dict[str, list[str]]:
    """The readings over each unit, `base: reading`, from the dataset's rubies table."""
    path = directory / "rubies.parquet"
    if not path.exists():
        return {}
    over: dict[str, list[str]] = defaultdict(list)
    for batch in tables.scan(path, Ruby):
        for ruby in batch:
            for unit in ruby.base_unit_ids:
                over[unit].append(f"{ruby.base}: {ruby.text}" + ("" if ruby.side == "right" else " (left)"))
    return over


def _rows(store: Path) -> Iterator[dict]:
    """Every active unit of a review store whose character and source text are one code point each."""
    with sqlite3.connect(f"file:{store}?mode=ro", uri=True) as conn:
        query = ("SELECT data FROM units WHERE active=1 AND json_extract(data,'$.unicode') IS NOT NULL "
                 "AND length(json_extract(data,'$.text_source'))=1")
        for (data,) in conn.execute(query):
            yield json.loads(data)


def candidates(directory: Path, *, characters: Iterable[str] = ()) -> list[Candidate]:
    """The looked-at crops of a dataset whose Han character differs from their Han source text.

    `characters` limits the list to crops now filed under one of them.
    """
    directory = Path(directory)
    wanted = set(characters)
    over = _rubies(directory)
    found = []
    for data in _rows(directory / "review.sqlite"):
        points = str(data["unicode"]).split()
        if len(points) != 1 or str(data.get("review")) not in LOOKED:
            continue
        character, source = refs.to_char(points[0]), data["text_source"]
        if character == source or not (_han(character) and _han(source)) or (wanted and character not in wanted):
            continue
        context = (data.get("meta") or {}).get("ainu_records", {}).get("context") or ""
        found.append(Candidate(
            character=character, code_point=points[0], source=source, source_code_point=refs.to_code_point(source),
            basis=_basis(data), relation=_relation(character, source), rubies=" / ".join(over.get(data["id"], [])),
            unit=data["id"], review=str(data.get("review")), document=data.get("document_id") or "",
            line=data.get("line_id") or "", context=context))
    return sorted(found, key=lambda row: (row.code_point, row.source_code_point, row.document, row.unit))


def pairs(rows: Iterable[Candidate]) -> list[tuple[str, str, int, int]]:
    """`(character, source, crops, documents)` for each pair, most crops first."""
    crops: Counter[tuple[str, str]] = Counter()
    documents: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        crops[row.character, row.source] += 1
        documents[row.character, row.source].add(row.document)
    return [(character, source, count, len(documents[character, source]))
            for (character, source), count in sorted(crops.items(), key=lambda item: (-item[1], item[0]))]


def write(rows: list[Candidate], out: Path) -> None:
    """The candidates as TSV, one crop a row."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
