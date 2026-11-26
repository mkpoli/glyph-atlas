"""Crops a reviewer need not be asked about: agreed on their character and on their written form.

`neighbour_agreement` passes a crop whose nearest crops all carry its label, and confirms the 字種
only: a な written 𛂂 sits among crops labelled な. This module adds the written form. A crop's
`same` is how many of its `K` nearest crops of its own 字種, from other books, carry exactly its
label. Crops of its book are left out because a book-wide relabel gives them all one label, right
or wrong, and twins (`TWIN`) because one image is filed under two characters in places.

A crop is `agreed` when its streak is full and all `K` carry its label. A crop whose `K` all carry
one other label of its 字種 is `flagged` with that label. Any other crop is left to a reviewer.

`run` writes `<similar>/agreed-marks.json`: `agreed`, the pending collected crops that pass, each
with the label it was judged under, and `flagged`, each with its label and the form its neighbours
carry. It also tests the form rule on the crops reviewers judged, each under the label the index was
built with (`measured`).
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .neighbour_agreement import PAGE_BITS, streaks
from .neighbour_agreement import K as STREAK

METHOD = "agreed-marks-v1"
FILE = "agreed-marks.json"
#: Nearest crops of the 字種 a crop's written form is judged by.
K = 10
#: Two crops at least this similar are one image filed twice, and neither judges the other.
TWIN = 0.999
COLLECTED, PENDING, CONFIRMED, CORRECTED = 0, 0, 1, 3


def forms(labels: Sequence[str | None], families: Sequence[str | None], books: Sequence[int | None],
          vectors: np.ndarray, *, k: int = K, rows: Sequence[int] | None = None,
          asked: dict[int, str] | None = None, block: int = 1024) -> dict[int, tuple[int, str | None]]:
    """For each of `rows` (every crop by default): how many of its `k` nearest crops of its 字種 in
    other books carry its label, and the one other label all `k` carry, if they do. A row in `asked`
    is judged under that label; its neighbours carry their own. A crop with fewer than `k` such
    neighbours counts none."""
    asked = asked or {}
    wanted = set(range(len(labels)) if rows is None else rows)
    members: dict[str, list[int]] = defaultdict(list)
    for n, family in enumerate(families):
        if family and labels[n]:
            members[family].append(n)
    found: dict[int, tuple[int, str | None]] = {n: (0, None) for n in wanted}
    for family_rows in members.values():
        judged = [i for i, n in enumerate(family_rows) if n in wanted]
        if not judged or len(family_rows) <= k:
            continue
        names = sorted({labels[n] for n in family_rows})
        code = np.array([names.index(labels[n]) for n in family_rows])
        book = np.array([-1 if books[n] is None else books[n] for n in family_rows])
        gallery = np.asarray(vectors[family_rows], dtype=np.float32)
        gallery /= np.linalg.norm(gallery, axis=1, keepdims=True) + 1e-9
        for start in range(0, len(judged), block):
            part = np.array(judged[start:start + block])
            scores = gallery[part] @ gallery.T
            scores[scores > TWIN] = -1
            scores[np.arange(len(part)), part] = -1
            scores[(book[part][:, None] == book[None, :]) & (book[part][:, None] >= 0)] = -1
            top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
            enough = np.take_along_axis(scores, top, axis=1).min(axis=1) > -1
            for offset, i in enumerate(part):
                n = family_rows[i]
                if not enough[offset]:
                    continue
                counts = np.bincount(code[top[offset]], minlength=len(names))
                label = asked.get(n, labels[n])
                same = int(counts[names.index(label)]) if label in names else 0
                lead = int(counts.argmax())
                found[n] = (same, names[lead] if counts[lead] == k and names[lead] != label else None)
    return found


def numbers(ids: Sequence[str], mirror: Path, chunk: int = 900) -> list[int | None]:
    """The v3 crop number of each source id, asked of the mirror a chunk at a time: its whole id
    table does not fit in memory beside the index."""
    known: dict[str, int] = {}
    with sqlite3.connect(f"file:{mirror}?mode=ro", uri=True) as db:
        for start in range(0, len(ids), chunk):
            part = ids[start:start + chunk]
            known.update(db.execute(f"SELECT text, id FROM ids WHERE text IN ({','.join('?' * len(part))})", part))
    return [known.get(identity) for identity in ids]


def facts(crops: Sequence[int | None], atlas: Path, chunk: int = 900) -> dict[int, tuple[str, str, int, int, int]]:
    """Each crop's label, 字種 head, book, kind and state by row, from the local copy of the site's database."""
    row = {number: n for n, number in enumerate(crops) if number is not None}
    held, found = sorted(row), {}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        for start in range(0, len(held), chunk):
            part = held[start:start + chunk]
            for crop, text, head, book, kind, state in db.execute(
                    f"""SELECT c.id, g.text, coalesce(h.text, g.text), c.book, c.kind, c.state FROM crops c
                        JOIN graphemes g ON g.id = c.char LEFT JOIN graphemes h ON h.id = g.head
                        WHERE c.id IN ({','.join('?' * len(part))})""", part):
                found[row[crop]] = (unicodedata.normalize("NFC", text), unicodedata.normalize("NFC", head), book, kind, state)
    return found


def measure(was: Sequence[str | None], known: dict[int, tuple[str, str, int, int, int]], labels, families, books,
            vectors: np.ndarray, *, k: int = K) -> dict:
    """The rule on the collected crops reviewers confirmed or corrected, each judged under the label the
    index was built with: `kept` where the reviewer left that label, `form` where the crop is now
    filed under another label of the 字種. `named` counts flags that name the reviewer's label."""
    judged = {n: was[n] for n, (_, _, _, kind, state) in known.items()
              if kind == COLLECTED and state in (CONFIRMED, CORRECTED) and was[n]}
    found = forms(labels, families, books, vectors, k=k, rows=sorted(judged), asked=judged)
    result = {kind: Counter() for kind in ("kept", "form")}
    heads = {label: head for label, head, *_ in known.values()}
    for n, old in judged.items():
        label, head = known[n][0], known[n][1]
        if old != label and heads.get(old) != head:
            continue  # filed under another 字種 now: the streak's business
        kind = "kept" if old == label else "form"
        same, other = found[n]
        verdict = "agreed" if same == k else "flagged" if other else "left"
        result[kind][verdict] += 1
        result[kind]["named"] += int(other == label and kind == "form")
    return {kind: dict(tally) for kind, tally in result.items()}


