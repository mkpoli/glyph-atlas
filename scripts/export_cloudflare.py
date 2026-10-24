"""Publish a consistent, path-free catalogue snapshot for the Cloudflare viewer.

This writes deployment inputs only. It never changes the review dataset or uploads
anything. Review events collected on Cloudflare live in a separate D1 journal.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import export_character_variants
from catalogue_schema import schema
from PIL import Image

from glyph_atlas import ainu_geometry, export_lock, images, ngrams, page_sources, refs, style, tone, withdrawn
from glyph_atlas.corpus.api import PROXYABLE, CorpusAPI
from glyph_atlas.review import atlas, characters, collection, corpus_source, ledger
from glyph_atlas.review.context_suggestions import context_guesses
from glyph_atlas.review.media import MediaCache
from glyph_atlas.review.request_cache import lookup_scope
from glyph_atlas.review.store import Store

#: The columns a publication writes into `units`, by name: the table has columns no publication fills.
UNIT_COLUMNS = ("id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,"
                "data,snapshot,context,visual,document,style")


#: What a crop's record holds about its cut: kept from an earlier run of a resumed export while the
#: crop's image is the same, since the images they describe are already packed. `tone` and `image_size`
#: (`glyph_atlas.tone`) are what the site paints in the crop's place until its image arrives.
CUT_KEYS = ("context_image", "context", "context_box", "line", "source_scale", "crop_editable", "crop_box",
            "tone", "image_size")


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
        # Numbered past every pack the catalogue names, on disk or not: an earlier run's packs may be
        # deleted once published, and a new pack under one of their names would put new bytes under
        # the rows still pointing into it.
        names = {p.name for p in output.glob("pack-*.bin")} | {o for o, in db.execute("SELECT DISTINCT object FROM media")}
        self.index = max([int(m[1]) for n in names if (m := re.fullmatch(r"pack-(\d+)\.bin", n))], default=0)
        # A run killed after committing rows whose bytes never reached the pack leaves them past its end;
        # they go, so their images are packed again. A pack gone from disk was published and deleted, and
        # its rows stay.
        for name, in db.execute("SELECT DISTINCT object FROM media").fetchall():
            if (output / name).exists():
                db.execute("DELETE FROM media WHERE object=? AND offset+size>?", (name, (output / name).stat().st_size))
        db.commit()
        self.seen = {r[0] for r in db.execute("SELECT key FROM media")}

    def add(self, key, path, content_type="image/webp"):
        if key in self.seen:
            return
        if self.file is None or self.file.tell() >= 32 * 1024**2:
            if self.file:
                self.file.close()
            self.index += 1
            self.name = f"pack-{self.index:04}.bin"
            self.file = (self.output / self.name).open("wb")
        # The row goes in only once the bytes are in the pack. A copy that fails partway is cut off the
        # pack, so the next image starts where this one did and no row points at bytes it lacks.
        offset = self.file.tell()
        try:
            with path.open("rb") as source:
                shutil.copyfileobj(source, self.file, 1024 * 1024)
        except BaseException:
            self.file.seek(offset)
            self.file.truncate()
            raise
        self.db.execute("INSERT INTO media VALUES(?,?,?,?,?)",
                        (key, self.name, offset, self.file.tell() - offset, content_type))
        self.seen.add(key)

    def read(self, key):
        """The bytes of a packed image, or None when its pack is no longer in the export."""
        row = self.db.execute("SELECT object,offset,size FROM media WHERE key=?", (key,)).fetchone()
        path = self.output / row[0] if row else None
        if path is None or not path.exists():
            return None
        if self.file and not self.file.closed:
            self.file.flush()
        with path.open("rb") as handle:
            handle.seek(row[1])
            data = handle.read(row[2])
        return data if len(data) == row[2] else None

    def sync(self):
        """Put the open pack's bytes on disk. Call it before committing rows that point into the pack."""
        if self.file and not self.file.closed:
            self.file.flush()
            os.fsync(self.file.fileno())

    def close(self):
        if self.file:
            self.sync()
            self.file.close()


