"""Character boxes on a page image, from an ONNX detector.

A page is cut into 1024x1024 tiles with 128 px of overlap, the geometry T20 uses to build the
training data, and the tiles at the right and bottom edge are padded with white. A detection is
mapped from tile coordinates to page coordinates, and duplicates that two overlapping tiles both
report are removed by score-ordered non-maximum suppression over the whole page: a box that
straddles a tile border comes out once, and a detection a border cut, which holds part of a
character whose rest is in the tile next door, loses to a kept box that contains it. `boxes_in` runs
the tiles that meet a region only.

The model takes one float32 tensor, `pixel_values`, of shape (1, 3, 1024, 1024): RGB, scaled to
[0, 1], normalised with the ImageNet mean and standard deviation, and no resizing. It returns
either the RT-DETR pair `logits` (1, queries, classes) and `pred_boxes` (1, queries, 4, centre form,
normalised to the tile), or one `dets` tensor (1, boxes, 5 or 6) of pixel `x1, y1, x2, y2`, score
and an optional label. Scores are class probabilities; with one class the model is class-agnostic.

The session runs on the CUDA provider when onnxruntime offers it and on the CPU provider otherwise.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .schema import Box

# Tiling geometry, the same one T20 cuts training tiles with.
TILE = 1024
OVERLAP = 128
STRIDE = TILE - OVERLAP
PAD = 255

# Preprocessing, the same one the training configuration pins.
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

# A detection narrower or shorter than this many pixels is dropped after clipping to the page.
MIN_SIZE = 2

# Share of a border-cut detection's area that lying inside a kept box takes for the cut detection to
# be dropped as the same character. A corner of four tiles leaves a sliver whose IoU is too low for
# the score threshold to reach.
CONTAIN = 0.9


def tile_origins(length: int, tile: int = TILE, overlap: int = OVERLAP) -> list[int]:
    """Origins along one axis of the tiles that cover `length` pixels.

    The first tile starts at 0, each following tile starts one stride further on, and the last tile
    reaches the end of the page, so the tiles cover the axis with no gap. A length at or under one
    tile gives the single origin 0.
    """
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    stride = tile - overlap
    if stride <= 0:
        raise ValueError(f"overlap {overlap} must be smaller than the tile size {tile}")
    if length <= tile:
        return [0]
    return [index * stride for index in range(math.ceil((length - overlap) / stride))]


def tiles(width: int, height: int, tile: int = TILE, overlap: int = OVERLAP) -> list[Box]:
    """Every tile of a `width` x `height` page, in reading order, each one `tile` pixels square."""
    if width <= 0 or height <= 0:
        raise ValueError(f"page must have a positive size, got {width}x{height}")
    return [
        Box(x=x, y=y, w=tile, h=tile)
        for y in tile_origins(height, tile, overlap)
        for x in tile_origins(width, tile, overlap)
    ]


def overlap(a: Box, b: Box) -> bool:
    """Whether two boxes share at least one pixel."""
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def _open(image: Image.Image | str | Path) -> Image.Image:
    """The page as an RGB image; a path is read and a mode other than RGB is converted."""
    if isinstance(image, (str, Path)):
        with Image.open(image) as handle:
            return handle.convert("RGB")
    return image if image.mode == "RGB" else image.convert("RGB")


def tile_array(page: Image.Image, tile: Box) -> np.ndarray:
    """One tile as a (1, 3, tile, tile) float32 batch, padded with white outside the page."""
    canvas = Image.new("RGB", (tile.w, tile.h), (PAD, PAD, PAD))
    right, bottom = min(tile.x + tile.w, page.width), min(tile.y + tile.h, page.height)
    left, top = max(tile.x, 0), max(tile.y, 0)
    if right > left and bottom > top:
        canvas.paste(page.crop((left, top, right, bottom)), (left - tile.x, top - tile.y))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    array = (array - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None])


def _area(boxes: np.ndarray) -> np.ndarray:
    """Area of `x1, y1, x2, y2` boxes, zero for a box that is turned inside out."""
    return np.clip(boxes[..., 2] - boxes[..., 0], 0.0, None) * np.clip(
        boxes[..., 3] - boxes[..., 1], 0.0, None
    )


def _intersection(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """Intersection area of one `x1, y1, x2, y2` box with a stack of them."""
    left = np.maximum(box[0], others[:, 0])
    top = np.maximum(box[1], others[:, 1])
    right = np.minimum(box[2], others[:, 2])
    bottom = np.minimum(box[3], others[:, 3])
    return np.clip(right - left, 0.0, None) * np.clip(bottom - top, 0.0, None)


def iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """IoU of one `x1, y1, x2, y2` box against a stack of them."""
    inter = _intersection(box, others)
    union = _area(box) + _area(others) - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1.0), 0.0)


def suppress(
    boxes: np.ndarray,
    scores: np.ndarray,
    threshold: float,
    clipped: np.ndarray | None = None,
    contain: float = CONTAIN,
) -> list[int]:
    """Indexes kept by greedy non-maximum suppression, highest score first.

    A box is dropped when it overlaps a kept box above `threshold`. A box that `clipped` marks as
    cut by a tile border is also dropped when `contain` of its area lies inside a kept box, which is
    what a character at a corner where four tiles meet leaves in the tile that holds a sliver of it.
    Boxes the border did not cut are considered before those it did, and ties go to the lower index,
    so the result does not depend on the order the boxes were found in.
    """
    order = np.argsort(-scores, kind="stable")
    if clipped is not None:
        order = order[np.argsort(clipped[order], kind="stable")]
    keep: list[int] = []
    while order.size:
        best = int(order[0])
        keep.append(best)
        if order.size == 1:
            break
        rest = order[1:]
        drop = iou(boxes[best], boxes[rest]) > threshold
        if clipped is not None:
            smaller = np.minimum(_area(boxes[best]), _area(boxes[rest]))
            inside = _intersection(boxes[best], boxes[rest]) > contain * np.maximum(smaller, 1e-6)
            drop |= clipped[rest] & inside
        order = rest[~drop]
    return keep


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


def cxcywh_to_xyxy(boxes: np.ndarray, size: int) -> np.ndarray:
    """Centre-form boxes normalised to the tile, as pixel `x1, y1, x2, y2` on the tile."""
    centre_x, centre_y, width, height = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    return np.stack(
        [
            (centre_x - width / 2) * size,
            (centre_y - height / 2) * size,
            (centre_x + width / 2) * size,
            (centre_y + height / 2) * size,
        ],
        axis=1,
    ).astype(np.float32)


def clipped_by_border(boxes: np.ndarray, tile: Box, width: int, height: int) -> np.ndarray:
    """Which detections touch a tile border that lies inside the page.

    Such a detection holds the part of a character that reaches into this tile; the rest of the
    character is in the tile next door. `boxes` are pixel `x1, y1, x2, y2` on the tile, `width` and
    `height` the size of the page.
    """
    flags = np.zeros(len(boxes), dtype=bool)
    if boxes.size == 0:
        return flags
    edge = np.float32(0.5)
    if tile.x > 0:
        flags |= boxes[:, 0] <= edge
    if tile.y > 0:
        flags |= boxes[:, 1] <= edge
    if tile.x + tile.w < width:
        flags |= boxes[:, 2] >= np.float32(tile.w) - edge
    if tile.y + tile.h < height:
        flags |= boxes[:, 3] >= np.float32(tile.h) - edge
    return flags


def post_process(
    boxes: np.ndarray,
    scores: np.ndarray,
    *,
    score: float = 0.3,
    nms: float | None = None,
    max_per_tile: int = 1500,
    clipped: np.ndarray | None = None,
    contain: float = CONTAIN,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Keep the detections that pass `score`, cap them and suppress overlaps.

    `boxes` are pixel `x1, y1, x2, y2`, either on one tile or over a whole page, and are expected to
    be clipped to their frame already. `nms` is the IoU above which a lower-scoring duplicate is
    dropped, and None leaves suppression to the caller. The result is the boxes, the scores and the
    `clipped` flags kept, highest score first, with the flags None when they were not given.
    """
    keep = np.flatnonzero(scores >= score)
    if keep.size > max_per_tile:
        keep = keep[np.argsort(-scores[keep], kind="stable")[:max_per_tile]]
    boxes, scores = boxes[keep], scores[keep]
    cut = None if clipped is None else clipped[keep]
    if boxes.size == 0:
        return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32), cut
    if nms is not None:
        order = np.asarray(suppress(boxes, scores, nms, clipped=cut, contain=contain), dtype=int)
        boxes, scores = boxes[order], scores[order]
        cut = None if cut is None else cut[order]
    return boxes, scores, cut


