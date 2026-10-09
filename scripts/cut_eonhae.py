#!/usr/bin/env python3
"""Cut 老乞大諺解 scan pages into large Chinese text and small 언해 characters.

    scripts/cut_eonhae.py work/nlk-sayeogwon --hangul-model <onnx> \\
        --out work/nogeoldae-eonhae --report work/nogeoldae-eonhae/report.json --gpu

The upstream Wikisource JSON is read from `work/nogeoldae-eonhae/upstream` by default. Missing
pinned files are fetched from Wikisource's revision API, then their revision ids are checked before
any page is cut. Page images must already be present in the glyph-atlas image cache.

Each page runs through the alignment run's character detector, the atlas classifier and the Hangul
classifier. The output directory becomes a dataset of kept scan pages: `documents.parquet`,
`pages.parquet`, `lines.parquet` and `units.parquet`. `--report` writes each page's Chinese
alignment, each 언해 run's phrase decision, geometry refusals and a per-volume summary. Labels come
from the pinned texts; classifiers only check and choose among candidate phrases.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from PIL import Image

from glyph_atlas import align, eonhae, images, tables
from glyph_atlas.classify import Classifier
from glyph_atlas.detect import Detector
from glyph_atlas.review.suggestions import classifier_path
from glyph_atlas.schema import Box, Document, Line, LineRole, Page, Unit

RUN = Path("models/align/runs/pilot-v1.yaml")
DROP_REASON_ORDER = ("overlap", "small box", "gap")


def union(boxes: Iterable[Box]) -> Box:
    boxes = tuple(boxes)
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def scale_box(box: Box, sx: float, sy: float) -> Box:
    return Box(x=round(box.x * sx), y=round(box.y * sy), w=round(box.w * sx), h=round(box.h * sy))


def parse_pages(value: str | None) -> set[int] | None:
    if not value:
        return None
    pages: set[int] = set()
    for piece in value.split(","):
        if "-" in piece:
            a, b = piece.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(piece))
    return pages


def volume_names(value: str) -> list[eonhae.Volume]:
    if value in ("all", "both"):
        return ["sang", "ha"]
    if value in ("sang", "上"):
        return ["sang"]
    if value in ("ha", "下"):
        return ["ha"]
    raise argparse.ArgumentTypeError("expected sang, ha, 上, 下 or all")


def document_for(documents: list[Document], volume: eonhae.Volume, explicit: str | None) -> Document:
    if explicit:
        for document in documents:
            if document.id == explicit:
                return document
        raise SystemExit(f"{explicit} is not in the page dataset")
    scan = eonhae.VOLUMES[volume]["scan"]
    for document in documents:
        refs = " ".join([document.id, document.title, *document.source_refs.values()])
        if scan in refs:
            return document
    raise SystemExit(f"no document for {scan}; pass --document")


def read_table(path: Path, model: type) -> list:
    return list(tables.read(path, model)) if path.exists() else []


def display_path(path: Path) -> str:
    try:
        return "~/" + str(path.resolve().relative_to(Path.home()))
    except ValueError:
        return str(path)


def page_image_path(page: Page) -> Path | None:
    return images.path_for(page.image)


def line_box(units: list[eonhae.PendingUnit]) -> Box:
    return union(unit.box for unit in units)


def page_run_counts(events: Iterable[eonhae.LayoutEvent], active_count: int | None) -> list[int]:
    counts: list[int] = []
    current = active_count
    for event in events:
        if event.kind == "hanja":
            if current is not None:
                counts.append(current)
            current = None
        elif event.kind == "circle":
            if current is not None:
                counts.append(current)
            current = 0
        elif event.kind == "eonhae" and current is not None:
            current += 1
    if current is not None:
        counts.append(current)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="scan dataset with documents/pages parquet")
    parser.add_argument("--out", required=True, type=Path, help="output dataset directory")
    parser.add_argument("--upstream", type=Path, default=Path("work/nogeoldae-eonhae/upstream"))
    parser.add_argument("--volume", type=volume_names, default=["sang", "ha"], help="sang, ha, 上, 下 or all")
    parser.add_argument("--pages", help="1-based page numbers or ranges, e.g. 6,8-12")
    parser.add_argument("--document", help="override scan document id when cutting one volume")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--hangul-model", required=True, type=Path, help="Hangul classifier ONNX")
    parser.add_argument("--window", type=int, default=12, help="ko phrase search window around the cursor")
    parser.add_argument("--reacquire", type=int, default=8, help="dropped eonhae runs before global ko search")
    parser.add_argument("--margin", type=float, default=1.0, help="minimum mean-log-probability margin in nats")
    parser.add_argument("--min-agree", type=float, default=0.6, help="minimum restricted argmax agreement share")
    parser.add_argument("--min-evidence", type=int, default=3, help="minimum known Hangul boxes in an eonhae run")
    parser.add_argument("--gpu", action="store_true", help="try CUDAExecutionProvider before CPU")
    args = parser.parse_args()

    requested_pages = parse_pages(args.pages)
    documents = read_table(args.source / "documents.parquet", Document)
    all_pages = read_table(args.source / "pages.parquet", Page)
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if args.gpu else ["CPUExecutionProvider"]
    run = align.load_run(RUN, "collection-v1")
    detector = Detector(run.detector, score=run.score, nms=run.nms, providers=providers)
    classifier = Classifier(classifier_path(), providers=providers)
    known = set(classifier.classes)
    hangul_classifier = Classifier(args.hangul_model, providers=providers)
    hangul_index = eonhae.hangul_shape_index(
        hangul_classifier.classes,
        eonhae.hangul_model_name(args.hangul_model),
    )

    kept_page_ids: set[str] = set()
    out_documents: dict[str, Document] = {}
    out_pages: dict[str, Page] = {}
    lines: list[Line] = []
    units: list[Unit] = []
    report: dict[str, object] = {}
    volume_reports: dict[eonhae.Volume, dict[str, object]] = {}
    line_seq = 0

    def add_units(
        document_id: str,
        line_id: str,
        role: str,
        pending: list[eonhae.PendingUnit],
        source: eonhae.WikiSource,
    ) -> int:
        kept = 0
        for seq, item in enumerate(pending):
            if item.meta.get("emit") is False:
                continue
            if not eonhae.emits_unit(item.verdict) or not eonhae.names_a_character(item.char):
                continue
            units.append(Unit(
                id=eonhae.unit_id(item.page_id, role, item.box),
                document_id=document_id,
                page_id=item.page_id,
                line_id=line_id,
                seq=seq,
                box=item.box,
                text_source=item.char,
                unicode=eonhae.codepoints(item.char),
                script=eonhae.script_of(item.char),
                method="detect-align",
                upstream=source.upstream,
                meta={
                    "segmentation": eonhae.SEGMENTATION,
                    "atlas_top5": list(item.top5),
                    "classifier_agrees": item.verdict,
                    "role": role,
                    "volume": volume,
                    "text_revid": source.revid,
                    "column": item.column,
                    **item.meta,
                },
            ))
            kept += 1
            kept_page_ids.add(item.page_id)
        return kept

    for volume in args.volume:
        if len(args.volume) > 1 and args.document:
            raise SystemExit("--document may be used only with a single --volume")
        ko_source, zh_source = eonhae.load_sources(args.upstream, volume)
        ko_lines = eonhae.ko_lines(ko_source, volume)
        ko_phrases = eonhae.ko_phrases(ko_source, volume)
        zh_chars = eonhae.zh_stream(zh_source)
        document = document_for(documents, volume, args.document)
        out_documents[document.id] = document
        pages = sorted((p for p in all_pages if p.document_id == document.id), key=lambda p: p.seq)
        if requested_pages is not None:
            pages = [p for p in pages if p.seq + 1 in requested_pages]

        zh_cursor = 0
        ko_cursor = eonhae.EonhaeCursorState()
        ko_circles_consumed = 0
        ko_global_searches = 0
        ko_assignment_accepted = 0
        ko_jumps: list[int] = []
        circle_ordinal = 0
        in_eonhae = False
        active_run: eonhae.PendingRun | None = None
        active_chinese: list[eonhae.PendingUnit] = []
        page_geometry: dict[str, eonhae.EonhaePageGeometry] = {}
        dropped_units_disagree = 0

        def flush_chinese(
            *,
            document: Document = document,
            volume: eonhae.Volume = volume,
            zh_source: eonhae.WikiSource = zh_source,
        ) -> None:
            nonlocal active_chinese, line_seq
            if not active_chinese:
                return
            text = "".join(unit.char for unit in active_chinese)
            offsets = [unit.meta.get("zh_index") for unit in active_chinese if isinstance(unit.meta.get("zh_index"), int)]
            if offsets:
                phrase_key = f"zh:{min(offsets)}-{max(offsets) + 1}"
            else:
                phrase_key = "zh:" + eonhae.box_key(active_chinese[0].box)
            segments = eonhae.page_local_line_segments(
                active_chinese,
                role="text",
                phrase_key=phrase_key,
                text_raw=text,
                meta={"source": "cut_eonhae", "role": "text", "volume": volume},
            )
            for segment in segments:
                lines.append(Line(
                    id=segment.line_id,
                    page_id=segment.page_id,
                    seq=line_seq,
                    vertical=True,
                    box=line_box(list(segment.units)),
                    text_raw=segment.text_raw,
                    text=segment.text,
                    match_method="eonhae-sentences",
                    meta=segment.meta,
                ))
                add_units(document.id, segment.line_id, "text", list(segment.units), zh_source)
                line_seq += 1
            active_chinese = []

        def flush_eonhae(
            reason_page: str,
            *,
            document: Document = document,
            ko_phrases: list[eonhae.KoPhrase] = ko_phrases,
            ko_source: eonhae.WikiSource = ko_source,
            page_geometry: dict[str, eonhae.EonhaePageGeometry] = page_geometry,
            volume: eonhae.Volume = volume,
            ko_jumps: list[int] = ko_jumps,
        ) -> None:
            nonlocal active_run, line_seq, ko_cursor, dropped_units_disagree
            nonlocal ko_global_searches, ko_assignment_accepted
            if active_run is None:
                return
            first_page = active_run.events[0][0] if active_run.events else reason_page
            ordinal = active_run.circle_ordinal or 0
            rows = [row for _event_page, _event, row in active_run.events]
            center = ko_cursor.center
            global_search = ko_cursor.uses_global_search(args.reacquire)
            if global_search:
                ko_global_searches += 1
            assignment = eonhae.assign_eonhae_run(
                rows,
                ko_phrases,
                center,
                hangul_index,
                window=args.window,
                margin=args.margin,
                min_agree=args.min_agree,
                min_evidence=args.min_evidence,
                global_search=global_search,
                min_index=ko_cursor.anchor,
            )
            phrase = assignment.phrase
            report_ko_index = phrase.index if phrase is not None else None
            key = f"{first_page}:circle-{ordinal:03d}:ko-{report_ko_index if report_ko_index is not None else center}"
            box_count = len(active_run.events)
            base_report: dict[str, object] = {
                "volume": volume,
                "run_page": first_page,
                "pages": sorted(active_run.pages),
                "circle_ordinal": ordinal,
                "search_center": assignment.center,
                "global_search": assignment.global_search,
                "ko_search_center": assignment.center,
                "ko_search_global": assignment.global_search,
                "ko_index": report_ko_index,
                "accepted_ko_index": phrase.index if assignment.accepted and phrase is not None else None,
                "box_count": box_count,
                "phrase_score": assignment.score,
                "phrase_margin": assignment.margin,
                "phrase_agree_fraction": assignment.agree_fraction,
                "phrase_candidates": assignment.candidates,
                "phrase_evidence": assignment.evidence,
                "num_unknown": assignment.num_unknown,
            }
            if phrase is not None:
                base_report["ko_search_jump"] = phrase.index - assignment.center
            if phrase is not None:
                base_report.update({
                    "ko_line": phrase.line_index,
                    "ko_phrase": phrase.phrase_index,
                    "ko_text": phrase.text,
                    "ko_text_start": phrase.text[:16],
                    "raw": phrase.raw,
                    "characters": len(phrase.chars),
                })
            if not assignment.accepted:
                report[key] = {
                    **base_report,
                    "outcome": "dropped eonhae run",
                    "reason": assignment.reason,
                }
                ko_cursor = ko_cursor.drop()
                active_run = None
                return
            assert phrase is not None
            ko_assignment_accepted += 1
            ko_jumps.append(phrase.index - assignment.center)
            reason_details: dict[str, list[str]] = {}
            geometry_events = [(page_id, event) for page_id, event, _row in active_run.events]
            geometry_problems = eonhae.eonhae_run_geometry_problems(geometry_events, page_geometry)
            for problem in geometry_problems:
                reason_details.setdefault(problem.reason, []).append(problem.detail)
            if reason_details:
                ordered_reasons = [reason for reason in DROP_REASON_ORDER if reason in reason_details]
                reason = ordered_reasons[0]
                details = [
                    detail
                    for ordered_reason in ordered_reasons
                    for detail in reason_details[ordered_reason]
                ]
                report[key] = {
                    **base_report,
                    "outcome": "dropped eonhae run",
                    "reason": reason,
                    "reasons": ordered_reasons,
                    "detail": reason_details[reason][0],
                    "geometry_details": details,
                }
                ko_cursor = ko_cursor.drop()
                active_run = None
                return
            pending: list[eonhae.PendingUnit] = []
            refused_hanja = 0
            run_dropped_units_disagree = 0
            hangul_decisions = eonhae.eonhae_hangul_unit_decisions(phrase, rows, hangul_index)
            for offset, ((event_page, event, row), char, hangul_decision) in enumerate(zip(
                active_run.events,
                phrase.chars,
                hangul_decisions,
                strict=True,
            )):
                meta: dict[str, object] = {
                    "phrase_score": assignment.score,
                    "phrase_margin": assignment.margin,
                    "hangul_model": hangul_index.model_name,
                    "phrase_offset": offset,
                }
                if eonhae.script_of(char) == "hangul":
                    verdict = hangul_decision.classifier_agrees
                    meta["hangul_p"] = hangul_decision.hangul_p
                    meta["hangul_top5"] = list(eonhae.top_class_labels(row, hangul_classifier.classes))
                    if verdict is False:
                        dropped_units_disagree += 1
                        run_dropped_units_disagree += 1
                elif eonhae.is_han(char):
                    verdict = eonhae.classifier_agrees(char, event.top5, known)
                    refused_hanja += verdict is False
                else:
                    verdict = None
                pending.append(eonhae.PendingUnit(event_page, event.box, char, event.top5, verdict, event.column, meta))
            phrase_key = f"ko:{phrase.index}"
            base_meta = {
                    "source": "cut_eonhae",
                    "role": "eonhae",
                    "volume": volume,
                    "ko_index": phrase.index,
                    "ko_line": phrase.line_index,
                    "ko_phrase": phrase.phrase_index,
                    "ko_text": phrase.text,
            }
            segments = eonhae.page_local_line_segments(
                pending,
                role="eonhae",
                phrase_key=phrase_key,
                text_raw=phrase.raw,
                meta=base_meta,
            )
            for segment in segments:
                lines.append(Line(
                    id=segment.line_id,
                    page_id=segment.page_id,
                    seq=line_seq,
                    vertical=True,
                    role=LineRole.WARIGAKI,
                    box=line_box(list(segment.units)),
                    text_raw=segment.text_raw,
                    text=segment.text,
                    match_method="eonhae-sentences",
                    meta=segment.meta,
                ))
                add_units(document.id, segment.line_id, "eonhae", list(segment.units), ko_source)
                line_seq += 1
            report[key] = {
                **base_report,
                "outcome": "kept eonhae run",
                "refused_hanja": refused_hanja,
                "dropped_units_disagree": run_dropped_units_disagree,
            }
            active_run = None
            ko_cursor = ko_cursor.accept(phrase.index)

        skipped_page_eonhae_units = 0
        skipped_page_chinese_units = 0
        skipped_page_circles = 0

        def skip_pending_page(
            reason_page: str,
            *,
            circle_count: int = 0,
            volume: eonhae.Volume = volume,
        ) -> None:
            nonlocal active_run, active_chinese, in_eonhae, ko_cursor
            nonlocal skipped_page_eonhae_units, skipped_page_chinese_units, skipped_page_circles
            if active_chinese:
                flush_chinese()
            decision = eonhae.skip_page_decision(
                ko_cursor,
                active_run=active_run,
                active_chinese=(),
                circle_count=circle_count,
            )
            ko_cursor = decision.ko_cursor
            skipped_page_eonhae_units += decision.dropped_eonhae_units
            skipped_page_chinese_units += decision.dropped_chinese_units
            skipped_page_circles += decision.dropped_circles
            if active_run is not None:
                ordinal = active_run.circle_ordinal or 0
                key = f"{reason_page}:circle-{ordinal:03d}:ko-{ko_cursor.center}"
                report[key] = {
                    "volume": volume,
                    "run_page": active_run.events[0][0] if active_run.events else reason_page,
                    "pages": sorted(active_run.pages),
                    "circle_ordinal": ordinal,
                    "box_count": len(active_run.events),
                    "outcome": "dropped eonhae run",
                    "reason": "page skipped",
                }
            active_run = None
            active_chinese = []
            in_eonhae = False

        previous_page_seq: int | None = None
        for page in pages:
            if previous_page_seq is not None and page.seq > previous_page_seq + 1:
                skip_pending_page(page.id)
            previous_page_seq = page.seq
            page_report: dict[str, object] = {"volume": volume, "seq": page.seq + 1}
            out_pages[page.id] = page
            image_path = page_image_path(page)
            if image_path is None:
                page_report["outcome"] = "image not in cache"
                report[page.id] = page_report
                skip_pending_page(page.id)
                continue
            image = Image.open(image_path).convert("RGB")
            boxes = [box for box, _ in detector.boxes(image)]
            crops = [image.crop((b.x, b.y, b.x + b.w, b.y + b.h)) for b in boxes]
            probabilities = classifier.probabilities_many(crops) if crops else []
            ranked = [[classifier.classes[int(i)] for i in row.argsort()[::-1][:5]] for row in probabilities]
            page_width = image.width
            source_width = image.width
            source_height = image.height
            grid = eonhae.fit_column_grid(boxes, ranked, page_width)
            if grid is None:
                page_report["outcome"] = "no column grid"
                report[page.id] = page_report
                skip_pending_page(page.id, circle_count=sum(1 for labels in ranked if labels and labels[0] in eonhae.CIRCLES))
                continue
            layout = eonhae.page_layout(boxes, ranked, grid, in_eonhae=in_eonhae)
            eonhae_crops = [
                image.crop((event.box.x, event.box.y, event.box.x + event.box.w, event.box.y + event.box.h))
                for event in layout.events
                if event.kind == "eonhae"
            ]
            eonhae_probabilities = hangul_classifier.probabilities_many(eonhae_crops) if eonhae_crops else []
            eonhae_probability_rows = [
                tuple(float(value) for value in row)
                for row in eonhae_probabilities
            ]
            hanja_events = [event for event in layout.events if event.kind == "hanja"]
            alignment = eonhae.align_hanja(hanja_events, zh_chars, zh_cursor, known)
            page_report.update({
                "columns": len(grid.centers),
                "pitch": round(grid.pitch, 2),
                "boxes": len(boxes),
                "large_hanja": len(hanja_events),
                "reading_boxes": layout.reading_boxes,
                "eonhae_boxes": sum(event.kind == "eonhae" for event in layout.events),
                "zh_agreement": alignment.agreement,
                "zh_matched_hanja": alignment.matched.count(True),
                "zh": alignment.reason,
            })
            if alignment.offset is not None:
                next_zh_cursor = alignment.offset + alignment.consumed
            if not alignment.accepted:
                page_report["outcome"] = "left out"
                report[page.id] = page_report
                skip_pending_page(page.id, circle_count=sum(event.kind == "circle" for event in layout.events))
                continue
            zh_cursor = next_zh_cursor if alignment.offset is not None else zh_cursor
            in_eonhae = layout.end_in_eonhae

            sx = page.width / source_width if page.width else 1.0
            sy = page.height / source_height if page.height else 1.0
            raw_geometry = eonhae.eonhae_page_geometry(boxes, ranked, grid)
            page_geometry[page.id] = eonhae.EonhaePageGeometry(
                median_small_height=raw_geometry.median_small_height * sy,
                half_column_width=raw_geometry.half_column_width * sx,
            )
            hanja_index = 0
            eonhae_index = 0
            hanja_decisions = eonhae.hanja_unit_decisions(hanja_events, alignment)
            dropped_unjudged_hanja = 0
            for event in layout.events:
                scaled = scale_box(event.box, sx, sy)
                if event.kind == "hanja":
                    flush_eonhae(page.id)
                    decision = hanja_decisions[hanja_index]
                    if decision.emitted:
                        char = decision.char
                        verdict = decision.verdict
                        assert char is not None
                        active_chinese.append(eonhae.PendingUnit(
                            page.id,
                            scaled,
                            char,
                            event.top5,
                            verdict,
                            event.column,
                            {"zh_index": decision.stream_index} if decision.stream_index is not None else {},
                        ))
                    elif decision.matched and decision.verdict is None:
                        dropped_unjudged_hanja += 1
                    hanja_index += 1
                elif event.kind == "circle":
                    flush_eonhae(page.id)
                    flush_chinese()
                    circle_ordinal += 1
                    ko_circles_consumed += 1
                    active_run = eonhae.PendingRun(circle_ordinal=circle_ordinal)
                    active_run.pages.add(page.id)
                elif event.kind == "eonhae":
                    row = eonhae_probability_rows[eonhae_index] if eonhae_index < len(eonhae_probability_rows) else ()
                    eonhae_index += 1
                    if active_run is not None:
                        active_run.events.append((page.id, eonhae.LayoutEvent(
                            event.kind, scaled, event.top5, event.column, event.half
                        ), row))
                        active_run.pages.add(page.id)
            page_report["outcome"] = "cut"
            if dropped_unjudged_hanja:
                page_report["dropped_unjudged_hanja"] = dropped_unjudged_hanja
            report[page.id] = page_report
        if active_run is not None:
            flush_eonhae(pages[-1].id if pages else document.id)
        if active_chinese:
            flush_chinese()
        volume_reports[volume] = {
            "document_id": document.id,
            "ko_lines": len(ko_lines),
            "ko_phrases": len(ko_phrases),
            "ko_last_line_index": len(ko_lines) - 1,
            "ko_last_phrase_index": len(ko_phrases) - 1,
            "ko_cursor_end": ko_cursor.anchor,
            "ko_anchor_end": ko_cursor.anchor,
            "ko_dropped_since_accept_end": ko_cursor.dropped_since_accept,
            "ko_circles_consumed": ko_circles_consumed,
            "global_searches": ko_global_searches,
            "accepted_runs": ko_assignment_accepted,
            "mean_jump": sum(ko_jumps) / len(ko_jumps) if ko_jumps else None,
            "ko_global_searches": ko_global_searches,
            "ko_assignment_accepted_runs": ko_assignment_accepted,
            "ko_mean_jump": sum(ko_jumps) / len(ko_jumps) if ko_jumps else None,
            "dropped_units_disagree": dropped_units_disagree,
            "skipped_page_eonhae_units": skipped_page_eonhae_units,
            "skipped_page_chinese_units": skipped_page_chinese_units,
            "skipped_page_circles": skipped_page_circles,
        }

    page_reports = [value for value in report.values() if isinstance(value, dict) and "seq" in value]
    line_reports = [value for value in report.values() if isinstance(value, dict) and "outcome" in value and "seq" not in value]
    for volume in args.volume:
        pages_for_volume = [page for page in page_reports if page.get("volume") == volume]
        outcomes = Counter(str(page.get("outcome", "unknown")) for page in pages_for_volume)
        agreements = [float(page["zh_agreement"]) for page in pages_for_volume if "zh_agreement" in page]
        cut_agreements = [
            float(page["zh_agreement"])
            for page in pages_for_volume
            if page.get("outcome") == "cut" and "zh_agreement" in page
        ]
        volume_units = [unit for unit in units if unit.meta.get("volume") == volume]
        text_units = [unit for unit in volume_units if unit.meta.get("role") == "text"]
        eonhae_units = [unit for unit in volume_units if unit.meta.get("role") == "eonhae"]
        kept_runs = sum(
            1 for value in line_reports
            if value.get("outcome") == "kept eonhae run" and value.get("volume") == volume
        )
        dropped_runs = sum(
            1 for value in line_reports
            if value.get("outcome") == "dropped eonhae run" and value.get("volume") == volume
        )
        dropped_reasons = Counter(
            str(value.get("reason", "unknown")) for value in line_reports
            if value.get("outcome") == "dropped eonhae run" and value.get("volume") == volume
        )
        volume_reports[volume].update({
            "pages": len(pages_for_volume),
            "pages_cut": outcomes.get("cut", 0),
            "pages_left_out": {key: value for key, value in outcomes.items() if key != "cut"},
            "zh_agreement_min": min(agreements) if agreements else None,
            "zh_agreement_mean": sum(agreements) / len(agreements) if agreements else None,
            "cut_zh_agreement_min": min(cut_agreements) if cut_agreements else None,
            "cut_zh_agreement_mean": sum(cut_agreements) / len(cut_agreements) if cut_agreements else None,
            "hanja_units": len(text_units),
            "eonhae_units": len(eonhae_units),
            "hangul_units": sum(unit.script == "hangul" for unit in eonhae_units),
            "kept_eonhae_runs": kept_runs,
            "dropped_eonhae_runs": dropped_runs,
            "dropped_eonhae_reasons": dict(dropped_reasons),
        })
    report["_summary"] = volume_reports

    args.out.mkdir(parents=True, exist_ok=True)
    command = " ".join(["scripts/cut_eonhae.py", *(display_path(Path(arg)) if str(Path.home()) in arg else arg for arg in sys.argv[1:])])
    tables.write(args.out / "documents.parquet", list(out_documents.values()), Document)
    tables.write(args.out / "pages.parquet", [page for page_id, page in out_pages.items() if page_id in kept_page_ids], Page)
    tables.write(args.out / "lines.parquet", lines, Line)
    tables.write(args.out / "units.parquet", units, Unit, command=command)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    verdicts = [unit.meta["classifier_agrees"] for unit in units]
    print(json.dumps({
        "pages": len(kept_page_ids),
        "lines": len(lines),
        "units": len(units),
        "classifier_agrees": verdicts.count(True),
        "unverifiable": verdicts.count(None),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
