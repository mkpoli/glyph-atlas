"""Build `apps/review/static/palette/ids-parts.json`, the parts palette of the IDS editor (難輸入部件).

A part is a component that the sequences of `data/vocab/han-ids.tsv` (BabelStone IDS) use and that
a writer picks whole. That is one of:
- a component hfhchan/ids leaves undecomposed (its entry has no description operator and names the
  character itself, or a variant that is atomic in turn);
- one BabelStone gives no sequence with an operator whose components are all known (⿻？乚 leaves 也
  a part);
- one whose every such sequence is strokes only, or strokes and one other component, a stroke being a
  CJK stroke or a one-stroke ideograph (𠂉 ⿰㇒一, 𠂇 ⿻一丿, 䒑 ⿱丷一);
- one at least `USE_THRESHOLD` (50) ideographs use, whatever its sequence (殳 門 髟 齒 䖵).
Each part counts the ideographs whose sequences use it, leaving out the sequences of ideographs that
hfhchan/ids keeps whole. Every character of the CJK Strokes block (U+31C0–U+31E5) is a part, used or
not; CJK Radicals Supplement characters are parts when used. Operators, ？ and BabelStone's unencoded
components {n} are not parts.

Each part carries:
- `strokes`: Unihan's kTotalStrokes (its first value, which Unihan gives for the zh-Hans form where
  the forms differ). A part Unihan lacks gets the sum over the leaves of its first compound sequence
  whose leaves all have counts, marked derived; a CJK stroke counts 1 and is marked derived; anything
  else stays null.
- `first`: the class of its first stroke, 横 豎 撇 點 or 折, where the KanjiVG witnesses agree: the
  character's own entry, every group drawing it whole (`kvg:element`, not marked part, partial or
  variant), and every character whose sequence starts with it under ⿰ ⿱ ⿸. The class is kept when
  at least 3 witnesses have a class and at least 90% of them agree (`MIN_WITNESSES`,
  `MIN_AGREEMENT`); 𠂇 starts with 一 in 左 友 and with 丿 in 右 有 布, so it has none. A CJK stroke
  is classed as itself. A bare stroke type maps to the five classes by the table `CLASS` (an
  editorial table, after the five classes Chinese dictionaries order strokes by: 提 counts as 横, 豎鉤
  as 豎, 捺 and 提捺 as 點, every turning stroke as 折, and ㇣ as none). A suffixed type (㇑a, which
  KanjiVG gives to the dot of 亠 and the vertical of 山 alike), an alternative whose sides disagree
  (㇔/㇑), or a type outside the table is no witness. KanjiVG follows Japanese stroke order.
- `kind`: s for a CJK stroke; c (common) for a part that is a 常用漢字 (Unihan kJoyoKanji, with the
  form a U+XXXX value points to) or in the first level of the 通用规范汉字表 (Unihan kTGH, positions
  1–3500); d (difficult) for the rest. The palette's Common, Difficult and Strokes tabs list them.

    uv run python scripts/build_ids_parts.py          # write ids-parts.json
    uv run python scripts/build_ids_parts.py --font   # and the webfont subset

`--font` subsets Noto Sans CJK JP for the CJK strokes and Plangothic for the rest (both SIL OFL
1.1) to the parts few installed fonts draw: CJK strokes, CJK Radicals Supplement and the
supplementary planes (Extensions B–J). It writes `NotoSansCJKjp-ids-strokes.woff2` and
`Plangothic-ids-parts.woff2` under `apps/review/static/fonts/`. The rest of the Basic Multilingual
Plane, Extension A included, comes from the reader's own CJK font, as for the vi-Hani interface font.

Unihan, hfhchan/ids and KanjiVG are fetched once into `cache/` and checked against pinned SHA-256s.
The `first` field adapts KanjiVG's stroke data and is shared alike under CC BY-SA 3.0.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab"
TARGET = ROOT / "apps" / "review" / "static" / "palette" / "ids-parts.json"
FONT_DIR = ROOT / "apps" / "review" / "static" / "fonts"
PLANGOTHIC_URL = (
    "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project/releases/download/V2.9.5795"
)
# Noto Sans CJK JP (at weight 400) draws the CJK strokes up to U+31E3, which Plangothic lacks;
# Plangothic draws the rest.
FONTS = {
    "PlangothicP1-Regular.ttf": (
        f"{PLANGOTHIC_URL}/PlangothicP1-Regular.ttf",
        "550b5d0775b15405946b18f4843df439a51e69508d7e6778d94c1f7a53dc5ad6",
    ),
    "PlangothicP2-Regular.ttf": (
        f"{PLANGOTHIC_URL}/PlangothicP2-Regular.ttf",
        "681933370adfe0fc7253f77735275a82fea09fe4f8adba907bdeb46c110daf8f",
    ),
    "NotoSansCJKjp-VF-2.004.ttf": (
        "https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf",
        "240c9b83bf7b386edbae39995ae7e068ed4583f484d92e4a74c34158b5f27b1a",
    ),
}
# Each file, the fonts it is cut from and the code points it takes. The ranges do not overlap, so the
# page names them in one @font-face rule each.
FONT_FILES = {
    "NotoSansCJKjp-ids-strokes.woff2": (["NotoSansCJKjp-VF-2.004.ttf"], [(0x31C0, 0x31E3)]),
    "Plangothic-ids-parts.woff2": (
        ["PlangothicP1-Regular.ttf", "PlangothicP2-Regular.ttf"],
        [(0x2E80, 0x2EFF), (0x31E4, 0x31EF), (0x20000, 0x3FFFF)],
    ),
}

UNICODE_VERSION = "18.0.0"
UNIHAN_URL = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/Unihan.zip"
UNIHAN_SHA256 = "4c93ea9c1f636451729a840978f1667a53886af37ba854fdcce109721c63d43e"
HFHCHAN_URL = "https://github.com/hfhchan/ids/releases/download/v1.0.2/ids-20240112.txt"
HFHCHAN_SHA256 = "7466c8bd9151f1db5ef247fcbf84d7de4b36a6ec741e793c54972aa3dcbc0af4"
KANJIVG_URL = "https://github.com/KanjiVG/kanjivg/releases/download/r20260714/kanjivg-20260714.xml.gz"
KANJIVG_SHA256 = "d4a8e5e11533fe6a5d81a158b2c153964322641d2982c90a919461f3c06dca6b"

# Binary and ternary description characters, and the unary ones (〾 ⿾ ⿿); ㇯ subtracts.
OPERATORS = set("⿰⿱⿲⿳⿴⿵⿶⿷⿸⿹⿺⿻⿼⿽⿾⿿㇯〾")
UNKNOWN = re.compile(r"\{\d+\}|？")
REGIONS = re.compile(r"\([^)]*\)$")
KVG = "{http://kanjivg.tagaini.net}"
TGH_FIRST_LEVEL = 3500

# The CJK Strokes block, U+31C0–U+31E5, by the abbreviations of its Unicode 18.0 names (CJK STROKE
# <letters>), written out so the output does not depend on the Unicode version Python carries.
STROKE_NAMES = [
    "T",
    "WG",
    "XG",
    "BXG",
    "SW",
    "HZZ",
    "HZG",
    "HP",
    "HZWG",
    "SZWG",
    "HZT",
    "HZZP",
    "HPWG",
    "HZW",
    "HZZZ",
    "N",
    "H",
    "S",
    "P",
    "SP",
    "D",
    "HZ",
    "HG",
    "SZ",
    "SWZ",
    "ST",
    "SG",
    "PD",
    "PZ",
    "TN",
    "SZZ",
    "SWG",
    "HXWG",
    "HZZZG",
    "PG",
    "Q",
    "HXG",
    "SZP",
]
STROKES = [chr(0x31C0 + offset) for offset in range(len(STROKE_NAMES))]
# Stroke → class: the strokes named here are 横, 豎, 撇 or 點 (㇣ Q is none); every other stroke is 折.
_NAMED = {
    "H": "横",
    "T": "横",
    "S": "豎",
    "SG": "豎",
    "P": "撇",
    "SP": "撇",
    "D": "點",
    "N": "點",
    "TN": "點",
    "Q": None,
}
CLASS = {
    stroke: _NAMED.get(name, "折")
    for stroke, name in zip(STROKES, STROKE_NAMES, strict=True)
    if _NAMED.get(name, "折")
}
# Parts that BabelStone splits into strokes still count as parts when each of their sequences is
# strokes only, or strokes and one other component; every component this many ideographs use counts
# as a part as well.
USE_THRESHOLD = 50


def type_class(kind: str) -> str | None:
    """The class of a KanjiVG `kvg:type`, when every side of it is a bare stroke of one class.

    A type with a letter suffix (㇑a, ㇐b, ㇕c) names a variant shape, and KanjiVG gives the same
    suffixed type to strokes of different classes: ㇑a is the top dot of 亠 宀 广 as well as the
    verticals of 山 止 出. Such a type, an alternative whose sides disagree (㇔/㇑), or a type outside
    the table gives None.
    """
    sides = kind.split("/")
    if any(len(side) != 1 for side in sides):
        return None
    found = {CLASS.get(side) for side in sides}
    return found.pop() if len(found) == 1 else None


def components(ids: str) -> list[str]:
    """The components a sequence names, region tags, operators and unknown components dropped."""
    ids = UNKNOWN.sub("", REGIONS.sub("", ids))
    return [char for char in ids if char not in OPERATORS]


def read_ids(path: Path) -> dict[str, list[str]]:
    """char → its sequences (region tags dropped), from `han-ids.tsv`."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", "code_point\t")):
            continue
        _, char, sequences = line.split("\t")
        out[char] = [REGIONS.sub("", sequence) for sequence in sequences.split(" ")]
    return out


