"""The second remedy for precision, measured: a val-chosen sweep of `nms`.

The shipped detector's test precision is 0.6998 at recall 0.9283, and a quarter of its false positives
overlap an annotated box below the match threshold — duplicates and splits of a real character. Those
are what suppression is for, and `nms` is a number the shipped configuration picked by hand (0.5)
rather than from `val`. This script measures the whole-page curve over a grid of `nms` on `val`,
chooses a value there, and reports what that value does on `test`.

Two steps, so a sweep costs one inference pass rather than one per value:

    # one forward pass over each split, cached beside the checkpoints
    .venv/bin/python models/detector/sweep_nms.py --dump
    # read the cache and print the curves
    .venv/bin/python models/detector/sweep_nms.py --sweep

`--dump` needs the GPU and the detector data; `--sweep` is CPU-only and seconds long. The cache holds
raw decoded detections, so both steps see exactly what `train.py --test` sees at the floor of the
score grid, and the page metrics come from `train.page_metrics` — the same function the acceptance
numbers come from.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "models" / "detector" / "nms-cache"
#: The grid, denser at the low end: the first sweep chose 0.2, which was the lowest value it tested,
#: so the low end is where the curve has to be extended before the choice can be called a maximum.
NMS_GRID = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)

_spec = importlib.util.spec_from_file_location("detector_train", ROOT / "models" / "detector" / "train.py")
assert _spec is not None and _spec.loader is not None
train = importlib.util.module_from_spec(_spec)
sys.modules["detector_train"] = train
_spec.loader.exec_module(train)


def dump(checkpoint: Path, splits: list[str]) -> None:
    """One forward pass a split, with the raw detections and what produced them written beside them.

    Everything a later sweep needs to know that the cache belongs to *this* measurement goes into
    `provenance.json` beside the arrays: the checkpoint's own hash, the operating point the detections
    were collected at, the suppression the metrics will apply, the tile geometry, and the identity of
    each split file. Without it, dumping with one checkpoint and sweeping with another — or mixing a
    val dump from one run with a test dump from another — produces a comparison that looks fine and
    means nothing.
    """
    config = train.load_config(ROOT / "models" / "detector" / "config.yaml")
    training, evaluation = config["training"], config["evaluation"]
    data = ROOT / config["data"]["directory"]
    CACHE.mkdir(parents=True, exist_ok=True)
    model = train.build_model(config).to("cuda")
    model.load_state_dict(train.read_state(checkpoint)["model"])
    floor = min(float(value) for value in evaluation["score_grid"])
    provenance = {
        "checkpoint": train.relative_to_root(checkpoint),
        "checkpoint_sha256": train.file_sha256(checkpoint),
        "model": str(config["model"]["checkpoint"]),
        "model_revision": str(config["model"].get("revision")),
        "collection": {
            "score": floor,
            "max_per_tile": int(evaluation["max_per_tile"]),
            "precision": "bf16",
            "batch_size": int(training["batch_size"]),
            "tile": train.detect.TILE,
            "overlap": train.detect.OVERLAP,
        },
        "measurement": {
            "iou": float(evaluation["iou"]),
            "nms_grid": [float(value) for value in NMS_GRID],
            "unit": "whole page, tiles merged",
        },
        "splits": {},
    }
    for name in splits:
        tiles = train.read_split(data / config["data"]["splits"][name],
                                 cache=ROOT / config["data"]["image_cache"],
                                 splits=train.read_splits(ROOT / config["data"]["split_table"]),
                                 materialised=data / "tiles")
        loader = train.loader_for(tiles, augment=False, batch_size=int(training["batch_size"]),
                                  workers=int(training["num_workers"]), seed=0)
        found = train.model_boxes(model, tiles, loader, precision="bf16", score=floor,
                                  max_per_tile=int(evaluation["max_per_tile"]))
        target = CACHE / f"{name}.npz"
        # Written in the split's own order and read back positionally: `key` repeats across tiles of
        # one page, so a cache keyed by it cannot be reassembled into the split. The origin and size
        # travel with it because a reordered tile of the same page shares its key.
        np.savez_compressed(
            target,
            keys=np.asarray([tile.key for tile in tiles]),
            origins=np.asarray([tile.origin for tile in tiles], dtype=np.int32),
            sizes=np.asarray([tile.size for tile in tiles], dtype=np.int32),
            boxes=np.asarray([row[0] for row in found], dtype=object),
            scores=np.asarray([row[1] for row in found], dtype=object),
        )
        split_path = data / config["data"]["splits"][name]
        provenance["splits"][name] = {
            "file": train.relative_to_root(split_path),
            "sha256": train.file_sha256(split_path),
            "tiles": len(tiles),
            "detections": int(sum(len(row[1]) for row in found)),
        }
        print(f"{name}: {len(tiles)} tiles -> {target}", flush=True)
        del tiles, loader, found
    (CACHE / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"provenance -> {CACHE / 'provenance.json'}", flush=True)


def provenance_of(cache_dir: Path = CACHE) -> dict[str, Any]:
    """What wrote the cache, or a refusal when it states nothing."""
    path = cache_dir / "provenance.json"
    if not path.exists():
        raise SystemExit(
            f"{path} is missing: a cache without provenance cannot be attributed to a checkpoint, so "
            f"run --dump again rather than sweeping it"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load(name: str, cache_dir: Path = CACHE) -> dict[str, Any]:
    """The cached detections of one split, as the arrays `page_metrics` wants.

    The arrays are checked for shape and internal consistency here: `boxes` and `scores` are object
    arrays of one entry a tile, so a cache written for a different split length cannot be read as this
    one, and a sweep that would silently mis-pair detections with truth fails instead.
    """
    path = cache_dir / f"{name}.npz"
    if not path.exists():
        raise SystemExit(f"{path} is missing; run --dump first")
    with np.load(path, allow_pickle=True) as data:
        keys = [str(key) for key in data["keys"]]
        origins = np.asarray(data["origins"])
        sizes = np.asarray(data["sizes"])
        boxes = list(data["boxes"])
        scores = list(data["scores"])
    shapes = {
        "keys": len(keys),
        "origins": len(origins),
        "sizes": len(sizes),
        "boxes": len(boxes),
        "scores": len(scores),
    }
    if len(set(shapes.values())) != 1:
        raise SystemExit(f"{path}: the arrays disagree on length: {shapes}")
    if origins.ndim != 2 or origins.shape[1] != 2:
        raise SystemExit(f"{path}: origins should be (tiles, 2), got {origins.shape}")
    if sizes.ndim != 1:
        raise SystemExit(f"{path}: sizes should be (tiles,), got {sizes.shape}")
    for index, (box, score) in enumerate(zip(boxes, scores, strict=True)):
        if len(box) != len(score):
            raise SystemExit(
                f"{path}: tile {index} has {len(box)} boxes and {len(score)} scores"
            )
    return {"keys": keys, "origins": origins, "sizes": sizes, "boxes": boxes, "scores": scores}


def tiles_for(name: str, config: dict[str, Any], cache: dict[str, Any]) -> list[Any]:
    """The real `Tile` objects of a split, checked tile by tile against the cache's own record.

    The check is the identity of every tile, not just the page it belongs to: a tile's `key` is its
    page and kind, so two tiles of one page share it, and a split reordered within a page would pass a
    key-only check while every detection of those two tiles was paired with the other's truth. Origin
    and size are what distinguish them, so all three are compared, in order.
    """
    data = ROOT / config["data"]["directory"]
    tiles = train.read_split(data / config["data"]["splits"][name],
                             cache=ROOT / config["data"]["image_cache"],
                             splits=train.read_splits(ROOT / config["data"]["split_table"]),
                             materialised=data / "tiles")
    if len(cache["keys"]) != len(tiles):
        raise SystemExit(
            f"{name}: the cache holds {len(cache['keys'])} tiles, the split has {len(tiles)}"
        )
    for index, tile in enumerate(tiles):
        key, origin, size = cache["keys"][index], cache["origins"][index], cache["sizes"][index]
        if key != tile.key or tuple(int(value) for value in origin) != tuple(tile.origin) \
                or int(size) != int(tile.size):
            raise SystemExit(
                f"{name}: tile {index} differs — the split has {tile.key} at {tuple(tile.origin)} "
                f"size {tile.size}, the cache {key} at {tuple(int(v) for v in origin)} size {int(size)}"
            )
    return tiles


def verify(checkpoint: Path | None, splits: list[str], cache_dir: Path = CACHE) -> dict[str, Any]:
    """Refuse to sweep a cache that does not describe this checkpoint and these split files.

    Three ways a sweep can be wrong without looking wrong: the cache was written by another
    checkpoint, one split's cache came from another dump, or the split file itself changed since the
    dump. Each is checked here, against the file's own hash rather than its timestamp, so the sweep
    either reports a comparison it can justify or stops.
    """
    provenance = provenance_of(cache_dir)
    recorded = provenance.get("checkpoint_sha256")
    if checkpoint is not None:
        current = train.file_sha256(checkpoint)
        if current != recorded:
            raise SystemExit(
                f"the cache was written by {provenance.get('checkpoint')} "
                f"({str(recorded)[:12]}), not by {train.relative_to_root(checkpoint)} "
                f"({current[:12]}); run --dump again"
            )
    for name in splits:
        entry = (provenance.get("splits") or {}).get(name)
        if entry is None:
            raise SystemExit(f"the cache holds no {name} split; run --dump for it")
        config = train.load_config(ROOT / "models" / "detector" / "config.yaml")
        path = ROOT / config["data"]["directory"] / config["data"]["splits"][name]
        if train.file_sha256(path) != entry["sha256"]:
            raise SystemExit(
                f"the {name} split file changed since the dump ({entry['sha256'][:12]} -> "
                f"{train.file_sha256(path)[:12]}); run --dump again"
            )
    return provenance


def sweep(nms_grid: tuple[float, ...], score: float | None,
          cache_dir: Path = CACHE) -> dict[str, Any]:
    """Whole-page precision, recall and F1 over `nms`, and the split of the false positives."""
    config = train.load_config(ROOT / "models" / "detector" / "config.yaml")
    evaluation = config["evaluation"]
    out: dict[str, Any] = {"grid": list(nms_grid), "splits": {}}
    for name in ("val", "test"):
        cache = load(name, cache_dir)
        tiles = tiles_for(name, config, cache)
        found = list(zip(cache["boxes"], cache["scores"], strict=True))
        at = float(evaluation["score"]) if score is None else float(score)
        rows = []
        for value in nms_grid:
            overall, by_production, _ = train.page_metrics(
                tiles, found, score=at, nms=value,
                max_per_tile=int(evaluation["max_per_tile"]),
                iou_threshold=float(evaluation["iou"]))
            rates = overall.rates()
            rows.append({
                "nms": value,
                "precision": rates["precision"], "recall": rates["recall"], "f1": rates["f1"],
                "mean_iou": rates["mean_iou"],
                "tp": rates["tp"], "fp": rates["fp"], "fn": rates["fn"],
                "by_production": {name: rates for name, rates in
                                  ((key, value.rates()) for key, value in by_production.items())},
            })
            print(f"{name} nms {value}: precision {rates['precision']:.4f} recall {rates['recall']:.4f} "
                  f"f1 {rates['f1']:.4f} fp {rates['fp']}", flush=True)
        out["splits"][name] = rows
    chosen = max(out["splits"]["val"], key=lambda row: (row["f1"], row["nms"]))
    out["chosen_on_val"] = chosen
    at = [row for row in out["splits"]["test"] if row["nms"] == chosen["nms"]]
    out["test_at_chosen"] = at[0] if at else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", action="store_true", help="one pass a split, into the cache")
    parser.add_argument("--sweep", action="store_true", help="read the cache and print the curves")
    parser.add_argument("--checkpoint", type=Path,
                        default=ROOT / "models" / "detector" / "artifacts" / "best.pt")
    parser.add_argument("--split", action="append", default=[], help="only this split (val, test)")
    parser.add_argument("--score", type=float, default=None, help="override the operating point")
    parser.add_argument("--out", type=Path, default=ROOT / "models" / "detector" / "nms-sweep.json")
    args = parser.parse_args()
    if not args.dump and not args.sweep:
        parser.error("pass --dump, --sweep, or both")

    if args.dump:
        dump(args.checkpoint, args.split or ["val", "test"])
    if args.sweep:
        verify(args.checkpoint, args.split or ["val", "test"])
        result = sweep(NMS_GRID, args.score)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        chosen, at = result["chosen_on_val"], result["test_at_chosen"]
        print()
        print(f"chosen on val: nms {chosen['nms']} (val f1 {chosen['f1']:.4f})")
        if at:
            print(f"test at that nms: precision {at['precision']:.4f} recall {at['recall']:.4f} "
                  f"f1 {at['f1']:.4f} fp {at['fp']}")
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
