"""Place unboxed transcription lines by line detection, OCR and text matching.

This is a second placement method beside `glyph_atlas.ainu`: the Ainu derivation pairs lines with
columns of detected ink, while this module follows the Honkoku-Lines method and never consults or
modifies that derivation. It detects text lines on the cached page image, recognises each detected
line with the existing PARSeq recogniser, and assigns page transcription lines to OCR lines by a
least-cost one-to-one edit-distance match. Each method owns only the boxes it writes.

The line detector preprocessing and output decoding are ported from ndl-lab/ndlkotenocr-lite
revision ede4283845cdc0ba2bda8b7ebfc3dc80b33c92c8, CC BY 4.0: `src/rtmdet.py`,
`src/config/ndl.yaml` and the line-crop handoff in `src/ocr.py`. The acceptance thresholds come from
`data/sources/honkoku-lines.yaml`: normalised Levenshtein distance at most 0.4 and length ratio from
0.85 to 1.20.

Empirical checks on three cached `work/honkoku-lines` pages found all 603 values in this
checkpoint's `labels` output to be 0. Its input was 1024 by 1024 despite the model filename.
On the 3142 by 2480 page, the 23 detections above 0.3 had median width/height 99/1375 pixels:
label 0 described individual text lines. These observations, rather than upstream documentation,
show that this export's label semantics do not match `src/config/ndl.yaml`'s per-index table.
That table is retained below for provenance only; raw labels are recorded, never used as a gate.
"""

from __future__ import annotations

import csv
import gc
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .schema import Box, Line, Page

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "cache/models/ndlkotenocr-lite"
DEFAULT_ONNX = MODEL_DIR / "rtmdet.onnx"
DEFAULT_SCORE = 0.3
METHOD = "ndlkoten-line-match-v1"
REPORT_FIELDS = ("page_id", "document_id", "detected_lines", "candidates", "matched", "reason")
MEAN_BGR = np.asarray([103.53, 116.28, 123.675], dtype=np.float32)
STD_BGR = np.asarray([57.375, 57.12, 58.395], dtype=np.float32)
MIN_SIZE = 2
MAX_DISTANCE = 0.4
MIN_LENGTH_RATIO = 0.85
MAX_LENGTH_RATIO = 1.20
NDL_CLASSES = {
    0: "text_block",
    1: "line_main",
    2: "line_caption",
    3: "line_ad",
    4: "line_note",
    5: "line_note_dummy",
    6: "block_fig",
    7: "block_ad",
    8: "block_pillar",
    9: "block_folio",
    10: "block_rubi",
    11: "block_chart",
    12: "block_eqn",
    13: "block_cfm",
    14: "block_eng",
    15: "table",
}


@dataclass(frozen=True)
class Detection:
    """One kept RTMDet line detection in page coordinates."""

    box: Box
    score: float
    class_id: int
    class_name: str = "line_main"


@dataclass(frozen=True)
class ReadLine:
    """A detection with the OCR text read from its page crop."""

    detection: Detection
    text: str


@dataclass(frozen=True)
class Match:
    """One assignment from a transcription line to a detected OCR line."""

    line: Line
    read: ReadLine
    distance: float
    length_ratio: float

    @property
    def accepted(self) -> bool:
        return (
            self.distance <= MAX_DISTANCE
            and MIN_LENGTH_RATIO <= self.length_ratio <= MAX_LENGTH_RATIO
        )


@dataclass(frozen=True)
class PageMatches:
    """The read-only result of running the method on one page."""

    page: Page
    candidates: list[Line]
    read_lines: list[ReadLine]
    matches: list[Match]
    reason: str = "processed"

    @property
    def accepted(self) -> list[Match]:
        return [match for match in self.matches if match.accepted]