def hfhchan_atomic(text: str) -> set[str]:
    """The characters hfhchan/ids leaves undecomposed, by the rule of han-compose's han-atomic table."""
    table = {}
    for line in text.splitlines():
        fields = line.rstrip("\r").split("\t")
        if len(fields) >= 3 and fields[1] and not line.startswith("#"):
            table[fields[1]] = fields[2].split("[")[0].strip()

    def whole(char: str, seen: tuple[str, ...] = ()) -> bool:
        entry = table.get(char)
        if entry is None or any(ch in OPERATORS for ch in entry):
            return False
        if entry == char:
            return True
        return len(entry) == 1 and entry not in seen and whole(entry, (*seen, char))

    return {char for char in table if whole(char)}


def compound(sequence: str) -> bool:
    return any(char in OPERATORS for char in sequence)


def parts(ids: dict[str, list[str]], atomic: set[str], single: set[str]) -> Counter:
    """Each part → how many ideographs' sequences use it (once per ideograph).

    `single` holds the one-stroke ideographs (一 丨 丿 丶 乙 亅 …), which count as strokes when a
    sequence is read for whether it is made of strokes.
    """
    uses: Counter = Counter()
    for char, sequences in ids.items():
        if char in atomic:
            continue
        named = {part for sequence in sequences if compound(sequence) for part in components(sequence)}
        named.discard(char)
        uses.update(named)
    stroke = set(STROKES) | single

    def known(part: str) -> list[str]:
        return [
            sequence for sequence in ids.get(part, []) if compound(sequence) and not UNKNOWN.search(sequence)
        ]

    def of_strokes(sequence: str) -> bool:
        leaves = components(sequence)
        return sum(leaf not in stroke for leaf in leaves) <= 1 and any(leaf in stroke for leaf in leaves)

    def kept(part: str) -> bool:
        sequences = known(part)
        return (
            part in atomic
            or not sequences
            or all(of_strokes(sequence) for sequence in sequences)
            or uses[part] >= USE_THRESHOLD
        )

    return Counter({part: count for part, count in uses.items() if kept(part)})


