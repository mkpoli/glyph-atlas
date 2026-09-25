"""Write D1 SQL that replaces `document_characters` for the documents ainu-records' character pages show.

The input is a dataset `atlas ainu merge` wrote. Every active unit with a box on those documents whose
label the atlas stands behind is listed in source order: page, then the transcribed lines in order, then the OCR blocks, then position.
A unit the atlas aligned is placed by its own line; one imported from ainu-records keeps the block and
context its character page gave it. The label is the character the unit is, and `source` says where
it came from. A unit withheld by the alignment repair, rejected by the aligner or flagged is left out
until a review settles it. Run the output with `wrangler d1 execute glyph-atlas --remote --file <out>` after
migration 0007 is applied; it replaces only the documents it lists.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from glyph_atlas import tables
from glyph_atlas.ainu_characters import DECIDED, META, trusted, written
from glyph_atlas.schema import Line, Unit

BATCH = 200


def documents_of(units: list[Unit]) -> set[str]:
    """The documents ainu-records' character pages cover: those holding any unit it had a say on."""
    return {u.document_id for u in units if u.document_id and META in u.meta}


def index(identity: str | None, marker: str) -> int | None:
    found = re.search(rf":{marker}(\d+)$", identity or "")
    return int(found.group(1)) if found else None


def row(unit: Unit, lines: dict[str, Line]) -> dict[str, Any]:
    known = unit.meta.get(META) or {}
    box = [round(unit.box.x), round(unit.box.y), round(unit.box.w), round(unit.box.h)]
    label = written(unit)
    if unit.id.startswith("ar:"):
        return {"sample": known["id"], "page": known["page"], "line": known.get("line"), "block": known.get("block"),
                "position": known.get("position"), "context": known.get("context") or "", "source": known["origin"],
                "label": label, "box": box}
    page = int(unit.page_id.rsplit(":", 1)[1]) + 1
    line_index = index(unit.line_id, "L")
    line = lines.get(unit.line_id or "")
    return {"sample": known.get("id"), "page": page, "line": None if line_index is None else line_index + 1,
            "block": None if line_index is None else f"l{line_index + 1}", "position": unit.seq,
            "context": line.text if line else "", "source": "review" if unit.review in DECIDED else "transcription",
            "label": label, "box": box}


def order(data: dict[str, Any], unit_id: str) -> tuple:
    """Source order as ainu-records' pages give it: lines first, then OCR blocks, each by position."""
    block = data["block"] or ""
    kind, number = (0, int(block[1:])) if re.fullmatch(r"l\d+", block) else \
        (1, int(block[3:])) if re.fullmatch(r"ocr\d+", block) else (2, 0)
    return data["page"], kind, number, block, data["position"] if data["position"] is not None else 1 << 30, unit_id


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def export(dataset: Path, out: Path, documents: set[str] | None = None) -> dict[str, int]:
    units = [u for u in tables.read(dataset / "units.parquet", Unit) if u.active and u.box and u.page_id]
    documents = documents or documents_of(units)
    lines = {line.id: line for line in tables.read(dataset / "lines.parquet", Line)}
    rows: dict[str, list[tuple[tuple, str, dict]]] = {d: [] for d in sorted(documents)}
    for unit in units:
        if unit.document_id in rows and trusted(unit):
            data = row(unit, lines)
            rows[unit.document_id].append((order(data, unit.id), unit.id, data))
    statements = [f"DELETE FROM document_characters WHERE document IN ({','.join(map(quoted, rows))});"]
    counts: Counter = Counter()
    values = []
    for document, listed in rows.items():
        for ord_, (_, unit_id, data) in enumerate(sorted(listed, key=lambda r: r[0])):
            values.append(f"({quoted(document)},{ord_},{quoted(unit_id)},"
                          f"{quoted(json.dumps(data, ensure_ascii=False, separators=(',', ':')))})")
            counts[document] += 1
    for start in range(0, len(values), BATCH):
        statements.append("INSERT INTO document_characters VALUES" + ",".join(values[start:start + BATCH]) + ";")
    out.write_text("\n".join(statements) + "\n", encoding="utf-8")
    return dict(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="dataset `atlas ainu merge` wrote")
    parser.add_argument("out", type=Path)
    parser.add_argument("--documents", help="comma-separated document ids instead of those ainu-records covers")
    args = parser.parse_args()
    counts = export(args.dataset, args.out, set(args.documents.split(",")) if args.documents else None)
    print(json.dumps({"documents": counts, "rows": sum(counts.values())}, ensure_ascii=False))
