"""One family's crops, with the site cluster and classifier embedding of each."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

#: Longest side of a stored crop.
EDGE = 160


def collect(root: Path, forms: Path, code: str, out: Path) -> Path:
    """Write `out/crops.npz`: ids, site cluster number, embedding, grey crop and its size, for every crop of `code`.

    Site clusters are numbered as the forms page numbers them, from 1 in the order of `clusters.json`
    (largest first). A crop whose pixels are not on disk keeps a size of 0.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from PIL import Image, ImageOps

    from .. import form_clusters

    path = out / "crops.npz"
    if path.exists():
        return path
    units = pq.read_table(forms / "units.parquet")
    rows = np.nonzero(pc.equal(units.column("family"), code).to_numpy(zero_copy_only=False))[0]
    if not len(rows):
        raise ValueError(f"{code}: no clustered crops in {forms}")
    ids = units.column("id").take(rows).to_pylist()
    cluster = units.column("cluster").take(rows).to_pylist()
    family = json.loads((forms / "clusters.json").read_text())["families"][code]
    number = {c["id"]: i + 1 for i, c in enumerate(family["clusters"])}
    site = np.array([number[c] for c in cluster], np.int16)
    emb = np.asarray(np.load(forms / "embeddings.npy", mmap_mode="r")[rows], np.float32)

    pos = {g: i for i, g in enumerate(ids)}
    pixels, by_file = form_clusters.Pixels(root), defaultdict(list)
    for glyph in form_clusters.glyphs(root, set(ids)):
        at = pixels(glyph)
        if at:
            by_file[str(at[0])].append((glyph["id"], at[1]))
    grey = np.full((len(ids), EDGE, EDGE), 255, np.uint8)
    size = np.zeros((len(ids), 2), np.int32)
    for file, items in by_file.items():
        try:
            image = Image.open(file)
            image.load()
        except OSError:
            continue
        for identity, box in items:
            crop = image if box is None else image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
            crop = ImageOps.grayscale(crop)
            crop.thumbnail((EDGE, EDGE), Image.LANCZOS)
            i = pos[identity]
            grey[i, :crop.height, :crop.width] = np.asarray(crop)
            size[i] = (crop.height, crop.width)
    out.mkdir(parents=True, exist_ok=True)
    np.savez(path, ids=np.array(ids), site=site, emb=emb, grey=grey, size=size,
             revision=json.loads((forms / "manifest.json").read_text()).get("revision", forms.resolve().name))
    return path
