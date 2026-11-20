#!/usr/bin/env python3
"""Cut the headwords of the 澤存堂本 廣韻 from images of its openings, labelled by nk2028's tables.

    uv run scripts/cut_guangyun_headwords.py photographs work/guangyun-photographs --table cache/tshet-uinh-data \
        --images cache/tshet-uinh-images --report work/guangyun-photographs.report.json
    uv run scripts/cut_guangyun_headwords.py ioc work/guangyun-ioc --table cache/tshet-uinh-data \
        --images cache/commons/guangyun-ioc --report work/guangyun-ioc.report.json

`COPY` is one of the copies `data/sources/guangyun-zecuntang.yaml` lists. `--table` is a checkout of
the table's repository at the commit that file pins; `--images` is the copy's images: a checkout of
its repository at the pinned commit, or the folder holding its PDF files as Wikimedia Commons names
them. Each opening is enlarged as the copy asks, run through the character detector at the copy's
scale, split at its gutter, and each half-leaf's headwords placed by `glyph_atlas.leaf_text.place` from
the columns, positions and gloss lengths the tables give. The atlas classifier and, for characters
it has no class for, NDLkotenOCR judge a headword on its box; their verdicts steer the pairing.

A headword is kept when a detected box stands at its place, the reader does not refuse it, its
column's gaps fit the text within the copy's `fit` rows (and, for a copy marked `anchored`, a
headword of the column was read where it stands), and its half-leaf is borne out: at least `CONFIRM`
of its headwords judged, and `AGREED` of those read. The label always comes from the table.

`OUTPUT` becomes a dataset: one document per volume, a page per opening with a kept headword, a
line and a unit per headword, their boxes on the opening's image at its own size. `--volumes` and
`--openings` bound a run.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import yaml
from PIL import Image

from glyph_atlas import align, images, leaf_text, refs, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.glossary import Glyph, agrees
from glyph_atlas.importers.honkoku_data import clone_revision
from glyph_atlas.registry import SOURCES
from glyph_atlas.review.suggestions import Recognizer, classifier_path, decode, preprocess
from glyph_atlas.schema import (
    Box,
    Classification,
    Dating,
    Document,
    Licence,
    Line,
    Page,
    Register,
    ReviewState,
    Rights,
    Script,
    Unit,
)

RUN = Path("models/align/runs/collection-v2.yaml")
SOURCE = "guangyun-zecuntang"
CORPUS = "guangyun"
COLUMNS = 10
#: A half-leaf is borne out when the readers judge at least this many of its headwords and read this share of them.
CONFIRM = 3
AGREED = 0.6
_PLACE = re.compile(r"^w(?P<volume>\d)(?P<leaf>\d\d)(?P<side>[ab])(?P<column>\d\d)(?P<position>\d\d)")
_MARKS = re.compile(r"[〈〉［］｛｝（）〖〗｟｠〘〙]")
SEGMENTATION = ("columns found from the detector's headword-sized boxes; each column's headwords paired in order with "
                "its boxes by the gaps their gloss lengths predict, steered by the classifier and NDLkotenOCR")


def read_entries(table: Path) -> dict[tuple[str, int, str], dict[int, list[tuple[int, str, leaf_text.Head, dict[str, str]]]]]:
    """Every headword by half-leaf and column: its position, its id, its character with its gloss length, and its row."""
    gloss: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}
    heads_seen: set[str] = set()
    with (table / "韻書" / "廣韻.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            length = len(_MARKS.sub("", row["釋義"]))
            if row["小韻號"] not in heads_seen:
                # The first headword of a 小韻 also carries the ○ before it, its 反切, 切 and the count.
                heads_seen.add(row["小韻號"])
                length += len(_MARKS.sub("", row["反切"])) + 2
            gloss[(row["小韻號"].rstrip("abc"), row["小韻字號"])] = (length, row)
    found: dict[tuple[str, int, str], dict[int, list]] = defaultdict(lambda: defaultdict(list))
    with (table / "src" / "字序表.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            place = _PLACE.match(row["sbgy_id"])
            if not place or len(row["字"]) != 1:
                continue
            length, entry = gloss.get((row["原書小韻號"], row["小韻字號"]), (0, {}))
            found[(place["volume"], int(place["leaf"]), place["side"])][int(place["column"])].append(
                (int(place["position"]), row["sbgy_id"], leaf_text.Head(row["字"], length), entry))
    return found


def readers(classifier: Classifier, second: Recognizer):
    """A function giving, for one opening and its boxes, `reads(char, box)`: True, False or None."""
    codes = set(classifier.classes)
    alphabet = set(second.alphabet)
    wanted = second.sequence.get_inputs()[0]

    def forms(char: str) -> set[str]:
        return {form for form in refs.equivalents(char, "align-v1") if len(form) == 1} | {char}

    def of(image: Image.Image, boxes: list[Box], unit: float):
        large = [b for b in boxes if b.h >= leaf_text.WIDE * unit and b.w >= leaf_text.WIDE * unit]
        crops = {(b.x, b.y, b.w, b.h): image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in large}
        ranked = classifier.probabilities_many(list(crops.values())) if crops else []
        top = {key: [classifier.classes[int(i)] for i in row.argsort()[::-1][:5]] for key, row in zip(crops, ranked, strict=True)}
        text: dict[tuple[int, int, int, int], str] = {}

        def reads(char: str, box: Box) -> bool | None:
            key = (box.x, box.y, box.w, box.h)
            if key not in top:
                return None
            said = agrees(Glyph(char), top[key], codes)
            if said:
                return True
            written = forms(char)
            if not written & alphabet:
                return said
            if key not in text:
                pixels = preprocess(crops[key], (wanted.shape[3], wanted.shape[2]))
                decoded = decode(second.sequence.run(None, {wanted.name: pixels})[0], second.alphabet)
                text[key] = decoded[0]["text"] if decoded else ""
            if len(text[key]) == 1 and text[key] in written:
                return True
            return said if said is not None else (False if len(text[key]) == 1 else None)

        return reads, top

    return of


def document_of(source: dict, copy: dict, volume: dict) -> Document:
    return Document(
        id=f"{CORPUS}-{copy['id']}:{volume['volume']}",
        title=f"{source['title']}{volume['label']}",
        source_refs={SOURCE: volume["label"], "images": copy["repository"]},
        holder=copy["holder"],
        shelfmark=copy.get("shelfmark"),
        production="printed/woodblock",
        origin="china",
        genre=["dictionary"],
        text_register=Register(source["register"]),
        image_kind=copy["image_kind"],
        dating=[Dating(literal=str(d["literal"]), start=d["start"], end=d["end"], kind=d["kind"], evidence=d["evidence"])
                for d in source["dating"]],
        image_rights=Rights(licence=Licence(copy["licence"]), holder=copy["holder"], attribution=copy["attribution"],
                            evidence=copy.get("licence_evidence", copy["repository"])),
        text_rights=Rights(licence=Licence(source["licence"]), holder=source["publisher"],
                           attribution=source["attribution"], evidence=source["licence_evidence"]),
        meta={"volume": volume["label"], "edition": copy["title"], "copy": copy["id"]},
    )


def openings(copy: dict, volume: dict, root: Path) -> Iterator[tuple[int, str, Image.Image]]:
    """A volume's openings in order: its number, the URL its image is cached under, and the image.

    A directory copy holds one photograph an opening, `p<number>.jpg`. A PDF copy holds one opening
    a page; the page's embedded scan is taken as it is and cached under the file's Commons URL with
    `#page=<number>`.
    """
    if copy["kind"] == "directory":
        for path in sorted((root / copy["path"] / volume["files"]).glob("p*.jpg")):
            relative = path.relative_to(root).as_posix()
            url = f"{copy['repository'].replace('github.com', 'raw.githubusercontent.com')}/{copy['revision']}/{quote(relative)}"
            if images.path_for(url) is None:
                images.register(path, url)
            yield int(path.stem[1:]), url, Image.open(path).convert("RGB")
        return
    import fitz

    name = volume["files"]
    document = fitz.open(root / name)
    for index in range(len(document)):
        url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name.replace(' ', '_'))}#page={index + 1}"
        embedded = document[index].get_images(full=True)
        if len(embedded) != 1:
            continue
        data = document.extract_image(embedded[0][0])
        cached = images.path_for(url)
        if cached is None:
            scratch = root / f".{index + 1}.{data['ext']}"
            scratch.write_bytes(data["image"])
            images.register(scratch, url)
            scratch.unlink()
        yield index + 1, url, Image.open(io.BytesIO(data["image"])).convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("copy")
    parser.add_argument("output", type=Path)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--volumes", help="comma-separated volume numbers")
    parser.add_argument("--openings", help="comma-separated image numbers within the volumes")
    parser.add_argument("--enlarge", type=int, help="enlarge the openings this many times instead of the copy's own setting")
    parser.add_argument("--scale", type=float, help="the size the detector sees the enlarged opening at, instead of the copy's")
    parser.add_argument("--fit", type=float, help="the most a kept column's gaps may be off, in gloss rows, instead of the copy's")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source = yaml.safe_load((SOURCES / f"{SOURCE}.yaml").read_text(encoding="utf-8"))
    copy = next((c for c in source["copies"] if c["id"] == args.copy), None)
    if copy is None:
        raise SystemExit(f"{SOURCE}.yaml lists no copy {args.copy!r}")
    pins = [(args.table, source["revision"])] + ([(args.images, copy["revision"])] if copy["kind"] == "directory" else [])
    for clone, pinned in pins:
        found = clone_revision(clone)
        if found != pinned:
            raise SystemExit(f"{clone} is at {found}; {SOURCE}.yaml pins {pinned}")
    entries = read_entries(args.table)
    wanted = set(args.volumes.split(",")) if args.volumes else None
    only = {int(n) for n in args.openings.split(",")} if args.openings else None

    run = align.load_run(RUN)
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    second = Recognizer()
    if second.sequence is None:
        raise SystemExit("NDLkotenOCR is not installed under cache/models/ndlkotenocr-lite")
    reader_of = readers(classifier, second)

    documents, pages, lines, units, report = [], [], [], [], {}
    totals = defaultdict(int)
    enlarge = args.enlarge or int(copy["enlarge"])
    scale = args.scale or float(copy["scale"])
    fit = args.fit or float(copy["fit"])
    anchored = bool(copy.get("anchored"))
    prefix = f"{CORPUS}-{copy['id']}"
    for volume in copy["volumes"]:
        number = volume["volume"]
        if wanted is not None and number not in wanted:
            continue
        document = document_of(source, copy, volume)
        kept_in_volume = 0
        for opening, url, image in openings(copy, volume, args.images):
            leaf = opening - volume["offset"]
            if only is not None and opening not in only:
                continue
            sides = {"right": (number, leaf, "b"), "left": (number, leaf + 1, "a")}
            if not any(entries.get(key) for key in sides.values()):
                continue
            page = Page(id=f"{prefix}:{number}:{opening}", document_id=document.id, seq=opening - 1, image=url,
                        width=image.width, height=image.height, image_kind=copy["image_kind"],
                        transcription={"source": SOURCE, "entry": f"{number}:{leaf}b,{leaf + 1}a", "revision": source["revision"]})
            # The detector and the readers see the opening enlarged; a kept box is written at the image's own size.
            if enlarge != 1:
                image = image.resize((image.width * enlarge, image.height * enlarge), Image.Resampling.LANCZOS)
            boxes = [box for box, _ in detector.boxes_at(image, scale)]
            unit = leaf_text.side(boxes)
            found = leaf_text.axes(leaf_text.big(boxes, unit), unit) if boxes else []
            even = leaf_text.flat(found, COLUMNS, image.width) if copy["image_kind"] == "flat-reproduction" else None
            middle = even[0] if even else leaf_text.gutter(found, image.width, COLUMNS) if found else None
            kept_here = 0
            report[page.id] = {}
            for name, key in sides.items():
                columns = {column: [head for _, _, head, _ in sorted(rows, key=lambda r: r[0])]
                           for column, rows in (entries.get(key) or {}).items()}
                count = sum(len(heads) for heads in columns.values())
                if not count:
                    continue
                totals["headwords"] += count
                label = f"{key[1]}{key[2]}"
                if middle is None:
                    report[page.id][label] = {"skipped": "no gutter found", "headwords": count}
                    continue
                half = [b for b in boxes if (b.x + b.w / 2 > middle) == (name == "right")]
                half = leaf_text.bold(image, half, unit)
                reads, top = reader_of(image, half, unit)
                axes = leaf_text.ruled(middle, even[1], COLUMNS, name == "right") if even else None
                placed = leaf_text.place(half, columns, COLUMNS, reads, axes)
                if placed is None:
                    report[page.id][label] = {"skipped": "columns not found", "headwords": count}
                    continue
                verdicts = {(column, k): reads(cell.head.char, cell.box)
                            for column, (cells, _) in placed.items() for k, cell in enumerate(cells) if cell.detected}
                judged = [v for v in verdicts.values() if v is not None]
                borne = len(judged) >= CONFIRM and sum(judged) / len(judged) >= AGREED
                counts = {"headwords": count, "detected": len(verdicts), "judged": len(judged), "read": sum(judged), "kept": 0}
                if borne:
                    for column, (cells, error) in sorted(placed.items()):
                        if anchored and not any(verdicts.get((column, k)) for k in range(len(cells))):
                            continue  # no headword of the column was read where it stands
                        ids = [(sbgy, row) for _, sbgy, _, row in sorted(entries[key][column], key=lambda r: r[0])]
                        for k, cell in enumerate(cells):
                            verdict = verdicts.get((column, k))
                            if not cell.detected or verdict is False or error >= fit:
                                continue
                            sbgy, row = ids[k]
                            line_id = f"gy-{copy['id']}:{sbgy}"
                            box = Box(x=cell.box.x // enlarge, y=cell.box.y // enlarge, w=cell.box.w // enlarge, h=cell.box.h // enlarge)
                            lines.append(Line(id=line_id, page_id=page.id, seq=len(lines), vertical=True, box=box,
                                              text_raw=cell.head.char, text=cell.head.char, match_method="leaf-text",
                                              meta={"source": SOURCE, "entry_id": sbgy, "copy": copy["id"], "volume": number,
                                                    "小韻號": row.get("小韻號", ""), "反切": row.get("反切", ""),
                                                    "音韻地位": row.get("音韻地位", "")}))
                            units.append(Unit(
                                id=f"{line_id}:0", document_id=document.id, page_id=page.id, line_id=line_id, seq=0,
                                box=box, text_source=cell.head.char, unicode=cell.head.char,
                                classification=Classification.IDENTIFIED, script=Script.HAN, method="detect-align",
                                review=ReviewState.MACHINE, upstream={"source": SOURCE, "entry_id": sbgy, "copy": copy["id"]},
                                meta={"segmentation": SEGMENTATION, "read": verdict, "column_fit": round(error, 3),
                                      "classifier_top5": top.get((cell.box.x, cell.box.y, cell.box.w, cell.box.h))}))
                            counts["kept"] += 1
                else:
                    counts["skipped"] = "not borne out by the readers"
                report[page.id][label] = counts
                kept_here += counts["kept"]
            if kept_here:
                pages.append(page)
                kept_in_volume += kept_here
            totals["kept"] += kept_here
            print(json.dumps({"page": page.id, "kept": kept_here}, ensure_ascii=False), flush=True)
        if kept_in_volume:
            documents.append(document)

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_guangyun_headwords.py", *sys.argv[1:]])
    tables.write(args.output / "documents.parquet", documents, Document)
    tables.write(args.output / "pages.parquet", pages, Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps({"totals": dict(totals), "pages": report}, ensure_ascii=False, indent=1))
    print(json.dumps({"pages": len(pages), "units": len(units), **totals}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
