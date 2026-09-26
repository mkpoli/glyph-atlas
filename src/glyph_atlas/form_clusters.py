"""Cluster glyphs by shape within each character family, for form assignment.

Some corpora transcribe every form of a family under one code point: a hentaigana は and the
波-derived は are both U+306F, and CODH transcribes 假 as 仮. The glyphs of those corpora (`CORPORA`)
are pooled per family, so one set of clusters covers a family across all of them. Within each
family that has more than one member, the glyphs are embedded with the character classifier's
penultimate features and grouped by spherical k-means. A cluster is a proposal of shape, never an
identity; a person names its form.

A glyph is clustered when its pixels are on disk: a held page scan and its box, or a pre-cut crop.
The others wait for their pages to be harvested; the manifest counts them.

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
from collections import defaultdict, deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from functools import cache, partial
from multiprocessing import get_context
from pathlib import Path
from typing import Any

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
#: Corpora whose labels never record which form a glyph takes: none of their 1.59M labels uses a
#: hentaigana code point (checked 2026-09-25), and a kana glyph carries only its modern kana. The CODH
#: and HI Lab source records say so; Honkoku-Lines and the Ainu records align transcriptions from
#: みんなで翻刻, whose labels show the same. 古活字 records each kana's 字母 (4,417 hentaigana labels)
#: and HNG sorts each character by 字体 itself, so neither is clustered.
CORPORA = ("codh-full", "hilab", "honkoku-lines", "ainu-records")


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


def _corpora(root: Path) -> list:
    from .corpus import sources

    found = {corpus.name: corpus for corpus in sources.discover(root)}
    return [found[name] for name in CORPORA if name in found and found[name].parquet_files("units")]


def units_stamp(root: Path) -> tuple:
    """Changes whenever a clustered corpus's units change."""
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                 for corpus in _corpora(root) for path in corpus.parquet_files("units"))


def glyphs(root: Path, wanted: set[str] | None = None) -> Iterator[dict]:
    """Every character glyph of a multi-form family in `CORPORA`, or of those in `wanted`.

    Each is `{id, corpus, family, info, page, box, crop}`: a boxed glyph names its page image and
    its box as `(x, y, w, h)`, any other its pre-cut crop.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    for corpus in _corpora(root):
        pages = {}
        if corpus.parquet_files("pages"):
            table = ds.dataset([str(p) for p in corpus.parquet_files("pages")], format="parquet")
            pages = dict(zip(*table.to_table(columns=["id", "image"]).to_pydict().values(), strict=True))
        # Filtered in Arrow, so only candidate rows become Python objects.
        where = (pc.field("active") & (pc.field("kind") == "char")
                 & (pc.field("box").is_valid() | pc.field("crop").is_valid()))
        if wanted is not None:
            where &= pc.field("id").isin(pa.array(sorted(wanted), pa.string()))
        table = ds.dataset([str(p) for p in corpus.parquet_files("units")], format="parquet").to_table(
            columns=["id", "page_id", "box", "crop", "unicode"], filter=where)
        # Box fields column by column: a struct per row as a Python dict costs more than the glyphs.
        box = table.column("box").combine_chunks()
        columns = [table.column(name).to_pylist() for name in ("id", "page_id", "crop", "unicode")]
        corners = [box.field(k).to_pylist() for k in "xywh"]
        valid = box.is_valid().to_pylist()
        for row, (identity, page_id, crop, unicode) in enumerate(zip(*columns, strict=True)):
            info = _family(unicode)
            if info is None:
                continue
            page = pages.get(page_id) if valid[row] else None
            yield {"id": identity, "corpus": corpus.name, "family": info["code_point"], "info": info, "page": page,
                   "box": tuple(c[row] for c in corners) if page else None, "crop": None if page else crop}


@cache
def _family(code_point: str | None) -> dict | None:
    return family_of(code_point)


class Pixels:
    """Where a glyph's pixels are on disk: its page scan and `(x, y, w, h)`, or its crop file and None."""

    def __init__(self, root: Path):
        from . import images
        from .corpus.crops import CropResolver

        # HI Lab crops are extracted to `cache/hilab` beside the image cache, wherever that is.
        self.resolver = CropResolver(root, file_bases=(root, images.cache_root().parent, Path.cwd()))
        # Only found files are remembered, so a page harvested or a crop extracted later is found.
        self.pages: dict[tuple[str, str], Path] = {}

    def __call__(self, glyph: dict) -> tuple[Path, tuple[int, int, int, int] | None] | None:
        if glyph["box"]:
            key = (glyph["corpus"], glyph["page"])
            if key not in self.pages:
                found = self.resolver.page_image_path(glyph["page"], glyph["corpus"])
                if found is None:
                    return None
                self.pages[key] = found
            return self.pages[key], glyph["box"]
        path = self.resolver.archive_member_path(glyph["crop"])
        return (path, None) if path else None


def _crops(job: tuple[str, list[tuple[str, dict]]], size: int) -> tuple[list[str], np.ndarray | None]:
    """Cut every box of one page, preprocessed for the classifier at its size."""
    from PIL import Image

    from .classify import preprocess

    path, boxes = job
    with Image.open(path) as scan:
        scan = scan.convert("L")
        ids, arrays = [], []
        for identity, box in boxes:
            if box is None:
                # A pre-cut crop is the glyph whole.
                ids.append(identity)
                arrays.append(np.asarray(preprocess(scan, size=size), dtype=np.uint8))
                continue
            x, y, w, h = box
            right, bottom = min(scan.width, x + w), min(scan.height, y + h)
            if right <= x or bottom <= y:
                continue
            crop = scan.crop((x, y, right, bottom))
            ids.append(identity)
            arrays.append(np.asarray(preprocess(crop, size=size), dtype=np.uint8))
    return ids, np.stack(arrays) if arrays else None


