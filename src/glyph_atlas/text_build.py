"""`atlas text build`: the rubies and tokens of every transcription line, in `text.sqlite`.

The text layer of decisions 0009 and 0010 is built from the みんなで翻刻 clone alone, one pass over
every entry:

- every page with text gets a text page number, kept from the previous build while the clone keeps
  the page, so numbers stay fixed across rebuilds;
- every line keeps its raw text and, unless it is kanbun, its reading runs with their raw offsets;
- every run is parsed (`tokens.Analyser`), and each token names a form, which names its lexeme;
- every ruby of the line is a pair `(rb, side, rt)` and an occurrence of it, with the outcome of the
  ruby test for the right rubies the parse was forced on.

Links to crops are a later step: they need the datasets' lines and the layout's mirror. Nothing here
reads or writes the site.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
from array import array
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from . import tokens as tk
from .importers import honkoku_data
from .refs import to_hiragana

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- release: the UniDic version its README names; sha256: of its sys.dic
CREATE TABLE dictionaries (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, release TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE books (
  entry TEXT PRIMARY KEY, project TEXT NOT NULL, label TEXT NOT NULL,
  dictionary INTEGER NOT NULL REFERENCES dictionaries(id));
-- Every clone page ever numbered keeps its number; present is 0 for one with no text in this build.
CREATE TABLE pages (
  id INTEGER PRIMARY KEY, entry TEXT NOT NULL, page INTEGER NOT NULL, present INTEGER NOT NULL,
  UNIQUE (entry, page));
-- state: 0 parsed, 1 kanbun (not parsed), 2 no reading text
CREATE TABLE lines (
  page INTEGER NOT NULL, line INTEGER NOT NULL, raw TEXT NOT NULL, state INTEGER NOT NULL,
  PRIMARY KEY (page, line)) WITHOUT ROWID;
-- kind: 0 main text, 1 割書; offsets: the raw offset of each character, uint16
CREATE TABLE runs (
  page INTEGER NOT NULL, line INTEGER NOT NULL, run INTEGER NOT NULL, kind INTEGER NOT NULL,
  text TEXT NOT NULL, offsets BLOB NOT NULL,
  PRIMARY KEY (page, line, run)) WITHOUT ROWID;
CREATE TABLE lexemes (id INTEGER PRIMARY KEY, lemma TEXT NOT NULL, reading TEXT NOT NULL, pos TEXT NOT NULL);
-- lexeme is NULL for a surface no entry spells (basis unknown)
CREATE TABLE forms (
  id INTEGER PRIMARY KEY, lexeme INTEGER REFERENCES lexemes(id), surface TEXT NOT NULL,
  kana TEXT NOT NULL, pos TEXT NOT NULL, ctype TEXT NOT NULL, cform TEXT NOT NULL,
  UNIQUE (surface, lexeme, kana, pos, ctype, cform));
-- basis: index into tokens.BASES; start and end count characters of the run
CREATE TABLE tokens (
  page INTEGER NOT NULL, line INTEGER NOT NULL, run INTEGER NOT NULL, start INTEGER NOT NULL,
  end INTEGER NOT NULL, form INTEGER NOT NULL REFERENCES forms(id), basis INTEGER NOT NULL,
  PRIMARY KEY (page, line, run, start)) WITHOUT ROWID;
CREATE TABLE pairs (
  id INTEGER PRIMARY KEY, rb TEXT NOT NULL, side TEXT NOT NULL, rt TEXT NOT NULL,
  UNIQUE (rb, side, rt));
-- outcome of the ruby test for a forced right ruby (tokens.AGREED, CHOSEN, UNMATCHED, NO_ENTRY);
-- NULL for a ruby the parse was not forced on, a left ruby, or a 〻 reading
CREATE TABLE rubies (
  page INTEGER NOT NULL, line INTEGER NOT NULL, element INTEGER NOT NULL, side TEXT NOT NULL,
  pair INTEGER NOT NULL REFERENCES pairs(id), typed TEXT NOT NULL, source TEXT NOT NULL,
  base_offsets BLOB NOT NULL, outcome TEXT,
  PRIMARY KEY (page, line, element, side)) WITHOUT ROWID;
"""
INDEXES = """
CREATE INDEX forms_lexeme ON forms(lexeme);
CREATE INDEX tokens_form ON tokens(form);
CREATE INDEX rubies_pair ON rubies(pair);
CREATE INDEX pairs_rt ON pairs(rt);
"""
KINDS = {"main": 0, "warigaki": 1}

