"""Crops whose nearest crops all carry their label: the ones a reviewer can pass at a glance.

The similar-crop index (`glyph_atlas.similar`) holds every published crop as the classifier's
penultimate features. A crop filed under the right character sits among other crops of it. Its
`streak` is how many of its `K` nearest crops, nearest first, agree with its label before the first
that does not (`consensus_relabel.agree`: one character, one kana, variants of one character, a
kana and the kanji it is written with). Crops of the crop's own page are left out, as the detector
and the Ainu records cut one glyph twice; a page is the site's, which for corpus glyphs that come
without a page scan is a run of up to 256 glyphs. A crop with a full streak is `agreed`.

The streak is a bar on the label, and says nothing about a crop with a short one: a rare
character, an unusual hand and a wrong label all break it early. The distance to the nearest crop of
the same label alone was tried twice and does not separate good crops from bad ones
(`quiz_suspects`, and docs/reports/neighbour-agreement.md).

An agreed crop is still one character among its 字体: of the wrong crops with a long streak in the
measurement, every one was a reviewer writing the form on the page (時 as 时, 点 as 點) where the
transcription and its neighbours had the usual one. The streak confirms the 字種 and never the
written form.

`run` writes `<similar>/agreement.parquet`, one row per crop of the index in its order: `id`,
`label`, `streak` and `floor`, the similarity of the last neighbour counted. With the local copy of
the site's database it reads each crop's label from there and measures the bar against the reviews
held there (`reviewed`, `evaluate`).
"""
from __future__ import annotations

import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from .consensus_relabel import agree, nearest

METHOD = "neighbour-agreement-v1"
FILE = "agreement.parquet"
#: Neighbours a crop is judged by; a crop is agreed when all of them carry its label.
K = 20
#: Crops fetched per query before those of the crop's own page are dropped.
FETCH = 96
#: A v3 crop number is its page shifted by this many bits, then its slot on the page.
PAGE_BITS = 24
#: `events.kind` and `events.issue` of the site's database, as its `codes` table numbers them.
MATCH, WRONG, CHARACTER_ISSUE = 0, 1, 0
ISSUES = ("character", "merged", "crop", "blank", "other", "unclear", "reading")


def streak(label: str | None, neighbours: Iterable[str | None]) -> int:
    """How many of `neighbours`, nearest first, agree with `label` before the first that does not."""
    count = 0
    if not label:
        return count
    for other in neighbours:
        if not other or not agree(label, other):
            break
        count += 1
    return count


def streaks(labels: Sequence[str | None], pages: Sequence[int | None], vectors: np.ndarray, *, k: int = K,
            rows: Sequence[int] | None = None, asked: dict[int, str] | None = None,
            block: int = 65536) -> tuple[np.ndarray, np.ndarray]:
    """The streak of each of `rows` (every crop by default) over its `k` nearest crops of other
    pages, and the similarity of the last neighbour counted (0 for a streak of none), in the order
    of `rows`. A row in `asked` is judged under that label in place of its own; its neighbours
    carry their own. A crop with no page is judged against every other crop."""
    rows = range(len(labels)) if rows is None else rows
    asked = asked or {}
    found = np.zeros(len(rows), dtype=np.int16)
    floor = np.zeros(len(rows), dtype=np.float32)
    agreed: dict[tuple[str, str], bool] = {}
    for start in range(0, len(rows), block):
        part = rows[start:start + block]
        indices, scores = nearest(vectors, part, pages, k=k, fetch=FETCH)
        for offset, row in enumerate(part):
            label = asked.get(row, labels[row])
            count = 0
            for index in indices[offset]:
                other = labels[index] if index >= 0 else None
                if not label or not other:
                    break
                pair = (label, other)
                if pair not in agreed:
                    agreed[pair] = agree(label, other)
                if not agreed[pair]:
                    break
                count += 1
            found[start + offset] = count
            floor[start + offset] = scores[offset, count - 1] if count else 0.0
    return found, floor


def numbers(ids: Sequence[str], mirror: Path) -> list[int | None]:
    """The v3 crop number of each source id, from the migration mirror; None for a crop it lacks."""
    with sqlite3.connect(f"file:{mirror}?mode=ro", uri=True) as db:
        known = dict(db.execute("SELECT text, id FROM ids"))
    return [known.get(identity) for identity in ids]


def current(crops: Sequence[int | None], atlas: Path) -> dict[int, str]:
    """The character each crop is filed under now, by row, from the local copy of the site's database."""
    row = {number: n for n, number in enumerate(crops) if number is not None}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        return {row[crop]: unicodedata.normalize("NFC", text)
                for crop, text in db.execute("SELECT c.id, g.text FROM crops c JOIN graphemes g ON g.id = c.char")
                if crop in row}


