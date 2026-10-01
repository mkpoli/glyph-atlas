#!/usr/bin/env python3
"""Cut the headword characters of the 観智院本類聚名義抄 from the NDL facsimile, labelled by HDIC KRM.

    uv run scripts/cut_krm_headwords.py work/hdic-krm --clone cache/krm --report work/hdic-krm.json

`--clone` is a checkout of https://github.com/shikeda/krm at the commit `data/sources/hdic-krm.yaml`
pins. Every frame of the ten NDL volumes that KRM's `krm_ndl.tsv` names is fetched once into the
image cache, run through the alignment run's character detector and the atlas classifier on CUDA,
and its 天理 pages placed by `glyph_atlas.krm.place` on the frame's two page grids, one page to a
grid (`krm.assign_pages`). A page `krm_ndl.tsv` puts on two frames is left out.

`OUTPUT` becomes a dataset of the frames with a kept headword: one document per NDL volume, one
line per entry holding its kept headword characters, one unit per character. `--volumes` and
`--frames` bound a run; `--report` writes each page's counts.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image

from glyph_atlas import align, images, krm, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.importers.honkoku_data import canvas_page, canvases, clone_revision, fetch_manifest
from glyph_atlas.registry import SOURCES
from glyph_atlas.review.suggestions import classifier_path
from glyph_atlas.schema import Box, Classification, Document, Line, Page, ReviewState, Script, Unit

RUN = Path("models/align/runs/collection-v2.yaml")
SOURCE = SOURCES / "hdic-krm.yaml"
SEGMENTATION = ("KRM cells: an eight-column, four-tier grid fitted to the detector's headword-sized boxes; "
                "each cell's KRM headword characters paired with its boxes top to bottom, checked by the classifier")


def union(boxes: list[Box]) -> Box:
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--clone", type=Path, default=Path("cache/krm"))
    parser.add_argument("--volumes", help="comma-separated volume names, e.g. 仏上,仏中")
    parser.add_argument("--frames", help="comma-separated NDL frame numbers within the volumes")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    found = clone_revision(args.clone)
    if found != source["revision"]:
        raise SystemExit(f"{args.clone} is at {found}; {SOURCE.name} pins {source['revision']}")
    entries = krm.read_entries(args.clone / "krm_main.tsv")
    frames, conflicts = krm.read_frames(args.clone / "krm_ndl.tsv")
    volumes = {v["volume"]: v["pid"] for v in source["images"]["volumes"]}
    wanted = set(args.volumes.split(",")) if args.volumes else set(volumes)
    only = {int(f) for f in args.frames.split(",")} if args.frames else None

    by_frame: dict[tuple[str, int], dict[int, list[krm.Entry]]] = defaultdict(lambda: defaultdict(list))
    unmapped, conflicting = Counter(), Counter()
    for entry in entries:
        if entry.volume not in wanted:
            continue
        if (entry.volume, entry.page) in conflicts:
            conflicting[entry.volume] += 1
            continue
        place = frames.get((entry.volume, entry.page))
        if place is None or place[0] != volumes[entry.volume]:
            unmapped[entry.volume] += 1
            continue
        if only is None or place[1] in only:
            by_frame[place][entry.page].append(entry)

    run = align.load_run(RUN)
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    known = set(classifier.classes)

    documents: dict[str, Document] = {}
    manifests: dict[str, list[dict]] = {}
    pages, lines, units, report = [], [], [], {}
    for (pid, frame), on_frame in sorted(by_frame.items()):
        volume = next(e.volume for page in on_frame.values() for e in page)
        if pid not in manifests:
            manifest = fetch_manifest(f"https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json")
            manifests[pid] = canvases(manifest)
            documents[pid] = krm.document_of(volume, pid, manifest, source)
        canvas, image_url, _, _ = canvas_page(manifests[pid][frame - 1])
        if images.path_for(image_url) is None:
            images.fetch(image_url)
        image = Image.open(images.path_for(image_url)).convert("RGB")
        page = Page(id=f"hdic-krm:{pid}:{frame}", document_id=documents[pid].id, seq=frame - 1, canvas=canvas,
                    image=image_url, width=image.width, height=image.height,
                    transcription={"source": "hdic-krm", "entry": ",".join(str(p) for p in sorted(on_frame)),
                                   "revision": source["revision"]})
        boxes = [box for box, _ in detector.boxes(image)]
        unit = krm.side(boxes)
        grids = krm.page_grids(boxes, unit)
        if not grids:
            report[page.id] = {"skipped": "no column grid fitted"}
            continue
        probabilities = classifier.probabilities_many(
            [image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in boxes]) if boxes else []
        top5 = {id(b): [classifier.classes[int(i)] for i in row.argsort()[::-1][:5]]
                for b, row in zip(boxes, probabilities, strict=True)}
        def rank(box: Box, top5=top5) -> list[str]:
            return top5[id(box)]

        kept_here = []
        report[page.id] = {}
        tried = {tenri: {name: krm.place(members, boxes, grid, unit, rank, known) for name, grid in grids.items()}
                 for tenri, members in on_frame.items()}
        chosen = krm.assign_pages(tried)
        if not chosen:
            report[page.id] = {"skipped": f"{len(on_frame)} pages for {len(grids)} grids"}
            continue
        for tenri, name in sorted(chosen.items()):
            result = tried[tenri][name]
            report[page.id][tenri] = {"grid": name, **result.counts}
            kept_here += [p for p in result.pairs if p.kept]
        if not kept_here:
            continue
        pages.append(page)
        by_entry: dict[str, list[krm.Pair]] = defaultdict(list)
        for pair in kept_here:
            by_entry[pair.entry.entry_id].append(pair)
        for seq, (entry_id, kept) in enumerate(sorted(by_entry.items(), key=lambda kv: kv[1][0].entry.location)):
            entry = kept[0].entry
            line_id = f"krm:{entry_id}"
            written = "".join(g.text for g in entry.glyphs)
            lines.append(Line(
                id=line_id, page_id=page.id, seq=seq, vertical=True, box=union([p.box for p in kept]),
                text_raw=written, text=written, match_method="krm-cells",
                meta={"source": "hdic-krm", "entry_id": entry_id, "tenri_location": entry.location,
                      "kazama_location": entry.kazama, "volume": entry.volume, "radical": entry.radical,
                      "hanzi_entry": entry.hanzi_entry, "original_entry": entry.original_entry,
                      "definition": entry.definition}))
            for pair in kept:
                if not pair.glyph.readable:
                    continue  # KRM does not name this character
                code = krm.encoded(pair.glyph)
                meta = {"segmentation": SEGMENTATION, "classifier_top5": pair.top5, "classifier_agrees": pair.verdict}
                if pair.glyph.standard:
                    meta["standard_form"] = pair.glyph.standard
                units.append(Unit(
                    id=f"krm:{entry_id}:{pair.slot}", document_id=page.document_id, page_id=page.id, line_id=line_id,
                    seq=pair.slot, box=pair.box, text_source=pair.glyph.text, reading=pair.glyph.text,
                    unicode=code, classification=Classification.IDENTIFIED if code else Classification.UNIDENTIFIED,
                    script=Script.HAN, method="detect-align", review=ReviewState.MACHINE,
                    upstream={"source": "hdic-krm", "entry_id": entry_id, "hanzi_id": entry.hanzi_id,
                              "tenri_location": entry.location, "kazama_location": entry.kazama},
                    meta=meta))
        print(json.dumps({"page": page.id, "kept": len(kept_here)}, ensure_ascii=False), flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_krm_headwords.py", *sys.argv[1:]])
    kept_documents = [documents[pid] for pid in sorted(documents) if any(p.document_id == documents[pid].id for p in pages)]
    tables.write(args.output / "documents.parquet", kept_documents, Document)
    tables.write(args.output / "pages.parquet", pages, Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps({"unmapped": dict(unmapped), "conflicting": dict(conflicting), "pages": report}, ensure_ascii=False, indent=1))
    verdicts = [u.meta["classifier_agrees"] for u in units]
    print(json.dumps({"frames": len(by_frame), "pages": len(pages), "entries": len(lines), "units": len(units),
                      "classifier_agrees": verdicts.count(True), "unverifiable": verdicts.count(None),
                      "unmapped_entries": sum(unmapped.values()), "conflicting_entries": sum(conflicting.values())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