_ANALYSER: tk.Analyser | None = None


def _offsets(values: list[int]) -> bytes:
    if values and max(values) > 0xFFFF:
        raise ValueError("a line longer than 65,535 characters")
    return array("H", values).tobytes()


def offsets(blob: bytes) -> list[int]:
    """The raw offsets a `runs.offsets` or `rubies.base_offsets` blob holds."""
    values = array("H")
    values.frombytes(blob)
    return values.tolist()


def reading_of(typed: str) -> str:
    """A ruby field as a pair's rt: spaces removed, katakana as hiragana, historical kana kept."""
    return to_hiragana("".join(typed.split()))


def _start(dictionary: str) -> None:
    global _ANALYSER
    _ANALYSER = tk.Analyser(Path(dictionary))


def _entry(job: tuple[str, str, str]) -> tuple[str, list[tuple]]:
    """Parse one entry: its pages, each as `(page, lines)`, and every line's runs, tokens and rubies."""
    clone, project, entry = job
    assert _ANALYSER is not None
    pages = []
    for number, path in honkoku_data.page_files(Path(clone) / "v3" / project / entry):
        lines = []
        for index, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if not raw.strip():
                continue
            reading = tk.read_line(raw)
            parsed, outcome = ([], {}) if reading.kanbun else _ANALYSER.line(reading)
            state = 1 if reading.kanbun else (0 if reading.runs else 2)
            runs = [(KINDS[run.kind], run.text, _offsets(run.offsets)) for run in reading.runs]
            words = [(t.run, t.start, t.end, t.entry, tk.BASES.index(t.basis)) for t in parsed]
            marks = [(mark.element, side, mark.base, typed, mark.source, _offsets(mark.base_offsets),
                      outcome.get(number) if side == "right" else None)
                     for number, mark in enumerate(reading.marks) for side, typed in mark.readings.items()]
            lines.append((index, raw, state, runs, words, marks))
        if lines:
            pages.append((number, lines))
    return entry, pages


class _Writer:
    """Rows of the build, with forms, lexemes and pairs numbered as they first appear."""

    def __init__(self, db: sqlite3.Connection, previous: dict[tuple[str, int], int]) -> None:
        self.db = db
        self.previous = previous
        self.next_page = max(previous.values(), default=0) + 1
        self.seen: set[tuple[str, int]] = set()
        self.forms: dict[tuple, int] = {}
        self.lexemes: set[int] = set()
        self.pairs: dict[tuple[str, str, str], int] = {}
        self.figures: Counter[str] = Counter()

    def page_id(self, entry: str, page: int) -> int:
        found = self.previous.get((entry, page))
        if found is None:
            found = self.next_page
            self.next_page += 1
        return found

    def form(self, item: tk.Entry) -> int:
        key = (item.surface, item.lemma_id, item.kana, item.pos, item.ctype, item.cform)
        found = self.forms.get(key)
        if found is None:
            if item.lemma_id is not None and item.lemma_id not in self.lexemes:
                self.lexemes.add(item.lemma_id)
                self.db.execute("INSERT INTO lexemes VALUES (?,?,?,?)", (item.lemma_id, item.lemma, item.reading, item.pos))
            found = len(self.forms) + 1
            self.forms[key] = found
            self.db.execute("INSERT INTO forms VALUES (?,?,?,?,?,?,?)",
                            (found, item.lemma_id, item.surface, item.kana, item.pos, item.ctype, item.cform))
        return found

    def pair(self, rb: str, side: str, rt: str) -> int:
        key = (rb, side, rt)
        found = self.pairs.get(key)
        if found is None:
            found = len(self.pairs) + 1
            self.pairs[key] = found
            self.db.execute("INSERT INTO pairs VALUES (?,?,?,?)", (found, *key))
        return found

    def entry(self, entry: str, pages: list[tuple]) -> None:
        f = self.figures
        for number, lines in pages:
            page = self.page_id(entry, number)
            self.seen.add((entry, number))
            self.db.execute("INSERT INTO pages VALUES (?,?,?,1)", (page, entry, number))
            f["pages"] += 1
            for index, raw, state, runs, words, marks in lines:
                self.db.execute("INSERT INTO lines VALUES (?,?,?,?)", (page, index, raw, state))
                f["lines"] += 1
                f[("parsed lines", "kanbun lines", "lines without text")[state]] += 1
                self.db.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                                    [(page, index, n, kind, text, blob) for n, (kind, text, blob) in enumerate(runs)])
                f["characters"] += sum(len(text) for _, text, _ in runs)
                self.db.executemany("INSERT INTO tokens VALUES (?,?,?,?,?,?,?)",
                                    [(page, index, run, start, end, self.form(item), basis)
                                     for run, start, end, item, basis in words])
                f["tokens"] += len(words)
                for _, _, _, _, basis in words:
                    f[f"tokens {tk.BASES[basis]}"] += 1
                for element, side, base, typed, source, blob, outcome in marks:
                    self.db.execute("INSERT INTO rubies VALUES (?,?,?,?,?,?,?,?,?)",
                                    (page, index, element, side, self.pair(base, side, reading_of(typed)),
                                     typed, source, blob, outcome))
                    f["rubies"] += 1
                    if outcome:
                        f[f"right rubies {outcome}"] += 1


