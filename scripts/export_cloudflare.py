"""Publish a consistent, path-free catalogue snapshot for the Cloudflare viewer.

This writes deployment inputs only. It never changes the review dataset or uploads
anything. Review events collected on Cloudflare live in a separate D1 journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cloudflare_schema import schema
from PIL import Image

from glyph_atlas import refs
from glyph_atlas.corpus.api import PROXYABLE, CorpusAPI
from glyph_atlas.review import atlas, characters, collection, corpus_source
from glyph_atlas.review.context_suggestions import context_guesses
from glyph_atlas.review.media import MediaCache
from glyph_atlas.review.request_cache import lookup_scope
from glyph_atlas.review.store import Store


def encoded(value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if re.search(r"/home/[^/\s\"]+", text):
        raise ValueError("A public snapshot contains a local home path")
    return text


class Packs:
    """Immutable image packs let publication upload thousands of crops in a few PUTs."""
    def __init__(self, output, db):
        self.output, self.db = output, db
        self.file = None
        self.index = max([int(p.stem.split("-")[-1]) for p in output.glob("pack-*.bin")], default=0)
        self.seen = {r[0] for r in db.execute("SELECT key FROM media")}

    def add(self, key, path, content_type="image/webp"):
        if key in self.seen:
            return
        self.seen.add(key)
        if self.file is None or self.file.tell() >= 32 * 1024**2:
            if self.file:
                self.file.close()
            self.index += 1
            self.name = f"pack-{self.index:04}.bin"
            self.file = (self.output / self.name).open("wb")
        size = path.stat().st_size
        self.db.execute("INSERT INTO media VALUES(?,?,?,?,?)",
                        (key, self.name, self.file.tell(), size, content_type))
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.file, 1024 * 1024)

    def close(self):
        if self.file:
            self.file.close()


def read_crops(db, media):
    """Store image-only OCR for every crop; a publication never ships a crop unread.

    A crop read by other models than the current ones is read again, so one publication never
    mixes models. Results are cached per image and model signature. Every result, stored or
    cached, is put in the current `rank` order.
    """
    from glyph_atlas.review.suggestions import Recognizer, rank

    model = Recognizer()
    if not model.engines or any(engine["provider"] != "CUDAExecutionProvider" for engine in model.engines):
        raise RuntimeError("Reading crops for publication requires the configured CUDA models")
    current = encoded(model.engines)
    rows = []
    for identity, raw, visual in db.execute("SELECT id,data,visual FROM units WHERE origin='local'").fetchall():
        stored = json.loads(visual)
        if stored.get("status") != "ready" or encoded(stored.get("engines")) != current:
            rows.append((identity, raw))
        elif (ranked := rank(stored["candidates"], stored.get("votes", []))) != stored["candidates"]:
            db.execute("UPDATE units SET visual=? WHERE id=?", (encoded({**stored, "candidates": ranked}), identity))
    db.commit()
    if not rows:
        return
    signature = hashlib.sha256(json.dumps(model.engines, sort_keys=True).encode()).hexdigest()[:16]
    cache = Path("cache/cloudflare-suggestions") / signature
    cache.mkdir(parents=True, exist_ok=True)
    print(encoded({"stage": "image-suggestions", "total": len(rows)}), flush=True)
    for index, (identity, raw) in enumerate(rows):
        image = json.loads(raw)["image"]
        key = image.rsplit("/", 1)[-1].removesuffix(".webp")
        saved = cache / (key + ".json")
        if saved.exists():
            result = json.loads(saved.read_text())
            result["candidates"] = rank(result["candidates"], result.get("votes", []))
        else:
            with Image.open(media.materialize(key)) as picture:
                result = model.read(picture.convert("RGB"))
            result.update(input_image=image, verified=False)
            # Renamed into place, so an interrupted export never leaves a partial cache entry.
            partial = saved.with_suffix(".partial")
            partial.write_text(json.dumps(result, ensure_ascii=False))
            partial.replace(saved)
        db.execute("UPDATE units SET visual=? WHERE id=?", (encoded(result), identity))
        if index % 500 == 0:
            db.commit()
            print(encoded({"stage": "image-suggestions", "done": index}), flush=True)
    db.commit()


def export(dataset: Path, output: Path, *, resume=False):
    output.mkdir(parents=True, exist_ok=resume)
    frozen = output / "source"
    fresh = not frozen.exists()
    frozen.mkdir(exist_ok=resume)
    # SQLite backup includes the WAL atomically; readers never copy a live WAL piecemeal.
    for name in ("review.sqlite", "feedback-receipts.sqlite"):
        if fresh and (dataset / name).exists():
            with sqlite3.connect(dataset / name) as src, sqlite3.connect(frozen / name) as dst:
                src.backup(dst)
    for path in dataset.glob("*.parquet") if fresh else []:
        shutil.copy2(path, frozen / path.name)
    store = Store(frozen)
    media = MediaCache(corpus_root=Path("work"))
    corpus = CorpusAPI("work", "work/corpus-index", autobuild=False)
    corpus_source.connect(corpus)
    api = atlas.router(store, media=media)
    endpoints = {r.path: r.endpoint for r in api.routes if "GET" in r.methods}
    db = sqlite3.connect(output / "catalogue.sqlite")
    schema(db)
    packs = Packs(output, db)
    with lookup_scope():
        existing = {r[0] for r in db.execute("SELECT id FROM units")}
        listing = endpoints["/atlas"](limit=10**7)
        units = {unit.id: unit for unit, revision in store.unit_snapshot()}
        neighbors = defaultdict(list)
        for unit in units.values():
            if unit.line_id:
                neighbors[unit.line_id].append(unit)
        pages = store.pages()
        documents = {d.id: d for d in store.documents()}
        lines = {}
        counts = Counter()
        print(encoded({"stage": "local-crops", "total": len(listing["items"])}), flush=True)
        for i, item in enumerate(listing["items"]):
            unit = units[item["id"]]
            page = pages.get(unit.page_id)
            doc = documents.get(unit.document_id or (page.document_id if page else None))
            if not doc or not doc.image_rights or str(doc.image_rights.licence) not in PROXYABLE:
                db.execute("DELETE FROM units WHERE id=?", (item["id"],))
                continue
            if item["id"] in existing:
                detail = json.loads(db.execute("SELECT data FROM units WHERE id=?", (item["id"],)).fetchone()[0])
                detail.update(licence=str(doc.image_rights.licence), holder=doc.holder,
                              attribution=doc.image_rights.attribution, rights_url=doc.image_rights.evidence)
                db.execute("UPDATE units SET data=?,document=? WHERE id=?", (encoded(detail), doc.id, item["id"]))
                continue
            if unit.line_id not in lines:
                lines[unit.line_id] = store.line(unit.line_id) if unit.line_id else None
            line = lines[unit.line_id]
            key = item["image"].rsplit("/", 1)[-1].removesuffix(".webp")
            spec = json.loads((media.directory / key[:2] / (key + ".json")).read_text())
            path = media.roots[spec["source"]] / spec["path"]
            box = spec["box"]
            detail = {**item, "context_image": media.local(path, box, context=True),
                      "source": doc.title if doc else "", "text": line.text if line else "",
                      "page_number": page.seq + 1 if page else None,
                      "context": bool(box), "context_box": None, "line": None,
                      "source_scale": [1, 1], "crop_editable": False}
            if box:
                with Image.open(path) as picture:
                    left, top, right, bottom = atlas.crop_bounds(picture, box, context=True)
                detail["context_box"] = {"x": left, "y": top, "w": right-left, "h": bottom-top}
                detail["crop_box"] = dict(zip(("x", "y", "w", "h"), box, strict=True))
                detail["source_scale"] = [box[2]/unit.box.w, box[3]/unit.box.h]
            detail["licence"] = str(doc.image_rights.licence) if doc and doc.image_rights else None
            detail["holder"] = doc.holder if doc else None
            detail["attribution"] = doc.image_rights.attribution
            detail["rights_url"] = doc.image_rights.evidence
            # Full-resolution pages remain separate; the viewer can pan the hosted context
            # immediately without trying a local full-page endpoint.
            detail["full_page_available"] = False
            for url in {detail["image"], detail["context_image"]}:
                key = url.rsplit("/", 1)[-1].removesuffix(".webp")
                packs.add(key, media.materialize(key))
            snapshot = {"character": item, "source_refs": doc.source_refs if doc else {},
                        "canvas": page.canvas if page else None,
                        "page_index": page.seq if page else None, "image_sha256": item["image_sha256"]}
            context = context_guesses(unit, line, neighbors.get(unit.line_id, []))
            # Filled by read_crops below, which also covers rows kept from a resumed export.
            visual = {"status": "unavailable", "candidates": []}
            cp = refs.to_code_point(item["label"]) if len(item["label"]) == 1 else None
            counts[cp] += 1
            family = refs.grapheme(cp) if cp else None
            db.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                item["id"], "local", item["label"], item["reading"], family, None,
                item["production"], atlas.character_group(unit), item["state"], item["revision"],
                int(not atlas.repair_withheld(unit)), atlas.review_priority(unit),
                int(hashlib.sha256(item["id"].encode()).hexdigest()[:7], 16),
                encoded(detail), encoded(snapshot), encoded(context), encoded(visual), doc.id))
            if i % 500 == 0:
                db.commit()
                print(encoded({"stage": "local-crops", "done": i}), flush=True)
        db.commit()
        read_crops(db, media)
        counts = Counter({refs.to_code_point(char): n for char, n in db.execute(
            "SELECT character,count(*) FROM units GROUP BY character") if len(char) == 1})
        print(encoded({"stage": "characters"}), flush=True)
        live_counts = {row["char"]: corpus._count_row(row["char"], row) for row in corpus.index.characters()}
        db.execute("DELETE FROM characters")
        db.execute("DELETE FROM aliases")
        for row in refs.characters():
            info = characters._row(row, counts)
            info["candidates"] = characters.candidate_summary(row.char, live=live_counts.get(row.char),
                                                               local=counts.get(row.code_point, 0))
            info["kind"] = ("ligature" if row.ligature else "han" if str(row.script) == "han"
                            else "hangul" if str(row.script) == "hangul"
                            else "gugyeol" if str(row.script) == "gugyeol" else "kana")
            info["default_scope"] = "grapheme" if info["candidates"].get("requires_family_scope") else info["default_scope"]
            detail = {**info, "alias": row.alias, "category": row.category,
                      "confusables": [characters.to_row(refs.character(cp)) for cp in row.confusables],
                      "characters": [characters._row(r, counts) for cp in characters._forms(row.code_point)
                                     if (r := refs.character(cp))],
                      "derived": [characters._row(r, counts) for cp in refs.derived(row.char)
                                  if (r := refs.character(cp))],
                      "expansions": characters._expansions(row, counts, expand="none"),
                      "visual_analysis": characters.visual_families.family_analysis(row.code_point)}
            db.execute("INSERT INTO characters VALUES (?,?,?,?,?)",
                       (row.code_point, row.char, row.name or "", encoded(info), encoded(detail)))
            aliases = {row.char: 0, row.code_point.lower(): 0}
            for value in [*row.readings, *row.jibo]:
                aliases.setdefault(refs.to_hiragana(value), 4)
            if row.ligature:
                for value in [row.ligature.reading, refs.from_code_points(row.ligature.components)]:
                    if value:
                        aliases[refs.to_hiragana(value)] = 1
            for value, rank in aliases.items():
                db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?,?)", (value, row.code_point, rank))
        meta = {"catalogue": {k: v for k, v in listing.items() if k != "items"},
                "published_at": datetime.now(UTC).isoformat(),
                "corpus_index": json.loads(Path("work/corpus-index/index.json").read_text()),
                "collection": collection.status(Path("work").resolve()), "review_epoch": store.review_epoch()}
        # The public status reports publication, never a stale claim that a collector is running.
        def public_status(value):
            if isinstance(value, dict):
                return {k: ("snapshot" if k == "status" else public_status(v)) for k, v in value.items()
                        if k not in {"root", "output", "path", "disk_free_bytes", "error", "errors"}}
            if isinstance(value, list):
                return [public_status(v) for v in value]
            return value
        for key, value in meta.items():
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, encoded(public_status(value))))
        db.commit()
    packs.close()
    with (output / "catalogue.sql").open("w") as handle:
        for line in db.iterdump():
            if line not in {"BEGIN TRANSACTION;", "COMMIT;"} and not line.startswith("CREATE TRIGGER"):
                handle.write(line + "\n")
    db.close()
    # The source backup contains private ingestion state and is never an upload input.
    print(encoded({"stage": "complete", "units": len(listing["items"]), "images": len(packs.seen)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    export(args.dataset, args.output, resume=args.resume)
