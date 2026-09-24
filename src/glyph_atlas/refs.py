"""Reference tables: the character layer, hentaigana candidates, readings and character equivalence.

Characters are literal strings and code points are `U+XXXX` strings. The tables under
`data/vocab/` are read once and cached; a table that is missing raises `MissingTable` naming the
script that writes it, so importing the module never fails over a table that has not been built.

    from glyph_atlas import refs

    refs.character("U+1B127")           # the Character row of the character layer
    refs.character("U+1B127").jibo      # ['子'], the 字母 the form derives from
    refs.grapheme("U+1B127")            # 'U+306D', the ね it is a form of
    refs.jibo("U+1B098")                # '子', the first 字母 of a character
    refs.readings("U+1B098")            # ['ね', 'こ']
    refs.candidates("ね")               # ['U+306D', 'U+1B092', ..., 'U+1B127'], likeliest first
    refs.forms("ね")                    # the same forms, the modern kana first
    refs.same("国", "國", "align-v1")   # True

`characters.tsv` is the character layer, one row per code point of the kana and the CJK unified
ideographs, built by `scripts/build_character_table.py` from Unicode's own files. It is what knows
what a character is: its name, script, 字母, readings, grapheme and confusables. A unit carries a
code point and reaches all of that through this module, so no unit repeats it.

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

from .schema import Character, Ligature, Script

ROOT = Path(__file__).resolve().parents[2]
VOCAB = ROOT / "data" / "vocab"
CHARACTERS_TSV = "characters.tsv"
HENTAIGANA_TSV = "hentaigana.tsv"
MJ_TSV = "mj-hentaigana.tsv"
EQUIVALENTS_TSV = "kanji-equivalents.tsv"
POLICIES_YAML = "equivalence-policies.yaml"
LIGATURES_YAML = "ligatures.yaml"
BUILT_BY = {
    CHARACTERS_TSV: "scripts/build_character_table.py",
    HENTAIGANA_TSV: "scripts/build_hentaigana_table.py",
    MJ_TSV: "scripts/build_mj_table.py",
    EQUIVALENTS_TSV: "scripts/build_kanji_equivalents.py",
}
#: hentaigana.tsv and mj-hentaigana.tsv joined, with the character layer for everything the two
#: kana tables do not state: the Unicode name and 字母, the MJ figure, the 音価 merged into
#: `readings`, the 戸籍統一文字番号, the 学術用変体仮名番号 and the 国語研 URL.
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
def _character_rows() -> tuple[dict[str, str], ...]:
    return tuple(_read_tsv(CHARACTERS_TSV))


@cache
def _equivalence_rows() -> tuple[dict[str, str], ...]:
    return tuple(_read_tsv(EQUIVALENTS_TSV))


@cache
def _policies() -> dict[str, dict]:
    path = VOCAB / POLICIES_YAML
    if not path.exists():
        raise MissingTable(f"{path} is missing; it is a hand-written table")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def _ligature_rows() -> dict[str, Ligature]:
    """`data/vocab/ligatures.yaml`, the characters that are two kana set as one.

    The file is hand-written because the relation is not a Unicode property: the name of U+2A708 is
    `CJK UNIFIED IDEOGRAPH-2A708`. A tree without the file has no ligature relations and still has
    the character layer, so a missing overlay answers no ligatures rather than an error.
    """
    path = VOCAB / LIGATURES_YAML
    if not path.exists():
        # The ligature overlay is not the base table: a tree without it still has the character
        # layer, and a call that needs the base table must name the script that writes *that* table
        # rather than stopping here. An absent overlay is an absent relation, answered as none.
        return {}
    rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("ligatures") or {}
    ligatures: dict[str, Ligature] = {}
    for code_point, row in rows.items():
        components = [to_code_point(char) for char in row["components"]]
        ligatures[_normalise(code_point)] = Ligature(
            components=components,
            reading=row.get("reading"),
            kind=row.get("kind") or "katakana-ligature",
            evidence=row.get("evidence"),
        )
    return ligatures


def ligatures() -> dict[str, Ligature]:
    """Every ligature of the layer by code point, in code point order.

    A ligature is one encoded identity that is two kana set as one: 𪜈 is ト + モ, ゟ is よ + り.
    The components are characters of their own and the ligature is a form of neither of them, so a
    search for ヨ does not answer 𛄦 unless the caller asks for ligatures, which is what this is for.
    """
    return {code_point: _ligature_rows()[code_point] for code_point in sorted(_ligature_rows())}


def ligature(code_point: str) -> Ligature | None:
    """The components and the reading of a ligature, or `None` when the character is not one."""
    return _ligature_rows().get(_normalise(code_point))


def derived(char: str) -> list[str]:
    """The code points that state `char` as a 字母: the kana written as that kanji.

    The layer records the relation from a kana to the kanji it derives from, so this is the other
    direction. A reviewer looking at 子 is asking which kana are written as 子, and the answer is
    `refs.derived("子")`; the kana are characters of their own and 子 is not a form of them.
    """
    return list(_by_jibo().get(char, ()))


def clear_cache() -> None:
    """Drop every cached table, so the next call reads the files again."""
    for cached in (
        _unicode_rows,
        _mj_rows,
        _character_rows,
        _equivalence_rows,
        _policies,
        _ligature_rows,
        _definition,
        _hentaigana_rows,
        _characters,
        _ordered_characters,
        graphemes,
        _grapheme_families,
        _grapheme_info,
        _by_code_point,
        _by_reading,
        _by_jibo,
        _ordinary_kana,
        _reading_forms,
        _primary_forms,
        _kana_by_reading,
        _written_forms,
        _relation,
        _ambiguous,
        _components,
        _by_ligature,
    ):
        cached.cache_clear()


@cache
def _hentaigana_rows() -> tuple[dict, ...]:
    """hentaigana.tsv joined with mj-hentaigana.tsv on the code point and with the character layer.

    `readings` merges the readings of the Unicode name and the MJ 音価, the Unicode name first, and
    `primary_reading` is the first of them that is not the character itself, so that a form named
    after its own reading (𛀁) still points at the kana it is a form of. `name`, `jibo` and
    `jibo_code_point` come from the character layer, which is where 字母 lives; `jibo` is its first
    and `jibo_code_point` the code point of that kanji. Fields without a value are `None`. Rows
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
        character = _characters().get(code_point)
        jibo = character.jibo[0] if character and character.jibo else None
        rows.append(
            {
                "code_point": code_point,
                "char": unicode_row["char"],
                "name": unicode_row["name"] or None,
                "readings": readings,
                "primary_reading": _primary(readings, unicode_row["char"]),
                "jibo": jibo,
                "jibo_code_point": to_code_point(jibo) if jibo else None,
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
        character = _characters().get(code_point)
        jibo = character.jibo[0] if character and character.jibo else (mj_row["jibo"] or None)
        rows.append(
            {
                "code_point": code_point,
                "char": to_char(code_point),
                "name": None,
                "readings": readings,
                "primary_reading": _primary(readings, to_char(code_point)),
                "jibo": jibo,
                "jibo_code_point": to_code_point(jibo) if jibo else None,
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
def _characters() -> dict[str, Character]:
    """The character layer, by code point.

    The table states several values in one cell, joined by a space, because a form may derive from
    more than one 字母, read as more than one kana and be confusable with more than one character.
    A row is turned into a `schema.Character` once, so a caller shares the object rather than a
    fresh copy of it; do not change one.
    """
    characters: dict[str, Character] = {}
    # The generated base table is read first: it is the table whose builder a missing-file error has
    # to name, and the hand-written ligature overlay is read only once the base is there.
    rows = _character_rows()
    ligatures = _ligature_rows()
    for row in rows:
        values = {
            name: (row[name] or None)
            for name in ("name", "alias", "category", "age", "block", "grapheme")
        }
        characters[row["code_point"]] = Character(
            code_point=row["code_point"],
            char=row["char"],
            script=row["script"] or "unknown",
            jibo=_split(row["jibo"]),
            readings=_split(row["readings"]),
            confusables=_split(row["confusables"]),
            ligature=ligatures.get(row["code_point"]),
            **values,
        )
    return characters


def _split(cell: str) -> list[str]:
    """One cell of a table as its values: `"子 こ"` -> `["子", "こ"]`."""
    return [value for value in cell.replace("\u3000", " ").split(" ") if value]


@cache
def _ordered_characters() -> tuple[Character, ...]:
    return tuple(row for _, row in sorted(_characters().items()))


def characters() -> list[Character]:
    """Every character of the character layer, in code point order.

    The rows are shared between calls; do not change them.
    """
    return list(_ordered_characters())


def character(code_point: str) -> Character | None:
    """The character layer's row for a code point, or `None` when the table has no row.

    `refs.character("U+1B127")` is 𛄧: `KATAKANA LETTER ALTERNATE NE`, script katakana, 字母 子,
    reading ね, a form of the grapheme U+306D, confusable with U+5B50.
    """
    return _characters().get(_normalise(code_point))


def grapheme(code_point: str) -> str | None:
    """The representative of a character's curated grapheme family.

    仮 and 假 share U+4EEE while retaining separate encoded identities. Kana keep
    their curated families. Characters without a stated relation represent themselves.
    """
    row = character(code_point)
    return row.grapheme or row.code_point if row else None


@cache
def graphemes() -> dict[str, list[str]]:
    """Every grapheme mapped to its written characters. Do not mutate the shared index."""
    forms: dict[str, list[str]] = {}
    for row in characters():
        forms.setdefault(row.grapheme or row.code_point, []).append(row.code_point)
    return forms


@cache
def _grapheme_families() -> dict[str, dict]:
    path = VOCAB / "graphemes.yaml"
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    families = {}
    for head, family in document.get("families", {}).items():
        members = [to_code_point(char) for char in family["members"]]
        if set(members) != set(graphemes().get(head, [])):
            raise ValueError(f"family {head} disagrees with characters.tsv; rebuild the character table")
        families[head] = {
            "members": members,
            "relation": family["relation"],
            "evidence": [{"id": key, **document["sources"][key]} for key in family["sources"]],
        }
    return families


@cache
def _grapheme_info(head: str) -> dict:
    row = character(head)
    stated = _grapheme_families().get(head)
    points = (stated["members"] if stated else
              [head] + [point for point in graphemes()[head] if point != head])
    members = [{"code_point": point, "char": to_char(point)} for point in points]
    return {
        "code_point": head, "char": row.char, "name": row.name, "script": str(row.script),
        "label": " = ".join(member["char"] for member in members),
        "members": members, "character_count": len(members),
        "relation": stated["relation"] if stated else "kana-family" if len(members) > 1 else "self",
        "evidence": stated["evidence"] if stated else [],
        "url": f"/layers/graphemes/{head}",
    }


def grapheme_info(code_point: str) -> dict | None:
    """Family identity, written members and cited relation; shared read-only metadata.

    Either 仮 or 假 resolves to the same family. Counts are supplied by the caller's
    occurrence index; a relation never changes a character's reading or code point.
    """
    head = grapheme(code_point)
    return _grapheme_info(head) if head else None


def script_of(char: str) -> Script:
    """The script of one character, from the character layer.

    This is what the table is for: a code point's script is a property of the character, so the
    importers ask here rather than each carrying a range list that drifts from Unicode. Katakana
    and hiragana are kept apart, a hentaigana answers `hentaigana`, a kanji answers `han`, and the
    marks and symbols of the kana blocks - ー, ・, the iteration marks, the combining voicing marks
    - answer `symbol`, which is how this project labels the characters that belong to no one
    script. A character the table does not hold answers `unknown`.
    """
    row = character(to_code_point(char)) if len(char) == 1 else None
    return row.script if row else Script.UNKNOWN


@cache
def _by_code_point() -> dict[str, dict]:
    return {row["code_point"]: row for row in _hentaigana_rows()}


def jibo(code_point: str) -> str | None:
    """The first 字母 of a character, or `None` when the character table has none for it.

    字母 is metadata on a character, so this reads the character layer rather than any unit. A
    character with several 字母, as 𛂘 derives from 子 and from こ, answers with the first; the whole
    list is `refs.character(code_point).jibo`.
    """
    row = character(code_point)
    return row.jibo[0] if row and row.jibo else None


def readings(code_point: str) -> list[str]:
    """Every reading of a character, in table order; `[]` when it is not a kana.

    The readings merge the Unicode name's and the MJ 音価's, the Unicode name's first, so 𛂘 reads
    ね and then こ.
    """
    row = character(code_point)
    return list(row.readings) if row else []


def jibo_of(unicode: str | None) -> list[str]:
    """The 字母 of a code point sequence, each character's own, in order; `[]` when it has none.

    A unit's `unicode` is a sequence - a kana with a combining voicing mark is two code points - so
    this walks it and collects what the character layer says about each character. The 字母 is not a
    field of a unit: it is metadata on a character, and this is what reaches it.
    """
    letters: list[str] = []
    for point in (unicode or "").split():
        row = character(point)
        for letter in row.jibo if row else ():
            if letter not in letters:
                letters.append(letter)
    return letters


def jibo_of_unit(unicode: str | None) -> str | None:
    """The first 字母 of a code point sequence, or `None`; the whole list is `jibo_of`."""
    letters = jibo_of(unicode)
    return letters[0] if letters else None


def _normalise(code_point: str) -> str:
    if len(code_point) == 1 and not code_point.isascii():
        return to_code_point(code_point)
    return "U+" + code_point.removeprefix("U+").removeprefix("u+").upper().zfill(4)


def normalise(code_point: str) -> str:
    """A code point in the table's spelling: `"u+2a708"` -> `"U+2A708"`.

    The tables are keyed by `U+XXXX`, so a caller that takes a code point from a URL, a query or a
    person typing needs one spelling before it looks anything up.
    """
    return _normalise(code_point)


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
    """The code points a transcriber may have written for `reading`, the likeliest first.

    The ordinary hiragana comes first, then every hentaigana with that 音価 in code point order,
    shared-reading letters such as KA-KE included, and then the kana forms of the reading that are
    neither: the alternate katakana of Unicode 18.0, which a Meiji-period source prints where a
    later one prints ネ. Katakana maps through hiragana; a reading that no table has returns `[]`.

    This is the list a classifier's scores are masked to, so it is ordered by how likely a form is
    to be the one written. `forms` is the same ordered list.
    """
    if not reading:
        return []
    hiragana = to_hiragana(reading)
    code_points = list(_reading_forms().get(hiragana, ()))
    ordinary = _ordinary_kana().get(hiragana)
    if ordinary:
        code_points = [ordinary] + [code_point for code_point in code_points if code_point != ordinary]
    return code_points + [point for point in _written_forms().get(hiragana, ()) if point not in code_points]


def forms(reading: str) -> list[str]:
    """Every character written for `reading`, the ordinary kana first.

    The list is the forms the character layer knows, in the order a reviewer wants them: the modern
    kana of the reading, then its hentaigana in code point order, then its katakana. A reviewer
    asking what ね can look like gets all ten, and the two a modern text would use come first.
    """
    if not reading:
        return []
    hiragana = to_hiragana(reading)
    ordered = candidates(reading)
    return ordered + [point for point in _kana_by_reading().get(hiragana, ()) if point not in ordered]


def search(term: str, *, like: str | None = None, limit: int | None = None) -> list[Character]:
    """The characters a query names, the most literal reading of the query first.

    A reviewer looking for a character has one of five things in hand, and this answers all of them
    without being told which: a code point (`U+1B127`), the character itself (𛄧 or ね), a reading
    (ね, ネ, ねこ), a 字母 (`子`), or a Unicode name (`KATAKANA LETTER ALTERNATE NE`).

    A reading is the common case, so it is answered the way the layer answers it everywhere else:
    with every form the reading can be written as, in `forms`' order, so asking for ね returns the
    modern kana first and the hentaigana of the same 音価 with it. A 字母 is answered with the
    characters that derive from it, because that is the relation the layer records; the 字母 itself
    comes first when it is a character of the table.

    `like` adds a case-insensitive substring match over the names, for a caller searching a word of a
    name rather than the whole of it. A term that is entirely ASCII letters and spaces is treated as
    a name as well, since no reading or character is written that way, so
    `search("KATAKANA LETTER ALTERNATE NE")` finds 𛄧 without the caller using `like`.

    A query the layer does not hold returns `[]`, as `character` answers `None` rather than raising.
    `limit` cuts the answer after it is ordered, so the likeliest forms survive the cut.
    """
    term = term.strip()
    if not term and not like:
        return []
    found: list[str] = []

    def keep(code_point: str | None) -> None:
        if code_point and code_point not in found:
            found.append(code_point)

    if term:
        row = character(term)
        if row is None and len(term) == 1:
            # `character` reads a code point, and a reviewer types the character itself, so the
            # character is resolved here rather than answering nothing for the commonest query.
            row = character(to_code_point(term))
        keep(row.code_point if row else None)
        if len(term) == 1:
            for code_point in forms(term):
                keep(code_point)
        for code_point in jibo_of(term) or []:
            keep(code_point)
        for other in _by_jibo().get(term, ()):
            keep(other)
        for code_point in _by_reading().get(term, ()):
            keep(code_point)
        if len(term) > 1 and not _looks_like_a_name(term):
            # A word of kana reads as its characters: the layer records 音価 per character, so ねこ
            # is answered by ね and こ rather than by a row that spells the word. Each character of
            # the word leads with the form searched for and is followed by the other forms of it, so
            # the word a reviewer typed comes back before the variants of its first character.
            for character_here in term:
                keep(to_code_point(character_here))
                for code_point in forms(character_here):
                    keep(code_point)
            # A term that spells a ligature's two characters or its reading is asking for the
            # ligature: 𪜈 is what トモ is written as, and ゟ and 𛄦 are both より. The components
            # alone are not answered this way, so a search for ヨ stays a search for ヨ.
            for code_point in _by_ligature().get(term, ()):
                keep(code_point)

    needles = [value.strip().lower() for value in (like, term) if value and value.strip()]
    if _looks_like_a_name(term) or like:
        for needle in needles:
            if not needle:
                continue
            for other in sorted(_characters().values(), key=lambda item: item.code_point):
                if needle in (other.name or "").lower():
                    keep(other.code_point)

    ordered = [row for code_point in found if (row := character(code_point)) is not None]
    return ordered[:limit] if limit else ordered


def _looks_like_a_name(term: str) -> bool:
    """Whether a query can only be a Unicode name: ASCII letters and spaces, and more than one word."""
    stripped = term.strip()
    return " " in stripped and all(character.isascii() for character in stripped)


@cache
def _by_ligature() -> dict[str, list[str]]:
    """The two characters of a ligature written together, or its reading -> the ligature.

    A reader asks for トモ and means 𪜈, and the layer states the pair in `ligatures.yaml` rather
    than in the Unicode name, so this is the way in. Each key is also indexed through hiragana,
    because katakana maps through hiragana everywhere else in this module, so ヨリ and より both
    reach ゟ and 𛄦.
    """
    index: dict[str, list[str]] = {}
    for code_point, row in sorted(_ligature_rows().items()):
        keys = {from_code_points(row.components)}
        if row.reading:
            keys.add(row.reading)
        for key in sorted(keys | {to_hiragana(key) for key in keys}):
            index.setdefault(key, [])
            if code_point not in index[key]:
                index[key].append(code_point)
    return index


@cache
def _by_reading() -> dict[str, list[str]]:
    """reading -> the code points that read as it, in code point order."""
    index: dict[str, list[str]] = {}
    for row in sorted(_characters().values(), key=lambda item: item.code_point):
        for reading in row.readings:
            index.setdefault(reading, []).append(row.code_point)
    return index


@cache
def _by_jibo() -> dict[str, list[str]]:
    """字母 -> the code points derived from it, in code point order.

    The layer records the relation one way, from a kana to the kanji it derives from, and a reviewer
    searching 子 is asking the other way: which kana are written as 子.
    """
    index: dict[str, list[str]] = {}
    for row in sorted(_characters().values(), key=lambda item: item.code_point):
        for letter in row.jibo:
            index.setdefault(letter, []).append(row.code_point)
    return index


@cache
def _kana_by_reading() -> dict[str, list[str]]:
    """reading -> the code points the character layer says read as it, in code point order."""
    by_reading: dict[str, list[str]] = {}
    for row in _character_rows():
        for reading in _split(row["readings"]):
            by_reading.setdefault(to_hiragana(reading), []).append(row["code_point"])
    return {reading: sorted(points) for reading, points in by_reading.items()}


@cache
def _written_forms() -> dict[str, list[str]]:
    """reading -> the kana forms of it that `_reading_forms` does not reach, in code point order.

    What is left after the hentaigana are the kana Unicode assigns and the hentaigana tables do not
    figure: the six letters of Unicode 18.0, which are katakana, and the plain katakana letters,
    which no table lists because a reader does not need them listed. The historical forms come
    first, so that the last candidates are the ones a transcriber is least likely to have written.
    """
    tabled = {row["code_point"] for row in _hentaigana_rows()}
    forms: dict[str, list[str]] = {}
    for reading, points in _kana_by_reading().items():
        extra = [point for point in points if point not in tabled and not point.startswith("U+30")]
        plain = [point for point in points if point.startswith("U+30")]
        if extra or plain:
            forms[reading] = extra + plain
    return forms


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
