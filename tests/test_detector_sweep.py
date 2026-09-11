"""Tests for `models/detector/sweep_nms.py`'s cache handling, on the CPU and without inference.

The sweep reads raw detections from a cache and pairs them, tile by tile, with the ground truth of a
split. Three ways that can go wrong without looking wrong, each of which these tests pin:

* a tile's `key` is its page and kind, so two tiles of one page share it. A cache whose tiles were
  reordered within a page passes a key-only check and silently pairs every detection with the other
  tile's truth, which is why origin and size are compared too;
* `boxes` and `scores` are object arrays of one entry a tile, so a cache written for a split of a
  different length reads as a shorter one unless the shapes are checked;
* a cache states what wrote it — the checkpoint's hash, the operating point, the split files' hashes.
  Sweeping a cache whose checkpoint or split has changed since is a comparison that means nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("torch")
_spec = importlib.util.spec_from_file_location("sweep_nms", ROOT / "models" / "detector" / "sweep_nms.py")
assert _spec is not None and _spec.loader is not None
sweep_nms = importlib.util.module_from_spec(_spec)
sys.modules["sweep_nms"] = sweep_nms
_spec.loader.exec_module(sweep_nms)


def stub_config() -> dict:
    """The keys `tiles_for` reads off the configuration, and nothing else."""
    return {"data": {"directory": "work/detector", "image_cache": "cache/images",
                     "split_table": "data/splits/codh.tsv", "splits": {"val": "val.json",
                                                                        "test": "test.json"}}}


class Tile:
    """The parts of `train.Tile` the cache checks read."""

    def __init__(self, key: str, origin: tuple[int, int], size: int = 1024) -> None:
        self.key = key
        self.origin = origin
        self.size = size


def write_cache(directory: Path, name: str, tiles: list[Tile], *, shift: int = 0) -> None:
    """A cache in the shape `dump` writes, optionally with the tiles rotated by `shift`."""
    rotated = tiles[shift:] + tiles[:shift]
    tile_boxes = [np.zeros((1, 4), dtype=np.float32) for _ in tiles]
    tile_scores = [np.zeros((1,), dtype=np.float32) for _ in tiles]
    np.savez_compressed(
        directory / f"{name}.npz",
        keys=np.asarray([tile.key for tile in rotated]),
        origins=np.asarray([tile.origin for tile in rotated], dtype=np.int32),
        sizes=np.asarray([tile.size for tile in rotated], dtype=np.int32),
        boxes=np.asarray(tile_boxes, dtype=object),
        scores=np.asarray(tile_scores, dtype=object),
    )


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    directory = tmp_path / "cache"
    directory.mkdir()
    return directory


def test_a_reordered_tile_inside_a_page_is_refused(cache: Path, monkeypatch) -> None:
    """Two tiles of one page share a key, so the check has to compare origin and size as well.

    The tiles below are the same two pages twice, with the second page's tiles swapped. A key-only
    check sees the same list of keys and passes; every detection of those two tiles would then be
    scored against the other tile's truth.
    """
    tiles = [Tile("page:A", (0, 0)), Tile("page:A", (896, 0)),
             Tile("page:B", (0, 0)), Tile("page:B", (896, 0))]
    reordered = [tiles[0], tiles[1], tiles[3], tiles[2]]
    write_cache(cache, "val", reordered)
    monkeypatch.setattr(sweep_nms.train, "read_split", lambda *a, **k: tiles)
    monkeypatch.setattr(sweep_nms.train, "read_splits", lambda *a, **k: {})
    loaded = sweep_nms.load("val", cache)
    with pytest.raises(SystemExit, match="differs"):
        sweep_nms.tiles_for("val", stub_config(), loaded)


def test_a_cache_for_another_split_length_is_refused(cache: Path, monkeypatch) -> None:
    """A cache written for a longer split cannot be read as a shorter one."""
    tiles = [Tile("page:A", (0, 0)), Tile("page:A", (896, 0))]
    write_cache(cache, "val", tiles + [Tile("page:B", (0, 0))])
    loaded = sweep_nms.load("val", cache)
    monkeypatch.setattr(sweep_nms.train, "read_split", lambda *a, **k: tiles)
    monkeypatch.setattr(sweep_nms.train, "read_splits", lambda *a, **k: {})
    with pytest.raises(SystemExit, match="the cache holds 3 tiles, the split has 2"):
        sweep_nms.tiles_for("val", stub_config(), loaded)


def test_arrays_of_different_lengths_are_refused(cache: Path) -> None:
    """`boxes` and `scores` are one entry a tile, and a mismatch is caught at load."""
    np.savez_compressed(
        cache / "val.npz",
        keys=np.asarray(["a", "b"]),
        origins=np.asarray([[0, 0], [1, 1]], dtype=np.int32),
        sizes=np.asarray([1024, 1024], dtype=np.int32),
        boxes=np.asarray([np.zeros((1, 4), dtype=np.float32)], dtype=object),
        scores=np.asarray([np.zeros((1,), dtype=np.float32)] * 2, dtype=object),
    )
    with pytest.raises(SystemExit, match="disagree on length"):
        sweep_nms.load("val", cache)


def test_a_tile_whose_boxes_and_scores_disagree_is_refused(cache: Path) -> None:
    """A tile with two boxes and one score cannot be scored, so it is refused rather than padded."""
    np.savez_compressed(
        cache / "val.npz",
        keys=np.asarray(["a"]),
        origins=np.asarray([[0, 0]], dtype=np.int32),
        sizes=np.asarray([1024], dtype=np.int32),
        boxes=np.asarray([np.zeros((2, 4), dtype=np.float32)], dtype=object),
        scores=np.asarray([np.zeros((1,), dtype=np.float32)], dtype=object),
    )
    with pytest.raises(SystemExit, match="2 boxes and 1 scores"):
        sweep_nms.load("val", cache)


def test_a_cache_without_provenance_is_refused(cache: Path) -> None:
    """A cache that does not say which checkpoint wrote it cannot be attributed to one."""
    with pytest.raises(SystemExit, match="cannot be attributed"):
        sweep_nms.provenance_of(cache)


def test_a_sweep_refuses_a_cache_from_another_checkpoint(cache: Path, tmp_path: Path) -> None:
    """The recorded checkpoint hash is compared against the file's, not taken on trust."""
    measured = ROOT / "models" / "detector" / "artifacts" / "best.pt"
    other = tmp_path / "other.bin"
    other.write_bytes(b"not the checkpoint")
    (cache / "provenance.json").write_text(json.dumps({
        "checkpoint": "models/detector/artifacts/best.pt",
        "checkpoint_sha256": sweep_nms.train.file_sha256(other),
        "splits": {},
    }) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="run --dump again"):
        sweep_nms.verify(measured, ["val"], cache)


