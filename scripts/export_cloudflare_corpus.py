"""Stream corpus metadata and locally held glyph images into immutable R2 packs, with the runs of the
glyphs exported (`corpus_runs`)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

import pyarrow.dataset as ds
from catalogue import schema
from export_cloudflare import Packs, encoded

from glyph_atlas import box_relabel, export_lock, ngrams, style, tone, withdrawn
from glyph_atlas.corpus import sources
from glyph_atlas.corpus.api import PROXYABLE, CorpusAPI
from glyph_atlas.corpus.details import DetailResolver, _iiif_region, _viewport, unit_label
from glyph_atlas.corpus.index import (
    UNIT_CORPORA,
    _char_of_codepoint,
    _character_unit_row,
    _MetaCache,
    _unit_row,
)
from glyph_atlas.importers.codh_all import reading_place
from glyph_atlas.review.media import MediaCache


class FrozenResolver(DetailResolver):
    def _stamp_digest(self):
        if not hasattr(self, "export_stamp"):
            self.export_stamp = super()._stamp_digest()
        return self.export_stamp


class Published:
    """Stands in for `Packs` when only records are exported: every image must already be public.

    `keys` is the set of display crops an earlier publication packed, read from that export's
    `corpus.sqlite` or from a list of D1's `media` keys. A record whose image is not in it is left
    out rather than published pointing at an image R2 does not hold.
    """

    file = None

    def __init__(self, keys):
        self.keys = keys

    def sync(self):
        pass

    def add(self, key, path):
        if key not in self.keys:
            raise ValueError(f"display crop {key} was never published")

    def close(self):
        pass


def published_keys(path: Path) -> set[str]:
    """Media keys from an earlier export's `corpus.sqlite`, or from a text file, one per line."""
    if path.suffix == ".sqlite":
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            return {key for (key,) in db.execute("SELECT key FROM media")}
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def unit_ids(path: Path) -> set[str]:
    """Unit ids from a text file, one per line: the corpus units a site already holds."""
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def held(db, skip=frozenset()):
    """Ids this export does not write again: those already in its database, and those in `skip`."""
    return {r[0] for r in db.execute("SELECT id FROM corpus_units")} | set(skip)


def holder_context(joined, box, page):
    """The holder's IIIF region of a unit's context window and that window, or (None, None) when the page
    has no service, no registered size, or was registered at a scaled rendition, whose boxes are not in
    the service's pixels; a packed crop then cuts its context from the local page."""
    service, image = joined.get("image_service"), (page.get("image") or "").rstrip("/")
    if not service or image not in (service, f"{service}/full/full/0/default.jpg", f"{service}/full/max/0/default.jpg"):
        return None, None
    window = _viewport(box, page.get("width"), page.get("height"))
    url = _iiif_region(service, window, edge=900)
    return (window, url) if url else (None, None)


def attribution(joined):
    """The credit line the source asks for, from the image rights of its registry record, or None."""
    return (joined.get("image_rights") or {}).get("attribution") or None


def text_attribution(joined):
    """The credit of the transcription that labels the unit, or None when it repeats the image's credit."""
    credit = joined.get("text_attribution") or None
    return None if credit == attribution(joined) else credit


def holder_image(joined, box, enabled):
    """The holder's own IIIF region of a unit's box when `enabled` and the page has a service."""
    return _iiif_region(joined.get("image_service"), box, edge=480) if enabled else None


#: Corpora the site publishes as its own crops. The Ainu records are merged into the Ainu build and
#: published from it as local crops under the same ids; as corpus glyphs too, a round would deal the
#: copy and every save of it would be refused against the local row.
PUBLISHED_LOCALLY = frozenset({"ainu-records"})


def locally_published_ids(root="work") -> set[str]:
    """The unit ids of the corpora the site publishes as its own crops."""
    ids = set()
    for corpus in sources.discover(root):
        path = corpus.table("units") if corpus.name in PUBLISHED_LOCALLY else None
        if path is not None:
            ids.update(ds.dataset(path, format="parquet").to_table(columns=["id"])["id"].to_pylist())
    return ids


