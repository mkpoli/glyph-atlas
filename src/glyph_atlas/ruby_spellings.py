"""Count the spellings the みんなで翻刻 transcriptions give a word, read off their 振り仮名.

A transcriber who types 斗（ばかり） states that the 斗 of that line reads ばかり. Gathered over the
corpus, those statements say which characters write a word: ばかり is written 許, 計 and 斗, など is
written 抔, 等 and 杯. `count` collects them for a chosen set of readings, one row per reading and
spelling.

What a row holds:

- `spelling` is the whole base of one ruby, as `koji.parse` delimits it: a run of kanji in the legacy
  form `base（reading）`, cut by a space or `／`, or the first field of `《振り仮名：base｜reading》`.
  A transcriber who did not cut the run gives a longer spelling (屋等 for など), and the row keeps it.
  A ruby nested inside another counts for both: `《振り仮名：孿（ふた）胎｜サンタイ》` gives 孿 for
  ふた and 孿胎 for サンタイ.
- `reading` is the right-hand 振り仮名 with spaces removed and katakana mapped to hiragana; `ruby`
  lists the forms as typed. A left-hand 振り仮名 (左訓) often glosses the meaning, so it is not read.
  A reading written without its dakuten (はかり) is its own key: nothing here says it is the same
  word.
- `documents` counts entries. One entry can repeat a spelling on every page, so a spelling attested
  by many documents is a convention and one attested by a single document may be a scribe's habit.

Only bases holding a Han character are counted. The parser files `《迎え仮名：…》` under the same kind
as 振り仮名; the clone at revision be63dc2 holds none.

The source does not say whether a 振り仮名 stands on the page or was added by the transcriber, and
transcribers are asked to type modern forms (https://wiki.honkoku.org/doku.php?id=guidelines), so a
row says what transcribers wrote, not what the ink shows. Its expert audit found 1.5 errors or
spelling inconsistencies per 100 characters.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import koji
from .importers import honkoku_data
from .rare_chars import DOCUMENT_ROLES, _lines, is_han
from .refs import to_hiragana

#: Readings counted when none are named: three grammatical words, and the two of them written
#: without their dakuten.
WORDS = ("ばかり", "はかり", "など", "なと", "のみ")
#: Example locations kept per row, each from a different entry.
EXAMPLES = 5
COLUMNS = ("reading", "spelling", "code_points", "documents", "occurrences", "projects", "ruby", "examples")


@dataclass
class Spelling:
    occurrences: int = 0
    entries: set[str] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)
    ruby: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)


def rubies(text: str) -> list[tuple[str, str]]:
    """The `(base, right-hand reading)` of every 振り仮名 of a raw line, in document order.

    The base is every document character inside the ruby, nested rubies included; the reading is the
    right-hand ruby text whose innermost ruby is this one, with spaces left out.
    """
    if "（" not in text and "振り仮名" not in text:
        return []
    parsed = koji.parse(text)
    order = [element.id for element in koji.walk(parsed.nodes) if element.kind == "ruby"]
    if not order:
        return []
    wanted = set(order)
    bases = {ruby: "" for ruby in order}
    readings = {ruby: "" for ruby in order}
    for char in parsed.chars:
        mine = [node for node in char.path if node in wanted]
        if char.role in DOCUMENT_ROLES:
            for ruby in mine:
                bases[ruby] += char.text
        elif char.role == "ruby" and mine and not char.text.isspace():
            readings[mine[-1]] += char.text
    return [(bases[ruby], readings[ruby]) for ruby in order]


def _count_entry(job: tuple[str, str, str, tuple[str, ...]]) -> list[tuple[str, str, str, int, int]]:
    """`(reading, spelling, ruby as typed, page, line)` for each counted ruby of one entry."""
    root, project, entry, words = job
    wanted = set(words)
    found = []
    for page, path in honkoku_data.page_files(Path(root) / "v3" / project / entry):
        for number, text in enumerate(_lines(path), 1):
            for base, ruby in rubies(text):
                reading = to_hiragana(ruby)
                if reading in wanted and any(is_han(char) for char in base):
                    found.append((reading, base, ruby, page, number))
    return found


def count(
    clone: Path, words: Iterable[str] = WORDS, *, projects: list[str] | None = None, workers: int = 4
) -> dict[tuple[str, str], Spelling]:
    """Every spelling the clone's v3 transcriptions give each of `words`, keyed by (reading, spelling)."""
    words = tuple(to_hiragana(word) for word in words)
    found, _, _ = honkoku_data.entries(clone, projects)
    jobs = [(str(clone), project, row["id"].strip(), words) for project, row in found]
    spellings: dict[tuple[str, str], Spelling] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for (project, row), hits in zip(found, pool.map(_count_entry, jobs, chunksize=16), strict=True):
            entry = row["id"].strip()
            for reading, base, ruby, page, line in hits:
                spelling = spellings.setdefault((reading, base), Spelling())
                spelling.occurrences += 1
                spelling.projects.add(project)
                spelling.ruby[ruby] += 1
                if entry not in spelling.entries and len(spelling.examples) < EXAMPLES:
                    spelling.examples.append(f"{project}/{entry}/{page}:{line}")
                spelling.entries.add(entry)
    return spellings


def rows(spellings: dict[tuple[str, str], Spelling]) -> list[dict]:
    """One row per reading and spelling: readings in code point order, then by documents, most first."""
    ordered = sorted(spellings.items(), key=lambda item: (item[0][0], -len(item[1].entries), -item[1].occurrences, item[0][1]))
    return [{
        "reading": reading,
        "spelling": base,
        "code_points": " ".join(f"U+{ord(char):04X}" for char in base),
        "documents": len(found.entries),
        "occurrences": found.occurrences,
        "projects": len(found.projects),
        "ruby": " ".join(ruby for ruby, _ in found.ruby.most_common()),
        "examples": " ".join(found.examples),
    } for (reading, base), found in ordered]


def write(found: list[dict], out: Path, *, revision: str | None) -> None:
    """The table with a header naming the source, its revision and its weakness."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "# Spellings of a word, read off the 振り仮名 of the みんなで翻刻 transcriptions; see "
            "src/glyph_atlas/ruby_spellings.py.\n"
            "# source honkoku-data: みんなで翻刻データ; CC-BY-SA-4.0 (https://github.com/yuta1984/honkoku-data); "
            "みんなで翻刻（https://honkoku.org/）翻刻データ, CC BY-SA 4.0\n"
            f"#   revision {revision or 'unknown'}, v3 only (v1 is carried into v3 as project honkokuv1)\n"
            "#   crowd transcriptions: 1.5 errors or spelling inconsistencies per 100 characters in a "
            "100,000-character expert audit (https://repository.kulib.kyoto-u.ac.jp/dspace/handle/2433/233817); "
            "transcribers type modern forms, and the markup guide does not say whether a 振り仮名 is on the page\n"
            "# examples: project/entry/page:line, one per entry; a page reads at "
            "https://app.honkoku.org/transcription/{entry}/{page}\n"
        )
        writer = csv.DictWriter(handle, COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(found)
