#!/usr/bin/env python3
"""Cut the headwords of 龍龕手鏡's 宋刻本 from its scan, labelled by 青雀古籍's table.

    uv run scripts/cut_longkan_headwords.py song work/longkan-song --table cache/qingque-data --pdf <folder> \
        --fonts cache/fonts/PlangothicP1-Regular.ttf,... --report work/longkan-song.report.json

`COPY` is one `data/sources/longkan-shoujing.yaml` lists. `--table` is a checkout of the table's repository at
the pinned commit, or its folder with the one CSV file; `--pdf` the folder with the copy's PDF under its Commons
name, whose SHA-1 the source pins. Each half-leaf's boxes are placed along its columns read as one tape
(`glyph_atlas.tape`), in the order the table gives, and the table page it shows is the one that fits best.

A headword is kept when its pair has a probability of `POSTERIOR` over all order-preserving pairings and is the
most likely pairing's, its crop is at least `ALIKE` like the character drawn in a font, and its half-leaf is borne
out: the page costs `COST` a headword or less and beats the next page by `MARGIN`, and the pages of the volume
run in order. The label is the table's headword, a character the table's editor wrote; the print may show a
variant of it. A headword the table leaves blank is placed with the rest and not kept.

`OUTPUT` becomes a dataset: one document per volume, a page per scan page with a kept headword, a line and a unit
per headword. `--report` says why each half-leaf was kept or not; `--leaves` bounds a run to these PDF pages.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import numpy as np
import yaml
from PIL import Image

from glyph_atlas import align, images, longkan, tables, tape
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.registry import SOURCES
from glyph_atlas.review.suggestions import classifier_path
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

SOURCE = "longkan-shoujing"
CORPUS = "longkan"
TABLE = "data/古辞书综合/龙龛手镜.csv"
RUN = Path("models/align/runs/collection-v2.yaml")
SEGMENTATION = "tape-anchors-v1"
POSTERIOR = 0.9
ALIKE = 0.35
COST = -0.3
MARGIN = 0.3
#: The table pages searched either side of the one a half-leaf is expected to show.
REACH, WIDE_REACH = 12, 40
#: A headword's height and a gloss row in half-column pitches: what the fit of a page tries.
HEADS = (1.2, 1.35, 1.5, 1.65, 1.8, 1.95)
ROWS = (0.72, 0.8, 0.88, 0.96, 1.04, 1.12)
#: The sizes a page is first compared at.
FIRST = (1.55, 0.9)


def document_of(source: dict, copy: dict, volume: str) -> Document:
    return Document(
        id=f"{CORPUS}-{copy['id']}:{volume}",
        title=f"{source['work']}{source['volumes'][volume]}（{source['printing']}，{copy['of']}）",
        source_refs={SOURCE: source["volumes"][volume], "images": copy["repository"]},
        holder=copy["holder"],
        production="printed/woodblock",
        origin="china",
        genre=["dictionary"],
        text_register=Register(source["register"]),
        image_kind=copy["image_kind"],
        dating=[Dating(literal=str(d["literal"]), start=d["start"], end=d["end"], kind=d["kind"], evidence=d["evidence"]) for d in source["dating"]],
        image_rights=Rights(licence=Licence(copy["licence"]), holder=copy["holder"], attribution=copy["attribution"],
                            evidence=copy["licence_evidence"]),
        text_rights=Rights(licence=Licence(source["licence"]), holder=source["publisher"], attribution=source["attribution"],
                           evidence=source["licence_evidence"]),
        meta={"volume": source["volumes"][volume], "edition": copy["title"], "copy": copy["id"]},
    )


def leaves(copy: dict, folder: Path, only: set[int] | None) -> Iterator[tuple[int, str, Image.Image]]:
    """The scan's pages in order: the number of the PDF page, the URL its image is cached under, and the embedded image."""
    import pymupdf

    path = folder / copy["file"]
    if hashlib.sha1(path.read_bytes()).hexdigest() != copy["sha1"]:
        raise SystemExit(f"{path} is not the file {SOURCE}.yaml pins")
    document = pymupdf.open(path)
    for index in range(len(document)):
        if only is not None and index + 1 not in only:
            continue
        embedded = document[index].get_images(full=True)
        if len(embedded) != 1:
            continue
        url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(copy['file'].replace(' ', '_'))}#page={index + 1}"
        data = document.extract_image(embedded[0][0])
        if images.path_for(url) is None:
            scratch = folder / f".{index + 1}.{data['ext']}"
            scratch.write_bytes(data["image"])
            images.register(scratch, url)
            scratch.unlink()
        yield index + 1, url, Image.open(io.BytesIO(data["image"])).convert("RGB")


