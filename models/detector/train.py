"""Train the class-agnostic character detector and measure it.

The data is the COCO split the training data build writes under `work/detector/`: one image entry per 1024x1024 tile,
with the cached page it is cut from and the origin of the tile on that page, and one annotation per
character box clipped to the tile, carrying `source_unit_id`. A tile is cut from the cached page at
the recorded origin when the run reaches it and padded with white at the right and bottom, so the
split stays small and a page is read once per epoch. Annotations marked `iscrowd` (the `unreadable`
units) are not trained on, and a detection that lands in one is neither a hit nor a false positive.

The model is RT-DETR with a ResNet-18 backbone through `transformers`, class-agnostic
(`num_labels: 1`), with `num_queries` above the densest tile's box count, which the run checks
before it starts. Training is mixed precision and writes one checkpoint an epoch plus the best one
by validation F1. The metrics go to `artifacts/metrics.json`: precision, recall,
F1 and mean IoU at IoU 0.5 over the tiles of `val` for every epoch, and with `--test` the whole-page
metrics of `test` at the score chosen on `val`, overall, by production type and by box-size decile.

    python models/detector/train.py --config models/detector/config.yaml
    python models/detector/train.py --config models/detector/config.yaml --profile
    python models/detector/train.py --config models/detector/config.yaml --test

The checkpoint is fetched from Hugging Face on the first run; `HF_HOME=cache/huggingface` keeps it
under `cache/`, as the conventions ask.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from PIL import Image

from glyph_atlas import detect
from glyph_atlas.schema import Box

ROOT = Path(__file__).resolve().parents[2]


# --- data ------------------------------------------------------------------------------------


@dataclass
class Tile:
    """One tile of the split: where it comes from and what it holds."""

    key: str
    """The page the tile belongs to, so that tiles of one page are evaluated together."""

    origin: tuple[int, int]
    size: int
    boxes: np.ndarray
    """`(n, 4)` float32 `x1, y1, x2, y2` in tile pixels, clipped to the tile."""

    sources: list[str | None] = field(default_factory=list)
    """The `source_unit_id` of every box, None when the split does not record one."""

    ignores: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), dtype=np.float32))
    """`(m, 4)` boxes of `iscrowd` regions, in tile pixels."""

    page: Path | None = None
    """The cached page image the tile is cut from."""

    file: Path | None = None
    """A materialised tile image, used instead of the page when the split points at one."""

    page_size: tuple[int, int] | None = None
    """The size of the page the tile was cut from, which says which tile borders cut a character."""

    production: str = "unknown"


def read_splits(path: Path) -> dict[str, str]:
    """`bid` to production type, from the split table `scripts/build_codh_split.py` writes.

    The file opens with comment lines, then a tab separated `bid, title, production, split` header,
    as `scripts/build_codh_split.py` writes it.
    """
    if not path.exists():
        return {}
    rows: dict[str, str] = {}
    columns: dict[str, int] | None = None
    with path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if columns is None:
                if "bid" not in fields or "production" not in fields:
                    return {}
                columns = {name: index for index, name in enumerate(fields)}
                continue
            if len(fields) > max(columns["bid"], columns["production"]):
                rows[fields[columns["bid"]]] = fields[columns["production"]]
    return rows


def _origin_of(entry: dict) -> tuple[int, int]:
    origin = entry.get("origin", entry.get("tile_origin"))
    if isinstance(origin, (list, tuple)) and len(origin) == 2:
        return int(origin[0]), int(origin[1])
    if "origin_x" in entry or "origin_y" in entry:
        return int(entry.get("origin_x", 0)), int(entry.get("origin_y", 0))
    return int(entry.get("tile_x", 0)), int(entry.get("tile_y", 0))


def _path_of(entry: dict) -> Path | None:
    for key in ("cache_path", "page_path", "path", "file_name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return Path(value)
    return None


def _page_size_of(entry: dict) -> tuple[int, int] | None:
    width, height = entry.get("page_width"), entry.get("page_height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return None


def _key_of(entry: dict, fallback: Path | None) -> str:
    for name in ("page", "page_id", "page_url", "document_id", "bid", "item_id"):
        value = entry.get(name)
        if value:
            return str(value)
    return str(fallback) if fallback else str(entry.get("id"))


def _production_of(entry: dict, splits: dict[str, str]) -> str:
    stated = entry.get("production")
    if stated:
        return str(stated)
    for name in ("bid", "document_id", "book"):
        value = entry.get(name)
        if value and str(value) in splits:
            return splits[str(value)]
    return "unknown"


def _resolve(name: Path, roots: list[Path | None]) -> Path | None:
    """The first of `roots` that holds `name`, either as a relative path or by its file name."""
    if name.is_absolute() and name.exists():
        return name
    for root in roots:
        if root is None:
            continue
        for candidate in (root / name, root / name.name):
            if candidate.exists():
                return candidate
    return None


def _clip_xywh(bbox: list[float], size: int) -> np.ndarray | None:
    """`x, y, w, h` as `x1, y1, x2, y2` inside a `size` square, or None when nothing is left."""
    x1 = max(0.0, float(bbox[0]))
    y1 = max(0.0, float(bbox[1]))
    x2 = min(float(size), float(bbox[0]) + float(bbox[2]))
    y2 = min(float(size), float(bbox[1]) + float(bbox[3]))
    if x2 - x1 < 1.0 or y2 - y1 < 1.0:
        return None
    return np.asarray([x1, y1, x2, y2], dtype=np.float32)


def read_split(
    path: Path,
    *,
    cache: Path | None = None,
    splits: dict[str, str] | None = None,
    materialised: Path | None = None,
) -> list[Tile]:
    """Read one COCO split into tiles.

    A tile whose annotations carry `source_unit_id` can be joined back into the source boxes of its
    page. A split that lists materialised tiles instead of pages is read from them; otherwise the
    page and the origin are kept and the tile is cut when the run reaches it. `splits` is the table
    of `bid` to production type; a tile whose book the table disagrees about stops the run, since
    the production breakdown of the metrics rests on it.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    images = {int(entry["id"]): entry for entry in document.get("images", [])}
    annotations: dict[int, list[dict]] = {key: [] for key in images}
    for annotation in document.get("annotations", []):
        annotations[int(annotation["image_id"])].append(annotation)
    tables = splits or {}
    tiles: list[Tile] = []
    for key, entry in images.items():
        size = int(entry.get("tile_size", entry.get("width", detect.TILE)))
        origin = _origin_of(entry)
        stated = entry.get("production")
        listed = tables.get(str(entry.get("bid")))
        if stated and listed and str(stated) != listed:
            raise ValueError(
                f"{path}: {entry.get('bid')} is {stated} in the split and {listed} in data/splits/codh.tsv"
            )
        page: Path | None = None
        file: Path | None = None
        named = entry.get("tile_path") or entry.get("tile_file")
        if named:
            file = _resolve(Path(str(named)), [materialised, cache])
        else:
            page_name = _path_of(entry)
            if page_name is not None:
                found = _resolve(page_name, [cache, materialised])
                if found is None:
                    page = page_name
                elif materialised is not None and materialised in found.parents:
                    file = found
                else:
                    page = found
        boxes, sources, ignores = [], [], []
        for row in annotations[key]:
            bbox = row.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            if row.get("iscrowd"):
                clipped = _clip_xywh(bbox, size)
                if clipped is not None:
                    ignores.append(clipped)
                continue
            clipped = _clip_xywh(bbox, size)
            if clipped is not None:
                boxes.append(clipped)
                sources.append(row.get("source_unit_id"))
        tiles.append(
            Tile(
                key=_key_of(entry, page or file),
                origin=origin,
                size=size,
                boxes=np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
                sources=sources,
                ignores=np.asarray(ignores, dtype=np.float32).reshape(-1, 4),
                page=page,
                file=file,
                page_size=_page_size_of(entry),
                production=_production_of(entry, tables),
            )
        )
    return tiles