def image_tone(packs, media, url):
    """The crop's `tone` and `image_size` (`glyph_atlas.tone`), read from the image as the export packed
    it, or cut again when its pack has gone."""
    key = url.rsplit("/", 1)[-1].removesuffix(".webp")
    packed = packs.read(key)
    return tone.crop_tone(io.BytesIO(packed) if packed is not None else media.materialize(key))


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


PRIVATE_STATUS = frozenset({"root", "output", "path", "disk_free_bytes", "error", "errors"})


def public_status(value):
    """What the site may show of a collector's status: no local paths, disk space or error text, and
    `status` as a publication snapshot, never a stale claim that a collector is running."""
    if isinstance(value, dict):
        return {k: ("snapshot" if k == "status" else public_status(v)) for k, v in value.items()
                if k not in PRIVATE_STATUS}
    if isinstance(value, list):
        return [public_status(v) for v in value]
    return value


def page_fields(page) -> dict:
    """What a crop shows of its page: the page number, seq + 1, and the page's 0-based position."""
    return {"page_number": page.seq + 1 if page else None, "page_index": page.seq if page else None}


def page_record(page, doc, crops, previous, following, catalogue=None) -> dict:
    """A page as the page view shows it (0078): its book, number and size in the page pixels boxes are
    given in, its IIIF image service, where it can be opened at its source (`page_sources`), its
    credit, the pages before and after it that hold crops, and the ids of its crops in reading order."""
    rights = doc.image_rights if doc else None
    service = images.service_of(page.image or "")
    return {"id": page.id, "document": page.document_id, "title": doc.title if doc else None,
            "page_number": page.seq + 1, "width": page.width, "height": page.height, "service": service,
            **page_sources.page_sources(doc.source_refs if doc else None, page.canvas, service, catalogue),
            "holder": doc.holder if doc else None, "licence": str(rights.licence) if rights else None,
            "attribution": rights.attribution if rights else None, "rights_url": rights.evidence if rights else None,
            "previous": previous, "next": following, "crops": crops}


def page_rows(db, published, units, pages, documents):
    """Rewrite `pages` from the ids of the crops `published`: a page appears once one of its crops does,
    and its neighbours are the pages of its book that do too."""
    held = defaultdict(list)
    for identity in published:
        unit = units.get(identity)
        if unit is not None and unit.page_id in pages:
            held[unit.page_id].append(unit)
    books = defaultdict(list)
    for page_id in held:
        books[pages[page_id].document_id].append(pages[page_id])
    db.execute("DELETE FROM pages")
    catalogue = page_sources.catalogue_urls((d, doc.source_refs, doc.meta) for d, doc in documents.items())
    for document_id, own in books.items():
        own.sort(key=lambda page: (page.seq, page.id))
        for i, page in enumerate(own):
            crops = [u.id for u in sorted(held[page.id], key=lambda u: (u.line_id or "", u.seq if u.seq is not None else -1, u.id))]
            record = page_record(page, documents.get(document_id), crops,
                                 own[i - 1].id if i else None, own[i + 1].id if i + 1 < len(own) else None,
                                 catalogue.get(document_id))
            db.execute("INSERT INTO pages VALUES (?,?,?)", (page.id, document_id, encoded(record)))
    db.commit()


def freeze(dataset: Path, frozen: Path) -> None:
    """Copy what the export reads of `dataset` to `frozen`, so the dataset can change while it runs."""
    # SQLite backup includes the WAL atomically; readers never copy a live WAL piecemeal.
    for name in ("review.sqlite", "feedback-receipts.sqlite"):
        if (dataset / name).exists():
            with sqlite3.connect(dataset / name) as src, sqlite3.connect(frozen / name) as dst:
                src.backup(dst)
    # The crop geometry flags (`glyph_atlas.ainu_geometry`) are read beside the tables they describe.
    for path in [*dataset.glob("*.parquet"), *dataset.glob(ainu_geometry.FILE)]:
        shutil.copy2(path, frozen / path.name)


