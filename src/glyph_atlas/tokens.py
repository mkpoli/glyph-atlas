"""The words of a transcription line, from a morphological analyser that its 振り仮名 steer.

A line is parsed with a period UniDic through MeCab (decision 0010). This module reads a raw line
into the text the analyser sees and the constraints its rubies set, and turns the analyser's nodes
into tokens.

- **Reading text:** the characters `koji` marks `main` or `inserted` that stand outside every
  ruby's reading field. What a 見せ消ち strikes out is skipped, so the corrected word stays whole. A
  correction nested in a reading takes the role `inserted`, so the field is tested as
  `rubies.marks` tests it. A 割書, its columns in reading order, is a run of its own. A damage mark,
  a space, a control character or a note ends a run, so no token spans one.
- **Kanbun:** a line with 返り点 is read in another order than it is written, so it is not parsed.
- **Ruby constraints:** a right ruby whose base lies whole and contiguous in one run is forced to
  token boundaries. A base whose ruby holds another ruby must start and end at token boundaries;
  the others must be one token. A base broken by a damage mark lies in two runs and is not forced.
- **Ruby choice:** a one-token base keeps the parse's entry when its kana match the ruby on
  `kana_key`. Otherwise the entry of the base whose kana match is forced in a second parse of the
  run. A bounded base is tested on the joined kana of its tokens.

Every token is in the `parsed` tier. `basis` says how its entry was reached, and the outcome of each
ruby is kept beside the tokens.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from . import koji, rubies
from .refs import to_hiragana

#: The features of a UniDic (ver. 2.x and later) node, in the order MeCab prints them.
FIELDS = (
    "pos1", "pos2", "pos3", "pos4", "ctype", "cform", "lform", "lemma", "orth", "pron", "orth_base",
    "pron_base", "goshu", "itype", "iform", "ftype", "fform", "iconType", "fconType", "type", "kana",
    "kana_base", "form", "form_base", "atype", "acon_type", "amod_type", "lid", "lemma_id",
)
#: Roles whose characters are read as the document's text.
READ_ROLES = frozenset({"main", "inserted", "warigaki"})
#: Roles that end a run: a token never spans damage.
BREAK_ROLES = frozenset({"gap", "unreadable"})

#: How a token's entry was reached: the parse alone, a ruby choosing another entry of its base, a
#: ruby whose reading no entry of its base has, or no entry spelling the surface at all.
PARSED, RUBY, RUBY_UNMATCHED, UNKNOWN = "parsed", "ruby", "ruby-unmatched", "unknown"
BASES = (PARSED, RUBY, RUBY_UNMATCHED, UNKNOWN)
#: The outcome of the ruby test for a forced right ruby: the parse's entry has the reading, another
#: entry of the base has it, none has it, or a token of the base has no entry.
AGREED, CHOSEN, UNMATCHED, NO_ENTRY = "agreed", "chosen", "unmatched", "unknown"


# --------------------------------------------------------------------------------------------
# Comparing kana.

_SMALL = str.maketrans("ぁぃぅぇぉゃゅょっゎゕゖ", "あいうえおやゆよつわかけ")
_MEDIAL = str.maketrans("はひふへほ", "わいうえお")
#: Historical spellings and their modern readings, applied after voicing is removed.
_HISTORICAL = (
    ("やう", "よう"), ("かう", "こう"), ("さう", "そう"), ("たう", "とう"), ("なう", "のう"),
    ("まう", "もう"), ("らう", "ろう"), ("わう", "おう"), ("あう", "おう"),
    ("しう", "しゆう"), ("ちう", "ちゆう"), ("にう", "にゆう"), ("りう", "りゆう"), ("きう", "きゆう"),
    ("てう", "ちよう"), ("せう", "しよう"), ("けう", "きよう"), ("えう", "よう"), ("いう", "ゆう"),
    ("くわ", "か"), ("ゐ", "い"), ("ゑ", "え"), ("を", "お"),
)
_VOICING = frozenset("゙゚")


def kana_key(text: str, *, voicing: bool = False) -> str:
    """`text` folded so that a historical or unvoiced spelling meets the modern kana of an entry.

    Katakana become hiragana and iteration marks are written out. Voicing marks (unless `voicing`),
    small kana and the long-vowel mark are dropped, then medial は行 is read as わ行 and historical
    long vowels (やう, かう, てう and the like), くわ and ゐ ゑ を are folded. Transcribers often leave
    voicing unmarked (てかた for 手形), so the ruby test tries the key with voicing first and the one
    without it after.
    """
    out: list[str] = []
    for char in to_hiragana(text):
        if char in "ゝゞヽヾ" and out:
            out.append(out[-1])
        elif not char.isspace():
            out.append(char)
    # ぢ and づ are read as じ and ず before voicing is dropped (はぢ for 恥, ハジ).
    joined = "".join(out).replace("ー", "").replace("ぢ", "じ").replace("づ", "ず")
    decomposed = unicodedata.normalize("NFD", joined)
    folded = unicodedata.normalize("NFC", "".join(char for char in decomposed if voicing or char not in _VOICING))
    folded = folded.translate(_SMALL)
    folded = folded[:1] + folded[1:].translate(_MEDIAL)
    for old, new in _HISTORICAL:
        folded = folded.replace(old, new)
    return folded


# --------------------------------------------------------------------------------------------
# A raw line read for the analyser.


@dataclass
class Run:
    """A stretch of reading text the analyser parses as one: its characters and their raw offsets."""

    kind: str
    text: str = ""
    offsets: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class Constraint:
    """A right ruby forced on a run: characters `start:end` of the run, as one token (`whole`) or
    bounded at its ends. `ruby` is the ruby's index in `Reading.marks`."""

    run: int
    start: int
    end: int
    whole: bool
    reading: str
    ruby: int


