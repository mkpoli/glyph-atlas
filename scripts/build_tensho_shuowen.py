"""Place the crops of CODH's 説文解字 on Unicode's small seal characters.

Book TE00039 of the 篆書字体データセット cuts the seal forms of the 陳昌治 edition of 説文解字 (同治十二年
刊), the head of each entry and the 或体 below it, and files each under a Han 親字. Unicode 18.0
encodes the small seal characters of the same edition one by one: `SealSources.txt` gives each its
place in the edition (`kSEAL_CCZSrc`, `C-00001` onwards, in the order of the book) and the Han
character that transcribes its structure (`kSEAL_MCJK`).

The crops are read in the order of the book: by page of the 東方學デジタル圖書館 facsimile, by
column from the right, down each column. That sequence and the sequence of seal characters are then
matched, and a crop is placed by one of three kinds of evidence, written to the `placed` column:

- `label`: its 親字 is the seal character's `kSEAL_MCJK`, in the longest common run of the two
  sequences;
- `variant`: between two such crops, its 親字 and the `kSEAL_MCJK` are joined by a row of
  `data/vocab/kanji-variants.tsv` (CODH files 㫄 under 旁);
- `order`: the crops left between two placed ones are exactly as many as the seal characters between
  them, and take them in order.

The edition prints some characters in two places (吹 under 口 and again under 欠). Unicode encodes
such a character once and leaves the second place out of `kSEAL_CCZSrc`, a gap in its numbering.
A fourth kind of evidence places the crops that stand at such gaps:

- `repeat`: the crops between two placed ones are exactly as many as the places between them, every
  one of those places a gap, and the crop's 親字 is or is a variant of the `kSEAL_MCJK` of one seal
  character only, which it then shares with the crop at that character's own place.

A crop none of these reaches keeps an empty `placed` and no seal character. The edition's 古文 and
籀文 are seal characters of Unicode that CODH did not cut, so the seal sequence is the longer one.

    uv run scripts/build_tensho_shuowen.py <TE00039 directory or zip> --seal-sources cache/ucd/SealSources.txt

It writes `data/tensho/TE00039/characters.tsv` and reaches no network.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
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
#: Head characters of one column stand within this many pixels of each other, measured at the box
#: centre; the columns of the edition are about 160 pixels apart.
COLUMN = 70
FIELDS = ("seq", "image", "canvas", "x", "y", "w", "h", "parent", "parent_code_point", "char", "code_point",
          "ccz", "equivalent", "placed", "sha256")


class Book:
    """The files of the book, from its directory or its zip."""

    def __init__(self, path: Path) -> None:
        self.archive = zipfile.ZipFile(path) if path.is_file() else None
        self.root = path

    def read(self, member: str) -> bytes:
        if self.archive is not None:
            return self.archive.read(f"{BOOK}/{member}")
        folder = self.root if (self.root / f"{BOOK}_coordinate.csv").is_file() else self.root / BOOK
        return (folder / member).read_bytes()

    def table(self, name: str) -> list[dict[str, str]]:
        text = self.read(f"{BOOK}_{name}.csv").decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))


def member_of(row: dict[str, str]) -> str:
    """The crop file of a coordinate row, as the dataset names it."""
    return (f"characters/{row['Unicode']}/{row['Unicode']}_{row['Image']}"
            f"_X{int(row['X']):04d}_Y{int(row['Y']):04d}.jpg")


def crops_in_order(book: Book) -> list[dict]:
    """The crops in the order of the book, each with its page, box and file checksum.

    A row with no width or height (one in the release) takes them from its crop file.
    """
    from PIL import Image

    canvases = {row["Image"]: row["Canvas URI"] for row in book.table("source")}
    pages: dict[int, list[dict]] = defaultdict(list)
    for row in book.table("coordinate"):
        data = book.read(member_of(row))
        if row["Width"] and row["Height"]:
            width, height = int(row["Width"]), int(row["Height"])
        else:
            width, height = Image.open(io.BytesIO(data)).size
        canvas = canvases[row["Image"]]
        number = re.search(r"A028(\d+)\.jpg$", canvas)
        if number is None:
            raise SystemExit(f"{canvas}: not a page of the facsimile")
        pages[int(number.group(1))].append({
            "image": row["Image"], "canvas": canvas, "x": int(row["X"]), "y": int(row["Y"]), "w": width, "h": height,
            "parent": row["Unicode"].removeprefix("U+"), "sha256": hashlib.sha256(data).hexdigest(),
        })
    ordered: list[dict] = []
    for number in sorted(pages):
        columns: list[tuple[float, list[dict]]] = []
        for crop in sorted(pages[number], key=lambda crop: -(crop["x"] + crop["w"] / 2)):
            centre = crop["x"] + crop["w"] / 2
            if columns and abs(columns[-1][0] - centre) < COLUMN:
                columns[-1][1].append(crop)
            else:
                columns.append((centre, [crop]))
        for _, column in columns:
            ordered += sorted(column, key=lambda crop: crop["y"])
    return ordered


def seal_characters(path: Path) -> list[dict]:
    """The seal characters of the 陳昌治 edition in its order: code point, place, transcription."""
    fields: dict[str, dict[str, str]] = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("U+"):
            point, name, value = line.split("\t")
            fields[point][name] = value.strip()
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
        if i2 - i1 == j2 - j1:
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
                seq, crop["image"], crop["canvas"], crop["x"], crop["y"], crop["w"], crop["h"],
                chr(int(crop["parent"], 16)), f"U+{crop['parent']}",
                chr(point) if point else "", seal["code_point"] if seal else "",
                f"C-{crop.get('place', seal['ccz']):05d}" if seal else "",
                chr(int(seal["equivalent"], 16)) if seal else "", crop.get("placed", ""), crop["sha256"],
            ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("book", type=Path, help=f"the {BOOK} directory or {BOOK}.zip of the dataset")
    parser.add_argument("--seal-sources", type=Path, required=True, help="SealSources.txt of Unicode 18.0.0")
    parser.add_argument("--variants", type=Path, default=VARIANTS)
    parser.add_argument("--release", default="v2.0", help="the dataset release the book is from")
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
