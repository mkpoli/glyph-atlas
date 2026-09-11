"""Check the koji parser against the plain text stored in Honkoku-Lines.

    uv run python scripts/check_koji.py [--path cache/honkoku-lines/lines.jsonl.gz] [--limit 10000]
                                        [--seed 0] [--target 0.995]

Reads `lines.jsonl.gz`, draws `--limit` rows at random (`--limit 0` reads every row), compares
`koji.parse(text).plain` with the stored `plain_text`, prints the match rate, and writes the
mismatches to `<input directory>/<input name>-koji-mismatches.tsv`. The exit status is 1 when the
rate is below `--target`.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import random
import sys
from pathlib import Path

from glyph_atlas import koji

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "cache" / "honkoku-lines" / "lines.jsonl.gz"
DEFAULT_LIMIT = 10_000
DEFAULT_TARGET = 0.995
COLUMNS = ("image_id", "line_index", "text", "plain_text", "parsed_plain")


def output_path(source: Path) -> Path:
    """The mismatch file beside the input: `lines.jsonl.gz` -> `lines-koji-mismatches.tsv`."""
    name = source.name
    for suffix in (".jsonl.gz", ".jsonl", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return source.parent / f"{name}-koji-mismatches.tsv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="lines.jsonl.gz to read")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="rows to compare; 0 for all")
    parser.add_argument("--seed", type=int, default=0, help="seed of the row sample")
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET, help="match rate to reach")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"input not found: {args.path}", file=sys.stderr)
        return 2

    mismatches: list[dict[str, object]] = []
    reservoir: list[str] = []
    rng = random.Random(args.seed)
    total = compared = matched = 0

    def compare(line: str) -> None:
        nonlocal compared, matched
        row = json.loads(line)
        text = row["text"]
        parsed = koji.parse(text).plain
        compared += 1
        if parsed == row["plain_text"]:
            matched += 1
            return
        mismatches.append(
            {
                "image_id": row["image_id"],
                "line_index": row["line_index"],
                "text": text,
                "plain_text": row["plain_text"],
                "parsed_plain": parsed,
            }
        )

    # Reservoir sampling over the raw lines, so only the sampled rows are parsed as JSON.
    with gzip.open(args.path, "rt", encoding="utf-8") as handle:
        for total, line in enumerate(handle, start=1):
            if args.limit > 0:
                if len(reservoir) < args.limit:
                    reservoir.append(line)
                    continue
                index = rng.randrange(total)
                if index < args.limit:
                    reservoir[index] = line
                continue
            compare(line)
        for line in reservoir:
            compare(line)

    rate = matched / compared if compared else 0.0
    target = output_path(args.path)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(mismatches)
    print(f"read {total} rows from {args.path}")
    print(f"compared {compared} rows (seed {args.seed})")
    print(f"plain == plain_text for {matched} of {compared} = {rate:.4%} (target {args.target:.2%})")
    print(f"wrote {len(mismatches)} mismatches to {target}")
    for row in mismatches[:5]:
        print(f"  {row['image_id']}: {row['text']!r} -> {row['parsed_plain']!r} != {row['plain_text']!r}")
    return 0 if rate >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
