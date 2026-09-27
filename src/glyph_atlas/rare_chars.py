"""Find the Han characters that the みんなで翻刻 transcriptions attest in few documents.

A character counts where `koji.parse` gives it a role of document text (`DOCUMENT_ROLES`):
振り仮名, 注記, 送り仮名 and 返り点 are left out, and so are the tag names of nested constructs,
which `koji.plain` would keep. A line without any notation character is all document text and is not
parsed. Rarity is the number of entries (documents) that attest a character, since one document can
repeat a character on every page. Page files are split on `\n` only: Honkoku-Lines keeps form feeds
and other separators inside a line's text.

For each rare character the report lists up to `examples` lines, each from a different entry, with
the 振り仮名 the transcriber gave the character and, when Honkoku-Lines holds the same line, its line
box. Honkoku-Lines uses the entry ids of みんなで翻刻 as `item_id` and counts pages from 0 in
`image_index`; its `line_index` follows a different line numbering than the page files, so a line is
matched by entry, page and raw text, and failing that by its plain text, which recovers lines that
Honkoku-Lines holds in an older revision. `line_box_match` says which key matched.

Transcribers are asked to type modern forms (https://wiki.honkoku.org/doku.php?id=guidelines), so a
rare form on the page is often counted under its common character, and a character that is rare here
can be a typing or conversion error. The report is a queue for review.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import koji
from .importers import honkoku_data

#: Roles of `koji.Char` that are glyphs of the document.
DOCUMENT_ROLES = frozenset({"main", "warigaki", "inserted", "cancelled"})
#: Roles that hold a 振り仮名 reading.
READING_ROLES = frozenset({"ruby", "ruby-left"})
#: Characters that can start or belong to koji notation. A line without any of them is plain text;
#: a false match costs only a parse.
NOTATION = re.compile(r"[《》【】（）()〔〕｛｝＜＞<>：｜|＿_￣■□〓#＃％%／﹅]")
HAN_NAMES = ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")
COLUMNS = (
    "code_point", "char", "documents", "occurrences", "project", "entry", "title", "page", "line",
    "reading", "reading_left", "text", "honkoku_url", "line_box", "line_box_match", "image_license",
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


def is_han(char: str) -> bool:
    """A unified or compatibility ideograph; CJK radicals and strokes are not characters of a text."""
    return unicodedata.name(char, "").startswith(HAN_NAMES)


def _lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def document_chars(text: str) -> Iterable[str]:
    """The characters of a raw line that are text of the document."""
    if not NOTATION.search(text):
        return text
    return (char.text for char in koji.parse(text).chars if char.role in DOCUMENT_ROLES)


def _count_entry(job: tuple[str, str, str]) -> tuple[Counter[str], dict[str, tuple[int, int]], int, int]:
    """One entry's occurrences, its first location of each Han character, and its page and line counts."""
    root, project, entry = job
    occurrences: Counter[str] = Counter()
    first: dict[str, tuple[int, int]] = {}
    pages = lines = 0
    for page, path in honkoku_data.page_files(Path(root) / "v3" / project / entry):
        pages += 1
        for number, text in enumerate(_lines(path), 1):
            lines += 1
            for char in document_chars(text):
                if is_han(char):
                    occurrences[char] += 1
                    first.setdefault(char, (page, number))
    return occurrences, first, pages, lines


def count(clone: Path, *, projects: list[str] | None = None, examples: int = 5, workers: int = 4) -> Counts:
    """Count every Han character of a clone's v3 transcriptions by occurrence and by entry."""
    counts = Counts()
    found, _, _ = honkoku_data.entries(clone, projects)
    jobs = [(str(clone), project, row["id"].strip()) for project, row in found]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for (project, row), result in zip(found, pool.map(_count_entry, jobs, chunksize=16), strict=True):
            occurrences, first, pages, lines = result
            entry, title = row["id"].strip(), (row.get("label") or "").strip()
            counts.occurrences.update(occurrences)
            counts.documents.update(first.keys())
            for char, (page, line) in first.items():
                kept = counts.examples.setdefault(char, [])
                if len(kept) < examples:
                    kept.append(Location(project, entry, title, page, line))
            counts.entries += 1
            counts.pages += pages
            counts.lines += lines
    return counts


def readings(text: str, char: str) -> list[tuple[str, str]]:
    """The right and left 振り仮名 of each document occurrence of `char` in a raw line.

    A reading belongs to the innermost ruby around the character; readings of a ruby nested inside
    it, and spaces, are left out. A ruby spanning several characters gives the reading of the group.
    """
    parsed = koji.parse(text)
    ruby = {element.id for element in koji.walk(parsed.nodes) if element.kind == "ruby"}

    def owner(path: list[int]) -> int | None:
        return next((node for node in reversed(path) if node in ruby), None)

    found = []
    for glyph in parsed.chars:
        if glyph.text != char or glyph.role not in DOCUMENT_ROLES:
            continue
        mine = owner(glyph.path)
        sides = {"ruby": "", "ruby-left": ""}
        if mine is not None:
            for other in parsed.chars:
                if other.role in READING_ROLES and owner(other.path) == mine and not other.text.isspace():
                    sides[other.role] += other.text
        found.append((sides["ruby"], sides["ruby-left"]))
    return found


def line_boxes(lines_path: Path, wanted: Iterable[tuple[str, int, str]]) -> dict[tuple[str, int, str], tuple[dict, str]]:
    """Honkoku-Lines rows for (lower-case entry, page counted from 1, raw text), with the matching key.

    A line whose raw text Honkoku-Lines lacks is matched by its plain text (`"plain"`).
    """
    keys = set(wanted)
    plains = {(entry, page, koji.plain(text)): (entry, page, text) for entry, page, text in keys}
    exact: dict[tuple[str, int, str], dict] = {}
    loose: dict[tuple[str, int, str], dict] = {}
    if not keys or not Path(lines_path).is_file():
        return {}
    with gzip.open(lines_path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            entry, page = row["item_id"].lower(), row["image_index"] + 1
            key = (entry, page, row["text"])
            if key in keys:
                exact.setdefault(key, row)
            wanted_key = plains.get((entry, page, row.get("plain_text") or koji.plain(row["text"])))
            if wanted_key is not None:
                loose.setdefault(wanted_key, row)
    matched = {key: (row, "plain") for key, row in loose.items()}
    matched.update({key: (row, "text") for key, row in exact.items()})
    return matched


def report(
    clone: Path,
    lines_path: Path | None,
    *,
    max_documents: int = 1,
    examples: int = 5,
    projects: list[str] | None = None,
    workers: int = 4,
) -> tuple[list[dict], Counts]:
    """One row per example line of every Han character attested in at most `max_documents` entries."""
    counts = count(clone, projects=projects, examples=examples, workers=workers)
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
        box, match = boxes.get((place.entry.lower(), place.page, text), ({}, ""))
        found = list(dict.fromkeys(readings(text, char)))
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
            "reading": " / ".join(right for right, _ in found if right),
            "reading_left": " / ".join(left for _, left in found if left),
            "text": text,
            "honkoku_url": f"https://app.honkoku.org/transcription/{place.entry}/{place.page}",
            "line_box": box.get("iiif_region_url", ""),
            "line_box_match": match,
            "image_license": box.get("image_license", ""),
        })
    return rows, counts


def write(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