def drop_locally_published(db, ids) -> int:
    """Remove from an export's `corpus_units` the glyphs the site publishes as its own crops; rows an
    earlier export wrote stay otherwise, so a resumed export or a seal of it would bring them back."""
    db.execute("CREATE TEMP TABLE IF NOT EXISTS published_locally (id TEXT PRIMARY KEY)")
    db.execute("DELETE FROM published_locally")
    db.executemany("INSERT OR IGNORE INTO published_locally VALUES (?)", [(i,) for i in ids])
    return db.execute("DELETE FROM corpus_units WHERE id IN (SELECT id FROM published_locally)").rowcount


def drop_withdrawn(db) -> int:
    """Remove from an export's `corpus_units` the glyphs of withdrawn documents an earlier run wrote, and
    from its `unit_ngrams` their runs. A run never crosses a line, so its first glyph names its document."""
    dropped = 0
    for doc in withdrawn.documents():
        low, high = withdrawn.corpus_range(doc)
        dropped += db.execute("DELETE FROM corpus_units WHERE id>=? AND id<?", (low, high)).rowcount
        db.execute("DELETE FROM unit_ngrams WHERE first>=? AND first<?", (low, high))
    return dropped


def unit_corpora(names=None):
    """Every unit corpus under `work` the site does not publish locally, or only those in `names`,
    which must all exist."""
    found = [corpus for corpus in sources.discover("work")
             if corpus.name in UNIT_CORPORA and corpus.name not in PUBLISHED_LOCALLY]
    if names is None:
        return found
    missing = set(names) - {corpus.name for corpus in found}
    if missing:
        raise ValueError(f"no unit corpus named {', '.join(sorted(missing))}")
    return [corpus for corpus in found if corpus.name in names]


def corpus_titles(corpora) -> dict[str, str]:
    """The title of each document `corpora` hold, by document id, from their `documents` tables."""
    titles: dict[str, str] = {}
    for corpus in corpora:
        paths = corpus.parquet_files("documents")
        if not paths:
            continue
        table = ds.dataset([str(p) for p in paths], format="parquet").to_table(columns=["id", "title"])
        for document, title in zip(table.column("id").to_pylist(), table.column("title").to_pylist(), strict=True):
            if document and title:
                titles[document] = title
    return titles


def line_orientation(corpus) -> dict[str, bool]:
    """Each line's `vertical` flag, from the corpus's lines table, or nothing when it has none."""
    paths = corpus.parquet_files("lines")
    if not paths:
        return {}
    # A sharded table's directory holds its manifest beside the shards.
    lines = ds.dataset([str(p) for p in paths], format="parquet")
    if not {"id", "vertical"} <= set(lines.schema.names):
        return {}
    table = lines.to_table(columns=["id", "vertical"])
    return dict(zip(table["id"].to_pylist(), table["vertical"].to_pylist(), strict=True))


#: What a run is read from, of a corpus's units.
RUN_COLUMNS = ("id", "document_id", "page_id", "line_id", "seq", "box", "kind", "granularity", "active", "method", "unicode",
               "text_source")
#: Units whose line numbers come from the aligner, which numbered a line's boxes in a scrambled order
#: until 2026-09-24 (`glyph_atlas.box_relabel`): their places are read from their boxes instead.
ALIGNED = "detect-align"


def placed(row: dict) -> dict:
    """A unit row with its line and position: its own, or those its dataset's ids carry
    (`codh_all.reading_place`)."""
    if row.get("line_id") is None and (place := reading_place(row["id"], row.get("page_id"))):
        row["line_id"], row["seq"] = place
    return row


def single(text: str) -> bool:
    """Whether a text is one character, as the Worker splits a run's text into crops."""
    return len(text) == 1 and not unicodedata.combining(text)