def tile_image(tile: Tile) -> Image.Image:
    """The tile as an image, cut from the cached page or read from the materialised file."""
    if tile.file is not None:
        with Image.open(tile.file) as handle:
            return handle.convert("RGB")
    if tile.page is None:
        raise FileNotFoundError(f"{tile.key}: the split names no image for this tile")
    with Image.open(tile.page) as handle:
        page = handle.convert("RGB")
        canvas = Image.new("RGB", (tile.size, tile.size), (detect.PAD, detect.PAD, detect.PAD))
        left, top = max(tile.origin[0], 0), max(tile.origin[1], 0)
        right = min(tile.origin[0] + tile.size, page.width)
        bottom = min(tile.origin[1] + tile.size, page.height)
        if right > left and bottom > top:
            canvas.paste(page.crop((left, top, right, bottom)), (left - tile.origin[0], top - tile.origin[1]))
        return canvas


def tile_tensor(
    tile: Tile, image: Image.Image, *, augment: bool, rng: random.Random | None = None
) -> torch.Tensor:
    """A tile as the (3, size, size) float32 tensor the model takes."""
    array = np.asarray(image, dtype=np.float32) / 255.0
    if augment:
        # Scans differ in contrast and exposure; a character keeps its shape under both.
        rng = rng or random.Random(0)
        mean = float(array.mean())
        array = np.clip((array - mean) * rng.uniform(0.85, 1.15) + mean, 0.0, 1.0)
        array = np.clip(array * rng.uniform(0.85, 1.15), 0.0, 1.0)
    array = (array - np.asarray(detect.MEAN, dtype=np.float32)) / np.asarray(detect.STD, dtype=np.float32)
    return torch.from_numpy(np.ascontiguousarray(array.transpose(2, 0, 1)))


