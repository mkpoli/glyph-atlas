"""Build data/vocab/kana-origins.tsv: the kanji each modern hiragana is the cursive form of.

Unicode and the MJ and NINJAL hentaigana tables give a 字母 only to the hentaigana; the modern
kana, which are themselves cursive kanji (た from 太), have none there. This table takes the
平仮名字源 field of each single-kana article of the Japanese Wikipedia (the article た states
「太の草書体」), keeps the fields that name a cursive form, and records the revision each came from.

Columns: kana, 字母, 字母 code point, the field as the article gives it (links reduced to their text),
and the article revision.

    uv run python scripts/build_kana_origins.py
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

KANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわゐゑをん"
API = "https://ja.wikipedia.org/w/api.php"
OUT = Path(__file__).resolve().parents[1] / "data" / "vocab" / "kana-origins.tsv"
FIELD = re.compile(r"^\|\s*平仮名字源\s*=\s*(?P<value>[^|]*(?:\[\[[^\]]*\]\][^|]*)*)")
LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
IDEOGRAPH = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")


def fetch() -> dict[str, dict]:
    query = {"action": "query", "titles": "|".join(KANA), "prop": "revisions", "rvprop": "ids|content",
             "rvslots": "main", "format": "json", "formatversion": 2, "redirects": 1}
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(query)}",
                                     headers={"User-Agent": "glyph-atlas (https://github.com/mkpoli/glyph-atlas)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        document = json.load(response)["query"]
    redirects = {row["from"]: row["to"] for row in document.get("redirects", [])}
    pages = {page["title"]: page for page in document["pages"]}
    return {kana: pages[redirects.get(kana, kana)] for kana in KANA}


def origin(content: str) -> str | None:
    """The 平仮名字源 field's text, links reduced to what they show."""
    for line in content.splitlines():
        found = FIELD.match(line.strip())
        if found:
            return LINK.sub(r"\1", found["value"]).strip()
    return None


def rows(pages: dict[str, dict]) -> list[tuple[str, ...]]:
    table = []
    for kana, page in pages.items():
        revision = page["revisions"][0]
        text = origin(revision["slots"]["main"]["content"])
        # Only a cursive form: the kana is then the kanji's own shape, written quickly.
        if not text or "草" not in text:
            continue
        kanji = IDEOGRAPH.search(text)
        if kanji:
            table.append((kana, kanji[0], f"U+{ord(kanji[0]):04X}", text, str(revision["revid"])))
    return table


def main() -> None:
    table = rows(fetch())
    header = [
        "# 平仮名字源 of each single-kana article, Japanese Wikipedia (https://ja.wikipedia.org/wiki/<kana>)",
        "# licence: CC-BY-SA-4.0 (https://ja.wikipedia.org/wiki/Wikipedia:ウィキペディアを二次利用する)",
        "# attribution: Wikipedia 日本語版の執筆者, 各仮名の記事（revision は各行）",
        f"# accessed: {datetime.now(UTC).date().isoformat()}",
        f"# rows: {len(table)} of {len(KANA)} kana",
        "kana\tjibo\tjibo_code_point\tsource_text\trevision",
    ]
    OUT.write_text("\n".join(header + ["\t".join(row) for row in table]) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(table), "out": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
