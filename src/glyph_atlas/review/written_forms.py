"""Crops filed under one character that are written as its variant.

A transcription writes 時 where the page has 时, and the crop is filed under 時. Its nearest crops
are filed the same way, so neither the neighbours (`neighbour_agreement`) nor the classifier, which
is trained on such labels, sees the difference. For a pair of variants the index
(`glyph_atlas.similar`) often holds crops under both characters, and the crops of the label that lie
among the variant's can be told from the rest. Labels on both sides come from sources, reviewers and
model passes alike; the pass does not ask which.

For each pair of characters that at least `SOURCES` variant tables give as variants of each other
(`refs.variants`), with `MIN_CROPS` crops or more under each:

- **twins.** A source may file one picture under both characters (HI Lab does). A crop with a
  picture of the other character at `TWIN` or more is left out altogether;
- **split.** The crops of both are divided in two by shape: each crop goes to the nearer of two
  means, which start as the means of the two labels and are taken again until nothing moves. Crops
  marked suspect on the site under the label they still carry take no part in the means;
- **told apart.** The pair counts only when at most one crop filed under the variant, or `STRAYS`
  of them, falls on the label's side, suspects included, and at most `MOST` of the label's crops
  fall on the variant's. Otherwise the two sides are hands, scans or cursive, or one shape filed
  under both, and no forms (島 and 嶋, 国 and 國 fail here);
- **written.** A crop of the label on the variant's side is `written` when its mean similarity to
  its `NEAREST` nearest crops on that side filed under the variant is `NEAR` or more, and `MARGIN`
  more than to those on the label's side, and its box holds one whole character
  (`consensus_relabel.gate`, against the boxes of its page). A stroke, a joined cut or another
  character falls on one side as well, and is near neither. A glyph cut by its source has no box on
  a page and is not judged by one (`gate` is `unboxed`).

The pair's label is the character with more crops. The tables also pair look-alikes (陣 and 陳),
where a crop found is one of the other character; the report names each pair's tables. What is found
is for a person to read: the rule has been checked by eye alone (docs/reports/written-forms.md).

`run` writes `<similar>/written-forms.parquet`, a row for every crop of the label on the variant's
side in the pairs told apart: `id`, `label`, `other`, `book`, `near`, `own`, `gate`, `written`.
Nothing is relabelled.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .consensus_relabel import gate

METHOD = "variant-split-v2"
FILE = "written-forms.parquet"
#: Variant tables that have to give two characters as variants for them to be taken as a pair.
SOURCES = 2
#: Crops the index has to hold under each character of a pair, and crops of each on its own side.
MIN_CROPS, MIN_SIDE = 15, 10
#: Share of the variant's crops that may fall on the label's side, where that is more than one crop.
STRAYS = 0.03
#: Crops a similarity is the mean over, the least similarity to the variant's side, and the least
#: lead over the label's.
NEAREST, NEAR, MARGIN = 10, 0.8, 0.1
#: Most of the label's crops that may fall on the variant's side.
MOST = 0.5
#: Similarity at which two crops are one picture.
TWIN = 0.999
#: `gate` of a crop with no box on a page.
UNBOXED = "unboxed"
#: Rounds of the split.
ROUNDS = 50
#: A crop number is its page shifted by this many bits, then its slot on the page.
PAGE_BITS = 24


def pairs(counts: dict[str, int], *, sources: int = SOURCES,
          least: int = MIN_CROPS) -> dict[tuple[str, str], tuple[str, ...]]:
    """Pairs of variants that both have `least` crops, the one with more crops first, each with the
    tables that state it."""
    from .. import refs

    found = {}
    for char, count in counts.items():
        if count < least or len(char) != 1:
            continue
        try:
            listed = refs.variants(char)
        except (KeyError, ValueError):
            continue
        for other, stated in listed:
            if len(stated) >= sources and counts.get(other, 0) >= least and other != char:
                first, second = sorted((char, other), key=lambda c: (-counts[c], c))
                found[first, second] = stated
    return found


def split(vectors: np.ndarray, own: np.ndarray, suspect: np.ndarray) -> np.ndarray:
    """Which crops lie on the label's side. `own` says which are filed under the label."""
    side = own.copy()
    for _ in range(ROUNDS):
        ours, theirs = side & ~suspect, ~side & ~suspect
        if ours.sum() < MIN_SIDE or theirs.sum() < MIN_SIDE:
            break
        first, second = vectors[ours].mean(0), vectors[theirs].mean(0)
        moved = vectors @ (second - first) <= (first + second) @ (second - first) / 2
        if (moved == side).all():
            break
        side = moved
    return side


