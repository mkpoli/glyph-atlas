"""Crops filed under one character that look written as its variant.

A transcription writes 国 where the page has 國, and the crop is filed under 国. Its nearest crops
are filed the same way, so neither the neighbours (`neighbour_agreement`) nor the classifier, which
is trained on such labels, sees the difference. Some sources do keep the form, so for a pair of
variants the index (`glyph_atlas.similar`) holds crops under both characters. Their mean vectors
give a direction from one form to the other, and a crop is scored along it: positive when it lies
nearer the other form's crops than its own label's.

For each pair of characters that at least `SOURCES` variant tables give as variants of each other
(`refs.variants`), with `MIN_CROPS` crops or more under each. The tables also pair look-alikes
(玉 and 王, 陣 and 陳), where a high score is a crop of the other character and no written form;
the report names each pair's tables:

- **held-out books.** A crop is scored with the means of every other book's crops, so a book's hand
  or scan never stands in for a form. A crop is left unscored when the other books hold fewer than
  `MIN_TRAIN` crops of either character;
- **the score** is the crop's vector along the difference of the two means, zero at their midpoint.
  It ranks crops for a person to look at and is no verdict: the means come from the labels this
  pass doubts, so where most crops of 国 are written 國 the two means sit close together and the
  scores are small. Zero is no threshold, least of all for the character with few crops;
- **books.** The mean score of a book's crops of one character is kept too: a print or a scribe
  mostly keeps to one form, and a book whose 国 all lean to 國 is one decision for a reviewer.

Cursive is out of reach: 懐 and 懷 are one shape there, and the ranking of cursive crops follows
how fully the character is written out.

`run` writes `<similar>/written-forms.parquet`, one row per scored crop and pair: `id`, `label`,
`other` (the variant it was scored against), `book`, `score`. A character with two variants in the
index (余 with 餘 and 予) gives its crops a row for each. A crop the site does not hold, or holds
under no character, is left out. Nothing is relabelled.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np

METHOD = "variant-mean-direction-v1"
FILE = "written-forms.parquet"
#: Variant tables that have to give two characters as variants for them to be scored as a pair.
SOURCES = 2
#: Crops the index has to hold under each character of a pair.
MIN_CROPS = 15
#: Crops of each character the other books have to hold for a crop to be scored.
MIN_TRAIN = 5
#: Crops of one character a book needs for its mean to be reported.
MIN_BOOK = 5


def pairs(counts: dict[str, int], *, sources: int = SOURCES,
          least: int = MIN_CROPS) -> dict[tuple[str, str], tuple[str, ...]]:
    """Pairs of variants that both have `least` crops, the smaller code point first, each with the
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
                found[min(char, other), max(char, other)] = stated
    return found


def scores(vectors: np.ndarray, own: Sequence[bool], books: Sequence[int]) -> np.ndarray:
    """Each crop's place along the direction from its own character's mean to the other's.

    `vectors` are the crops of one pair, `own` says which carry the pair's first character. A crop
    is scored with the means of the other books' crops; positive means nearer the other character
    than its label's. NaN where the other books hold fewer than `MIN_TRAIN` crops of either.
    """
    own = np.asarray(own, dtype=bool)
    books = np.asarray(books)
    x = np.asarray(vectors, dtype=np.float32)
    sums = {True: x[own].sum(0), False: x[~own].sum(0)}
    totals = {True: int(own.sum()), False: int((~own).sum())}
    out = np.full(len(x), np.nan, dtype=np.float32)
    for book in np.unique(books):
        here = books == book
        means = {}
        for side in (True, False):
            mask = here & (own == side)
            left = totals[side] - int(mask.sum())
            if left < MIN_TRAIN:
                break
            means[side] = (sums[side] - x[mask].sum(0)) / left
        else:
            first, second = means[True], means[False]
            along = x[here] @ (second - first) - (first + second) @ (second - first) / 2
            # Towards the second character is positive for a crop of the first, and the reverse.
            out[here] = np.where(own[here], along, -along)
    return out


def _books(ids: Sequence[str], mirror: Path, atlas: Path) -> tuple[list[int | None], dict[int, str]]:
    """Each crop's book and the character the site files it under now, by row."""
    with sqlite3.connect(f"file:{mirror}?mode=ro", uri=True) as db:
        known = dict(db.execute("SELECT text, id FROM ids"))
    row = {known[identity]: n for n, identity in enumerate(ids) if identity in known}
    books: list[int | None] = [None] * len(ids)
    labels: dict[int, str] = {}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        for crop, book, text in db.execute(
                "SELECT c.id, c.book, g.text FROM crops c LEFT JOIN graphemes g ON g.id = c.char"):
            n = row.get(crop)
            if n is not None:
                books[n] = book
                if text:
                    labels[n] = unicodedata.normalize("NFC", text)
    return books, labels


def run(similar: Path, *, mirror: Path, atlas: Path) -> dict:
    """Score every crop of every variant pair the index holds and write the scores beside it."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ids = pq.read_table(similar / "units.parquet", columns=["id"]).column("id").to_pylist()
    books, current = _books(ids, mirror, atlas)
    rows: dict[str, list[int]] = defaultdict(list)
    for n, label in current.items():
        if books[n] is not None:
            rows[label].append(n)
    vectors = np.load(similar / "vectors.npy", mmap_mode="r")
    out: dict[str, list] = {"id": [], "label": [], "other": [], "book": [], "score": []}
    report = []
    stated = pairs({label: len(found) for label, found in rows.items()})
    for first, second in sorted(stated):
        members = np.array(sorted(rows[first] + rows[second]))
        own = np.isin(members, rows[first])
        held = np.array([books[n] for n in members])
        found = scores(vectors[members], own, held)
        scored = ~np.isnan(found)
        for n, mine, book, score in zip(members[scored], own[scored], held[scored], found[scored], strict=True):
            out["id"].append(ids[n])
            out["label"].append(first if mine else second)
            out["other"].append(second if mine else first)
            out["book"].append(int(book))
            out["score"].append(float(score))
        entry = {"pair": [first, second], "sources": list(stated[first, second]),
                 "crops": [int(own.sum()), int((~own).sum())]}
        for label, side in ((first, own), (second, ~own)):
            leaning = Counter()
            for book in np.unique(held[side & scored]):
                mask = side & scored & (held == book)
                if mask.sum() >= MIN_BOOK:
                    leaning[int(book)] = float(found[mask].mean())
            entry[label] = {"scored": int((side & scored).sum()),
                            "highest": round(float(found[side & scored].max()), 4) if (side & scored).any() else None,
                            "books": {str(book): round(mean, 4) for book, mean in leaning.most_common()}}
        report.append(entry)
    pq.write_table(pa.table(out, schema=pa.schema([("id", pa.string()), ("label", pa.string()), ("other", pa.string()),
                                                    ("book", pa.int64()), ("score", pa.float32())])),
                   similar / FILE, compression="zstd")
    return {"method": METHOD, "index": len(ids), "held": len(current), "pairs": len(report),
            "in_pairs": len({n for first, second in stated for n in rows[first] + rows[second]}),
            "scores": len(out["id"]), "scored": len(set(out["id"])), "items": report}