def run(similar: Path, *, mirror: Path, atlas: Path, k: int = K, streak: int = STREAK) -> dict:
    """Write the marks beside the index and return the counts and the measurement."""
    import pyarrow.parquet as pq

    table = pq.read_table(similar / "units.parquet", columns=["id", "label"]).to_pydict()
    ids = table["id"]
    was = [unicodedata.normalize("NFC", label) if label else None for label in table["label"]]
    crops = numbers(ids, mirror)
    known = facts(crops, atlas)
    labels = [known[n][0] if n in known else None for n in range(len(ids))]
    families = [known[n][1] if n in known else None for n in range(len(ids))]
    books = [known[n][2] if n in known else None for n in range(len(ids))]
    vectors = np.load(similar / "vectors.npy", mmap_mode="r")
    pending = [n for n, (_, _, _, kind, state) in known.items() if kind == COLLECTED and state == PENDING]
    found = forms(labels, families, books, vectors, k=k, rows=pending)
    pages = [number >> PAGE_BITS if number is not None else None for number in crops]
    full = {n for n, count in zip(pending, streaks(labels, pages, vectors, k=streak, rows=pending)[0].tolist(), strict=True)
            if count >= streak}
    agreed = {ids[n]: labels[n] for n in pending if n in full and found[n][0] == k}
    flagged = {ids[n]: {"label": labels[n], "form": found[n][1]} for n in pending if found[n][1]}
    result = {"method": METHOD, "k": k, "streak": streak,
              "counts": {"pending": len(pending), "full_streak": len(full),
                         "agreed": len(agreed), "flagged": len(flagged)},
              "measured": measure(was, known, labels, families, books, vectors, k=k)}
    (similar / FILE).write_text(json.dumps({**result, "agreed": agreed, "flagged": flagged}, ensure_ascii=False) + "\n")
    return result