def nearness(vectors: np.ndarray, others: np.ndarray, k: int = NEAREST) -> np.ndarray:
    """Each vector's mean similarity to its `k` nearest of `others`."""
    found = vectors @ others.T
    k = min(k, found.shape[1])
    return np.sort(found, axis=1)[:, -k:].mean(1)


def twins(vectors: np.ndarray, own: np.ndarray) -> np.ndarray:
    """Which crops are the picture of a crop filed under the other character."""
    found = np.zeros(len(vectors), dtype=bool)
    ours, theirs = np.flatnonzero(own), np.flatnonzero(~own)
    for start in range(0, len(ours), 2048):
        part = ours[start:start + 2048]
        same = vectors[part] @ vectors[theirs].T >= TWIN
        found[part] |= same.any(1)
        found[theirs] |= same.any(0)
    return found


def judge(vectors: np.ndarray, own: np.ndarray, suspect: np.ndarray) -> dict | None:
    """What the split of a pair says: the variant's crops and how many stray to the label's side, the
    label's crops and how many fall on the variant's, whether that tells the pair apart, and the
    rows of the latter with their similarity to each side. None where a side keeps too few crops of
    its own character."""
    vectors = np.asarray(vectors, dtype=np.float32)
    kept = np.flatnonzero(~twins(vectors, own))
    vectors, own, suspect = vectors[kept], own[kept], suspect[kept]
    if own.sum() < MIN_SIDE or (~own).sum() < MIN_SIDE:
        return None
    side = split(vectors, own, suspect)
    ours, theirs = own & side & ~suspect, ~own & ~side & ~suspect
    if ours.sum() < MIN_SIDE or theirs.sum() < MIN_SIDE:
        return None
    rows = np.flatnonzero(own & ~side)
    near = nearness(vectors[rows], vectors[theirs]) if len(rows) else np.zeros(0, dtype=np.float32)
    mine = nearness(vectors[rows], vectors[ours]) if len(rows) else np.zeros(0, dtype=np.float32)
    strays, filed, labelled = int((~own & side).sum()), int((~own).sum()), int(own.sum())
    return {"strays": strays, "filed": filed, "over": len(rows), "labelled": labelled,
            "told_apart": strays <= max(1, STRAYS * filed) and len(rows) <= MOST * labelled,
            "rows": kept[rows], "near": near, "own": mine}


def written(near: float, own: float, reason: str | None) -> bool:
    """Whether a crop on the variant's side is written as the variant."""
    return near >= NEAR and near - own >= MARGIN and reason in (None, UNBOXED)


def whole(crops: dict[str, int], pages: Path) -> dict[str, str | None]:
    """Why each crop's box is not one whole character, against the boxes of its page, or None."""
    wanted: dict[int, dict[int, str]] = defaultdict(dict)
    for identity, number in crops.items():
        wanted[number >> PAGE_BITS][number & ((1 << PAGE_BITS) - 1)] = identity
    found = {}
    for page, slots in wanted.items():
        bundles = sorted(pages.glob(f"{page}-*.json.gz"), key=lambda path: int(path.stem.split("-")[1].split(".")[0]))
        if not bundles:
            continue
        with gzip.open(bundles[-1], "rt", encoding="utf-8") as handle:
            boxed = [entry for entry in json.load(handle).get("crops", ()) if entry.get("box")]
        peers = [(entry["box"]["w"], entry["box"]["h"]) for entry in boxed]
        for entry in boxed:
            identity = slots.get(int(entry["slot"]))
            if identity:
                # Ink is judged by the similarity to whole crops; the boxes are judged here.
                found[identity] = gate((entry["box"]["w"], entry["box"]["h"]), peers, 1.0)
    return found