@dataclass
class Reading:
    """A raw line as the analyser reads it. `runs` is empty for a line with no text or with 返り点."""

    runs: list[Run]
    marks: list[rubies.Mark]
    constraints: list[Constraint]
    kanbun: bool = False


def read_line(raw: str) -> Reading:
    """The runs of `raw` and the constraints its right rubies set on them."""
    parsed = koji.parse(raw)
    marks = rubies.marks(raw)
    if any(char.role == "kaeriten" for char in parsed.chars):
        return Reading([], marks, [], kanbun=True)
    elements = {element.id: element for element in koji.walk(parsed.nodes)}
    fields = {ident: rubies._fields(raw, element) for ident, element in elements.items() if element.kind == "ruby"}
    struck = rubies._struck(raw, parsed)
    runs: list[Run] = []
    current: Run | None = None
    current_key: int | None = None
    for char in parsed.chars:
        if char.role in rubies.SIDES or any(
                node in fields and rubies._field_at(fields[node], char.start) is not None for node in char.path):
            continue
        if char.start in struck or char.role == "cancelled":
            continue
        if (char.role in BREAK_ROLES or char.role not in READ_ROLES or char.text.isspace()
                or unicodedata.category(char.text[0]) == "Cc"):
            current = None
            continue
        # The innermost 割書 that is not one of its columns names the run; main text is run -1.
        key = next((node for node in reversed(char.path)
                    if elements[node].kind == "warigaki" and "column" not in elements[node].attrs), -1)
        if current is None or key != current_key:
            current, current_key = Run("main" if key == -1 else "warigaki"), key
            runs.append(current)
        current.text += char.text
        current.offsets.append(char.start)
    spans = {ident: (element.start, element.end) for ident, element in elements.items()}
    return Reading(runs, marks, constraints(runs, marks, spans))


def constraints(runs: list[Run], marks: list[rubies.Mark], spans: dict[int, tuple[int, int]]) -> list[Constraint]:
    """The boundaries the right rubies of a line force, outermost first.

    A base must lie in one run as a contiguous stretch of it. A ruby whose element holds another
    forced ruby's element is bounded at its ends, even when the two bases are the same characters;
    the others are forced to one token. `spans` gives each element's bounds in the raw line.
    """
    where = {offset: (index, at) for index, run in enumerate(runs) for at, offset in enumerate(run.offsets)}
    placed_marks: list[tuple[int, int, int, str, int, tuple[int, int]]] = []
    for number, mark in enumerate(marks):
        reading = mark.readings.get("right")
        if not reading or not mark.base_offsets or any(offset not in where for offset in mark.base_offsets):
            continue
        placed = sorted(where[offset] for offset in mark.base_offsets)
        run = placed[0][0]
        positions = [at for index, at in placed if index == run]
        if len(positions) != len(placed) or positions != list(range(positions[0], positions[-1] + 1)):
            continue
        placed_marks.append((run, positions[0], positions[-1] + 1, reading, number, spans[mark.element]))
    found = []
    for run, start, end, reading, number, (outer_start, outer_end) in placed_marks:
        holds = any(other != number and outer_start < o_start and o_end < outer_end
                    for _, _, _, _, other, (o_start, o_end) in placed_marks)
        found.append(Constraint(run, start, end, not holds, reading, number))
    return _consistent(found)


