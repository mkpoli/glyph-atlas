"""Embed every published crop for similar-crop search, and list each crop's nearest crops.

The vectors are the served character classifier's penultimate features (`form_clusters.Encoder`),
L2-normalised. On HI Lab crops (2026-09-27) they found a crop's nearest other crop of the same
character 81% of the time for characters the classifier knows and 46% for the 689 it has no class
for, in a gallery of 242,841 crops; a general-purpose DINOv2-S found 2–4%.

Two kinds of crop are embedded, keyed by the ids the site serves:

- corpus glyphs, from the corpus datasets under `root` (`corpus.index.UNIT_CORPORA`): every active
  character unit whose page scan or crop file is on disk (`form_clusters.Pixels`). CODH glyphs, whose
  zips were removed, wait until their images are held again;
- local crops, from the catalogue exports of Cloudflare publications that the caller names, in
  publication order: a later export's row wins for the same id. Each row names the display crop the
  site shows; the export's `media` table places its bytes in the export's packs, and a crop whose
  pack has been removed is fetched from the site by its key.

`index` writes `<out>/<revision>/`:

- `units.parquet`: one row per crop, row-aligned with the vectors: `id`, `label` (the text it is
  filed under), `origin` (`corpus` or `local`), `source` (its corpus or export) and `fingerprint`;
- `vectors.npy`: float16 unit vectors;
- `manifest.json`: counts and the inputs.

A crop whose image, box and encoder are unchanged since the current revision keeps its vector, so a
run embeds only what is new; a changed label or source makes a new revision without re-embedding.
`<out>/current` then points at the revision. `neighbours` adds `<revision>/neighbours/`.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import refs

METHOD = "classifier-penultimate-v1"
#: Neighbours kept per crop, in each list.
NEIGHBOURS = 20
#: A crop's shard is the first `SHARD_DIGITS` hex digits of the SHA-256 of its id.
SHARD_DIGITS = 3


class ExportUnreadable(RuntimeError):
    """A named export could not be read, so the crops it publishes would silently fall back."""


def corpus_crops(root: Path) -> dict[str, dict]:
    """Every held corpus glyph as `{id: {path, box, label, origin, source}}`."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    from .corpus import sources
    from .corpus.index import UNIT_CORPORA
    from .form_clusters import Pixels

    pixels = Pixels(root)
    found = {}
    corpora = {corpus.name: corpus for corpus in sources.discover(root)}
    for name in UNIT_CORPORA:
        corpus = corpora.get(name)
        if corpus is None or not corpus.parquet_files("units"):
            continue
        pages = {}
        if corpus.parquet_files("pages"):
            table = ds.dataset([str(p) for p in corpus.parquet_files("pages")], format="parquet")
            pages = dict(zip(*table.to_table(columns=["id", "image"]).to_pydict().values(), strict=True))
        where = (pc.field("active") & (pc.field("kind") == "char")
                 & (pc.field("box").is_valid() | pc.field("crop").is_valid()))
        table = ds.dataset([str(p) for p in corpus.parquet_files("units")], format="parquet").to_table(
            columns=["id", "page_id", "box", "crop", "unicode"], filter=where)
        box = table.column("box").combine_chunks()
        corners = [box.field(k).to_pylist() for k in "xywh"]
        valid = box.is_valid().to_pylist()
        columns = [table.column(n).to_pylist() for n in ("id", "page_id", "crop", "unicode")]
        for row, (identity, page_id, crop, unicode) in enumerate(zip(*columns, strict=True)):
            page = pages.get(page_id) if valid[row] else None
            glyph = {"corpus": name, "page": page, "box": tuple(c[row] for c in corners) if page else None,
                     "crop": None if page else crop}
            if glyph["box"] is None and glyph["crop"] is None:
                continue
            located = pixels(glyph)
            if located is None:
                continue
            found[identity] = {"path": str(located[0]), "box": located[1],
                               "label": refs.to_char(unicode) if unicode else None,
                               "origin": "corpus", "source": name}
    return found


