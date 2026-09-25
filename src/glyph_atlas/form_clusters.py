"""Cluster CODH glyphs by shape within each character family, for form assignment.

CODH transcribes every form of a family under one code point: a hentaigana は and the 波-derived
は are both U+306F, and 假 is transcribed 仮. Within each family that has more than one member,
the glyphs are embedded with the character classifier's penultimate features and grouped by
spherical k-means. A cluster is a proposal of shape, never an identity; a person names its form.

One run writes `<out>/<revision>/`:

- `units.parquet`: one row per clustered glyph, with its family, cluster, similarity to the
  cluster centre and rank by that similarity (0 is the most central).
- `clusters.json`: per family, its members and its clusters, largest first, each with the ids of
  its most central glyphs.
- `embeddings.npy`: the float16 unit vectors, row-aligned with `units.parquet`.
- `manifest.json`: the inputs the revision is derived from.
- `neighbours.json`: per family, its clusters ordered so that similar shapes are adjacent, the
  similarity of each adjacent pair, and each cluster's most similar other cluster.

`<out>/current` then points at the revision.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import shutil
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import refs

#: Glyphs whose family has fewer members than this are left to per-glyph review.
MIN_FAMILY_UNITS = 12
#: Most clusters a family is split into, however large it is.
MAX_CLUSTERS = 48
#: Most central glyphs listed per cluster.
REPRESENTATIVES = 24
METHOD = "classifier-penultimate/spherical-kmeans-v1"


def cluster_count(n: int) -> int:
    """How many clusters a family of `n` glyphs is split into."""
    if n < MIN_FAMILY_UNITS:
        return 1
    return max(2, min(MAX_CLUSTERS, round(math.sqrt(n) / 4)))


def family_of(code_point: str | None) -> dict | None:
    """The grapheme family of a code point, when it has more than one member."""
    info = refs.grapheme_info(code_point) if code_point else None
    if not info or len(info.get("members") or []) < 2:
        return None
    return info


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _crops(job: tuple[str, list[tuple[str, dict]]]) -> tuple[list[str], np.ndarray | None]:
    """Cut every box of one page, preprocessed for the classifier."""
    from PIL import Image

    from .classify import preprocess

    path, boxes = job
    with Image.open(path) as scan:
        scan = scan.convert("L")
        ids, arrays = [], []
        for identity, box in boxes:
            right = min(scan.width, box["x"] + box["w"])
            bottom = min(scan.height, box["y"] + box["h"])
            if right <= box["x"] or bottom <= box["y"]:
                continue
            crop = scan.crop((box["x"], box["y"], right, bottom))
            ids.append(identity)
            arrays.append(np.asarray(preprocess(crop), dtype=np.uint8))
    return ids, np.stack(arrays) if arrays else None


class Encoder:
    """The classifier's penultimate features, as L2-normalised vectors."""

    def __init__(self, checkpoint: Path, classes: Path):
        import timm
        import torch

        from .classify import read_classes

        if not torch.cuda.is_available():
            raise RuntimeError("form clustering needs CUDA; run gpu-check")
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        self.model = timm.create_model("convnext_tiny", pretrained=False, num_classes=len(read_classes(classes)))
        self.model.load_state_dict(state["model"], strict=True)
        self.model = self.model.eval().cuda()
        self.torch = torch

    def __call__(self, pixels: np.ndarray) -> np.ndarray:
        from .classify import MEAN, STD

        torch = self.torch
        with torch.inference_mode():
            x = torch.from_numpy(pixels).cuda().float().div_(255).sub_(MEAN).div_(STD)
            x = x[:, None].expand(-1, 3, -1, -1)
            features = self.model.forward_head(self.model.forward_features(x), pre_logits=True).float()
            return torch.nn.functional.normalize(features, dim=1).cpu().numpy()


def _embed(jobs: list[tuple[str, list[tuple[str, dict]]]], encoder: Encoder, *, workers: int,
           batch: int = 512) -> Iterator[tuple[list[str], np.ndarray]]:
    pending_ids: list[str] = []
    pending: list[np.ndarray] = []
    with ProcessPoolExecutor(workers, mp_context=get_context("fork")) as pool:
        for ids, arrays in pool.map(_crops, jobs, chunksize=4):
            if arrays is None:
                continue
            pending_ids.extend(ids)
            pending.append(arrays)
            if len(pending_ids) >= batch:
                pixels = np.concatenate(pending)
                yield pending_ids, encoder(pixels)
                pending_ids, pending = [], []
    if pending_ids:
        yield pending_ids, encoder(np.concatenate(pending))


