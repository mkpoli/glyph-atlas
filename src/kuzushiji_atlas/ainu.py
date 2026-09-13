"""Line boxes for the アイヌ関連資料 records, derived from the ink the detector finds.

The Ainu records are page transcriptions and nothing else: 8,212 lines of text with no boxes, because
the platform transcribes a page and the atlas has nothing to align a character to. Everything else in
the atlas arrives with line boxes — Honkoku-Lines ships them — so this derivation is the one piece of
machinery the plan never had to build.

The rule is the one measured in `docs/reports/card-status.md`: a vertical line of a woodblock print is
a column of ink, so the detector's character boxes are grouped into columns read right to left, and a
page's columns are paired with its transcribed lines when the two counts agree. A page where they do
not agree is left unresolved rather than guessed at, because a line box that stands for a different
number of lines than the transcription has is worse than no box at all.

`derive_directory` writes the columns it found to `columns.tsv` beside the dataset and sets the box of
every line it could pair. It stages nothing and runs no alignment: a caller that wants character units
runs the aligner over the directory afterwards, which is what `atlas ainu derive` does.
"""

from __future__ import annotations

import csv
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import Box, Line, Page, ReviewState, Unit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "work" / "ainu-records"
DEFAULT_ONNX = ROOT / "models" / "detector" / "artifacts" / "detector.onnx"
#: The operating point of the pilot run: the detector's score cutoff.
SCORE = 0.02
#: A column is a run of detections whose centres step by no more than this share of the median width.
GAP_RATIO = 0.5
#: Two such runs are one column when their centres are closer than this share of the median width.
MERGE_RATIO = 0.9
#: A gap of this share of the page's width or more separates two blocks of text, such as a spread's
#: two leaves. Only the widest gaps are cut, so a crowded text block stays one region.
REGION_SHARE = 0.05
#: A transcription of fewer than this many lines names a title or a caption rather than the page.
BODY_LINES = 4
#: The name a derived box's provenance carries, so a later run can find and withdraw it.
DERIVATION_METHOD = "ainu-ink-columns-v1"
#: How a line records that a person set its box. A machine run never replaces or withdraws those.
HUMAN_MATCH_METHODS = ("manual", "review", "adjudicated")
#: The review states a pipeline wrote. A unit a person reviewed is never retired by a machine run.
MACHINE_REVIEW = (ReviewState.MACHINE, ReviewState.REJECTED)
#: A column has to hold at least this share of the characters its line is transcribed with. A count
#: match alone is not evidence: on 蝦夷紀行 page 2 the twenty columns meet twenty transcribed lines
#: and the rightmost column holds four detections for a line of twenty characters, because the leaf
#: is torn at the edge and the column is a sliver of what the transcription names. Such a page pairs
#: by luck, and a line box that stands for four characters of twenty is worse than no box. The gate
#: is a floor on the evidence, not a claim about quality; a reviewer settles the page either way.
MIN_DETECTIONS_PER_CHARACTER = 0.5
#: The columns of `columns.tsv`, which is the measurement this module's rule was fixed on.
COLUMNS_FIELDS = (
    "page_id", "document_id", "width", "height", "characters", "columns", "regions",
    "region_columns", "largest_region_columns", "lines", "text_lines", "text_characters", "body",
    "title", "detections_per_character", "weakest_column", "median_column", "paired", "reason",
    "options",
)


def centre_of(box: Box) -> float:
    """The x centre of a detection, which is what the column rule steps along."""
    return box.x + box.w / 2


