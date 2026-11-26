"""Crops a reviewer need not be asked about: agreed on their character and on their written form.

`neighbour_agreement` passes a crop whose nearest crops all carry its label, and confirms the 字種
only: a な written 𛂂 sits among crops labelled な. This module adds the written form. A crop's
`same` is how many of its `K` nearest crops of its own 字種, from other books, carry exactly its
label. Crops of its book are left out because a book-wide relabel gives them all one label, right
or wrong, and twins (`TWIN`) because one image is filed under two characters in places.

A crop is `agreed` when its streak is full and all `K` carry its label. A crop whose `K` all carry
one other label of its 字種 is `flagged` with that label. Any other crop is left to a reviewer.

`run` writes `<similar>/agreed-marks.json`: `agreed`, the pending collected crops that pass, each
with the label and the cut it was judged under, and `flagged`, each with its label and the form its
neighbours carry. Only a crop still on its first cut is marked: the index holds that image. It also
tests the form rule on the crops reviewers gave a verdict on (`verdicts`, `measured`).
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .neighbour_agreement import CHARACTER_ISSUE, MATCH, PAGE_BITS, WRONG, streaks
from .neighbour_agreement import K as STREAK

METHOD = "agreed-marks-v1"
FILE = "agreed-marks.json"
#: Nearest crops of the 字種 a crop's written form is judged by.
K = 10
#: Two crops at least this similar are one image filed twice, and neither judges the other.
TWIN = 0.999
COLLECTED, PENDING = 0, 0


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


def facts(crops: Sequence[int | None], atlas: Path, chunk: int = 900) -> dict[int, tuple[str, str, int, int, int, int]]:
    """Each crop's label, 字種 head, book, kind, state and cut by row, from the local copy of the site's
    database. The texts are the database's own: a compatibility ideograph stays apart from its
    unified character, as the site files them apart."""
    row = {number: n for n, number in enumerate(crops) if number is not None}
    held, found = sorted(row), {}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        for start in range(0, len(held), chunk):
            part = held[start:start + chunk]
            for crop, text, head, book, kind, state, cut in db.execute(
                    f"""SELECT c.id, g.text, coalesce(h.text, g.text), c.book, c.kind, c.state, c.cut FROM crops c
                        JOIN graphemes g ON g.id = c.char LEFT JOIN graphemes h ON h.id = g.head
                        WHERE c.id IN ({','.join('?' * len(part))})""", part):
                found[row[crop]] = (text, head, book, kind, state, cut)
    return found


def verdicts(crops: Sequence[int | None], atlas: Path) -> dict[int, tuple[str, str]]:
    """Each collected crop's last verdict by row: `kept` with the character a reviewer confirmed, or
    `changed` with the character a wrong-character report named. A crop only shown in a round has none."""
    row = {number: n for n, number in enumerate(crops) if number is not None}
    found: dict[int, tuple[str, str]] = {}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        for crop, kind, issue, text in db.execute("""SELECT e.crop, e.kind, e.issue, g.text FROM events e
                JOIN crops c ON c.id = e.crop AND c.kind = ? LEFT JOIN graphemes g ON g.id = e.label
                WHERE e.kind IN (?, ?) ORDER BY e.at, e.id""", (COLLECTED, MATCH, WRONG)):
            n = row.get(crop)
            if n is None:
                continue
            found.pop(n, None)
            if text and (kind == MATCH or issue == CHARACTER_ISSUE):
                found[n] = ("kept" if kind == MATCH else "changed", text)
    return found


def measure(was: Sequence[str | None], judged: dict[int, tuple[str, str]], labels, families, books,
            vectors: np.ndarray, *, k: int = K) -> dict:
    """The form rule on the crops reviewers gave a verdict on. `kept`: a confirmed crop, judged under the
    character confirmed. `form`: a crop a reviewer filed under another character of the 字種 than the
    index was built with (`was`), judged under that earlier label; `named` counts the flags that name
    the reviewer's character. A crop filed under another 字種 is the streak's business and is left out."""
    heads = {label: head for label, head in zip(labels, families, strict=True) if label}
    asked, truth = {}, {}
    for n, (kind, text) in judged.items():
        old = was[n]
        if kind == "kept":
            asked[n] = text
        elif old and unicodedata.normalize("NFC", text) != old and old in heads and heads[old] == heads.get(text):
            asked[n], truth[n] = old, text
    found = forms(labels, families, books, vectors, k=k, rows=sorted(asked), asked=asked)
    result = {kind: Counter() for kind in ("kept", "form")}
    for n in asked:
        kind = "form" if n in truth else "kept"
        same, other = found[n]
        result[kind]["agreed" if same == k else "flagged" if other else "left"] += 1
        result[kind]["named"] += int(kind == "form" and other == truth[n])
    return {kind: dict(tally) for kind, tally in result.items()}


def run(similar: Path, *, mirror: Path, atlas: Path, k: int = K, streak: int = STREAK) -> dict:
    """Write the marks beside the index and return the counts and the measurement."""
    import pyarrow.parquet as pq

    table = pq.read_table(similar / "units.parquet", columns=["id", "label"]).to_pydict()
    ids = table["id"]
    was = [unicodedata.normalize("NFC", label) if label else None for label in table["label"]]  # as the index holds them
    crops = numbers(ids, mirror)
    known = facts(crops, atlas)
    labels = [known[n][0] if n in known else None for n in range(len(ids))]
    families = [known[n][1] if n in known else None for n in range(len(ids))]
    books = [known[n][2] if n in known else None for n in range(len(ids))]
    vectors = np.load(similar / "vectors.npy", mmap_mode="r")
    # A crop cut again since its first publication is no longer the image the index holds.
    pending = [n for n, (_, _, _, kind, state, cut) in known.items() if kind == COLLECTED and state == PENDING and cut == 0]
    found = forms(labels, families, books, vectors, k=k, rows=pending)
    pages = [number >> PAGE_BITS if number is not None else None for number in crops]
    full = {n for n, count in zip(pending, streaks(labels, pages, vectors, k=streak, rows=pending)[0].tolist(), strict=True)
            if count >= streak}
    agreed = {ids[n]: {"label": labels[n], "cut": known[n][5]} for n in pending if n in full and found[n][0] == k}
    flagged = {ids[n]: {"label": labels[n], "form": found[n][1]} for n in pending if found[n][1]}
    result = {"method": METHOD, "k": k, "streak": streak,
              "counts": {"pending": len(pending), "full_streak": len(full),
                         "agreed": len(agreed), "flagged": len(flagged)},
              "measured": measure(was, verdicts(crops, atlas), labels, families, books, vectors, k=k)}
    (similar / FILE).write_text(json.dumps({**result, "agreed": agreed, "flagged": flagged}, ensure_ascii=False) + "\n")
    return result
