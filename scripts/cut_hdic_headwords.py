#!/usr/bin/env python3
"""Cut the headword characters of an HDIC dictionary from its NDL facsimile, labelled by HDIC.

    uv run scripts/cut_hdic_headwords.py krm work/hdic-krm --clone cache/krm --report work/hdic-krm.report.json
    uv run scripts/cut_hdic_headwords.py ktb work/hdic-ktb --clone cache/hdic --report work/hdic-ktb.report.json
    uv run scripts/cut_hdic_headwords.py tsj work/hdic-tsj --clone cache/hdic --report work/hdic-tsj.report.json

`DICTIONARY` is one of `glyph_atlas.hdic.DICTIONARIES`. `--clone` is a checkout of the repository its
source file (`data/sources/hdic-<dictionary>.yaml`) names, at the commit that file pins. Every frame
of the NDL volumes the source file lists that HDIC's frame table names is fetched once into the image
cache, run through the alignment run's character detector and the atlas classifier on CUDA, and its
pages placed by `glyph_atlas.hdic.place` on the frame's two page grids, one page to a grid
(`hdic.assign_pages`). A page the frame table puts on two frames is left out.

`OUTPUT` becomes a dataset of the frames with a kept headword: one document per NDL volume, one
line per entry holding its kept headword characters, one unit per character. `--volumes` (NDL pids)
and `--frames` bound a run; `--report` writes each page's counts.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image

from glyph_atlas import align, hdic, images, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.importers.honkoku_data import canvas_page, canvases, clone_revision, fetch_manifest
from glyph_atlas.registry import SOURCES
from glyph_atlas.review.suggestions import Recognizer, classifier_path, decode, preprocess
from glyph_atlas.schema import Box, Classification, Document, Line, Page, ReviewState, Script, Unit

RUN = Path("models/align/runs/collection-v2.yaml")
#: Entry fields a unit carries upstream, besides its entry id.
UPSTREAM = {"krm": ("hanzi_id", "tenri_location", "kazama_location"), "ktb": ("entry_type",), "tsj": ("rinsen",)}


def segmentation(layout: hdic.Layout) -> str:
    cells = f"{layout.columns}-column, {layout.tiers}-tier" if layout.tiers > 1 else f"{layout.columns}-column"
    return (f"HDIC cells: a {cells} grid fitted to the detector's headword-sized boxes; each cell's HDIC "
            "headword characters paired with its boxes top to bottom, checked by the classifier")


def seal_records(seals: list[hdic.Seal], page: Page, lines: list[Line], units: list[Unit]) -> int:
    """Add a line and a unit for each of a frame's seal forms, as HDIC boxed and labelled it; return how many."""
    for seal in seals:
        glyph = hdic.headword(seal.entry, "")
        code = hdic.encoded(glyph[0]) if len(glyph) == 1 else None
        line_id = f"ktb:{seal.seal_id}:seal"
        lines.append(Line(id=line_id, page_id=page.id, seq=len(lines), vertical=True, box=seal.box,
                          text_raw=seal.entry, text=seal.entry, match_method="import",
                          meta={"source": "hdic-ktb", "entry_id": seal.entry_id, "file": "KTB_ndl_Seal.tsv"}))
        units.append(Unit(
            id=f"ktb:{seal.seal_id}:seal", document_id=page.document_id, page_id=page.id, line_id=line_id, seq=0,
            box=seal.box, text_source=seal.entry, unicode=code,
            classification=Classification.IDENTIFIED if code else Classification.UNIDENTIFIED,
            script=Script.HAN, style="seal", method="import", review=ReviewState.TRANSCRIBER,
            upstream={"source": "hdic-ktb", "entry_id": seal.entry_id, "seal_id": f"T{seal.seal_id}"},
            meta={"classifier_agrees": None}))
    return len(seals)


