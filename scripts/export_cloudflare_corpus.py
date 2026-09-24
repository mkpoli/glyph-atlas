"""Stream corpus metadata and locally held glyph images into immutable R2 packs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
from export_cloudflare import Packs, encoded

from glyph_atlas.corpus import sources
from glyph_atlas.corpus.api import PROXYABLE, CorpusAPI
from glyph_atlas.corpus.details import DetailResolver, _iiif_region, _viewport
from glyph_atlas.corpus.index import (
    UNIT_CORPORA,
    _char_of_codepoint,
    _character_unit_row,
    _MetaCache,
    _unit_row,
)
from glyph_atlas.review.media import MediaCache


class FrozenResolver(DetailResolver):
    def _stamp_digest(self):
        if not hasattr(self, "export_stamp"):
            self.export_stamp = super()._stamp_digest()
        return self.export_stamp


def export(output, *, resume=False):
    output.mkdir(parents=True, exist_ok=resume)
    db = sqlite3.connect(output / "corpus.sqlite")
    db.executescript(Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text())
    media = MediaCache(corpus_root=Path("work"))
    api = CorpusAPI("work", "work/corpus-index", autobuild=False)
    resolver = FrozenResolver(api)
    packs = Packs(output, db)
    # Distinct names allow catalogue and corpus exports to be combined safely.
    packs.index = max(packs.index, 10000)
    record_file = None
    pack_index = max([int(p.stem.split("-")[-1]) for p in output.glob("corpus-*.bin")], default=0)
    for name, in db.execute("SELECT DISTINCT object FROM corpus_units"):
        size = (output / name).stat().st_size
        db.execute("DELETE FROM corpus_units WHERE object=? AND offset+size>?", (name, size))
    db.commit()
    existing = {r[0] for r in db.execute("SELECT id FROM corpus_units")}
    counts = Counter(json.loads((output / "progress.json").read_text()) if (output / "progress.json").exists() else {})
    for corpus in sources.discover("work"):
        if corpus.name not in UNIT_CORPORA:
            continue
        paths = corpus.parquet_files("units")
        if not paths:
            continue
        context = _MetaCache(corpus)
        dataset = ds.dataset([str(p) for p in paths], format="parquet")
        columns = [c for c in ("id", "document_id", "page_id", "line_id", "seq", "box", "crop", "crop_sha256",
                   "kind", "granularity", "text_source", "reading", "unicode", "method", "review", "active", "upstream")
                   if c in dataset.schema.names]
        for batch in dataset.scanner(columns=columns, batch_size=2048, use_threads=False).to_batches():
            for row in batch.to_pylist():
                if row["id"] in existing:
                    continue
                if not _character_unit_row(row):
                    continue
                char = _char_of_codepoint(row.get("unicode")) or row.get("text_source")
                if not char:
                    continue
                joined = _unit_row(corpus, context, row, char, row.get("unicode") or "")
                if joined.get("image_licence") not in PROXYABLE:
                    counts["licence-link-only"] += 1
                    continue
                api._decorate_unit(joined, width=480)
                if not joined.get("render_available"):
                    counts["unavailable"] += 1
                    continue
                box = joined.get("box")
                page = context.page(joined.get("page_id") or "")
                context_box = _viewport(box, page.get("width"), page.get("height"))
                image = joined["thumbnail"].get("iiif_url")
                context_image = _iiif_region(joined.get("image_service"), context_box, edge=900)
                if joined["thumbnail"].get("mode") != "remote_iiif":
                    image = media.corpus_image(joined, api.crops, edge=480)
                    if not image:
                        counts["unavailable"] += 1
                        continue
                    key = image.rsplit("/", 1)[-1].removesuffix(".webp")
                    try:
                        packs.add(key, media.materialize(key))
                    except (OSError, ValueError):
                        counts["unavailable"] += 1
                        continue
                    # Local image crops are sufficient for pre-cut datasets; page-backed
                    # sources get a context generated with the same coordinate transform.
                    spec = json.loads((media.directory / key[:2] / (key + ".json")).read_text())
                    if spec.get("box"):
                        path = media.roots[spec["source"]] / spec["path"]
                        context_image = media.local(path, spec["box"], context=True)
                        context_key = context_image.rsplit("/", 1)[-1].removesuffix(".webp")
                        packs.add(context_key, media.materialize(context_key))
                        from PIL import Image

                        from glyph_atlas.review.atlas import crop_bounds
                        with Image.open(path) as picture:
                            l, t, r, b = crop_bounds(picture, spec["box"], context=True)
                        context_box = {"x": l, "y": t, "w": r-l, "h": b-t}
                    else:
                        context_box, context_image = None, None
                source = {"corpus": corpus.name, **{k: joined.get(k) for k in
                    ("document_id", "page_id", "line_id", "title", "holder", "shelfmark", "image_service")},
                    "source_url": joined.get("canvas")}
                detail = resolver._assemble(identity=row["id"], unit_id=row["id"], label=char,
                    source_label=row.get("text_source"), reading=row.get("reading"), code_point=row.get("unicode"),
                    box=box, image=image, image_reason=None, crop_sha256=row.get("crop_sha256"),
                    source_crop=row.get("crop"), proxyable=True, licence=joined.get("image_licence"),
                    source=source, context_box=context_box, context_image=context_image,
                    context_basis="derived_viewport" if context_box else None, crop_box=box,
                    record_url=resolver._record_url(corpus, joined, box),
                    extra={"kind": "char", "method": joined.get("method"), "basis": "upstream_bbox" if box else "upstream_crop",
                        "review": joined.get("review") or "machine", "confirmed_by_human": False,
                        "render_available": True, **{k: joined.get(k) for k in ("production", "production_label", "production_evidence")}})
                detail.update(origin="corpus", state="pending", revision=0, suggestions=[], located=True, grid_safe=True)
                if record_file is None or record_file.tell() >= 32 * 1024**2:
                    if record_file:
                        record_file.close()
                    pack_index += 1
                    record_name = f"corpus-{pack_index:04}.bin"
                    record_file = (output / record_name).open("wb")
                raw = encoded(detail).encode()
                family = detail.get("grapheme")
                visual = detail.get("visual_group") or {}
                db.execute("INSERT OR IGNORE INTO corpus_units VALUES(?,?,?,?,?,?,?,?)", (
                    row["id"], detail.get("written_character"), family, visual.get("id"),
                    int(hashlib.sha256(row["id"].encode()).hexdigest()[:7], 16), record_name, record_file.tell(), len(raw)))
                record_file.write(raw)
                counts[corpus.name] += 1
            if record_file:
                record_file.flush()
            if packs.file:
                packs.file.flush()
            db.commit()
            (output / "progress.json").write_text(encoded(dict(counts)))
        print(encoded({"corpus": corpus.name, "counts": dict(counts)}), flush=True)
    if record_file:
        record_file.close()
    packs.close()
    db.close()
    print(encoded({"complete": True, "counts": dict(counts)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    export(args.output, resume=args.resume)
