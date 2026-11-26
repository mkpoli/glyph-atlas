"""Add the curated ligature occurrences to a public corpus publication."""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from io import BytesIO
from pathlib import Path

import httpx
from catalogue_schema import schema
from export_cloudflare import encoded
from export_cloudflare_corpus import FrozenResolver
from PIL import Image

from glyph_atlas import style
from glyph_atlas.corpus.api import CorpusAPI

MEDIA_PACK = "anchors-images.bin"
RECORD_PACK = "anchors-records.bin"


def export(output: Path, *, api: CorpusAPI | None = None, fetch=httpx.get) -> dict[str, int]:
    """Export proxyable curated anchors into `output / corpus.sqlite`, replacing any prior anchor rows."""
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output / "corpus.sqlite", timeout=60)
    schema(db)
    api = api or CorpusAPI("work", "work/corpus-index", autobuild=False)
    resolver = FrozenResolver(api)
    details, images, skipped = [], [], 0
    for entry in api.index.glyphs.entries.values():
        detail = resolver._from_anchor(entry)
        if not detail.get("proxyable") or not detail.get("image"):
            skipped += 1
            continue
        detail.update(origin="corpus", revision=0, state="pending", suggestions=[], located=True, grid_safe=True)
        for field in ("image", "context_image"):
            if not detail.get(field):
                continue
            response = fetch(detail[field], follow_redirects=True, timeout=60)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            result = BytesIO()
            image.save(result, "WEBP", quality=88)
            raw = result.getvalue()
            key = hashlib.sha256(raw).hexdigest()
            images.append((key, raw))
            detail[field] = f"/atlas/media/{key}.webp"
        detail["image_is_served_by_this_api"] = True
        details.append(detail)
    # Remove any rows from an earlier anchor run before rewriting the packs so skipped anchors leave no stale offsets.
    db.execute("DELETE FROM media WHERE object=?", (MEDIA_PACK,))
    db.execute("DELETE FROM corpus_units WHERE object=?", (RECORD_PACK,))
    with (output / MEDIA_PACK).open("wb") as media, (output / RECORD_PACK).open("wb") as records:
        for key, raw in images:
            offset = media.tell()
            media.write(raw)
            db.execute("INSERT OR REPLACE INTO media VALUES(?,?,?,?,?)", (key, MEDIA_PACK, offset, len(raw), "image/webp"))
        for detail in details:
            raw = encoded(detail).encode()
            offset = records.tell()
            records.write(raw)
            # An anchor states no style of its own or of its page; its document's confirmed style applies.
            document = (detail.get("source") or {}).get("document_id")
            db.execute(
                "INSERT OR REPLACE INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,style,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    detail["id"], detail["written_character"], detail["grapheme"], None,
                    int(hashlib.sha256(detail["id"].encode()).hexdigest()[:7], 16), RECORD_PACK, offset, len(raw),
                    detail.get("production") or "unknown", style.resolve_values(None, None, document, None)[0], document,
                ),
            )
    db.commit()
    db.close()
    return {"anchors": len(details), "hosted_images": len(images), "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(encoded(export(args.output)))
