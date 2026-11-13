"""One character's crops as a pack for allograph-clustering (https://github.com/mkpoli/allograph-clustering).

That project clusters a character's crops by stroke shape, names its allographs as categories and labels
every crop; the abbreviated forms of 候 are kept there. A pack is the part of that work that needs the corpus:
every clustered crop of one family, at most 160 px on the long side, with the classifier embedding and the
form cluster of each, in one `.npz` file:

    code_point           "U+5019"
    revision             the form clustering revision the pack was cut from
    ids                  (n,) crop identifiers
    grey                 (n, 160, 160) uint8, the crop in its top-left corner on white
    size                 (n, 2) height and width of the crop inside `grey`; 0 when its pixels are not on disk
    embedding            (n, d) float32, the character classifier's penultimate layer
    classifier_cluster   (n,) int16, the number in the cluster's label, as the forms page shows it
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

#: Longest side of a stored crop.
EDGE = 160
#: Corpora whose images carry one licence each, so their crops can be passed on in a pack. Images of the
#: other clustered corpora are licensed holder by holder.
PACKED = ("codh-full", "hilab")


def write(root: Path, forms: Path, code: str, out: Path) -> dict:
    """Write `out/<code>.npz` and return its counts.

    `left_out` are the crops of corpora outside `PACKED`, and `relabelled` those the corpus now reads as another
    family; neither is packed. `changed` names the corpus tables that differ from the ones the clustering was
    made from: a crop cut again since then no longer matches its embedding and cluster.
    """
    import numpy as np
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from PIL import Image, ImageOps

    from . import form_clusters

    units = pq.read_table(forms / "units.parquet")
    rows = np.nonzero(pc.equal(units.column("family"), code).to_numpy(zero_copy_only=False))[0]
    if not len(rows):
        raise ValueError(f"{code}: no clustered crops in {forms}")
    ids = units.column("id").take(rows).to_pylist()
    family = json.loads((forms / "clusters.json").read_text())["families"][code]
    number = {c["id"]: int(c["label"].removeprefix("Cluster ")) for c in family["clusters"]}
    cluster = np.array([number[c] for c in units.column("cluster").take(rows).to_pylist()], np.int16)
    embedding = np.asarray(np.load(forms / "embeddings.npy", mmap_mode="r")[rows], np.float32)

    pos = {g: i for i, g in enumerate(ids)}
    pixels, by_file = form_clusters.Pixels(root), defaultdict(list)
    packed, found, relabelled = (np.zeros(len(ids), bool) for _ in range(3))
    for glyph in form_clusters.glyphs(root, set(ids)):
        found[pos[glyph["id"]]] = True
        if glyph["family"] != code:
            relabelled[pos[glyph["id"]]] = True
            continue
        if glyph["corpus"] not in PACKED:
            continue
        packed[pos[glyph["id"]]] = True
        at = pixels(glyph)
        if at:
            by_file[str(at[0])].append((glyph["id"], at[1]))
    if not found.all():
        raise ValueError(f"{code}: {int((~found).sum())} clustered crops are no longer in the corpus; cluster again")
    grey = np.full((len(ids), EDGE, EDGE), 255, np.uint8)
    size = np.zeros((len(ids), 2), np.int32)
    for file, items in by_file.items():
        try:
            image = Image.open(file)
            image.load()
        except OSError:
            continue
        for identity, box in items:
            crop = image
            if box is not None:
                # Clipped to the scan, as the clustering cut it.
                right, bottom = min(image.width, box[0] + box[2]), min(image.height, box[1] + box[3])
                if right <= box[0] or bottom <= box[1]:
                    continue
                crop = image.crop((box[0], box[1], right, bottom))
            crop = ImageOps.grayscale(crop)
            crop.thumbnail((EDGE, EDGE), Image.LANCZOS)
            i = pos[identity]
            grey[i, :crop.height, :crop.width] = np.asarray(crop)
            size[i] = (crop.height, crop.width)
    manifest = json.loads((forms / "manifest.json").read_text())
    revision = manifest.get("revision", forms.resolve().name)
    changed = form_clusters.changed_inputs(root, manifest)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{code}.npz"
    np.savez_compressed(path, code_point=code, revision=revision, ids=np.array(ids)[packed], grey=grey[packed],
                        size=size[packed], embedding=embedding[packed], classifier_cluster=cluster[packed])
    return {"pack": str(path), "crops": int(packed.sum()), "with_pixels": int((size[packed, 0] > 0).sum()),
            "left_out": int((~packed & ~relabelled).sum()), "relabelled": int(relabelled.sum()), "revision": revision,
            "changed": changed}