def local_crops(exports: list[Path], fetched: Path) -> dict[str, dict]:
    """Every local crop the named catalogue exports publish, as the display crop the site shows.

    `exports` is in publication order and a later export's row wins. An export that cannot be read,
    such as one still being written, raises `ExportUnreadable`: its crops would otherwise come from an
    older export, or be left out. A crop whose pack has been removed is read from
    `fetched/<key[:2]>/<key>.webp`, which `fetch` fills from the site.
    """
    found = {}
    for export in exports:
        try:
            with sqlite3.connect(f"file:{export}?mode=ro", uri=True, timeout=1) as db:
                rows = db.execute("""SELECT u.id, json_extract(u.data,'$.label'), m.key, m.object, m.offset, m.size
                    FROM units u JOIN media m ON m.key = replace(replace(json_extract(u.data,'$.image'),
                        '/atlas/media/', ''), '.webp', '')
                    WHERE u.origin='local'""").fetchall()
        except sqlite3.Error as error:
            raise ExportUnreadable(f"{export}: {error}") from error
        for identity, label, key, pack, offset, size in rows:
            label = unicodedata.normalize("NFC", label) if label else None
            crop = {"label": label or None, "origin": "local", "source": export.parent.name, "key": key}
            path = export.parent / pack
            if path.is_file():
                crop.update(path=str(path), range=(offset, size))
            else:
                crop.update(path=str(fetched / key[:2] / f"{key}.webp"))
            found[identity] = crop
    return found


def _is_webp(data: bytes) -> bool:
    return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def fetch(crops: dict[str, dict], base: str, *, workers: int = 8) -> dict[str, int]:
    """Download the display crops `local_crops` expects under its fetched directory and lacks.

    Each is `<base>/atlas/media/<key>.webp`, the public URL the site serves it at. A response that is
    not a WebP image is not kept, so the next run asks again.
    """
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    wanted = {c["key"]: Path(c["path"]) for c in crops.values() if "key" in c and "range" not in c}
    missing = [(key, path) for key, path in wanted.items() if not path.is_file()]
    counts = {"held": len(wanted) - len(missing), "fetched": 0, "failed": 0}
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        def one(item):
            key, path = item
            try:
                response = client.get(f"{base.rstrip('/')}/atlas/media/{key}.webp")
                response.raise_for_status()
                if not _is_webp(response.content):
                    return False
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".part")
                temporary.write_bytes(response.content)
                temporary.replace(path)
            except (httpx.HTTPError, OSError):
                return False
            return True
        with ThreadPoolExecutor(workers) as pool:
            for ok in pool.map(one, missing):
                counts["fetched" if ok else "failed"] += 1
    return counts


def _cut(job: tuple[str, list[tuple[str, tuple | None, tuple | None]]], size: int) -> tuple[list[str], np.ndarray | None]:
    """Every crop of one file at the classifier's size: boxes of a page scan, a whole crop file, or
    byte ranges of a pack of display crops. A file or crop that cannot be read yields nothing."""
    from PIL import Image

    from .classify import preprocess

    path, items = job
    ids, arrays = [], []
    try:
        if items[0][2] is not None:
            with open(path, "rb") as handle:
                for identity, _, (offset, length) in items:
                    handle.seek(offset)
                    try:
                        with Image.open(io.BytesIO(handle.read(length))) as image:
                            arrays.append(np.asarray(preprocess(image, size=size), dtype=np.uint8))
                    except (OSError, ValueError):
                        continue
                    ids.append(identity)
        else:
            with Image.open(path) as scan:
                scan = scan.convert("L")
                for identity, box, _ in items:
                    crop = scan
                    if box is not None:
                        x, y, w, h = box
                        right, bottom = min(scan.width, x + w), min(scan.height, y + h)
                        if right <= x or bottom <= y:
                            continue
                        crop = scan.crop((x, y, right, bottom))
                    arrays.append(np.asarray(preprocess(crop, size=size), dtype=np.uint8))
                    ids.append(identity)
    except (OSError, ValueError, Image.DecompressionBombError):
        return [], None
    return ids, np.stack(arrays) if arrays else None