def unihan_fields(path: Path) -> dict[str, dict[str, str]]:
    wanted = {"kTotalStrokes", "kJoyoKanji", "kTGH"}
    found: dict[str, dict[str, str]] = defaultdict(dict)
    with zipfile.ZipFile(path) as archive:
        for name in ("Unihan_IRGSources.txt", "Unihan_OtherMappings.txt"):
            for line in archive.read(name).decode("utf-8").splitlines():
                cells = line.split("\t")
                if len(cells) == 3 and cells[1] in wanted:
                    found[cells[1]][chr(int(cells[0].removeprefix("U+"), 16))] = cells[2]
    return found


def common_set(unihan: dict[str, dict[str, str]]) -> set[str]:
    """常用漢字 and the first level of the 通用规范汉字表."""
    first_level = {
        char
        for char, value in unihan["kTGH"].items()
        if any(int(entry.split(":")[1]) <= TGH_FIRST_LEVEL for entry in value.split())
    }
    # A kJoyoKanji value of U+XXXX names the code point of the form the 常用漢字表 prints (剥 → 剝);
    # both are counted as common.
    joyo = set(unihan["kJoyoKanji"]) | {
        chr(int(value.removeprefix("U+"), 16))
        for value in unihan["kJoyoKanji"].values()
        if value.startswith("U+")
    }
    return joyo | first_level