def union(boxes: list[Box]) -> Box:
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dictionary", choices=sorted(hdic.DICTIONARIES))
    parser.add_argument("output", type=Path)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--volumes", help="comma-separated NDL pids of the volumes to cut")
    parser.add_argument("--frames", help="comma-separated NDL frame numbers within the volumes")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    dictionary = hdic.DICTIONARIES[args.dictionary]
    corpus = f"hdic-{dictionary.name}"
    path = SOURCES / f"{corpus}.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = clone_revision(args.clone)
    if found != source["revision"]:
        raise SystemExit(f"{args.clone} is at {found}; {path.name} pins {source['revision']}")
    entries, frames, conflicts = dictionary.read(args.clone)
    listed = {str(v["pid"]) for v in source["images"]["volumes"]}
    wanted = set(args.volumes.split(",")) if args.volumes else listed
    only = {int(f) for f in args.frames.split(",")} if args.frames else None

    by_frame: dict[tuple[str, int], dict[object, list[hdic.Entry]]] = defaultdict(lambda: defaultdict(list))
    unmapped, conflicting = Counter(), Counter()
    for entry in entries:
        if (entry.volume, entry.page) in conflicts:
            conflicting[entry.volume] += 1
            continue
        place = frames.get((entry.volume, entry.page))
        if place is None or place[0] not in listed:
            unmapped[entry.volume] += 1
            continue
        if place[0] in wanted and (only is None or place[1] in only):
            by_frame[place][entry.page].append(entry)
    # HDIC's own boxes around KTB's seal-script forms: units as they stand, and no headword is sought in them.
    seals: dict[tuple[str, int], list[hdic.Seal]] = defaultdict(list)
    if dictionary.name == "ktb":
        for seal in hdic.read_ktb_seals(args.clone):
            if seal.pid in wanted and (only is None or seal.frame in only):
                seals[(seal.pid, seal.frame)].append(seal)
                by_frame[(seal.pid, seal.frame)]  # a frame with seal forms only is still cut

    run = align.load_run(RUN)
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    known = set(classifier.classes)
    # NDLkotenOCR reads characters the classifier has no class for.
    reader = Recognizer()
    if reader.sequence is None:
        raise SystemExit("NDLkotenOCR is not installed under cache/models/ndlkotenocr-lite")
    sequence_input = reader.sequence.get_inputs()[0]
    described = segmentation(dictionary.layout)

    documents: dict[str, Document] = {}
    manifests: dict[str, list[dict]] = {}
    pages, lines, units, report = [], [], [], {}
    for (pid, frame), on_frame in sorted(by_frame.items()):
        if pid not in manifests:
            manifest = fetch_manifest(f"https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json")
            manifests[pid] = canvases(manifest)
            documents[pid] = hdic.document_of(dictionary, pid, manifest, source)
        canvas, image_url, _, _ = canvas_page(manifests[pid][frame - 1])
        if images.path_for(image_url) is None:
            images.fetch(image_url)
        image = Image.open(images.path_for(image_url)).convert("RGB")
        page = Page(id=f"{corpus}:{pid}:{frame}", document_id=documents[pid].id, seq=frame - 1, canvas=canvas,
                    image=image_url, width=image.width, height=image.height,
                    transcription={"source": corpus, "entry": ",".join(str(p) for p in sorted(on_frame)),
                                   "revision": source["revision"]})
        boxes = [box for box, _ in detector.boxes(image)]
        boxes = [b for b in boxes if not any(hdic.overlap(b, s.box) > 0.5 for s in seals[(pid, frame)])]
        unit = hdic.side(boxes)
        grids = hdic.page_grids(boxes, unit, dictionary.layout) if on_frame else {}
        here = seal_records(seals[(pid, frame)], page, lines, units)
        if not grids:
            report[page.id] = {"skipped": "no column grid fitted", "seals": here}
            if here:
                pages.append(page)
            continue
        probabilities = classifier.probabilities_many(
            [image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in boxes]) if boxes else []
        top5 = {id(b): [classifier.classes[int(i)] for i in row.argsort()[::-1][:5]]
                for b, row in zip(boxes, probabilities, strict=True)}

        def rank(box: Box, top5=top5) -> list[str]:
            return top5[id(box)]

        def read(box: Box, image=image) -> str | None:
            pixels = preprocess(image.crop((box.x, box.y, box.x + box.w, box.y + box.h)),
                                (sequence_input.shape[3], sequence_input.shape[2]))
            decoded = decode(reader.sequence.run(None, {sequence_input.name: pixels})[0], reader.alphabet)
            return decoded[0]["text"] if decoded else None

        tried = {key: {name: hdic.place(members, boxes, grid, unit, rank, known, dictionary.layout, read) for name, grid in grids.items()}
                 for key, members in on_frame.items()}
        chosen = hdic.assign_pages(tried, dictionary.right)
        if not chosen:
            report[page.id] = {"skipped": f"{len(on_frame)} pages for {len(grids)} grids", "seals": here}
            if here:
                pages.append(page)
            continue
        kept_here = []
        report[page.id] = {"seals": here} if here else {}
        for key, name in sorted(chosen.items()):
            result = tried[key][name]
            report[page.id][str(key)] = {"grid": name, **result.counts}
            kept_here += [p for p in result.pairs if p.kept]
        if kept_here or here:
            pages.append(page)
        by_entry: dict[str, list[hdic.Pair]] = defaultdict(list)
        for pair in kept_here:
            by_entry[pair.entry.entry_id].append(pair)
        for seq, (entry_id, kept) in enumerate(sorted(by_entry.items(), key=lambda kv: kv[1][0].entry.location)):
            entry = kept[0].entry
            line_id = f"{dictionary.name}:{entry_id}"
            written = "".join(g.text for g in entry.glyphs)
            lines.append(Line(
                id=line_id, page_id=page.id, seq=seq, vertical=True, box=union([p.box for p in kept]),
                text_raw=written, text=written, match_method="hdic-cells",
                meta={"source": corpus, "entry_id": entry_id, "volume": entry.volume, **entry.meta}))
            for pair in kept:
                if not pair.glyph.readable:
                    continue  # HDIC does not name this character
                code = hdic.encoded(pair.glyph)
                meta = {"segmentation": described, "classifier_top5": pair.top5, "classifier_agrees": pair.classifier,
                        "ndl_reading": pair.second, "read": pair.verdict}
                if pair.glyph.standard:
                    meta["standard_form"] = pair.glyph.standard
                units.append(Unit(
                    id=f"{dictionary.name}:{entry_id}:{pair.slot}", document_id=page.document_id, page_id=page.id,
                    line_id=line_id, seq=pair.slot, box=pair.box, text_source=pair.glyph.text,
                    unicode=code, classification=Classification.IDENTIFIED if code else Classification.UNIDENTIFIED,
                    script=Script.HAN, method="detect-align", review=ReviewState.MACHINE,
                    upstream={"source": corpus, "entry_id": entry_id,
                              **{k: entry.meta[k] for k in UPSTREAM[dictionary.name] if entry.meta.get(k)}},
                    meta=meta))
        print(json.dumps({"page": page.id, "kept": len(kept_here)}, ensure_ascii=False), flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_hdic_headwords.py", *sys.argv[1:]])
    kept_documents = [documents[pid] for pid in sorted(documents) if any(p.document_id == documents[pid].id for p in pages)]
    tables.write(args.output / "documents.parquet", kept_documents, Document)
    tables.write(args.output / "pages.parquet", pages, Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps({"unmapped": dict(unmapped), "conflicting": dict(conflicting), "pages": report},
                                          ensure_ascii=False, indent=1))
    verdicts = [u.meta.get("read") for u in units if not u.id.endswith(":seal")]
    print(json.dumps({"frames": len(by_frame), "pages": len(pages), "entries": len(lines), "units": len(units),
                      "seals": len(units) - len(verdicts), "read": verdicts.count(True), "unread": verdicts.count(None),
                      "unmapped_entries": sum(unmapped.values()), "conflicting_entries": sum(conflicting.values())},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