def _held(ids: Sequence[str], mirror: Path, atlas: Path) -> tuple[list[int | None], dict[int, tuple[int, str]], set[int]]:
    """Each crop's number, the book and character of those the site holds under one, and the numbers
    marked suspect under the character they still carry."""
    with sqlite3.connect(f"file:{mirror}?mode=ro", uri=True) as db:
        known = dict(db.execute("SELECT text, id FROM ids"))
    numbers = [known.get(identity) for identity in ids]
    row = {number: n for n, number in enumerate(numbers) if number is not None}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        held = {row[crop]: (book, unicodedata.normalize("NFC", text)) for crop, book, text in db.execute(
            "SELECT c.id, c.book, g.text FROM crops c JOIN graphemes g ON g.id = c.char") if crop in row}
        suspects = {crop for crop, label in db.execute("SELECT crop, label FROM suspects")
                    if crop in row and held.get(row[crop], (0, None))[1] == unicodedata.normalize("NFC", label)}
    return numbers, held, suspects


def run(similar: Path, *, mirror: Path, atlas: Path, pages: Path) -> dict:
    """Judge every variant pair the index holds and write the crops on the variant's side."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ids = pq.read_table(similar / "units.parquet", columns=["id"]).column("id").to_pylist()
    numbers, held, suspects = _held(ids, mirror, atlas)
    rows: dict[str, list[int]] = defaultdict(list)
    for n, (_, label) in held.items():
        rows[label].append(n)
    vectors = np.load(similar / "vectors.npy", mmap_mode="r")
    stated = pairs({label: len(found) for label, found in rows.items()})
    report, crops = [], []
    for label, other in sorted(stated):
        members = np.array(sorted(rows[label] + rows[other]))
        own = np.isin(members, rows[label])
        judged = judge(vectors[members], own, np.array([numbers[n] in suspects for n in members]))
        entry = {"label": label, "other": other, "tables": list(stated[label, other]),
                 "crops": [int(own.sum()), int((~own).sum())],
                 **({"told_apart": False} if judged is None else
                    {name: judged[name] for name in ("strays", "filed", "over", "labelled", "told_apart")})}
        report.append(entry)
        if entry["told_apart"]:
            for n, near, mine in zip(members[judged["rows"]], judged["near"], judged["own"], strict=True):
                crops.append({"id": ids[n], "label": label, "other": other, "book": held[n][0],
                              "near": float(near), "own": float(mine), "number": numbers[n]})
    gates = whole({crop["id"]: crop["number"] for crop in crops}, pages)
    for crop in crops:
        crop["gate"] = gates.get(crop["id"], UNBOXED)
        crop["written"] = written(crop["near"], crop["own"], crop["gate"])
        del crop["number"]
    for entry in report:
        entry["written"] = sum(crop["written"] for crop in crops
                               if crop["label"] == entry["label"] and crop["other"] == entry["other"])
    pq.write_table(pa.Table.from_pylist(crops, schema=pa.schema([
        ("id", pa.string()), ("label", pa.string()), ("other", pa.string()), ("book", pa.int64()),
        ("near", pa.float32()), ("own", pa.float32()), ("gate", pa.string()), ("written", pa.bool_())])),
        similar / FILE, compression="zstd")
    return {"method": METHOD, "index": len(ids), "held": len(held), "pairs": len(report),
            "told_apart": sum(entry["told_apart"] for entry in report), "judged": len(crops),
            "written": sum(crop["written"] for crop in crops), "items": report}