class Encoder:
    """The classifier's penultimate features, as L2-normalised vectors."""

    def __init__(self, checkpoint: Path):
        import torch

        from .classify import load_checkpoint

        if not torch.cuda.is_available():
            raise RuntimeError("form clustering needs CUDA; run gpu-check")
        trained = load_checkpoint(checkpoint)
        self.size, self.classes, self.temperature = trained.size, trained.classes, trained.temperature
        self.model = trained.model.cuda()
        self.torch = torch

    def __call__(self, pixels: np.ndarray) -> np.ndarray:
        return self.classify(pixels)[0]

    def classify(self, pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The features, and the calibrated class probabilities the classifier's head gives them."""
        from .classify import MEAN, STD

        torch = self.torch
        with torch.inference_mode():
            x = torch.from_numpy(pixels).cuda().float().div_(255).sub_(MEAN).div_(STD)
            x = x[:, None].expand(-1, 3, -1, -1)
            pooled = self.model.forward_features(x)
            features = self.model.forward_head(pooled, pre_logits=True)
            # Calibrated as the served export's `probs` are: the logits over the fitted temperature.
            probabilities = (self.model.forward_head(pooled).float() / self.temperature).softmax(1)
            return (torch.nn.functional.normalize(features.float(), dim=1).cpu().numpy(),
                    probabilities.cpu().numpy())


def _embed(jobs: list[tuple[str, list[tuple[str, dict]]]], encoder, *, size: int, workers: int,
           batch: int = 512) -> Iterator[tuple[list[str], Any]]:
    """Cut the glyphs of every job in worker processes at `size`, and run `encoder` over them a batch
    at a time."""
    pending_ids: list[str] = []
    pending: list[np.ndarray] = []
    with ProcessPoolExecutor(workers, mp_context=get_context("fork")) as pool:
        for ids, arrays in _bounded_map(pool, partial(_crops, size=size), jobs, ahead=4 * workers):
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


def _bounded_map(pool: ProcessPoolExecutor, fn, jobs, *, ahead: int) -> Iterator:
    """`pool.map(fn, jobs)` with at most `ahead` jobs submitted and not yet consumed.

    `Executor.map` submits every job at once, so when the consumer is slower than the workers
    their finished results pile up in this process's memory until the run ends or runs out of it.
    """
    queued: deque = deque()
    for job in jobs:
        if len(queued) >= ahead:
            yield queued.popleft().result()
        queued.append(pool.submit(fn, job))
    while queued:
        yield queued.popleft().result()


def _file_jobs(located: dict[str, tuple[Path, tuple | None]]) -> list[tuple[str, list[tuple[str, dict | None]]]]:
    """One job per file on disk: a page scan with its boxes, or a crop file with none."""
    by_file: dict[str, list[tuple[str, tuple | None]]] = defaultdict(list)
    for identity, (path, box) in located.items():
        by_file[str(path)].append((identity, box))
    return sorted(by_file.items())


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


def run(root: Path, out: Path, *, checkpoint: Path, workers: int = 8) -> dict:
    """Embed, cluster and publish one revision. `root` is the corpus root holding `CORPORA`."""
    import torch

    families: dict[str, dict] = {}
    family_of_unit: dict[str, str] = {}
    corpus_of_unit: dict[str, str] = {}
    located: dict[str, tuple[Path, tuple | None]] = {}
    pixels = Pixels(root)
    unheld = defaultdict(int)
    for glyph in glyphs(root):
        found = pixels(glyph)
        if found is None:
            unheld[glyph["corpus"]] += 1
            continue
        families.setdefault(glyph["family"], glyph["info"])
        family_of_unit[glyph["id"]] = glyph["family"]
        corpus_of_unit[glyph["id"]] = glyph["corpus"]
        located[glyph["id"]] = found

    corpora = _corpora(root)
    # The glyphs whose pixels are on disk are an input too: a page harvested since the last run
    # brings its glyphs into a new revision.
    inputs = {"units": {corpus.name: [_digest(path) for path in corpus.parquet_files("units")] for corpus in corpora},
              "pages": {corpus.name: [_digest(path) for path in corpus.parquet_files("pages")] for corpus in corpora},
              "located": hashlib.sha256("\n".join(sorted(located)).encode()).hexdigest(),
              "checkpoint": _digest(checkpoint),
              "vocab": _digest(Path(refs.__file__).parents[2] / "data/vocab/characters.tsv"),
              "method": METHOD, "max_clusters": MAX_CLUSTERS, "min_family_units": MIN_FAMILY_UNITS}
    revision = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    target = out / revision
    if target.is_dir():
        write_neighbours(target)
        _point_current(out, revision)
        return {k: v for k, v in json.loads((target / "manifest.json").read_text()).items() if k != "inputs"}

    encoder = Encoder(checkpoint)
    ids: list[str] = []
    vectors: list[np.ndarray] = []
    for batch_ids, batch_vectors in _embed(_file_jobs(located), encoder, size=encoder.size, workers=workers):
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
    by_corpus = defaultdict(int)
    for identity in ids:
        by_corpus[corpus_of_unit[identity]] += 1
    summary = {"revision": revision, "families": len(published), "units": len(ids),
               "clusters": sum(len(f["clusters"]) for f in published.values()),
               "by_corpus": dict(sorted(by_corpus.items())), "unheld": dict(sorted(unheld.items()))}
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
