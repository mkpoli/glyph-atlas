"""Reference tables: hentaigana candidates, 字母, readings and character equivalence.

Characters are literal strings and code points are `U+XXXX` strings. The tables under
`data/vocab/` are read once and cached; a table that is missing raises `MissingTable` naming the
script that writes it, so importing the module never fails over a table that has not been built.

    from kuzushiji_atlas import refs

    refs.candidates("か")               # ['U+304B', 'U+1B017', ...]
    refs.jibo("U+1B098")                # '子'
    refs.readings("U+1B098")            # ['ね', 'こ']
    refs.same("国", "國", "align-v1")   # True

A policy is a row of `data/vocab/equivalence-policies.yaml` naming the relations it composes;
`strict` keeps only Unicode compatibility mappings, `align-v1` adds the kana, 新旧字体 and 異体字
relations the alignment uses. A relation links characters pairwise and the links carry across a
policy, except that a kana form with several readings stands for each of them without making the
readings stand for one another. `to_code_points` and `from_code_points` convert between text and
`U+XXXX` strings.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VOCAB = ROOT / "data" / "vocab"
HENTAIGANA_TSV = "hentaigana.tsv"
MJ_TSV = "mj-hentaigana.tsv"
EQUIVALENTS_TSV = "kanji-equivalents.tsv"
POLICIES_YAML = "equivalence-policies.yaml"
BUILT_BY = {
    HENTAIGANA_TSV: "scripts/build_hentaigana_table.py",
    MJ_TSV: "scripts/build_mj_table.py",
    EQUIVALENTS_TSV: "scripts/build_kanji_equivalents.py",
}
#: hentaigana.tsv and mj-hentaigana.tsv joined: the Unicode name and 字母, the MJ figure, the 音価
#: merged into `readings`, the 戸籍統一文字番号, the 学術用変体仮名番号 and the 国語研 URL.
HENTAIGANA_FIELDS = [
    "code_point",
    "char",
    "name",
    "readings",
    "primary_reading",
    "jibo",
    "jibo_code_point",
    "unicode_readings",
    "unicode_name",
    "mj",
    "mj_name",
    "mj_readings",
    "koseki",
    "gakujutsu",
    "ninjal_url",
    "note",
]
#: the relations a policy may name, with what each one is.
RELATIONS = {
    "kana-reading": "kana forms that share one reading, katakana mapped through hiragana",
    "compatibility": "a Unicode compatibility decomposition to one CJK unified ideograph",
    "shinji-kyuji": "a 旧字 and the 常用漢字 新字 it is unified into",
    "itaiji": "characters JIS X 0213 places at one 面区点",
    "voicing": "a kana and its voiced or semi-voiced form",
    "small-kana": "a small kana and its full-size kana",
}
#: hiragana and the katakana code point 0x60 above it; U+30F6 ヶ maps to U+3096 ゖ.
KATAKANA_OFFSET = 0x60
HIRAGANA_FIRST = 0x3041
HIRAGANA_LAST = 0x3096
ARCHAIC = {"𛀁": "U+1B001", "𛄟": "U+1B11F"}
VOICING = [
    ("か", "が"), ("き", "ぎ"), ("く", "ぐ"), ("け", "げ"), ("こ", "ご"),
    ("さ", "ざ"), ("し", "じ"), ("す", "ず"), ("せ", "ぜ"), ("そ", "ぞ"),
    ("た", "だ"), ("ち", "ぢ"), ("つ", "づ"), ("て", "で"), ("と", "ど"),
    ("は", "ば", "ぱ"), ("ひ", "び", "ぴ"), ("ふ", "ぶ", "ぷ"),
    ("へ", "べ", "ぺ"), ("ほ", "ぼ", "ぽ"), ("う", "ゔ"),
]
SMALL_KANA = [
    ("ぁ", "あ"), ("ぃ", "い"), ("ぅ", "う"), ("ぇ", "え"), ("ぉ", "お"), ("っ", "つ"),
    ("ゃ", "や"), ("ゅ", "ゆ"), ("ょ", "よ"), ("ゎ", "わ"), ("ゕ", "か"), ("ゖ", "け"),
]


class MissingTable(RuntimeError):
    """A generated vocabulary table is not on disk. Run the build script that writes it."""


def to_code_point(char: str) -> str:
    """`"か"` -> `"U+304B"`."""
    return f"U+{ord(char):04X}"


def to_char(code_point: str) -> str:
    """`"U+304B"` -> `"か"`."""
    return chr(int(code_point.removeprefix("U+"), 16))


def to_code_points(text: str) -> list[str]:
    """Every code point of `text`, in order."""
    return [to_code_point(char) for char in text]


def from_code_points(code_points: Iterable[str]) -> str:
    """The text spelled by `U+XXXX` strings."""
    return "".join(to_char(code_point) for code_point in code_points)


def to_hiragana(text: str) -> str:
    """Katakana mapped through hiragana; other characters are unchanged."""
    return "".join(
        chr(ord(char) - KATAKANA_OFFSET) if 0x30A1 <= ord(char) <= 0x30F6 else char for char in text
    )


def _read_tsv(name: str) -> list[dict[str, str]]:
    path = VOCAB / name
    if not path.exists():
        raise MissingTable(f"{path} is missing; run {BUILT_BY[name]} to write it")
    with path.open(encoding="utf-8") as handle:
        rows = [line for line in handle if not line.startswith("#")]
    return [dict(row) for row in csv.DictReader(rows, delimiter="\t")]


@cache
def _unicode_rows() -> tuple[dict[str, str], ...]:
    return tuple(_read_tsv(HENTAIGANA_TSV))


@cache
def _mj_rows() -> tuple[dict[str, str], ...]:
    return tuple(_read_tsv(MJ_TSV))


@cache
def _equivalence_rows() -> tuple[dict[str, str], ...]:
    return tuple(_read_tsv(EQUIVALENTS_TSV))


@cache
def _policies() -> dict[str, dict]:
    path = VOCAB / POLICIES_YAML
    if not path.exists():
        raise MissingTable(f"{path} is missing; it is a hand-written table")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def clear_cache() -> None:
    """Drop every cached table, so the next call reads the files again."""
    for cached in (
        _unicode_rows,
        _mj_rows,
        _equivalence_rows,
        _policies,
        _definition,
        _hentaigana_rows,
        _by_code_point,
        _ordinary_kana,
        _reading_forms,
        _primary_forms,
        _relation,
        _ambiguous,
        _components,
    ):
        cached.cache_clear()


@cache
def _hentaigana_rows() -> tuple[dict, ...]:
    """hentaigana.tsv joined with mj-hentaigana.tsv on the code point, as a tuple of rows.

    `readings` merges the readings of the Unicode name and the MJ 音価, the Unicode name first,
    and `primary_reading` is the first of them that is not the character itself, so that a form
    named after its own reading (𛀁) still points at the kana it is a form of. `jibo` and
    `jibo_code_point` are the 字母 both tables agree on. Fields without a value are `None`. Rows
    whose code point is in the MJ table only are appended.
    """
    unicode_rows = _unicode_rows()
    by_code_point = {row["code_point"]: row for row in _mj_rows() if row["code_point"]}
    rows = []
    joined = set()
    for unicode_row in unicode_rows:
        code_point = unicode_row["code_point"]
        mj_row = by_code_point.get(code_point)
        joined.add(code_point)
        unicode_readings = [reading for reading in unicode_row["readings"].split("/") if reading]
        mj_readings = [reading for reading in mj_row["readings"].split("/") if reading] if mj_row else []
        readings = list(dict.fromkeys(unicode_readings + mj_readings))
        rows.append(
            {
                "code_point": code_point,
                "char": unicode_row["char"],
                "name": unicode_row["name"] or None,
                "readings": readings,
                "primary_reading": _primary(readings, unicode_row["char"]),
                "jibo": unicode_row["jibo"] or (mj_row["jibo"] if mj_row else None),
                "jibo_code_point": unicode_row["jibo_code_point"] or (mj_row["jibo_code_point"] if mj_row else None),
                "unicode_readings": unicode_readings,
                "unicode_name": unicode_row["name"] or None,
                "mj": mj_row["mj"] if mj_row else None,
                "mj_name": (mj_row["name"] or None) if mj_row else None,
                "mj_readings": mj_readings,
                "koseki": (mj_row["koseki"] or None) if mj_row else None,
                "gakujutsu": (mj_row["gakujutsu"] or None) if mj_row else None,
                "ninjal_url": (mj_row["ninjal_url"] or None) if mj_row else None,
                "note": (mj_row["note"] or None) if mj_row else None,
            }
        )
    for code_point, mj_row in by_code_point.items():
        if code_point in joined:
            continue
        readings = [reading for reading in mj_row["readings"].split("/") if reading]
        rows.append(
            {
                "code_point": code_point,
                "char": to_char(code_point),
                "name": None,
                "readings": readings,
                "primary_reading": _primary(readings, to_char(code_point)),
                "jibo": mj_row["jibo"] or None,
                "jibo_code_point": mj_row["jibo_code_point"] or None,
                "unicode_readings": [],
                "unicode_name": None,
                "mj": mj_row["mj"],
                "mj_name": mj_row["name"] or None,
                "mj_readings": readings,
                "koseki": mj_row["koseki"] or None,
                "gakujutsu": mj_row["gakujutsu"] or None,
                "ninjal_url": mj_row["ninjal_url"] or None,
                "note": mj_row["note"] or None,
            }
        )
    return tuple(rows)


def hentaigana() -> list[dict]:
    """The joined rows: the Unicode name and 字母, the MJ figure, the merged readings and 音価,
    the 戸籍統一文字番号, the 学術用変体仮名番号 and the 国語研 URL.

    The rows are shared between calls; do not change them.
    """
    return list(_hentaigana_rows())


def _primary(readings: list[str], char: str) -> str | None:
    for reading in readings:
        if reading != char:
            return reading
    return readings[0] if readings else None


@cache
def _by_code_point() -> dict[str, dict]:
    return {row["code_point"]: row for row in _hentaigana_rows()}


def jibo(code_point: str) -> str | None:
    """The 字母 of a hentaigana code point, or `None` when no table has it."""
    row = _by_code_point().get(_normalise(code_point))
    return row["jibo"] if row else None


def readings(code_point: str) -> list[str]:
    """Every reading of a hentaigana code point, in table order; `[]` when no table has it."""
    row = _by_code_point().get(_normalise(code_point))
    return list(row["readings"]) if row else []


def _normalise(code_point: str) -> str:
    return "U+" + code_point.removeprefix("U+").removeprefix("u+").upper().zfill(4)


@cache
def _ordinary_kana() -> dict[str, str]:
    """reading -> code point of the ordinary kana; every hiragana letter reads as itself."""
    ordinary = {chr(code_point): to_code_point(chr(code_point)) for code_point in range(HIRAGANA_FIRST, HIRAGANA_LAST + 1)}
    ordinary.update(ARCHAIC)
    return ordinary


@cache
def _reading_forms() -> dict[str, list[str]]:
    """reading -> sorted hentaigana code points whose readings include it."""
    forms: dict[str, set[str]] = {}
    for row in _hentaigana_rows():
        for reading in row["readings"]:
            forms.setdefault(reading, set()).add(row["code_point"])
    return {reading: sorted(code_points) for reading, code_points in forms.items()}


@cache
def _primary_forms() -> dict[str, list[str]]:
    """reading -> sorted hentaigana code points whose primary reading it is."""
    forms: dict[str, set[str]] = {}
    for row in _hentaigana_rows():
        if row["primary_reading"]:
            forms.setdefault(row["primary_reading"], set()).add(row["code_point"])
    return {reading: sorted(code_points) for reading, code_points in forms.items()}


def candidates(reading: str) -> list[str]:
    """The code points a transcriber may have written for `reading`.

    The ordinary hiragana comes first, then every hentaigana with that 音価 in code point order,
    shared-reading letters such as KA-KE included. Katakana maps through hiragana; a reading that
    no table has returns `[]`.
    """
    if not reading:
        return []
    hiragana = to_hiragana(reading)
    code_points = list(_reading_forms().get(hiragana, ()))
    ordinary = _ordinary_kana().get(hiragana)
    if ordinary:
        code_points = [ordinary] + [code_point for code_point in code_points if code_point != ordinary]
    return code_points


def policy(name: str) -> dict:
    """One row of data/vocab/equivalence-policies.yaml: version, relations, description."""
    return _definition(name)


@cache
def _definition(name: str) -> dict:
    if name not in _policies():
        raise KeyError(f"unknown policy {name!r}; {VOCAB / POLICIES_YAML} has {sorted(_policies())}")
    return _policies()[name]


@cache
def _relation(name: str) -> tuple[tuple[str, str], ...]:
    """The pairs that carry one relation on to other characters."""
    if name == "kana-reading":
        ordinary = _ordinary_kana()
        pairs = [
            (chr(code_point), chr(code_point + KATAKANA_OFFSET))
            for code_point in range(HIRAGANA_FIRST, HIRAGANA_LAST + 1)
        ]
        for reading, code_points in _primary_forms().items():
            forms = [to_char(code_point) for code_point in code_points]
            base = ordinary.get(reading, code_points[0])
            if base not in code_points:
                forms.append(to_char(base))
            katakana = ord(to_char(base)) + KATAKANA_OFFSET
            if 0x30A1 <= katakana <= 0x30F6:
                forms.append(chr(katakana))
            pairs += [(first, second) for index, first in enumerate(forms) for second in forms[index + 1 :]]
        return tuple(pairs)
    if name == "voicing":
        return tuple(_pairs(VOICING))
    if name == "small-kana":
        return tuple(_pairs(SMALL_KANA))
    if name in ("compatibility", "shinji-kyuji", "itaiji"):
        return tuple((row["a"], row["b"]) for row in _equivalence_rows() if row["kind"] == name)
    raise KeyError(f"unknown relation {name!r}; refs.RELATIONS has {sorted(RELATIONS)}")


def _pairs(groups: list[tuple[str, ...]]) -> list[tuple[str, str]]:
    """Every unordered pair of the hiragana groups, and of their katakana counterparts."""
    pairs = []
    for group in groups:
        for script in (group, tuple(chr(ord(char) + KATAKANA_OFFSET) for char in group)):
            pairs += [(first, second) for index, first in enumerate(script) for second in script[index + 1 :]]
    return pairs


@cache
def _ambiguous(name: str) -> dict[str, frozenset[str]]:
    """The links a relation makes without carrying its class across.

    A hentaigana with several readings stands for the kana of each of them, but the readings are
    not thereby equivalent: 𛀅 reads あ and を, while あ and を do not stand for one another.
    """
    if name != "kana-reading":
        return {}
    adjacency: dict[str, set[str]] = {}
    for row in _hentaigana_rows():
        for reading in row["readings"]:
            base = _ordinary_kana().get(reading)
            if reading == row["primary_reading"] or base is None or base == row["code_point"]:
                continue
            adjacency.setdefault(row["char"], set()).add(to_char(base))
            adjacency.setdefault(to_char(base), set()).add(row["char"])
    return {char: frozenset(neighbours) for char, neighbours in adjacency.items()}


@cache
def _components(name: str) -> dict[str, frozenset[str]]:
    """Every character of a policy mapped to the class the policy's relations put it in."""
    adjacency: dict[str, set[str]] = {}
    for relation in _definition(name)["relations"]:
        if relation not in RELATIONS:
            raise KeyError(f"policy {name!r} names unknown relation {relation!r}")
        for left, right in _relation(relation):
            adjacency.setdefault(left, set()).add(right)
            adjacency.setdefault(right, set()).add(left)
    components: dict[str, frozenset[str]] = {}
    for start in adjacency:
        if start in components:
            continue
        members: set[str] = set()
        pending = [start]
        while pending:
            char = pending.pop()
            if char in members:
                continue
            members.add(char)
            pending.extend(adjacency.get(char, ()))
        group = frozenset(members)
        for char in members:
            components[char] = group
    return components


def equivalents(char: str, policy: str) -> set[str]:
    """The characters `char` may stand for under `policy`, itself included.

    A character that no table has stands only for itself. A string of several characters stands
    for itself alone.
    """
    if len(char) != 1:
        return {char} if char else set()
    result = set(_components(policy).get(char, ())) | {char}
    for relation in _definition(policy)["relations"]:
        result |= _ambiguous(relation).get(char, frozenset())
    return result


def same(a: str, b: str, policy: str) -> bool:
    """Whether `policy` reads `a` and `b` for one another. Strings of several characters only
    match themselves."""
    if a == b:
        return True
    if len(a) != 1 or len(b) != 1:
        return False
    return b in equivalents(a, policy)
