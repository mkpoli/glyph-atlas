"""Place the crops of a book of CODH's 篆書字体データセット by the Han character each is filed under.

A book of the dataset other than 説文解字 gives no order to match against Unicode's small seal
characters, so its crops are placed by their 親字 alone. `SealSources.txt` of Unicode 18.0 gives
every small seal character the Han character that transcribes its structure (`kSEAL_MCJK`). The
`placed` column says what a crop was put on:

- `equivalent`: exactly one seal character has the 親字 as its `kSEAL_MCJK`, and the crop takes it. A
  親字 CODH gives as a compatibility ideograph is read as the unified ideograph it is canonically
  equivalent to (器 U+FA38 as U+5668);
- `parent`: no seal character has it, or more than one, and the crop stays on the 親字;
- empty: the 親字 is 〓, a character CODH found no code point for, and the crop has no character.

The rule reads no ink. A book that prints several forms under one 親字 has them all on the one seal
character, whichever of them is the small seal form.

The crops are numbered by page, by column from the right and down each column. The pages are those
of the holder's IIIF manifest, which gives each its image service and size.

    uv run scripts/build_tensho_equivalents.py <book zip> --seal-sources cache/ucd/SealSources.txt

It writes `data/tensho/<book>/characters.tsv` and `pages.tsv`. The zip must be the one the source
record pins. The manifest is the one the source record names, read from the manifest cache or
downloaded into it once.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from build_tensho_shuowen import FIELDS, Book, crops_in_order, seal_fields

from glyph_atlas.importers.honkoku_data import fetch_manifest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "sources" / "codh-tensho.yaml"
DATA = ROOT / "data" / "tensho"
#: CODH's mark for a 親字 with no code point (凡例 2).
GETA = "3013"
#: Two boxes whose centres are closer than this are in one column: in the three volumes of 新撰篆書字典
#: two neighbours in a column are 86 pixels apart at most, and two columns are never nearer than 140.
COLUMN = 120
PAGE_FIELDS = ("page", "canvas", "image", "width", "height")


def unified(parent: str) -> str:
    """A 親字 as the ideograph it is canonically equivalent to, as a hex code point."""
    return f"{ord(unicodedata.normalize('NFC', chr(int(parent, 16)))):04X}"


def canvas_number(canvas: str) -> int:
    """The number of a canvas of a IIIF manifest, from its URI."""
    number = re.search(r"/canvas/(\d+)$", canvas)
    if number is None:
        raise SystemExit(f"{canvas}: not a numbered canvas")
    return int(number.group(1))


def equivalents(path: Path) -> dict[str, list[str]]:
    """The seal characters of each Han character that is a `kSEAL_MCJK`, as hex code points."""
    held: dict[str, list[str]] = defaultdict(list)
    for point, values in seal_fields(path).items():
        if "kSEAL_MCJK" in values:
            held[values["kSEAL_MCJK"]].append(point)
    return held


def place(crops: list[dict], seals: dict[str, list[str]]) -> None:
    """Give each crop the seal character its 親字 alone answers, or leave it on the 親字."""
    for crop in crops:
        found = seals.get(unified(crop["parent"]), [])
        if crop["parent"] == GETA:
            crop["placed"] = ""
        elif len(found) == 1:
            crop.update(placed="equivalent", seal=found[0], equivalent=unified(crop["parent"]))
        else:
            crop["placed"] = "parent"


def manifest_pages(manifest: dict) -> dict[int, dict]:
    """The id, image service and size of every canvas of a Presentation 2 manifest, by canvas number."""
    pages: dict[int, dict] = {}
    for canvas in manifest["sequences"][0]["canvases"]:
        resource = canvas["images"][0]["resource"]
        pages[canvas_number(canvas["@id"])] = {
            "canvas": canvas["@id"], "image": resource["service"]["@id"].rstrip("/"),
            "width": int(resource["width"]), "height": int(resource["height"]),
        }
    return pages


def write(crops: list[dict], target: Path, book: str, title: str, release: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# The seal forms of {title} cut by CODH, 篆書字体データセット book {book} ({release}), each on the",
        "# small seal character of Unicode 18.0 its 親字 alone answers, or on the 親字. Written by",
        "# scripts/build_tensho_equivalents.py. seq numbers the crops by page, by column from the right and",
        "# down each column; image, x, y, w, h and parent (the 親字) are CODH's, the box in pixels of the page",
        "# image; column counts the page's columns of crops from the right; canvas is the page's canvas as the",
        "# holder's manifest names it; char, code_point and equivalent (kSEAL_MCJK) are from SealSources.txt of Unicode",
        "# 18.0.0; ccz is empty, the book being no edition of 説文解字; placed is equivalent, parent or, for a",
        "# 親字 with no code point, empty; sha256 is the crop file's.",
    )
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        for seq, crop in enumerate(crops, start=1):
            seal = crop.get("seal")
            parent = chr(int(crop["parent"], 16))
            writer.writerow([
                seq, crop["image"], crop["canvas"], crop["column"], crop["x"], crop["y"], crop["w"], crop["h"],
                parent, f"U+{crop['parent']}", chr(int(seal.removeprefix("U+"), 16)) if seal else "", seal or "",
                "", chr(int(crop["equivalent"], 16)) if seal else "", crop["placed"], crop["sha256"],
            ])


def write_pages(crops: list[dict], pages: dict[int, dict], target: Path, book: str, manifest: str) -> None:
    header = (
        f"# The pages that hold a crop of book {book}, in the order of the book. page is the number of the",
        f"# canvas in {manifest};",
        "# canvas is its id, image its Image API service and width and height its size, as the manifest states",
        "# them. Written by scripts/build_tensho_equivalents.py.",
    )
    canvases = sorted({crop["canvas"] for crop in crops}, key=canvas_number)
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(PAGE_FIELDS)
        for canvas in canvases:
            page = pages[canvas_number(canvas)]
            writer.writerow([canvas_number(canvas), canvas, page["image"], page["width"], page["height"]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("book", type=Path, help="the zip of the book, named after it (TE00024.zip)")
    parser.add_argument("--seal-sources", type=Path, required=True, help="SealSources.txt of Unicode 18.0.0")
    parser.add_argument("--release", default="v2.0", help="the dataset release the book is from")
    parser.add_argument("--out", type=Path, default=DATA, help="the directory of the books' tables")
    args = parser.parse_args(argv)

    name = args.book.stem
    record = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    entry = next((held for held in record["books"] if held["id"] == name), None)
    if entry is None:
        raise SystemExit(f"{SOURCE}: no book {name}")
    if hashlib.sha256(args.book.read_bytes()).hexdigest() != entry["zip_sha256"]:
        raise SystemExit(f"{args.book}: not the zip the source record pins")

    book = Book(args.book, name)
    url = entry["manifest"]
    # CODH names the library's host with `www.`, the library's own manifest without.
    if {row["Manifest URI"].replace("//www.", "//") for row in book.table("source")} != {url}:
        raise SystemExit(f"{name}: its pages are not all of {url}")
    pages = manifest_pages(fetch_manifest(url))

    crops = crops_in_order(book, number=canvas_number, pitch=COLUMN)
    for crop in crops:
        page = pages[canvas_number(crop["canvas"])]
        crop["canvas"] = page["canvas"]
        if crop["x"] + crop["w"] > page["width"] or crop["y"] + crop["h"] > page["height"]:
            raise SystemExit(f"{crop['image']}: a box lies off its page of {page['width']} x {page['height']}")
    place(crops, equivalents(args.seal_sources))
    write(crops, args.out / name / "characters.tsv", name, f"{entry['title']} ({entry['edition']})", args.release)
    write_pages(crops, pages, args.out / name / "pages.tsv", name, url)
    counts = Counter(crop["placed"] for crop in crops)
    print(f"crops            {len(crops):>6}", file=sys.stderr)
    for evidence in ("equivalent", "parent", ""):
        print(f"{evidence or 'no character':<16} {counts[evidence]:>6}", file=sys.stderr)
    print(f"pages            {len({crop['canvas'] for crop in crops}):>6}", file=sys.stderr)
    print(f"-> {args.out / name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