def corpus_runs(corpus) -> tuple[list[ngrams.Run], list[str], dict[str, str]]:
    """A corpus's runs, the ids of every glyph on a line, whose earlier runs the new ones replace, and
    the label each of those glyphs' records is shown with while no written form is settled.

    A run is found the way a crop's is (`ngrams.adjacent_ngrams`): by the glyph's line and position on
    it. A corpus whose units carry no line, such as a set of pre-cut crops, has none; a CODH unit's line
    and place are read from its id (`codh_all.reading_place`), an aligned unit's place from its box
    (`ngrams.in_reading_order`). The glyphs of withdrawn documents are left out.
    """
    paths = corpus.parquet_files("units")
    if not paths:
        return [], [], {}
    dataset = ds.dataset([str(p) for p in paths], format="parquet")
    if not {"line_id", "seq"} <= set(dataset.schema.names):
        return [], [], {}
    vertical = line_orientation(corpus)
    gone = withdrawn.documents()
    rows = [placed(row) for row in dataset.to_table(columns=[c for c in RUN_COLUMNS if c in dataset.schema.names]).to_pylist()
            if row.get("document_id") not in gone]
    rows = [row for row in rows if row.get("line_id")]
    horizontal = {line for line, down in vertical.items() if down is False}
    glyphs = [ngrams.Glyph.of(row) for row in rows if row.get("method") != ALIGNED]
    glyphs += ngrams.in_reading_order((ngrams.Glyph.of(row) for row in rows if row.get("method") == ALIGNED), horizontal)
    # A line read from the ids is a block of several columns (`adjacent_ngrams`).
    blocks = {row["line_id"] for row in rows if reading_place(row["id"], row.get("page_id"))}
    runs = ngrams.adjacent_ngrams(glyphs, horizontal, blocks)
    # A label stands for one crop of a run, so a transcription of several characters gives none.
    members = {i for run in runs for i in run.units}
    labels = {row["id"]: label for row in rows if row["id"] in members and (label := unit_label(row)) and single(label)}
    return runs, [row["id"] for row in rows], labels


#: Ids a statement names at once.
CHUNK = 5000


def record_corpus_runs(db, corpus) -> int:
    """Replace the runs of a corpus's glyphs in an export's `unit_ngrams`; return how many were written.

    A glyph with no written character takes its label, which its runs read in place of one. A run is
    recorded only while all of its glyphs are in the export's `corpus_units`, so one with a glyph of a
    withdrawn document or one the export left out is not. A glyph with a review overlay (a `units` row
    with `origin='corpus'`) reads its character, order and shuffle from it, as publish.py takes the
    glyph's character from it; the label stands in only while neither holds a character. The run's
    document is its first glyph's.
    """
    runs, on_lines, labels = corpus_runs(corpus)
    for start in range(0, len(on_lines), CHUNK):
        db.execute("DELETE FROM unit_ngrams WHERE first IN (SELECT value FROM json_each(?))", (json.dumps(on_lines[start:start + CHUNK]),))
    named = sorted(labels.items())
    db.executemany("UPDATE corpus_units SET label=? WHERE id=? AND character IS NULL", [(label, i) for i, label in named])
    members = sorted({i for run in runs for i in run.units})
    glyphs = {}
    for start in range(0, len(members), CHUNK):
        glyphs.update((i, ngrams.Written(*rest)) for i, *rest in db.execute(
            "SELECT c.id,coalesce(iif(u.id IS NULL,c.character,u.character),c.label),c.document,"
            "coalesce(u.hand_order,c.hand_order),coalesce(u.shuffle,c.shuffle) FROM corpus_units c"
            " LEFT JOIN units u ON u.id=c.id AND u.origin='corpus' WHERE c.id IN (SELECT value FROM json_each(?))",
            (json.dumps(members[start:start + CHUNK]),)))
    return ngrams.record_runs(db, sorted(runs), glyphs)


def stale_units(dataset, vertical=None) -> set[str]:
    """The ids of the units on lines whose detect-align units were aligned in the old detection order,
    or that hold a box another line of their page holds.

    Their labels sit on the wrong boxes (`glyph_atlas.box_relabel`), so a publication leaves them out
    until `atlas repair relabel` has given each box its character and each detection one line.
    `vertical` gives each line's orientation where it is known.
    """
    from glyph_atlas.schema import Box

    if not {"id", "page_id", "line_id", "seq", "box", "method"} <= set(dataset.schema.names):
        return set()
    lines, ids, held = {}, {}, []
    table = dataset.to_table(columns=["id", "page_id", "line_id", "seq", "box"],
                             filter=ds.field("method") == "detect-align")
    for row in table.to_pylist():
        if row["line_id"]:
            ids.setdefault(row["line_id"], []).append(row["id"])
            if row["seq"] is not None and row["box"]:
                box = Box(**row["box"])
                lines.setdefault(row["line_id"], []).append((row["seq"], box))
                held.append((row["page_id"], row["line_id"], box))
    known = vertical or {}
    stale = box_relabel.shared_lines(held)
    stale |= {line for line, placed in lines.items() if box_relabel.stale(placed, vertical=known.get(line))}
    return {unit for line in stale for unit in ids[line]}