def halves(image: Image.Image, boxes: list[Box]) -> list[tuple[str, list[Box]]]:
    """The boxes of each half-leaf of a scan page, the right half first."""
    if image.width <= image.height:
        return [("single", boxes)]
    return [("right", [b for b in boxes if b.x + b.w / 2 > image.width / 2]), ("left", [b for b in boxes if b.x + b.w / 2 <= image.width / 2])]


def fitted(page: tape.Half, slots: list[tape.Slot], alike: np.ndarray) -> tuple[float, float, float]:
    """The cost a headword, and the sizes, a page fits best at."""
    best = min((tape.solve(slots, page, alike, h * page.pitch, r * page.pitch)[1], h, r) for h in HEADS for r in ROWS)
    return best[0] / len(slots), best[1], best[2]


def ordered(found: list[tuple[int, int, bool]]) -> set[int]:
    """Which of the half-leaves (index, table page, borne out) run in the book's order: the longest run of rising pages."""
    tails: list[int] = []
    chain: dict[int, tuple[int, int | None]] = {}
    last_of: list[int] = []
    for index, page, good in found:
        if not good:
            continue
        at = bisect_left(tails, page)
        chain[index] = (page, last_of[at - 1] if at else None)
        if at == len(tails):
            tails.append(page)
            last_of.append(index)
        else:
            tails[at], last_of[at] = page, index
    kept: set[int] = set()
    index = last_of[-1] if last_of else None
    while index is not None:
        kept.add(index)
        index = chain[index][1]
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("copy")
    parser.add_argument("output", type=Path)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--fonts", required=True, help="comma-separated font files that draw the headwords, the first that has one used")
    parser.add_argument("--leaves", help="comma-separated PDF page numbers")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = yaml.safe_load((SOURCES / f"{SOURCE}.yaml").read_text(encoding="utf-8"))
    copy = next((c for c in source["copies"] if c["id"] == args.copy), None)
    if copy is None:
        raise SystemExit(f"{SOURCE}.yaml lists no copy {args.copy!r}")
    table = args.table / TABLE if (args.table / TABLE).exists() else args.table / Path(TABLE).name
    entries = longkan.read(table, source["format"]["edition"])
    only = {int(n) for n in args.leaves.split(",")} if args.leaves else None
    run = align.load_run(RUN)
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    if classifier.features(Image.new("L", (64, 64), 255)) is None:
        raise SystemExit("the classifier export has no features")
    anchors = tape.Anchors(args.fonts.split(","), classifier.features)
    slots_of = {page: longkan.slots(rows) for page, rows in entries.items()}

    found, expected = [], None
    for leaf, url, image in leaves(copy, args.pdf, only):
        big = image.resize((image.width * copy["enlarge"], image.height * copy["enlarge"]), Image.Resampling.LANCZOS)
        boxes = [box for box, _ in detector.boxes_at(big, float(copy["scale"]))]
        for side, part in halves(big, boxes):
            page = tape.half(part, *copy["pitch"])
            if page is None or len(page.candidates) < 10:
                found.append({"leaf": leaf, "side": side, "url": url, "size": image.size, "skipped": "no grid"})
                continue
            page.candidates = tape.recut(big, page)
            crops = np.stack([classifier.features(big.crop((b.x, b.y, b.x + b.w, b.y + b.h))) for _, _, b in page.candidates])
            centre = expected + 1 if expected is not None else 2 * leaf - (2 if side == "left" else 3)
            for reach in (REACH, WIDE_REACH):
                costs = {}
                for number in range(centre - reach, centre + reach + 1):
                    if number not in slots_of:
                        continue
                    alike = tape.similarity(slots_of[number], crops, anchors)
                    costs[number] = tape.solve(slots_of[number], page, alike, FIRST[0] * page.pitch, FIRST[1] * page.pitch)[1] / len(slots_of[number])
                if costs and min(costs.values()) <= COST:
                    break
            if not costs:
                found.append({"leaf": leaf, "side": side, "url": url, "size": image.size, "skipped": "no table page in reach"})
                continue
            number = min(costs, key=costs.get)
            alike = tape.similarity(slots_of[number], crops, anchors)
            cost, head, row = fitted(page, slots_of[number], alike)
            others = [v for n, v in costs.items() if n != number]
            found.append({"leaf": leaf, "side": side, "url": url, "size": image.size, "table_page": number, "cost": cost,
                          "margin": min(others) - costs[number] if others else 0.0, "page": page, "head": head, "row": row, "alike": alike,
                          "good": cost <= COST and (not others or min(others) - costs[number] >= MARGIN)})
            if found[-1]["good"]:
                expected = number
            print(json.dumps({"leaf": leaf, "side": side, "page": number, "cost": round(cost, 2), "margin": round(found[-1]["margin"], 2),
                              "good": found[-1]["good"]}), flush=True)
    in_order = ordered([(k, f["table_page"], f["good"]) for k, f in enumerate(found) if "table_page" in f])

    documents: dict[str, Document] = {}
    pages: dict[int, Page] = {}
    lines: list[Line] = []
    units: list[Unit] = []
    report = []
    for k, f in enumerate(found):
        row = {key: f[key] for key in ("leaf", "side", "skipped", "table_page", "cost", "margin") if key in f}
        if k in in_order:
            slots = slots_of[f["table_page"]]
            chance = tape.posterior(slots, f["page"], f["alike"], f["head"] * f["page"].pitch, f["row"] * f["page"].pitch)
            best = tape.solve(slots, f["page"], f["alike"], f["head"] * f["page"].pitch, f["row"] * f["page"].pitch)[0]
            kept = 0
            for n, slot in enumerate(slots):
                j = best.get(n)
                char = slot.char
                if j is None or slot.entry < 0 or char is None or np.isnan(f["alike"][n, j]):
                    continue
                if chance[n, j] < POSTERIOR or f["alike"][n, j] < ALIKE:
                    continue
                entry = next(e for e in entries[f["table_page"]] if e.row == slot.entry)
                box = f["page"].candidates[j][2]
                scale = copy["enlarge"]
                box = Box(x=round(box.x / scale), y=round(box.y / scale), w=max(1, round(box.w / scale)), h=max(1, round(box.h / scale)))
                volume = entry.volume
                documents.setdefault(volume, document_of(source, copy, volume))
                page_id = f"{CORPUS}-{copy['id']}:{f['leaf']}"
                if f["leaf"] in pages and pages[f["leaf"]].document_id != documents[volume].id:
                    continue  # a scan page that a volume ends on belongs to the first of its two volumes
                if f["leaf"] not in pages:
                    pages[f["leaf"]] = Page(id=page_id, document_id=documents[volume].id, seq=f["leaf"] - 1, image=f["url"],
                                            width=f["size"][0], height=f["size"][1], image_kind=copy["image_kind"],
                                            transcription={"source": SOURCE, "entry": str(f["table_page"]), "revision": source["revision"]})
                line_id = f"lk-{copy['id']}:{slot.entry}-{slot.sub}"
                lines.append(Line(id=line_id, page_id=page_id, seq=len(lines), vertical=True, box=box, text_raw=entry.field, text=char, match_method="tape",
                                  match_confidence=float(min(chance[n, j], 1.0)),
                                  meta={"source": SOURCE, "row": slot.entry, "table_page": f["table_page"], "leaf": f["leaf"], "side": f["side"],
                                        "section": entry.section, "tone": entry.tone}))
                one = len(char) == 1
                units.append(Unit(id=f"{line_id}:0", document_id=documents[volume].id, page_id=page_id, line_id=line_id, seq=0, box=box, text_source=char,
                                  unicode=f"U+{ord(char):04X}" if one else None,
                                  classification=Classification.IDENTIFIED if one else Classification.UNIDENTIFIED, script=Script.HAN,
                                  method="detect-align", review=ReviewState.MACHINE, upstream={"source": SOURCE, "entry_id": str(slot.entry), "copy": copy["id"]},
                                  meta={"segmentation": SEGMENTATION, "posterior": round(float(chance[n, j]), 4), "similarity": round(float(f["alike"][n, j]), 3),
                                        "page_cost": round(float(f["cost"]), 3), "label": "table headword"}))
                kept += 1
            row["kept"] = kept
        else:
            row.setdefault("skipped", "page not borne out" if not f.get("good") else "out of order")
        report.append(row)

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_longkan_headwords.py", *sys.argv[1:]])
    order = sorted(documents)
    tables.write(args.output / "documents.parquet", [documents[v] for v in order], Document)
    tables.write(args.output / "pages.parquet", [pages[leaf] for leaf in sorted(pages)], Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    totals = {"half_leaves": len(found), "used": len(in_order), "kept": len(units),
              "headwords": sum(1 for k in in_order for s in slots_of[found[k]["table_page"]] if s.entry >= 0),
              "skipped": dict(Counter(r["skipped"] for r in report if "skipped" in r))}
    if args.report:
        fonts = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.fonts.split(",")}
        args.report.write_text(json.dumps({"totals": totals, "fonts": fonts, "pages": report}, ensure_ascii=False, indent=1))
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
