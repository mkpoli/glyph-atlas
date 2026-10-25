"""The words of a transcription line, from a morphological analyser that its 振り仮名 steer.

A line is parsed with a period UniDic through MeCab (decision 0010). This module reads a raw line
into the text the analyser sees and the constraints its rubies set, and turns the analyser's nodes
into tokens.

- **Reading text:** the characters `koji` marks `main` or `inserted` that stand outside every
  ruby's reading field and outside what a 見せ消ち strikes out. A correction nested in a reading
  takes the role `inserted`, so the field is tested as `rubies.marks` tests it. A 割書, its
  columns in reading order, is a run of its own. A damage mark, a space or a note ends a run, so no token spans one.
- **Kanbun:** a line with 返り点 is read in another order than it is written, so it is not parsed.
- **Ruby constraints:** a right ruby whose base lies whole and contiguous in one run is forced to
  token boundaries. A base that holds no other ruby must be one token; one that holds another is
  bounded at its ends only. A base broken by a damage mark lies in two runs and is not forced.
- **Ruby choice:** among the dictionary's entries spelt by a one-token base, the one whose kana match
  the ruby on `kana_key` is forced in a second parse of the line.

Every token is in the `parsed` tier; `basis` says how its entry was reached.
"""

from __future__ import annotations

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
READ_ROLES = frozenset({"main", "inserted"})
#: Roles that end a run: a token never spans damage.
BREAK_ROLES = frozenset({"gap", "unreadable"})

#: How a token's entry was reached.
PARSED, RUBY, RUBY_UNMATCHED, UNKNOWN = "parsed", "ruby", "ruby-unmatched", "unknown"
BASES = (PARSED, RUBY, RUBY_UNMATCHED, UNKNOWN)


# --------------------------------------------------------------------------------------------
# Comparing kana.

_SMALL = str.maketrans("ぁぃぅぇぉゃゅょっゎゕゖ", "あいうえおやゆよつわかけ")
_MEDIAL = str.maketrans("はひふへほ", "わいうえお")
#: Historical spellings of long vowels and their modern readings, voiced forms included.
_HISTORICAL = (
    ("やう", "よう"), ("かう", "こう"), ("さう", "そう"), ("たう", "とう"), ("なう", "のう"),
    ("はう", "ほう"), ("まう", "もう"), ("らう", "ろう"), ("わう", "おう"), ("がう", "ごう"),
    ("ざう", "ぞう"), ("だう", "どう"), ("ばう", "ぼう"), ("ぱう", "ぽう"), ("あう", "おう"),
    ("しう", "しゆう"), ("ちう", "ちゆう"), ("にう", "にゆう"), ("りう", "りゆう"), ("きう", "きゆう"),
    ("ぎう", "ぎゆう"), ("じう", "じゆう"), ("てう", "ちよう"), ("せう", "しよう"), ("けう", "きよう"),
    ("げう", "ぎよう"), ("ぜう", "じよう"), ("でう", "ぢよう"), ("えう", "よう"), ("くわ", "か"),
    ("ぐわ", "が"), ("いう", "ゆう"), ("おほ", "おお"), ("ゐ", "い"), ("ゑ", "え"), ("を", "お"),
    ("ぢ", "じ"), ("づ", "ず"),
)
_VOICING = frozenset("゙゚")


def kana_key(text: str) -> str:
    """`text` folded so that a historical or unvoiced spelling meets the modern kana of an entry.

    Katakana become hiragana and iteration marks are written out. Small kana, the long-vowel mark,
    medial は行 (as わ行), historical long vowels (やう, かう, てう and the like), くわ, ゐ ゑ を and
    voicing marks are folded. Transcribers often leave voicing unmarked (てかた for 手形).
    """
    out: list[str] = []
    for char in to_hiragana(text):
        if char in "ゝゞヽヾ" and out:
            out.append(out[-1])
        elif not char.isspace():
            out.append(char)
    folded = "".join(out).replace("ー", "").translate(_SMALL)
    folded = folded[:1] + folded[1:].translate(_MEDIAL)
    for old, new in _HISTORICAL:
        folded = folded.replace(old, new)
    decomposed = unicodedata.normalize("NFD", folded)
    return unicodedata.normalize("NFC", "".join(char for char in decomposed if char not in _VOICING))


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
    """A right ruby forced on a run: its characters `start:end` of the run, and whether as one token."""

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
    if any(char.role == "kaeriten" for char in parsed.chars):
        return Reading([], rubies.marks(raw), [], kanbun=True)
    elements = {element.id: element for element in koji.walk(parsed.nodes) if element.kind == "ruby"}
    fields = {ident: rubies._fields(raw, element) for ident, element in elements.items()}
    struck = rubies._struck(raw, parsed)
    runs: list[Run] = []
    current: Run | None = None
    current_key: tuple[str, int] | None = None
    for char in parsed.chars:
        if char.role in rubies.SIDES or any(
                node in fields and rubies._field_at(fields[node], char.start) is not None for node in char.path):
            continue
        if char.start in struck or char.role in BREAK_ROLES or char.text.isspace():
            current = None
            continue
        if char.role == "warigaki":
            key = ("warigaki", next((node for node in char.path if node not in fields), -1))
        elif char.role in READ_ROLES:
            key = ("main", 0)
        else:
            current = None
            continue
        if current is None or key != current_key:
            current, current_key = Run(key[0]), key
            runs.append(current)
        current.text += char.text
        current.offsets.append(char.start)
    marks = rubies.marks(raw)
    return Reading(runs, marks, constraints(runs, marks))