def test_a_sweep_refuses_a_split_the_cache_does_not_hold(cache: Path) -> None:
    """A cache holding only `val` cannot answer a sweep that asks for `test` too."""
    (cache / "provenance.json").write_text(json.dumps({
        "checkpoint": "models/detector/artifacts/best.pt",
        "checkpoint_sha256": sweep_nms.train.file_sha256(
            ROOT / "models" / "detector" / "artifacts" / "best.pt"),
        "splits": {"val": {"sha256": "0" * 64, "tiles": 1}},
    }) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="holds no test split"):
        sweep_nms.verify(None, ["test"], cache)


def test_a_sweep_refuses_a_split_file_that_changed(cache: Path) -> None:
    """The split file's hash at dump time is compared with its hash now."""
    measured = ROOT / "models" / "detector" / "artifacts" / "best.pt"
    (cache / "provenance.json").write_text(json.dumps({
        "checkpoint": "models/detector/artifacts/best.pt",
        "checkpoint_sha256": sweep_nms.train.file_sha256(measured),
        "splits": {"val": {"sha256": "0" * 64, "tiles": 1}},
    }) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since the dump"):
        sweep_nms.verify(None, ["val"], cache)


def test_the_grid_extends_below_the_first_sweeps_choice() -> None:
    """The first sweep chose 0.2, its lowest tested value, so the grid now goes under it."""
    assert min(sweep_nms.NMS_GRID) < 0.2
    assert 0.2 in sweep_nms.NMS_GRID and 0.5 in sweep_nms.NMS_GRID
    assert list(sweep_nms.NMS_GRID) == sorted(sweep_nms.NMS_GRID)