class LineDetector:
    """RTMDet-s line detector for one full page image.

    The ONNX export returns `dets` in input-pixel coordinates and raw integer `labels`. The
    graph has already done NMS, so post-processing filters by score, rescales boxes from
    the session's input dimensions to the padded square image, expands each box vertically by two percent of
    its own height, then clips to the true page. CUDA uses a capped 1 GiB arena so this detector can
    share the machine with the extraction service.
    """

    def __init__(
        self,
        onnx_path: str | Path = DEFAULT_ONNX,
        *,
        score: float = DEFAULT_SCORE,
        session: Any | None = None,
        providers: Sequence[Any] | None = None,
    ) -> None:
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score must lie in [0, 1], got {score}")
        self.onnx_path = Path(onnx_path)
        self.score = float(score)
        self._session = session if session is not None else self._build_session(providers)
        shape = self._session.get_inputs()[0].shape
        if len(shape) != 4 or any(not isinstance(n, int) or n <= 0 for n in shape[2:]):
            raise ValueError(f"RTMDet requires fixed input height and width, got {shape}")
        self.input_height, self.input_width = shape[2:]

    def _build_session(self, providers: Sequence[Any] | None) -> Any:
        import onnxruntime as ort

        if providers is None:
            wanted: list[Any] = [
                (
                    "CUDAExecutionProvider",
                    {
                        "gpu_mem_limit": 1024 * 1024 * 1024,
                        "arena_extend_strategy": "kSameAsRequested",
                    },
                ),
                "CPUExecutionProvider",
            ]
        else:
            wanted = list(providers)
        offered = ort.get_available_providers()
        usable = [
            provider
            for provider in wanted
            if (provider[0] if isinstance(provider, tuple) else provider) in offered
        ]
        if not usable:
            raise RuntimeError(f"onnxruntime offers {offered}, none of {[p[0] if isinstance(p, tuple) else p for p in wanted]}")
        preload = getattr(ort, "preload_dlls", None)
        if preload is not None and any((p[0] if isinstance(p, tuple) else p).startswith("CUDA") for p in usable):
            try:
                preload()
            except (ImportError, OSError, RuntimeError):
                pass
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        return ort.InferenceSession(str(self.onnx_path), sess_options=options, providers=usable)

    @property
    def input_name(self) -> str:
        described = getattr(self._session, "get_inputs", None)
        if described is None:
            return "input"
        inputs = described()
        return inputs[0].name if inputs else "input"

    def providers(self) -> list[str]:
        described = getattr(self._session, "get_providers", None)
        return list(described()) if described is not None else []

    def boxes(self, image: Image.Image | str | Path) -> list[Detection]:
        page = _open(image)
        tensor = self.preprocess(page)
        outputs = self._session.run(None, {self.input_name: tensor})
        return self.decode(outputs, page.width, page.height)

    def preprocess(self, page: Image.Image) -> np.ndarray:
        side = max(page.width, page.height)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        canvas.paste(page, (0, 0))
        resized = np.asarray(canvas.resize((self.input_width, self.input_height)), dtype=np.float32)
        bgr = resized[:, :, ::-1]
        normalised = (bgr - MEAN_BGR) / STD_BGR
        return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None].astype(np.float32))

    def decode(self, outputs: Sequence[np.ndarray], page_width: int, page_height: int) -> list[Detection]:
        bboxes = np.asarray(outputs[0], dtype=np.float32).reshape(-1, 5)
        class_ids = np.asarray(outputs[1]).reshape(-1).astype(int)
        if bboxes.size == 0 or class_ids.size == 0:
            return []
        count = min(len(bboxes), len(class_ids))
        bboxes, class_ids = bboxes[:count], class_ids[:count]
        scores = bboxes[:, 4]
        keep = scores > self.score
        if not np.any(keep):
            return []
        side = float(max(page_width, page_height))
        boxes = bboxes[keep, :4].copy()
        scores = scores[keep]
        class_ids = class_ids[keep]
        boxes /= np.asarray([self.input_width, self.input_height] * 2, dtype=np.float32)
        boxes *= np.asarray([side, side, side, side], dtype=np.float32)
        heights = boxes[:, 3] - boxes[:, 1]
        boxes[:, 1] -= heights * 0.02
        boxes[:, 3] += heights * 0.02
        boxes[:, 0::2] = np.clip(boxes[:, 0::2], 0.0, float(page_width))
        boxes[:, 1::2] = np.clip(boxes[:, 1::2], 0.0, float(page_height))
        detections: list[Detection] = []
        for raw, score, class_id in zip(boxes, scores, class_ids, strict=True):
            box = _to_box(raw, page_width, page_height)
            if box is not None:
                detections.append(
                    Detection(box=box, score=float(score), class_id=int(class_id))
                )
        detections.sort(key=lambda item: (-item.score, item.box.y, item.box.x, item.box.h, item.box.w))
        return detections


