"""Place the crops of CODH's 説文解字 on Unicode's small seal characters.

Book TE00039 of the 篆書字体データセット cuts the seal forms of the 陳昌治 edition of 説文解字 (同治十二年
刊), the head of each entry and the 或体 below it, and files each under a Han 親字. Unicode 18.0
encodes the small seal characters of the same edition one by one: `SealSources.txt` gives each its
place in the edition (`kSEAL_CCZSrc`, `C-00001` onwards, in the order of the book) and a Han
character as its modern equivalent (`kSEAL_MCJK`).

The crops are read in the order of the book: by page of the 東方學デジタル圖書館 facsimile, by
column from the right, down each column. That sequence and the sequence of seal characters are then
matched, and a crop is placed by one of three kinds of evidence, written to the `placed` column:

- `label`: its 親字 is the seal character's `kSEAL_MCJK`, in the longest common run of the two
  sequences;
- `variant`: between two such crops, its 親字 and the `kSEAL_MCJK` are joined by a row of
  `data/vocab/kanji-variants.tsv` (CODH files 㫄 under 旁);
- `order`: the crops left between two placed ones are exactly as many as the seal characters between
  them, the edition's numbering runs through them without a gap, and they take them in order.

Where an entry's 古文 has the same `kSEAL_MCJK` as its head, or the variant graph joins the 親字 to
both, the crop takes the earlier seal character: the head of an entry stands before its other forms,
and CODH cut the head.

The edition prints some characters in two places (吹 under 口 and again under 欠). Unicode encodes
such a character once and leaves the second place out of `kSEAL_CCZSrc`, a gap in its numbering.
A fourth kind of evidence places the crops that stand at such gaps:

- `repeat`: the crops between two placed ones are exactly as many as the places between them, every
  one of those places a gap, and the crop's 親字 is or is a variant of the `kSEAL_MCJK` of one seal
  character only, which it then shares with the crop at that character's own place.

A crop none of these reaches keeps an empty `placed` and no seal character. The edition's 古文 and
籀文 are seal characters of Unicode that CODH did not cut, so the seal sequence is the longer one.

    uv run scripts/build_tensho_shuowen.py <TE00039 directory or zip> --seal-sources cache/ucd/SealSources.txt \\
        --pages <directory of the facsimile's page images>

It writes `data/tensho/TE00039/characters.tsv` and, with `--pages`, `pages.tsv`, which pins by
checksum the page image of the 東方學デジタル圖書館 facsimile each crop was cut from. It reaches no
network.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import itertools
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = "TE00039"
OUT = ROOT / "data" / "tensho" / BOOK / "characters.tsv"
VARIANTS = ROOT / "data" / "vocab" / "kanji-variants.tsv"
#: Relations of the variant graph that do not say two characters are one character in two shapes.
UNRELATED = frozenset(("spoofing", "non-cognate", "borrowed", "substitute"))
#: Two boxes whose centres are closer than this are in one column: within a column of the book the
#: centres spread over 63 pixels at most, and two columns are never nearer than 119.
COLUMN = 90
PAGE_FIELDS = ("page", "canvas", "file", "sha256", "bytes", "width", "height")
FIELDS = ("seq", "image", "canvas", "column", "x", "y", "w", "h", "parent", "parent_code_point", "char", "code_point",
          "ccz", "equivalent", "placed", "sha256")


class Book:
    """The files of a book of the dataset, from its directory or its zip."""

    def __init__(self, path: Path, book: str = BOOK) -> None:
        self.archive = zipfile.ZipFile(path) if path.is_file() else None
        self.root, self.id = path, book

    def read(self, member: str) -> bytes:
        if self.archive is not None:
            return self.archive.read(f"{self.id}/{member}")
        folder = self.root if (self.root / f"{self.id}_coordinate.csv").is_file() else self.root / self.id
        return (folder / member).read_bytes()

    def table(self, name: str) -> list[dict[str, str]]:
        text = self.read(f"{self.id}_{name}.csv").decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))


def member_of(row: dict[str, str]) -> str:
    """The crop file of a coordinate row, as the dataset names it."""
    return (f"characters/{row['Unicode']}/{row['Unicode']}_{row['Image']}"
            f"_X{int(row['X']):04d}_Y{int(row['Y']):04d}.jpg")


def facsimile_page(canvas: str) -> int:
    """The number of a page of the 東方學デジタル圖書館 facsimile, from its image URL."""
    number = re.search(r"A028(\d+)\.jpg$", canvas)
    if number is None:
        raise SystemExit(f"{canvas}: not a page of the facsimile")
    return int(number.group(1))


def crops_in_order(book: Book, *, number=facsimile_page, pitch: int = COLUMN) -> list[dict]:
    """The crops by page, by column from the right and down each column, each with its page, box and
    file checksum.

    `number` gives the number of a page from its canvas URI. Two boxes whose centres are closer than
    `pitch` pixels are in one column. A row with no width or height (one in the release) takes them
    from its crop file, and a row the table states twice is read once.
    """
    from PIL import Image

    canvases = {row["Image"]: row["Canvas URI"] for row in book.table("source")}
    pages: dict[int, list[dict]] = defaultdict(list)
    seen: set[tuple[str, ...]] = set()
    for row in book.table("coordinate"):
        if tuple(row.values()) in seen:
            continue
        seen.add(tuple(row.values()))
        data = book.read(member_of(row))
        if row["Width"] and row["Height"]:
            width, height = int(row["Width"]), int(row["Height"])
        else:
            width, height = Image.open(io.BytesIO(data)).size
        canvas = canvases[row["Image"]]
        pages[number(canvas)].append({
            "image": row["Image"], "canvas": canvas, "x": int(row["X"]), "y": int(row["Y"]), "w": width, "h": height,
            "parent": row["Unicode"].removeprefix("U+"), "sha256": hashlib.sha256(data).hexdigest(),
        })
    ordered: list[dict] = []
    for page in sorted(pages):
        columns: list[list[dict]] = []
        last = None
        for crop in sorted(pages[page], key=lambda crop: -(crop["x"] + crop["w"] / 2)):
            centre = crop["x"] + crop["w"] / 2
            if last is not None and last - centre < pitch:
                columns[-1].append(crop)
            else:
                columns.append([crop])
            last = centre
        for at, column in enumerate(columns, start=1):
            for crop in column:
                crop["column"] = at
            ordered += sorted(column, key=lambda crop: crop["y"])
    return ordered


def seal_fields(path: Path) -> dict[str, dict[str, str]]:
    """The fields of every seal character of `SealSources.txt`, by its code point."""
    fields: dict[str, dict[str, str]] = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+"):
            point, name, value = line.split("\t")
            fields[point][name] = value.strip()
    return fields


def seal_characters(path: Path) -> list[dict]:
    """The seal characters of the 陳昌治 edition in its order: code point, place, transcription."""
    fields = seal_fields(path)
    held = [{"code_point": point, "ccz": int(values["kSEAL_CCZSrc"].removeprefix("C-")),
             "equivalent": values["kSEAL_MCJK"]}
            for point, values in fields.items() if "kSEAL_CCZSrc" in values]
    return sorted(held, key=lambda seal: seal["ccz"])


def variant_pairs(path: Path) -> set[tuple[str, str]]:
    """The pairs of characters the variant graph joins, both ways, as hex code points."""
    pairs: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t"):
            if row["relation"] in UNRELATED or len(row["a"]) != 1 or len(row["b"]) != 1:
                continue
            a, b = f"{ord(row['a']):04X}", f"{ord(row['b']):04X}"
            pairs.add((a, b))
            pairs.add((b, a))
    return pairs


def _common(crops: list[dict], seals: list[dict], pairs: set[tuple[str, str]]) -> list[tuple[int, int]]:
    """The longest run of crops and seal characters, in order, that the variant graph joins."""
    rows, columns = len(crops), len(seals)
    best = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(columns - 1, -1, -1):
            joined = (crops[i]["parent"], seals[j]["equivalent"]) in pairs
            best[i][j] = best[i + 1][j + 1] + 1 if joined else max(best[i + 1][j], best[i][j + 1])
    found, i, j = [], 0, 0
    while i < rows and j < columns:
        if (crops[i]["parent"], seals[j]["equivalent"]) in pairs and best[i][j] == best[i + 1][j + 1] + 1:
            found.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return found


def place(crops: list[dict], seals: list[dict], pairs: set[tuple[str, str]]) -> None:
    """Give each crop its seal character and the evidence for it, or neither."""

    def take(crop: dict, seal: dict, evidence: str) -> None:
        crop.update(seal=seal, placed=evidence)

    def between(first: tuple[int, int], last: tuple[int, int]) -> None:
        (i1, j1), (i2, j2) = first, last
        if i1 == 0 or j1 == 0 or i2 == len(crops) or j2 == len(seals):
            return  # no placed crop on one side
        if i2 - i1 == j2 - j1 and seals[j2]["ccz"] - seals[j1 - 1]["ccz"] == j2 - j1 + 1:
            for offset in range(i2 - i1):
                take(crops[i1 + offset], seals[j1 + offset], "order")

    matcher = difflib.SequenceMatcher(
        None, [crop["parent"] for crop in crops], [seal["equivalent"] for seal in seals], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                take(crops[i1 + offset], seals[j1 + offset], "label")
            continue
        at = (i1, j1)
        for i, j in _common(crops[i1:i2], seals[j1:j2], pairs):
            between(at, (i1 + i, j1 + j))
            take(crops[i1 + i], seals[j1 + j], "variant")
            at = (i1 + i + 1, j1 + j + 1)
        between(at, (i2, j2))
    _prefer_heads(crops, seals, pairs)


def _prefer_heads(crops: list[dict], seals: list[dict], pairs: set[tuple[str, str]]) -> None:
    """Move a crop to the earliest free seal character its 親字 answers, after the crop before it."""
    index = {seal["code_point"]: at for at, seal in enumerate(seals)}
    for before, crop in itertools.pairwise(crops):
        if crop.get("placed") not in ("label", "variant") or "seal" not in before:
            continue
        for at in range(index[before["seal"]["code_point"]] + 1, index[crop["seal"]["code_point"]]):
            equivalent = seals[at]["equivalent"]
            if equivalent == crop["parent"] or (crop["placed"] == "variant" and (crop["parent"], equivalent) in pairs):
                crop["seal"] = seals[at]
                break


def place_repeats(crops: list[dict], seals: list[dict], pairs: set[tuple[str, str]]) -> None:
    """Place the crops at the places Unicode leaves out, on the character encoded at its other place."""
    numbered = {seal["ccz"] for seal in seals}
    by_equivalent: dict[str, list[dict]] = defaultdict(list)
    for seal in seals:
        by_equivalent[seal["equivalent"]].append(seal)

    def shown(crop: dict) -> dict | None:
        found = by_equivalent.get(crop["parent"]) or [
            seal for seal in seals if (crop["parent"], seal["equivalent"]) in pairs]
        return found[0] if len(found) == 1 else None

    start = 0
    while start < len(crops):
        if "seal" in crops[start]:
            start += 1
            continue
        end = start
        while end < len(crops) and "seal" not in crops[end]:
            end += 1
        if 0 < start and end < len(crops):
            places = range(crops[start - 1]["seal"]["ccz"] + 1, crops[end]["seal"]["ccz"])
            found = [shown(crop) for crop in crops[start:end]]
            if len(places) == end - start and not numbered & set(places) and all(found):
                for crop, place_, seal in zip(crops[start:end], places, found, strict=True):
                    crop.update(seal=seal, placed="repeat", place=place_)
        start = end


def write(crops: list[dict], target: Path, release: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# The seal forms of 説文解字 (陳昌治 edition) cut by CODH, 篆書字体データセット book {BOOK} ({release}),",
        "# each on the small seal character of Unicode 18.0 it shows. Written by scripts/build_tensho_shuowen.py.",
        "# seq is the crop's place in the order of the book; image, x, y, w, h and parent (the 親字) are CODH's,",
        "# the box in pixels of CODH's page image; column counts the page's columns of crops from the right;",
        "# canvas the page of the 東方學デジタル圖書館 facsimile; char, code_point and equivalent (kSEAL_MCJK)",
        "# are from SealSources.txt of Unicode 18.0.0; ccz is the crop's place in the edition, the character's",
        "# kSEAL_CCZSrc except on a repeat, whose place Unicode numbers no character at; placed is the evidence:",
        "# label, variant, order or repeat, empty for a crop no evidence places; sha256 is the crop file's.",
    )
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        for seq, crop in enumerate(crops, start=1):
            seal = crop.get("seal")
            point = int(seal["code_point"].removeprefix("U+"), 16) if seal else None
            writer.writerow([
                seq, crop["image"], crop["canvas"], crop["column"], crop["x"], crop["y"], crop["w"], crop["h"],
                chr(int(crop["parent"], 16)), f"U+{crop['parent']}",
                chr(point) if point else "", seal["code_point"] if seal else "",
                f"C-{crop.get('place', seal['ccz']):05d}" if seal else "",
                chr(int(seal["equivalent"], 16)) if seal else "", crop.get("placed", ""), crop["sha256"],
            ])


def write_pages(crops: list[dict], folder: Path, target: Path) -> None:
    """Pin the page image of every page that holds a crop: its file, checksum and size."""
    from PIL import Image

    canvases = sorted({crop["canvas"] for crop in crops}, key=lambda canvas: canvas.rsplit("/", 1)[-1])
    header = (
        f"# The pages of the 東方學デジタル圖書館 facsimile that hold a crop of book {BOOK}, in the order of the",
        "# book. page is the number in the file name; sha256, bytes, width and height are those of the image",
        "# the library served when the table was built. Written by scripts/build_tensho_shuowen.py.",
    )
    rows = []
    for canvas in canvases:
        name = canvas.rsplit("/", 1)[-1]
        data = (folder / name).read_bytes()
        width, height = Image.open(io.BytesIO(data)).size
        rows.append([facsimile_page(canvas), canvas, name, hashlib.sha256(data).hexdigest(), len(data), width, height])
    # Written only once every image has been read, so a missing one leaves the table as it was.
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(PAGE_FIELDS)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("book", type=Path, help=f"the {BOOK} directory or {BOOK}.zip of the dataset")
    parser.add_argument("--seal-sources", type=Path, required=True, help="SealSources.txt of Unicode 18.0.0")
    parser.add_argument("--variants", type=Path, default=VARIANTS)
    parser.add_argument("--release", default="v2.0", help="the dataset release the book is from")
    parser.add_argument("--pages", type=Path, help="directory of the facsimile's page images, to write pages.tsv")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    crops = crops_in_order(Book(args.book))
    seals = seal_characters(args.seal_sources)
    pairs = variant_pairs(args.variants)
    place(crops, seals, pairs)
    place_repeats(crops, seals, pairs)
    taken = Counter(crop["seal"]["code_point"] for crop in crops if crop.get("placed") in ("label", "variant", "order"))
    twice = [point for point, count in taken.items() if count > 1]
    if twice:
        raise SystemExit(f"seal characters placed on two crops: {', '.join(twice[:10])}")
    if args.pages:
        write_pages(crops, args.pages, args.out.with_name("pages.tsv"))
    write(crops, args.out, args.release)
    counts = Counter(crop.get("placed", "") for crop in crops)
    print(f"crops            {len(crops):>6}", file=sys.stderr)
    for evidence in ("label", "variant", "order", "repeat", ""):
        print(f"{evidence or 'not placed':<16} {counts[evidence]:>6}", file=sys.stderr)
    print(f"seal characters  {len(seals):>6}, without a crop {len(seals) - len(taken)}", file=sys.stderr)
    print(f"-> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
