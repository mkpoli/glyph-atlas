"""Build data/vocab/kanji-variants.tsv, the 異体字 graph: one row per variant edge per source.

The table keeps every source's own claim side by side and merges nothing. A consumer picks the
relations and sources it trusts and builds its groups from those; chaining every row together would
join characters no source relates (yitizi's 閒~閑 and 閒~間 do not make 閑 a variant of 間).

Relations. Symmetric ones are stored with `a` before `b` in code point order; for a directed one the
row reads "b is the <relation> of a".

`equivalent`            interchangeable in most texts (yitizi 全等異體)
`overlap`               interchangeable in some senses only (yitizi 語義交疊, one row per pair of a group)
`semantic`              Unihan kSemanticVariant
`specialized-semantic`  directed: b is a variant of a in some senses (Unihan kSpecializedSemanticVariant,
                        Wikidata P5475 with 特徴 Q126726325)
`variant`               a variant with no finer kind (Wikidata P5475, HNG 異体字 column)
`z`                     one abstract character encoded twice (Unihan kZVariant)
`simplified`            directed: b is a simplified form of a (Unihan kSimplifiedVariant, and
                        kTraditionalVariant read backwards; OpenCC TSCharacters; yitizi 簡體)
`shinjitai`             directed: b is the Japanese 新字体 of a (Unihan kJapaneseNewVariant, and
                        kJapaneseOldVariant read backwards; OpenCC JPShinjitaiCharacters)
`regional`              directed: b is the form a regional standard writes for a (OpenCC TW/HKVariants)
`shuowen`               directed: b is the 說文解字 隸定字 of a (OpenCC SealVariants)
`reduction`             directed: an MJ figure of a reduces to b (MJ縮退マップ; detail names the table)
`compatibility`         directed: a is a compatibility character whose decomposition is b (UnicodeData)
`spoofing`              visually confusable, not the same character (Unihan kSpoofingVariant)

    uv sync --extra data
    .venv/bin/python scripts/build_kanji_variants.py

Network access happens here and nowhere else; downloads are cached under `cache/variants/` and
`cache/ucd/`. The Wikidata query result is not versioned upstream, so its download date and digest
go into the table header; `--refresh` fetches everything again.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import yaml

from glyph_atlas.net import download

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "vocab" / "kanji-variants.tsv"
SOURCES = ROOT / "data" / "sources"
MJ_KANJI = ROOT / "data" / "vocab" / "mj-kanji.tsv"

UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
UNICODEDATA_URL = "https://www.unicode.org/Public/UCD/latest/ucd/UnicodeData.txt"
OPENCC_REVISION = "2939943bd6f4d459b46d7fdcf07a885ab3f01761"
OPENCC_URL = "https://raw.githubusercontent.com/BYVoid/OpenCC/{revision}/data/dictionary/{name}"
YITIZI_REVISION = "60e232c40d6076af07679151c008349363e699ae"
YITIZI_URL = "https://raw.githubusercontent.com/nk2028/yitizi/{revision}/data/{name}"
SHRINK_MAP_URL = "https://moji.or.jp/wp-content/mojikiban/oscdl/MJShrinkMap.1.2.0.json"
HNG_REVISION = "e2174a30844b8100c34af1c0dbe1e301f186883e"
HNG_URL = "https://raw.githubusercontent.com/chise/hng-basic-data/{revision}/{name}"
HNG_INDEX = "all_table_v.5.0_2019-01-15.csv"
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
#: P5475 CJKV variant character, with the character of each item (P487), the works the statement
#: cites (P248 under its references), 特徴 (P1552) and writing system (P282).
WIKIDATA_QUERY = """\
SELECT ?s ?ac ?bc
  (GROUP_CONCAT(DISTINCT STRAFTER(STR(?ref), "entity/"); separator=" ") AS ?stated)
  (GROUP_CONCAT(DISTINCT STRAFTER(STR(?kind), "entity/"); separator=" ") AS ?kinds)
  (GROUP_CONCAT(DISTINCT STRAFTER(STR(?ws), "entity/"); separator=" ") AS ?systems)