def _previous_pages(out: Path) -> dict[tuple[str, int], int]:
    if not out.exists():
        return {}
    with sqlite3.connect(out) as old:
        return {(entry, page): ident for ident, entry, page in old.execute("SELECT id, entry, page FROM pages")}


def jobs(clone: Path, projects: list[str] | None) -> Iterator[tuple[tuple[str, str, str], dict[str, str]]]:
    found, _, _ = honkoku_data.entries(clone, projects)
    for project, row in found:
        yield (str(clone), project, row["id"]), {"project": project, "label": row.get("label") or ""}


def _parsed(listed: list[tuple[str, str, str]], dictionary: Path, workers: int) -> Iterator[tuple[str, list[tuple]]]:
    """Every entry parsed, by `workers` processes, or in this process when `workers` is 1 or less."""
    if workers <= 1:
        _start(str(dictionary))
        yield from map(_entry, listed)
        return
    context = multiprocessing.get_context("forkserver")
    with context.Pool(workers, initializer=_start, initargs=(str(dictionary),)) as pool:
        yield from pool.imap_unordered(_entry, listed, chunksize=2)


def build(clone: Path, out: Path, dictionary: Path, *, projects: list[str] | None = None, workers: int = 4) -> dict:
    """Write `out` from every entry of `clone`, parsed with `dictionary`; return the build's figures."""
    out = Path(out)
    previous = _previous_pages(out)
    staging = out.with_name(out.name + ".building")
    staging.unlink(missing_ok=True)
    db = sqlite3.connect(staging)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    db.execute("INSERT INTO dictionaries VALUES (1,?,?,?)",
               (Path(dictionary).name, tk.release(dictionary), tk.fingerprint(dictionary)))
    writer = _Writer(db, previous)
    listed = list(jobs(clone, projects))
    books = {job[2]: info for job, info in listed}
    db.executemany("INSERT INTO books VALUES (?,?,?,1)",
                   [(entry, info["project"], info["label"]) for entry, info in books.items()])
    for done, (entry, pages) in enumerate(_parsed([job for job, _ in listed], dictionary, workers), 1):
        writer.entry(entry, pages)
        if done % 200 == 0:
            db.commit()
    # Pages numbered before and holding no text now keep their numbers, so none is given to another.
    db.executemany("INSERT INTO pages VALUES (?,?,?,0)",
                   [(ident, entry, page) for (entry, page), ident in previous.items() if (entry, page) not in writer.seen])
    figures = dict(sorted(writer.figures.items()))
    figures.update(books=len(books), lexemes=len(writer.lexemes), forms=len(writer.forms), pairs=len(writer.pairs))
    meta = {
        "schema": str(SCHEMA_VERSION),
        "clone_revision": honkoku_data.clone_revision(clone) or "",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "figures": json.dumps(figures, ensure_ascii=False),
    }
    db.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    db.executescript(INDEXES)
    db.commit()
    db.close()
    os.replace(staging, out)
    return figures
