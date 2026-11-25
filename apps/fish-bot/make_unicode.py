"""Builds the Unicode data the bot answers from, out of the Unicode Character Database.

    python3 make_unicode.py 18.0.0 2026-09-16

It downloads Blocks.txt, DerivedAge.txt, PropList.txt and extracted/DerivedName.txt of that version from
https://www.unicode.org/Public/ and writes unicode-<version>.json.gz beside this file: the blocks, the
version each range of characters came with, the number of unified ideographs, and every character's
name, with ranges of names that only differ in their code point kept as one. Run it when a version is
published, name the file in fish_bot.py and bring the facts in PERSONA_PROMPT up to it; a test compares
them.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://www.unicode.org/Public/{version}/ucd/{name}"
FILES = ("Blocks.txt", "DerivedAge.txt", "PropList.txt", "extracted/DerivedName.txt")
LINE = re.compile(r"([0-9A-F]{4,6})(?:\.\.([0-9A-F]{4,6}))?\s*;\s*([^#]+?)\s*(?:#.*)?$")


def rows(text: str) -> list[tuple[int, int, str]]:
    found = []
    for line in text.splitlines():
        match = LINE.match(line)
        if match:
            low = int(match[1], 16)
            found.append((low, int(match[2], 16) if match[2] else low, match[3]))
    return found


def build(version: str, released: str, texts: dict[str, str]) -> dict[str, object]:
    # The noncharacters have a version too, and will never be characters.
    ages = sorted(row for row in rows(texts["DerivedAge.txt"]) if row[0] & 0xFFFE != 0xFFFE and row[0] != 0xFDD0)
    names = rows(texts["extracted/DerivedName.txt"])
    return {
        "version": version, "released": released,
        "sources": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()},
        "unified": sum(high - low + 1 for low, high, name in rows(texts["PropList.txt"]) if name == "Unified_Ideograph"),
        "blocks": rows(texts["Blocks.txt"]), "ages": ages,
        "patterns": [row for row in names if row[2].endswith("*")],
        "names": {f"{low:X}": name for low, _, name in names if not name.endswith("*")},
    }


def main(version: str, released: str) -> None:
    texts = {}
    for name in FILES:
        with urllib.request.urlopen(SOURCE.format(version=version, name=name), timeout=120) as response:
            texts[name] = response.read().decode("utf-8")
    data = json.dumps(build(version, released, texts), ensure_ascii=False, separators=(",", ":")).encode()
    path = Path(__file__).with_name(f"unicode-{version}.json.gz")
    path.write_bytes(gzip.compress(data, 9, mtime=0))
    print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main(*sys.argv[1:3])
