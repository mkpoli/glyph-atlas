import csv
from collections import Counter
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1] / "data" / "vocab" / "hentaigana.tsv"


def test_table_covers_every_hentaigana_with_its_jibo():
    rows = list(csv.DictReader(TABLE.open(encoding="utf-8"), delimiter="\t"))
    assert len(rows) == 287
    assert all(r["jibo"] and r["readings"] for r in rows)
    by_pair = Counter((r["readings"], r["jibo"]) for r in rows)
    assert sum(1 for n in by_pair.values() if n > 1) == 52
    ka = {r["code_point"] for r in rows if "か" in r["readings"].split("/")}
    assert {"U+1B019", "U+1B01A", "U+1B022"} <= ka and len(ka) == 12