def _page_jobs(corpus: Path, units: list[dict], resolve) -> list[tuple[str, list[tuple[str, dict]]]]:
    pages = {p["id"]: p["image"] for p in pq.read_table(corpus / "pages.parquet", columns=["id", "image"]).to_pylist()}
    by_page: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for unit in units:
        by_page[unit["page_id"]].append((unit["id"], unit["box"]))
    jobs = []
    for page, boxes in sorted(by_page.items()):
        path = resolve(pages.get(page))
        if path is None:
            raise FileNotFoundError(f"{page}: page scan is not held locally")
        jobs.append((str(path), boxes))
    return jobs


def _kmeans(x, k: int, seed: int):
    """Spherical k-means with k-means++ seeding, on the device `x` lives on."""
    import torch

    generator = torch.Generator(device=x.device).manual_seed(seed)
    centres = [x[int(torch.randint(len(x), (1,), generator=generator, device=x.device))]]
    closest = (1 - x @ centres[0]).clamp_min(0)
    for _ in range(1, k):
        weights = closest.square()
        index = int(torch.multinomial(weights / weights.sum(), 1, generator=generator)) if weights.sum() > 0 else 0
        centres.append(x[index])
        closest = torch.minimum(closest, (1 - x @ x[index]).clamp_min(0))
    c = torch.stack(centres)
    labels = None
    for _ in range(100):
        updated = (x @ c.T).argmax(1)
        if labels is not None and torch.equal(updated, labels):
            break
        labels = updated
        sums = torch.zeros_like(c).index_add_(0, labels, x)
        counts = torch.bincount(labels, minlength=k)
        c = torch.where(counts[:, None] > 0, torch.nn.functional.normalize(sums, dim=1), c)
    return labels, c


def run(root: Path, out: Path, *, checkpoint: Path, classes: Path, workers: int = 8) -> dict:
    """Embed, cluster and publish one revision. `root` is the corpus root holding `codh-full`."""
    import torch

    from . import images

    codh = root / "codh-full"
    inputs = {"codh_units": _digest(codh / "units.parquet"), "checkpoint": _digest(checkpoint),
              "classes": _digest(classes), "vocab": _digest(Path(refs.__file__).parents[2] / "data/vocab/characters.tsv"),
              "method": METHOD, "max_clusters": MAX_CLUSTERS, "min_family_units": MIN_FAMILY_UNITS}
    revision = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    target = out / revision
    if target.is_dir():
        write_neighbours(target)
        _point_current(out, revision)
        return {k: v for k, v in json.loads((target / "manifest.json").read_text()).items() if k != "inputs"}

    families: dict[str, dict] = {}
    units = []
    for unit in pq.read_table(codh / "units.parquet", columns=["id", "page_id", "box", "unicode", "kind", "active"]).to_pylist():
        if not unit["active"] or unit["kind"] != "char" or not unit["box"]:
            continue
        info = family_of(unit["unicode"])
        if info is None:
            continue
        families.setdefault(info["code_point"], info)
        units.append({**unit, "family": info["code_point"]})

    encoder = Encoder(checkpoint, classes)
    family_of_unit = {u["id"]: u["family"] for u in units}
    ids: list[str] = []
    vectors: list[np.ndarray] = []
    for batch_ids, batch_vectors in _embed(_page_jobs(codh, units, images.held), encoder, workers=workers):
        ids.extend(batch_ids)
        vectors.append(batch_vectors.astype(np.float16))
    matrix = np.concatenate(vectors)

    rows = {"id": [], "family": [], "cluster": [], "similarity": [], "rank": []}
    order: list[int] = []
    published = {}
    by_family: dict[str, list[int]] = defaultdict(list)
    for index, identity in enumerate(ids):
        by_family[family_of_unit[identity]].append(index)
    for family, indexes in sorted(by_family.items()):
        x = torch.as_tensor(matrix[indexes], device="cuda").float()
        x = torch.nn.functional.normalize(x, dim=1)
        k = cluster_count(len(indexes))
        labels, centres = _kmeans(x, k, seed=int(hashlib.sha256(family.encode()).hexdigest()[:8], 16))
        similarity = (x * centres[labels]).sum(1)
        clusters = []
        for c in range(k):
            local = torch.where(labels == c)[0]
            if not len(local):
                continue
            ranked = local[torch.argsort(-similarity[local])].tolist()
            members = [ids[indexes[i]] for i in ranked]
            cluster_id = f"{family}:{hashlib.sha256(members[0].encode()).hexdigest()[:10]}"
            for rank, i in enumerate(ranked):
                order.append(indexes[i])
                rows["id"].append(ids[indexes[i]])
                rows["family"].append(family)
                rows["cluster"].append(cluster_id)
                rows["similarity"].append(round(float(similarity[i]), 5))
                rows["rank"].append(rank)
            clusters.append({"id": cluster_id, "count": len(members),
                             "coherence": round(float(similarity[local].mean()), 5),
                             "representatives": members[:REPRESENTATIVES]})
        clusters.sort(key=lambda c: (-c["count"], c["id"]))
        for serial, cluster in enumerate(clusters, 1):
            cluster["label"] = f"Cluster {serial}"
        info = families[family]
        published[family] = {"family": family, "char": info["char"], "label": info["label"],
                             "members": info["members"], "count": len(indexes), "clusters": clusters}

    staging = out / f".{revision}.partial"
    # What an interrupted run of the same revision left behind is incomplete by construction.
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    pq.write_table(pa.table(rows), staging / "units.parquet")
    np.save(staging / "embeddings.npy", matrix[order], allow_pickle=False)
    (staging / "clusters.json").write_text(json.dumps(
        {"revision": revision, "method": METHOD, "families": published}, ensure_ascii=False, indent=1) + "\n")
    summary = {"revision": revision, "families": len(published), "units": len(ids),
               "clusters": sum(len(f["clusters"]) for f in published.values())}
    (staging / "manifest.json").write_text(json.dumps({**summary, "inputs": inputs}, indent=1) + "\n")
    os.replace(staging, target)
    write_neighbours(target)
    _point_current(out, revision)
    return summary


