"""Train the class-agnostic character detector and measure it.

The data is the COCO split T20 writes under `work/detector/`: one image entry per 1024x1024 tile,
with the cached page it is cut from and the origin of the tile on that page, and one annotation per
character box clipped to the tile, carrying `source_unit_id`. A tile is cut from the cached page at
the recorded origin when the run reaches it and padded with white at the right and bottom, so the
split stays small and a page is read once per epoch. Annotations marked `iscrowd` (the `unreadable`
units) are not trained on, and a detection that lands in one is neither a hit nor a false positive.

The model is RT-DETR with a ResNet-18 backbone through `transformers`, class-agnostic
(`num_labels: 1`), with `num_queries` above the densest tile's box count, which the run checks
before it starts. Training is mixed precision and writes one checkpoint an epoch plus the best one
by validation F1. The metrics the card asks for go to `artifacts/metrics.json`: precision, recall,
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

from kuzushiji_atlas import detect
from kuzushiji_atlas.schema import Box

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
    """`bid` to production type, from the split table T20 commits."""
    if not path.exists():
        return {}
    rows: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        if "bid" not in header or "production" not in header:
            return {}
        bid, production = header.index("bid"), header.index("production")
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) > max(bid, production):
                rows[fields[bid]] = fields[production]
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
    page and the origin are kept and the tile is cut when the run reaches it.
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
    which restores the box T20 started from. A split without `source_unit_id` falls back to the
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
"""Matched at IoU 0.5, the threshold the card measures at."""


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
    model: Any, optimizer: Any, scheduler: Any, config: dict[str, Any], epoch: int, metrics: dict
) -> dict[str, Any]:
    return {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "epoch": epoch,
        "config": config,
        "metrics": metrics,
    }


def peak_vram() -> str:
    if not torch.cuda.is_available():
        return "no cuda"
    return f"peak {torch.cuda.max_memory_allocated() / 2**30:.2f}GiB"


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
                f"{path} is missing: T20 writes the detector data with scripts/build_detector_data.py"
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
    model = build_model(config).to(device)
    print(
        f"{config['model']['checkpoint']} on {device}, "
        f"{sum(p.numel() for p in model.parameters()) / 1e6:.1f}M parameters, "
        f"num_queries {config['model']['num_queries']}, {precision}",
        flush=True,
    )
    metrics_path = out / "metrics.json"
    report: dict[str, Any] = {"config": str(args.config), "epochs": [], "test": None}

    if args.test:
        if metrics_path.exists():
            report = json.loads(metrics_path.read_text(encoding="utf-8"))
            report.setdefault("epochs", [])
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
    densest = max((len(tile.boxes) for tile in train_tiles), default=0)
    report["data"] = {
        "directory": str(data),
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
    scaler = torch.amp.GradScaler("cuda", enabled=precision == "fp16")
    start = 0
    if args.resume is not None:
        state = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(state["model"])
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
            if step % 50 == 0 or step == len(train_loader):
                print(
                    f"epoch {epoch} step {step}/{len(train_loader)} loss {running / seen:.4f} "
                    f"{(time.time() - started) / seen:.2f}s/it {peak_vram()}",
                    flush=True,
                )
            if args.profile and step >= args.profile_steps:
                break
        if args.profile:
            print(
                f"profiled {min(args.profile_steps, len(train_loader))} steps at batch "
                f"{batch_size}, precision {precision}, {peak_vram()}",
                flush=True,
            )
            return

        detections = model_boxes(
            model,
            val_tiles,
            val_loader,
            precision=precision,
            score=float(evaluation["score"]),
            max_per_tile=max_per_tile,
        )
        rates = tile_metrics(val_tiles, detections).rates()
        rates["loss"] = running / seen
        rates["seconds"] = time.time() - started
        report["epochs"].append({"epoch": epoch, **rates})
        print(
            f"epoch {epoch}: loss {rates['loss']:.4f} precision {rates['precision']:.4f} "
            f"recall {rates['recall']:.4f} f1 {rates['f1']:.4f} mean IoU {rates['mean_iou']:.4f}",
            flush=True,
        )
        torch.save(snapshot(model, optimizer, scheduler, config, epoch, rates), out / "last.pt")
        if rates["f1"] > best:
            best = rates["f1"]
            torch.save(snapshot(model, optimizer, scheduler, config, epoch, rates), out / "best.pt")
            print(f"epoch {epoch}: best F1 {best:.4f} -> {out / 'best.pt'}", flush=True)
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"-> {metrics_path}", flush=True)


if __name__ == "__main__":
    main()