def _open(image: Image.Image | str | Path) -> Image.Image:
    if isinstance(image, (str, Path)):
        with Image.open(image) as handle:
            return handle.convert("RGB")
    return image if image.mode == "RGB" else image.convert("RGB")


def _to_box(raw: np.ndarray, width: int, height: int) -> Box | None:
    left, top = max(0.0, float(raw[0])), max(0.0, float(raw[1]))
    right, bottom = min(float(raw[2]), float(width)), min(float(raw[3]), float(height))
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return None
    x, y = round(left), round(top)
    return Box(x=x, y=y, w=max(1, round(right) - x), h=max(1, round(bottom) - y))


def levenshtein(first: str, second: str) -> int:
    """Levenshtein edit distance with unit insertions, deletions and substitutions."""
    if first == second:
        return 0
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for row, char in enumerate(first, start=1):
        current = [row]
        for column, other in enumerate(second, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def normalised_distance(first: str, second: str) -> float:
    """Edit distance divided by the longer string length; two empty strings have distance 0."""
    denominator = max(len(first), len(second))
    return levenshtein(first, second) / denominator if denominator else 0.0


def length_ratio(first: str, second: str) -> float:
    """The shorter text length divided by the longer; two empty strings have ratio 1."""
    longer = max(len(first), len(second))
    return min(len(first), len(second)) / longer if longer else 1.0


def candidate_lines(lines: Sequence[Line], *, ignore_existing_boxes: bool = False) -> list[Line]:
    """Transcribed lines eligible for placement, in source sequence order."""
    return sorted(
        [
            line
            for line in lines
            if (ignore_existing_boxes or line.box is None) and (line.text or "").strip()
            and not line.page_scope
        ],
        key=lambda line: (line.seq is None, line.seq if line.seq is not None else 0, line.id),
    )


def assign(costs: np.ndarray) -> list[tuple[int, int]]:
    """Globally least-cost one-to-one assignment with SciPy's Hungarian implementation."""
    if costs.size == 0 or 0 in costs.shape:
        return []
    from scipy.optimize import linear_sum_assignment

    rows, columns = linear_sum_assignment(costs)
    return [(int(row), int(column)) for row, column in zip(rows, columns, strict=True)]


def match_read_lines(lines: Sequence[Line], read_lines: Sequence[ReadLine]) -> list[Match]:
    """Assign transcription lines to OCR lines by the globally least total normalised distance."""
    if not lines or not read_lines:
        return []
    costs = np.asarray(
        [
            [normalised_distance(line.text.strip(), read.text.strip()) for read in read_lines]
            for line in lines
        ],
        dtype=np.float64,
    )
    matches: list[Match] = []
    for line_index, read_index in assign(costs):
        line = lines[line_index]
        read = read_lines[read_index]
        transcription = line.text.strip()
        ocr_text = read.text.strip()
        matches.append(
            Match(
                line=line,
                read=read,
                distance=float(costs[line_index, read_index]),
                length_ratio=length_ratio(transcription, ocr_text),
            )
        )
    return matches


def page_matches(
    page: Page,
    lines: Sequence[Line],
    *,
    detector: Any,
    recognizer: Any,
    ignore_existing_boxes: bool = False,
) -> PageMatches:
    """Compute matches for one page without writing any dataset table."""
    image = _page_image(page)
    if image is None:
        return PageMatches(page=page, candidates=[], read_lines=[], matches=[], reason="no cached image")
    path, size = image
    if page.width and page.height and size != (page.width, page.height):
        return PageMatches(page=page, candidates=[], read_lines=[], matches=[], reason="image size mismatch")
    candidates = candidate_lines(
        [line for line in lines if line.page_id == page.id],
        ignore_existing_boxes=ignore_existing_boxes,
    )
    if not candidates:
        return PageMatches(page=page, candidates=[], read_lines=[], matches=[])
    detections = _detect(path, detector)
    if not ignore_existing_boxes:
        # Ink a boxed line already covers is not offered to another line, or a line whose own
        # detection is missing could take its neighbour's and put two boxes on the same ink.
        held = [line.box for line in lines if line.page_id == page.id and line.box is not None
                and not line.page_scope]
        detections = [item for item in detections
                      if not any(inside_share(item.box, box) >= 0.5 for box in held)]
    read_lines = _read_detections(path, detections, recognizer)
    matches = match_read_lines(candidates, read_lines)
    return PageMatches(page=page, candidates=candidates, read_lines=read_lines, matches=matches)


def _page_image(page: Page) -> tuple[Path, tuple[int, int]] | None:
    from . import images

    path = images.path_for(page.image)
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return path, (int(image.width), int(image.height))
    except (OSError, ValueError):
        return None


def _detect(path: Path, detector: Any) -> list[Detection]:
    if callable(detector) and not hasattr(detector, "boxes"):
        return [coerce_detection(item) for item in detector(path)]
    return [coerce_detection(item) for item in detector.boxes(path)]


def coerce_detection(item: Any) -> Detection:
    if isinstance(item, Detection):
        return item
    if isinstance(item, Box):
        return Detection(box=item, score=1.0, class_id=1, class_name=NDL_CLASSES[1])
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], Box):
        return Detection(box=item[0], score=float(item[1]), class_id=1, class_name=NDL_CLASSES[1])
    if isinstance(item, dict):
        box = item.get("box")
        if not isinstance(box, Box):
            box = Box.model_validate(box)
        class_id = int(item.get("class_id", item.get("class_index", 1)))
        return Detection(
            box=box,
            score=float(item.get("score", item.get("confidence", 1.0))),
            class_id=class_id,
            class_name=str(item.get("class_name", "line_main")),
        )
    raise TypeError(f"cannot use detection {item!r}")