def _consistent(found: list[Constraint]) -> list[Constraint]:
    """The constraints that nest or stand apart; a base that only overlaps another is dropped."""
    kept: list[Constraint] = []
    for item in sorted(found, key=lambda c: (c.run, c.start, -c.end, c.whole)):
        clash = any(other.run == item.run and other.start < item.end and item.start < other.end
                    and not (other.start <= item.start and item.end <= other.end and not other.whole)
                    for other in kept)
        if not clash:
            kept.append(item)
    return kept


# --------------------------------------------------------------------------------------------
# The analyser.


@dataclass(frozen=True)
class Entry:
    """One analyser node: its surface and the features the text layer keeps."""

    surface: str
    lemma_id: int | None
    lemma: str
    reading: str
    pos: str
    ctype: str
    cform: str
    kana: str

    @property
    def known(self) -> bool:
        return self.lemma_id is not None


def entry(surface: str, feature: str) -> Entry:
    """The node `surface` with its raw MeCab feature string. A node with too few fields is MeCab's
    guess for a surface no entry spells, and its lexical fields stay empty."""
    values = _split(feature)
    if len(values) < len(FIELDS):
        return Entry(surface, None, "", "", "", "", "", "")
    named = dict(zip(FIELDS, values, strict=True))
    pos = "-".join(named[key] for key in ("pos1", "pos2", "pos3", "pos4") if named[key] != "*")
    return Entry(surface, int(named["lemma_id"]), named["lemma"], named["lform"], pos,
                 _blank(named["ctype"]), _blank(named["cform"]), named["kana"])


def _blank(value: str) -> str:
    return "" if value == "*" else value


def _split(feature: str) -> list[str]:
    if '"' not in feature:
        return feature.split(",")
    values, buffer, quoted = [], [], False
    for char in feature:
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            values.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    values.append("".join(buffer))
    return values


@dataclass(frozen=True)
class Token:
    """A token of a run: characters `start:end` of the run, its entry and how it was reached.
    `ruby` is the index of the ruby forced on exactly this token, if any."""

    run: int
    start: int
    end: int
    entry: Entry
    basis: str
    ruby: int | None = None


#: Characters that MeCab's partial-parse input reads as structure (a line break ends a segment, a
#: tab begins a constraint) or that its C code cannot take (NUL and the other controls). Each is
#: replaced by one character, so offsets hold.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")
#: A segment of exactly this text would end the sentence.
_TERMINATOR = "EOS"


def _pattern(found: Entry) -> str:
    """A partial-parse pattern that admits only entries of `found`'s lemma with its kana."""
    values = ["*"] * (FIELDS.index("kana") + 1)
    values[FIELDS.index("lform")] = found.reading
    values[FIELDS.index("lemma")] = found.lemma
    values[FIELDS.index("kana")] = found.kana
    return ",".join(values)


