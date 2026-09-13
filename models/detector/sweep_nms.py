"""The card's second remedy for precision, measured: a val-chosen sweep of `nms`.

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
score grid, and the page metrics come from `train.page_metrics` — the same function the card's numbers
come from.
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
NMS_GRID = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)

_spec = importlib.util.spec_from_file_location("detector_train", ROOT / "models" / "detector" / "train.py")
assert _spec is not None and _spec.loader is not None
train = importlib.util.module_from_spec(_spec)
sys.modules["detector_train"] = train
_spec.loader.exec_module(train)


def dump(checkpoint: Path, splits: list[str]) -> None:
    """One forward pass a split, with the raw detections written beside the checkpoints."""
    config = train.load_config(ROOT / "models" / "detector" / "config.yaml")
    training, evaluation = config["training"], config["evaluation"]
    data = ROOT / config["data"]["directory"]
    CACHE.mkdir(parents=True, exist_ok=True)
    model = train.build_model(config).to("cuda")
    model.load_state_dict(train.read_state(checkpoint)["model"])
    floor = min(float(value) for value in evaluation["score_grid"])
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
        # one page, so a cache keyed by it cannot be reassembled into the split.
        np.savez_compressed(
            target,
            keys=np.asarray([tile.key for tile in tiles]),
            origins=np.asarray([tile.origin for tile in tiles], dtype=np.int32),
            sizes=np.asarray([tile.size for tile in tiles], dtype=np.int32),
            boxes=np.asarray([row[0] for row in found], dtype=object),
            scores=np.asarray([row[1] for row in found], dtype=object),
        )
        print(f"{name}: {len(tiles)} tiles -> {target}", flush=True)
        del tiles, loader, found


def load(name: str) -> dict[str, Any]:
    """The cached detections of one split, as the arrays `page_metrics` wants."""
    path = CACHE / f"{name}.npz"
    if not path.exists():
        raise SystemExit(f"{path} is missing; run --dump first")
    with np.load(path, allow_pickle=True) as data:
        return {
            "keys": [str(key) for key in data["keys"]],
            "origins": data["origins"],
            "sizes": data["sizes"],
            "boxes": list(data["boxes"]),
            "scores": list(data["scores"]),
        }


def tiles_for(name: str, config: dict[str, Any], cache: dict[str, Any]) -> list[Any]:
    """The real `Tile` objects of a split, checked against the cache's own record of them.

    Both come from the same reading of the same split file, so the check is that the cache still
    describes exactly this split — same length, same keys in the same order — rather than a
    reconstruction that could pair a detection with another tile's truth.
    """
    data = ROOT / config["data"]["directory"]
    tiles = train.read_split(data / config["data"]["splits"][name],
                             cache=ROOT / config["data"]["image_cache"],
                             splits=train.read_splits(ROOT / config["data"]["split_table"]),
                             materialised=data / "tiles")
    cached = cache["keys"]
    if len(cached) != len(tiles):
        raise SystemExit(f"{name}: the cache holds {len(cached)} tiles, the split has {len(tiles)}")
    for index, (key, tile) in enumerate(zip(cached, tiles, strict=True)):
        if key != tile.key:
            raise SystemExit(f"{name}: tile {index} is {tile.key} in the split and {key} in the cache")
    return tiles


def sweep(nms_grid: tuple[float, ...], score: float | None) -> dict[str, Any]:
    """Whole-page precision, recall and F1 over `nms`, and the split of the false positives."""
    config = train.load_config(ROOT / "models" / "detector" / "config.yaml")
    evaluation = config["evaluation"]
    out: dict[str, Any] = {"grid": list(nms_grid), "splits": {}}
    for name in ("val", "test"):
        cache = load(name)
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