def columns_of(boxes: Sequence[Box], gap_ratio: float = GAP_RATIO,
               merge_ratio: float = MERGE_RATIO) -> list[list[int]]:
    """The detection indices grouped into columns, right to left.

    A column is a run of detections whose centres follow one another by no more than `gap_ratio` of
    the page's median character width, which is the step from one character to the next inside a
    line. Two runs are then merged when the white space between them is narrower than `merge_ratio`
    of that width, because a line whose characters lean or thin out pauses by more than a step
    without ending.

    Both thresholds were measured on a page of 蝦夷紀行 with ten visible lines and 195 detections,
    whose neighbouring columns of ink stand 8 to 22 px apart against a median character width of 30 px
    and whose line centres are 50 px apart. Two rules that look reasonable were measured and rejected:

    - Comparing the runs' *means* splits a leaning line. That line's characters wander 27 px sideways
      across three of them, so its two runs' means differ by 35 px, over any merge threshold that
      still keeps the next column (50 px) apart. It gave 13 columns for 10 lines.
    - Comparing *every* pair across runs merges unrelated columns, because some wandering character of
      a column sits close to a run that is nowhere near it. Over the full census it collapsed pages to
      a single column: a median of 0.20 columns a transcribed line, against 1.13 for this rule.
    """
    if not boxes:
        return []
    width = statistics.median(box.w for box in boxes)
    order = sorted(range(len(boxes)), key=lambda index: centre_of(boxes[index]))
    runs: list[list[int]] = [[order[0]]]
    for index in order[1:]:
        if centre_of(boxes[index]) - centre_of(boxes[runs[-1][-1]]) <= width * gap_ratio:
            runs[-1].append(index)
        else:
            runs.append([index])
    merged: list[list[int]] = [runs[0]]
    for run in runs[1:]:
        here = statistics.mean(centre_of(boxes[index]) for index in run)
        there = statistics.mean(centre_of(boxes[index]) for index in merged[-1])
        if here - there < width * merge_ratio:
            merged[-1].extend(run)
        else:
            merged.append(run)
    # The runs were built from the left, and a vertical line is read from the right.
    merged.reverse()
    return merged


def regions_of(boxes: Sequence[Box], share: float = REGION_SHARE) -> list[list[Box]]:
    """The page's detections split where the columns stop, so a spread's leaves are counted apart.

    A double-page scan holds a text block on one leaf and often cataloguing marks on the other, and a
    capture of two facing pages holds two text blocks. A gap of `share` of the page's width or more
    between neighbouring columns separates them, which no space inside a line reaches.
    """
    if not boxes:
        return []
    width = statistics.median(box.w for box in boxes)
    right = max(box.x + box.w for box in boxes)
    cut = max(width * 3.0, right * share)
    order = sorted(boxes, key=lambda box: -centre_of(box))
    chunks: list[list[Box]] = [[order[0]]]
    edge = centre_of(order[0])
    for box in order[1:]:
        if centre_of(box) < edge - cut:
            chunks.append([])
        chunks[-1].append(box)
        edge = centre_of(box)
    return chunks


def histogram(boxes: Sequence[Box], width: int = 100) -> str:
    """A one-line picture of where the ink sits across the page, for eyeballing a grouping."""
    if not boxes:
        return "(no detections)"
    right = max(box.x + box.w for box in boxes)
    bins = [0] * width
    for box in boxes:
        bins[min(width - 1, int(centre_of(box) / max(1, right) * width))] += 1
    peak = max(bins) or 1
    return "".join(" .:-=+*#@"[min(8, round(value / peak * 8))] for value in bins)


@dataclass
class Derivation:
    """What one page's ink columns say about its transcribed lines."""

    page_id: str
    columns: list[list[int]] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    reason: str = ""
    paired: bool = False
    #: Which transcribed line each column was paired with, in `columns` order. Empty when unpaired.
    pairing: list[Line] = field(default_factory=list)
    #: One evidence value per paired column: its detections over its line's characters.
    evidence: list[float] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def line_box(self, index: int, page: Page | None = None) -> Box | None:
        """The union of one column's detections, as the box of the line paired with it.

        A column is the ink of one line, so its box is the smallest rectangle that holds that ink. It
        is not grown: the aligner keeps a detection whose centre is inside the box, and the detections
        the detector already found are what the box was built from. `page` is accepted so a caller can
        hand a page in and have nothing change; the boxes are in the image's own pixels.
        """
        if not self.paired or not 0 <= index < len(self.columns):
            return None
        return union([self.boxes[position] for position in self.columns[index]])


def union(boxes: Iterable[Box]) -> Box | None:
    """The smallest box holding all of `boxes`, or None when there are none."""
    boxes = list(boxes)
    if not boxes:
        return None
    x = min(box.x for box in boxes)
    y = min(box.y for box in boxes)
    right = max(box.x + box.w for box in boxes)
    bottom = max(box.y + box.h for box in boxes)
    return Box(x=x, y=y, w=right - x, h=bottom - y)


def characters_of(line: Line) -> int:
    """The characters a transcribed line names, which is what its column has to account for."""
    return len(line.text or "")