def reviewed(crops: Sequence[int | None], was: Sequence[str | None], labels: Sequence[str | None],
             atlas: Path) -> dict[int, tuple[str, str]]:
    """Each reviewed crop's last verdict and the label it was given on, by row.

    A confirmation counts for the label the reviewer confirmed. A wrong-character review counts for
    the label the index was built with (`was`), when the reviewer's character disagrees with it: the
    crop is filed under the correction now. Any other wrong review counts for the crop's label.
    """
    row = {number: n for n, number in enumerate(crops) if number is not None}
    found: dict[int, tuple[str, str]] = {}
    with sqlite3.connect(f"file:{atlas}?mode=ro", uri=True) as db:
        events = db.execute("""SELECT e.crop, e.kind, e.issue, g.text FROM events e
            LEFT JOIN graphemes g ON g.id = e.label WHERE e.kind IN (?, ?) ORDER BY e.at, e.id""", (MATCH, WRONG))
        for crop, kind, issue, text in events:
            n = row.get(crop)
            if n is None:
                continue
            found.pop(n, None)
            text = unicodedata.normalize("NFC", text) if text else None
            if kind == MATCH:
                if text:
                    found[n] = ("confirmed", text)
            elif issue == CHARACTER_ISSUE:
                if text and was[n] and not agree(was[n], text):
                    found[n] = ("character", was[n])
            elif labels[n]:
                found[n] = (ISSUES[issue] if issue is not None and issue < len(ISSUES) else "other", labels[n])
    return found


def evaluate(ids: Sequence[str], verdicts: dict[int, tuple[str, str]], found: dict[int, int], *, k: int = K) -> dict:
    """For each least streak, how many confirmed crops and how many of each kind of wrong crop reach
    it, and the wrong crops that are agreed. `found` is each reviewed row's streak under the label
    its review was given on."""
    judged = Counter(kind for kind, _ in verdicts.values())
    bars = []
    for least in sorted({1, 5, 10, 15, k}):
        passed = Counter(kind for n, (kind, _) in verdicts.items() if found[n] >= least)
        bars.append({"streak": least, **{kind: passed.get(kind, 0) for kind in sorted(judged)}})
    passing = [{"id": ids[n], "label": label, "kind": kind, "streak": int(found[n])}
               for n, (kind, label) in sorted(verdicts.items()) if kind != "confirmed" and found[n] >= k]
    return {"judged": dict(judged), "bars": bars, "wrong_and_agreed": passing}


def run(similar: Path, *, mirror: Path, atlas: Path | None = None, k: int = K) -> dict:
    """Write every crop's streak beside the index and return the counts.

    With `atlas`, crops are judged under the character the site files them under now, which a
    relabel or a review may have changed since the index was built, and the bar is measured against
    the reviews held there.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(similar / "units.parquet", columns=["id", "label"]).to_pydict()
    ids = table["id"]
    was = [unicodedata.normalize("NFC", label) if label else None for label in table["label"]]
    crops = numbers(ids, mirror)
    pages = [number >> PAGE_BITS if number is not None else None for number in crops]
    labels, verdicts = list(was), {}
    if atlas is not None:
        for n, label in current(crops, atlas).items():
            labels[n] = label
        verdicts = reviewed(crops, was, labels, atlas)
    vectors = np.load(similar / "vectors.npy", mmap_mode="r")
    found, floor = streaks(labels, pages, vectors, k=k)
    pq.write_table(pa.table({"id": ids, "label": labels, "streak": pa.array(found, pa.int16()),
                             "floor": pa.array(floor, pa.float32())}), similar / FILE, compression="zstd")
    by_source: dict[str, Counter] = {}
    for identity, count in zip(ids, found, strict=True):
        tally = by_source.setdefault(identity.split(":", 1)[0], Counter())
        tally["crops"] += 1
        tally["agreed"] += int(count >= k)
        tally["none"] += int(count == 0)
    result = {"method": METHOD, "k": k, "crops": len(ids), "agreed": int((found >= k).sum()),
              "none": int((found == 0).sum()), "by_source": {name: dict(tally) for name, tally in sorted(by_source.items())}}
    if atlas is not None:
        judged = sorted(verdicts)
        under, _ = streaks(labels, pages, vectors, k=k, rows=judged, asked={n: verdicts[n][1] for n in judged})
        result["measured"] = evaluate(ids, verdicts, dict(zip(judged, under.tolist(), strict=True)), k=k)
    return result