class Tiles(torch.utils.data.Dataset):
    """The tiles of one split, cut from the cache as the run reaches them."""

    def __init__(self, tiles: list[Tile], *, augment: bool, seed: int = 0) -> None:
        self.tiles = tiles
        self.augment = augment
        self.seed = seed

    def __len__(self) -> int:
        return len(self.tiles)

    def __getitem__(self, index: int) -> dict[str, Any]:
        tile = self.tiles[index]
        rng = random.Random(f"{self.seed}:{index}")
        pixels = tile_tensor(tile, tile_image(tile), augment=self.augment, rng=rng)
        boxes = tile.boxes.astype(np.float32)
        size = float(tile.size)
        centres = np.stack(
            [
                (boxes[:, 0] + boxes[:, 2]) / 2 / size,
                (boxes[:, 1] + boxes[:, 3]) / 2 / size,
                (boxes[:, 2] - boxes[:, 0]) / size,
                (boxes[:, 3] - boxes[:, 1]) / size,
            ],
            axis=1,
        ).astype(np.float32)
        return {
            "pixel_values": pixels,
            "labels": {
                "class_labels": torch.zeros(len(boxes), dtype=torch.long),
                "boxes": torch.from_numpy(centres),
            },
            "index": index,
        }


def collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pixel_values": torch.stack([item["pixel_values"] for item in batch]),
        "labels": [item["labels"] for item in batch],
        "index": [item["index"] for item in batch],
    }


# --- metrics ---------------------------------------------------------------------------------