def _read_detections(path: Path, detections: Sequence[Detection], recognizer: Any) -> list[ReadLine]:
    with Image.open(path) as image:
        page = image.convert("RGB")
        return [ReadLine(detection=detection, text=_recognize_crop(page, detection.box, recognizer)) for detection in detections]


def _recognize_crop(page: Image.Image, box: Box, recognizer: Any) -> str:
    crop = page.crop((box.x, box.y, box.x + box.w, box.y + box.h))
    if callable(recognizer) and not hasattr(recognizer, "sequence"):
        return str(recognizer(crop, box))
    method = getattr(recognizer, "read_text", None)
    if method is not None:
        return str(method(crop))
    sequence = getattr(recognizer, "sequence", None)
    alphabet = getattr(recognizer, "alphabet", None)
    if sequence is None or alphabet is None:
        # The single-character classifier on a whole line would "read" one character, which can
        # match a one-character transcription line exactly: no placement is better than that.
        raise RuntimeError("line matching needs the PARSeq sequence model; run scripts/fetch_review_ocr.py")
    from .review.suggestions import decode, preprocess

    input_info = sequence.get_inputs()[0]
    pixels = preprocess(crop, (input_info.shape[3], input_info.shape[2]))
    output = sequence.run(None, {input_info.name: pixels})[0]
    decoded = decode(output, alphabet, limit=None)
    return decoded[0]["text"] if decoded else ""


def line_match_meta(match: Match, *, detector_sha256: str, recognizer_sha256: str) -> dict[str, Any]:
    return {
        "ocr_text": match.read.text,
        "distance": match.distance,
        "length_ratio": match.length_ratio,
        "detector_sha256": detector_sha256,
        "recognizer_sha256": recognizer_sha256,
        "class": match.read.detection.class_name,
        "class_id": match.read.detection.class_id,
        "box": match.read.detection.box.model_dump(mode="json"),
    }


def written_by_this_method(line: Line) -> bool:
    if line.box is None or line.match_method != METHOD:
        return False
    recorded = (line.meta or {}).get("line_match", {}).get("box")
    return recorded is None or recorded == line.box.model_dump(mode="json")


def _recognizer_sha256(recognizer: Any) -> str:
    for engine in getattr(recognizer, "engines", []) or []:
        if engine.get("name") == "NDLkotenOCR":
            return str(engine.get("sha256") or "")
    path = MODEL_DIR / "parseq.onnx"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def detector_settings(onnx: Path | str = DEFAULT_ONNX, *, score: float = DEFAULT_SCORE) -> dict[str, Any]:
    path = Path(onnx)
    return {
        "onnx": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "",
        "score": score,
        "input": "session-shape",
        "labels": "score-only-raw-labels-v2",
    }


def _cache_header(settings: dict[str, Any] | None) -> dict[str, Any]:
    return {"kind": "glyph-atlas-line-detections", "version": 1, "settings": settings}