def release(dictionary: Path) -> str:
    """The UniDic release named in the dictionary's README (`ver. 2025.12`), or empty."""
    readme = Path(dictionary) / "README.md"
    if readme.exists():
        match = re.search(r"ver\.\s*(\d{4}\.\d{2})", readme.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
    return ""


def fingerprint(dictionary: Path) -> str:
    """SHA-256 of the dictionary's `sys.dic`, so a changed dictionary under one name is told apart."""
    digest = hashlib.sha256()
    with (Path(dictionary) / "sys.dic").open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _match(parsed: Entry, entries: tuple[Entry, ...], reading: str) -> Entry | None:
    """The entry a ruby's reading picks: the parse's own or another of the base's entries whose kana
    match it with voicing, else without voicing; None when no entry matches."""
    for voicing in (True, False):
        want = kana_key(reading, voicing=voicing)
        if kana_key(parsed.kana, voicing=voicing) == want:
            return parsed
        found = next((e for e in entries if kana_key(e.kana, voicing=voicing) == want), None)
        if found is not None:
            return found
    return None


class Analyser:
    """MeCab with one dictionary: a partial parser and an all-morphs lookup of a surface's entries."""

    def __init__(self, dictionary: Path) -> None:
        import fugashi

        self.dictionary = Path(dictionary)
        self.name = self.dictionary.name
        self._parser = fugashi.GenericTagger(f"-r /dev/null -p -d {self.dictionary}")
        self._lookup = fugashi.GenericTagger(f"-r /dev/null -a -d {self.dictionary}")
        self._entries: dict[str, tuple[Entry, ...]] = {}

    def entries(self, surface: str) -> tuple[Entry, ...]:
        """Every dictionary entry spelt exactly `surface`, in the dictionary's order."""
        found = self._entries.get(surface)
        if found is None:
            found = tuple(dict.fromkeys(
                entry(node.surface, node.feature_raw)
                for node in self._lookup.parseToNodeList(_UNSAFE.sub("・", surface))
                if node.surface == surface and len(_split(node.feature_raw)) >= len(FIELDS)))
            self._entries[surface] = found
        return found

    def _parse(self, text: str, forced: dict[tuple[int, int], str | None]) -> list[tuple[int, int, Entry]]:
        """Parse `text` with spans `(start, end)` forced: to one token matching a pattern, or (None)
        to boundaries only. Returns `(start, end, entry)` in order."""
        cuts = sorted({at for span in forced for at in span} | {0, len(text)})
        segments = []
        for left, right in pairwise(cuts):
            piece = _UNSAFE.sub("・", text[left:right])
            pattern = forced.get((left, right))
            if pattern is None and piece == _TERMINATOR:
                pattern = "*"
            segments.append(piece if pattern is None else f"{piece}\t{pattern}")
        found, at = [], 0
        for node in self._parser.parseToNodeList("\n".join(segments) + "\nEOS\n"):
            found.append((at, at + len(node.surface), entry(node.surface, node.feature_raw)))
            at += len(node.surface)
        if at != len(text):
            raise ValueError(f"the parse covers {at} of {len(text)} characters of {text!r}")
        return found

    def run(self, text: str, run: int, forced: list[Constraint]) -> tuple[list[Token], dict[int, str]]:
        """The tokens of one run and the outcome of each of its forced rubies."""
        spans: dict[tuple[int, int], str | None] = {(c.start, c.end): ("*" if c.whole else None) for c in forced}
        nodes = self._parse(text, spans)
        outcomes: dict[int, str] = {}
        replaced: dict[tuple[int, int], Entry] = {}
        for item in forced:
            if not item.whole or "〻" in item.reading:
                continue
            node = next((found for start, end, found in nodes if (start, end) == (item.start, item.end)), None)
            if node is None or not node.known:
                outcomes[item.ruby] = NO_ENTRY
                continue
            match = _match(node, self.entries(text[item.start:item.end]), item.reading)
            if match is None:
                outcomes[item.ruby] = UNMATCHED
            elif match == node:
                outcomes[item.ruby] = AGREED
            else:
                replaced[(item.start, item.end)] = match
                spans[(item.start, item.end)] = _pattern(match)
        if replaced:
            nodes = self._parse(text, spans)
        at = {(start, end): found for start, end, found in nodes}
        for span, match in replaced.items():
            got = at.get(span)
            ruby = next(c.ruby for c in forced if c.whole and (c.start, c.end) == span)
            ok = got is not None and got.lemma_id == match.lemma_id and kana_key(got.kana) == kana_key(match.kana)
            outcomes[ruby] = CHOSEN if ok else UNMATCHED
        for item in forced:
            if item.whole or "〻" in item.reading:
                continue
            inside = [found for start, end, found in nodes if item.start <= start and end <= item.end]
            if any(not found.known for found in inside):
                outcomes[item.ruby] = NO_ENTRY
            else:
                joined = kana_key("".join(found.kana for found in inside))
                outcomes[item.ruby] = AGREED if joined == kana_key(item.reading) else UNMATCHED
        whole = {(c.start, c.end): c.ruby for c in forced if c.whole}
        tokens = []
        for start, end, found in nodes:
            ruby = whole.get((start, end))
            outcome = outcomes.get(ruby) if ruby is not None else None
            if not found.known:
                basis = UNKNOWN
            elif outcome == CHOSEN:
                basis = RUBY
            elif outcome == UNMATCHED:
                basis = RUBY_UNMATCHED
            else:
                basis = PARSED
            tokens.append(Token(run, start, end, found, basis, ruby))
        return tokens, outcomes

    def line(self, reading: Reading) -> tuple[list[Token], dict[int, str]]:
        """The tokens of every run of a line, and the outcome of each forced ruby by its index."""
        out: list[Token] = []
        outcomes: dict[int, str] = {}
        for index, run in enumerate(reading.runs):
            found, results = self.run(run.text, index, [c for c in reading.constraints if c.run == index])
            out.extend(found)
            outcomes.update(results)
        return out, outcomes
