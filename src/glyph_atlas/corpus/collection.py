"""Publish collected Honkoku books as a replacement generation of the source tables."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .. import tables


def promote_file(source: Path, destination: Path) -> None:
    """Atomically install an index even when collection storage is on another volume."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".publish-", delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def archive_statistics(root: Path) -> dict:
    """Count renderable single-character rows once per corpus family, outside HTTP requests."""
    from .index import _character_unit_row
    from .sources import TEXT_COLUMNS, discover, documents_of

    corpora = discover(root)
    has_full_codh = any(corpus.name == "codh-full" for corpus in corpora)
    total = 0
    crop_works = set()
    text_works = set()
    sources = {}
    for corpus in corpora:
        if corpus.name == "codh" and has_full_codh:
            continue
        documents = documents_of(corpus)

        work_keys = {}
        for ident, document in documents.items():
            refs = document.get("source_refs") or {}
            if isinstance(refs, str):
                refs = json.loads(refs)
            if isinstance(refs, list):
                refs = dict(refs)
            canonical = refs.get("honkoku-data") if corpus.id_family == "honkoku-platform" else None
            work_keys[ident] = (corpus.id_family, canonical or ident)

        if corpus.has_page_texts or corpus.has_lines:
            page_documents = {}
            for file in corpus.parquet_files("pages"):
                for batch in pq.ParquetFile(file).iter_batches(batch_size=8192, columns=["id", "document_id"]):
                    page_documents.update(zip(batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True))
            for name in ("page_texts", "lines"):
                for file in corpus.parquet_files(name):
                    parquet = pq.ParquetFile(file)
                    names = parquet.schema_arrow.names
                    text_column = next((key for key in TEXT_COLUMNS[name] if key in names), None)
                    if text_column is None:
                        continue
                    columns = [key for key in ("document_id", "page_id", text_column) if key in names]
                    for batch in parquet.iter_batches(batch_size=8192, columns=columns):
                        for row in batch.to_pylist():
                            text = row.get(text_column)
                            ident = row.get("document_id") or page_documents.get(row.get("page_id"))
                            if ident and isinstance(text, str) and text.strip():
                                text_works.add(work_keys.get(ident, (corpus.id_family, ident)))
        count = 0
        works = set()
        for file in corpus.parquet_files("units"):
            parquet = pq.ParquetFile(file)
            columns = [name for name in ("document_id", "unicode", "active", "box", "crop",
                       "granularity", "kind", "text_source", "reading") if name in parquet.schema_arrow.names]
            for batch in parquet.iter_batches(batch_size=8192, columns=columns):
                for row in batch.to_pylist():
                    if _character_unit_row(row):
                        count += 1
                        if row.get("document_id"):
                            ident = row["document_id"]
                            works.add(work_keys.get(ident, (corpus.id_family, ident)))
        total += count
        crop_works.update(works)
        sources[corpus.name] = {"character_crops": count, "works_with_crops": len(works)}
    result = {"character_crops": total, "works_with_crops": len(crop_works),
              "text_works": len(text_works), "sources": sources,
              "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    destination = root / "corpus-index" / "archive.json"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result)+"\n")
    os.replace(temporary, destination)
    return result


@contextmanager
def publication_lock(collection):
    with (collection / "publish.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _merge_table(base: Path, additions: list[Path], target: Path, name: str, ids: set[str],
                 changed_chars: set[str], *, by_row: bool = False) -> int:
    schema = tables.schema_for(tables.TABLES[name])
    identity = "id" if name == "documents" or (name == "pages" and by_row) else "document_id" if name == "pages" else "page_id"
    if by_row:
        ids = set()
        for path in additions:
            ids.update(pq.read_table(path / f"{name}.parquet", columns=[identity])[identity].to_pylist())
    count = 0
    with pq.ParquetWriter(target, schema, compression="zstd") as writer:
        if base.is_file():
            for batch in pq.ParquetFile(base).iter_batches(batch_size=4096):
                table = pa.Table.from_batches([batch])
                values = table[identity].to_pylist()
                keep = [((value if by_row or name != "page_texts" else value.rsplit(":", 1)[0]) not in ids)
                        for value in values]
                if name == "page_texts":
                    for kept, text in zip(keep, table["text_raw"].to_pylist(), strict=True):
                        if not kept:
                            changed_chars.update(text or "")
                table = table.filter(pa.array(keep)).cast(schema)
                writer.write_table(table)
                count += table.num_rows
        for path in additions:
            for batch in pq.ParquetFile(path / f"{name}.parquet").iter_batches(batch_size=4096):
                table = pa.Table.from_batches([batch]).cast(schema)
                if name == "page_texts":
                    for text in table["text_raw"].to_pylist():
                        changed_chars.update(text or "")
                writer.write_table(table)
                count += table.num_rows
    return count


def publish(root: Path, *, source: str = "honkoku", rebuild_index: bool = True,
            min_free_bytes: int = 5 * 1024**3) -> dict:
    if source not in {"honkoku", "wikisource"}:
        raise ValueError("Unknown collected source")
    corpus_name = "honkoku-data" if source == "honkoku" else "wikisource"
    collection = root / f"{source}-collection"
    collection.mkdir(parents=True, exist_ok=True)
    with publication_lock(collection):
        if shutil.disk_usage(collection).free < min_free_bytes:
            raise RuntimeError("Collection publication paused: less than 5 GiB free")
        listing = json.loads((collection / "index.json").read_text())
        records = listing.get("books", [])
        if not records:
            return {"published": 0}
        # The collector index only lists committed books. Pin that exact set throughout the build.
        additions = [collection / row["dataset"] for row in records]
        ids = {"hk:" + row["entry_id"] for row in records}
        generation = collection / "generations" / str(time.time_ns())
        generation.mkdir(parents=True)
        base = root / corpus_name
        base.mkdir(exist_ok=True)
        counts = {}
        changed_chars: set[str] = set()
        try:
            for name in ("documents", "pages", "page_texts"):
                counts[name] = _merge_table(base / f"{name}.parquet", additions,
                                            generation / f"{name}.parquet", name, ids, changed_chars,
                                            by_row=source == "wikisource")
            manifest = {"schema_version": tables.SCHEMA_VERSION, "tables": counts,
                        "command": f"publish collected {source} transcriptions",
                        "collected_books": len(ids)}
            (generation / "MANIFEST.json").write_text(json.dumps(manifest)+"\n")
            # Build against a pinned staging root; the live sources change only when it succeeds.
            index_stage = generation / "index"
            if rebuild_index:
                from .index import build_chars
                staging_root = generation / "sources"
                staging_root.mkdir()
                for child in root.iterdir():
                    if child.is_dir() and child.name != collection.name:
                        dest = generation if child.name == corpus_name else child.resolve()
                        (staging_root / child.name).symlink_to(dest.resolve(), target_is_directory=True)
                build_chars(staging_root, index_stage)
                # Persist a portable root string; provenance never needs a local username.
                meta_path = index_stage / "index.json"
                meta = json.loads(meta_path.read_text())
                meta["root"] = "work"
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+"\n")
                shutil.rmtree(staging_root)
            current = collection / "current"
            link = collection / ".current-next"
            link.unlink(missing_ok=True)
            link.symlink_to(generation.relative_to(collection), target_is_directory=True)
            os.replace(link, current)
            if rebuild_index:
                index_dir = root / "corpus-index"
                index_dir.mkdir(exist_ok=True)
                for name in ("chars.parquet", "index.json"):
                    promote_file(index_stage / name, index_dir / name)
                shutil.rmtree(index_stage)
                from .index import OCC_DIR
                from .occurrence import codepoint
                # Keep unrelated rare-character searches warm.
                for char in changed_chars:
                    (index_dir / OCC_DIR / f"{codepoint(char)}.parquet").unlink(missing_ok=True)
            report = {"source": source, "published": len(records), "tables": counts, "at": time.time()}
            (collection / "published.json.tmp").write_text(json.dumps(report)+"\n")
            os.replace(collection / "published.json.tmp", collection / "published.json")
            # The exact committed generation, not whatever the collector finishes next.
            queue_path = collection / "queue.sqlite"
            if source == "honkoku" and queue_path.exists():
                from ..importers.honkoku_queue import Queue
                Queue(queue_path).mark_published([row["entry_id"] for row in records],
                                                generation=int(generation.name))
            # Keep the previous generation for readers already using its paths.
            older = sorted((collection / "generations").iterdir(), key=lambda p: p.name)
            for path in older[:-2]:
                shutil.rmtree(path)
            return report
        except Exception:
            if not (collection / "current").exists() or (collection / "current").resolve() != generation.resolve():
                shutil.rmtree(generation, ignore_errors=True)
            raise