def stroke_counts(
    names: list[str], ids: dict[str, list[str]], total: dict[str, str]
) -> dict[str, tuple[int | None, bool]]:
    """part → (stroke count, derived), from Unihan, else from its own first compound sequence."""
    unihan = {char: int(value.split()[0]) for char, value in total.items()}

    def count(char: str, seen: frozenset = frozenset()) -> tuple[int | None, bool]:
        if char in unihan:
            return unihan[char], False
        if char in STROKES:
            return 1, True
        if char in seen:
            return None, False
        # The first sequence whose leaves all have counts; a later one is tried when an earlier fails.
        for sequence in ids.get(char, []):
            if compound(sequence) and not UNKNOWN.search(sequence):
                leaves = [count(leaf, seen | {char})[0] for leaf in components(sequence)]
                if leaves and all(value is not None for value in leaves):
                    return sum(leaves), True
        return None, False

    return {name: count(name) for name in names}


# Operators whose first operand is written first in the characters they describe (⿺ is not: 辶
# comes last), so a character spelled ⿸𠂇X starts as its 𠂇 does.
LEADING = set("⿰⿱⿸")
# A part keeps a first-stroke class when at least this many witnesses have a bare stroke type and at
# least this share of them agree on one class; otherwise it has none.
MIN_WITNESSES = 3
MIN_AGREEMENT = 0.9


def kanjivg_witnesses(path: Path, ids: dict[str, list[str]]) -> dict[str, list[str | None]]:
    """char → the first-stroke classes of its KanjiVG witnesses, None where a witness has none.

    A component's first stroke can depend on the character it stands in: 𠂇 writes 一 first in 左 and
    友 but 丿 first in 右 有 布. The witnesses are the character's own entry, every group drawing it
    whole, and every character with an entry whose sequence starts with it (⿰ ⿱ ⿸ and the component
    as the first operand), whose own first stroke is then the component's. KanjiVG names no 𠂇 group
    in 右, so only these characters show that 𠂇 starts two ways. A class is kept when at least
    `MIN_WITNESSES` witnesses have one and at least `MIN_AGREEMENT` of them agree (`agreement`), so a
    few odd groups do not clear 口's 豎, while 𠂇, split between 一 and 丿, has none. A
    witness whose first stroke has no class (a suffixed type such as ㇑a) is left out, and so is a
    group KanjiVG marks as drawing part of the component or a variant shape of it.
    """
    own: dict[str, str | None] = {}
    witnesses: dict[str, list[str | None]] = defaultdict(list)

    def first_type(node) -> str | None:
        for path_node in node.iter("path"):
            return path_node.get(f"{KVG}type")
        return None

    def cls(node) -> str | None:
        kind = first_type(node)
        return type_class(kind) if kind else None

    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    for kanji in root.iter("kanji"):
        hexcode = kanji.get("id", "").removeprefix("kvg:kanji_")
        if not re.fullmatch(r"[0-9a-f]{5}", hexcode):
            continue
        char = chr(int(hexcode, 16))
        own[char] = cls(kanji)
        witnesses[char].append(own[char])
        for group in kanji.iter("g"):
            element = group.get(f"{KVG}element")
            # A group that draws only some of the component (kvg:part, kvg:partial) or a variant shape
            # of it (kvg:variant) says nothing of how the component itself starts.
            whole = all(group.get(f"{KVG}{name}") is None for name in ("part", "partial", "variant"))
            if element and len(element) == 1 and whole and group is not kanji:
                witnesses[element].append(cls(group))
    for char, sequences in ids.items():
        if char not in own:
            continue
        for sequence in sequences:
            if (
                len(sequence) > 1
                and sequence[0] in LEADING
                and sequence[1] not in OPERATORS
                and sequence[1] != char
            ):
                witnesses[sequence[1]].append(own[char])
    return witnesses


