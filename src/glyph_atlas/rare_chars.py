"""Find the Han characters that the みんなで翻刻 transcriptions attest in few documents.

A character counts where the transcriber wrote it as text of the document, after `koji.plain`:
振り仮名 readings and 注記 are left out. Rarity is the number of entries (documents) that attest a
character, since one document can repeat a character on every page.

For each rare character the report lists up to `examples` lines, each from a different entry, with
the 振り仮名 the transcriber gave the character and, when Honkoku-Lines holds the same line, its line
box. Honkoku-Lines uses the entry ids of みんなで翻刻 as `item_id` and counts pages from 0 in
`image_index`; its `line_index` follows a different line numbering than the page files, so a line is
matched by entry, page and raw text.

Transcribers are asked to type modern forms (https://wiki.honkoku.org/doku.php?id=guidelines), so a
rare form on the page is often counted under its common character, and a character that is rare here
can be a typing or conversion error. The report is a queue for review.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import koji
from .importers import honkoku_data
from .ruled_grid import is_han

#: Roles of `koji.Char` that are glyphs of the document.
DOCUMENT_ROLES = frozenset({"main", "warigaki", "inserted", "cancelled"})
#: Roles that hold a 振り仮名 reading.
READING_ROLES = frozenset({"ruby", "ruby-left"})
COLUMNS = (
    "code_point", "char", "documents", "occurrences", "project", "entry", "title", "page", "line",
    "reading", "text", "honkoku_url", "line_box", "image_license",
)


@dataclass
class Location:
    project: str
    entry: str
    title: str
    page: int
    line: int


@dataclass
class Counts:
    occurrences: Counter[str] = field(default_factory=Counter)
    documents: Counter[str] = field(default_factory=Counter)
    #: Up to `examples` locations per character, each from a different entry.
    examples: dict[str, list[Location]] = field(default_factory=dict)
    entries: int = 0
    pages: int = 0
    lines: int = 0


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def count(clone: Path, *, projects: list[str] | None = None, examples: int = 5) -> Counts:
    """Count every Han character of a clone's v3 transcriptions by occurrence and by entry."""
    counts = Counts()
    found, _, _ = honkoku_data.entries(clone, projects)
    for project, row in found:
        entry = row["id"].strip()
        title = (row.get("label") or "").strip()
        seen: set[str] = set()
        for page, path in honkoku_data.page_files(Path(clone) / "v3" / project / entry):
            counts.pages += 1
            for number, text in enumerate(_lines(path), 1):
                counts.lines += 1
                for char in koji.plain(text):
                    if not is_han(char):
                        continue
                    counts.occurrences[char] += 1
                    if char in seen:
                        continue
                    seen.add(char)
                    counts.documents[char] += 1
                    kept = counts.examples.setdefault(char, [])
                    if len(kept) < examples:
                        kept.append(Location(project, entry, title, page, number))
        counts.entries += 1
    return counts


def readings(text: str, char: str) -> list[str]:
    """The 振り仮名 of each document occurrence of `char` in a raw line; "" where it has none."""
    parsed = koji.parse(text)
    ruby = {element.id for element in koji.walk(parsed.nodes) if element.kind == "ruby"}
    found = []
    for glyph in parsed.chars:
        if glyph.text != char or glyph.role not in DOCUMENT_ROLES:
            continue
        owner = next((node for node in reversed(glyph.path) if node in ruby), None)
        found.append("".join(
            other.text for other in parsed.chars
            if owner is not None and other.role in READING_ROLES and owner in other.path
        ))
    return found


def line_boxes(lines_path: Path, wanted: Iterable[tuple[str, int, str]]) -> dict[tuple[str, int, str], dict]:
    """The Honkoku-Lines rows keyed by (lower-case entry, page counted from 1, raw text)."""
    keys = set(wanted)
    rows: dict[tuple[str, int, str], dict] = {}
    if not keys or not Path(lines_path).is_file():
        return rows
    with gzip.open(lines_path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            key = (row["item_id"].lower(), row["image_index"] + 1, row["text"])
            if key in keys:
                rows.setdefault(key, row)
    return rows


def report(
    clone: Path,
    lines_path: Path | None,
    *,
    max_documents: int = 1,
    examples: int = 5,
    projects: list[str] | None = None,
) -> tuple[list[dict], Counts]:
    """One row per example line of every Han character attested in at most `max_documents` entries."""
    counts = count(clone, projects=projects, examples=examples)
    rare = sorted(
        (char for char, n in counts.documents.items() if n <= max_documents),
        key=lambda char: (counts.documents[char], counts.occurrences[char], ord(char)),
    )
    texts: dict[tuple[str, str, int], list[str]] = {}
    picked = []
    for char in rare:
        for place in counts.examples[char]:
            source = (place.project, place.entry, place.page)
            if source not in texts:
                texts[source] = _lines(Path(clone) / "v3" / place.project / place.entry / f"{place.page:03d}.txt")
            picked.append((char, place, texts[source][place.line - 1]))
    boxes = line_boxes(lines_path, ((p.entry.lower(), p.page, t) for _, p, t in picked)) if lines_path else {}
    rows = []
    for char, place, text in picked:
        box = boxes.get((place.entry.lower(), place.page, text), {})
        rows.append({
            "code_point": f"U+{ord(char):04X}",
            "char": char,
            "documents": counts.documents[char],
            "occurrences": counts.occurrences[char],
            "project": place.project,
            "entry": place.entry,
            "title": place.title,
            "page": place.page,
            "line": place.line,
            "reading": " / ".join(r for r in dict.fromkeys(readings(text, char)) if r),
            "text": text,
            "honkoku_url": f"https://app.honkoku.org/transcription/{place.entry}/{place.page}",
            "line_box": box.get("iiif_region_url", ""),
            "image_license": box.get("image_license", ""),
        })
    return rows, counts


def write(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