@dataclass
class Tally:
    """Matches of predictions against truth, with the counts a rate is computed from."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    ious: list[float] = field(default_factory=list)
    truth_areas: list[float] = field(default_factory=list)
    hit_areas: list[float] = field(default_factory=list)

    def add(self, other: Tally) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.ious.extend(other.ious)
        self.truth_areas.extend(other.truth_areas)
        self.hit_areas.extend(other.hit_areas)

    def rates(self) -> dict[str, float]:
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0
        recall = self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "mean_iou": float(np.mean(self.ious)) if self.ious else 0.0,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
        }


def match(
    boxes: np.ndarray,
    scores: np.ndarray,
    truth: np.ndarray,
    ignores: np.ndarray,
    *,
    iou_threshold: float,
) -> Tally:
    """Greedy match of predictions to truth boxes at `iou_threshold`, best score first.

    One truth box takes one prediction. A prediction that matches no truth box but overlaps an
    `ignore` region by `iou_threshold` counts as neither a hit nor a false positive: the region is a
    part of the page the annotations do not cover, such as an unreadable unit or a ruby gloss.
    """
    tally = Tally()
    tally.fn = len(truth)
    tally.truth_areas = [float((row[2] - row[0]) * (row[3] - row[1])) for row in truth]
    if len(boxes) == 0:
        return tally
    taken = np.zeros(len(truth), dtype=bool)
    for index in np.argsort(-scores, kind="stable"):
        box = boxes[index]
        if len(truth):
            overlaps = detect.iou(box, truth)
            overlaps[taken] = 0.0
            best = int(np.argmax(overlaps))
            if overlaps[best] >= iou_threshold:
                taken[best] = True
                tally.tp += 1
                tally.fn -= 1
                tally.ious.append(float(overlaps[best]))
                tally.hit_areas.append(tally.truth_areas[best])
                continue
        if len(ignores) and float(np.max(detect.iou(box, ignores))) >= iou_threshold:
            continue
        tally.fp += 1
    return tally


def deciles(truth_areas: list[float], hit_areas: list[float]) -> list[dict[str, float]]:
    """Recall per decile of truth box area, smallest boxes first."""
    if not truth_areas:
        return []
    edges = np.quantile(np.asarray(truth_areas), np.linspace(0.0, 1.0, 11)[1:-1])
    bounds = np.concatenate([[0.0], edges, [math.inf]])
    counts = np.zeros(10, dtype=int)
    hits = np.zeros(10, dtype=int)
    for area in truth_areas:
        counts[min(int(np.searchsorted(bounds, area, side="right")) - 1, 9)] += 1
    for area in hit_areas:
        hits[min(int(np.searchsorted(bounds, area, side="right")) - 1, 9)] += 1
    return [
        {
            "decile": index + 1,
            "max_area": float(bounds[index + 1]),
            "truth": int(counts[index]),
            "hit": int(hits[index]),
            "recall": float(hits[index] / counts[index]) if counts[index] else 0.0,
        }
        for index in range(10)
    ]


def page_extent(tiles: list[Tile]) -> tuple[int, int]:
    """The size of the page a group of tiles belongs to.

    The split records the page size; without it the furthest edge a tile reaches stands in, which
    counts a border at the page edge as interior and so suppresses a little more than the detector
    does.
    """
    stated = next((tile.page_size for tile in tiles if tile.page_size), None)
    if stated is not None:
        return stated
    return (
        max(tile.origin[0] + tile.size for tile in tiles),
        max(tile.origin[1] + tile.size for tile in tiles),
    )


def tile_curve(
    tiles: list[Tile],
    detections: list[tuple[np.ndarray, np.ndarray]],
    grid: list[float],
) -> list[dict[str, float]]:
    """Tile precision, recall, F1 and mean IoU at every score of `grid`.

    The matching runs once per tile, in descending score order, and every grid point reads a prefix
    of it, so a whole curve costs one pass. The scores of a freshly initialised classification head
    are compressed near zero and climb as the head learns, so a curve at a fixed threshold says
    little about a run; the curve says where the model is.
    """
    curves = {float(score): Tally() for score in grid}
    for tile, (boxes, scores) in zip(tiles, detections, strict=True):
        truth = tile.boxes
        if len(scores) == 0:
            for tally in curves.values():
                tally.fn += len(truth)
            continue
        taken = np.zeros(len(truth), dtype=bool)
        order = np.argsort(-scores, kind="stable")
        matched = np.zeros(len(order), dtype=bool)
        ious = np.zeros(len(order), dtype=np.float64)
        for position, index in enumerate(order):
            if not len(truth):
                break
            overlaps = detect.iou(boxes[index], truth)
            overlaps[taken] = 0.0
            best = int(np.argmax(overlaps))
            if overlaps[best] >= IOU:
                taken[best] = True
                matched[position] = True
                ious[position] = float(overlaps[best])
        # Compare in float32, the width the scores and the thresholds are used at everywhere else,
        # so that a detection scored exactly at a grid point is kept rather than lost to the last
        # bit of a float64 conversion.
        ordered = np.asarray(scores, dtype=np.float32)[order]
        hits = np.cumsum(matched)
        for score, tally in curves.items():
            count = int(np.searchsorted(-ordered, -np.float32(score), side="right"))
            tp = int(hits[count - 1]) if count else 0
            tally.tp += tp
            tally.fp += count - tp
            tally.fn += len(truth) - tp
            if tp:
                tally.ious.extend(ious[:count][matched[:count]].tolist())
    return [{"score": float(score), **curves[float(score)].rates()} for score in grid]


def recall_at(curve: list[dict[str, float]], precision: float) -> dict[str, float]:
    """The largest recall on the curve among the points whose precision reaches `precision`.

    This is the shape the pilot's acceptance is written in, joint precision at a coverage, so a
    curve reported this way is what the alignment is tuned against. When no point reaches the
    precision, the recall is zero and `score` is None.
    """
    reaching = [row for row in curve if row["precision"] >= precision]
    if not reaching:
        return {"precision_target": precision, "recall": 0.0, "score": None, "precision": 0.0}
    best = max(reaching, key=lambda row: (row["recall"], row["score"]))
    return {
        "precision_target": precision,
        "recall": best["recall"],
        "score": best["score"],
        "precision": best["precision"],
    }


def tile_metrics(tiles: list[Tile], detections: list[tuple[np.ndarray, np.ndarray]]) -> Tally:
    """Matches of the tile detections against the tile annotations."""
    tally = Tally()
    for tile, (boxes, scores) in zip(tiles, detections, strict=True):
        tally.add(
            match(
                boxes,
                scores,
                tile.boxes,
                tile.ignores,
                iou_threshold=IOU,
            )
        )
    return tally


def page_truth(tiles: list[Tile]) -> dict[str, np.ndarray]:
    """Ground truth of every page in page coordinates, one entry per source box.

    A source box that several tiles carry a clipped part of is counted once: the parts are joined,
    which restores the box the training data build started from. A split without `source_unit_id` falls back to the
    rounded page box, which counts a box that straddles a border once per tile.
    """
    pages: dict[str, dict[Any, list[float]]] = {}
    for tile in tiles:
        rows = pages.setdefault(tile.key, {})
        for index, box in enumerate(tile.boxes):
            offset = np.asarray(
                [tile.origin[0], tile.origin[1], tile.origin[0], tile.origin[1]], dtype=np.float32
            )
            page = (box + offset).tolist()
            source = tile.sources[index] if index < len(tile.sources) else None
            counted = source if source is not None else tuple(round(value) for value in page)
            if counted in rows:
                current = rows[counted]
                rows[counted] = [
                    min(current[0], page[0]),
                    min(current[1], page[1]),
                    max(current[2], page[2]),
                    max(current[3], page[3]),
                ]
            else:
                rows[counted] = page
    return {
        key: np.asarray(list(rows.values()), dtype=np.float32).reshape(-1, 4) for key, rows in pages.items()
    }


def page_metrics(
    tiles: list[Tile],
    detections: list[tuple[np.ndarray, np.ndarray]],
    *,
    score: float,
    nms: float,
    max_per_tile: int,
    iou_threshold: float,
) -> tuple[Tally, dict[str, Tally], list[dict[str, float]]]:
    """Match whole pages: the tiles of a page merged, against the source boxes of that page."""
    grouped: dict[str, list[int]] = {}
    for index, tile in enumerate(tiles):
        grouped.setdefault(tile.key, []).append(index)
    truth = page_truth(tiles)
    overall, by_production = Tally(), {}
    for key, members in grouped.items():
        boxes, values, cut = [], [], []
        width, height = page_extent([tiles[index] for index in members])
        for index in members:
            tile = tiles[index]
            found, found_scores = detections[index]
            frame = Box(x=tile.origin[0], y=tile.origin[1], w=tile.size, h=tile.size)
            flags = detect.clipped_by_border(found, frame, width, height)
            offset = np.asarray(
                [tile.origin[0], tile.origin[1], tile.origin[0], tile.origin[1]], dtype=np.float32
            )
            for box, value, flag in zip(found, found_scores, flags, strict=True):
                boxes.append((box + offset).tolist())
                values.append(float(value))
                cut.append(bool(flag))
        kept, kept_scores, _ = detect.post_process(
            np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
            np.asarray(values, dtype=np.float32),
            score=score,
            nms=nms,
            max_per_tile=max_per_tile * max(len(members), 1),
            clipped=np.asarray(cut, dtype=bool),
        )
        tally = match(
            kept,
            kept_scores,
            truth.get(key, np.zeros((0, 4), dtype=np.float32)),
            np.zeros((0, 4), dtype=np.float32),
            iou_threshold=iou_threshold,
        )
        overall.add(tally)
        by_production.setdefault(tiles[members[0]].production, Tally()).add(tally)
    return overall, by_production, deciles(overall.truth_areas, overall.hit_areas)


# --- model -----------------------------------------------------------------------------------

IOU = 0.5
"""Matched at IoU 0.5, the threshold this module measures at."""


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_model(config: dict[str, Any]) -> Any:
    from transformers import RTDetrForObjectDetection

    model = config["model"]
    return RTDetrForObjectDetection.from_pretrained(
        model["checkpoint"],
        revision=model["revision"],
        num_labels=int(model["num_labels"]),
        num_queries=int(model["num_queries"]),
        ignore_mismatched_sizes=bool(model["ignore_mismatched_sizes"]),
    )


def read_state(path: Path) -> dict[str, Any]:
    """What a checkpoint holds: the model state, and for a run of this script the rest of the run.

    A bare state dictionary is accepted as well, so that weights saved by hand can be measured.
    """
    if not path.exists():
        raise SystemExit(f"{path} is missing")
    state = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "model" in state:
        return state
    return {"model": state}


def optimizer_for(model: Any, config: dict[str, Any]) -> torch.optim.Optimizer:
    training = config["training"]
    backbone, head = [], []
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            (backbone if ".backbone." in name else head).append(parameter)
    groups = [
        {"params": head, "lr": float(training["lr"])},
        {"params": backbone, "lr": float(training["backbone_lr"])},
    ]
    return torch.optim.AdamW(groups, weight_decay=float(training["weight_decay"]))


def scheduler_for(optimizer: torch.optim.Optimizer, config: dict[str, Any], steps: int) -> Any:
    from transformers.optimization import get_cosine_schedule_with_warmup

    return get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(config["training"]["warmup_steps"]),
        num_training_steps=max(1, steps),
    )


def autocast_of(precision: str) -> Any:
    if precision == "bf16":
        return torch.autocast("cuda", dtype=torch.bfloat16)
    if precision == "fp16":
        return torch.autocast("cuda", dtype=torch.float16)
    return torch.autocast("cuda", enabled=False)


def decode(outputs: Any, sizes: list[int]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Detections of a batch as pixel `x1, y1, x2, y2` on the tile and class-agnostic scores."""
    logits = outputs.logits.detach().float()
    scores = torch.sigmoid(logits).amax(dim=-1).cpu().numpy().astype(np.float32)
    raw = outputs.pred_boxes.detach().float().cpu().numpy().astype(np.float32)
    out = []
    for position, size in enumerate(sizes):
        boxes = detect.cxcywh_to_xyxy(raw[position], size)
        if boxes.size:
            boxes[:, 0::2] = np.clip(boxes[:, 0::2], 0.0, size)
            boxes[:, 1::2] = np.clip(boxes[:, 1::2], 0.0, size)
        out.append((boxes, scores[position]))
    return out