def evidence_per_column(derivation: Derivation) -> list[float]:
    """Detections over transcribed characters, for each column and the line it was paired with.

    The gate is applied here rather than to the page: a page-wide average hides a column that holds
    almost no ink, because the other columns cover for it. A page whose first line is twenty
    characters and whose rightmost column holds two detections pairs four columns of 20, 20, 20 and 2
    detections at a page average of 0.78 while that first line's own column is at 0.10. It is a
    screening statistic and cannot prove that a column is the line it was paired with, nor that the
    reading order is right; only a reviewer settles that.
    """
    return [
        len(column) / characters_of(line) if characters_of(line) else 0.0
        for column, line in zip(derivation.columns, derivation.pairing, strict=True)
    ]


def evidence_per_character(derivation: Derivation, lines: Sequence[Line]) -> float:
    """The page's detections over its transcribed characters, the summary the census reports."""
    text_lines = [line for line in lines if (line.text or "").strip()]
    characters = sum(characters_of(line) for line in text_lines)
    if not characters:
        return 0.0
    return len(derivation.boxes) / characters


def transcribed_lines(lines: Sequence[Line]) -> list[Line]:
    """The lines of a page that carry text, in the order the platform published them.

    `Line.seq` is that order, and it is the one a pairing relies on; a line without a sequence sorts
    last rather than first, because a missing sequence is not evidence of position zero.
    """
    text_lines = [line for line in lines if (line.text or "").strip()]
    return sorted(text_lines, key=lambda line: (line.seq is None, line.seq if line.seq is not None else 0))


def derive_page(page: Page, lines: Sequence[Line], boxes: Sequence[Box],
                *, gap_ratio: float = GAP_RATIO, merge_ratio: float = MERGE_RATIO,
                body_lines: int = BODY_LINES,
                min_per_character: float = MIN_DETECTIONS_PER_CHARACTER) -> Derivation:
    """Pair one page's transcribed lines with the ink columns the detector found.

    Three things have to hold. There are exactly as many columns as transcribed lines, so the pairing
    is forced right to left and the first column is the first line, which is the order `Line.seq`
    records. There are enough lines for the transcription to be a page rather than a title. And every
    column holds enough ink to be the line paired with it: at least `min_per_character` detections for
    every character that line names. A page that fails any of them comes back unpaired, with the
    counts in `reason`, and no line box is written for it.
    """
    text_lines = transcribed_lines(lines)
    found = columns_of(boxes, gap_ratio, merge_ratio)
    derivation = Derivation(page_id=page.id, columns=found, boxes=list(boxes))
    if not found:
        derivation.reason = "no detection"
        return derivation
    if not text_lines:
        derivation.reason = "no transcribed line"
        return derivation
    if len(found) != len(text_lines):
        derivation.reason = f"{len(found)} columns for {len(text_lines)} lines"
        return derivation
    if len(text_lines) < body_lines:
        derivation.reason = f"{len(text_lines)} lines is a title, not a page"
        return derivation
    derivation.pairing = text_lines
    derivation.evidence = evidence_per_column(derivation)
    weakest = min(derivation.evidence)
    if weakest < min_per_character:
        # The gate is per column: the weakest pairing is what a page has to answer for.
        derivation.reason = f"weakest column {weakest:.2f} detections a character"
        derivation.pairing = []
        derivation.evidence = []
        return derivation
    derivation.paired = True
    derivation.reason = "paired"
    return derivation