def _read_cache(cache: Path | None, *, settings: dict[str, Any] | None = None) -> dict[str, list[Detection]]:
    if cache is None or not cache.exists():
        return {}
    found: dict[str, list[Detection]] = {}
    with cache.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("kind") == _cache_header(None)["kind"] and "page_id" not in record:
                if settings is not None and record.get("settings") not in (None, settings):
                    return {}
                continue
            found[record["page_id"]] = [coerce_detection(item) for item in record["detections"]]
    return found


def _write_cache(
    cache: Path,
    found: dict[str, list[Detection]],
    order: Sequence[str] = (),
    *,
    settings: dict[str, Any] | None = None,
) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    sequence = [page_id for page_id in order if page_id in found]
    sequence += [page_id for page_id in sorted(found) if page_id not in set(sequence)]
    with cache.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_cache_header(settings), ensure_ascii=False) + "\n")
        for page_id in sequence:
            handle.write(json.dumps(_cache_record(page_id, found[page_id]), ensure_ascii=False) + "\n")


def append_cache(cache: Path, page_id: str, detections: Sequence[Detection]) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_cache_record(page_id, detections), ensure_ascii=False) + "\n")


def _cache_record(page_id: str, detections: Sequence[Detection]) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "detections": [
            {
                "box": detection.box.model_dump(mode="json"),
                "score": detection.score,
                "class_id": detection.class_id,
                "class_name": detection.class_name,
            }
            for detection in detections
        ],
    }


def detector_for(onnx: Path | str = DEFAULT_ONNX, *, score: float = DEFAULT_SCORE, session: Any | None = None) -> LineDetector:
    return LineDetector(onnx, score=score, session=session)


def recognizer_for() -> Any:
    from .review.suggestions import Recognizer

    return Recognizer()


def _collect_detections(
    pages: Sequence[Page],
    lines_by_page: dict[str, list[Line]],
    found: dict[str, list[Detection]],
    *,
    detector: Any | None,
    onnx_path: Path | str,
    score: float,
    cache: Path | None = None,
) -> int:
    """Finish detection before loading PARSeq, releasing the owned detector's CUDA arena."""
    reused = 0
    for page in pages:
        if not candidate_lines(lines_by_page.get(page.id, []), ignore_existing_boxes=True):
            continue
        image = _page_image(page)
        if image is None or (page.width and page.height and image[1] != (page.width, page.height)):
            continue
        if page.id in found:
            reused += 1
            continue
        if detector is None:
            detector = detector_for(onnx_path, score=score)
        found[page.id] = _detect(image[0], detector)
        if cache is not None:
            append_cache(cache, page.id, found[page.id])
    del detector
    gc.collect()
    return reused


