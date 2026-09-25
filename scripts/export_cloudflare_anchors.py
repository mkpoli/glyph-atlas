"""Add the curated ligature occurrences to a public corpus publication."""
import argparse
import hashlib
import sqlite3
from io import BytesIO
from pathlib import Path

import httpx
from cloudflare_schema import CORPUS_CHARACTERS, schema
from export_cloudflare import encoded
from export_cloudflare_corpus import FrozenResolver
from PIL import Image

from glyph_atlas.corpus.api import CorpusAPI

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
db = sqlite3.connect(args.output / "corpus.sqlite", timeout=60)
schema(db)
api = CorpusAPI("work", "work/corpus-index", autobuild=False)
resolver = FrozenResolver(api)
details, images = [], []
for entry in api.index.glyphs.entries.values():
    detail = resolver._from_anchor(entry)
    detail.update(origin="corpus", revision=0, state="pending", suggestions=[], located=True, grid_safe=True)
    # Follow the existing corpus policy: rehost permitted images; other records
    # retain holder-served image URLs and their original licence.
    if detail["proxyable"]:
        for field in ("image", "context_image"):
            if not detail.get(field):
                continue
            response = httpx.get(detail[field], follow_redirects=True, timeout=60)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            result = BytesIO()
            image.save(result, "WEBP", quality=88)
            raw = result.getvalue()
            key = hashlib.sha256(raw).hexdigest()
            images.append((key, raw))
            detail[field] = f"/atlas/media/{key}.webp"
    details.append(detail)
with (args.output / "anchors-images.bin").open("wb") as media, (args.output / "anchors-records.bin").open("wb") as records:
    for key, raw in images:
        offset = media.tell()
        media.write(raw)
        db.execute("INSERT OR REPLACE INTO media VALUES(?,?,?,?,?)", (key, "anchors-images.bin", offset, len(raw), "image/webp"))
    for detail in details:
        raw = encoded(detail).encode()
        offset = records.tell()
        records.write(raw)
        db.execute("INSERT OR REPLACE INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?)", (
            detail["id"], detail["written_character"], detail["grapheme"], None,
            int(hashlib.sha256(detail["id"].encode()).hexdigest()[:7], 16), "anchors-records.bin", offset, len(raw),
            detail.get("production") or "unknown"))
db.commit()
db.executescript(CORPUS_CHARACTERS)
print(encoded({"anchors": len(details), "hosted_images": len(images)}))
