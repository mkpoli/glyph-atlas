"""Write and read the dataset tables as Parquet."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel


def write(path: Path, records: Iterable[BaseModel]) -> int:
    rows = [r.model_dump(mode="json") for r in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
    return len(rows)


def read(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()
