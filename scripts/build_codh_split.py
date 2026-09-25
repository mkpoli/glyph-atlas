"""Fix the CODH split by book for the detector and the classifier.

The split is by book, never by page, because two pages of one book share a hand, a block and a scan,
so a page split would leak the training set into the test set. Four books go to `test` and two to
`val`; the choice is `sha1(bid)` order within each stratum of production type, so the same books are
chosen on any machine and the decision does not depend on the order the files happen to be read in.

Production comes from `data/sources/codh-books.tsv`, which carries what the NIJL IIIF manifest states
(`刊` or `写`) for the 41 books whose manifest exists. The three books whose manifest does not exist
are a stratum of their own: their production is unknown, and the detector's report has to say which
of the two kinds it found them to be rather than assuming one.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / "data" / "sources" / "codh-books.tsv"
TEST_BOOKS = 4
VAL_BOOKS = 2
# The two kinds the manifest states, and the books it says nothing about.
STRATA = ("printed", "handwritten", "unknown")


def books(path: Path) -> list[dict[str, str]]:
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    header = next(line for line in lines if line.startswith("# bid")).removeprefix("# ").split("\t")
    body = [line for line in lines if not line.startswith("#")]
    return [dict(zip(header, line.split("\t"), strict=True)) for line in body]


def allocate(counts: dict[str, int], slots: int) -> dict[str, int]:
    """Hand out `slots` books over the strata, in proportion to their size.

    Every stratum with books gets one slot before the rest are shared out in proportion, so that the
    kind of book a stratum stands for is measured rather than assumed from the majority.
    """
    # A stratum is only reserved a slot when there are enough to go round: with two slots the
    # proportional share is what keeps the split close to the size of each stratum, and the test
    # group, which is the larger one, is where a small stratum is measured.
    reserve = slots >= 3
    quota = {name: (1 if reserve and counts[name] else 0) for name in counts}
    remaining = slots - sum(quota.values())
    room = {name: counts[name] - quota[name] for name in counts}
    total = sum(room.values())
    if not total or remaining <= 0:
        return quota
    exact = {name: remaining * room[name] / total for name in counts}
    for name, value in exact.items():
        quota[name] += min(room[name], int(value))
    for name, _ in sorted(exact.items(), key=lambda item: (-(item[1] - int(item[1])), item[0])):
        if sum(quota.values()) >= slots:
            break
        if quota[name] < counts[name]:
            quota[name] += 1
    while sum(quota.values()) < slots:
        for name in sorted(counts):
            if quota[name] < counts[name] and sum(quota.values()) < slots:
                quota[name] += 1
    return quota


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "splits" / "codh.tsv")
    parser.add_argument("--books", type=Path, default=BOOKS)
    args = parser.parse_args()

    rows = books(args.books)
    counts = {name: sum(1 for row in rows if row["production"] == name) for name in STRATA}
    test_quota = allocate(counts, TEST_BOOKS)
    remaining = {name: counts[name] - test_quota[name] for name in STRATA}
    val_quota = allocate(remaining, VAL_BOOKS)

    assigned: dict[str, str] = {}
    for name in STRATA:
        pool = sorted(
            (row for row in rows if row["production"] == name),
            key=lambda row: hashlib.sha1(row["bid"].encode()).hexdigest(),
        )
        for row in pool[: test_quota[name]]:
            assigned[row["bid"]] = "test"
        for row in pool[test_quota[name] : test_quota[name] + val_quota[name]]:
            assigned[row["bid"]] = "val"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# CODH split by book, fixed by sha1(bid) order within each production type.\n")
        handle.write(f"# {TEST_BOOKS} books in test and {VAL_BOOKS} in val; the rest train.\n")
        handle.write("# source: data/sources/codh-books.tsv (production from the NIJL IIIF manifest)\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["bid", "title", "production", "split"])
        for row in rows:
            writer.writerow([row["bid"], row["title"], row["production"], assigned.get(row["bid"], "train")])
    for name in STRATA:
        print(f"{name:<11} {counts[name]:>2} books, test {test_quota[name]}, val {val_quota[name]}")
    print(f"{len(rows)} books -> {args.out}")
    for split in ("test", "val"):
        print(f"{split}: " + ", ".join(sorted(bid for bid, value in assigned.items() if value == split)))


if __name__ == "__main__":
    main()
