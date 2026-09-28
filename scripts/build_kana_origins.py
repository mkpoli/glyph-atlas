"""Build data/vocab/kana-origins.tsv: the kanji each modern hiragana and katakana is written from (字源).

Unicode and the MJ and NINJAL hentaigana tables give a 字母 only to the hentaigana; the modern
kana, which are themselves kanji written quickly (た from 太) or in part (タ from 多), have none
there. Each single-kana article of the Japanese Wikipedia states both in its infobox: 平仮名字源
(the article た gives 「太の草書体」) and 片仮名字源 (「多の一部分」). A hiragana is a whole kanji
in cursive; a katakana is usually a part of one, which is why the table calls both 字源 and the
site shows them apart from a hentaigana's 字母.

This table keeps a hiragana field that names a cursive form, and a katakana field that names its
kanji, before 「の」 when it says which part (「阿の偏」 gives 阿). A katakana field that weighs
another explanation itself (ン: 「多数説あり」, ワ: 「…あるいは○の変形」) is left out rather than
decided here. The article 片仮名, section 字体の由来, records other theories for several katakana
(ケ from 个, ツ from 門 or 津, ヱ from 慧 …) and 中田祝夫's critique of the traditional table; a row
whose kanji that section contests carries the other kanji in `also_cited` and is shown as uncertain.

A link whose text holds no Japanese (ア's field is 「阿の[[偏]][[MOLA]]」) is dropped from the field
text, and the row's `note` says so.

Columns: kana, 字源, its code point, the infobox field, the field as the article gives it (links
reduced to their text), the article revision, `note`, `also_cited` (the other kanji the article
片仮名 names, space-separated) and `also_cited_revision` (that article's revision).

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
#: The infobox fields, wherever they sit on a line: わ's article puts several fields on one.
FIELDS = {name: re.compile(rf"\|\s*{name}\s*=\s*(?P<value>[^\n]*)") for name in ("平仮名字源", "片仮名字源", "Unicode片仮名")}
LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
IDEOGRAPH = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")
JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")
#: The article on katakana as a whole, whose 字体の由来 section records the contested origins.
KATAKANA_ARTICLE = "片仮名"
SECTION = re.compile(r"^==\s*字体の由来\s*==\s*$(?P<body>.*?)^==[^=]", re.MULTILINE | re.DOTALL)
RED = re.compile(r"\{\{Color\|red\|'''(?P<text>[^']+)'''\}\}")
BOLD_KANA = re.compile(r"「'''(?P<kana>[゠-ヿ])'''」|\{\{Color\|red\|'''(?P<red>[゠-ヿ])'''\}\}")
USER_AGENT = "glyph-atlas (https://github.com/mkpoli/glyph-atlas)"


def fetch(titles: str) -> dict[str, dict]:
    """The latest revision of each article, by the title asked for."""
    query = {"action": "query", "titles": titles, "prop": "revisions", "rvprop": "ids|content",
             "rvslots": "main", "format": "json", "formatversion": 2, "redirects": 1}
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(query)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        document = json.load(response)["query"]
    redirects = {row["from"]: row["to"] for row in document.get("redirects", [])}
    pages = {page["title"]: page for page in document["pages"]}
    return {title: pages[redirects.get(title, title)] for title in titles.split("|")}


def field_value(text: str) -> str:
    """A template field's value: up to the next `|` that is not inside a link."""
    depth = 0
    for i, char in enumerate(text):
        if text.startswith("[[", i):
            depth += 1
        elif text.startswith("]]", i) and depth:
            depth -= 1
        elif char == "|" and not depth:
            return text[:i]
    return text


def reduce_links(text: str) -> tuple[str, list[str]]:
    """Links reduced to what they show, and the links dropped because they show no Japanese."""
    dropped: list[str] = []

    def shown(match: re.Match) -> str:
        if JAPANESE.search(match[1]):
            return match[1]
        dropped.append(match[0])
        return ""

    return LINK.sub(shown, text).strip(), dropped


def field(content: str, name: str) -> tuple[str, list[str]] | None:
    """An infobox field's text, links reduced, and the links dropped from it."""
    found = FIELDS[name].search(content)
    return reduce_links(field_value(found["value"])) if found else None