def drop_stale(db, ids) -> int:
    """Remove from an export's `corpus_units` the glyphs of stale lines an earlier run wrote."""
    db.execute("CREATE TEMP TABLE IF NOT EXISTS stale_units (id TEXT PRIMARY KEY)")
    db.execute("DELETE FROM stale_units")
    db.executemany("INSERT OR IGNORE INTO stale_units VALUES (?)", [(i,) for i in ids])
    return db.execute("DELETE FROM corpus_units WHERE id IN (SELECT id FROM stale_units)").rowcount


def export(output, *, resume=False, published=None, corpora=None, skip=frozenset(), holder_images=False):
    """Export every unit corpus, or only those named in `corpora`, leaving out the ids in `skip`.

    With `holder_images`, a unit on a page the holder serves through IIIF points at the holder's
    region of its box, so no crop is packed for it; a pre-cut crop is packed as before.
    """
    found = unit_corpora(corpora)
    output.mkdir(parents=True, exist_ok=resume)
    lock = export_lock.hold(output)  # noqa: F841 -- held while the export writes
    db = sqlite3.connect(output / "corpus.sqlite")
    schema(db)
    media = MediaCache(corpus_root=Path("work"))
    api = CorpusAPI("work", "work/corpus-index", autobuild=False)
    resolver = FrozenResolver(api)
    packs = Published(published) if published is not None else Packs(output, db)
    # Distinct names allow catalogue and corpus exports to be combined safely.
    if published is None:
        packs.index = max(packs.index, 10000)
    record_file = None
    pack_index = max([int(p.stem.split("-")[-1]) for p in output.glob("corpus-*.bin")], default=0)
    for name, in db.execute("SELECT DISTINCT object FROM corpus_units"):
        size = (output / name).stat().st_size
        db.execute("DELETE FROM corpus_units WHERE object=? AND offset+size>?", (name, size))
    db.commit()
    drop_locally_published(db, locally_published_ids())
    drop_withdrawn(db)
    # The titles of the documents exported, which the publication writes with their counts (0075).
    db.executemany("INSERT INTO corpus_documents(document,n,title) VALUES(?,0,?) ON CONFLICT(document) DO UPDATE SET title=excluded.title",
                   sorted(corpus_titles(found).items()))
    db.commit()
    existing = held(db, skip)
    counts = Counter(json.loads((output / "progress.json").read_text()) if (output / "progress.json").exists() else {})
    for corpus in found:
        paths = corpus.parquet_files("units")
        if not paths:
            continue
        context = _MetaCache(corpus)
        dataset = ds.dataset([str(p) for p in paths], format="parquet")
        stale = stale_units(dataset, line_orientation(corpus))
        counts["stale-order-dropped"] += drop_stale(db, stale)
        db.commit()
        columns = [c for c in ("id", "document_id", "page_id", "line_id", "seq", "box", "crop", "crop_sha256",
                   "kind", "granularity", "text_source", "unicode", "method", "review", "active", "upstream",
                   "style")
                   if c in dataset.schema.names]
        for batch in dataset.scanner(columns=columns, batch_size=2048, use_threads=False).to_batches():
            for row in batch.to_pylist():
                if row["id"] in stale:
                    counts["stale-order"] += 1
                    continue
                if row["id"] in existing:
                    continue
                if row["document_id"] in withdrawn.documents():
                    counts["withdrawn"] += 1
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
                context_box, context_image = holder_context(joined, box, page)
                image = joined["thumbnail"].get("iiif_url")
                # What the site paints in the crop's place until it loads (`glyph_atlas.tone`), read from a
                # crop packed here; a holder's own image is not fetched for it.
                painted = {}
                holder = holder_image(joined, box, holder_images)
                if holder:
                    image = holder
                elif joined["thumbnail"].get("mode") != "remote_iiif":
                    image = media.corpus_image(joined, api.crops, edge=480)
                    if not image:
                        counts["unavailable"] += 1
                        continue
                    key = image.rsplit("/", 1)[-1].removesuffix(".webp")
                    try:
                        crop = media.materialize(key)
                        packs.add(key, crop)
                        painted = tone.crop_tone(crop)
                    except (OSError, ValueError):
                        counts["unavailable"] += 1
                        continue
                    # A page the holder serves shows the holder's region as its context; any other
                    # page-backed crop gets a context cut with the same coordinate transform, and a
                    # pre-cut dataset has none.
                    spec = json.loads((media.directory / key[:2] / (key + ".json")).read_text())
                    if not context_image and spec.get("box"):
                        path = media.roots[spec["source"]] / spec["path"]
                        context_image = media.local(path, spec["box"], context=True)
                        context_key = context_image.rsplit("/", 1)[-1].removesuffix(".webp")
                        try:
                            packs.add(context_key, media.materialize(context_key))
                        except (OSError, ValueError):
                            counts["unavailable"] += 1
                            continue
                        from PIL import Image

                        from glyph_atlas.review.atlas import crop_bounds
                        with Image.open(path) as picture:
                            l, t, r, b = crop_bounds(picture, spec["box"], context=True)
                        context_box = {"x": l, "y": t, "w": r-l, "h": b-t}
                    elif not context_image:
                        context_box, context_image = None, None
                source = {"corpus": corpus.name, **{k: joined.get(k) for k in
                    ("document_id", "page_id", "line_id", "title", "holder", "shelfmark", "image_service")},
                    "source_url": joined.get("canvas")}
                detail = resolver._assemble(identity=row["id"], unit_id=row["id"], label=char,
                    source_label=row.get("text_source"), code_point=row.get("unicode"),
                    box=box, image=image, image_reason=None, crop_sha256=row.get("crop_sha256"),
                    source_crop=row.get("crop"), proxyable=True, licence=joined.get("image_licence"),
                    source=source, context_box=context_box, context_image=context_image,
                    context_basis="derived_viewport" if context_box else None, crop_box=box,
                    record_url=resolver._record_url(corpus, joined, box),
                    extra={"kind": "char", "method": joined.get("method"), "basis": "upstream_bbox" if box else "upstream_crop",
                        "review": joined.get("review") or "machine", "confirmed_by_human": False,
                        "render_available": True, "attribution": attribution(joined),
                        "text_attribution": text_attribution(joined),
                        **{k: joined.get(k) for k in ("production", "production_label", "production_evidence")}})
                detail.update(origin="corpus", state="pending", revision=0, suggestions=[], located=True, grid_safe=True,
                              **painted)
                if record_file is None or record_file.tell() >= 32 * 1024**2:
                    if record_file:
                        record_file.close()
                    pack_index += 1
                    record_name = f"corpus-{pack_index:04}.bin"
                    record_file = (output / record_name).open("wb")
                raw = encoded(detail).encode()
                family = detail.get("grapheme")
                visual = detail.get("visual_group") or {}
                document_id = joined.get("document_id") or None
                written_style = style.resolve_values(row.get("style"), page.get("style"), document_id,
                                                     context.document(document_id or "").get("style"))[0]
                db.execute("INSERT OR IGNORE INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,style,document) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    row["id"], detail.get("written_character"), family, visual.get("id"),
                    int(hashlib.sha256(row["id"].encode()).hexdigest()[:7], 16), record_name, record_file.tell(), len(raw),
                    detail.get("production") or "unknown", written_style, document_id))
                record_file.write(raw)
                counts[corpus.name] += 1
            if record_file:
                record_file.flush()
            packs.sync()
            db.commit()
            (output / "progress.json").write_text(encoded(dict(counts)))
        print(encoded({"corpus": corpus.name, "counts": dict(counts)}), flush=True)
    if record_file:
        record_file.close()
    packs.close()
    for corpus in found:
        counts["runs"] += record_corpus_runs(db, corpus)
        db.commit()
    db.close()
    print(encoded({"complete": True, "counts": dict(counts)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--records-only", type=Path, metavar="PUBLISHED",
                        help="write records only; PUBLISHED lists the media keys already in D1 "
                             "(an earlier export's corpus.sqlite, or a text file of keys)")
    parser.add_argument("--corpus", action="append", dest="corpora", metavar="NAME",
                        help="export only this unit corpus; repeat for several (default: every one)")
    parser.add_argument("--skip", type=Path, metavar="IDS",
                        help="leave out the unit ids listed in IDS, one per line: the corpus units "
                             "the site already holds, so a publication adds units without replacing them")
    parser.add_argument("--holder-images", action="store_true",
                        help="point units on IIIF pages at the holder's region instead of packing a crop")
    args = parser.parse_args()
    export(args.output, resume=args.resume, corpora=args.corpora,
           published=published_keys(args.records_only) if args.records_only else None,
           skip=unit_ids(args.skip) if args.skip else frozenset(), holder_images=args.holder_images)