def match_dataset(
    directory: Path,
    *,
    pages: Sequence[str] | None = None,
    out: Path | None = None,
    detector: Any | None = None,
    recognizer: Any | None = None,
    cache: Path | None = None,
    onnx_path: Path | str = DEFAULT_ONNX,
    score: float = DEFAULT_SCORE,
) -> dict[str, int]:
    """Run the line detector and matcher over a dataset and write this method's proposals."""
    from . import tables

    dataset = tables.Dataset(directory)
    if dataset.tables["lines"] is None or dataset.tables["pages"] is None:
        raise ValueError(f"{directory} needs lines and pages to match line boxes")
    wanted = set(pages) if pages is not None else None
    if wanted is not None and not wanted:
        write_report(out if out is not None else directory / "line-match.tsv", [])
        return _nothing()

    lines_by_page = _lines_by_page(dataset, wanted)
    settings = detector_settings(onnx_path, score=score)
    found_map = _read_cache(cache, settings=settings)
    selected_pages = [
        page for page in sorted(dataset.read("pages"), key=lambda item: item.id)
        if wanted is None or page.id in wanted
    ]
    if cache is not None:
        _write_cache(cache, found_map, settings=settings)
    cached_pages = _collect_detections(
        selected_pages, lines_by_page, found_map, detector=detector,
        onnx_path=onnx_path, score=score, cache=cache,
    )
    counts = {
        "pages": 0,
        "matched pages": 0,
        "candidates": 0,
        "matched": 0,
        "withdrawn": 0,
        "failed": 0,
        "stale": 0,
        "units-retired": 0,
        "cache-entries": 0,
        "cache-reused": 0,
    }
    rows: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    recognizer_sha256 = _recognizer_sha256(recognizer) if recognizer is not None else ""
    detector_sha256 = settings["sha256"]

    for page in selected_pages:
        lines = lines_by_page.get(page.id, [])
        proposals = [
            line.model_copy(update={"box": None}) if written_by_this_method(line) else line
            for line in lines
        ]
        if recognizer is None and found_map.get(page.id) and candidate_lines(proposals):
            recognizer = recognizer_for()
            recognizer_sha256 = _recognizer_sha256(recognizer)
        result = page_matches(
            page, proposals, detector=lambda path, page_id=page.id: found_map.get(page_id, []), recognizer=recognizer,
        )
        reason = result.reason
        counts["failed"] += int(reason != "processed")
        candidates, read_lines = result.candidates, result.read_lines
        accepted = result.accepted
        accepted_by_id = {match.line.id: match for match in accepted}
        for line in lines:
            update: dict[str, Any] | None = None
            match = accepted_by_id.get(line.id)
            if match is not None and (line.box is None or written_by_this_method(line)):
                update = {
                    "box": match.read.detection.box,
                    "line_match": line_match_meta(
                        match,
                        detector_sha256=detector_sha256,
                        recognizer_sha256=recognizer_sha256,
                    ),
                    "match_confidence": 1.0 - match.distance,
                }
            elif reason == "processed" and match is None and written_by_this_method(line):
                update = {"box": None, "line_match": None, "match_confidence": None}
            if update is None:
                continue
            update["was"] = (line.box, line.match_method)
            updates[line.id] = update
            counts["matched" if update["box"] is not None else "withdrawn"] += 1
        counts["pages"] += 1
        counts["matched pages"] += int(bool(accepted))
        counts["candidates"] += len(candidates)
        rows.append(
            {
                "page_id": page.id,
                "document_id": page.document_id,
                "detected_lines": len(read_lines),
                "candidates": len(candidates),
                "matched": len(accepted),
                "reason": reason,
            }
        )

    if cache is not None:
        _write_cache(cache, found_map, [page.id for page in dataset.read("pages")], settings=settings)
        counts["cache-entries"] = len(found_map)
        counts["cache-reused"] = cached_pages
    if updates:
        commit = _commit(directory, updates)
        counts["stale"] = commit["stale"]
        counts["units-retired"] = commit["units-retired"]
        counts["matched"] -= len(commit["stale-matched"])
        counts["withdrawn"] -= len(commit["stale-withdrawn"])
    write_report(out if out is not None else directory / "line-match.tsv", rows)
    return counts


def _lines_by_page(dataset: Any, wanted: set[str] | None = None) -> dict[str, list[Line]]:
    lines_by_page: dict[str, list[Line]] = {}
    for line in dataset.read("lines"):
        if wanted is not None and line.page_id not in wanted:
            continue
        lines_by_page.setdefault(line.page_id, []).append(line)
    for lines in lines_by_page.values():
        lines.sort(key=lambda item: (item.seq is None, item.seq if item.seq is not None else 0, item.id))
    return lines_by_page


