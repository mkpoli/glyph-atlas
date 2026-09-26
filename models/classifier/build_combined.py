"""Write the training manifests: CODH's crops with the HI Lab crops added, and the class list.

`build_manifests.py` cuts the CODH crops first; its manifests are only read here. The output is
`work/classifier-combined/{train,val,test}.parquet`, in the same columns, and the served class list
`models/classifier/classes.json`.

HI Lab crops are split by `models/benchmark/bench.py`, whose `hilab-test` set is the held-out
tenth; only the rest is trained on. CODH's val and test books are kept as they are; HI Lab adds its
held-out tenth to test only.

A class is a code point with at least `--min-crops` training crops across both sources; every other
crop is `other`, as in the baseline.

    python models/classifier/build_combined.py --min-crops 5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
CODH = ROOT / "work/classifier"
sys.path.insert(0, str(ROOT / "models/benchmark"))
from bench import hilab_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-crops", type=int, default=5)
    parser.add_argument("--out", type=Path, default=ROOT / "work/classifier-combined")
    parser.add_argument("--classes", type=Path, default=ROOT / "models/classifier/classes.json")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    codh = {name: pq.read_table(CODH / f"{name}.parquet") for name in ("train", "val", "test")}
    schema = codh["train"].schema
    extra = hilab_rows()
    splits = {"train": codh["train"].to_pylist() + [r for r in extra if r["split"] == "train"],
              "val": codh["val"].to_pylist(),
              "test": codh["test"].to_pylist() + [r for r in extra if r["split"] == "test"]}
    counts = Counter(r["code_point"] for r in splits["train"])
    classes = [cp for cp, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if n >= args.min_crops]
    known = set(classes)
    for name, rows in splits.items():
        for row in rows:
            row["label"] = row["code_point"] if row["code_point"] in known else "other"
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), args.out / f"{name}.parquet")
    args.classes.write_text(json.dumps({"classes": [*classes, "other"]}, indent=1) + "\n")
    print(json.dumps({"hilab": len(extra), **{n: len(r) for n, r in splits.items()}, "classes": len(classes)}))


if __name__ == "__main__":
    main()