def constraints(runs: list[Run], marks: list[rubies.Mark]) -> list[Constraint]:
    """The boundaries the right rubies of a line force, outermost first.

    A base must lie in one run as a contiguous stretch of it. A base that holds another ruby's base
    is bounded at its ends; the others are forced to one token.
    """
    where = {offset: (index, at) for index, run in enumerate(runs) for at, offset in enumerate(run.offsets)}
    spans: list[tuple[int, int, int, str, int]] = []
    for number, mark in enumerate(marks):
        reading = mark.readings.get("right")
        if not reading or not mark.base_offsets or any(offset not in where for offset in mark.base_offsets):
            continue
        placed = sorted(where[offset] for offset in mark.base_offsets)
        run = placed[0][0]
        positions = [at for index, at in placed if index == run]
        if len(positions) != len(placed) or positions != list(range(positions[0], positions[-1] + 1)):
            continue
        spans.append((run, positions[0], positions[-1] + 1, reading, number))
    found = []
    for run, start, end, reading, number in spans:
        holds = any((o_run, o_start, o_end) != (run, start, end) and o_run == run
                    and start <= o_start and o_end <= end for o_run, o_start, o_end, _, _ in spans)
        found.append(Constraint(run, start, end, not holds, reading, number))
    return _consistent(found)


def _consistent(found: list[Constraint]) -> list[Constraint]:
    """The constraints that nest or stand apart; a base that only overlaps another is dropped."""
    kept: list[Constraint] = []
    for item in sorted(found, key=lambda c: (c.run, c.start, -c.end)):
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
    """The node `surface` with its raw MeCab feature string; a node with too few fields is unknown."""
    values = _split(feature)
    if len(values) < len(FIELDS):
        pos = ",".join(value for value in values[:4] if value != "*")
        return Entry(surface, None, "", "", pos, "", "", "")
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
    """A token of a run: characters `start:end` of the run, its entry and how it was reached."""

    run: int
    start: int
    end: int
    entry: Entry
    basis: str
    ruby: int | None = None
    chosen: bool = False


#: Characters MeCab's partial-parse input gives a meaning: a line break ends a segment, a tab
#: begins a constraint.
_UNSAFE = re.compile(r"[\t\r\n]")


class Analyser:
    """MeCab with one dictionary: a partial parser and an all-morphs lookup of a surface's entries."""

    def __init__(self, dictionary: Path) -> None:
        import fugashi

        self.dictionary = Path(dictionary)
        self.name = self.dictionary.name
        self._parser = fugashi.GenericTagger(f"-r /dev/null -p -d {self.dictionary}")
        self._lookup = fugashi.GenericTagger(f"-r /dev/null -a -d {self.dictionary}")
        self._entries: dict[str, tuple[Entry, ...]] = {}
        info = self._parser.dictionary_info
        self.version = str(info[0]["version"]) if info else ""

    def entries(self, surface: str) -> tuple[Entry, ...]:
        """Every dictionary entry spelt exactly `surface`, in the dictionary's order."""
        found = self._entries.get(surface)
        if found is None:
            found = tuple(dict.fromkeys(
                entry(node.surface, node.feature_raw)
                for node in self._lookup.parseToNodeList(surface)
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
            pattern = forced.get((left, right), None)
            segments.append(f"{piece}\t{pattern}" if (left, right) in forced and pattern is not None else piece)
        found, at = [], 0
        for node in self._parser.parseToNodeList("\n".join(segments) + "\nEOS\n"):
            found.append((at, at + len(node.surface), entry(node.surface, node.feature_raw)))
            at += len(node.surface)
        return found

    def run(self, text: str, run: int, forced: list[Constraint]) -> list[Token]:
        """The tokens of one run, the run's constraints applied and its rubies choosing entries."""
        spans: dict[tuple[int, int], str | None] = {
            (c.start, c.end): ("*" if c.whole else None) for c in forced}
        nodes = self._parse(text, spans)
        chosen: dict[tuple[int, int], tuple[Entry, int]] = {}
        unmatched: dict[tuple[int, int], int] = {}
        for item in forced:
            if not item.whole:
                continue
            node = next((n for n in nodes if (n[0], n[1]) == (item.start, item.end)), None)
            if node is None or not node[2].known or "〻" in item.reading:
                continue
            want = kana_key(item.reading)
            if kana_key(node[2].kana) == want or kana_key(node[2].reading) == want:
                chosen[(item.start, item.end)] = (node[2], item.ruby)
                continue
            match = next((e for e in self.entries(text[item.start:item.end])
                          if kana_key(e.kana) == want or kana_key(e.reading) == want), None)
            if match is None:
                unmatched[(item.start, item.end)] = item.ruby
            else:
                chosen[(item.start, item.end)] = (match, item.ruby)
                spans[(item.start, item.end)] = f"*,*,*,*,*,*,{match.reading},{match.lemma}"
        replaced = {span for span in chosen if spans[span] not in ("*", None)}
        if replaced:
            nodes = self._parse(text, spans)
        rubies_at = {(c.start, c.end): c.ruby for c in forced if c.whole}
        tokens = []
        for start, end, found in nodes:
            span = (start, end)
            if not found.known:
                basis = UNKNOWN
            elif span in chosen and chosen[span][0].lemma_id == found.lemma_id:
                basis = RUBY
            elif span in rubies_at and (span in chosen or span in unmatched):
                basis = RUBY_UNMATCHED
            else:
                basis = PARSED
            changed = span in replaced and basis == RUBY
            tokens.append(Token(run, start, end, found, basis, rubies_at.get(span), changed))
        return tokens

    def line(self, reading: Reading) -> list[Token]:
        """The tokens of every run of a line."""
        out: list[Token] = []
        for index, run in enumerate(reading.runs):
            out.extend(self.run(run.text, index, [c for c in reading.constraints if c.run == index]))
        return out