def export(dataset: Path, output: Path, *, resume=False):
    output.mkdir(parents=True, exist_ok=resume)
    lock = export_lock.hold(output)  # noqa: F841 -- held while the export writes
    frozen = output / "source"
    fresh = not frozen.exists()
    frozen.mkdir(exist_ok=resume)
    if fresh:
        freeze(dataset, frozen)
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
            # A crop's form is the site's ledger's, which the Worker adds to the record it serves.
            item = dict(item)
            item.pop("form", None)
            unit = units[item["id"]]
            page = pages.get(unit.page_id)
            doc = documents.get(unit.document_id or (page.document_id if page else None))
            if (not doc or doc.id in withdrawn.documents() or not doc.image_rights
                    or str(doc.image_rights.licence) not in PROXYABLE):
                db.execute("DELETE FROM units WHERE id=?", (item["id"],))
                continue
            # A crop an earlier run wrote keeps what was cut for it (its images, their boxes, its reading
            # by the crop models) while its image is unchanged; the rest of its record is written anew,
            # so a resumed export publishes the same record a fresh one would.
            cut = visual = None
            if item["id"] in existing:
                data, stored = db.execute("SELECT data,visual FROM units WHERE id=?", (item["id"],)).fetchone()
                earlier = json.loads(data)
                if earlier.get("image") == item["image"]:
                    cut = {key: earlier[key] for key in CUT_KEYS if key in earlier}
                    visual = json.loads(stored)
                else:
                    # A crop whose box moved since the earlier run (a reviewer redrew it) is cut again from
                    # its new box, which is a new image and a new crop version.
                    db.execute("DELETE FROM units WHERE id=?", (item["id"],))
                    existing.discard(item["id"])
            if unit.line_id not in lines:
                lines[unit.line_id] = store.line(unit.line_id) if unit.line_id else None
            line = lines[unit.line_id]
            if cut is None:
                key = item["image"].rsplit("/", 1)[-1].removesuffix(".webp")
                spec = json.loads((media.directory / key[:2] / (key + ".json")).read_text())
                path = media.roots[spec["source"]] / spec["path"]
                box = spec["box"]
                cut = {"context_image": media.local(path, box, context=True), "context": bool(box),
                       "context_box": None, "line": None, "source_scale": [1, 1], "crop_editable": bool(box)}
                if box:
                    with Image.open(path) as picture:
                        left, top, right, bottom = atlas.crop_bounds(picture, box, context=True)
                    cut["context_box"] = {"x": left, "y": top, "w": right-left, "h": bottom-top}
                    cut["crop_box"] = dict(zip(("x", "y", "w", "h"), box, strict=True))
                    cut["source_scale"] = [box[2]/unit.box.w, box[3]/unit.box.h]
                for url in {item["image"], cut["context_image"]}:
                    key = url.rsplit("/", 1)[-1].removesuffix(".webp")
                    packs.add(key, media.materialize(key))
            if "tone" not in cut:
                cut.update(image_tone(packs, media, item["image"]))
            detail = {**item, "source": doc.title if doc else "", "text": line.text if line else "",
                      "page_number": page_fields(page)["page_number"], **cut}
            detail["licence"] = str(doc.image_rights.licence) if doc and doc.image_rights else None
            detail["holder"] = doc.holder if doc else None
            detail["attribution"] = doc.image_rights.attribution
            detail["rights_url"] = doc.image_rights.evidence
            # Full-resolution pages remain separate; the viewer can pan the hosted context
            # immediately without trying a local full-page endpoint.
            detail["full_page_available"] = False
            snapshot = {"character": item, "source_refs": doc.source_refs if doc else {},
                        "canvas": page.canvas if page else None,
                        "page_index": page_fields(page)["page_index"], "image_sha256": item["image_sha256"]}
            context = context_guesses(unit, line, neighbors.get(unit.line_id, []))
            # Filled by read_crops below, which also reads a kept crop again when its models changed.
            visual = visual or {"status": "unavailable", "candidates": []}
            cp = refs.to_code_point(item["label"])
            counts[cp] += 1
            # A label with no family is its own grapheme, so every named crop is one `family` lookup.
            family = atlas.grapheme_of(item["label"])
            db.execute(f"INSERT OR REPLACE INTO units({UNIT_COLUMNS}) VALUES ({','.join('?' * (UNIT_COLUMNS.count(',') + 1))})", (
                item["id"], "local", item["label"], family, None,
                item["production"], atlas.character_group(unit), item["state"], item["revision"],
                int(not atlas.repair_withheld(unit) and not ainu_geometry.out_of_quiz(item.get("geometry"))), atlas.review_priority(unit),
                int(hashlib.sha256(item["id"].encode()).hexdigest()[:7], 16),
                encoded(detail), encoded(snapshot), encoded(context), encoded(visual), doc.id,
                style.style_of(unit, page, doc)))
            if i % 500 == 0:
                packs.sync()
                db.commit()
                print(encoded({"stage": "local-crops", "done": i}), flush=True)
        packs.sync()
        page_rows(db, [i for i, in db.execute("SELECT id FROM units WHERE origin='local'")], units, pages, documents)
        # The claims made here, and those an action here names, go to the site's ledger, but none about a
        # crop of a withdrawn document; the site resolves their slots once they are there (`seal_cloudflare`).
        gone = withdrawn.documents()
        ranges = [withdrawn.corpus_range(document) for document in gone]

        def kept(subject):
            unit = units.get(subject)
            if unit is not None:
                page = pages.get(unit.page_id)
                return (unit.document_id or (page.document_id if page else None)) not in gone
            return not any(low <= subject < high for low, high in ranges)

        with store._connection() as source:
            ledger.copy_published(source, db, kept)
        db.commit()
        # Only runs whose crops were all published above are recorded.
        db.execute("DELETE FROM unit_ngrams")
        published = {i: ngrams.Written(*rest) for i, *rest in db.execute(
            "SELECT id,character,document,hand_order,shuffle FROM units WHERE origin='local'")}
        ngrams.record_runs(db, ngrams.adjacent_ngrams(units.values(), store.horizontal_lines()), published)
        db.commit()
        read_crops(db, media)
        # Keyed as the character layer keys a row, so a sequence it holds (𛂞 + U+3099) is counted too.
        counts = Counter({refs.to_code_point(char): n for char, n in db.execute(
            "SELECT character,count(*) FROM units GROUP BY character") if char})
        print(encoded({"stage": "characters"}), flush=True)
        live_counts = {row["char"]: corpus._count_row(row["char"], row) for row in corpus.index.characters()}
        # The derived forms reach the site through export_character_variants.py, never through a collection.
        export_character_variants.fill(db, derived=False)
        db.execute("DELETE FROM characters")
        db.execute("DELETE FROM aliases")
        exported = {row.code_point for row in refs.characters()}
        rows = refs.characters() + [
            row for code_point in sorted(counts, key=refs._point_order)
            if code_point not in exported and (row := refs.character(code_point)) is not None
        ]
        for row in rows:
            info = characters._row(row, counts)
            info["origin"] = refs.origin_of(row.code_point)
            info["candidates"] = characters.candidate_summary(row.char, live=live_counts.get(row.char),
                                                               local=counts.get(row.code_point, 0))
            info["kind"] = characters.kind_of(row)
            info["default_scope"] = "grapheme" if info["candidates"].get("requires_family_scope") else info["default_scope"]
            detail = {**info, "alias": row.alias, "category": row.category,
                      "confusables": [characters.to_row(refs.character(cp)) for cp in row.confusables],
                      "characters": [characters.form_row(r, counts) for cp in characters._forms(row.code_point)
                                     if (r := refs.character(cp))],
                      "derived": [characters._row(r, counts) for cp in refs.derived(row.char)
                                  if (r := refs.character(cp))],
                      "expansions": characters._expansions(row, counts, expand="none"),
                      "visual_analysis": characters.visual_families.family_analysis(row.code_point)}
            db.execute("INSERT INTO characters VALUES (?,?,?,?,?)",
                       (row.code_point, row.char, row.name or "", encoded(info), encoded(detail)))
            aliases = {row.char: 0, row.code_point.lower(): 0}
            # A hiragana is found by the kanji it is the cursive of (の by 乃); a katakana is written from
            # part of a kanji (ミ from 三), which a reader searching 三 is not asking for.
            cursive = [e["char"] for e in info["origin"] if e["field"] == "平仮名字源"]
            for value in [*row.readings, *row.jibo, *cursive]:
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