def model_boxes(
    model: Any,
    tiles: list[Tile],
    loader: Any,
    *,
    precision: str,
    score: float,
    max_per_tile: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Run the tiles and return the detections of each, in tile pixels, suppression left out."""
    out: list[tuple[np.ndarray, np.ndarray]] = [
        (np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32))
    ] * len(tiles)
    model.eval()
    with torch.no_grad():
        for batch in loader:
            pixels = batch["pixel_values"].to(model.device, non_blocking=True)
            with autocast_of(precision):
                outputs = model(pixel_values=pixels)
            sizes = [tiles[index].size for index in batch["index"]]
            for index, (boxes, scores) in zip(batch["index"], decode(outputs, sizes), strict=True):
                boxes, scores, _ = detect.post_process(boxes, scores, score=score, max_per_tile=max_per_tile)
                out[index] = (boxes, scores)
    return out


def loader_for(
    tiles: list[Tile],
    *,
    augment: bool,
    batch_size: int,
    workers: int,
    seed: int,
) -> Any:
    return torch.utils.data.DataLoader(
        Tiles(tiles, augment=augment, seed=seed),
        batch_size=batch_size,
        shuffle=augment,
        num_workers=workers,
        collate_fn=collate,
        pin_memory=torch.cuda.is_available(),
        drop_last=False,
        persistent_workers=workers > 0,
    )


def snapshot(
    model: Any, optimizer: Any, scheduler: Any, config: dict[str, Any], epoch: int, metrics: dict,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What a checkpoint holds: the state of the run, and the arguments that produced it."""
    return {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "epoch": epoch,
        "config": config,
        "runtime": runtime,
        "metrics": metrics,
    }


def peak_vram() -> str:
    if not torch.cuda.is_available():
        return "no cuda"
    return f"peak {torch.cuda.max_memory_allocated() / 2**30:.2f}GiB"


def relative_to_root(path: Path | str | None) -> str | None:
    """A path as the repository sees it, so a report never carries an absolute home directory.

    A path outside the repository is written as its name alone rather than its full path: a metrics
    file is a durable record, and the machine's layout is not part of the measurement.
    """
    if path is None:
        return None
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(ROOT))
    except ValueError:
        return candidate.name


