"""Shape order for Quick review: crops of one character shown with similar shapes side by side.

A round deals up to 96 crops of one character. Shown in shape order, the crops of each form sit
together and a crop that is another character stands out among them. The order is computed ahead
of time: every crop the collection can show is embedded with the character classifier's
penultimate features, the crops of each character are clustered, the clusters are chained so that
similar ones are adjacent (`form_clusters.shape_order`, cut into runs as in the Forms view), and
within a cluster the most typical crop comes first. Each crop gets one integer, `shape_order`,
comparable only with crops of the same character.

The order changes only how a round is displayed. Which crops a round deals is decided as before.

One run writes `<out>/<revision>/shapes.json` and points `<out>/current` at it.
"""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

METHOD = "classifier-penultimate/quiz-shape-order-v1"


def shapes_dir() -> Path:
    root = Path(__file__).resolve().parents[3]
    return Path(os.environ.get("ATLAS_QUIZ_SHAPES", root / "work/quiz-shapes/current"))


def load(directory: Path | None = None) -> dict[str, int]:
    """Each crop's `shape_order` by unit id, or nothing when no order has been computed."""
    path = (directory or shapes_dir()) / "shapes.json"
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _load(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


_CACHE: dict[tuple, dict[str, int]] = {}


def _load(path: str, stamp: int, size: int) -> dict[str, int]:
    key = (path, stamp, size)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = json.loads(Path(path).read_text())["orders"]
    return _CACHE[key]


def _crops(dataset: Path) -> tuple[dict[str, list[str]], dict[str, bytes]]:
    """Every crop that can be shown, grouped by the character it is dealt under.

    The collection's whole browse list is ordered, not only what this server deals now: a hosted
    round deals from the set fixed at its last export, which differs from the local review queue.
    """
    from fastapi.testclient import TestClient

    from .server import create_app

    app = create_app(dataset)
    client = TestClient(app)
    media = app.state.media
    summary = client.get("/atlas", params={"purpose": "browse", "production": "all", "limit": 1}).json()
    groups: dict[str, list[str]] = defaultdict(list)
    images: dict[str, bytes] = {}
    for category in summary["categories"]:
        offset = 0
        while True:
            page = client.get("/atlas", params={"purpose": "browse", "production": "all", "reading": category["label"],
                                                "limit": 96, "offset": offset}).json()
            for item in page["items"]:
                found = re.fullmatch(r"/atlas/media/([0-9a-f]{64})\.webp", item["image"] or "")
                if found:
                    data = media.materialize(found[1]).read_bytes()
                else:
                    response = client.get(item["image"])
                    if response.status_code != 200:
                        continue
                    data = response.content
                groups[category["label"]].append(item["id"])
                images[item["id"]] = data
            offset += len(page["items"])
            if not page["items"] or offset >= page["total"]:
                break
    return groups, images


def order_group(vectors: np.ndarray) -> list[int]:
    """Positions of one character's crops in shape order."""
    import torch

    from ..form_clusters import _kmeans, cluster_count, shape_order
    from .forms import shape_runs

    n = len(vectors)
    x = torch.nn.functional.normalize(torch.as_tensor(vectors, dtype=torch.float32), dim=1)
    k = min(cluster_count(n), n)
    if k < 2:
        return torch.argsort(-(x @ torch.nn.functional.normalize(x.mean(0), dim=0))).tolist()
    labels, centres = _kmeans(x, k, seed=11)
    similarity = (x * centres[labels]).sum(1)
    present = [c for c in range(k) if (labels == c).any()]
    chained = [present[i] for i in shape_order(centres[present].numpy())]
    adjacent = [float(centres[a] @ centres[b]) for a, b in itertools.pairwise(chained)]
    counts = {str(c): int((labels == c).sum()) for c in present}
    runs = shape_runs({"order": [str(c) for c in chained], "adjacent": adjacent},
                      [{"id": c, "count": count} for c, count in counts.items()])
    positions: list[int] = []
    for c in runs:
        members = torch.where(labels == int(c))[0]
        positions.extend(members[torch.argsort(-similarity[members])].tolist())
    return positions


def compute(dataset: Path, out: Path, *, checkpoint: Path, classes: Path) -> dict[str, Any]:
    """Order every Quick review crop of `dataset` by shape, and publish the order."""
    from PIL import Image

    from ..classify import preprocess
    from ..form_clusters import Encoder

    groups, images = _crops(dataset)
    encoder = Encoder(checkpoint, classes)
    ids = [identity for members in groups.values() for identity in members]
    vectors = []
    for start in range(0, len(ids), 512):
        batch = []
        for identity in ids[start:start + 512]:
            with Image.open(io.BytesIO(images[identity])) as picture:
                batch.append(np.asarray(preprocess(picture.convert("L")), dtype=np.uint8))
        vectors.append(encoder(np.stack(batch)))
    embedded = dict(zip(ids, np.concatenate(vectors), strict=True))
    orders: dict[str, int] = {}
    for label, members in sorted(groups.items()):
        for position, index in enumerate(order_group(np.stack([embedded[m] for m in members]))):
            orders[members[index]] = position
    digest = hashlib.sha256(json.dumps({"method": METHOD, "orders": orders}, sort_keys=True).encode()).hexdigest()[:16]
    target = out / digest
    staging = out / f".{digest}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    (staging / "shapes.json").write_text(json.dumps({"revision": digest, "method": METHOD, "dataset": dataset.name,
                                                     "orders": orders}, ensure_ascii=False) + "\n")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    link = out / ".current.next"
    link.unlink(missing_ok=True)
    link.symlink_to(digest)
    os.replace(link, out / "current")
    return {"revision": digest, "characters": len(groups), "crops": len(orders)}
