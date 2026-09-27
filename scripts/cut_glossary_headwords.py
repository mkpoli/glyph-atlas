#!/usr/bin/env python3
"""Cut the headword characters of a glossary printed in entries down ruled columns.

    uv run scripts/cut_glossary_headwords.py work/nlk-sayeogwon work/wikisource-ko-waeeo \\
        work/waeeo-headwords --document nlk:CNTS-00092710493 \\
        --text-document ws:ko:scan:5c612f2372d9cfdacc19c72b --page-offset 1 \\
        --report work/waeeo-headwords.json

`PAGES` holds the scan (the document and its pages); `TEXTS` holds the transcription of the same
book, a dataset from `collect_wikisource_scans.py`. Page `n` of the scan is transcribed on page
`n - offset` of the text. `OUTPUT` becomes a dataset of the scan pages that were cut: one line per
kept entry holding its headword, and one unit per headword character, labelled with the
transcription (`glyph_atlas.glossary` says how entries are found and when one is left out).

Each page runs through the alignment run's character detector and the atlas classifier, on CUDA.
A unit records whether the classifier's five best classes hold its character or an equivalent form
(`classifier_agrees`, null when the classifier has no class for it), and an entry with a character
the classifier knows but does not read there is left out. The label never comes from the
classifier. `--report` writes every page's outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

from glyph_atlas import align, glossary, images, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.review.suggestions import classifier_path
from glyph_atlas.schema import Box, Document, Line, Page, PageText, Unit

RUN = Path("models/align/runs/pilot-v1.yaml")
SEGMENTATION = ("glossary entries: detector boxes down each column, the large ones before each "
                "circle taken as the headword, counted against the transcribed headword")


def unit_id(page_id: str, place: int, seq: int) -> str:
    return "gl:" + hashlib.sha1(f"{page_id}|{place}|{seq}".encode()).hexdigest()[:20]


def union(boxes: tuple[Box, ...]) -> Box:
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pages", type=Path)
    parser.add_argument("texts", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--document", required=True, help="the scan's document id in PAGES")
    parser.add_argument("--text-document", required=True, help="the transcription's document id in TEXTS")
    parser.add_argument("--page-offset", type=int, default=0, help="scan page n is text page n - offset")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    documents = [d for d in tables.read(args.pages / "documents.parquet", Document) if d.id == args.document]
    if not documents:
        raise SystemExit(f"{args.document} is not in {args.pages}")
    pages = sorted((p for p in tables.read(args.pages / "pages.parquet", Page) if p.document_id == args.document),
                   key=lambda p: p.seq)
    text_pages = {p.id: p for p in tables.read(args.texts / "pages.parquet", Page)
                  if p.document_id == args.text_document}
    by_seq = {p.seq: p for p in text_pages.values()}
    texts = {t.page_id: t for t in tables.read(args.texts / "page_texts.parquet", PageText) if t.page_id in text_pages}

    run = align.load_run(RUN, "collection-v1")
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=["CUDAExecutionProvider"])
    classifier = Classifier(classifier_path(), providers=["CUDAExecutionProvider"])
    known = set(classifier.classes)

    kept_pages, lines, units, report = [], [], [], {}
    for page in pages:
        text_page = by_seq.get(page.seq - args.page_offset)
        text = texts.get(text_page.id) if text_page else None
        if text is None or not text.text_raw.strip():
            report[page.id] = "no transcription"
            continue
        heads = glossary.headwords(text.text_raw)
        if not heads:
            report[page.id] = "no headword in the page text"
            continue
        image = Image.open(images.path_for(page.image)).convert("RGB")
        boxes = [box for box, _ in detector.boxes(image)]
        crops = [image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in boxes]
        probabilities = classifier.probabilities_many(crops) if crops else []
        ranked = [[classifier.classes[int(i)] for i in row.argsort()[::-1][:5]] for row in probabilities]
        top5 = {id(box): classes for box, classes in zip(boxes, ranked, strict=True)}
        entries = glossary.page_entries(boxes, [classes[0] for classes in ranked])
        kept, why = glossary.match(entries, heads)
        checked = []
        for place, entry, head in kept:
            verdicts = [glossary.agrees(glyph, top5[id(box)], known) for box, glyph in zip(entry.boxes, head, strict=True)]
            if False not in verdicts:
                checked.append((place, entry, head, verdicts))
        refused = len(kept) - len(checked)
        report[page.id] = f"{why}, {refused} of them refused by the classifier (text page {text_page.id})"
        if refused > glossary.REFUSED_SHARE * len(kept):
            report[page.id] += "; page left out as misaligned"
            continue
        if not checked:
            continue
        kept_pages.append(page)
        for place, entry, head, verdicts in checked:
            line_id = f"{page.id}:E{place}"
            written = "".join(g.text for g in head)
            lines.append(Line(id=line_id, page_id=page.id, seq=place, vertical=True, box=union(entry.boxes),
                              text_raw=written, text=written, match_method="glossary-entries",
                              meta={"source": "cut_glossary_headwords", "column": entry.column}))
            for seq, (box, glyph, verdict) in enumerate(zip(entry.boxes, head, verdicts, strict=True)):
                if not glyph.readable:
                    continue  # the transcription does not name this character
                code = f"U+{ord(glyph.text):04X}" if glyph.encoded and len(glyph.text) == 1 else None
                meta = {"segmentation": SEGMENTATION, "text_quality": text_page.meta.get("quality"),
                        "classifier_top5": top5[id(box)], "classifier_agrees": verdict}
                if glyph.standard:
                    meta["standard_form"] = glyph.standard
                units.append(Unit(
                    id=unit_id(page.id, place, seq), document_id=args.document, page_id=page.id, line_id=line_id,
                    seq=seq, box=box, text_source=glyph.text, reading=glyph.text,
                    unicode=code, script="han", method="detect-align",
                    upstream={"source": "wikisource-scans", "page": text_page.id}, meta=meta))

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_glossary_headwords.py", *sys.argv[1:]])
    tables.write(args.output / "documents.parquet", documents, Document)
    tables.write(args.output / "pages.parquet", kept_pages, Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    verdicts = [u.meta["classifier_agrees"] for u in units]
    print(json.dumps({"pages": len(kept_pages), "left_out": len(report) - len(kept_pages), "entries": len(lines),
                      "units": len(units), "classifier_agrees": verdicts.count(True),
                      "unverifiable": verdicts.count(None)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