class Detector:
    """A detector over an exported ONNX model.

    `score` is the lowest score a detection keeps, `nms` the IoU above which a lower-scoring
    duplicate is dropped, and `max_per_tile` the number of detections one tile may contribute, taken
    by descending score. `session` replaces the onnxruntime session, which tests use to run a stub;
    `tile`, `overlap` and `format` override the geometry and the output layout.
    """

    def __init__(
        self,
        onnx_path: str | Path,
        score: float = 0.3,
        nms: float = 0.5,
        max_per_tile: int = 1500,
        *,
        session: Any | None = None,
        providers: Sequence[str] | None = None,
        tile: int = TILE,
        overlap: int = OVERLAP,
        format: str | None = None,
    ) -> None:
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score must lie in [0, 1], got {score}")
        if not 0.0 <= nms <= 1.0:
            raise ValueError(f"nms must lie in [0, 1], got {nms}")
        if max_per_tile < 1:
            raise ValueError(f"max_per_tile must be positive, got {max_per_tile}")
        if format not in (None, "rtdetr", "dets"):
            raise ValueError(f"unknown output format {format!r}")
        self.onnx_path = Path(onnx_path)
        self.score = float(score)
        self.nms = float(nms)
        self.max_per_tile = int(max_per_tile)
        self.tile = int(tile)
        self.overlap = int(overlap)
        self.format = format
        self._session = session if session is not None else self._build_session(providers)

    def _build_session(self, providers: Sequence[str] | None) -> Any:
        import onnxruntime as ort

        wanted = list(providers or ("CUDAExecutionProvider", "CPUExecutionProvider"))
        offered = ort.get_available_providers()
        usable = [name for name in wanted if name in offered]
        if not usable:
            raise RuntimeError(f"onnxruntime offers {offered}, none of {wanted}")
        # onnxruntime-gpu finds cuDNN and cuBLAS through the wheel's own copies or through
        # LD_LIBRARY_PATH; when they come from the nvidia packages, as they do here, the CUDA
        # provider fails until they are preloaded.
        preload = getattr(ort, "preload_dlls", None)
        if preload is not None and any(name.startswith("CUDA") for name in usable):
            try:
                preload()
            except (ImportError, OSError, RuntimeError):
                # A machine without the CUDA libraries keeps the CPU provider, and a session with
                # the CUDA provider still raises if it cannot use them.
                pass
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(str(self.onnx_path), sess_options=options, providers=usable)

    @property
    def input_name(self) -> str:
        inputs = getattr(self._session, "get_inputs", None)
        if inputs is None:
            return "pixel_values"
        described = inputs()
        return described[0].name if described else "pixel_values"

    def providers(self) -> list[str]:
        """The providers the session actually runs on."""
        described = getattr(self._session, "get_providers", None)
        return list(described()) if described is not None else []

    def boxes(self, image: Image.Image | str | Path) -> list[tuple[Box, float]]:
        """Every character box on the page, in page coordinates, highest score first."""
        page = _open(image)
        found = tiles(page.width, page.height, self.tile, self.overlap)
        return self._collect(page, found)

    def boxes_in(self, image: Image.Image | str | Path, region: Box) -> list[tuple[Box, float]]:
        """The boxes that meet `region`, in page coordinates, highest score first.

        Only the tiles that meet the region are run, so the boxes are the ones a whole-page run
        would return inside it and the geometry the model sees is unchanged.
        """
        page = _open(image)
        wanted = [
            tile for tile in tiles(page.width, page.height, self.tile, self.overlap) if overlap(tile, region)
        ]
        return [(box, score) for box, score in self._collect(page, wanted) if overlap(box, region)]

    def _collect(self, page: Image.Image, wanted: Iterable[Box]) -> list[tuple[Box, float]]:
        """Run every tile, map the detections to page coordinates and suppress duplicates."""
        found: list[np.ndarray] = []
        scores: list[float] = []
        clipped: list[bool] = []
        for tile in wanted:
            boxes, tile_scores = self._detect_tile(page, tile)
            cut = self._clipped(boxes, tile, page)
            for box, score, is_cut in zip(boxes, tile_scores, cut, strict=True):
                found.append([box[0] + tile.x, box[1] + tile.y, box[2] + tile.x, box[3] + tile.y])
                scores.append(float(score))
                clipped.append(bool(is_cut))
        if not found:
            return []
        stack = np.asarray(found, dtype=np.float32)
        score_array = np.asarray(scores, dtype=np.float32)
        # One clamp before suppression, so that a box the padding pushed past the page does not
        # suppress a real one through the clipped part.
        stack[:, 0] = np.clip(stack[:, 0], 0.0, page.width)
        stack[:, 1] = np.clip(stack[:, 1], 0.0, page.height)
        stack[:, 2] = np.clip(stack[:, 2], 0.0, page.width)
        stack[:, 3] = np.clip(stack[:, 3], 0.0, page.height)
        keep = suppress(stack, score_array, self.nms, clipped=np.asarray(clipped, dtype=bool))
        out: list[tuple[Box, float]] = []
        for index in keep:
            box = _to_box(stack[index], page.width, page.height)
            if box is not None:
                out.append((box, float(score_array[index])))
        out.sort(key=lambda pair: (-pair[1], pair[0].y, pair[0].x, pair[0].h, pair[0].w))
        return out

    @staticmethod
    def _clipped(boxes: np.ndarray, tile: Box, page: Image.Image) -> np.ndarray:
        return clipped_by_border(boxes, tile, page.width, page.height)

    def _detect_tile(self, page: Image.Image, tile: Box) -> tuple[np.ndarray, np.ndarray]:
        """Detections of one tile as pixel `x1, y1, x2, y2` on the tile and their scores."""
        outputs = self._session.run(None, {self.input_name: tile_array(page, tile)})
        boxes, scores = self._decode(outputs, max(tile.w, tile.h))
        if boxes.size == 0:
            return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32)
        boxes = boxes.astype(np.float32)
        boxes[:, 0::2] = np.clip(boxes[:, 0::2], 0.0, tile.w)
        boxes[:, 1::2] = np.clip(boxes[:, 1::2], 0.0, tile.h)
        # Suppression waits for the page-wide pass, where the boxes of every tile are together.
        boxes, scores, _ = post_process(boxes, scores, score=self.score, max_per_tile=self.max_per_tile)
        return boxes.astype(np.float32), scores.astype(np.float32)

    def _output_names(self, outputs: Sequence[np.ndarray]) -> list[str]:
        described = getattr(self._session, "get_outputs", None)
        if described is not None:
            names = [item.name for item in described()]
            if len(names) == len(outputs):
                return names
        return [f"output{index}" for index in range(len(outputs))]

    def _decode(self, outputs: Sequence[np.ndarray], size: int) -> tuple[np.ndarray, np.ndarray]:
        """Boxes on the tile and their scores, in the layout `format` names or the model implies."""
        names = self._output_names(outputs)
        tensors = {name: np.asarray(value) for name, value in zip(names, outputs, strict=True)}
        lowered = {name.lower(): name for name in names}
        logits_name = next((n for k, n in lowered.items() if "logit" in k), None)
        boxes_name = next((n for k, n in lowered.items() if "box" in k), None)
        dets_name = next((n for k, n in lowered.items() if "det" in k), None)
        layout = self.format
        if layout is None:
            if logits_name is not None and boxes_name is not None:
                layout = "rtdetr"
            elif dets_name is not None:
                layout = "dets"
            elif len(outputs) == 2:
                layout = "rtdetr"
            elif len(outputs) == 1 and np.asarray(outputs[0]).shape[-1] in (5, 6):
                layout = "dets"
            else:
                raise ValueError(f"cannot tell the output layout of {names}")
        if layout == "rtdetr":
            logits = tensors[logits_name] if logits_name else np.asarray(outputs[0])
            raw = tensors[boxes_name] if boxes_name else np.asarray(outputs[-1])
            scores = _sigmoid(np.asarray(logits, dtype=np.float32)[0])
            scores = scores.max(axis=-1) if scores.ndim > 1 else scores
            boxes = cxcywh_to_xyxy(np.asarray(raw, dtype=np.float32)[0], size)
        else:
            dets = np.asarray(tensors[dets_name] if dets_name else outputs[0], dtype=np.float32)[0]
            boxes, scores = dets[:, :4], dets[:, 4]
        return boxes, np.asarray(scores, dtype=np.float32)


def _to_box(box: np.ndarray, width: int, height: int) -> Box | None:
    """A page box, or None when the detection is thinner or shorter than `MIN_SIZE`."""
    left, top = max(0.0, float(box[0])), max(0.0, float(box[1]))
    right, bottom = min(float(box[2]), float(width)), min(float(box[3]), float(height))
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return None
    x, y = round(left), round(top)
    return Box(x=x, y=y, w=max(1, round(right) - x), h=max(1, round(bottom) - y))