def kanjivg_first(path: Path, ids: dict[str, list[str]]) -> dict[str, str]:
    """char → class of its first stroke, where its KanjiVG witnesses agree enough (`agreement`)."""
    return {char: cls for char, (cls, _, _) in agreement(kanjivg_witnesses(path, ids)).items() if cls}


def agreement(witnesses: dict[str, list[str | None]]) -> dict[str, tuple[str | None, int, int]]:
    """char → (class or None, witnesses agreeing on the most common class, witnesses with a class)."""
    out = {}
    for char, found in witnesses.items():
        votes = Counter(value for value in found if value)
        total = sum(votes.values())
        if not total:
            out[char] = (None, 0, 0)
            continue
        cls, agreeing = votes.most_common(1)[0]
        kept = total >= MIN_WITNESSES and agreeing / total >= MIN_AGREEMENT
        out[char] = (cls if kept else None, agreeing, total)
    return out


def palette(
    ids: dict[str, list[str]], atomic: set[str], unihan: dict[str, dict[str, str]], first: dict[str, str]
) -> list[list]:
    """Rows `[part, uses, strokes, derived, first, kind]`, most used first; kind is c(ommon),
    d(ifficult) or s(troke)."""
    single = {char for char, value in unihan["kTotalStrokes"].items() if value.split()[0] == "1"}
    uses = parts(ids, atomic, single)
    strokes = STROKES
    names = sorted(set(uses) | set(strokes), key=lambda char: (-uses.get(char, 0), ord(char)))
    counts = stroke_counts(names, ids, unihan["kTotalStrokes"])
    common = common_set(unihan)
    rows = []
    for name in names:
        count, derived = counts[name]
        kind = "s" if name in strokes else "c" if name in common else "d"
        cls = CLASS.get(name) if kind == "s" else first.get(name)
        rows.append([name, uses.get(name, 0), count, 1 if derived else 0, cls, kind])
    return rows


def needs_font(char: str) -> bool:
    point = ord(char)
    return 0x2E80 <= point <= 0x2EFF or 0x31C0 <= point <= 0x31EF or point >= 0x20000


def subset_piece(source: Path, codes: list[int], path: Path) -> Path:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    options = subset.Options()
    options.drop_tables += ["FFTM"]
    options.layout_features, options.name_IDs, options.notdef_outline = [], ["*"], True
    piece = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codes)
    subsetter.subset(piece)
    if "fvar" in piece:
        piece = instancer.instantiateVariableFont(piece, {"wght": 400})
    head = TTFont(source)["head"]
    piece["head"].created, piece["head"].modified = head.created, head.modified
    piece.recalcTimestamp = False
    path.parent.mkdir(parents=True, exist_ok=True)
    piece.save(path)
    return path


def font(rows: list[list], cache: Path, refresh: bool, out: Path = FONT_DIR) -> None:
    """Subset Noto Sans CJK JP and Plangothic P1 and P2 (merged) to the parts `needs_font` names, one
    woff2 per family, so a page that shows no stroke never loads the strokes."""
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont

    wanted = sorted({ord(row[0]) for row in rows if needs_font(row[0])})
    covered: set[int] = set()
    for target, (names, ranges) in FONT_FILES.items():
        pieces = []
        for name in names:
            url, sha256 = FONTS[name]
            source = fetch(url, cache / "fonts" / name, sha256, refresh)
            cmap = TTFont(source).getBestCmap()
            codes = [
                code
                for code in wanted
                if code in cmap
                and code not in covered
                and any(first <= code <= last for first, last in ranges)
            ]
            if codes:
                covered.update(codes)
                pieces.append(str(subset_piece(source, codes, cache / "ids-parts" / f"part-{name}")))
        merged = Merger().merge(pieces) if len(pieces) > 1 else TTFont(pieces[0])
        head = TTFont(pieces[0])["head"]
        merged["head"].created, merged["head"].modified = head.created, head.modified
        merged.recalcTimestamp = False
        merged.flavor = "woff2"
        merged.save(out / target)
        print(
            f"{out / target}: {len(merged.getBestCmap())} characters, {(out / target).stat().st_size} bytes"
        )
    lacking = "".join(chr(code) for code in wanted if code not in covered)
    print(f"{len(covered)} of {len(wanted)} parts drawn; the fonts lack {lacking or 'none'}")


