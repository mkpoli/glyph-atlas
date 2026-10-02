#!/usr/bin/env python3
"""Cut the characters of a page printed in ruled columns, labelled by its Wikisource transcription.

    uv run scripts/cut_ruled_grid.py work/hunminjeongeum work/hunminjeongeum-hanja \\
        --document ws:ko:scan:4667d93fb09503c46c47e2e2 --han-only --report work/hunminjeongeum-hanja.json

`SOURCE` is a dataset from `collect_wikisource_scans.py`: the document, its pages and their page
texts. `OUTPUT` becomes a dataset of the pages that were cut: one line per column with the column's
characters, and one unit per character box (`glyph_atlas.ruled_grid` says how the boxes are found
and when a page is left out). With `--han-only` only the Han characters become units; the lines
still hold every character of the column, and a unit's `seq` is its place in it. `--max-height`
leaves out a box taller than that many cells, which is where a cut took part of a neighbour.

Page images come from the image cache and are fetched into it when missing. `--report` writes every
page's outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from glyph_atlas import images, ruled_grid, tables
from glyph_atlas.schema import Box, Document, Line, Page, PageText, Unit


def unit_id(page_id: str, column: int, place: int) -> str:
    return "hmj:" + hashlib.sha1(f"{page_id}|{column}|{place}".encode()).hexdigest()[:20]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--document", required=True)
    parser.add_argument("--han-only", action="store_true")
    parser.add_argument("--max-height", type=float, default=1.2, help="tallest box kept, in cells (default 1.2)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    documents = [d for d in tables.read(args.source / "documents.parquet", Document) if d.id == args.document]
    if not documents:
        raise SystemExit(f"{args.document} is not in {args.source}")
    pages = {p.id: p for p in tables.read(args.source / "pages.parquet", Page) if p.document_id == args.document}
    texts = {t.page_id: t for t in tables.read(args.source / "page_texts.parquet", PageText) if t.page_id in pages}

    kept_pages, lines, units, report = [], [], [], {}
    for page_id in sorted(texts, key=lambda i: pages[i].seq):
        page = pages[page_id]
        if images.path_for(page.image) is None:
            images.fetch(page.image)
        path = images.path_for(page.image)
        gray = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
        cut, why = ruled_grid.page_boxes(gray, texts[page_id].text_raw)
        if cut is None:
            report[page_id] = why
            continue
        scale_x, scale_y = page.width / gray.shape[1] if page.width else 1, page.height / gray.shape[0] if page.height else 1
        kept = tall = 0
        by_column: dict[int, list[tuple[ruled_grid.Box, str, int]]] = {}
        for box, label, column, place in cut.boxes:
            by_column.setdefault(column, []).append((box, label, place))
        for column, entries in sorted(by_column.items()):
            line_id = f"{page_id}:C{column + 1}"
            xs = [b.x for b, _, _ in entries]
            top, bottom = min(b.y for b, _, _ in entries), max(b.y + b.h for b, _, _ in entries)
            text = "".join(label for _, label, _ in entries)
            lines.append(Line(id=line_id, page_id=page_id, seq=column, vertical=True, text_raw=text, text=text,
                              box=Box(x=round(min(xs) * scale_x), y=round(top * scale_y),
                                      w=round(entries[0][0].w * scale_x), h=round((bottom - top) * scale_y)),
                              match_method="ruled-grid",
                              meta={"source": "cut_ruled_grid", "layout": cut.layout}))
            for box, label, place in entries:
                if args.han_only and not ruled_grid.is_han(label):
                    continue
                if box.h > args.max_height * cut.pitch:
                    tall += 1
                    continue
                units.append(Unit(
                    id=unit_id(page_id, column, place), document_id=args.document, page_id=page_id, line_id=line_id,
                    seq=place, box=Box(x=round(box.x * scale_x), y=round(box.y * scale_y),
                                       w=round(box.w * scale_x), h=round(box.h * scale_y)),
                    text_source=label,
                    unicode=" ".join(f"U+{ord(c):04X}" for c in label),
                    script="han" if ruled_grid.is_han(label) else "unknown",
                    method="detect-align",
                    upstream={"source": "wikisource-scans", "page": page_id},
                    meta={"segmentation": "ruled-grid: cells counted against the page text, each column cut "
                                          "at its least-ink rows", "layout": cut.layout}))
                kept += 1
        kept_pages.append(page)
        report[page_id] = f"cut {cut.layout}: {len(cut.boxes)} boxes, {kept} units, {tall} too tall"

    args.output.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_ruled_grid.py", *sys.argv[1:]])
    tables.write(args.output / "documents.parquet", documents, Document)
    tables.write(args.output / "pages.parquet", kept_pages, Page)
    tables.write(args.output / "lines.parquet", lines, Line)
    tables.write(args.output / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({"pages": len(kept_pages), "left_out": len(report) - len(kept_pages), "units": len(units)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
