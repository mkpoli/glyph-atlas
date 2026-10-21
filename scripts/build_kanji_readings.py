"""Build data/vocab/kanji-readings.json, the Japanese readings the search box finds kanji by.

A reader who types とき is shown 時 and 刻 as well as the kana 𛄤. The readings are Unihan's
`kJapanese` (Unihan_Readings.txt): 音読み in katakana and 訓読み in hiragana, as the dictionaries
behind the field give them, okurigana included (きざむ). Both are keyed here in hiragana, the form the
search box normalises a query to, so ジ and じ find the same kanji.

Within one reading the kanji the corpus attests most come first (`n_occurrences` of the corpus
index, `work/corpus-index/chars.parquet`), so the site reads only the kanji a list shows: こう lists
高, 行 and 考 before the hundreds of rarer kanji read こう. Kanji the corpus attests equally (most
of them never) follow the order a Japanese reader knows them in: 常用漢字 (`kJoyoKanji`), then 人名用漢字
(`kJinmeiyoKanji`), then the rest of JIS X 0208 (`kIRG_JSource` J0), then the others, then code point.
The order of a kanji's own readings in `kJapanese` says nothing of how common each is (以 lists おもう
first), so it is not used. Rebuild after the corpus grows much; the readings themselves change only
with a Unicode release.

scripts/atlas3/reference.py writes the table into the reference objects with the cards of the first
kanji of each reading, so a kana search reads one object for the kanji it lists; run it after this.

    uv run python scripts/build_kanji_readings.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import defaultdict
from pathlib import Path

import yaml

from glyph_atlas.net import download
from glyph_atlas.refs import to_hiragana

ROOT = Path(__file__).resolve().parents[1]
UNICODE_VERSION = "18.0.0"
UNIHAN_URL = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/Unihan.zip"
TARGET = ROOT / "data" / "vocab" / "kanji-readings.json"
SOURCE = ROOT / "data" / "sources" / "unihan.yaml"


def fields(archive: zipfile.ZipFile, name: str, wanted: set[str]) -> dict[str, dict[str, str]]:
    """`field -> {char: value}` for the wanted fields of one Unihan file."""
    found: dict[str, dict[str, str]] = defaultdict(dict)
    for line in archive.read(name).decode("utf-8").splitlines():
        cells = line.split("\t")
        if len(cells) == 3 and cells[1] in wanted:
            found[cells[1]][chr(int(cells[0].removeprefix("U+"), 16))] = cells[2]
    return found


def tier(char: str, mappings: dict[str, dict[str, str]], sources: dict[str, dict[str, str]]) -> int:
    if char in mappings["kJoyoKanji"]:
        return 0
    if char in mappings["kJinmeiyoKanji"]:
        return 1
    if sources["kIRG_JSource"].get(char, "").startswith("J0-"):
        return 2
    return 3


def corpus_counts(path: Path) -> dict[str, int]:
    """How often the corpus index counts each character."""
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["char", "n_occurrences"]).to_pydict()
    return dict(zip(table["char"], table["n_occurrences"], strict=True))


def readings(path: Path, counts: dict[str, int]) -> dict[str, list[str]]:
    """Each hiragana reading and the kanji that read so, most attested first."""
    with zipfile.ZipFile(path) as archive:
        japanese = fields(archive, "Unihan_Readings.txt", {"kJapanese"})["kJapanese"]
        mappings = fields(archive, "Unihan_OtherMappings.txt", {"kJoyoKanji", "kJinmeiyoKanji"})
        sources = fields(archive, "Unihan_IRGSources.txt", {"kIRG_JSource"})
    keyed: dict[str, set[str]] = defaultdict(set)
    for char, value in japanese.items():
        for reading in value.split():
            keyed[to_hiragana(reading)].add(char)
    return {reading: sorted(chars, key=lambda char: (-counts.get(char, 0), tier(char, mappings, sources), ord(char)))
            for reading, chars in sorted(keyed.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("--out", type=Path, default=TARGET)
    parser.add_argument("--counts", type=Path, default=ROOT / "work" / "corpus-index" / "chars.parquet")
    parser.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = parser.parse_args(argv)
    path = download(UNIHAN_URL, args.cache / "variants" / UNICODE_VERSION / "Unihan.zip",
                    expected="zip", refresh=args.refresh, timeout=300)
    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    table = readings(path, corpus_counts(args.counts))
    document = {
        "source": {
            "name": "Unicode Han Database (Unihan)",
            "url": UNIHAN_URL,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
            "fields": ["kJapanese", "kJoyoKanji", "kJinmeiyoKanji", "kIRG_JSource"],
            "licence": source["licence"],
            "attribution": source["attribution"],
        },
        "order": "corpus occurrences (most first), then 常用漢字, 人名用漢字, JIS X 0208, others, then code point",
        "readings": table,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{args.out.relative_to(ROOT)}: {len(table)} readings, {sum(map(len, table.values()))} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