WHERE {
  ?a p:P5475 ?s . ?s ps:P5475 ?b .
  ?a wdt:P487 ?ac . ?b wdt:P487 ?bc .
  OPTIONAL { ?s prov:wasDerivedFrom/pr:P248 ?ref }
  OPTIONAL { ?s pq:P1552 ?kind }
  OPTIONAL { ?s pq:P282 ?ws }
}
GROUP BY ?s ?ac ?bc
"""
SPECIALIZED_SEMANTIC = "Q126726325"

#: relation, region, and whether the file maps the other way round (JPShinjitaiCharacters is the
#: jp2t table: 新字体 key, 旧字体 values).
OPENCC_FILES = {
    "TSCharacters.txt": ("simplified", None, False),
    "JPShinjitaiCharacters.txt": ("shinjitai", None, True),
    "TWVariants.txt": ("regional", "TW", False),
    "HKVariants.txt": ("regional", "HK", False),
    "SealVariants.txt": ("shuowen", None, False),
}
YITIZI_FILES = ["ytenx/JihThex.csv", "ytenx/ThaJihThex.csv", "yitizi.txt"]
#: yitizi's corrections to the 韻典網 tables, copied from build/build_lib/loaders.py at
#: YITIZI_REVISION (CC0): characters whose ytenx relations it drops, and 全等 pairs it drops.
YITIZI_EXCLUDED_CHARS = set("苎蒙懞矇朦芸弁皝徠倈艫舻舮")
YITIZI_EXCLUDED_PAIRS = {("瀋", "沉"), ("干", "乾"), ("榦", "乾"), ("搋", "弌"), ("坯", "壊"), ("坯", "壞")}

UNIHAN_FIELDS = {
    "kSemanticVariant": ("semantic", False),
    "kSpecializedSemanticVariant": ("specialized-semantic", False),
    "kZVariant": ("z", False),
    "kSimplifiedVariant": ("simplified", False),
    "kTraditionalVariant": ("simplified", True),
    "kJapaneseNewVariant": ("shinjitai", False),
    "kJapaneseOldVariant": ("shinjitai", True),
    "kSpoofingVariant": ("spoofing", False),
}
SYMMETRIC = {"equivalent", "overlap", "semantic", "variant", "z", "spoofing"}
RELATIONS = [
    "equivalent",
    "overlap",
    "semantic",
    "specialized-semantic",
    "variant",
    "z",
    "simplified",
    "shinjitai",
    "regional",
    "shuowen",
    "reduction",
    "compatibility",
    "spoofing",
]
SOURCE_IDS = ["unihan", "unicode-ucd", "wikidata", "yitizi", "opencc", "mj-shrink-map", "hng-basic-data"]
COLUMNS = ["a", "b", "relation", "source", "detail"]
HEX = re.compile(r"U\+([0-9A-F]{4,6})")


def scalar(code_point: str) -> str:
    """`"U+4EFF"` -> `"仿"`."""
    return chr(int(code_point.removeprefix("U+"), 16))


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    relation: str
    source: str
    detail: str = ""


def edge(a: str, b: str, relation: str, source: str, detail: str = "") -> Edge | None:
    """One row, or None for a self-loop or a value that is not a single character."""
    if len(a) != 1 or len(b) != 1 or a == b:
        return None
    if relation in SYMMETRIC and b < a:
        a, b = b, a
    return Edge(a, b, relation, source, detail)


def unihan_edges(text: str) -> list[Edge]:
    """Unihan_Variants.txt lines `U+XXXX<TAB>field<TAB>U+YYYY<source,... U+ZZZZ`.

    The detail keeps the line's own direction (`圩→墟 kSemanticVariant<kPhonetic:F`), since a tag
    such as `:Z` or `kPhonetic:F` describes one endpoint and a symmetric row may be stored either way.
    """
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        code_point, field, values = line.split("\t")
        if field not in UNIHAN_FIELDS:
            continue
        relation, backwards = UNIHAN_FIELDS[field]
        subject = scalar(code_point)
        for value in values.split():
            target, _, tags = value.partition("<")
            other = scalar(target)
            detail = f"{subject}→{other} {field}" + (f"<{tags}" if tags else "")
            rows.append(
                edge(other, subject, relation, "unihan", detail)
                if backwards
                else edge(subject, other, relation, "unihan", detail)
            )
    return [row for row in rows if row]


def compatibility_edges(text: str) -> list[Edge]:
    """A compatibility character and the one CJK unified ideograph its decomposition names."""
    rows = []
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) < 6 or not fields[5]:
            continue
        parts = fields[5].split()
        if fields[1].startswith("CJK COMPATIBILITY IDEOGRAPH-") and len(parts) == 1:
            target = parts[0]
        elif parts[0] == "<compat>" and len(parts) == 2:
            target = parts[1]
        else:
            continue
        if not re.fullmatch(r"[0-9A-F]{4,6}", target) or not is_unified_ideograph(int(target, 16)):
            continue
        rows.append(
            edge(chr(int(fields[0], 16)), chr(int(target, 16)), "compatibility", "unicode-ucd", fields[5])
        )
    return [row for row in rows if row]


def is_unified_ideograph(code_point: int) -> bool:
    return (
        0x3400 <= code_point <= 0x4DBF or 0x4E00 <= code_point <= 0x9FFF or 0x20000 <= code_point <= 0x323AF
    )


def opencc_edges(name: str, text: str) -> list[Edge]:
    """`key<TAB>value value…`, each key a single character; comment lines start with `#`.

    A key whose first candidate is itself keeps its other candidates; in JPShinjitaiCharacters such
    a line is a historical entry outside 常用漢字表, 人名用漢字表 and 表外漢字字体表 (the file's
    header), and its rows say so.
    """
    relation, region, backwards = OPENCC_FILES[name]
    base = name.removesuffix(".txt") + (f" {region}" if region else "")
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        key, values = line.split("\t", 1)
        candidates = values.split()
        detail = base + (" (self first)" if candidates and candidates[0] == key else "")
        for value in candidates:
            rows.append(
                edge(value, key, relation, "opencc", detail)
                if backwards
                else edge(key, value, relation, "opencc", detail)
            )
    return [row for row in rows if row]


def yitizi_edges(name: str, text: str) -> list[Edge]:
    """The three basic connections of yitizi, read as its build/build_lib/loaders.py reads them.

    ytenx CSV rows give 全等 (with the entry), 語義交疊 and 其他異體 (one group with the entry),
    簡體 and 繁體. yitizi.txt lines are `=AB…` (全等 group), `AB…` (交疊 group) or `A>BC…` (簡體).
    """
    rows: list[Edge | None] = []

    def group(characters: str, relation: str) -> None:
        members = sorted(set(characters))
        rows.extend(
            edge(x, y, relation, "yitizi", f"{name} {''.join(members)}")
            for i, x in enumerate(members)
            for y in members[i + 1 :]
        )

    if name.endswith(".csv"):
        for row in csv.DictReader(io.StringIO(text)):
            entry = row["#字"]
            if entry not in YITIZI_EXCLUDED_CHARS:
                for other in row.get("全等") or "":
                    if (
                        other not in YITIZI_EXCLUDED_CHARS
                        and (entry, other) not in YITIZI_EXCLUDED_PAIRS
                        and (other, entry) not in YITIZI_EXCLUDED_PAIRS
                    ):
                        rows.append(edge(entry, other, "equivalent", "yitizi", name))
            overlap = (row.get("語義交疊") or "") + (row.get("其他異體") or "")
            if overlap:
                group("".join(c for c in entry + overlap if c not in YITIZI_EXCLUDED_CHARS), "overlap")
            rows += [edge(entry, other, "simplified", "yitizi", name) for other in row.get("簡體") or ""]
            rows += [edge(other, entry, "simplified", "yitizi", name) for other in row.get("繁體") or ""]
        return [row for row in rows if row]
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("="):
            group(line[1:], "equivalent")
        elif len(line) >= 3 and line[1] == ">":
            rows += [edge(line[0], other, "simplified", "yitizi", name) for other in line[2:]]
        elif all(not c.isascii() for c in line):
            group(line, "overlap")
        else:
            raise SystemExit(f"yitizi {name} line {number}: unknown format {raw!r}")
    return [row for row in rows if row]


def shrink_map_edges(shrink: dict, figures: dict[str, str]) -> list[Edge]:
    """Each MJ figure's 対応するUCS to every UCS its reductions name, the table in the detail."""
    rows = []
    for entry in shrink["content"]:
        source = figures.get(entry["MJ文字図形名"])
        if not source:
            continue
        for table, targets in entry.items():
            if not isinstance(targets, list):
                continue
            for target in targets:
                if not HEX.fullmatch(target.get("UCS", "")):
                    continue
                parts = [table] + [
                    f"{key} {target[key]}" for key in ("種別", "表", "順位", "ホップ数") if key in target
                ]
                rows.append(
                    edge(
                        source,
                        scalar(target["UCS"]),
                        "reduction",
                        "mj-shrink-map",
                        f"{entry['MJ文字図形名']}: " + ", ".join(str(part) for part in parts),
                    )
                )
    return [row for row in rows if row]


