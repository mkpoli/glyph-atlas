"""Build data/vocab/headword-readings.tsv, the headwords of the 說文解字 and the 廣韻 with their readings.

`glyph_atlas.shape_families` reads it to tell a form that is a character of its own in an old dictionary
(听 宜引切) from one that is only another way to write its full form. One row per headword and reading:

- `guangyun`: each 字頭 of the 廣韻（澤存堂本）in nk2028's tshet-uinh-data with the 音韻地位 of its 小韻. Two
  readings are compared by that position, never by the spelling of the 反切. A collated 字頭 is the character
  the collation reads (［X］ supplied, A〈B〉 corrected to B; a ｛X｝ is left out).
- `shuowen`: each 親字 (`head`) and 重文 (`form`) of the 說文解字 in the data of shuowen.org with the 反切 徐鉉
  gives the entry.

    uv run scripts/build_headword_readings.py

Network access happens here and nowhere else; downloads are cached under `cache/headwords/`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import yaml

from glyph_atlas.net import download

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "vocab" / "headword-readings.tsv"
CACHE = ROOT / "cache" / "headwords"
SOURCES = ROOT / "data" / "sources"
GUANGYUN_SHA256 = "f2b66197355d4fbff0776ab34e4aece817363b00446fbcf08e2f1677a7ac0c5f"
GUANGYUN_PATH = "%E9%9F%BB%E6%9B%B8/%E5%BB%A3%E9%9F%BB.csv"
#: Of the entries' JSON, in the order of their ids: an archive's own bytes are not stable.
SHUOWEN_SHA256 = "4bfa32cb31434d2bdb6422d7bbb649bc11af2f87c6554daf247be126e3a0ae84"
COLUMNS = ["dictionary", "entry", "char", "role", "reading"]


def record(identifier: str) -> dict:
    return yaml.safe_load((SOURCES / f"{identifier}.yaml").read_text(encoding="utf-8"))


def headword(text: str) -> str | None:
    """The character a 字頭 of the 廣韻 is, as its collation reads it; None for one it deletes or cannot name."""
    text = re.sub(r"｛[^｝]*｝", "", text)
    text = re.sub(r".〈([^〉]*)〉", r"\1", text).strip("［］")
    return text if len(text) == 1 else None


def guangyun(text: str) -> list[tuple[str, ...]]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        char = headword(row["字頭"])
        if char and row["音韻地位"]:
            rows.append(("guangyun", f"{row['小韻號']}.{row['小韻字號']}", char, "head", row["音韻地位"]))
    return rows


def shuowen(entries: list[dict]) -> list[tuple[str, ...]]:
    rows = []
    for entry in entries:
        readings = re.findall(r"(..)切", entry["pronunciation"] or "") or [""]
        forms = [(entry["wordhead"], "head")] + [(variant["wordhead"], "form") for variant in entry["variants"]]
        rows += [("shuowen", str(entry["id"]), char, role, reading)
                 for char, role in dict.fromkeys(forms) if len(char) == 1 for reading in readings]
    return rows


def main() -> None:
    sound, graph = record("guangyun-zecuntang"), record("shuowen")
    table = download(f"https://raw.githubusercontent.com/nk2028/tshet-uinh-data/{sound['revision']}/{GUANGYUN_PATH}",
                     CACHE / f"guangyun-{sound['revision']}.csv")
    archive = download(f"{graph['url']}/archive/{graph['revision']}.zip", CACHE / f"shuowen-{graph['revision']}.zip", expected="zip")
    with zipfile.ZipFile(archive) as files:
        entries = sorted((json.loads(files.read(name)) for name in files.namelist()
                          if re.search(r"/data/\d+\.json$", name)), key=lambda entry: entry["id"])
    for found, expected, name in (
            (hashlib.sha256(table.read_bytes()).hexdigest(), GUANGYUN_SHA256, table),
            (hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), SHUOWEN_SHA256, archive)):
        if found != expected:
            raise SystemExit(f"{name}: sha256 {found}, not {expected}")
    rows = sorted({*guangyun(table.read_text(encoding="utf-8")), *shuowen(entries)},
                  key=lambda row: (row[0], [int(part) for part in row[1].split(".") if part.isdigit()], row[1:]))
    header = ["# Headwords of the 廣韻 and the 說文解字 with their readings; see scripts/build_headword_readings.py."]
    for source, what in ((sound, "guangyun: the 音韻地位 of each 字頭"), (graph, "shuowen: 徐鉉's 反切 of each entry")):
        header.append(f"# source {source['id']} ({what}): {source['name']}; {source['licence']} ({source['licence_evidence']}); "
                      f"{source['attribution']}; revision {source['revision']}")
    lines = [*header, "\t".join(COLUMNS), *("\t".join(row) for row in rows)]
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} rows -> {TARGET}")


if __name__ == "__main__":
    main()
