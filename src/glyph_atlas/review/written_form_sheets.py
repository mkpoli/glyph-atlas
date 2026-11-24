"""Contact sheets of the crops `written_forms` found, a row for each book.

A print or a scribe mostly keeps to one form, so a character's crops are read book by book. `run`
reads `<similar>/written-forms.parquet` and writes one HTML page: a section for each label and the
variant its crops are written as, the one with most crops first, and in it a row for every book,
the book with most of them first, with the number found and up to `PER_BOOK` of them, the likest
to the variant first.

A crop's picture is the display crop the site serves. Its key comes from the page bundles of the
local copy of the site (`work/atlas-v3/out/pages`); the file is read from the similar-crop index's
media cache and fetched from the site when the cache lacks it (`similar.fetch`), a few at a time. A
crop the bundles name no display crop for is left out, with its count; corpus glyphs, whose pictures
sit in their records, are among them.
"""
from __future__ import annotations

import base64
import gzip
import html
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .written_forms import FILE, PAGE_BITS

#: Crops shown for a book.
PER_BOOK = 40

PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Written forms by book</title>
<style>
:root{{--bg:#f6f6f3;--surface:#fff;--fg:#1d1f22;--muted:#61666d;--line:#dcdcd6}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#15171b;--surface:#1d2026;--fg:#e7e8ea;--muted:#9aa0a8;--line:#30343c}}}}
:root[data-theme="dark"]{{--bg:#15171b;--surface:#1d2026;--fg:#e7e8ea;--muted:#9aa0a8;--line:#30343c}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px 56px}}
main{{max-width:1100px;margin:auto;display:grid;gap:18px}}
h1{{font-size:1.5rem;margin:0}}h2{{font-size:1.25rem;margin:0;font-weight:600}}
section{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:grid;gap:10px;min-width:0}}
.book{{display:grid;gap:4px;min-width:0}}
.name{{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;font-size:.9rem}}
.name span{{color:var(--muted);font-variant-numeric:tabular-nums}}
.crops{{display:flex;flex-wrap:wrap;gap:4px}}
.crops img{{height:64px;width:auto;max-width:100%;border-radius:3px;background:#fff}}
</style>
<main><h1>Written forms by book</h1>
{sections}
</main></html>
"""


def keys(crops: dict[str, int], pages: Path) -> dict[str, str]:
    """The media key of each crop's display image, from the newest bundle of its page."""
    wanted: dict[int, dict[int, str]] = defaultdict(dict)
    for identity, number in crops.items():
        wanted[number >> PAGE_BITS][number & ((1 << PAGE_BITS) - 1)] = identity
    found = {}
    for page, slots in wanted.items():
        bundles = sorted(pages.glob(f"{page}-*.json.gz"), key=lambda path: int(path.stem.split("-")[1].split(".")[0]))
        if not bundles:
            continue
        with gzip.open(bundles[-1], "rt", encoding="utf-8") as handle:
            for entry in json.load(handle).get("crops", ()):
                identity = slots.get(int(entry.get("slot", -1)))
                image = entry.get("image") or ""
                if identity and image.startswith("/atlas/media/") and image.endswith(".webp"):
                    found[identity] = image[len("/atlas/media/"):-len(".webp")]
    return found


def choose(rows: list[tuple[str, str, int, str, float]], *, per_book: int = PER_BOOK) -> list[dict]:
    """The sections of the page from the crops found `(label, other, book, id, near)`, all with a
    picture: labels with most crops first, their books with most first, the likest crops first."""
    grouped: dict[tuple[str, str], dict[int, list[tuple[float, str]]]] = defaultdict(lambda: defaultdict(list))
    for label, other, book, identity, near in rows:
        grouped[label, other][book].append((near, identity))
    found = []
    for (label, other), books in grouped.items():
        held = [{"book": book, "crops": len(crops), "ids": [identity for _, identity in sorted(crops, reverse=True)[:per_book]]}
                for book, crops in books.items()]
        held.sort(key=lambda entry: (-entry["crops"], entry["book"]))
        found.append({"label": label, "other": other, "crops": sum(entry["crops"] for entry in held), "books": held})
    found.sort(key=lambda section: (-section["crops"], section["label"], section["other"]))
    return found


def render(sections: list[dict], titles: dict[int, str], pictures: dict[str, Path]) -> str:
    """The page: a section for each label and variant, a row of crops for each book."""
    parts = []
    for section in sections:
        books = []
        for book in section["books"]:
            images = "".join(
                f'<img alt="" loading="lazy" src="data:image/webp;base64,{base64.b64encode(pictures[i].read_bytes()).decode()}">'
                for i in book["ids"] if i in pictures)
            if images:
                title = html.escape(titles.get(book["book"]) or str(book["book"]))
                books.append(f'<div class="book"><div class="name"><b lang="ja">{title}</b>'
                             f'<span>{book["crops"]}</span></div><div class="crops">{images}</div></div>')
        if books:
            parts.append(f'<section><h2 lang="ja">{html.escape(section["label"])} → {html.escape(section["other"])}</h2>'
                         + "".join(books) + "</section>")
    return PAGE.format(sections="\n".join(parts))


def run(similar: Path, out: Path, *, mirror: Path, atlas: Path, pages: Path, media: Path,
        base: str = "https://glyphatlas.org") -> dict:
    """Write the sheets of the crops found in `<similar>/written-forms.parquet` to `out`."""
    import pyarrow.parquet as pq

    from ..similar import fetch

    crops = [row for row in pq.read_table(similar / FILE).to_pylist() if row["written"]]
    wanted = {row["id"] for row in crops}
    with sqlite3.connect(f"file:{mirror}?mode=ro", uri=True) as db:
        numbers = {identity: number for identity, number in db.execute("SELECT text, id FROM ids") if identity in wanted}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        titles = {book: title or key for book, key, title in db.execute("SELECT id, key, title FROM books")}
    named = keys(numbers, pages)
    sections = choose([(row["label"], row["other"], row["book"], row["id"], row["near"])
                       for row in crops if row["id"] in named])
    shown = sorted({identity for section in sections for book in section["books"] for identity in book["ids"]})
    files = {identity: {"key": named[identity], "path": str(media / named[identity][:2] / f"{named[identity]}.webp")}
             for identity in shown}
    fetched = fetch(files, base, workers=2)
    pictures = {identity: Path(entry["path"]) for identity, entry in files.items() if Path(entry["path"]).is_file()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(sections, titles, pictures), encoding="utf-8")
    return {"found": len(wanted), "with_picture": len(wanted & named.keys()), "shown": len(pictures), **fetched}
