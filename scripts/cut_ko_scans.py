"""Cut glyphs from ko.wikisource's transcribed scans, labelled from each page's transcription.

    uv run scripts/cut_ko_scans.py work/wikisource-ko-scans --out work/wikisource-ko-glyphs \\
        --hangul-model <hangul classifier.onnx> --image-root <image cache> --report report.json --gpu

The source is the dataset `scripts/collect_wikisource_scans.py --wiki ko` writes. Each page's boxes
come from the detector and are read in columns right to left; the page's wikitext is aligned to them
by `glyph_atlas.ko_scans`, and the atlas and hangul classifiers check every match. A page whose
matches disagree too often is left out whole. The output is a dataset of the kept pages, one line per
column and one unit per kept glyph; `--report` records every page's outcome.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

from glyph_atlas import align, eonhae, images, ko_scans, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.review.suggestions import classifier_path
from glyph_atlas.schema import Box, Document, Line, Page, PageText, Unit

RUN = Path("models/align/runs/pilot-v1.yaml")
METHOD = "ko-scan-align"


def read_table(path: Path, model: type) -> list:
    return list(tables.read(path, model)) if path.exists() else []


def page_meta(page: Page) -> dict:
    meta = page.meta
    return json.loads(meta) if isinstance(meta, str) else dict(meta or {})


def scale_box(box: Box, sx: float, sy: float) -> Box:
    return Box(x=round(box.x * sx), y=round(box.y * sy), w=max(1, round(box.w * sx)), h=max(1, round(box.h * sy)))


def union(boxes: list[Box]) -> Box:
    left, top = min(b.x for b in boxes), min(b.y for b in boxes)
    right, bottom = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="ko.wikisource scan dataset")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--hangul-model", required=True, type=Path, help="hangul classifier ONNX")
    parser.add_argument("--image-root", type=Path, help="image cache holding the page images")
    parser.add_argument("--document", action="append", default=[],
                        help="cut only documents whose id or title starts with this; repeat for several")
    parser.add_argument("--limit", type=int, help="cut at most this many pages, for a trial")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--gpu", action="store_true", help="try CUDAExecutionProvider before CPU")
    args = parser.parse_args()

    documents = {d.id: d for d in read_table(args.source / "documents.parquet", Document)}
    texts = {t.page_id: t for t in read_table(args.source / "page_texts.parquet", PageText)}
    chosen = {d.id for d in documents.values()
              if not args.document or any(d.id.startswith(p) or d.title.startswith(p) for p in args.document)}
    pages = sorted((p for p in read_table(args.source / "pages.parquet", Page) if p.document_id in chosen),
                   key=lambda p: (p.document_id, p.seq))
    if args.limit is not None:
        pages = pages[:args.limit]

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if args.gpu else ["CPUExecutionProvider"]
    run = align.load_run(RUN, "collection-v1")
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=providers)
    atlas = Classifier(classifier_path(), providers=providers)
    hangul = Classifier(args.hangul_model, providers=providers)
    hangul_index = eonhae.hangul_shape_index(hangul.classes, eonhae.hangul_model_name(args.hangul_model))
    model_name = eonhae.hangul_model_name(args.hangul_model)

    cache = {row.url: row for row in images.index(images.images_root(args.image_root))}
    out_documents: dict[str, Document] = {}
    out_pages: list[Page] = []
    lines: list[Line] = []
    units: list[Unit] = []
    report: dict[str, dict] = {}
    outcomes: Counter = Counter()

    for page in pages:
        entry: dict[str, object] = {"document": page.document_id, "seq": page.seq + 1}
        report[page.id] = entry
        text = texts.get(page.id)
        tokens = ko_scans.page_tokens(text.text_raw) if text else []
        quality = page_meta(page).get("quality")
        entry["quality"] = quality
        if not tokens:
            entry["outcome"] = "no transcribed glyphs"
            outcomes[entry["outcome"]] += 1
            continue
        path = images.path_for(page.image, root=args.image_root) if page.image in cache else None
        if path is None:
            entry["outcome"] = "image not in cache"
            outcomes[entry["outcome"]] += 1
            continue
        image = Image.open(path).convert("RGB")
        boxes = [box for box, _ in detector.boxes(image)]
        if not boxes:
            entry["outcome"] = "no boxes"
            outcomes[entry["outcome"]] += 1
            continue
        crops = [image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in boxes]
        models = ko_scans.Models(atlas.probabilities_many(crops), atlas.classes,
                                 hangul.probabilities_many(crops), hangul_index, hangul.classes)
        columns = ko_scans.reading_order(boxes)
        order = [index for column in columns for index in column]
        column_of = {index: number for number, column in enumerate(columns) for index in column}
        steps = ko_scans.align(models.costs(tokens, order))
        found = ko_scans.matches(steps, tokens, order, models)
        agreement, agreeing = ko_scans.page_agreement(found)
        entry.update({
            "tokens": len(tokens), "boxes": len(boxes), "columns": len(columns), "matches": len(found),
            "agreement": None if agreement is None else round(agreement, 4), "agreeing": agreeing,
        })
        if not ko_scans.page_accepted(found):
            entry["outcome"] = "left out"
            outcomes[entry["outcome"]] += 1
            continue
        kept = ko_scans.emitted(steps, found)
        if not kept:
            entry["outcome"] = "no units"
            outcomes[entry["outcome"]] += 1
            continue

        sx = page.width / image.width if page.width else 1.0
        sy = page.height / image.height if page.height else 1.0
        by_column: dict[int, list[ko_scans.Match]] = {}
        for match in found:
            by_column.setdefault(column_of[match.box], []).append(match)
        kept_tokens = {match.token for match in kept}
        line_seq = 0
        for column, members in sorted(by_column.items()):
            first, last = members[0].token, members[-1].token
            emitted = [m for m in members if m.token in kept_tokens]
            if not emitted:
                continue
            line = Line(
                id=ko_scans.line_id(page.id, column),
                page_id=page.id,
                seq=line_seq,
                box=scale_box(union([boxes[m.box] for m in members]), sx, sy),
                vertical=True,
                text_raw="".join(tokens[first:last + 1]),
                text="".join(tokens[first:last + 1]),
                match_method=METHOD,
                meta={"column": column, "token_start": first},
            )
            line_seq += 1
            lines.append(line)
            for match in emitted:
                char = tokens[match.token]
                box = scale_box(boxes[match.box], sx, sy)
                top_key = "hangul_top5" if match.model == "hangul" else "atlas_top5"
                units.append(Unit(
                    id=ko_scans.unit_id(page.id, box),
                    document_id=page.document_id,
                    page_id=page.id,
                    line_id=line.id,
                    seq=match.token - first,
                    box=box,
                    text_source=char,
                    unicode=eonhae.codepoints(char),
                    script=eonhae.script_of(char),
                    method="detect-align",
                    upstream={key: str(value) for key, value in {
                        "source": "wikisource", "url": page_meta(page).get("wikisource_url"),
                        "revid": text.revision}.items() if value},
                    meta={
                        "classifier_agrees": match.verdict,
                        "model": match.model if match.model != "hangul" else model_name,
                        top_key: list(match.top5),
                        "column": column,
                        "quality": quality,
                    },
                ))
        out_pages.append(page)
        out_documents[page.document_id] = documents[page.document_id]
        entry["outcome"] = "cut"
        entry["units"] = len(kept)
        outcomes["cut"] += 1
        print(json.dumps({"page": page.id, **entry}, ensure_ascii=False), flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    tables.write(args.out / "documents.parquet", list(out_documents.values()), Document)
    tables.write(args.out / "pages.parquet", out_pages, Page)
    tables.write(args.out / "lines.parquet", lines, Line)
    tables.write(args.out / "units.parquet", units, Unit)
    summary = {"pages": len(pages), "outcomes": dict(outcomes), "lines": len(lines), "units": len(units),
               "by_script": dict(Counter(u.script for u in units)),
               "unjudged": sum(u.meta["classifier_agrees"] is None for u in units)}
    if args.report:
        args.report.write_text(json.dumps({"_summary": summary, **report}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