def shape_order(centres: np.ndarray) -> list[int]:
    """An order of unit-length centres in which similar ones sit next to each other.

    Average-linkage merging; each merge joins two runs end to end, turned so that their closest
    ends meet. A family has at most `MAX_CLUSTERS` centres, so the quadratic loop is cheap.
    """
    similarity = centres @ centres.T
    runs = [[i] for i in range(len(centres))]
    while len(runs) > 1:
        best = None
        for a in range(len(runs)):
            for b in range(a + 1, len(runs)):
                score = float(similarity[np.ix_(runs[a], runs[b])].mean())
                if best is None or score > best[0]:
                    best = (score, a, b)
        _, a, b = best
        left, right = runs[a], runs[b]
        joins = [(left, right), (left, right[::-1]), (left[::-1], right), (left[::-1], right[::-1])]
        first, second = max(joins, key=lambda pair: similarity[pair[0][-1], pair[1][0]])
        runs = [run for i, run in enumerate(runs) if i not in (a, b)] + [first + second]
    return runs[0] if runs else []


def write_neighbours(directory: Path) -> Path:
    """Write `neighbours.json` beside a clustering: per family, its clusters in shape order.

    The centres are the normalised means of each cluster's embeddings, which `run` stores
    row-aligned with `units.parquet`; a clustering written before this file existed gets it here.
    """
    target = directory / "neighbours.json"
    if target.is_file():
        return target
    table = pq.read_table(directory / "units.parquet", columns=["family", "cluster"]).to_pydict()
    vectors = np.load(directory / "embeddings.npy", mmap_mode="r")
    rows: dict[str, list[int]] = defaultdict(list)
    family_of_cluster = {}
    for index, (family, cluster) in enumerate(zip(table["family"], table["cluster"], strict=True)):
        rows[cluster].append(index)
        family_of_cluster[cluster] = family
    by_family: dict[str, list[str]] = defaultdict(list)
    for cluster in rows:
        by_family[family_of_cluster[cluster]].append(cluster)
    result = {}
    for family, clusters in sorted(by_family.items()):
        clusters.sort()
        centres = np.stack([vectors[rows[c]].astype(np.float32).mean(0) for c in clusters])
        centres /= np.linalg.norm(centres, axis=1, keepdims=True)
        order = shape_order(centres)
        similarity = centres @ centres.T
        np.fill_diagonal(similarity, -1)
        result[family] = {"order": [clusters[i] for i in order],
                          "adjacent": [round(float(similarity[a, b]), 4) for a, b in itertools.pairwise(order)],
                          "nearest": {clusters[i]: {"id": clusters[int(similarity[i].argmax())],
                                                    "similarity": round(float(similarity[i].max()), 4)}
                                      for i in range(len(clusters)) if len(clusters) > 1}}
    staging = directory / ".neighbours.json.next"
    staging.write_text(json.dumps(result, indent=1) + "\n")
    os.replace(staging, target)
    return target


def _point_current(out: Path, revision: str) -> None:
    link = out / ".current.next"
    link.unlink(missing_ok=True)
    link.symlink_to(revision)
    os.replace(link, out / "current")
