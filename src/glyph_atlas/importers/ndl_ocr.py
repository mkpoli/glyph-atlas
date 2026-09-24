"""Located, unverified OCR segments from the NDL Next Digital Library."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .. import tables
from ..schema import Box, Line, Page


def import_ocr(root: Path, payload: bytes, pid: str) -> dict:
    if not pid.isdecimal():
        raise ValueError("NDL identifier must be numeric")
    source = f"https://lab.ndl.go.jp/dl/api/book/fulltext-json/{pid}"
    revision = hashlib.sha256(payload).hexdigest()
    records = json.loads(payload)["list"]
    dataset = tables.Dataset(root)
    pages = {p.seq: p for p in dataset.read("pages") if p.document_id == f"ndl:{pid}"}
    lines, updated, seen = [], {}, set()
    rejected = 0
    for record in records:
        seq = record["page"]
        if str(record["book"]) != pid or seq not in pages or seq in seen:
            raise ValueError("OCR pages do not match the IIIF book")
        seen.add(seq)
        page = pages[seq]
        segments = json.loads(record["coordjson"] or "[]")
        for order, segment in enumerate(segments):
            text = segment["contenttext"]
            coordinates = [segment[k] for k in ("xmin", "ymin", "xmax", "ymax")]
            if not text.strip() or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in coordinates):
                rejected += 1
                continue
            x, y = map(math.floor, coordinates[:2])
            right, bottom = map(math.ceil, coordinates[2:])
            if x < 0 or y < 0 or right > page.width or bottom > page.height or right <= x or bottom <= y:
                rejected += 1
                continue
            lines.append(Line(
                id=f"{page.id}:ocr:{order}", page_id=page.id, seq=order,
                box=Box(x=x, y=y, w=right-x, h=bottom-y), vertical=bottom-y >= right-x,
                text_raw=text, text=text, match_method="ndl-ocr-coordinates",
                meta={"source": "ndl-ocr", "source_url": source, "source_revision": revision,
                      "source_segment_id": segment["id"], "source_dimensions": [page.width, page.height],
                      "machine": True, "verified": False},
            ))
        updated[page.id] = page.model_copy(update={
            "transcription": {"source": "ndl-ocr", "entry_id": record["id"], "revision": revision},
            "meta": {**page.meta, "ocr_source": source, "ocr_verified": False},
        })
    if not lines:
        raise ValueError("No usable located OCR segments")
    # Validate every record before replacing either table. Source OCR remains separate from crops.
    for name, rows, model in (
        ("lines", lines, Line),
        ("pages", [updated.get(p.id, p) for p in dataset.read("pages")], Page),
    ):
        staged = root / f".{name}.parquet"
        tables.write(staged, rows, model)
        staged.replace(root / f"{name}.parquet")
    manifest_path = root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["lines"] = len(lines)
    manifest["collection"].update(stage="located OCR collected", ocr_pages=len(seen),
                                  ocr_segments=len(lines), transcription_verified=False)
    manifest["ocr"] = {"url": source, "sha256": revision, "rejected_segments": rejected}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    return {"pages": len(seen), "segments": len(lines), "rejected": rejected}
