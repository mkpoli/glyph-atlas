"""The forms a person named, applied crop by crop.

`data/forms/special/<code>.yaml` names each form and the shape clusters (leaves) it is made of, for one run:

    code_point: U+5019
    run: 3f2a…            # the run its leaf numbers belong to (`run.json`)
    forms:
      - title: 候
        written: true     # the character written out, in any hand
        leaves: [1, 2, 3, 4, 5]
        text: …
      - title: ん / h shape
        drawings: [n-shape]   # drawings/<name>.svg beside the file
        leaves: [18, 19]
        text: …
    checks: [27, 3, 6]    # site clusters to highlight in the report
    notes: [ … ]          # the reading of the evidence, shown as a list
    sources: [ … ]        # anything else the decisions rest on, cited in the report

The shape model judges stroke shape well and the character well only when the crop is clean; the site's
classifier reads the character well. So when a form is `written`, whether a crop is written out is decided
by the classifier: a site cluster counts as written when most of its crops fall in the written leaves; a
logistic regression on the classifier's embedding learns from the crops where the site cluster and the
leaf agree, and decides the crops where they do not. A crop it moves out of the written form takes the
majority form of its 15 nearest abbreviated crops in the encoder's space.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

#: Least crops of each side (written, abbreviated) the classifier needs among the agreed crops.
MIN_CLASS = 10
#: Abbreviated neighbours that vote on the form of a crop moved out of the written form.
NEIGHBOURS = 15


def load(path: Path, leaves: int) -> dict:
    data = yaml.safe_load(path.read_text())
    seen: dict[int, str] = {}
    for form in data["forms"]:
        for leaf in form["leaves"]:
            if leaf in seen:
                raise ValueError(f"{path}: leaf {leaf} is in both {seen[leaf]!r} and {form['title']!r}")
            seen[leaf] = form["title"]
    missing = sorted(set(range(1, leaves + 1)) - set(seen))
    if missing:
        raise ValueError(f"{path}: leaves {missing} belong to no form")
    if sum(bool(f.get("written")) for f in data["forms"]) > 1:
        raise ValueError(f"{path}: more than one form is the written character")
    return data


def proposal(code: str, run: str, groups: list[list[int]]) -> dict:
    """A starting decisions file: one unnamed form per group of leaves."""
    return {"code_point": code, "run": run,
            "forms": [{"title": f"Group {i + 1}", "leaves": g, "text": ""} for i, g in enumerate(groups)],
            "checks": [], "notes": [], "sources": []}


def assign(forms: list[dict], leaf: np.ndarray, site: np.ndarray, emb: np.ndarray, features: np.ndarray) -> tuple[np.ndarray, dict]:
    """Form index of every crop, and how the written-or-abbreviation call came out."""
    of_leaf = {l: i for i, f in enumerate(forms) for l in f["leaves"]}
    by_shape = np.array([of_leaf[int(l)] for l in leaf])
    written = next((i for i, f in enumerate(forms) if f.get("written")), None)
    if written is None:
        return by_shape, {}
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict
    from sklearn.neighbors import KNeighborsClassifier

    in_shape = by_shape == written
    clusters = [c for c in np.unique(site) if in_shape[site == c].mean() > 0.5]
    in_site = np.isin(site, clusters)
    agree = in_site == in_shape
    if min(in_site[agree].sum(), (~in_site[agree]).sum()) < MIN_CLASS:
        # The site clusters give too few examples of one side to learn from: the shape clusters decide.
        return by_shape, {"written_clusters": [int(c) for c in clusters], "skipped": True}
    x = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    clf = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")
    held = cross_val_predict(clf, x[agree], in_site[agree], cv=5, method="predict_proba")[:, 1]
    clf.fit(x[agree], in_site[agree])
    is_written = np.where(agree, in_site, clf.predict_proba(x)[:, 1] > 0.5)
    form = np.where(is_written, written, by_shape)
    moved = in_shape & ~is_written
    if moved.any():
        abbreviated = ~in_shape
        if not abbreviated.any():
            raise ValueError("crops leave the written form, but no shape cluster is an abbreviation to give them")
        knn = KNeighborsClassifier(min(NEIGHBOURS, int(abbreviated.sum())), metric="cosine")
        form[moved] = knn.fit(features[abbreviated], by_shape[abbreviated]).predict(features[moved])
    return form, {
        "written_clusters": [int(c) for c in clusters],
        "agree": int(agree.sum()),
        "held_out_accuracy": float(((held > 0.5) == in_site[agree]).mean()),
        "disagree": int((~agree).sum()),
        "to_written": int((~agree & is_written).sum()),
        "to_abbreviation": int((~agree & ~is_written).sum()),
    }