def _embed(crops: dict[str, dict], encoder, *, workers: int, batch: int = 512):
    """Cut `crops` in worker processes, grouped by file, and run `encoder` a batch at a time."""
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial
    from multiprocessing import get_context

    from .form_clusters import _bounded_map

    by_file = defaultdict(list)
    for identity, crop in crops.items():
        by_file[crop["path"]].append((identity, crop.get("box"), crop.get("range")))
    jobs = sorted(by_file.items())
    pending_ids, pending = [], []
    with ProcessPoolExecutor(workers, mp_context=get_context("fork")) as pool:
        for ids, arrays in _bounded_map(pool, partial(_cut, size=encoder.size), jobs, ahead=4 * workers):
            if arrays is None:
                continue
            pending_ids.extend(ids)
            pending.append(arrays)
            if len(pending_ids) >= batch:
                yield pending_ids, encoder(np.concatenate(pending))
                pending_ids, pending = [], []
    if pending_ids:
        yield pending_ids, encoder(np.concatenate(pending))


def fingerprint(crop: dict, encoder: str) -> str:
    """What a vector depends on: the image file as it is now, the box or byte range, and the encoder."""
    stat = os.stat(crop["path"])
    raw = json.dumps([crop["path"], stat.st_size, stat.st_mtime_ns, crop.get("box"), crop.get("range"), encoder])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def index(root: Path, exports: list[Path], out: Path, *, checkpoint: Path, workers: int = 8,
          base: str = "https://glyphatlas.org") -> dict:
    """Embed every corpus glyph and local crop, reusing unchanged vectors of the current revision."""
    from .form_clusters import Encoder

    encoder_digest = hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()
    local = local_crops(exports, out / "media")
    fetched = fetch(local, base)
    crops = {**corpus_crops(root), **{i: c for i, c in local.items() if Path(c["path"]).is_file()}}
    ids = sorted(crops)
    prints = [fingerprint(crops[i], encoder_digest) for i in ids]
    described = [[crops[i]["label"], crops[i]["origin"], crops[i]["source"]] for i in ids]
    revision = hashlib.sha256(json.dumps([METHOD, ids, prints, described]).encode()).hexdigest()[:16]
    target = out / revision
    if not target.is_dir():
        previous, old_vectors = {}, None
        current = out / "current"
        if (current / "units.parquet").is_file():
            old = pq.read_table(current / "units.parquet", columns=["id", "fingerprint"]).to_pydict()
            old_vectors = np.load(current / "vectors.npy", mmap_mode="r")
            previous = {(i, f): row for row, (i, f) in enumerate(zip(old["id"], old["fingerprint"], strict=True))}
        kept = {row: previous[(i, f)] for row, (i, f) in enumerate(zip(ids, prints, strict=True)) if (i, f) in previous}
        missing = {i: row for row, i in enumerate(ids) if row not in kept}
        vectors = np.zeros((len(ids), old_vectors.shape[1] if old_vectors is not None else 0), dtype=np.float16)
        embedded = np.zeros(len(ids), dtype=bool)
        if missing:
            encoder = Encoder(checkpoint)
            for batch_ids, batch_vectors in _embed({i: crops[i] for i in missing}, encoder, workers=workers):
                if vectors.shape[1] != batch_vectors.shape[1]:
                    vectors = np.zeros((len(ids), batch_vectors.shape[1]), dtype=np.float16)
                    embedded[:] = False
                rows = [missing[i] for i in batch_ids]
                vectors[rows] = batch_vectors.astype(np.float16)
                embedded[rows] = True
        for row, old_row in kept.items():
            vectors[row] = old_vectors[old_row]
            embedded[row] = True
        # A crop that could not be cut (an empty box, an unreadable image) has no vector.
        keep = np.flatnonzero(embedded)
        staging = out / f".{revision}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        pq.write_table(pa.table({
            "id": [ids[i] for i in keep], "label": [crops[ids[i]]["label"] for i in keep],
            "origin": [crops[ids[i]]["origin"] for i in keep], "source": [crops[ids[i]]["source"] for i in keep],
            "fingerprint": [prints[i] for i in keep]}), staging / "units.parquet")
        np.save(staging / "vectors.npy", vectors[keep], allow_pickle=False)
        origins = defaultdict(int)
        for i in keep:
            origins[crops[ids[i]]["origin"]] += 1
        manifest = {"revision": revision, "method": METHOD, "crops": len(keep), "by_origin": dict(origins),
                    "embedded": int(embedded[list(missing.values())].sum()) if missing else 0,
                    "reused": len(kept), "unembedded": len(ids) - len(keep), "encoder": encoder_digest,
                    "exports": [str(e) for e in exports], "fetched": fetched}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        os.replace(staging, target)
    link = out / "current"
    temporary = out / ".current.tmp"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(revision)
    os.replace(temporary, link)
    return json.loads((target / "manifest.json").read_text())