def page_row(derivation: Derivation, page: Page, lines: Sequence[Line], *,
             body_lines: int = BODY_LINES, min_per_character: float = MIN_DETECTIONS_PER_CHARACTER,
             gap_ratio: float = GAP_RATIO, merge_ratio: float = MERGE_RATIO,
             region_share: float = REGION_SHARE) -> dict[str, Any]:
    """One measured page, in the columns `columns.tsv` holds.

    Every parameter the row depends on is passed in rather than read from the module, so a census run
    with `--body-lines` or `--merge-ratio` reports what it actually ran with. The per-column evidence
    of a paired page is reported as its weakest, median and strongest, because the page's mean is
    exactly the number that hid the thin column the gate now refuses.
    """
    text_lines = transcribed_lines(lines)
    regions = regions_of(derivation.boxes, region_share)
    region_columns = [len(columns_of(region, gap_ratio, merge_ratio)) for region in regions]
    evidence = sorted(derivation.evidence)
    options = f"body>={body_lines} min={min_per_character} gap={gap_ratio} merge={merge_ratio} " \
              f"region={region_share}"
    return {
        "page_id": page.id,
        "document_id": page.document_id,
        "width": page.width,
        "height": page.height,
        "characters": len(derivation.boxes),
        "columns": derivation.column_count,
        "regions": len(regions),
        "region_columns": ",".join(str(value) for value in region_columns),
        "largest_region_columns": max(region_columns) if region_columns else 0,
        "lines": len(text_lines),
        "text_lines": sum(len(line.text_raw or "") for line in text_lines),
        "text_characters": sum(len(line.text or "") for line in text_lines),
        "body": int(len(text_lines) >= body_lines),
        "title": (text_lines[0].text or "")[:40] if text_lines else "",
        "detections_per_character": round(evidence_per_character(derivation, text_lines), 3),
        "weakest_column": round(evidence[0], 3) if evidence else "",
        "median_column": round(evidence[len(evidence) // 2], 3) if evidence else "",
        "paired": int(derivation.paired),
        "reason": derivation.reason,
        "options": options,
    }


def write_columns(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Write the measurement beside the dataset and return the number of rows."""
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS_FIELDS), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def detector_for(onnx: Path | str = DEFAULT_ONNX, *, score: float = SCORE,
                 session: Any | None = None) -> Any:
    """The detector the derivation runs on: the pilot run's export and operating point."""
    from .detect import Detector

    return Detector(onnx, score=score, session=session)


def provenance() -> dict[str, str]:
    """What a derived box came from, for the line's own record.

    A line box is a claim about where a transcription's ink stands, and this one is a proposal rather
    than an import. Both fields the line model offers are used: `meta["derivation"]` is what lets a
    later run find the boxes it wrote and withdraw them, and `match_method` is the summary a reader
    sees on the line itself. A box a person set carries neither and is never touched.
    """
    return {"source": "ainu-derive", "method": DERIVATION_METHOD}


def derived_by_atlas(line: Line) -> bool:
    """Whether this line's box is one the derivation wrote rather than the import."""
    return (line.meta or {}).get("derivation", {}).get("source") == "ainu-derive"


def human_box(line: Line) -> bool:
    """Whether this line's box is anybody's but this derivation's, and so not a proposal's to touch.

    A box the derivation wrote carries its provenance, and only such a box may be replaced on a paired
    page or withdrawn on an unpaired one. Everything else with a box is left alone: a reviewer's
    (`match_method` of `manual` or `review`, or a `meta` that names a person's source), and equally an
    import's box, which carries no provenance and no method tag at all. The first version of this
    function required one of those markers and so treated an untagged imported box as the
    derivation's, which is exactly the mistake this docstring exists to prevent.
    """
    return line.box is not None and not derived_by_atlas(line)


def _unit_is_machine(unit: Unit) -> bool:
    """Whether a unit is the pipeline's own output, which a withdrawn line box invalidates."""
    return unit.method == "detect-align" and unit.review in MACHINE_REVIEW


def derive_dataset(
    directory: Path,
    *,
    detector: Any | None = None,
    out: Path | None = None,
    gap_ratio: float = GAP_RATIO,
    merge_ratio: float = MERGE_RATIO,
    body_lines: int = BODY_LINES,
    min_per_character: float = MIN_DETECTIONS_PER_CHARACTER,
    region_share: float = REGION_SHARE,
    pages: Sequence[str] | None = None,
    detections: dict[str, list[Box]] | None = None,
    cache: Path | None = None,
    onnx_path: Path | str = DEFAULT_ONNX,
    score: float = SCORE,
) -> dict[str, int]:
    """Detect every page of `directory`, write `columns.tsv`, and set the boxes it can pair.

    A page the derivation cannot pair has the boxes a previous derivation wrote withdrawn, so a run
    with stricter options does not leave the earlier, looser boxes behind, and a machine unit the
    alignment placed inside such a box is retired with it. A box a person set is never replaced and
    never withdrawn, whatever this run decides about its page.

    `pages=[]` derives nothing and `pages=None` derives everything. `detections` is page id to the
    detections to use, which lets a census compare grouping rules without running the detector again;
    `cache` is a file to read that map from and add to, keyed by page id. Without either, the detector
    runs over every page's cached image. Returns the counts.
    """
    from . import tables

    dataset = tables.Dataset(directory)
    if dataset.tables["lines"] is None or dataset.tables["pages"] is None:
        raise ValueError(f"{directory} needs lines and pages to derive line boxes")
    wanted = set(pages) if pages is not None else None
    if wanted is not None and not wanted:
        return _nothing()
    if any(value < 0 for value in (body_lines, min_per_character)) or gap_ratio < 0 or merge_ratio < 0:
        raise ValueError(
            f"negative option: body_lines={body_lines} min_per_character={min_per_character} "
            f"gap_ratio={gap_ratio} merge_ratio={merge_ratio}"
        )
    settings = detector_settings(onnx_path, score=score)
    found_map = dict(detections) if detections is not None else _read_cache(cache, settings=settings)
    cached_pages = len(found_map)

    lines_by_page: dict[str, list[Line]] = {}
    for line in dataset.read("lines"):
        if wanted is not None and line.page_id not in wanted:
            continue
        lines_by_page.setdefault(line.page_id or "", []).append(line)
    for found in lines_by_page.values():
        found.sort(key=lambda line: (line.seq if line.seq is not None else 0))

    counts = {"pages": 0, "paired": 0, "unpaired": 0, "lines": 0, "boxes": 0, "withdrawn": 0,
              "units-retired": 0, "failed": 0, "cache-entries": 0, "cache-reused": 0}
    rows: list[dict[str, Any]] = []
    # Only the fields this run owns are carried to the commit: `box`, the derivation's `meta` entry
    # and `match_method`. Everything else on the line is read again under the lock, so an edit a
    # reviewer made while the detector was running is not thrown away by replacing the whole row.
    updates: dict[str, dict[str, Any]] = {}
    page_sizes: dict[str, tuple[int, int]] = {}
    for page in sorted(dataset.read("pages"), key=lambda page: page.id):
        if wanted is not None and page.id not in wanted:
            continue
        if not page.width or not page.height:
            size = _cached_size(page)
            if size is not None:
                page_sizes[page.id] = size
        boxes = found_map.get(page.id)
        if boxes is None:
            if detector is None:
                # The model loads on the first page the cache cannot answer for, so a run whose
                # cache covers every page never builds an ONNX session.
                detector = detector_for(onnx_path, score=score)
            boxes = _detect(page, detector, counts)
            found_map[page.id] = boxes
            if cache is not None:
                # One page at a time, so an interrupted census keeps what it has computed. The
                # header was written by the whole-file write below; without one, write it now.
                if not cache.exists():
                    _write_cache(cache, {}, settings=settings)
                append_cache(cache, page.id, boxes)
        lines = lines_by_page.get(page.id, [])
        derivation = derive_page(page, lines, boxes, gap_ratio=gap_ratio, merge_ratio=merge_ratio,
                                 body_lines=body_lines, min_per_character=min_per_character)
        pairing = {line.id: index for index, line in enumerate(derivation.pairing)}
        for line in lines:
            proposal: dict[str, Any] | None = None
            if derivation.paired and line.id in pairing:
                if human_box(line):
                    # Somebody's box, not this derivation's: a proposal does not replace it.
                    continue
                box = derivation.line_box(pairing[line.id])
                if box is not None:
                    proposal = {"box": box, "derivation": provenance()}
            elif derived_by_atlas(line):
                # This run did not pair the page, so the box the last run wrote here is withdrawn
                # rather than left standing as the import's own.
                proposal = {"box": None, "derivation": None}
            if proposal is None:
                continue
            # What the proposal was computed from. The commit compares this against the row it finds
            # under the lock, so an edit made while the detector was running wins.
            updates[line.id] = {
                **proposal,
                "was": (line.box, line.match_method, derived_by_atlas(line)),
            }
            counts["boxes" if proposal["box"] is not None else "withdrawn"] += 1
        counts["pages"] += 1
        counts["lines"] += len(lines)
        counts["paired"] += int(derivation.paired)
        counts["unpaired"] += int(not derivation.paired)
        rows.append(page_row(derivation, page, lines, body_lines=body_lines,
                             min_per_character=min_per_character, gap_ratio=gap_ratio,
                             merge_ratio=merge_ratio, region_share=region_share))

    if cache is not None:
        # The whole-file write drops the duplicates the appends left and keeps the pages of an
        # earlier subset run, which the in-memory map still holds because it was read from the file.
        _write_cache(cache, found_map, [page.id for page in dataset.read("pages")], settings=settings)
        counts["cache-entries"] = len(found_map)
        counts["cache-reused"] = cached_pages
    counts["pages-sized"] = 0
    counts["stale"] = 0
    if updates or page_sizes:
        commit = _commit(directory, updates, page_sizes=page_sizes)
        counts["units-retired"] = commit["units-retired"]
        counts["stale"] = commit["stale"]
        counts["boxes"] -= len(commit["stale-boxes"])
        counts["withdrawn"] -= len(commit["stale-withdrawn"])
        counts["pages-sized"] = len(page_sizes)
    write_columns(out if out is not None else directory / "columns.tsv", rows)
    return counts


def _cached_size(page: Page) -> tuple[int, int] | None:
    """The pixel size of a page's cached image, or None when it is not cached or not readable."""
    from PIL import Image

    from . import images

    path = images.path_for(page.image)
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, ValueError):
        return None


def _commit(directory: Path, updates: dict[str, dict[str, Any]],
            page_sizes: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
    """Apply this run's line updates under the table lock and retire the units they invalidate.

    The lines are read again here rather than reused from the loop, and every proposal is checked
    against the row as it stands now: the detector ran for minutes, and a box that changed in the
    meantime is somebody's answer, not this run's to overwrite. A proposal whose baseline moved is
    dropped and counted as stale. What is written is the box, the match method and the derivation's
    own `meta` key — the rest of the line's `meta` is the row's, so a note a reviewer added while the
    detector ran survives. A line whose box is withdrawn loses the machine units the alignment placed
    in it, but never a unit a person reviewed and never another run's units. A page whose record
    states no pixel size has it filled from the image the cache holds. Returns how many units were
    retired; the stale count is written into `counts` by the caller through `_commit_counts`.
    """
    from . import tables

    applied: set[str] = set()
    stale: set[str] = set()
    with tables.locked(directory):
        lines = tables.read(directory / "lines.parquet", Line)
        for line in lines:
            update = updates.get(line.id)
            if update is None:
                continue
            was = update["was"]
            if human_box(line) or (line.box, line.match_method, derived_by_atlas(line)) != was:
                # Somebody set a box, or changed the one the proposal was computed from, after the
                # page was read. The row as it stands now is the answer.
                stale.add(line.id)
                continue
            line.box = update["box"]
            line.match_method = (update["derivation"] or {}).get("method")
            meta = {key: value for key, value in (line.meta or {}).items() if key != "derivation"}
            if update["derivation"] is not None:
                meta["derivation"] = update["derivation"]
            line.meta = meta
            applied.add(line.id)
        tables._write_unlocked(directory / "lines.parquet", lines, Line)

        if page_sizes:
            pages = tables.read(directory / "pages.parquet", Page)
            for page in pages:
                size = page_sizes.get(page.id)
                if size is not None and (not page.width or not page.height):
                    page.width, page.height = size
            tables._write_unlocked(directory / "pages.parquet", pages, Page)

        withdrawn = {line.id for line in lines
                     if line.id in applied and updates[line.id]["box"] is None}
        retired = 0
        if withdrawn and (directory / "units.parquet").exists():
            units = tables.read(directory / "units.parquet", Unit)
            kept: list[Unit] = []
            for unit in units:
                if unit.line_id in withdrawn and unit.active and _unit_is_machine(unit):
                    # A box that is gone cannot hold a machine placement; the unit is retired rather
                    # than deleted, so what the pipeline claimed is still auditable.
                    unit.active = False
                    retired += 1
                kept.append(unit)
            if retired:
                tables._write_unlocked(directory / "units.parquet", kept, Unit)
    return {
        "units-retired": retired,
        "stale": len(stale),
        "stale-boxes": [line_id for line_id in stale if updates[line_id]["box"] is not None],
        "stale-withdrawn": [line_id for line_id in stale if updates[line_id]["box"] is None],
    }


def _nothing() -> dict[str, int]:
    """The counts of a run that was asked for no page."""
    return {"pages": 0, "paired": 0, "unpaired": 0, "lines": 0, "boxes": 0, "withdrawn": 0,
            "units-retired": 0, "failed": 0, "cache-entries": 0, "cache-reused": 0}


def _detect(page: Page, detector: Any, counts: dict[str, int]) -> list[Box]:
    """One page's detections, from an object with `boxes(path)` or a callable taking the page.

    A callable is what a caller that already has the detections — or a test — hands over; it is
    detected through a protocol rather than a class so the census can answer from a cache and the
    tests can count calls without an ONNX session.
    """
    if callable(detector) and not hasattr(detector, "boxes"):
        return list(detector(page))
    return _detect_page(page, detector, counts)


def _detect_page(page: Page, detector: Any, counts: dict[str, int]) -> list[Box]:
    """The detections of one page, or an empty list when the page or the detector fails on it."""
    from . import images, net

    path = images.path_for(page.image)
    if path is None:
        try:
            images.fetch(page.image)
        except (images.ImageError, net.DownloadError):
            # A page this machine cannot get is counted and left unresolved; the census still runs.
            counts["failed"] += 1
            return []
        path = images.path_for(page.image)
    if path is None:
        counts["failed"] += 1
        return []
    try:
        return [box for box, _ in detector.boxes(path) if box.w > 0 and box.h > 0]
    except (OSError, ValueError, RuntimeError):
        counts["failed"] += 1
        return []


def detector_settings(onnx: Path | str = DEFAULT_ONNX, *, score: float = SCORE) -> dict[str, Any]:
    """What a detection run has to match for its cached boxes to be reusable.

    The model file's own hash, not its path: an export can be rebuilt in place, and boxes computed
    with the old weights would then be silently attributed to the new ones. A `--score` change is a
    different operating point and therefore different boxes.
    """
    import hashlib

    path = Path(onnx)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    return {"onnx": str(path), "sha256": digest, "score": score, "tile": 1024, "overlap": 128}


def _cache_header(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The first line of a cache file: what produced the boxes in it."""
    return {"kind": "kuzushiji-atlas-ainu-detections", "version": 1, "settings": settings}


def settings_of(cache: Path | None) -> dict[str, Any] | None:
    """The settings a cache file was written with, or None when it states none."""
    import json

    if cache is None or not cache.exists():
        return None
    with cache.open(encoding="utf-8") as handle:
        first = handle.readline()
    if not first.strip():
        return None
    record = json.loads(first)
    if record.get("kind") != _cache_header(None)["kind"]:
        # A cache written before headers existed is a bare list of pages; it states no settings.
        return None
    return record.get("settings")


def _read_cache(cache: Path | None, *, settings: dict[str, Any] | None = None) -> dict[str, list[Box]]:
    """The detections a previous run cached, or an empty map when there is no cache.

    A cache whose settings do not match `settings` is not read at all: reusing boxes computed at a
    different score or with a different export would attribute one model's output to another. A cache
    that states no settings is read, because it was written before this check existed, and the run
    that reads it says so in its counts.
    """
    import json

    if cache is None or not cache.exists():
        return {}
    found: dict[str, list[Box]] = {}
    with cache.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("kind") == _cache_header(None)["kind"] and "page_id" not in record:
                continue
            found[record["page_id"]] = [Box.model_validate(box) for box in record["boxes"]]
    if settings is not None:
        stated = settings_of(cache)
        if stated is not None and stated != settings:
            return {}
    return found


def _write_cache(cache: Path, found: dict[str, list[Box]],
                 order: Sequence[str] = (), *, settings: dict[str, Any] | None = None) -> None:
    """Write every page in `found` as JSON lines, so a later run needs no detector.

    `order` only decides the file's order — the pages of a dataset in their own sequence — and a page
    in `found` that it does not name is still written. The earlier version wrote a page only when
    `order` held it, so a caller that passed an empty order got a file with nothing but its header.
    """
    import json

    cache.parent.mkdir(parents=True, exist_ok=True)
    sequence = [page_id for page_id in order if page_id in found]
    sequence += [page_id for page_id in sorted(found) if page_id not in set(sequence)]
    with cache.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_cache_header(settings), ensure_ascii=False) + "\n")
        for page_id in sequence:
            record = {"page_id": page_id,
                      "boxes": [box.model_dump(mode="json") for box in found[page_id]]}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_cache(cache: Path, page_id: str, boxes: Sequence[Box]) -> None:
    """Add one page's detections to a cache file, so a long run is not lost when it is interrupted."""
    import json

    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("a", encoding="utf-8") as handle:
        record = {"page_id": page_id, "boxes": [box.model_dump(mode="json") for box in boxes]}
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
