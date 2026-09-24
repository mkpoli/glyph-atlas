"""Precompute image-only OCR suggestions for the self-contained review publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from PIL import Image

from glyph_atlas.review.suggestions import Recognizer


def prepare(directory: Path):
    db = sqlite3.connect(directory / "catalogue.sqlite", timeout=60)
    model = Recognizer()
    if not model.engines or any(engine["provider"] != "CUDAExecutionProvider" for engine in model.engines):
        raise RuntimeError("The publication inference job requires the configured CUDA models")
    signature = hashlib.sha256(json.dumps(model.engines, sort_keys=True).encode()).hexdigest()[:16]
    cache = Path("cache/cloudflare-suggestions") / signature
    cache.mkdir(parents=True, exist_ok=True)
    rows = db.execute("SELECT id,data FROM units WHERE origin='local'").fetchall()
    for index, (identity, raw) in enumerate(rows):
        item = json.loads(raw)
        image_key = item["image"].rsplit("/", 1)[-1].removesuffix(".webp")
        saved = cache / (image_key + ".json")
        if saved.exists():
            result = json.loads(saved.read_text())
        else:
            path = Path("cache/display-crops") / image_key[:2] / (image_key + ".webp")
            with Image.open(path) as image:
                result = model.read(image.convert("RGB"))
            result["input_image"] = item["image"]
            result["verified"] = False
            saved.write_text(json.dumps(result, ensure_ascii=False))
        db.execute("UPDATE units SET visual=? WHERE id=?", (json.dumps(result, ensure_ascii=False), identity))
        if index % 50 == 0:
            db.commit()
            (directory / "suggestions-progress.json").write_text(json.dumps({"done": index, "total": len(rows), "provider": "CUDAExecutionProvider"}))
    db.commit()
    (directory / "suggestions-progress.json").write_text(json.dumps({"done": len(rows), "total": len(rows), "complete": True}))
    print(json.dumps({"suggestions": len(rows), "engines": model.engines}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare(args.directory)
