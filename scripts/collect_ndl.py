"""Collect an explicitly configured NDL book and its located, unverified OCR."""
import argparse
import json
from pathlib import Path

from glyph_atlas import net
from glyph_atlas.importers.iiif_collection import collect
from glyph_atlas.importers.ndl_ocr import import_ocr

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("pid")
args = parser.parse_args()
if not args.pid.isdecimal():
    parser.error("PID must contain only digits")
root = Path("work") / f"ndl-{args.pid}"
config = Path("data/sources") / f"ndl-{args.pid}.yaml"
if not config.is_file():
    parser.error("Add the book's IIIF manifest to its source configuration first")
if not (root / "pages.parquet").is_file():
    collect(config, root)
cached = root / "upstream" / "ocr.json"
if not cached.exists():
    cached.parent.mkdir(exist_ok=True)
    net.download(f"https://lab.ndl.go.jp/dl/api/book/fulltext-json/{args.pid}", cached, expected="json")
print(json.dumps(import_ocr(root, cached.read_bytes(), args.pid), ensure_ascii=False))