def hng_edges(text: str) -> list[Edge]:
    """HNG 文字 and each character of its 異体字 column, with the 統合ID."""
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or row[0] == "文字":
            continue
        rows += [
            edge(row[0], other, "variant", "hng-basic-data", row[2]) for other in row[1] if other.strip()
        ]
    return [row for row in rows if row]


def wikidata_edges(result: dict) -> list[Edge]:
    """The SPARQL JSON result: statement, both characters, then space-separated Q-id lists.

    The detail opens with the item that holds the statement, which is where to look it up.
    """
    rows = []
    for binding in result["results"]["bindings"]:

        def value(name: str, binding: dict = binding) -> str:
            return binding.get(name, {}).get("value", "")

        kinds = value("kinds").split()
        relation = "specialized-semantic" if SPECIALIZED_SEMANTIC in kinds else "variant"
        other_kinds = [kind for kind in kinds if kind != SPECIALIZED_SEMANTIC]
        parts = [value("s").rsplit("/", 1)[-1].split("-", 1)[0]]
        parts += [f"P248 {value('stated')}"] if value("stated") else []
        parts += [f"P1552 {' '.join(other_kinds)}"] if other_kinds else []
        parts += [f"P282 {value('systems')}"] if value("systems") else []
        rows.append(edge(value("ac"), value("bc"), relation, "wikidata", "; ".join(parts)))
    return [row for row in rows if row]


