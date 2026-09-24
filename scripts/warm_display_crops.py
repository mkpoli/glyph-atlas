"""Prepare local display crops by source page, so browsing only serves small files."""
from __future__ import annotations

import argparse
import json
import shutil
from functools import lru_cache
from pathlib import Path

from glyph_atlas import images
from glyph_atlas.review.atlas import image_size, label, single_character, written_identity
from glyph_atlas.review.media import MediaCache
from glyph_atlas.review.server import cached_image
from glyph_atlas.review.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    store, media = Store(args.dataset), MediaCache()
    by_url = {record.url: record for record in images.index(images.images_root())
              if not record.superseded_by}

    @lru_cache(maxsize=512)
    def page_source(page_id):
        page = store.page(page_id) if page_id else None
        path = cached_image(page.sha256) if page and page.sha256 else None
        if path is None and page:
            record = by_url.get(page.image)
            path = cached_image(record.sha256) if record else None
        return page, path

    units = sorted(store.iter_units(), key=lambda u: (u.page_id or "", u.id))
    count = skipped = 0
    for unit in units:
        if (not unit.active or unit.granularity != "char"
                or not (single_character(label(unit)) or single_character(written_identity(unit)))):
            continue
        page, page_path = page_source(unit.page_id)
        path = cached_image(unit.crop_sha256) if unit.crop_sha256 else None
        size = image_size(str(path), path.stat().st_mtime_ns) if path else None
        is_crop = bool(size)
        if not is_crop:
            path = page_path
        if path is None:
            skipped += 1
            continue
        size = image_size(str(path), path.stat().st_mtime_ns)
        if not size:
            skipped += 1
            continue
        box = None
        if not is_crop:
            if not page or not unit.box:
                continue
            b = unit.box
            sx = size[0] / page.width if page.width and page.height else 1
            sy = size[1] / page.height if page.width and page.height else 1
            box = (b.x*sx, b.y*sy, b.w*sx, b.h*sy)
        if count % 100 == 0 and shutil.disk_usage(images.cache_root()).free < 1024**3:
            raise SystemExit("Stopped: display cache disk has less than 1 GiB free")
        try:
            for context in ([False, True] if box else [False]):
                url = media.local(path, box, context=context)
                media.materialize(url.rsplit("/", 1)[1].removesuffix(".webp"))
        except (OSError, ValueError):
            skipped += 1
            continue
        count += 1
        if count % 500 == 0:
            print(json.dumps({"prepared": count, "skipped": skipped}), flush=True)
        if args.limit and count >= args.limit:
            break
    print(json.dumps({"prepared": count, "skipped": skipped, "done": True}), flush=True)


if __name__ == "__main__":
    main()
