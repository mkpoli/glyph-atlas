"""Repack published corpus glyphs whose images still point at external URLs into R2 media packs.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/pack_corpus_images.py <folder> --books hng: --list
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/pack_corpus_images.py <folder> --books hng: --packs <dir>
    rclone copy <folder>/packs r2:glyph-atlas/packs --immutable
    python3 scripts/atlas3/publish.py <folder>/records.sqlite --dry-run
    python3 scripts/atlas3/publish.py <folder>/records.sqlite

Each live corpus glyph whose published record points at an external `http(s)://` image or context_image URL
has its crop cut from the local cache (`cache/images` or the corpus's standalone crop clone), encoded as
WebP, and packed into content-addressed packs under `<folder>/packs/` alongside the rewritten record packs.
The resulting `records.sqlite` carries both `media` and `record_places` so `publish.py` uploads the new
image shards before moving page bundles to the rewritten records, preserving crop ids, reviews and
`source_revision`.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pyarrow.dataset as ds
from PIL import Image

from glyph_atlas import images, tone
from glyph_atlas.corpus.crops import CropResolver
from glyph_atlas.review.atlas import crop_bounds
from glyph_atlas.review.media import MediaCache

OUT = Path(os.environ.get("ATLAS3_DIR", Path(__file__).resolve().parents[2] / "work/atlas-v3")) / "out"
PACK = 32 * 1024**2
EDGE = 480


def is_external(url: object) -> bool:
    """Whether `url` is an external HTTP(S) address."""
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def placed(books: Sequence[str] | None = None, *, out: Path | None = None) -> list[tuple[str, list]]:
    """Each live published corpus glyph of the books matching `books` prefixes (or all corpus books)."""
    root = out or OUT
    atlas = sqlite3.connect(f"file:{root / 'atlas.sqlite'}?mode=ro", uri=True)
    prefixes = tuple(books or ())
    if prefixes:
        clauses = " OR ".join("substr(b.key, 1, ?) = ?" for _ in prefixes)
        params: list[object] = []
        for prefix in prefixes:
            params.extend((len(prefix), prefix))
        rows = atlas.execute(
            f"SELECT p.id, p.rev FROM pages p JOIN books b ON b.id = p.book "
            f"WHERE p.rev > 0 AND p.key LIKE 'corpus:%' AND ({clauses}) ORDER BY p.id",
            params,
        )
    else:
        rows = atlas.execute("SELECT p.id, p.rev FROM pages p WHERE p.rev > 0 AND p.key LIKE 'corpus:%' ORDER BY p.id")
    found = []
    for pid, rev in rows:
        bundle = json.loads(gzip.decompress((root / "pages" / f"{pid}-{rev}.json.gz").read_bytes()))
        found += [(e["id"], e["record_place"]) for e in bundle["crops"] if e.get("record_place") and not e.get("retired")]
    return found


class AddressedPacks:
    """Write immutable bytes into content-addressed `packs/<sha256>.bin` files."""

    def __init__(self, output: Path, limit: int = PACK):
        self.output = output
        self.limit = limit
        (output / "packs").mkdir(parents=True, exist_ok=True)
        self._buf = bytearray()
        self._pending: list[tuple[object, int, int]] = []
        self.rows: list[tuple[object, str, int, int]] = []

    def add(self, tag: object, raw: bytes) -> None:
        if self._buf and len(self._buf) + len(raw) > self.limit:
            self.flush()
        self._pending.append((tag, len(self._buf), len(raw)))
        self._buf.extend(raw)

    def flush(self) -> None:
        if not self._buf:
            return
        name = f"packs/{hashlib.sha256(self._buf).hexdigest()}.bin"
        (self.output / name).write_bytes(self._buf)
        self.rows.extend((tag, name, offset, size) for tag, offset, size in self._pending)
        self._buf.clear()
        self._pending.clear()


class PageMeta:
    """Registered page image URL and pixel dimensions per corpus, read once from `pages*.parquet`."""

    def __init__(self, corpus_root: Path):
        self.corpus_root = corpus_root
        self._by_corpus: dict[str, dict[str, dict]] = {}

    def get(self, corpus_name: str | None, page_id: str | None) -> dict | None:
        if not corpus_name or not page_id:
            return None
        if corpus_name not in self._by_corpus:
            directory = self.corpus_root / corpus_name
            files = sorted(directory.glob("pages*.parquet")) + sorted(directory.glob("pages/*.parquet"))
            table: dict[str, dict] = {}
            if files:
                dataset = ds.dataset([str(p) for p in files], format="parquet")
                cols = [c for c in ("id", "image", "width", "height") if c in dataset.schema.names]
                for row in dataset.to_table(columns=cols).to_pylist():
                    if row.get("id"):
                        table[row["id"]] = row
            self._by_corpus[corpus_name] = table
        return self._by_corpus[corpus_name].get(page_id)


def _source_page_url(record: dict, page_meta: dict | None) -> str | None:
    """The registered page URL (preserving scaled renditions), else the IIIF service or image URL."""
    if page_meta and page_meta.get("image"):
        return str(page_meta["image"])
    source = record.get("source") or {}
    if service := source.get("image_service"):
        return str(service)
    img = record.get("image")
    if is_external(img):
        return images.service_of(img) or str(img)
    return None


def repack_record(
    record: dict,
    *,
    crops: CropResolver,
    media: MediaCache,
    media_packs: AddressedPacks,
    packed_keys: set[str],
    page_cache: dict[tuple[str | None, str], Path | None],
    size_cache: dict[Path, tuple[int, int]],
    page_meta: PageMeta | None = None,
) -> tuple[dict | None, str]:
    """Return `(rewritten_record, status)` for one published corpus record.

    Status is one of:
    - `'rewritten'`: record updated to hosted `/atlas/media/` paths (new media added to `media_packs`).
    - `'hosted'`: image and context_image are already hosted under `/atlas/media/`.
    - `'non_proxyable'`: `proxyable` is false; skipped.
    - `'uncached:<url>'`: source image is absent locally; skipped.
    - `'mismatch:<url>'`: cached image pixel dimensions differ from the page table; skipped.
    """
    if record.get("proxyable") is False:
        return None, "non_proxyable"
    ext_image = is_external(record.get("image"))
    ext_context = is_external(record.get("context_image"))
    if not ext_image and not ext_context:
        return None, "hosted"

    new = dict(record)
    box = record.get("box")
    source = record.get("source") or {}
    corpus_name = source.get("corpus")

    def pack_key(url: str) -> Path:
        key = url.rsplit("/", 1)[-1].removesuffix(".webp")
        rendered = media.materialize(key)
        if key not in packed_keys:
            media_packs.add(key, rendered.read_bytes())
            packed_keys.add(key)
        return rendered

    if not box:
        # Standalone crop (e.g. HNG basic dataset): resolve the crop file directly from its local clone.
        ref = record.get("image") if ext_image else record.get("source_crop")
        path = crops.archive_member_path(ref) if isinstance(ref, str) else None
        if path is None:
            return None, f"uncached:{ref or record.get('id')}"
        image_url = media.local(path, edge=EDGE)
        rendered = pack_key(image_url)
        new["image"] = image_url
        new["image_is_served_by_this_api"] = True
        new["image_unavailable_reason"] = None
        new.update(tone.crop_tone(rendered))
        return new, "rewritten"

    # Boxed crop on a page scan: prefer the page table's URL and verify cached dimensions before cutting.
    meta = page_meta.get(corpus_name, source.get("page_id")) if page_meta else None
    page_url = _source_page_url(record, meta)
    if not page_url:
        return None, f"uncached:{record.get('image') or record.get('id')}"
    cache_key = (corpus_name, page_url)
    if cache_key not in page_cache:
        page_cache[cache_key] = crops.page_image_path(page_url, corpus_name)
    page_path = page_cache[cache_key]
    if page_path is None:
        return None, f"uncached:{page_url}"

    if page_path not in size_cache:
        with Image.open(page_path) as picture:
            size_cache[page_path] = picture.size
    width, height = size_cache[page_path]
    if meta and meta.get("width") and meta.get("height") and (width, height) != (meta["width"], meta["height"]):
        return None, f"mismatch:{page_url}"

    coords = [box["x"], box["y"], box["w"], box["h"]]
    if ext_image:
        image_url = media.local(page_path, coords, edge=EDGE)
        rendered = pack_key(image_url)
        new["image"] = image_url
        new["image_is_served_by_this_api"] = True
        new["image_unavailable_reason"] = None
        new.update(tone.crop_tone(rendered))

    context_url = media.local(page_path, coords, context=True)
    pack_key(context_url)
    l, t, r, b = crop_bounds(SimpleNamespace(width=width, height=height), coords, context=True)
    new["context_image"] = context_url
    new["context_box"] = {"x": l, "y": t, "w": r - l, "h": b - t}
    new["context_basis"] = "derived_viewport"

    return new, "rewritten"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--books", action="append", default=[],
                        help="book key prefix to scan (repeatable; default: all corpus books)")
    parser.add_argument("--packs", type=Path, help="folder with the packs `--list` named")
    parser.add_argument("--list", action="store_true", help="name the packs the records are in, and write nothing")
    parser.add_argument("--corpus-root", type=Path, default=Path("work"),
                        help="corpus root directory for resolving local files (default: work)")
    args = parser.parse_args(argv)

    found = placed(args.books)
    if args.list:
        for name in sorted({place[0] for _, place in found}):
            print(name.removeprefix("packs/"))
        return
    if args.packs is None:
        parser.error("--packs names the folder with the packs --list names")
    if (args.output / "records.sqlite").exists():
        sys.exit(f"{args.output / 'records.sqlite'} exists; the catalogue is written into a new folder")

    crops = CropResolver(args.corpus_root, file_bases=(args.corpus_root, Path.cwd()))
    render_dir = args.output / ".render"
    media = MediaCache(corpus_root=args.corpus_root, directory=render_dir)
    media_packs = AddressedPacks(args.output)
    record_packs = AddressedPacks(args.output)
    page_meta = PageMeta(args.corpus_root)
    packed_keys: set[str] = set()
    page_cache: dict[tuple[str | None, str], Path | None] = {}
    size_cache: dict[Path, tuple[int, int]] = {}

    counts: Counter[str] = Counter()
    missing_urls: Counter[str] = Counter()
    missing_hosts: Counter[str] = Counter()
    mismatch_urls: Counter[str] = Counter()
    skipped_ids: list[dict[str, str]] = []

    by_pack: dict[str, list] = {}
    for gid, place in found:
        by_pack.setdefault(place[0], []).append((gid, place))

    try:
        for name, entries in sorted(by_pack.items()):
            pack_file = args.packs / name.removeprefix("packs/")
            with pack_file.open("rb") as pack:
                for gid, (held, offset, size) in sorted(entries, key=lambda e: e[1][1]):
                    pack.seek(offset)
                    record = json.loads(pack.read(size))
                    if record["id"] != gid:
                        sys.exit(f"{name} at {offset} holds {record['id']}, not {gid}")
                    new, status = repack_record(
                        record,
                        crops=crops,
                        media=media,
                        media_packs=media_packs,
                        packed_keys=packed_keys,
                        page_cache=page_cache,
                        size_cache=size_cache,
                        page_meta=page_meta,
                    )
                    if status.startswith("uncached:"):
                        counts["uncached"] += 1
                        url = status.removeprefix("uncached:")
                        missing_urls[url] += 1
                        missing_hosts[urlparse(url).netloc or "unknown"] += 1
                        skipped_ids.append({"id": gid, "reason": "uncached", "url": url})
                        continue
                    if status.startswith("mismatch:"):
                        counts["mismatch"] += 1
                        url = status.removeprefix("mismatch:")
                        mismatch_urls[url] += 1
                        skipped_ids.append({"id": gid, "reason": "dimension_mismatch", "url": url})
                        continue
                    counts[status] += 1
                    if new is None:
                        continue
                    raw = json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode()
                    record_packs.add((gid, (held, offset, size)), raw)
    finally:
        shutil.rmtree(render_dir, ignore_errors=True)

    media_packs.flush()
    record_packs.flush()

    db = sqlite3.connect(args.output / "records.sqlite")
    db.execute(
        "CREATE TABLE media(key TEXT PRIMARY KEY, object TEXT NOT NULL, offset INTEGER NOT NULL, "
        "size INTEGER NOT NULL, content_type TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE record_places(id TEXT PRIMARY KEY, object TEXT NOT NULL, offset INTEGER NOT NULL, "
        "size INTEGER NOT NULL, old_object TEXT NOT NULL, old_offset INTEGER NOT NULL, old_size INTEGER NOT NULL)"
    )
    db.executemany(
        "INSERT INTO media VALUES(?, ?, ?, ?, ?)",
        [(key, obj, offset, size, "image/webp") for key, obj, offset, size in media_packs.rows],
    )
    db.executemany(
        "INSERT INTO record_places VALUES(?, ?, ?, ?, ?, ?, ?)",
        [(gid, obj, offset, size, *old) for (gid, old), obj, offset, size in record_packs.rows],
    )
    db.commit()
    db.close()

    if skipped_ids:
        (args.output / "uncached.json").write_text(
            json.dumps(
                {
                    "missing_hosts": dict(missing_hosts.most_common()),
                    "missing_pages": dict(missing_urls.most_common()),
                    "mismatch_pages": dict(mismatch_urls.most_common()),
                    "crops": skipped_ids,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    summary = {
        "scanned": len(found),
        "rewritten": counts["rewritten"],
        "hosted": counts["hosted"],
        "non_proxyable": counts["non_proxyable"],
        "uncached": counts["uncached"],
        "mismatch": counts["mismatch"],
        "media_packed": len(media_packs.rows),
        "packs": len({r[1] for r in media_packs.rows} | {r[1] for r in record_packs.rows}),
        "missing_hosts": dict(missing_hosts.most_common()),
        "missing_pages": len(missing_urls),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