def file_sha256(path: Path | str) -> str:
    """The checksum of a file, so a measured checkpoint can be identified exactly."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_of(
    args: Any,
    *,
    config: dict[str, Any],
    epochs: int,
    batch_size: int,
    precision: str,
    workers: int,
    seed: int,
    splits: dict[str, str],
    cache: Path,
    materialised: Path,
) -> dict[str, Any]:
    """The arguments a run is actually using, resolved and path-normalised.

    `--epochs` on the command line sets the scheduler's horizon, and `training.epochs` in a stored
    config kept the YAML's 24 through a six-epoch run, so a checkpoint's own config could not say
    which schedule trained it. This is what both the metrics file and every checkpoint carry instead.
    `steps_per_epoch` and `scheduler_total_steps` are left null here and filled by the caller that
    knows the loader's length, from this same object rather than a second calculation.
    """
    training = config["training"]
    return {
        "epochs": int(epochs),
        "epochs_configured": int(training["epochs"]),
        "batch_size": int(batch_size),
        "grad_accumulation": int(training["grad_accumulation"]),
        "workers": int(workers),
        "steps_per_epoch": None,
        "scheduler_total_steps": None,
        "warmup_steps": int(training["warmup_steps"]),
        "scheduler": str(training["scheduler"]),
        "lr": float(training["lr"]),
        "backbone_lr": float(training["backbone_lr"]),
        "precision": str(precision),
        "seed": int(seed),
        "seed_argument": getattr(args, "seed", None),
        "val_limit": getattr(args, "val_limit", None),
        "train_limit": getattr(args, "limit", None),
        "resumed_from": relative_to_root(getattr(args, "resume", None)),
        "weights": relative_to_root(getattr(args, "weights", None)),
        "config": relative_to_root(getattr(args, "config", None)),
        "data": relative_to_root(config["data"]["directory"]),
        "image_cache": relative_to_root(cache),
        "materialised_tiles": relative_to_root(materialised),
        "splits": {name: str(value) for name, value in splits.items()},
    }


def choose_score(
    val_tiles: list[Tile],
    detections: list[tuple[np.ndarray, np.ndarray]],
    config: dict[str, Any],
    *,
    nms: float,
    max_per_tile: int,
) -> tuple[float, list[dict[str, float]]]:
    """The score of the grid with the best F1 on `val`, highest score on a tie."""
    table = []
    for score in config["evaluation"]["score_grid"]:
        tally, _, _ = page_metrics(
            val_tiles,
            detections,
            score=float(score),
            nms=nms,
            max_per_tile=max_per_tile,
            iou_threshold=float(config["evaluation"]["iou"]),
        )
        table.append({"score": float(score), **tally.rates()})
    best = max(table, key=lambda row: (row["f1"], row["score"]))
    return float(best["score"]), table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--data", type=Path, default=None, help="override data.directory")
    parser.add_argument("--out", type=Path, default=None, help="override artifacts.directory")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="tiles of train to use")
    parser.add_argument("--val-limit", type=int, default=None, help="tiles of val to use")
    parser.add_argument("--precision", choices=["bf16", "fp16", "fp32"], default=None)
    parser.add_argument("--device", default=None, help="cuda, cuda:1, cpu")
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument(
        "--weights",
        type=Path,
        default=None,
        help="checkpoint to measure with --test; --resume names one too",
    )
    parser.add_argument("--test", action="store_true", help="measure the val and test splits and stop")
    parser.add_argument("--profile", action="store_true", help="time a few steps and stop")
    parser.add_argument("--profile-steps", type=int, default=10)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    evaluation = config["evaluation"]
    training = config["training"]
    epochs = args.epochs if args.epochs is not None else int(training["epochs"])
    batch_size = args.batch_size if args.batch_size is not None else int(training["batch_size"])
    precision = args.precision or str(training["precision"])
    workers = args.workers if args.workers is not None else int(training["num_workers"])
    nms = float(evaluation["nms"])
    max_per_tile = int(evaluation["max_per_tile"])
    seed = int(training["seed"])
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    data = Path(config["data"]["directory"])
    data = (args.data or (data if data.is_absolute() else ROOT / data)).resolve()
    out = args.out or (ROOT / config["artifacts"]["directory"])
    out.mkdir(parents=True, exist_ok=True)
    splits = config["data"]["splits"]
    cache = ROOT / config["data"]["image_cache"]
    materialised = data / "tiles"
    tables = read_splits(ROOT / config["data"]["split_table"])

    def read(name: str, limit: int | None = None) -> list[Tile]:
        path = data / splits[name]
        if not path.exists():
            raise SystemExit(
                f"{path} is missing: scripts/build_detector_data.py writes the detector data there"
            )
        tiles = read_split(path, cache=cache, splits=tables, materialised=materialised)
        if limit:
            tiles = tiles[:limit]
        print(f"{name}: {len(tiles)} tiles, {sum(len(t.boxes) for t in tiles)} boxes", flush=True)
        return tiles

    if torch.cuda.is_available() and args.device != "cpu":
        device = torch.device(args.device or "cuda")
    else:
        device = torch.device("cpu")
    state = read_state(args.resume) if args.resume is not None else None
    if args.test and state is None:
        if args.weights is None:
            raise SystemExit(
                "--test measures a trained detector: pass --weights <checkpoint>, or --resume "
                "<checkpoint> to name the run's last one. Without either the numbers would come "
                "from the untrained checkpoint."
            )
        state = read_state(args.weights)
    model = build_model(config)
    if state is not None and (args.test or args.resume is not None):
        model.load_state_dict(state["model"])
    model = model.to(device)
    print(
        f"{config['model']['checkpoint']} on {device}, "
        f"{sum(p.numel() for p in model.parameters()) / 1e6:.1f}M parameters, "
        f"num_queries {config['model']['num_queries']}, {precision}"
        + (f", weights {args.weights or args.resume}" if args.test else ""),
        flush=True,
    )
    metrics_path = out / "metrics.json"
    report: dict[str, Any] = {"config": relative_to_root(args.config), "epochs": [], "test": None}
    runtime = runtime_of(
        args,
        config=config,
        epochs=epochs,
        batch_size=batch_size,
        precision=precision,
        workers=workers,
        seed=seed,
        splits=splits,
        cache=cache,
        materialised=materialised,
    )
    report["runtime"] = runtime
    if args.test:
        # What the measurement itself used: which checkpoint, on which split files, at which
        # operating point, chosen by which protocol. `--val-limit 2` still measures the whole test
        # split, so the counts are the only honest statement of what was measured.
        measured = args.weights if args.weights is not None else args.resume
        report["evaluation"] = {
            "checkpoint": relative_to_root(measured),
            "checkpoint_sha256": file_sha256(measured) if measured is not None else None,
            "device": str(device),
            "precision": precision,
            "batch_size": batch_size,
            "workers": workers,
            "score_selection": {
                "split": splits["val"],
                "limit": args.val_limit,
                "grid": [float(value) for value in evaluation["score_grid"]],
                "rule": "highest F1 on val, highest score on a tie",
            },
            "measurement": {
                "split": splits["test"],
                "iou": float(evaluation["iou"]),
                "nms": float(evaluation["nms"]),
                "max_per_tile": int(evaluation["max_per_tile"]),
                "unit": "whole page, tiles merged",
            },
        }

    if metrics_path.exists():
        # A resumed run keeps the epochs an earlier one wrote, so the curve in the file is the whole
        # curve rather than the part since the last restart. The runtime and the evaluation record are
        # this run's and are written after this merge, never before it: reading the file over the
        # report would otherwise leave the previous run's weights named as the current measurement.
        previous = json.loads(metrics_path.read_text(encoding="utf-8"))
        report["epochs"] = previous.get("epochs", [])
        if not args.test:
            report["test"] = previous.get("test")
        if "dense" in previous:
            report["dense"] = previous["dense"]

    if args.test:
        lowest = min(float(value) for value in evaluation["score_grid"])
        val_tiles = read("val", args.val_limit)
        val_loader = loader_for(val_tiles, augment=False, batch_size=batch_size, workers=workers, seed=seed)
        val_found = model_boxes(
            model,
            val_tiles,
            val_loader,
            precision=precision,
            score=lowest,
            max_per_tile=max_per_tile,
        )
        chosen, table = choose_score(val_tiles, val_found, config, nms=nms, max_per_tile=max_per_tile)
        report["score_selection"] = {"grid": table, "chosen": chosen}
        print(f"score chosen on val: {chosen}", flush=True)

        test_tiles = read("test")
        test_loader = loader_for(test_tiles, augment=False, batch_size=batch_size, workers=workers, seed=seed)
        found = model_boxes(
            model,
            test_tiles,
            test_loader,
            precision=precision,
            score=chosen,
            max_per_tile=max_per_tile,
        )
        overall, by_production, by_decile = page_metrics(
            test_tiles,
            found,
            score=chosen,
            nms=nms,
            max_per_tile=max_per_tile,
            iou_threshold=float(evaluation["iou"]),
        )
        report["test"] = {
            "score": chosen,
            "iou": float(evaluation["iou"]),
            "overall": overall.rates(),
            "by_production": {key: value.rates() for key, value in sorted(by_production.items())},
            "recall_by_size_decile": by_decile,
        }
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["test"], indent=2), flush=True)
        print(f"-> {metrics_path}", flush=True)
        return

    train_tiles = read("train", args.limit)
    val_tiles = read("val", args.val_limit)
    # The runtime object built before the report existed is the same one filled here: the loader's
    # real length is known now, and nothing recomputes it into a second dict that could disagree.
    accumulation = runtime["grad_accumulation"]
    runtime["steps_per_epoch"] = max(1, math.ceil(len(train_tiles) / batch_size / accumulation))
    runtime["scheduler_total_steps"] = runtime["steps_per_epoch"] * epochs
    print(f"runtime: epochs {epochs} (configured {training['epochs']}), "
          f"{runtime['steps_per_epoch']} scheduler steps an epoch, "
          f"{runtime['scheduler_total_steps']} in total, warmup {runtime['warmup_steps']}", flush=True)
    densest = max((len(tile.boxes) for tile in train_tiles), default=0)
    report["data"] = {
        "directory": relative_to_root(data),
        "tiles": {"train": len(train_tiles), "val": len(val_tiles)},
        "boxes": {
            "train": int(sum(len(tile.boxes) for tile in train_tiles)),
            "val": int(sum(len(tile.boxes) for tile in val_tiles)),
        },
        "densest_tile": {"train": densest},
    }
    if densest > int(config["model"]["num_queries"]):
        raise SystemExit(
            f"the densest tile holds {densest} boxes and num_queries is "
            f"{config['model']['num_queries']}: raise num_queries in {args.config}"
        )

    train_loader = loader_for(train_tiles, augment=True, batch_size=batch_size, workers=workers, seed=seed)
    val_loader = loader_for(val_tiles, augment=False, batch_size=batch_size, workers=workers, seed=seed)
    optimizer = optimizer_for(model, config)
    accumulation = int(training["grad_accumulation"])
    steps = max(1, math.ceil(len(train_loader) / accumulation)) * epochs
    scheduler = scheduler_for(optimizer, config, steps)
    # What the run actually used. `--epochs` on the command line sets the scheduler's horizon, and a
    # stored config kept the YAML's 24 through a six-epoch run, so a checkpoint's own config could not
    # say which schedule trained it. The report and every checkpoint carry this instead.
    runtime["scheduler_total_steps"] = steps
    runtime["steps_per_epoch"] = max(1, math.ceil(len(train_loader) / accumulation))
    report["runtime"] = runtime
    scaler = torch.amp.GradScaler("cuda", enabled=precision == "fp16")
    start = 0
    if state is not None and args.resume is not None:
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start = int(state["epoch"]) + 1
        print(f"resumed {args.resume} at epoch {start}", flush=True)

    best = -1.0
    for epoch in range(start, epochs):
        model.train()
        running, seen, started = 0.0, 0, time.time()
        optimizer.zero_grad(set_to_none=True)
        for step, batch in enumerate(train_loader, start=1):
            pixels = batch["pixel_values"].to(device, non_blocking=True)
            labels = [{key: value.to(device) for key, value in row.items()} for row in batch["labels"]]
            with autocast_of(precision):
                outputs = model(pixel_values=pixels, labels=labels)
                loss = outputs.loss / accumulation
            scaler.scale(loss).backward()
            running += float(loss.detach()) * accumulation
            seen += 1
            if step % accumulation == 0 or step == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), float(training["grad_clip"]))
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                scheduler.step()
            if step % 50 == 0 or step == len(train_loader) or args.profile:
                print(
                    f"epoch {epoch} step {step}/{len(train_loader)} loss {running / seen:.4f} "
                    f"{(time.time() - started) / seen:.2f}s/it {peak_vram()}",
                    flush=True,
                )
            if args.profile and step >= args.profile_steps:
                break
        if args.profile:
            done = min(args.profile_steps, len(train_loader))
            print(
                f"profiled {done} steps at batch {batch_size}, precision {precision}, "
                f"{(time.time() - started) / max(done, 1):.2f}s/it, {peak_vram()}",
                flush=True,
            )
            return

        floor = min(float(value) for value in evaluation["score_grid"])
        detections = model_boxes(
            model,
            val_tiles,
            val_loader,
            precision=precision,
            score=floor,
            max_per_tile=max_per_tile,
        )
        curve = tile_curve(val_tiles, detections, [float(v) for v in evaluation["score_grid"]])
        chosen = max(curve, key=lambda row: (row["f1"], row["score"]))
        joint = recall_at(curve, float(evaluation["precision_target"]))
        rates = {
            **chosen,
            "loss": running / seen,
            "seconds": time.time() - started,
            "recall_at_precision": joint,
            "curve": curve,
        }
        report["epochs"] = [row for row in report["epochs"] if row.get("epoch") != epoch]
        report["epochs"].append({"epoch": epoch, **rates})
        report["epochs"].sort(key=lambda row: row["epoch"])
        print(
            f"epoch {epoch}: loss {rates['loss']:.4f} | best F1 {rates['f1']:.4f} at score "
            f"{rates['score']:.3f}: precision {rates['precision']:.4f} recall {rates['recall']:.4f} "
            f"mean IoU {rates['mean_iou']:.4f} | recall {joint['recall']:.4f} at precision "
            f"{joint['precision']:.4f}",
            flush=True,
        )
        state_now = snapshot(model, optimizer, scheduler, config, epoch, rates, runtime)
        torch.save(state_now, out / f"epoch-{epoch:02d}.pt")
        torch.save(state_now, out / "last.pt")
        if rates["f1"] > best:
            best = rates["f1"]
            torch.save(state_now, out / "best.pt")
            print(
                f"epoch {epoch}: best F1 {best:.4f} at score {rates['score']:.2f} -> {out / 'best.pt'}",
                flush=True,
            )
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"-> {metrics_path}", flush=True)


if __name__ == "__main__":
    main()