def contested(content: str) -> dict[str, list[str]]:
    """katakana -> the kanji the 片仮名 article's 字体の由来 section names for it, bullet by bullet."""
    section = SECTION.search(content)
    found: dict[str, list[str]] = {}
    for line in (section["body"] if section else "").splitlines():
        if not line.startswith("*"):
            continue
        kana = [m["kana"] or m["red"] for m in BOLD_KANA.finditer(line)]
        kanji = [k for m in RED.finditer(line) for k in IDEOGRAPH.findall(m["text"])]
        for letter in kana:
            found.setdefault(letter, [])
            found[letter] += [k for k in kanji if k not in found[letter]]
    return found


def hiragana_kanji(text: str) -> list[str]:
    """The kanji a 平仮名字源 names as cursive: every one before 「草」, or none."""
    # Only a cursive form: the kana is then the kanji's own shape, written quickly.
    if "草" not in text:
        return []
    return list(dict.fromkeys(IDEOGRAPH.findall(text[:text.index("草")])))


def katakana_kanji(text: str) -> list[str]:
    """The kanji a 片仮名字源 names: before 「の」 when it says which part (阿の偏), else the whole (千).

    A field that offers another explanation (ワ: 「和の旁の部分、あるいは○の変形」, ン: 「多数説あり」)
    gives none: keeping only its kanji would state a certainty the article does not.
    """
    if "説" in text or "あるいは" in text:
        return []
    return list(dict.fromkeys(IDEOGRAPH.findall(text.split("の")[0])))


def rows(pages: dict[str, dict], katakana_page: dict | None = None) -> list[tuple[str, ...]]:
    others: dict[str, list[str]] = {}
    other_revision = ""
    if katakana_page and katakana_page.get("revisions"):
        revision = katakana_page["revisions"][0]
        others, other_revision = contested(revision["slots"]["main"]["content"]), str(revision["revid"])
    table = []
    for kana, page in pages.items():
        if not page.get("revisions"):
            raise RuntimeError(f"the article {kana} returned no revision")
        revision = page["revisions"][0]
        content = revision["slots"]["main"]["content"]
        katakana_point = (field(content, "Unicode片仮名") or ("", []))[0].strip()
        sides = [(kana, "平仮名字源", hiragana_kanji)]
        if katakana_point:
            sides.append((chr(int(katakana_point, 16)), "片仮名字源", katakana_kanji))
        for letter, name, kanji_of in sides:
            found = field(content, name)
            if not found:
                continue
            text, dropped = found
            note = f"dropped the link {' '.join(dropped)}, which shows no Japanese" if dropped else ""
            for kanji in kanji_of(text):
                also = [k for k in others.get(letter, []) if k != kanji] if name == "片仮名字源" else []
                table.append((letter, kanji, f"U+{ord(kanji):04X}", name, text, str(revision["revid"]), note,
                              " ".join(also), other_revision if also else ""))
    return table


def main() -> None:
    table = rows(fetch("|".join(KANA)), fetch(KATAKANA_ARTICLE)[KATAKANA_ARTICLE])
    header = [
        "# 平仮名字源 and 片仮名字源 of each single-kana article, Japanese Wikipedia (https://ja.wikipedia.org/wiki/<kana>);",
        "# also_cited from the article 片仮名, section 字体の由来 (https://ja.wikipedia.org/wiki/片仮名), which records",
        "# other theories for several katakana and 中田祝夫's critique of the traditional table.",
        "# licence: CC-BY-SA-4.0 (https://ja.wikipedia.org/wiki/Wikipedia:ウィキペディアを二次利用する)",
        "# attribution: Wikipedia 日本語版の執筆者, 各仮名の記事と「片仮名」（revision は各行）",
        f"# accessed: {datetime.now(UTC).date().isoformat()}",
        f"# rows: {len(table)} from {len(KANA)} articles",
        "kana\torigin\torigin_code_point\tfield\tsource_text\trevision\tnote\talso_cited\talso_cited_revision",
    ]
    OUT.write_text("\n".join(header + ["\t".join(row) for row in table]) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(table), "contested": sum(1 for row in table if row[7]), "out": str(OUT)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
