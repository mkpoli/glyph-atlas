"""Build `data/sources/codh-books.tsv` from the CODH book list and the IIIF manifests.

The book list at https://codh.rois.ac.jp/char-shape/book/ carries the identifier, the title, the
number of code points, the number of characters and the release month of each of the 44 books. The
NIJL IIIF manifest `https://codh.rois.ac.jp/pmjt/book/{bid}/manifest.json` carries the production
type (`type`: 刊 or 写), the collection the scan belongs to and the publication statement. The three
books whose identifiers are CODH-local (brsk00000, hnsd00000, umgy00000) have no manifest at that
address; their rows keep the fields from the book list and leave the rest empty.

The list page is cached under `cache/`, the manifests under `cache/codh-manifests/`. Both are
refetched with `--refresh`; requests wait 1 second apart, as `docs/development.md` says
for codh.rois.ac.jp.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
LIST_URL = "https://codh.rois.ac.jp/char-shape/book/"
MANIFEST_URL = "https://codh.rois.ac.jp/pmjt/book/{bid}/manifest.json"
USER_AGENT = "glyph-atlas (+https://github.com/mkpoli/glyph-atlas)"
PAUSE = 1.0
COLUMNS = [
    "bid",
    "title",
    "code_points",
    "characters",
    "released",
    "type",
    "production",
    "collection",
    "issued",
]


def book_list(client: httpx.Client, cache: Path, refresh: bool) -> list[dict[str, str]]:
    page = cache / "codh-book-list.html"
    if refresh or not page.exists():
        response = client.get(LIST_URL)
        response.raise_for_status()
        page.write_text(response.text, encoding="utf-8")
    raw = page.read_text(encoding="utf-8")
    books = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.DOTALL)[1:]:
        cells = [
            html.unescape(re.sub("<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if len(cells) < 6 or not cells[1]:
            continue
        books.append(
            {
                "bid": cells[1],
                "title": cells[2],
                "code_points": cells[3].replace(",", ""),
                "characters": cells[4].replace(",", ""),
                "released": cells[5],
            }
        )
    return books


def manifest(client: httpx.Client, folder: Path, bid: str, refresh: bool) -> dict | None:
    target = folder / f"{bid}.json"
    if refresh or not target.exists():
        time.sleep(PAUSE)
        response = client.get(MANIFEST_URL.format(bid=bid))
        if response.status_code != 200 or not response.text.lstrip().startswith("{"):
            return None
        target.write_text(response.text, encoding="utf-8")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def production_of(kind: str | None) -> str:
    if kind == "刊":
        return "printed"
    if kind == "写":
        return "handwritten"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "sources" / "codh-books.tsv")
    parser.add_argument("--refresh", action="store_true", help="refetch the list page and the manifests")
    args = parser.parse_args()

    cache = ROOT / "cache"
    cache.mkdir(exist_ok=True)
    manifests = cache / "codh-manifests"
    manifests.mkdir(exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        books = book_list(client, cache, args.refresh)
        for book in books:
            document = manifest(client, manifests, book["bid"], args.refresh)
            fields = {m.get("label"): m.get("value") for m in (document or {}).get("metadata", [])}
            book["type"] = fields.get("type") or ""
            book["production"] = production_of(book["type"] or None)
            book["collection"] = fields.get("DCTERMS.relation") or ""
            book["issued"] = fields.get("DCTERMS.issued") or ""

    header = (
        f"# bid\ttitle\tcode_points\tcharacters\treleased\ttype\tproduction\tcollection\tissued\n"
        f"# source: 日本古典籍くずし字データセット (CODH), {LIST_URL}, and the NIJL IIIF manifests\n"
        f"# licence: CC BY-SA 4.0, http://codh.rois.ac.jp/char-shape/#license\n"
        f"# read on {time.strftime('%Y-%m-%d')}; {len(books)} books, "
        f"{sum(int(b['characters']) for b in books)} characters\n"
    )
    lines = ["\t".join(book[column] for column in COLUMNS) for book in books]
    args.out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(books)} books, {sum(int(b['characters']) for b in books)} characters -> {args.out}")
    unknown = [b["bid"] for b in books if b["production"] == "unknown"]
    if unknown:
        print(f"no manifest for {', '.join(unknown)}; production left unknown")


if __name__ == "__main__":
    main()