def log_agreement(rows: list[list], agreed: dict[str, tuple[str | None, int, int]]) -> None:
    """Print how the first-stroke witnesses of the parts agreed: the agreement stays in the log, since
    a column of it would add over 2 KB to the gzipped palette."""
    found = [(row[0], *agreed.get(row[0], (None, 0, 0))) for row in rows if row[5] != "s"]
    kept = [part for part in found if part[1]]
    few = [part for part in found if 0 < part[3] < MIN_WITNESSES]
    split = [part for part in found if part[3] >= MIN_WITNESSES and not part[1]]
    print(
        f"first strokes: {len(kept)} kept, {len(few)} with fewer than {MIN_WITNESSES} witnesses, "
        f"{len(split)} below {MIN_AGREEMENT:.0%} agreement, {len(found) - len(kept) - len(few) - len(split)} unwitnessed"
    )
    print(
        "  below agreement: " + " ".join(f"{part}({agreeing}/{total})" for part, _, agreeing, total in split)
    )
    dissent = [part for part in kept if part[2] < part[3]]
    print(
        "  kept with dissent: "
        + " ".join(f"{part}{cls}({agreeing}/{total})" for part, cls, agreeing, total in dissent)
    )


def fetch(url: str, dest: Path, sha256: str, refresh: bool) -> Path:
    from glyph_atlas.net import download

    path = download(url, dest, refresh=refresh, timeout=300)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError(f"{path} does not match the pinned SHA-256 of {url}.")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    parser.add_argument("--font", action="store_true", help="also write the webfont subset")
    args = parser.parse_args(argv)
    unihan_path = fetch(
        UNIHAN_URL, args.cache / "variants" / UNICODE_VERSION / "Unihan.zip", UNIHAN_SHA256, args.refresh
    )
    hfhchan_path = fetch(
        HFHCHAN_URL, args.cache / "components" / "hfhchan-ids-20240112.txt", HFHCHAN_SHA256, args.refresh
    )
    kanjivg_path = fetch(
        KANJIVG_URL, args.cache / "fonts" / "kanjivg-20260714.xml.gz", KANJIVG_SHA256, args.refresh
    )

    ids = read_ids(VOCAB / "han-ids.tsv")
    agreed = agreement(kanjivg_witnesses(kanjivg_path, ids))
    rows = palette(
        ids,
        hfhchan_atomic(hfhchan_path.read_text(encoding="utf-8")),
        unihan_fields(unihan_path),
        {char: cls for char, (cls, _, _) in agreed.items() if cls},
    )
    document = {
        "columns": ["part", "uses", "strokes", "derived", "first", "kind"],
        "common": f"常用漢字 (Unihan kJoyoKanji) or 通用规范汉字表 level 1 (Unihan kTGH ≤ {TGH_FIRST_LEVEL})",
        "sources": [
            "BabelStone IDS.TXT (data/vocab/han-ids.tsv), PD, Andrew West",
            f"hfhchan/ids v1.0.2, MIT, Copyright (c) 2021 Henry Chan, {HFHCHAN_URL}",
            f"Unihan {UNICODE_VERSION}, Unicode License v3, Copyright Unicode, Inc., {UNIHAN_URL}",
            f"KanjiVG r20260714, CC BY-SA 3.0, Copyright (C) 2009-2013 Ulrich Apel, {KANJIVG_URL}; the first column adapts it and is shared alike under CC BY-SA 3.0",
        ],
        "parts": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    args.out.write_text(text, encoding="utf-8")
    kinds = Counter(row[5] for row in rows)
    size = len(text.encode())
    packed = len(gzip.compress(text.encode(), 9))
    print(f"{args.out}: {len(rows)} parts ({dict(kinds)}), {size} bytes, {packed} gzipped")
    log_agreement(rows, agreed)
    if args.font:
        font(rows, args.cache, args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
