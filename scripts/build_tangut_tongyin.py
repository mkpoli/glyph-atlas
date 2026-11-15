"""Place the head characters of 同音 (甲種本, Инв. No. 207) on the plates of 俄藏黑水城文獻 vol. 7.

The volume prints one folio to a plate and two plates to a page: folio `n` is plate `(n - 1) % 2` of
printed page `(n + 1) // 2`. A folio is two half-folios of seven columns, side A on the right, and a
column holds up to eight head characters with their notes in small writing beside them.

nkay0's rhyme-dictionary table gives every head character's folio, side, column and position in this
edition (its `旧版同音` column), so the cutter knows which character stands in each cell and only has
to find it. It fits the printed rule lines to get the columns, slides the expected character's glyph
down each column, and chooses the eight places together, in order and near their rows. The match
score of each place is kept; the importer takes the places that score well and are of ordinary size.

    uv run --extra marks scripts/build_tangut_tongyin.py \
        --images <the volume's page images, 00000040.jpg onwards> \
        --table "<tangut rhyme dictionaries data.csv>"

It writes `pages.tsv`, `columns.tsv` and `characters.tsv` under `data/tangut/tongyin/`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "tangut" / "tongyin"
FONT = Path("/usr/share/fonts/truetype/noto/NotoSerifTangut-Regular.ttf")
FIRST_IMAGE = 40          # the scan of printed page 1
FOLIOS = range(1, 57)     # the plates are numbered 56-1 to 56-56
COLUMNS, ROWS = 7, 8
ACCEPT = 0.28             # match score from which a place is taken as found
FAINT = 0.6               # share of a folio's usual ink under which a box is taken to sit on paper
SIZES = (0.60, 0.68, 0.76)  # glyph width tried, as a share of the column


@dataclass(frozen=True)
class Place:
    x: int
    y: int
    w: int
    h: int
    score: float


def positions(table: Path) -> dict[tuple[int, str, int], dict[int, str]]:
    """Head characters by (folio, side, column), each a map from position to character."""
    cells: dict[tuple[int, str, int], dict[int, str]] = defaultdict(dict)
    with table.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            place = row["旧版同音"].strip()
            if len(place) == 5 and place[:2].isdigit() and place[2] in "AB" and place[3:].isdigit():
                cells[(int(place[:2]), place[2], int(place[3]))][int(place[4])] = row["文字"]
    return cells


def plates(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """The photographs on a printed page, top first: large regions darker than the paper."""
    small = cv2.blur(gray[::8, ::8], (9, 9))
    mask = cv2.morphologyEx((small < 225).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    found = [(int(x) * 8, int(y) * 8, int(x + w) * 8, int(y + h) * 8)
             for x, y, w, h, _ in stats[1:count] if w * h >= 0.08 * small.size]
    return sorted(found, key=lambda rect: rect[1])


def ink_of(plate: np.ndarray) -> np.ndarray:
    """Dark strokes over the local paper tone, which evens out a plate's stains."""
    smooth = cv2.GaussianBlur(plate, (0, 0), 2.0)
    paper = cv2.GaussianBlur(smooth, (0, 0), 40)
    return np.clip(paper.astype(np.float32) - smooth, 0, 255)


def long_strokes(ink: np.ndarray, *, vertical: bool, length: int) -> np.ndarray:
    kernel = np.ones((length, 1), np.uint8) if vertical else np.ones((1, length), np.uint8)
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)