def merged(rows: list[Edge]) -> list[Edge]:
    """One row per (a, b, relation, source); the details of duplicates are joined with ` | `."""
    details: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for row in rows:
        key = (row.a, row.b, row.relation, row.source)
        if row.detail not in details[key]:
            details[key].append(row.detail)
    order = {source: index for index, source in enumerate(SOURCE_IDS)}
    ranked = {relation: index for index, relation in enumerate(RELATIONS)}
    return sorted(
        (
            Edge(*key, " | ".join(sorted(detail for detail in values if detail)))
            for key, values in details.items()
        ),
        key=lambda row: (row.a, row.b, ranked[row.relation], order[row.source]),
    )


def figures_by_name(text: str) -> dict[str, str]:
    """MJ文字図形名 -> the character of its 対応するUCS, from data/vocab/mj-kanji.tsv."""
    figures = {}
    for line in text.splitlines():
        if line.startswith(("#", "mj\t")):
            continue
        name, code_point, *_ = line.split("\t")
        if HEX.fullmatch(code_point):
            figures[name] = scalar(code_point)
    return figures


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def provenance(paths: dict[str, Path], counts: dict[str, int], fetched: str) -> list[str]:
    def source(identifier: str) -> dict:
        return yaml.safe_load((SOURCES / f"{identifier}.yaml").read_text(encoding="utf-8"))

    lines = ["# 異体字 graph: one row per variant edge per source; see scripts/build_kanji_variants.py."]
    for identifier in SOURCE_IDS:
        record = source(identifier)
        lines.append(
            f"# source {identifier}: {record['name']}; {record['licence']} ({record['licence_evidence']}); "
            f"{record['attribution']}"
        )
    lines += [
        f"#   unihan: {UNIHAN_URL} sha256:{digest(paths['unihan'])}",
        f"#   unicode-ucd: {UNICODEDATA_URL} sha256:{digest(paths['ucd'])}",
        f"#   wikidata: P5475 query at {WIKIDATA_ENDPOINT}, fetched {fetched}, sha256:{digest(paths['wikidata'])}",
        f"#   yitizi: {YITIZI_URL.format(revision=YITIZI_REVISION, name='…')} ({', '.join(YITIZI_FILES)})",
        f"#   opencc: {OPENCC_URL.format(revision=OPENCC_REVISION, name='…')} ({', '.join(OPENCC_FILES)})",
        f"#   mj-shrink-map: {SHRINK_MAP_URL} sha256:{digest(paths['shrink'])}; figures from data/vocab/mj-kanji.tsv",
        f"#   hng-basic-data: {HNG_URL.format(revision=HNG_REVISION, name=HNG_INDEX)}, columns 文字, 異体字, 統合ID",
        (
            "# symmetric relations keep a before b in code point order; a directed row reads "
            '"b is the <relation> of a".'
        ),
        "# columns: " + ", ".join(COLUMNS),
        "# rows: " + ", ".join(f"{name} {counts[name]}" for name in SOURCE_IDS),
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)
    cache = args.cache / "variants"

    def fetch(url: str, dest: Path, expected: str = "text") -> Path:
        return download(url, dest, expected=expected, refresh=args.refresh, timeout=300)

    paths = {
        "unihan": fetch(UNIHAN_URL, cache / "Unihan.zip", "zip"),
        "ucd": fetch(UNICODEDATA_URL, args.cache / "ucd" / "UnicodeData.txt"),
        "shrink": fetch(SHRINK_MAP_URL, args.cache / "moji" / SHRINK_MAP_URL.rsplit("/", 1)[-1], "json"),
        "hng": fetch(HNG_URL.format(revision=HNG_REVISION, name=HNG_INDEX), cache / "hng" / HNG_INDEX),
    }
    wikidata = cache / "wikidata-p5475.json"
    fetch(f"{WIKIDATA_ENDPOINT}?{urlencode({'query': WIKIDATA_QUERY, 'format': 'json'})}", wikidata, "json")
    paths["wikidata"] = wikidata
    fetched = datetime.fromtimestamp(wikidata.stat().st_mtime, tz=UTC).date().isoformat()

    with zipfile.ZipFile(paths["unihan"]) as archive:
        unihan_text = archive.read("Unihan_Variants.txt").decode("utf-8")
    rows = unihan_edges(unihan_text)
    rows += compatibility_edges(paths["ucd"].read_text(encoding="utf-8"))
    rows += wikidata_edges(json.loads(wikidata.read_text(encoding="utf-8")))
    for name in YITIZI_FILES:
        path = fetch(YITIZI_URL.format(revision=YITIZI_REVISION, name=name), cache / "yitizi" / name)
        rows += yitizi_edges(name, path.read_text(encoding="utf-8"))
    for name in OPENCC_FILES:
        path = fetch(OPENCC_URL.format(revision=OPENCC_REVISION, name=name), cache / "opencc" / name)
        rows += opencc_edges(name, path.read_text(encoding="utf-8"))
    figures = figures_by_name(MJ_KANJI.read_text(encoding="utf-8"))
    rows += shrink_map_edges(json.loads(paths["shrink"].read_text(encoding="utf-8")), figures)
    rows += hng_edges(paths["hng"].read_text(encoding="utf-8-sig"))

    table = merged(rows)
    counts = {name: sum(1 for row in table if row.source == name) for name in SOURCE_IDS}
    for name, count in counts.items():
        if not count:
            raise SystemExit(f"no {name} rows; nothing is written rather than a table without that source")
    lines = provenance(paths, counts, fetched) + ["\t".join(COLUMNS)]
    lines += [f"{row.a}\t{row.b}\t{row.relation}\t{row.source}\t{row.detail}" for row in table]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(table)} rows {counts} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
