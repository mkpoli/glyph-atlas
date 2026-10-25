"""One family's crops, with the site cluster and classifier embedding of each."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

#: Longest side of a stored crop.
EDGE = 160
#: Longest side of a crop's colour copy, which the report shows.
COLOUR = 72


def _read(root: Path, ids: list[str], grey_edge: int | None, colour_edge: int | None) -> dict:
    """Each crop read once from its scan or crop file: grey at most `grey_edge` px, colour at most `colour_edge` px."""
    from PIL import Image, ImageOps

    from .. import form_clusters

    pos = {g: i for i, g in enumerate(ids)}
    pixels, by_file = form_clusters.Pixels(root), defaultdict(list)
    for glyph in form_clusters.glyphs(root, set(ids)):
        at = pixels(glyph)
        if at:
            by_file[str(at[0])].append((glyph["id"], at[1]))
    out = {}
    if grey_edge:
        out["grey"] = np.full((len(ids), grey_edge, grey_edge), 255, np.uint8)
        out["size"] = np.zeros((len(ids), 2), np.int32)
    if colour_edge:
        out["colour"] = np.full((len(ids), colour_edge, colour_edge, 3), 255, np.uint8)
        out["colour_size"] = np.zeros((len(ids), 2), np.int32)
    for file, items in by_file.items():
        try:
            image = Image.open(file)
            image.load()
        except OSError:
            continue
        for identity, box in items:
            crop = image if box is None else image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
            i = pos[identity]
            if grey_edge:
                g = ImageOps.grayscale(crop)
                g.thumbnail((grey_edge, grey_edge), Image.LANCZOS)
                out["grey"][i, :g.height, :g.width] = np.asarray(g)
                out["size"][i] = (g.height, g.width)
            if colour_edge:
                c = crop.convert("RGB")
                c.thumbnail((colour_edge, colour_edge), Image.LANCZOS)
                out["colour"][i, :c.height, :c.width] = np.asarray(c)
                out["colour_size"][i] = (c.height, c.width)
    return out


def collect(root: Path, forms: Path, code: str, out: Path) -> Path:
    """Write `out/crops.npz`: ids, site cluster number, embedding, grey crop and its size, for every crop of `code`,
    and `out/colour.npz`: each crop in colour, as the report shows it.

    Site clusters are numbered as the forms page numbers them, from 1 in the order of `clusters.json`
    (largest first). A crop whose pixels are not on disk keeps a size of 0.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    path = out / "crops.npz"
    if path.exists():
        if not (out / "colour.npz").exists():
            read = _read(root, np.load(path)["ids"].tolist(), None, COLOUR)
            np.savez(out / "colour.npz", colour=read["colour"], size=read["colour_size"])
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
    read = _read(root, ids, EDGE, COLOUR)
    out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "colour.npz", colour=read["colour"], size=read["colour_size"])
    np.savez(path, ids=np.array(ids), site=site, emb=emb, grey=read["grey"], size=read["size"],
             revision=json.loads((forms / "manifest.json").read_text()).get("revision", forms.resolve().name))
    return path