def frame_rows(ink: np.ndarray) -> tuple[int, int]:
    """The top and bottom frame lines: the strongest long horizontal strokes of the outer fifths."""
    profile = long_strokes(ink, vertical=False, length=ink.shape[1] // 6).mean(axis=1)
    fifth = len(profile) // 5
    return int(np.argmax(profile[:fifth])), int(len(profile) - fifth + np.argmax(profile[-fifth:]))


def smoothed(profile: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(profile[None, :].astype(np.float32), (0, 0), sigma)[0]


def snap(profile: np.ndarray, lines: np.ndarray, reach: float) -> list[int]:
    """Each fitted line moved to the strongest stroke within `reach` pixels."""
    near = smoothed(profile, 1.5)
    out = []
    for line in lines:
        low = max(0, int(line - reach))
        high = max(low + 1, min(len(near), int(line + reach)))
        out.append(low + int(np.argmax(near[low:high])))
    return out


def from_fold(profile: np.ndarray, width: int) -> dict[str, list[int]]:
    """Rule lines stepped outwards from the fold, each half with its own pitch."""
    middle = smoothed(profile, 4.0)
    low, high = int(width * 0.40), int(width * 0.60)
    fold = low + int(np.argmax(middle[low:high]))
    near = smoothed(profile, 2.0)
    fits = {}
    for side, direction in (("B", -1), ("A", 1)):
        best: tuple[float, np.ndarray | None] = (-1.0, None)
        for pitch in np.arange(width / 18.5, width / 14.5, 0.5):
            for offset in range(int(0.2 * pitch)):
                lines = fold + direction * (offset + pitch * np.arange(COLUMNS + 1))
                if lines.min() < 0 or lines.max() >= width:
                    continue
                value = float(near[lines.astype(int)].sum())
                if value > best[0]:
                    best = (value, lines)
        lines = best[1] if best[1] is not None else fold + direction * (width / 16.5) * np.arange(COLUMNS + 1)
        fits[side] = snap(profile, np.sort(lines), abs(lines[1] - lines[0]) * 0.06)
    return fits


def from_combs(profile: np.ndarray, width: int) -> dict[str, list[int]]:
    """Two evenly spaced combs of rule lines with one pitch, a fold apart, placed anywhere."""
    near = np.concatenate([smoothed(profile, 2.0), np.zeros(width, np.float32)])
    best = (-1.0, 0, 0, width / 16.5)
    for pitch in np.arange(width / 18.5, width / 15.0, 0.5):
        offsets = (pitch * np.arange(COLUMNS + 1)).astype(int)
        comb = sum(near[offset:offset + width] for offset in offsets)
        for gap in range(int(0.04 * pitch), int(0.7 * pitch)):
            apart = int(COLUMNS * pitch) + gap
            limit = int(width - apart - COLUMNS * pitch)
            if limit <= 0:
                continue
            total = comb[:width - apart] + comb[apart:width]
            start = int(np.argmax(total[:limit]))
            if total[start] > best[0]:
                best = (float(total[start]), start, start + apart, float(pitch))
    _, left, right, pitch = best
    steps = pitch * np.arange(COLUMNS + 1)
    return {"B": snap(profile, left + steps, pitch * 0.06), "A": snap(profile, right + steps, pitch * 0.06)}


_GLYPHS: dict[tuple[str, int], np.ndarray] = {}


def glyph(character: str, size: int, font: Path) -> np.ndarray:
    """The character's glyph at `size`, cut to its ink and thickened towards a woodblock's weight."""
    key = (character, size)
    if key not in _GLYPHS:
        canvas = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(canvas).text((size // 2, size // 2), character, font=ImageFont.truetype(str(font), size), fill=255)
        drawn = np.asarray(canvas)
        rows, columns = np.nonzero(drawn)
        drawn = drawn[rows.min():rows.max() + 1, columns.min():columns.max() + 1]
        drawn = cv2.dilate(drawn, np.ones((3, 3), np.uint8))
        _GLYPHS[key] = cv2.GaussianBlur(drawn, (0, 0), 1.2).astype(np.float32)
    return _GLYPHS[key]


def cover(character: str, font: Path) -> float:
    """The share of its box a character's glyph inks: few strokes ink little."""
    return max(0.05, float(glyph(character, 64, font).mean()) / 255)


def place(strip: np.ndarray, glyphs: list[np.ndarray], rows: list[int], pitch: float) -> list[Place]:
    """Where each glyph sits down a column strip.

    Every glyph is correlated with the strip. The places are then chosen together: in order, at least
    0.55 of a row apart, each drawn towards the row the table gives it.
    """
    height, width = strip.shape
    count = len(glyphs)
    gap = int(0.55 * pitch)
    scores, columns = [], []
    for template in glyphs:
        if template.shape[0] >= height or template.shape[1] >= width:
            scores.append(None)
            columns.append(None)
            continue
        response = cv2.matchTemplate(strip, template, cv2.TM_CCOEFF_NORMED)
        scores.append(response.max(axis=1))
        columns.append(response.argmax(axis=1))
    best = np.full((count, height), -1e9, np.float32)
    back = np.zeros((count, height), np.int32)
    for index in range(count):
        if scores[index] is None:
            continue
        length = len(scores[index])
        expected = (rows[index] - 1 + 0.42) * height / ROWS - glyphs[index].shape[0] / 2
        value = scores[index] - 2.5 * ((np.arange(length) - expected) / pitch) ** 2
        if index == 0:
            best[0, :length] = value
            continue
        running = np.maximum.accumulate(best[index - 1])
        source = np.zeros(height, np.int32)
        top, at = -1e9, 0
        for y in range(height):
            if best[index - 1, y] > top:
                top, at = best[index - 1, y], y
            source[y] = at
        best[index, gap:length] = running[:length - gap] + value[gap:]
        back[index, gap:length] = source[:length - gap]
    tops = [int(np.argmax(best[count - 1]))]
    for index in range(count - 1, 0, -1):
        tops.append(int(back[index, tops[-1]]))
    tops.reverse()
    found = []
    for index, top in enumerate(tops):
        if scores[index] is None or top >= len(scores[index]):
            found.append(Place(0, top, 1, 1, 0.0))
            continue
        template = glyphs[index]
        found.append(Place(int(columns[index][top]), top, template.shape[1], template.shape[0], float(scores[index][top])))
    return found


def tighten(strip: np.ndarray, found: Place, grow: float = 0.18) -> Place:
    """The box fitted to the ink it sits on, each edge moving by at most `grow` of the glyph's size."""
    height, width = strip.shape
    dx, dy = int(found.w * grow), int(found.h * grow)
    left, top = max(0, found.x - dx), max(0, found.y - dy)
    right, bottom = min(width, found.x + found.w + dx), min(height, found.y + found.h + dy)
    window = strip[top:bottom, left:right]
    if window.size == 0:
        return found
    mask = (window > max(12.0, 0.45 * float(np.percentile(window, 97)))).astype(np.uint8)
    core = np.zeros_like(mask)
    core[found.y - top + found.h // 5:found.y - top + found.h - found.h // 5,
         found.x - left + found.w // 5:found.x - left + found.w - found.w // 5] = 1
    count, labels = cv2.connectedComponents(cv2.dilate(mask, np.ones((5, 5), np.uint8)))
    keep = [label for label in range(1, count) if (core & (labels == label)).any()]
    rows, columns = np.nonzero(np.isin(labels, keep) & (mask > 0))
    if len(rows) < 30:
        return found
    return Place(left + int(columns.min()), top + int(rows.min()),
                 int(columns.max() - columns.min() + 1), int(rows.max() - rows.min() + 1), found.score)


def cut_folio(plate: np.ndarray, folio: int, cells: dict, font: Path) -> tuple[list[dict], list[dict]]:
    """The columns and characters of one folio, in the plate's own pixels."""
    ink = ink_of(plate)
    width = ink.shape[1]
    top, bottom = frame_rows(ink)
    pitch = (bottom - top) / ROWS
    profile = long_strokes(ink[top:bottom], vertical=True, length=max(3, (bottom - top) // 3)).mean(axis=0)
    fits = [from_fold(profile, width), from_combs(profile, width)]
    columns, characters = [], []
    for side in ("A", "B"):
        chosen: tuple[float, list] = (-1e9, [])
        for fit in fits:
            lines, total, held = fit[side], 0.0, []
            for column in range(1, COLUMNS + 1):             # column 1 is the rightmost
                left, right = max(0, lines[COLUMNS - column]), min(width, lines[COLUMNS + 1 - column])
                cell = cells.get((folio, side, column))
                if not cell or right - left < 20:
                    continue
                strip = ink[top:bottom, left:right]
                rows = sorted(cell)
                best: tuple[float, list[Place]] = (-1e9, [])
                for share in SIZES:
                    size = int(min((right - left) * share, pitch * 0.62))
                    found = place(strip, [glyph(cell[row], size, font) for row in rows], rows, pitch)
                    score = sum(item.score for item in found)
                    if score > best[0]:
                        best = (score, found)
                total += best[0]
                held.append((column, left, right, rows, [tighten(strip, item) for item in best[1]]))
            if total > chosen[0]:
                chosen = (total, held)
        for column, left, right, rows, found in chosen[1]:
            cell = cells[(folio, side, column)]
            columns.append({"folio": folio, "side": side, "column": column, "x": left, "y": top,
                            "w": right - left, "h": bottom - top, "text": "".join(cell[row] for row in rows)})
            for row, item in zip(rows, found, strict=True):
                boxed = ink[top + item.y:top + item.y + item.h, left + item.x:left + item.x + item.w]
                characters.append({"folio": folio, "side": side, "column": column, "position": row,
                                   "char": cell[row], "x": left + item.x, "y": top + item.y,
                                   "w": item.w, "h": item.h, "score": round(item.score, 3),
                                   "ink": round(float(boxed.mean()) / cover(cell[row], font) if boxed.size else 0.0, 1)})
    return columns, characters


def grade(characters: list[dict]) -> None:
    """Mark each place `ok`, `low-score`, `size` or `faint`.

    A box far from its folio's usual size sits on a part of a character or on two; one holding far
    less ink than the folio's others sits on paper beside a character.
    """
    by_folio: dict[int, list[dict]] = defaultdict(list)
    for row in characters:
        by_folio[row["folio"]].append(row)
    for rows in by_folio.values():
        sound = [row for row in rows if row["score"] >= ACCEPT] or rows
        width = float(np.median([row["w"] for row in sound]))
        height = float(np.median([row["h"] for row in sound]))
        ink = float(np.median([row["ink"] for row in sound]))
        for row in rows:
            odd = not (0.66 * width <= row["w"] <= 1.45 * width and 0.66 * height <= row["h"] <= 1.45 * height)
            row["quality"] = ("low-score" if row["score"] < ACCEPT else "size" if odd
                              else "faint" if row["ink"] < FAINT * ink else "ok")


def write(path: Path, header: list[str], fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for line in header:
            handle.write(f"# {line}\n")
        writer = csv.DictWriter(handle, fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--images", type=Path, required=True, help="the volume's page images, named 00000040.jpg onwards")
    parser.add_argument("--table", type=Path, required=True, help="nkay0's tangut rhyme dictionaries data.csv")
    parser.add_argument("--font", type=Path, default=FONT, help="Noto Serif Tangut")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--overlays", type=Path, help="directory for one checking image per folio")
    args = parser.parse_args()

    cells = positions(args.table)
    pages, columns, characters = [], [], []
    for number in sorted({(folio + 1) // 2 for folio in FOLIOS}):
        file = args.images / f"{FIRST_IMAGE + number - 1:08d}.jpg"
        data = file.read_bytes()
        gray = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        rects = plates(gray)
        pages.append({"page": number, "file": file.name, "sha256": hashlib.sha256(data).hexdigest(),
                      "bytes": len(data), "width": gray.shape[1], "height": gray.shape[0],
                      "folios": " ".join(str(2 * number - 1 + index) for index in range(len(rects))),
                      "plates": " ".join(",".join(map(str, (x0, y0, x1 - x0, y1 - y0))) for x0, y0, x1, y1 in rects)})
        for index, (x0, y0, x1, y1) in enumerate(rects[:2]):
            folio = 2 * number - 1 + index
            if not any(key[0] == folio for key in cells):
                continue
            folio_columns, folio_characters = cut_folio(gray[y0:y1, x0:x1], folio, cells, args.font)
            for row in folio_columns + folio_characters:
                row.update(page=number, x=row["x"] + x0, y=row["y"] + y0)
            columns += folio_columns
            characters += folio_characters
            print(f"folio {folio:2d}: {len(folio_characters):3d} characters, "
                  f"{sum(row['score'] >= ACCEPT for row in folio_characters):3d} at {ACCEPT} or more")
    grade(characters)
    for row in characters:
        row["codepoint"] = f"U+{ord(row['char']):04X}"

    if args.overlays:
        args.overlays.mkdir(parents=True, exist_ok=True)
        colours = {"ok": (0, 170, 0), "size": (0, 140, 255), "faint": (200, 0, 200), "low-score": (0, 0, 255)}
        for page in pages:
            image = cv2.imread(str(args.images / page["file"]))
            for row in columns:
                if row["page"] == page["page"]:
                    cv2.rectangle(image, (row["x"], row["y"]), (row["x"] + row["w"], row["y"] + row["h"]), (255, 160, 0), 1)
            for row in characters:
                if row["page"] == page["page"]:
                    cv2.rectangle(image, (row["x"], row["y"]), (row["x"] + row["w"], row["y"] + row["h"]), colours[row["quality"]], 2)
            cv2.imwrite(str(args.overlays / f"page-{page['page']:03d}.jpg"), image)

    args.out.mkdir(parents=True, exist_ok=True)
    table_sha = hashlib.sha256(args.table.read_bytes()).hexdigest()
    source = [
        "同音 甲種本 (Инв. No. 207) on the plates of 俄藏黑水城文獻 vol. 7 (上海古籍出版社, 1997), printed pages 1-28.",
        "Boxes are pixels of the page image, origin top left. Written by scripts/build_tangut_tongyin.py.",
    ]
    write(args.out / "pages.tsv", source, ["page", "file", "sha256", "bytes", "width", "height", "folios", "plates"], pages)
    write(args.out / "columns.tsv", source + ["A column's text is its head characters in order; the notes beside them are not transcribed."],
          ["folio", "side", "column", "page", "x", "y", "w", "h", "text"], columns)
    write(args.out / "characters.tsv", source + [
        ("Character and position: nkay0, Tangut-rhyme-dictionaries-data, column 旧版同音 "
         f"(https://github.com/nkay0/Tangut-rhyme-dictionaries-data; file sha256 {table_sha})."),
        f"score is the match of the expected glyph (Noto Serif Tangut) at the box; quality is ok, low-score (under {ACCEPT}), size or faint; ink is the stroke darkness in the box over the share of its box the glyph inks.",
    ], ["folio", "side", "column", "position", "char", "codepoint", "page", "x", "y", "w", "h", "score", "ink", "quality"], characters)
    counts = defaultdict(int)
    for row in characters:
        counts[row["quality"]] += 1
    print(f"{len(pages)} pages, {len(columns)} columns, {len(characters)} characters: "
          + ", ".join(f"{count} {name}" for name, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
