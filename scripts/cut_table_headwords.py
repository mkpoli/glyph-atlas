#!/usr/bin/env python3
"""Cut a dictionary's headwords from a scan of one printing, labelled by a table that gives each headword's half-leaf.

    uv run scripts/cut_table_headwords.py longkan-shoujing song work/longkan-song --table cache/qingque-data --pdf <folder> \
        --fonts <serif CJK font>,cache/fonts/PlangothicP1-Regular.ttf,... --report work/longkan-song.report.json

`SOURCE` is a file of `data/sources` that names the table (`format.file`), the module of `glyph_atlas` that reads it
(`format.reader`: `read`, `paged`, `unsettled`, `kin`), the corpus and the prefix of the ids it cuts, and its copies;
`COPY` is one of them, with the sizes its print is fitted at. `--table` is a checkout of the table's repository at
the pinned commit, or its folder with the one CSV file; `--pdf` the folder with the copy's PDF under its Commons
name, whose SHA-1 the source pins. Each half-leaf's boxes are placed along its columns read as one tape
(`glyph_atlas.tape`), in the order the table gives, and the table page it shows is the one that fits best.

A headword is kept when its pair has a probability of `POSTERIOR` over all order-preserving pairings and is the
most likely pairing's, its crop is at least `ALIKE` like the character drawn in a font, or at least `FLOOR` and
likest its own headword of the page's, ahead of the `NEAR` headwords either side by `LEAD`, and its half-leaf is borne
out: the page costs `COST` a headword or less and beats the next page by `MARGIN`, and the pages of the volume
run in order. The label is the table's headword; the print may show a variant of it. A headword the table leaves
blank is placed with the rest and not kept, and so is one whose row the reader's `unsettled` names. One it names only
for a neighbour written with the same character is kept when its crop leads that run (`longkan.leads`): at least
`KIN_ALIKE` like the character and ahead of every other crop of the run placed at `POSTERIOR` by `KIN_LEAD`. The
others of the run are the 俗, 正 and 通 forms the dictionary lists beside it: each is kept with a label that names
the character and not its printed form (`label_scope` "grapheme"), so the site files it under the character's
字種 with its written form open.

`OUTPUT` becomes a dataset: one document per volume, a page per scan page with a kept headword, a line and a unit
per headword. `--report` says why each half-leaf was kept or not; `--leaves` bounds a run to these PDF pages.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
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

RUN = Path("models/align/runs/collection-v2.yaml")
SEGMENTATION = "tape-anchors-v1"
POSTERIOR = 0.9
ALIKE = 0.65
#: A crop less like its headword than `ALIKE` is kept from `FLOOR` up when no other headword of the page rivals it.
FLOOR = 0.5
LEAD = 0.1
NEAR = 4
#: Of a run of headwords written with one character, the crop kept as that character: how like it, and how far ahead.
KIN_ALIKE = 0.7
KIN_LEAD = 0.15
COST = -0.3
MARGIN = 0.3
#: The table pages searched either side of the one a half-leaf is expected to show.
REACH, WIDE_REACH = 12, 40
#: How many of the pages closest at first sight have their sizes fitted, and how much a page can gain by it: every
#: page the winner's margin does not outlast by this is fitted too (0.28 was the most in 250 fits on ten half-leaves).
CANDIDATES = 4
GAIN = 0.35


def document_of(source: dict, copy: dict, volume: str) -> Document:
    return Document(
        id=f"{source['corpus']}-{copy['id']}:{volume}",
        title=f"{source['work']}{source['volumes'][volume]}（{source['printing']}，{copy['of']}）",
        source_refs={source["id"]: source["volumes"][volume], "images": copy["repository"]},
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
        raise SystemExit(f"{path} is not the file its source pins")
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


def fitted(page: tape.Half, slots: list[tape.Slot], alike: np.ndarray, copy: dict) -> tuple[float, float, float]:
    """The cost a headword, and the sizes, a page fits best at: a headword's height and a gloss row in half-column
    pitches, of those the copy lists (`heads`, `rows`)."""
    best = min((tape.solve(slots, page, alike, h * page.pitch, r * page.pitch)[1], h, r) for h in copy["heads"] for r in copy["rows"])
    return best[0] / len(slots), best[1], best[2]


def native(box: Box, times: int) -> Box:
    """`box` on the image at its own size: every pixel it touches on the image enlarged `times`."""
    x0, y0 = box.x // times, box.y // times
    x1, y1 = -(-(box.x + box.w) // times), -(-(box.y + box.h) // times)
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


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
    parser.add_argument("source")
    parser.add_argument("copy")
    parser.add_argument("output", type=Path)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--fonts", required=True, help="comma-separated font files that draw the headwords, the first that has one used")
    parser.add_argument("--leaves", help="comma-separated PDF page numbers")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = yaml.safe_load((SOURCES / f"{args.source}.yaml").read_text(encoding="utf-8"))
    copy = next((c for c in source["copies"] if c["id"] == args.copy), None)
    if copy is None:
        raise SystemExit(f"{args.source}.yaml lists no copy {args.copy!r}")
    reader = importlib.import_module(f"glyph_atlas.{source['format']['reader']}")
    name = source["format"]["file"]
    table = args.table / name if (args.table / name).exists() else args.table / Path(name).name
    entries = reader.read(table, source["format"]["edition"])
    only = {int(n) for n in args.leaves.split(",")} if args.leaves else None
    run = align.load_run(RUN)
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    if classifier.features(Image.new("L", (64, 64), 255)) is None:
        raise SystemExit("the classifier export has no features")
    anchors = tape.Anchors(args.fonts.split(","), classifier.features)
    slots_of = reader.paged(entries)
    unsettled, held = reader.unsettled(entries), reader.unsettled(entries, neighbours=False)
    kin = reader.kin(entries)
    first_head, first_row = copy["first"]

    found, last = [], None  # `last`: the half-leaf last borne out, as its index in the book (two to a scan page), and its table page
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
            index = 2 * leaf + (side == "left")
            centre = last[1] + index - last[0] if last is not None else 2 * leaf - (2 if side == "left" else 3)
            for reach in (REACH, WIDE_REACH):
                costs = {}
                for number in range(centre - reach, centre + reach + 1):
                    if number not in slots_of:
                        continue
                    alike = tape.similarity(slots_of[number], crops, anchors)
                    costs[number] = tape.solve(slots_of[number], page, alike, first_head * page.pitch, first_row * page.pitch)[1] / len(slots_of[number])
                if costs and min(costs.values()) <= COST:
                    break
            if not costs:
                found.append({"leaf": leaf, "side": side, "url": url, "size": image.size, "skipped": "no table page in reach"})
                continue
            # Sizes are fitted for the pages that came closest at first sight: one that fits best at other sizes is found.
            near = sorted(costs, key=costs.get)[:CANDIDATES]
            sized = {}
            for number in near:
                alike = tape.similarity(slots_of[number], crops, anchors)
                sized[number] = (*fitted(page, slots_of[number], alike, copy), alike)
            # A page left unfitted could still win if its first cost is less than a fitted one's and the most it may gain.
            while True:
                rest = [n for n in costs if n not in sized and costs[n] - GAIN < min(v[0] for v in sized.values()) + MARGIN]
                if not rest:
                    break
                number = min(rest, key=costs.get)
                alike = tape.similarity(slots_of[number], crops, anchors)
                sized[number] = (*fitted(page, slots_of[number], alike, copy), alike)
            number = min(sized, key=lambda n: sized[n][0])
            cost, head, row, alike = sized[number]
            # An unfitted page is as good as its first cost less the most it may gain.
            others = [sized[n][0] for n in sized if n != number] + [v - GAIN for n, v in costs.items() if n not in sized]
            margin = min(others) - cost if others else 0.0
            found.append({"leaf": leaf, "side": side, "url": url, "size": image.size, "table_page": number, "cost": cost, "margin": margin,
                          "page": page, "head": head, "row": row, "alike": alike, "good": cost <= COST and (not others or margin >= MARGIN)})
            if found[-1]["good"]:
                last = (index, number)
            print(json.dumps({"leaf": leaf, "side": side, "page": number, "cost": round(cost, 2), "margin": round(found[-1]["margin"], 2),
                              "good": found[-1]["good"]}), flush=True)
    in_order = ordered([(k, f["table_page"], f["good"]) for k, f in enumerate(found) if "table_page" in f])

    documents: dict[str, Document] = {}
    pages: dict[int, Page] = {}
    lines: list[Line] = []
    units: list[Unit] = []
    report = []

    def keep(f: dict, slot: tape.Slot, j: int, chance: float, alike: float, scope: str | None = None) -> bool:
        """Add the headword `slot` as box `j` of half-leaf `f`, paired at `chance` and `alike` its character; whether it was added."""
        entry = next(e for e in entries[f["table_page"]] if e.row == slot.entry)
        box = native(f["page"].candidates[j][2], copy["enlarge"])
        volume = entry.volume
        documents.setdefault(volume, document_of(source, copy, volume))
        page_id = f"{source['corpus']}-{copy['id']}:{f['leaf']}"
        if f["leaf"] in pages and pages[f["leaf"]].document_id != documents[volume].id:
            return False  # a scan page that a volume ends on belongs to the first of its two volumes
        if f["leaf"] not in pages:
            pages[f["leaf"]] = Page(id=page_id, document_id=documents[volume].id, seq=f["leaf"] - 1, image=f["url"],
                                    width=f["size"][0], height=f["size"][1], image_kind=copy["image_kind"],
                                    transcription={"source": source["id"], "entry": str(f["table_page"]), "revision": source["revision"]})
        char = slot.char
        line_id = f"{source['prefix']}-{copy['id']}:{slot.entry}-{slot.sub}"
        lines.append(Line(id=line_id, page_id=page_id, seq=len(lines), vertical=True, box=box, text_raw=entry.field, text=char, match_method="tape",
                          match_confidence=float(min(chance, 1.0)),
                          meta={"source": source["id"], "row": slot.entry, "table_page": f["table_page"], "leaf": f["leaf"], "side": f["side"],
                                "section": entry.section, "tone": entry.tone}))
        one = len(char) == 1
        units.append(Unit(id=f"{line_id}:0", document_id=documents[volume].id, page_id=page_id, line_id=line_id, seq=0, box=box, text_source=char,
                          unicode=f"U+{ord(char):04X}" if one else None,
                          classification=Classification.IDENTIFIED if one else Classification.UNIDENTIFIED, script=Script.HAN,
                          method="detect-align", review=ReviewState.MACHINE, upstream={"source": source["id"], "entry_id": str(slot.entry), "copy": copy["id"]},
                          meta={"segmentation": SEGMENTATION, "posterior": round(float(chance), 4), "similarity": round(alike, 3),
                                "page_cost": round(float(f["cost"]), 3), "label": "table headword",
                                **({"label_scope": scope} if scope else {})}))
        return True

    # The headwords are kept in a second pass, in reading order: one held only for a neighbour of its character is
    # set against every crop of its run, wherever on the book's pages that was placed.
    placed: dict[tuple[int, int], float] = {}
    chosen: list[tuple[dict, dict, tape.Slot, int, float, float, bool]] = []
    for k, f in enumerate(found):
        row = {key: f[key] for key in ("leaf", "side", "skipped", "table_page", "cost", "margin") if key in f}
        if k in in_order:
            slots = slots_of[f["table_page"]]
            chance = tape.posterior(slots, f["page"], f["alike"], f["head"] * f["page"].pitch, f["row"] * f["page"].pitch)
            best = tape.solve(slots, f["page"], f["alike"], f["head"] * f["page"].pitch, f["row"] * f["page"].pitch)[0]
            row["kept"] = row["unsettled"] = 0
            for n, slot in enumerate(slots):
                j = best.get(n)
                if j is None or slot.entry < 0 or slot.char is None or np.isnan(f["alike"][n, j]):
                    continue
                if chance[n, j] < POSTERIOR:
                    continue
                spot = (slot.entry, slot.sub)
                placed[spot] = float(f["alike"][n, j])
                if f["alike"][n, j] < FLOOR:
                    continue
                if f["alike"][n, j] < ALIKE and not tape.unrivalled(slots, f["alike"], n, j, LEAD, NEAR):
                    continue
                if spot in held:
                    row["unsettled"] += 1
                    continue
                chosen.append((f, row, slot, j, float(chance[n, j]), float(f["alike"][n, j]), spot in unsettled))
        else:
            row.setdefault("skipped", "page not borne out" if not f.get("good") else "out of order")
        report.append(row)
    for f, row, slot, j, chance, alike, by_kin in chosen:
        spot = (slot.entry, slot.sub)
        if not by_kin or longkan.leads(spot, kin, placed, KIN_ALIKE, KIN_LEAD):
            if keep(f, slot, j, chance, alike):
                row["kept"] += 1
                if by_kin:
                    row["kin"] = row.get("kin", 0) + 1
        elif spot in kin and keep(f, slot, j, chance, alike, scope="grapheme"):
            row["kept"] += 1
            row["variants"] = row.get("variants", 0) + 1
        else:
            row["unsettled"] += 1

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_table_headwords.py", *sys.argv[1:]])
    order = sorted(documents, key=lambda volume: (len(volume), volume))  # "2" before "10"
    tables.write(args.output / "documents.parquet", [documents[v] for v in order], Document)
    tables.write(args.output / "pages.parquet", [pages[leaf] for leaf in sorted(pages)], Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    totals = {"half_leaves": len(found), "used": len(in_order), "kept": len(units), "unsettled": sum(r.get("unsettled", 0) for r in report),
              "kin": sum(r.get("kin", 0) for r in report), "variants": sum(r.get("variants", 0) for r in report),
              "headwords": sum(1 for k in in_order for s in slots_of[found[k]["table_page"]] if s.entry >= 0),
              "skipped": dict(Counter(r["skipped"] for r in report if "skipped" in r))}
    if args.report:
        fonts = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.fonts.split(",")}
        args.report.write_text(json.dumps({"totals": totals, "fonts": fonts, "pages": report}, ensure_ascii=False, indent=1))
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
