"""The report page: one self-contained HTML file with thumbnails, the tree, the forms and the method.

Plotly is loaded from jsDelivr for the 3-D map and the flow chart; everything else is inline.
"""
from __future__ import annotations

import base64
import io
import json
from importlib import resources
from pathlib import Path

import numpy as np

from . import LEAVES, tree

#: Crops a shape cluster shows, from its centre outwards.
PER_LEAF = 48
#: Crops a site cluster × form cell keeps for the comparison grid.
PER_CELL = 40


def _webp(a: np.ndarray, edge: int, quality: int = 55) -> str:
    from PIL import Image

    image = Image.fromarray(a)
    image.thumbnail((edge, edge))
    buf = io.BytesIO()
    image.save(buf, "WEBP", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _png(a: np.ndarray, edge: int = 48) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(a).resize((edge, edge), Image.LANCZOS).convert("L").save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def page_data(run: Path, decisions: dict, form: np.ndarray, stats: dict) -> dict:
    """Everything the page draws, keyed as the template reads it."""
    import umap
    from sklearn.metrics import silhouette_score

    data = np.load(run / "crops.npz")
    grey, size, site = data["grey"], data["size"], data["site"].astype(int)
    idx = np.load(run / "idx.npy")
    shaped = np.load(run / "ink.npy")
    t = np.load(run / "tree.npz")
    y, z = t["y"], t["z"]
    features = np.load(run / "features.npy")
    n, nsite = len(idx), int(site.max())
    leaf = tree.cut(z, LEAVES) - 1
    old = site[idx]
    rng = np.random.default_rng(0)

    def thumb(i: int, edge: int = 64) -> str:
        h, w = size[i]
        return _webp(grey[i, :h, :w], edge)

    leaves_of: dict[int, set] = {}

    def below(c: int) -> set:
        if c < n:
            return {int(leaf[c])}
        if c not in leaves_of:
            a, b = z[c - n, :2].astype(int)
            leaves_of[c] = below(a) | below(b)
        return leaves_of[c]

    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * n))
    first = len(z) - (LEAVES - 1)
    nodes = []
    for j in range(LEAVES - 1):
        row = z[first + j]
        kids = [{"node": int(c) - n - first} if int(c) >= n + first else {"leaf": next(iter(below(int(c))))} for c in row[:2]]
        nodes.append({"kids": kids, "h": float(row[2])})

    centre = np.stack([y[leaf == c].mean(0) for c in range(LEAVES)])
    leaves = []
    for c in range(LEAVES):
        m = np.nonzero(leaf == c)[0]
        m = m[np.argsort(np.linalg.norm(y[m] - centre[c], axis=1))]
        pick = m[np.linspace(0, len(m) - 1, min(PER_LEAF, len(m))).astype(int)]
        leaves.append({"c": c, "count": len(m), "crops": [thumb(idx[i]) for i in pick],
                       "shapes": [_png(255 - shaped[idx[i]]) for i in pick],
                       "old": np.bincount(old[m], minlength=nsite + 1).tolist()})

    xyz = umap.UMAP(n_components=3, n_neighbors=30, min_dist=0.2, random_state=0).fit_transform(y)
    points, pthumbs = [], []
    for c in range(LEAVES):
        m = np.nonzero(leaf == c)[0]
        for i in rng.choice(m, min(60, len(m)), replace=False):
            points.append([c, *np.round(xyz[i], 3).tolist(), int(old[i])])
            pthumbs.append(thumb(idx[i]))
    sample = rng.choice(n, min(4000, n), replace=False)
    centredf = tree.centred(features)
    cuts = {}
    for k in range(2, LEAVES + 1):
        g = tree.cut(z, k)
        cuts[k] = {"groups": {int(leaf[i]): int(g[i]) for i in range(n)},
                   "sil": float(silhouette_score(centredf[sample], g[sample], metric="cosine")),
                   "height": float(z[-(k - 1), 2])}

    nforms = len(decisions["forms"])
    matrix, cl, lf = {}, {}, {}
    for c in range(1, nsite + 1):
        matrix[c] = []
        for f in range(nforms):
            m = np.nonzero((old == c) & (form == f))[0]
            matrix[c].append({"n": len(m), "items": [[thumb(idx[i], 60), int(leaf[i]) + 1] for i in rng.permutation(m)[:PER_CELL]]})
        for lv in range(LEAVES):
            m = np.nonzero((old == c) & (leaf == lv))[0]
            if len(m) >= 5:
                cl[f"{c}-{lv + 1}"] = [thumb(idx[i], 60) for i in rng.permutation(m)[:8]]
    for lv in range(LEAVES):
        for f in range(nforms):
            m = np.nonzero((leaf == lv) & (form == f))[0]
            if len(m):
                lf[f"{lv + 1}-{f}"] = [thumb(idx[i], 60) for i in rng.permutation(m)[:8]]

    return {"leaves": leaves, "nodes": nodes, "cuts": cuts, "points": points, "pthumbs": pthumbs,
            "xyz_centre": [np.round(xyz[leaf == c].mean(0), 3).tolist() for c in range(LEAVES)],
            "empty": int(len(site) - n), "total": len(site), "nsite": nsite,
            "oldcount": np.bincount(site, minlength=nsite + 1).tolist(),
            "matrix": matrix, "cl": cl, "lf": lf,
            "lform": [np.bincount(form[leaf == lv], minlength=nforms).tolist() for lv in range(LEAVES)],
            "formCount": np.bincount(form, minlength=nforms).tolist(), "stats": stats}


def render(run: Path, decisions: dict, drawings: Path, data: dict, char: str) -> str:
    meta = json.loads((run / "run.json").read_text())
    forms = []
    for f in decisions["forms"]:
        entry = {"title": f["title"], "leaves": f["leaves"], "text": f.get("text", "")}
        if f.get("written"):
            entry["han"] = char
        if f.get("drawings"):
            entry["svgs"] = f["drawings"]
        forms.append(entry)
    svgs = {name: (drawings / f"{name}.svg").read_text().strip() for f in decisions["forms"] for name in f.get("drawings", [])}
    data = {**data, "forms": forms, "notes": decisions.get("notes", []), "checks": decisions.get("checks", []),
            "sources": decisions.get("sources", []), "meta": {**meta, "char": char,
                                                               "stale": decisions.get("run") not in (None, meta["run"])}}
    template = resources.files(__package__).joinpath("report.html").read_text()

    def safe(value) -> str:
        return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

    fills = {"__CHAR__": char, "__CODE__": meta["code_point"], "__NSITE__": str(data["nsite"]),
             "__REVISION__": meta["revision"], "__STEPS__": f"{meta['steps']:,}", "__CRISP__": f"{meta['crisp']:,}",
             "__USABLE__": f"{meta['usable']:,}", "__EPOCHS__": str(meta["epochs"])}
    for key, value in fills.items():
        template = template.replace(key, value)
    return template.replace("__DATA__", safe(data)).replace("__SVGS__", safe(svgs))