def shard_of(identity: str, digits: int = SHARD_DIGITS) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()[:digits]


def neighbours(directory: Path, *, k: int = NEIGHBOURS, block: int = 1024, gallery: int = 262144,
               digits: int = SHARD_DIGITS) -> dict:
    """Each crop's nearest crops, and its nearest crops filed under another label, as shards.

    Writes `<directory>/neighbours/<shard>.json`, each `{id: {"similar": [[id, score], ...],
    "filed_differently": [[id, score], ...]}}` with cosine scores to three places, and
    `neighbours/manifest.json` naming the shard width. `filed_differently` is its own search over the
    crops with another label, so a common character still gets one. A crop without a label is listed
    in `similar` only and has no `filed_differently` list.

    Crops are taken shard by shard and each shard is written once its crops are done, so memory
    holds one shard's lists; the gallery is scanned a chunk at a time with a running top-k, so the
    GPU holds one block by one chunk whatever the number of crops.
    """
    import torch

    rows = pq.read_table(directory / "units.parquet", columns=["id", "label"]).to_pydict()
    ids, labels = rows["id"], rows["label"]
    vectors = torch.from_numpy(np.load(directory / "vectors.npy")).cuda()
    codes: dict[str, int] = {}
    label_codes = torch.tensor([codes.setdefault(label, len(codes)) if label else -1 for label in labels],
                               device="cuda")
    width = min(k, len(ids) - 1)
    order = sorted(range(len(ids)), key=lambda i: (shard_of(ids[i], digits), ids[i]))

    def search(query_rows):
        query = vectors[query_rows]
        own = label_codes[query_rows]
        empty = [torch.full((len(query_rows), 0), -2.0, device="cuda"),
                 torch.zeros((len(query_rows), 0), dtype=torch.long, device="cuda")]
        found = {"similar": empty, "filed_differently": [t.clone() for t in empty]}
        positions = torch.as_tensor(query_rows, device="cuda")
        for chunk in range(0, len(ids), gallery):
            scores = (query @ vectors[chunk:chunk + gallery].T).float()
            inside = positions - chunk
            mine = (inside >= 0) & (inside < scores.shape[1])
            scores[torch.nonzero(mine).squeeze(1), inside[mine]] = -2
            other = label_codes[chunk:chunk + gallery]
            differ = scores.masked_fill((other[None, :] == own[:, None]) | (other[None, :] < 0), -2)
            for name, values in (("similar", scores), ("filed_differently", differ)):
                local_scores, local = values.topk(min(width, values.shape[1]), dim=1)
                best_scores = torch.cat([found[name][0], local_scores], dim=1)
                best = torch.cat([found[name][1], local + chunk], dim=1)
                best_scores, pick = best_scores.topk(min(width, best_scores.shape[1]), dim=1)
                found[name] = [best_scores, best.gather(1, pick)]
        return {name: (value[0].cpu().numpy(), value[1].cpu().numpy()) for name, value in found.items()}

    target = directory / "neighbours"
    staging = directory / ".neighbours.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    written = 0

    def write(shard, entries):
        (staging / f"{shard}.json").write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")))

    shard, entries = None, {}
    for start in range(0, len(order), block):
        query_rows = order[start:start + block]
        found = search(query_rows)
        for n, row in enumerate(query_rows):
            identity = ids[row]
            here = shard_of(identity, digits)
            if here != shard:
                if shard is not None:
                    write(shard, entries)
                    written += 1
                shard, entries = here, {}
            entry = {}
            for name in ("similar", "filed_differently"):
                if name == "filed_differently" and not labels[row]:
                    continue
                scores, found_rows = found[name]
                entry[name] = [[ids[j], round(float(s), 3)]
                               for j, s in zip(found_rows[n], scores[n], strict=True) if s > -2]
            entries[identity] = entry
    if shard is not None:
        write(shard, entries)
        written += 1
    manifest = {"crops": len(ids), "k": k, "shard_digits": digits, "shards": written}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    return manifest