def _commit(directory: Path, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from . import tables

    stale: set[str] = set()
    applied: set[str] = set()
    with tables.locked(directory):
        dataset = tables.Dataset(directory)
        path = dataset._path("lines")
        lines = tables.read(path, Line)
        for line in lines:
            update = updates.get(line.id)
            if update is None:
                continue
            if (line.box, line.match_method) != update["was"]:
                stale.add(line.id)
                continue
            if update["box"] is not None and line.box is not None and not written_by_this_method(line):
                stale.add(line.id)
                continue
            if update["box"] is None and line.match_method != METHOD:
                stale.add(line.id)
                continue
            line.box = update["box"]
            line.match_method = METHOD if update["box"] is not None else None
            line.match_confidence = update["match_confidence"]
            meta = {key: value for key, value in (line.meta or {}).items() if key != "line_match"}
            if update["line_match"] is not None:
                meta["line_match"] = update["line_match"]
            line.meta = meta
            applied.add(line.id)
        tables._write_unlocked(path, lines, Line, shard=path.is_dir())
        withdrawn = {line_id for line_id in applied if updates[line_id]["box"] is None}
        retired = 0
        if withdrawn and (directory / "units.parquet").exists():
            from .ainu import _unit_is_machine
            from .schema import Unit

            units = tables.read(directory / "units.parquet", Unit)
            for unit in units:
                if unit.line_id in withdrawn and unit.active and _unit_is_machine(unit):
                    # As in `ainu._commit`: a withdrawn box cannot hold a machine placement, and the
                    # unit is retired rather than deleted so the claim stays auditable.
                    unit.active = False
                    retired += 1
            if retired:
                tables._write_unlocked(directory / "units.parquet", units, Unit)
    return {
        "units-retired": retired,
        "stale": len(stale),
        "stale-matched": [line_id for line_id in stale if updates[line_id]["box"] is not None],
        "stale-withdrawn": [line_id for line_id in stale if updates[line_id]["box"] is None],
    }


def write_report(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REPORT_FIELDS), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _nothing() -> dict[str, int]:
    return {
        "pages": 0,
        "matched pages": 0,
        "candidates": 0,
        "matched": 0,
        "withdrawn": 0,
        "failed": 0,
        "stale": 0,
        "units-retired": 0,
        "cache-entries": 0,
        "cache-reused": 0,
    }


def evaluate_dataset(
    directory: Path,
    *,
    limit: int | None = None,
    detector: Any | None = None,
    recognizer: Any | None = None,
    onnx_path: Path | str = DEFAULT_ONNX,
    score: float = DEFAULT_SCORE,
) -> dict[str, float | int]:
    """Evaluate predicted line boxes against existing boxes without writing any table."""
    from . import tables

    dataset = tables.Dataset(directory)
    if dataset.tables["lines"] is None or dataset.tables["pages"] is None:
        raise ValueError(f"{directory} needs lines and pages to evaluate line matching")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must not be negative, got {limit}")
    pages = sorted(dataset.read("pages"), key=lambda page: page.id)
    if limit is not None:
        pages = pages[:limit]
    wanted = {page.id for page in pages}
    lines_by_page = _lines_by_page(dataset, wanted)
    found: dict[str, list[Detection]] = {}
    _collect_detections(pages, lines_by_page, found, detector=detector, onnx_path=onnx_path, score=score)
    eligible = 0
    matched = 0
    good = 0
    own = 0
    skipped = 0
    ious: list[float] = []
    for page in pages:
        lines = [line for line in lines_by_page.get(page.id, []) if line.box is not None and (line.text or "").strip()]
        if not lines:
            continue
        if recognizer is None and found.get(page.id):
            recognizer = recognizer_for()
        result = page_matches(
            page, lines, detector=lambda path, page_id=page.id: found.get(page_id, []),
            recognizer=recognizer, ignore_existing_boxes=True,
        )
        if result.reason != "processed":
            skipped += 1
            continue
        eligible += len(lines)
        for match in result.accepted:
            if match.line.box is None:
                continue
            predicted = match.read.detection.box
            value = box_iou(predicted, match.line.box)
            ious.append(value)
            matched += 1
            good += int(value >= 0.5)
            # Reference boxes may be padded well beyond the ink (Honkoku-Lines' are), which keeps
            # IoU low for a correct box; the placement question is whose line the box lies in.
            best = max(lines, key=lambda line: inside_share(predicted, line.box))
            own += int(best.id == match.line.id and inside_share(predicted, match.line.box) >= 0.5)
    return {
        "pages": len(pages),
        "skipped": skipped,
        "eligible": eligible,
        "matched": matched,
        "matched_share": matched / eligible if eligible else 0.0,
        "in_own_line": own,
        "in_own_line_share": own / matched if matched else 0.0,
        "iou_ge_0.5": good,
        "iou_ge_0.5_share": good / matched if matched else 0.0,
        "median_iou": float(np.median(np.asarray(ious, dtype=np.float64))) if ious else 0.0,
    }


def inside_share(inner: Box, outer: Box) -> float:
    """The share of `inner`'s area that lies within `outer`."""
    left, top = max(inner.x, outer.x), max(inner.y, outer.y)
    right = min(inner.x + inner.w, outer.x + outer.w)
    bottom = min(inner.y + inner.h, outer.y + outer.h)
    area = inner.w * inner.h
    return max(0, right - left) * max(0, bottom - top) / area if area > 0 else 0.0


def box_iou(first: Box, second: Box) -> float:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.w, second.x + second.w)
    bottom = min(first.y + first.h, second.y + second.h)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = first.w * first.h + second.w * second.h - intersection
    return intersection / union if union > 0 else 0.0
