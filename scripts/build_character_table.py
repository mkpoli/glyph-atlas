"""Build data/vocab/characters.tsv, the character layer, from Unicode's own files.

The table answers what a code point *is*: its Unicode name, script, general category, age, block,
the 字母 it derives from, what it reads as, the grapheme it is a form of, and the characters
Unicode's confusables table pairs with it. One row per code point, keyed by `U+XXXX`. Nothing here
belongs to one written occurrence, so a unit or a crop never repeats a column of this table.

Sources, all from one Unicode release:

- `UnicodeData.txt` for the name, the general category, the range ends, and the canonical
  decomposition that says which kana a voiced kana is the voicing of (ば is は + U+3099).
- `Blocks.txt` for the block name.
- `Scripts.txt` for the script property; hentaigana, which Unicode classifies as Hiragana or
  Katakana and names `HENTAIGANA LETTER ...`, is labelled `hentaigana` here.
- `DerivedAge.txt` for the release that assigned the code point.
- `NamesList.txt` for the block name of each chart heading, and for the kana readings spelled by the
  character names. NamesList.txt is also what `scripts/build_hentaigana_table.py` reads; the two
  tables agree on names and disagree only in that this one is not limited to kana.
- `Jamo.txt` for the short names of the conjoining jamo, from which Unicode derives the name of
  each precomposed Hangul syllable (`HANGUL SYLLABLE GAG`); `UnicodeData.txt` writes the syllables
  as one `First`/`Last` range and names none of them.
- `confusables.txt` from the security directory, for the confusable pairs. The relation is
  directional in that table, which is why the column keeps its direction.
- `data/vocab/mj-hentaigana.tsv` for the MJ figure name and for the 字母 and 音価 of the hentaigana.
  The 字母 of a kana is curated work, not a Unicode property: this table takes the 字母 Unicode's
  own charts state, then MJ's where MJ has one, then a curated row of `data/vocab/graphemes.yaml`
  for the characters Unicode 18.0 added without a derivation note.
- `data/vocab/graphemes.yaml`, hand-written, for the grapheme groupings and for the kana Unicode
  names but does not give a reading.
- `Unihan.zip` of the same release, for `kIRG_JSource` in `Unihan_IRGSources.txt`: a `J0` source is
  JIS X 0208, which the shape families below read.
- `data/vocab/kanji-variants.tsv`, `data/vocab/kanji-equivalents.tsv` and `data/vocab/mj-kanji.tsv`
  for the kanji grapheme families no curated row states: `glyph_atlas.shape_families` joins the
  characters a source defines as one character in another shape, and the build writes the edges
  each family rests on to `data/vocab/grapheme-families.tsv`.
- `data/vocab/gugyeol.tsv`, hand-kept, for the 구결자 of the Hanyang private-use convention
  (U+F67E to U+F77C). Unicode assigns them nothing but a private-use code point, so the file is
  what gives each its script, reading and 字母.

Which code points are included: every character of every kana block that Unicode assigns, the CJK
Unified Ideographs and their extensions, the Hangul jamo and syllable blocks, the 구결자 of
`gugyeol.tsv`, and always the code points `graphemes.yaml` names, so a grapheme is never a dangling reference. Kana and Hangul outside
those blocks, which is to say the letters on the Enclosed CJK Letters and Months chart (㋕, ㉠ and
the like) and the halfwidth jamo, are not text characters and are left out.

    uv run python scripts/build_character_table.py cache/ucd

The script reads only the cache directory and reaches no network; refresh the cache with the files
of one Unicode release (`https://www.unicode.org/Public/18.0.0/ucd/`) before running it.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from glyph_atlas import shape_families

ROOT = Path(__file__).resolve().parents[1]
MJ_TABLE_NAME = "mj-hentaigana.tsv"
OVERRIDES_NAME = "graphemes.yaml"
GUGYEOL_NAME = "gugyeol.tsv"
OUTPUT_NAME = "characters.tsv"
VARIANTS_NAME = "kanji-variants.tsv"
EQUIVALENTS_NAME = "kanji-equivalents.tsv"
MJ_KANJI_NAME = "mj-kanji.tsv"
FAMILIES_NAME = "grapheme-families.tsv"
VOICING_NAME = "kana-voicing.tsv"
FAMILY_FIELDS = ("head", "a", "b", "relation", "source", "role", "detail")

#: The Unicode release the cached files and the generated table are from. Every file of the release
#: carries no version of its own, so the table records the one it was built against.
RELEASE = "18.0.0"

#: The blocks a dataset of Japanese writing needs characters from. The CJK Unified Ideographs are
#: here because they are the 字母 of the kana and the characters a source text is written in: a
#: record of ネ points at 子, and a record of 子 has to be able to say what 子 is. Kana Extended-B
#: is not: its letters are the tone marks of Taiwanese kana, which is not the writing this dataset
#: is about, and a table that held them would say a text character was written where none was.
BLOCKS = (
    "Hiragana",
    "Katakana",
    "Kana Extended-A",
    "Kana Supplement",
    "Small Kana Extension",
    "Katakana Phonetic Extensions",  # the small kana of Ainu, which the project holds records in
    "CJK Unified Ideographs",
    "CJK Unified Ideographs Extension A",
    "CJK Unified Ideographs Extension B",
    "CJK Unified Ideographs Extension C",
    "CJK Unified Ideographs Extension D",
    "CJK Unified Ideographs Extension E",
    "CJK Unified Ideographs Extension F",
    "CJK Unified Ideographs Extension G",
    "CJK Unified Ideographs Extension H",
    "CJK Unified Ideographs Extension I",
    # The compatibility ideographs are written in sources too (﨑 for 崎), and each is its own code
    # point with its own name in UnicodeData.txt.
    "CJK Compatibility Ideographs",
    "CJK Compatibility Ideographs Supplement",
    # Korean writing: the compatibility jamo a corpus transcribes a letter with, the conjoining jamo
    # of 옛한글 (Middle Korean spellings such as ᄫ and ᆞ are only encoded there), and the
    # precomposed syllables of modern Hangul.
    "Hangul Jamo",
    "Hangul Compatibility Jamo",
    "Hangul Jamo Extended-A",
    "Hangul Syllables",
    "Hangul Jamo Extended-B",
)

#: The name Unicode gives the ideographs of a CJK block, as a format over the hex code point.
#: `UnicodeData.txt` writes these blocks as a `First`/`Last` pair and names neither end, holding
#: that the names of the characters between are derived; `NamesList.txt` does not name them either.
#: The Unihan database, which would, is not a file this table needs: the name of an ideograph is the
#: name of its code point, which is what this writes out.
CJK_NAME = "CJK UNIFIED IDEOGRAPH-{point}"

#: The blocks whose characters are named after their own code point.
CJK_NAME_BLOCKS = frozenset(name for name in BLOCKS if name.startswith("CJK Unified Ideographs"))

#: The constants of the Hangul syllable name derivation in chapter 3.12 of the Unicode Standard: the
#: first syllable, the first leading consonant, vowel and trailing consonant (one before the first,
#: since a syllable may have none), and how many vowels and trailing consonants combine.
HANGUL_SYLLABLES_BLOCK = "Hangul Syllables"
S_BASE, L_BASE, V_BASE, T_BASE = 0xAC00, 0x1100, 0x1161, 0x11A7
V_COUNT, T_COUNT = 21, 28

#: The columns of the generated table, in order. `jibo` and `readings`
#: hold several values joined by a space, because a form may derive from more than one 字母, read as
#: more than one kana, and be confusable with more than one character; `grapheme` is one code point.
FIELDS = (
    "code_point",
    "char",
    "name",
    "alias",
    "script",
    "category",
    "age",
    "block",
    "jibo",
    "readings",
    "grapheme",
    "confusables",
)

#: The name a kana letter is spelled by, as Unicode names it, mapped to what it reads as. The
#: readings of the historic kana differ from the modern ones, and a name such as `WI` has to be read
#: as ゐ rather than as うぃ, which is what a romanisation would give.
KANA = {
    "A": "あ", "I": "い", "U": "う", "E": "え", "O": "お",
    "KA": "か", "KI": "き", "KU": "く", "KE": "け", "KO": "こ",
    "SA": "さ", "SI": "し", "SU": "す", "SE": "せ", "SO": "そ",
    "TA": "た", "TI": "ち", "TU": "つ", "TE": "て", "TO": "と",
    "NA": "な", "NI": "に", "NU": "ぬ", "NE": "ね", "NO": "の",
    "HA": "は", "HI": "ひ", "HU": "ふ", "HE": "へ", "HO": "ほ",
    "MA": "ま", "MI": "み", "MU": "む", "ME": "め", "MO": "も",
    "YA": "や", "YU": "ゆ", "YO": "よ",
    "RA": "ら", "RI": "り", "RU": "る", "RE": "れ", "RO": "ろ",
    "WA": "わ", "WI": "ゐ", "WE": "ゑ", "WO": "を", "N": "ん",
    # The archaic Y-row letters are not the vowel letters: the proposal's 五十音 table puts 𛄠
    # (`KATAKANA LETTER ARCHAIC YI`, U+1B120) in the Y-row under イ and 𛄡 (YE) under エ, which is
    # why their Unicode names spell YI, YE and WU where the letters they stand for are I, E and U.
    "YE": "𛀁", "WU": "𛄟", "KOTO": "こと",
    "YI": "い",
}
#: The small letters, and the small kana each of them reads as. A small kana is not the kana it is
#: the small form of: ぁ reads ぁ, and あ is the letter it is the small form of. Unicode spells the
#: name of the small kana with the full-size letter, so the map is what says which reading it has,
#: and `HIRAGANA LETTER SMALL KO` (U+1B132) reads こ.
SMALL = {
    "A": "ぁ", "I": "ぃ", "U": "ぅ", "E": "ぇ", "O": "ぉ",
    "TU": "っ", "YA": "ゃ", "YU": "ゅ", "YO": "ょ", "WA": "ゎ",
    "KA": "ゕ", "KE": "ゖ",
    "WI": "𛅐", "WE": "𛅑", "WO": "𛅒", "N": "𛅧",
    "KO": "𛄲", "YE": "𛅦",
}
#: The reading of a character whose name does not spell one, by code point.
#:
#: An archaic letter reads as itself and as the kana it stands for, which is how the kana tables
#: already list them: `hentaigana.tsv` gives 𛀁 the readings 𛀁 and え, and MJ names it E-1. The
#: order matters: the first reading is the one the 音価 map resolves through, and the modern kana
#: names the grapheme either way.
READINGS = {
    "U+1B001": ["𛀁", "え"],  # HIRAGANA LETTER ARCHAIC YE, which the MJ table names E-1
    "U+1B11F": ["𛄟", "う"],  # HIRAGANA LETTER ARCHAIC WU
    "U+1B120": ["𛄠", "い"],  # KATAKANA LETTER ARCHAIC YI, in the Y-row of the 五十音 under イ
    "U+1B121": ["𛄡", "え"],  # KATAKANA LETTER ARCHAIC YE, in the Y-row under エ
    "U+1B122": ["𛄢", "う"],  # KATAKANA LETTER ARCHAIC WU, in the W-row under ウ
    "U+1B168": ["𛅦", "え"],  # KATAKANA LETTER SMALL ARCHAIC YE
}
NAME = re.compile(r"^(?P<name>(?:HIRAGANA|KATAKANA|HENTAIGANA) LETTER (?P<body>.+))$")


@dataclass
class Range:
    """One line of Blocks.txt, Scripts.txt or DerivedAge.txt: a range and its value."""

    start: int
    end: int
    value: str


@dataclass
class Character:
    """One row of the generated table, before it is joined into a line."""

    code_point: str
    char: str
    name: str | None = None
    alias: str | None = None
    script: str = "unknown"
    category: str | None = None
    age: str | None = None
    block: str | None = None
    jibo: list[str] = field(default_factory=list)
    readings: list[str] = field(default_factory=list)
    grapheme: str | None = None
    confusables: list[str] = field(default_factory=list)

    def line(self) -> str:
        cells = [
            self.code_point,
            self.char,
            self.name or "",
            self.alias or "",
            self.script,
            self.category or "",
            self.age or "",
            self.block or "",
            " ".join(self.jibo),
            " ".join(self.readings),
            self.grapheme or "",
            " ".join(self.confusables),
        ]
        if len(cells) != len(FIELDS):
            raise AssertionError(f"{self.code_point}: {len(cells)} cells for {len(FIELDS)} fields")
        return "\t".join(cells)


def code_point(value: int) -> str:
    return f"U+{value:04X}"


def read_ranges(path: Path) -> list[Range]:
    """The data lines of a Unicode range file, `#` comments dropped."""
    ranges = []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        span, _, value = body.partition(";")
        first, _, last = span.strip().partition("..")
        ranges.append(Range(int(first, 16), int(last or first, 16), value.strip()))
    return ranges


def value_of(ranges: list[Range], point: int, default: str | None = None) -> str | None:
    """The value a range file gives a code point, or `default` when no range covers it.

    The ranges of one file do not overlap, so the first hit is the answer. The lists are short
    enough that a scan is cheaper than an interval tree.
    """
    for span in ranges:
        if span.start <= point <= span.end:
            return span.value
    return default


def unicode_data(path: Path) -> tuple[dict[int, tuple[str, str]], list[Range]]:
    """`UnicodeData.txt` as code point -> (name, general category), and its `First`/`Last` ranges.

    A range such as the CJK Unified Ideographs is written as a `First`/`Last` pair whose names say
    nothing about the characters between them. The pair is returned as a range, and the names of the
    ideographs inside it are derived from the block, which is the only place they are stated.
    """
    data: dict[int, tuple[str, str]] = {}
    ranges: list[Range] = []
    pending: tuple[int, str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = line.split(";")
        if len(cells) < 3:
            continue
        point, name, category = int(cells[0], 16), cells[1], cells[2]
        if name.endswith(", First>"):
            pending = point, category
            continue
        if name.endswith(", Last>"):
            if pending is not None:
                ranges.append(Range(pending[0], point, pending[1]))
            pending = None
            continue
        data[point] = (name, category)
    return data, ranges


#: The combining voicing marks: U+3099 voices a kana (は to ば) and U+309A half-voices it (は to ぱ).
VOICING_MARKS = (0x3099, 0x309A)


def voicings(path: Path) -> dict[int, tuple[int, int]]:
    """The kana `UnicodeData.txt` decomposes canonically to a kana and a voicing mark.

    Field 5 is the decomposition: `30D0;KATAKANA LETTER BA;Lo;0;L;30CF 3099;;;;N;;;;;` says バ is ハ
    with U+3099. Only canonical two-part decompositions ending in a voicing mark are kept, and only
    for letters: ゞ is ゝ + U+3099 too, but an iteration mark is not the voicing of a kana.
    """
    found: dict[int, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = line.split(";")
        if len(cells) < 6 or not NAME.match(cells[1]):
            continue
        parts = cells[5].split()
        if len(parts) == 2 and not parts[0].startswith("<") and int(parts[1], 16) in VOICING_MARKS:
            found[int(cells[0], 16)] = (int(parts[0], 16), int(parts[1], 16))
    return found


def names_list(path: Path) -> tuple[dict[int, str], dict[int, str]]:
    """`NamesList.txt` as code point -> name, and code point -> the kanji it derives from.

    NamesList.txt names the characters of a chart, including the ranges `UnicodeData.txt` leaves
    unnamed, which is why it is read beside it. Its `* derived from XXXX` note is Unicode's own
    statement of the 字母 of a kana form, and is what `scripts/build_hentaigana_table.py` reads for
    the same purpose.
    """
    names: dict[int, str] = {}
    derived: dict[int, str] = {}
    current: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([0-9A-F]{4,6})\t(.+)$", line)
        if match:
            current = int(match.group(1), 16)
            names[current] = match.group(2).strip()
            continue
        note = re.match(r"^\t\* derived from ([0-9A-F]{4,6})$", line)
        if note and current is not None:
            derived[current] = chr(int(note.group(1), 16))
    return names, derived


def confusables(path: Path) -> dict[int, list[str]]:
    """The `confusables.txt` pairs that name one code point each, by source code point.

    A line holds a source sequence, a target sequence and a type; the pairs that matter here are
    the single-character ones, because a sequence is a lookalike of a whole word rather than a
    pairing of two characters. The relation is directional in the table and the direction is kept.
    """
    pairs: dict[int, list[str]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        cells = [cell.strip() for cell in body.split(";")]
        if len(cells) < 3 or not cells[0] or not cells[1]:
            continue
        try:
            source = int(cells[0], 16)
            target = int(cells[1], 16)
        except ValueError:
            continue
        pairs[source].append(code_point(target))
    return {source: sorted(targets) for source, targets in pairs.items()}


def jamo_short_names(path: Path) -> dict[int, str]:
    """`Jamo.txt` as code point -> the short name of a conjoining jamo (IEUNG's is empty)."""
    names: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        point, short = (cell.strip() for cell in body.split(";"))
        names[int(point, 16)] = short
    return names


def hangul_syllable_name(point: int, short: dict[int, str]) -> str:
    """The name Unicode derives for a precomposed Hangul syllable, as chapter 3.12 states it."""
    index = point - S_BASE
    lead, rest = divmod(index, V_COUNT * T_COUNT)
    vowel, trail = divmod(rest, T_COUNT)
    parts = [short[L_BASE + lead], short[V_BASE + vowel], short[T_BASE + trail] if trail else ""]
    return "HANGUL SYLLABLE " + "".join(parts)


def mj_table(path: Path) -> dict[str, dict[str, str]]:
    """`mj-hentaigana.tsv` by code point, with the `#` header comments dropped."""
    with path.open(encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return {row["code_point"]: row for row in csv.DictReader(lines, delimiter="\t") if row["code_point"]}


def gugyeol_table(path: Path) -> dict[int, dict[str, str]]:
    """`gugyeol.tsv` by code point, with the `#` header comments dropped.

    Every row has to be a private-use code point: a 구결자 that Unicode encodes is a character of its
    own block, and a row here would give it a second script.
    """
    with path.open(encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    rows: dict[int, dict[str, str]] = {}
    for row in csv.DictReader(lines, delimiter="\t"):
        point = int(row["code_point"].removeprefix("U+"), 16)
        if not 0xE000 <= point <= 0xF8FF:
            raise ValueError(f"{path}: {row['code_point']} is not a private-use code point")
        if row["char"] != chr(point):
            raise ValueError(f"{path}: {row['code_point']} is written as {row['char']!r}")
        if point in rows:
            raise ValueError(f"{path}: {row['code_point']} is listed twice")
        rows[point] = row
    return rows


def overrides(path: Path) -> dict[str, Any]:
    """`graphemes.yaml`: the 音価 of the kana under `kana`, the curated rows under `characters`."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("kana", "characters"):
        if key not in document:
            raise ValueError(f"{path} names no {key!r} map")
    # Families are explicit orthographic relations, independent of readings and of
    # the broader equivalence policies used for aligning transcriptions.
    assigned: dict[str, str] = {}
    for head, family in document.get("families", {}).items():
        if not re.fullmatch(r"U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*", head):
            raise ValueError(f"invalid family representative: {head}")
        members = family.get("members", [])
        if len(members) < 2 or any(not isinstance(c, str) or len(c) != 1 for c in members):
            raise ValueError(f"family {head} needs distinct single-character members")
        points = [code_point(ord(c)) for c in members]
        if len(set(points)) != len(points) or points[0] != head:
            raise ValueError(f"family {head} must start with its representative and have unique members")
        sources = family.get("sources", [])
        if not family.get("relation") or not sources:
            raise ValueError(f"family {head} needs a relation and cited sources")
        for source in sources:
            evidence = document.get("sources", {}).get(source, {})
            if not evidence.get("title") or not str(evidence.get("url", "")).startswith("https://"):
                raise ValueError(f"family {head} has no valid source: {source}")
        for point in points:
            if point in assigned:
                raise ValueError(f"character {point} belongs to overlapping families")
            assigned[point] = head
            row = document["characters"].setdefault(point, {})
            if row.get("grapheme", head) != head:
                raise ValueError(f"character {point} has conflicting grapheme assignments")
            row["grapheme"] = head
    return document


def reading_of(name: str | None) -> list[str]:
    """What a kana character reads as, from the name Unicode gives it.

    `HENTAIGANA LETTER NE-KO` reads ね and こ, and so does `KATAKANA LETTER ALTERNATE NE`, whose
    name carries the modifier `ALTERNATE` before the letter. A name that spells no one kana - a
    digraph such as `KATAKANA DIGRAPH TOKI`, which is written as two kana rather than read as one -
    gives nothing.
    """
    if not name:
        return []
    body = name.removeprefix("HENTAIGANA LETTER ").removeprefix("HIRAGANA LETTER ").removeprefix(
        "KATAKANA LETTER "
    )
    if body == name:  # a digraph or another name that does not spell one kana
        return []
    small = body.startswith("SMALL ")
    if small:
        body = body.removeprefix("SMALL ")
    for modifier in ("ARCHAIC ", "ALTERNATE "):
        body = body.removeprefix(modifier)
    body = re.sub(r"-\d+$", "", body)
    table = SMALL if small else KANA
    return [table[part] for part in body.split("-") if part in table]


def script_of(point: int, name: str | None, ranges: list[Range]) -> str:
    """The script of a code point, in the labels `schema.Script` holds.

    Unicode puts the hentaigana in the Hiragana and Katakana scripts and says which ones they are in
    their names; a hentaigana is a kana form rather than a script, so the name wins here. The Han
    script is the kanji of this dataset, and is written `han` after ISO 15924. The Common and
    Inherited scripts are the marks and symbols that belong to no one script - the prolonged sound
    mark, the middle dot, the combining voicing marks - and a dataset of writing has one label for
    them: `symbol`.
    """
    if name and name.startswith("HENTAIGANA LETTER "):
        return "hentaigana"
    script = value_of(ranges, point, "Unknown") or "Unknown"
    return {
        "Han": "han",
        "Hangul": "hangul",
        "Hiragana": "hiragana",
        "Katakana": "katakana",
        "Common": "symbol",
        "Inherited": "symbol",
        "Unknown": "unknown",
    }.get(script, script.lower())


def selected_blocks(blocks: list[Range], wanted: tuple[str, ...] = BLOCKS) -> list[Range]:
    """The block ranges whose block name is one of `wanted`."""
    return [span for span in blocks if span.value in wanted]


def build(
    ucd: Path,
    vocab: Path | None = None,
    refused: list[tuple[str, shape_families.Edge]] | None = None,
    voicing: list[Voicing] | None = None,
) -> tuple[list[Character], list[shape_families.Family]]:
    """Every character in code point order, and the kanji families of more than one character.

    A voiced kana written as a letter and a combining mark (𛂞 + U+3099) is a row of its own, keyed
    by its code points and placed right after its letter. `refused`, when given, collects the merges
    `shape_families` turned down, for the summary; `voicing` collects what each member of a voiced
    grapheme rests on, for `kana-voicing.tsv`.
    """
    vocab = vocab or ROOT / "data" / "vocab"
    blocks = read_ranges(ucd / "Blocks.txt")
    scripts = read_ranges(ucd / "Scripts.txt")
    ages = read_ranges(ucd / "DerivedAge.txt")
    data, ranges = unicode_data(ucd / "UnicodeData.txt")
    voiced = voicings(ucd / "UnicodeData.txt")
    names, derived = names_list(ucd / "NamesList.txt")
    lookalikes = confusables(ucd / "confusables.txt")
    jamo = jamo_short_names(ucd / "Jamo.txt")
    mj = mj_table(vocab / MJ_TABLE_NAME)
    document = overrides(vocab / OVERRIDES_NAME)
    curated: dict[str, dict] = document["characters"]
    kana: dict[str, str] = document["kana"]

    characters = _stated_characters(blocks, scripts, ages, data, ranges, names, curated, kana, jamo)
    _add_gugyeol(characters, gugyeol_table(vocab / GUGYEOL_NAME), blocks, ages, ranges)
    # The curated rows are applied before anything is derived from them, so that a 字母 stated by
    # hand reaches the graphemes and the confusable pairs like any other.
    for point, values in curated.items():
        number = int(point.removeprefix("U+"), 16)
        if number in characters:
            _apply_curated(characters[number], values)
    _add_jibo(characters, mj, derived)
    _add_readings(characters)
    _add_graphemes(characters, kana, curated)
    _add_voiced_graphemes(characters, voiced, curated)
    families = _add_shape_families(characters, document, ucd, vocab, refused)
    _add_confusables(characters, lookalikes)
    sequences, evidence = _voiced_sequences(characters, voiced, mj)
    _check_graphemes(characters, sequences)
    if voicing is not None:
        voicing.extend(evidence)
    rows = [*characters.values(), *sequences]
    return sorted(rows, key=lambda row: [int(point.removeprefix("U+"), 16) for point in row.code_point.split()]), families


def read_table(path: Path) -> list[dict[str, str]]:
    """A generated tab-separated table with its `#` header lines dropped."""
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader([line for line in handle if not line.startswith("#")], delimiter="\t"))


def jis0208(ucd: Path) -> set[str]:
    """The ideographs whose Unihan `kIRG_JSource` is `J0`, the JIS X 0208 repertoire."""
    with zipfile.ZipFile(ucd / "Unihan.zip") as archive:
        text = archive.read("Unihan_IRGSources.txt").decode("utf-8")
    found = set()
    for line in text.splitlines():
        cells = line.split("\t")
        if len(cells) == 3 and cells[1] == "kIRG_JSource" and cells[2].startswith("J0-"):
            found.add(chr(int(cells[0].removeprefix("U+"), 16)))
    return found


def _add_shape_families(
    characters: dict[int, Character], document: dict[str, Any], ucd: Path, vocab: Path,
    refused: list[tuple[str, shape_families.Edge]] | None = None,
) -> list[shape_families.Family]:
    """Put the kanji a source defines as one character in another shape under one grapheme.

    A curated family of `graphemes.yaml` keeps its representative and may take in further members;
    every other family's representative is chosen by `shape_families.head_of`. Only ideographs
    take part, and a character another rule has already placed under a grapheme other than itself
    is never moved, so the kana rules and the curated rows stand.
    """
    ideographs = {row.char for row in characters.values()
                  if (row.name or "").startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"))}
    default_figure = {row["mj"]: chr(int(row["implemented_code_point"].removeprefix("U+"), 16))
                      for row in read_table(vocab / MJ_KANJI_NAME) if row["implemented_code_point"]}
    curated = {chr(int(head.removeprefix("U+"), 16)): list(family["members"])
               for head, family in (document.get("families") or {}).items()}
    families = shape_families.families(
        read_table(vocab / VARIANTS_NAME), read_table(vocab / EQUIVALENTS_NAME), ideographs,
        default_figure, jis0208(ucd), curated, refused)
    for family in families:
        head = code_point(ord(family.head))
        for member in family.members:
            row = characters[ord(member)]
            if row.grapheme not in (None, row.code_point, head):
                raise ValueError(f"{row.code_point} is under {row.grapheme} and a shape family of {head}")
            row.grapheme = head
    return families


def _check_graphemes(characters: dict[int, Character], sequences: list[Character] = ()) -> None:
    """Fail the build when a character hangs under a grapheme it does not belong to.

    Two rules, both of which a reader depends on:

    - A 変体仮名 is a form of one 音価, and the grapheme of a 音価 is the hiragana of U+3041 to
      U+3096. `atlas character --grapheme` lists the forms of a kana from that, and `refs.forms` is
      what a review dialog offers.
    - A small kana is a letter and not a form of the kana it is the small form of, so its grapheme
      is a small kana too: ァ is a form of ぁ, and っ is not a form of つ.

    Both are easy to break with a 音価 map entry in the wrong order or a name table read from the
    wrong map, which is how the I, U and E hentaigana once ended up under the historic katakana and
    how ヶ once ended up under け, so the build checks them rather than trusting them.
    """
    broken = []
    for row in [*characters.values(), *sequences]:
        grapheme = characters.get(int(row.grapheme.removeprefix("U+"), 16)) if row.grapheme else None
        if row.script == "hentaigana":
            if grapheme is None or grapheme.script != "hiragana" or not (
                HIRAGANA_FIRST <= int(grapheme.code_point.removeprefix("U+"), 16) <= HIRAGANA_LAST
            ):
                broken.append(f"{row.code_point} {row.name} -> {row.grapheme}")
        elif row.name and "SMALL" in row.name and (grapheme is None or "SMALL" not in (grapheme.name or "")):
            named = grapheme.name if grapheme else "a character the table does not hold"
            broken.append(f"{row.code_point} {row.name} -> {row.grapheme} {named}")
    if broken:
        raise ValueError(
            f"{len(broken)} characters are under the wrong grapheme: " + "; ".join(broken[:5])
        )


def _stated_characters(
    blocks: list[Range],
    scripts: list[Range],
    ages: list[Range],
    data: dict[int, tuple[str, str]],
    ranges: list[Range],
    names: dict[int, str],
    curated: dict[str, dict],
    kana: dict[str, str],
    jamo: dict[int, str],
) -> dict[int, Character]:
    """One row per assigned code point of the included blocks.

    A code point is assigned when `UnicodeData.txt` names it, when it lies in one of its ranges, or
    when `NamesList.txt` names it. A code point `graphemes.yaml` states, as a curated row or as a
    音価, is included whether or not any of those reach it, so that a curated grapheme can never
    point at a character the table does not hold.
    """
    stated = {int(point.removeprefix("U+"), 16) for point in (*curated, *kana)}

    def name_of(point: int, block: str | None) -> str | None:
        name, _ = data.get(point, (None, None))
        if name:
            return name
        if names.get(point):
            return names[point]
        if block in CJK_NAME_BLOCKS and value_of(ranges, point) is not None:
            # A CJK block has holes in it: code points the block reserves and Unicode has not
            # assigned. Only the ones inside a First/Last range are characters.
            return CJK_NAME.format(point=f"{point:04X}")
        if block == HANGUL_SYLLABLES_BLOCK and value_of(ranges, point) is not None:
            return hangul_syllable_name(point, jamo)
        return None

    def character(point: int, block: str | None) -> Character:
        name, category = data.get(point, (None, None))
        name = name_of(point, block)
        return Character(
            code_point=code_point(point),
            char=chr(point),
            name=name,
            script=script_of(point, name, scripts),
            category=category or value_of(ranges, point),
            age=value_of(ages, point),
            block=block,
        )

    characters: dict[int, Character] = {}
    for span in selected_blocks(blocks):
        for point in range(span.start, span.end + 1):
            if name_of(point, span.value) is None and point not in stated:
                continue  # an unassigned code point inside the block
            characters[point] = character(point, span.value)
    for point in sorted(stated - set(characters)):
        characters[point] = character(point, value_of(blocks, point))
    return characters


def _add_gugyeol(
    characters: dict[int, Character],
    gugyeol: dict[int, dict[str, str]],
    blocks: list[Range],
    ages: list[Range],
    ranges: list[Range],
) -> None:
    """One row per 구결자 of `gugyeol.tsv`, with the script, readings and 字母 it states.

    Unicode names no private-use character, and its Scripts.txt gives them the script Unknown; the
    block, category and age are still Unicode's, as for any other code point.
    """
    for point, row in gugyeol.items():
        characters[point] = Character(
            code_point=code_point(point),
            char=chr(point),
            script="gugyeol",
            category=value_of(ranges, point),
            age=value_of(ages, point),
            block=value_of(blocks, point),
            jibo=row["jibo"].split(),
            readings=row["readings"].split(),
        )


def _add_jibo(
    characters: dict[int, Character], mj: dict[str, dict[str, str]], derived: dict[int, str]
) -> None:
    """Fill the 字母 of a kana, from Unicode's chart note and then from MJ's table.

    Unicode's NamesList states the 字母 of every form its charts call derived - `* derived from
    5B50` - and that statement is the one this table keeps, because it is the one a reader of the
    chart sees. MJ's table states the 字母 of every hentaigana it figures, which reaches the same
    characters and adds the ones Unicode leaves out. A character `graphemes.yaml` states a 字母 for
    is applied before this and is not overwritten, and a kana neither reaches is left without one
    rather than guessed at.
    """
    for number, row in characters.items():
        if row.jibo:
            continue
        if number in derived:
            row.jibo = [derived[number]]
        entry = mj.get(row.code_point)
        if not row.jibo and entry and entry.get("jibo"):
            row.jibo = [entry["jibo"]]
        if entry and entry.get("name") and entry["name"] != row.name:
            # MJ names the same form the way the kana tables do; where that is the name Unicode
            # already gives, there is no second name to keep.
            row.alias = entry["name"]


def _add_readings(characters: dict[int, Character]) -> None:
    """Fill what each kana reads as, from the table, from the name, then from the 字母.

    A Hangul compatibility jamo reads as itself, as a modern kana does: ㅿ is the letter ㅿ. The
    conjoining jamo and the syllables are left without a reading, since no Unicode file states one.
    """
    for row in characters.values():
        if row.code_point in READINGS:
            row.readings = list(READINGS[row.code_point])
            continue
        if row.name and row.name.startswith("HANGUL LETTER "):
            row.readings = [row.char]
            continue
        readings = reading_of(row.name)
        if readings:
            row.readings = readings


def _add_graphemes(
    characters: dict[int, Character], kana: dict[str, str], curated: dict[str, dict]
) -> None:
    """Say which grapheme each character is a form of.

    A grapheme is one shape as the writing system distinguishes shapes, and for kana the writing
    system distinguishes shapes by the 音価 they stand for: ね, ネ, 𛂒 and 𛄧 are four characters and
    one grapheme, because a reader of ね reads all four the same way. The rules are:

    1. A character `graphemes.yaml` states a grapheme for is a form of that grapheme, and a
       character it states a `letter` for is a form of the grapheme of that kana. The second is how
       a name that leaves the reading open - `HENTAIGANA LETTER N-MU-MO-1` reads ん, む and も - is
       settled by hand.
    2. A kana is a form of the grapheme of the kana its name spells, looked up among the modern kana
       of the 音価 map: `HENTAIGANA LETTER NE-3`, `KATAKANA LETTER SMALL WI` and `KATAKANA LETTER
       ALTERNATE NE` all name the kana they are a form of. A katakana letter and its hiragana are one
       grapheme, which is the mapping `refs.to_hiragana` already reads a kana with. Where several
       characters of the map read alike, the hiragana of the modern block names the grapheme: い is
       the grapheme of the I-1 to I-4 hentaigana, and 𛄠, the historic katakana, is a form of it.
    3. Any other character is its own grapheme, which is every kanji, every digraph and every kana
       no reading reaches. 子 is a form of nothing and nothing is a form of it.

    `grapheme` is one code point and not a list. A character whose name names several kana, as 𛂘
    names ね and こ, is a form of each - but a record has one grapheme, and rule 1 is how the
    project says which one names it.
    """
    by_reading = _self_named(kana)
    by_name = _kana_names(kana, by_reading)

    for row in characters.values():
        stated = curated.get(row.code_point) or {}
        if stated.get("grapheme"):
            row.grapheme = stated["grapheme"]
            continue
        letter = stated.get("letter")
        if letter:
            # A curated `letter` states which kana the form is of, and the letter names the
            # grapheme, so it is read through the map that names graphemes.
            row.grapheme = by_reading.get(letter) or _grapheme_of_reading(kana).get(letter, row.code_point)
            continue
        named = _named_grapheme(row, by_reading, by_name)
        row.grapheme = named if named is not None else row.code_point


def _add_voiced_graphemes(
    characters: dict[int, Character], voiced: dict[int, tuple[int, int]], curated: dict[str, dict]
) -> None:
    """Put each voiced kana under the voicing of its base's grapheme.

    ば is は + U+3099 and バ is ハ + U+3099 by their canonical decompositions, and は and ハ are one
    grapheme, so ば and バ are one grapheme too: a katakana letter and its hiragana are one grapheme
    whether or not the letter is voiced. The hiragana of the modern block names it, as it names the
    grapheme of the base. A voicing no hiragana spells - ヷ is ワ + U+3099, and no hiragana is わ with
    the mark - is named by its lowest code point. A curated `grapheme` still wins.
    """
    groups: dict[tuple[str, int], list[Character]] = defaultdict(list)
    for point, (base, mark) in sorted(voiced.items()):
        row, under = characters.get(point), characters.get(base)
        if row is None or under is None or (curated.get(row.code_point) or {}).get("grapheme"):
            continue
        groups[(under.grapheme or under.code_point, mark)].append(row)
    for rows in groups.values():
        head = min(rows, key=lambda row: _modern_first(row.code_point)).code_point
        for row in rows:
            row.grapheme = head


@dataclass
class Voicing:
    """One row of `kana-voicing.tsv`: a fact one member of a voiced grapheme rests on."""

    head: str
    member: str
    char: str
    relation: str  # `voicing`: the mark voices the base; `base`: the letter is a form of the base
    tier: str  # `attested`: the source states it; `derived`: this build joins two stated facts
    source: str
    detail: str


VOICING_FIELDS = ("head", "member", "char", "relation", "tier", "source", "detail")


def _voiced_sequences(
    characters: dict[int, Character], voiced: dict[int, tuple[int, int]], mj: dict[str, dict[str, str]]
) -> tuple[list[Character], list[Voicing]]:
    """Each form of a kana written with a voicing mark after it, and what each member rests on.

    ば is は + U+3099, so every form of は written with U+3099 after it is a form of ば: 𛂞 + U+3099
    and each other hentaigana of は. No hentaigana has a precomposed voiced form, so a hentaigana is
    voiced only this way. A sequence NFC composes to a character of its own (は + U+3099 is ば, ハ +
    U+3099 is バ) is that character, which the table already holds, and makes no row: the site stores
    every written character composed. A voicing is taken from a precomposed kana Unicode decomposes
    to a letter and the mark; a mark no such kana attests (あ + U+3099, the ツ + U+309A of the Ainu
    orthography) makes no row, and neither does a voicing no hiragana names (ヷ, わ + U+3099).

    A letter joins only where a source reads it as the base: its Unicode name (HENTAIGANA LETTER
    HA-1) or its MJ 音価. The archaic WU letters are under う by this build's own readings, which no
    source states, so they make no voiced sequence. A sequence keeps its letter's script, age, block
    and 字母, and has no reading, as ば has none.

    Each member cites the decomposition that voices it (`voicing`) and, for a sequence, the source
    that reads its letter as the base (`base`). A precomposed kana's voicing is `attested`: Unicode
    decomposes it. A sequence's voicing is `derived`: Unicode decomposes ば to は + U+3099 and reads
    𛂞 as は, and the build joins the two.
    """
    groups: dict[tuple[str, int], list[Character]] = defaultdict(list)
    for point, (base, mark) in sorted(voiced.items()):
        row, under = characters.get(point), characters.get(base)
        if row is not None and under is not None:
            groups[(under.grapheme or under.code_point, mark)].append(row)
    members: dict[str, list[Character]] = defaultdict(list)
    for row in characters.values():
        if row.script in ("hiragana", "katakana", "hentaigana"):
            members[row.grapheme or row.code_point].append(row)
    sequences: list[Character] = []
    evidence: list[Voicing] = []

    def decomposition(row: Character) -> str:
        base, mark = voiced[int(row.code_point.removeprefix("U+"), 16)]
        return f"UnicodeData.txt: {row.code_point} {row.char} decomposes to {code_point(base)} {code_point(mark)}"

    def stated(letter: Character, reading: str) -> list[tuple[str, str]]:
        """The sources that read `letter` as `reading`, with what each says."""
        found = []
        if reading in reading_of(letter.name):
            found.append(("unicode-ucd", f"NamesList.txt: {letter.code_point} {letter.name}, read {reading}"))
        entry = mj.get(letter.code_point)
        if entry and reading in entry["readings"].split("/"):
            found.append(("mj-hentaigana", f"{entry['mj']} {entry['name']}: 音価 {entry['readings']}"))
        return found

    for (base, mark), precomposed in sorted(groups.items(), key=lambda item: (_modern_first(item[0][0]), item[0][1])):
        head = precomposed[0].grapheme
        if not HIRAGANA_FIRST <= int(head.removeprefix("U+"), 16) <= HIRAGANA_LAST:
            # ヷ to ヺ voice わ, ゐ, ゑ and を, and no hiragana spells those voicings to name the
            # grapheme a voiced hentaigana would be a form of; the precomposed katakana stay alone.
            continue
        # The voicing of the base itself (ば for は), else the first that voices a form of it (ヷ).
        basis = next((row for row in precomposed
                      if code_point(voiced[int(row.code_point.removeprefix("U+"), 16)][0]) == base), precomposed[0])
        for row in sorted(precomposed, key=lambda row: (row.code_point != head, _modern_first(row.code_point))):
            evidence.append(Voicing(head, row.code_point, row.char, "voicing", "attested", "unicode-ucd",
                                    decomposition(row)))
        reading = characters[int(base.removeprefix("U+"), 16)].char
        for letter in sorted(members[base], key=lambda row: _modern_first(row.code_point)):
            char = letter.char + chr(mark)
            if len(unicodedata.normalize("NFC", char)) == 1:
                continue  # は + U+3099 is ば
            sources = stated(letter, reading)
            if not sources:
                continue  # under the base by this build's own reading only
            sequence = Character(
                code_point=f"{letter.code_point} {code_point(mark)}", char=char, script=letter.script,
                age=letter.age, block=letter.block, jibo=list(letter.jibo), grapheme=head)
            sequences.append(sequence)
            evidence.append(Voicing(head, sequence.code_point, char, "voicing", "derived", "unicode-ucd",
                                    f"{decomposition(basis)}; {letter.code_point} {letter.char} reads {reading}, "
                                    f"so {letter.char} + {code_point(mark)} is its voicing"))
            evidence.extend(Voicing(head, sequence.code_point, char, "base", "attested", source, detail)
                            for source, detail in sources)
    return sequences, evidence


def write_voicing(evidence: list[Voicing], target: Path) -> None:
    """Write what each member of a voiced kana grapheme rests on, with its sources in the header."""
    lines = [
        "# Voiced kana graphemes: each member and what it rests on; see scripts/build_character_table.py.",
        "# A member is a precomposed voiced kana or a form of its base with the combining mark after it.",
    ]
    for identifier in sorted({row.source for row in evidence}):
        record = yaml.safe_load((ROOT / "data" / "sources" / f"{identifier}.yaml").read_text(encoding="utf-8"))
        lines.append(f"# source {identifier}: {record['name']}; {record['licence']} "
                     f"({record['licence_evidence']}); {record['attribution']}")
    lines += [
        f"#   unicode-ucd: UnicodeData.txt and NamesList.txt of Unicode {RELEASE}",
        ("# relation: voicing is the decomposition the mark voices the base by; base is what puts the letter "
         "under the base."),
        ("# tier: attested is stated by the source; derived joins a stated decomposition and a stated "
         "reading, which no source states together."),
        (f"# graphemes: {len({row.head for row in evidence})}, members: {len({row.member for row in evidence})}, "
         f"rows: {len(evidence)}"),
        "\t".join(VOICING_FIELDS),
    ]
    lines += ["\t".join(getattr(row, name) for name in VOICING_FIELDS) for row in evidence]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _self_named(kana: dict[str, str]) -> dict[str, str]:
    """The rows of the 音価 map that name their own character, as reading -> code point.

    The map holds two kinds of row. A row that reads as its own character names a grapheme: ぁ is
    the member that names the grapheme of the small あ. A row that reads as another character is a
    form of that character's grapheme, which is how ァ is a form of ぁ and ネ of ね.
    """
    return {
        reading: point
        for point, reading in kana.items()
        if reading == chr(int(point.removeprefix("U+"), 16))
    }


def _named_grapheme(
    row: Character, by_reading: dict[str, str], by_name: dict[tuple[bool, str], str]
) -> str | None:
    """The grapheme a character's name puts it under, or `None` when its name names no kana.

    A character the 音価 map names for itself is its own grapheme: ゕ is the small か and the map
    says so, so the name `SMALL KA` does not send it to か, and the small kana of Kana Supplement
    are letters of their own rather than forms of the full-size kana.

    Otherwise the name's letter comes first, because it is the letter the chart prints, and it is
    read as the small letter or the full-size one by whether the name says `SMALL`. An archaic
    letter reads as itself as well as as the kana it stands for - `KATAKANA LETTER ARCHAIC YI` reads
    𛄠 and い - and its own shape is not the grapheme it belongs to, so a reading that is the
    character itself is passed over and the first reading that names a kana settles it.
    """
    if row.char in by_reading:
        return by_reading[row.char]
    for reading in row.readings:
        if reading != row.char and reading in by_reading:
            return by_reading[reading]
    named = _kana_body(row.name)
    return by_name.get(named) if named else None


def _grapheme_of_reading(kana: dict[str, str]) -> dict[str, str]:
    """reading -> the code point that names its grapheme.

    The 音価 map lists more than one character for a reading: い stands for い and for the archaic
    Yi of Kana Extended-A, and え for え and for the archaic Ye. The grapheme of a reading is the
    hiragana of the modern block, so that every hentaigana of い is a form of U+3044 and the
    historic katakana is a form of it too, rather than the historic letter swallowing the forms.

    A reading no modern hiragana covers takes the lowest code point of the map, which is what the
    small kana of Kana Supplement do: 𛅐 is the grapheme of the small ゐ.
    """
    by_reading: dict[str, list[str]] = {}
    for point, reading in kana.items():
        by_reading.setdefault(reading, []).append(point)
    return {reading: min(points, key=_modern_first) for reading, points in by_reading.items()}


def _modern_first(point: str) -> tuple[int, int]:
    """Order the characters of one reading so that a hiragana of the modern block comes first."""
    number = int(point.removeprefix("U+"), 16)
    return (0 if HIRAGANA_FIRST <= number <= HIRAGANA_LAST else 1, number)


#: The hiragana of the modern block, U+3041 ぁ to U+3096 ゖ: the kana a grapheme is named for.
HIRAGANA_FIRST = 0x3041
HIRAGANA_LAST = 0x3096


def _kana_body(name: str | None) -> tuple[bool, str] | None:
    """A Unicode name as `(small, body)`: `KATAKANA LETTER SMALL ARCHAIC YE` -> `(True, 'YE')`."""
    if not name:
        return None
    body = name.removeprefix("HENTAIGANA LETTER ").removeprefix("HIRAGANA LETTER ").removeprefix(
        "KATAKANA LETTER "
    )
    if body == name:
        return None
    small = body.startswith("SMALL ")
    if small:
        body = body.removeprefix("SMALL ")
    for modifier in ("ARCHAIC ", "ALTERNATE "):
        body = body.removeprefix(modifier)
    body = body.split("-")[0]
    return (small, body) if body else None


def _kana_names(kana: dict[str, str], named: dict[str, str]) -> dict[tuple[bool, str], str]:
    """The kana a Unicode name names, as `(small, body)` -> grapheme code point.

    A body is the letter part of the name, `NE` in `HENTAIGANA LETTER NE-3` and in `KATAKANA LETTER
    ALTERNATE NE`, so two characters written as one kana reach one grapheme. The body is read
    through the 音価 of the letter (`NE` is ね) and then through the member of the 音価 map that
    names the grapheme of that reading: `SMALL A` is ぁ, the small hiragana, and `A` is あ. A body
    that names no kana of the map - the `N-MU-MO` of U+1B11D - is left out and the curated row for
    that character settles it instead.
    """
    # The reading is spelled by the name of the member that names the grapheme: `SMALL A` is ぁ,
    # which the map names, and `A` is あ. A reading no member names - こと, the reading of the
    # digraph 𛄣 - keeps the map's own resolution, which the digraph does not use anyway.
    resolved = dict(named)
    for reading, point in _grapheme_of_reading(kana).items():
        resolved.setdefault(reading, point)
    names: dict[tuple[bool, str], str] = {}
    for small, table in ((False, KANA), (True, SMALL)):
        for body, reading in table.items():
            point = resolved.get(reading)
            if point:
                names.setdefault((small, body), point)
    return names


def _add_confusables(characters: dict[int, Character], lookalikes: dict[int, list[str]]) -> None:
    """Record the confusable pairs that are between two characters of this table.

    `confusables.txt` writes one direction of a symmetric relation - it pairs what a reader may
    mistake for what, and it writes 𛄧 → 子 without writing 子 → 𛄧 - so a pair that concerns two
    characters of this table is recorded on both, and the direction the table gives is lost. What
    survives is the pairing, which is what a reviewer needs: 𛄧 and 子 look alike, and they are two
    characters all the same.
    """
    for number, row in characters.items():
        for target in lookalikes.get(number, ()):
            other = int(target.removeprefix("U+"), 16)
            if other not in characters or other == number:
                continue
            row.confusables = sorted({*row.confusables, target})
            characters[other].confusables = sorted({*characters[other].confusables, row.code_point})


def _apply_curated(row: Character, values: dict) -> None:
    """The fields `graphemes.yaml` states for one character, which the UCD files cannot."""
    if values.get("grapheme"):
        row.grapheme = values["grapheme"]
    for name in ("alias", "age", "block"):
        if values.get(name):
            setattr(row, name, str(values[name]))
    if values.get("jibo"):
        row.jibo = list(values["jibo"])
    if values.get("readings"):
        row.readings = list(values["readings"])
    if values.get("script"):
        row.script = str(values["script"])


def write(rows: list[Character], target: Path) -> None:
    """Write the table."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(FIELDS)] + [row.line() for row in rows]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_families(families: list[shape_families.Family], target: Path, vocab: Path) -> None:
    """Write the edges each kanji family rests on, with the sources they cite in the header.

    The counts describe the rows below: a curated family no edge joins (two 新旧字体 pairs) is
    part of the build but carries no row here, so it is not counted.
    """
    rows = [(family, edge) for family in families for edge in family.edges]
    emitted = [family for family in families if family.edges]
    used = sorted({edge.source for _, edge in rows} | {"unihan"})
    lines = [
        "# Kanji grapheme families: the edges that join each family; see src/glyph_atlas/shape_families.py.",
        "# The members of a family are the characters of data/vocab/characters.tsv under its head.",
    ]
    for identifier in used:
        path = ROOT / "data" / "sources" / f"{identifier}.yaml"
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} is missing; the grapheme-families header cites the source {identifier!r}")
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        lack = [key for key in ("name", "licence", "licence_evidence", "attribution") if key not in record]
        if lack:
            raise ValueError(f"{path} lacks {', '.join(lack)}; the grapheme-families header cites "
                             f"the source {identifier!r}")
        lines.append(f"# source {identifier}: {record['name']}; {record['licence']} "
                     f"({record['licence_evidence']}); {record['attribution']}")
    lines += [
        (f"#   from {VARIANTS_NAME} (compatibility, z, reduction and the corroborating rows), "
         f"{EQUIVALENTS_NAME} (itaiji, source mj-kanji: the MJ文字情報一覧表's X0213 column) and "
         f"{MJ_KANJI_NAME} (実装したUCS, the default figure of a code point)"),
        (f"#   unihan: Unihan_IRGSources.txt kIRG_JSource J0 (JIS X 0208) of Unicode {RELEASE}, "
         "for the representative and for which reductions need a second source"),
        ("# role: merge joins a and b; corroborates is the second source a reduction between two "
         "JIS X 0208 characters needs."),
        "# columns: " + ", ".join(FAMILY_FIELDS),
        (f"# families: {len(emitted)} ({sum(1 for family in emitted if family.curated)} curated), "
         f"characters: {sum(len(family.members) for family in emitted)}, rows: {len(rows)}"),
        "\t".join(FAMILY_FIELDS),
    ]
    lines += ["\t".join((code_point(ord(family.head)), edge.a, edge.b, edge.relation, edge.source,
                         edge.role, edge.detail)) for family, edge in rows]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ucd", type=Path, help="directory holding the Unicode release's files")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "vocab" / OUTPUT_NAME,
        help=f"where to write the table (default: data/vocab/{OUTPUT_NAME})",
    )
    parser.add_argument("--vocab", type=Path, default=ROOT / "data" / "vocab", help="where the curated tables are")
    arguments = parser.parse_args(argv)

    refused: list[tuple[str, shape_families.Edge]] = []
    voicing: list[Voicing] = []
    rows, families = build(arguments.ucd, arguments.vocab, refused, voicing)
    write(rows, arguments.out)
    write_families(families, arguments.out.with_name(FAMILIES_NAME), arguments.vocab)
    write_voicing(voicing, arguments.out.with_name(VOICING_NAME))
    scripts = Counter(row.script for row in rows)
    ages = Counter(row.age for row in rows)
    print(f"{len(rows)} rows from Unicode {RELEASE} -> {arguments.out}")
    print(f"  with a 字母: {sum(1 for row in rows if row.jibo)}")
    print(f"  with a reading: {sum(1 for row in rows if row.readings)}")
    print(f"  scripts: {dict(scripts.most_common())}")
    print(f"  ages: {dict(sorted(ages.items(), key=lambda item: (item[0] is None, item[0])))}")
    sizes = Counter(len(family.members) for family in families)
    print(f"  kanji families: {len(families)}, sizes {dict(sorted(sizes.items()))}")
    reasons = Counter(reason for reason, _ in refused)
    print(f"  refused merges: {len(refused)}"
          + (": " + ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))
             if refused else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
