"""Bounded, restartable extraction from transcribed page rectangles.

Each page is committed as an immutable dataset. The review store imports completed
pages separately; this worker never rewrites its baseline or review history. `Queue.claim`
takes pending work coverage-first: a page holding characters the atlas still lacks is claimed
ahead of a page of characters it already has plenty of, as `Queue.prioritize` scores it; see
`Queue`'s docstring for the full claim order.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import align, images, tables
from .schema import Classification, ReviewState, UnitKind

POLICY = "single-character-consensus-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


#: How often a page is extracted, or published, before it is left failed for a person to look at.
MAX_ATTEMPTS = 3


def scorable_chars(text: str) -> set[str]:
    """The distinct characters of `text` worth prioritizing: no whitespace, punctuation or marks.

    Whitespace (including the common full-width U+3000) and every Unicode punctuation category
    (P*) carry no glyph to extract. Combining marks and variation selectors (Unicode category
    M*, which includes both) never stand alone as their own crop, so they are skipped too; see
    also `single_character` in `.review.atlas` for a related, string-level check.
    """
    return {c for c in text if not c.isspace() and not unicodedata.category(c).startswith(("P", "M"))}


def load_char_counts(path: Path) -> dict[str, int]:
    """Crops the atlas already holds per character, from a corpus-index characters table.

    Reads only the `char` and `n_units` columns of the parquet table at `path` (by default
    `work/corpus-index/chars.parquet`); the caller decides which path to pass to `Queue.prioritize`.
    """
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["char", "n_units"])
    return dict(zip(table.column("char").to_pylist(), table.column("n_units").to_pylist(), strict=True))


class Queue:
    """A durable, restartable queue of pages to extract, claimed one at a time.

    `claim` orders pending work by coverage priority first — a page scores higher the more its
    characters are still scarce in the atlas, as `prioritize` computes it — then by whether its
    image is already cached, then by the page's original rank within its book, then by document
    and page id as a stable tie-break for equal scores.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "queue.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL,
            source TEXT NOT NULL, cached INTEGER NOT NULL, rank INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            output TEXT, accepted INTEGER NOT NULL DEFAULT 0, examined INTEGER NOT NULL DEFAULT 0,
            error TEXT, updated_at TEXT)""")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(pages)")}
        for name in ("published_at", "publish_error", "retry_after"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE pages ADD COLUMN {name} TEXT")
        if "publish_attempts" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN publish_attempts INTEGER NOT NULL DEFAULT 0")
        if "priority" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN priority REAL NOT NULL DEFAULT 0")
        self.db.execute("""CREATE INDEX IF NOT EXISTS idx_pages_claim_order
            ON pages(priority DESC, cached DESC, rank, document_id, id)""")
        self.db.commit()

    def seed(self, source: Path, *, include_ainu=False):
        dataset = tables.Dataset(source)
        documents = {d.id: d for d in dataset.read("documents")}
        # Read the cache index once, rather than rescanning it for 79,000 pages.
        import pyarrow.parquet as pq
        index = images.index_path()
        records = pq.read_table(index).to_pylist() if index.exists() else []
        cached = {r[k] for r in records for k in ("url", "service") if r.get(k)}
        groups = defaultdict(list)
        for page in dataset.read("pages"):
            document = documents[page.document_id]
            if not include_ainu and any(s in document.title for s in ("蝦夷", "北海随筆", "アイヌ", "藻汐")):
                continue
            groups[page.document_id].append(page)
        before = self.db.total_changes
        with self.db:
            for document_id, pages in groups.items():
                for rank, page in enumerate(sorted(pages, key=lambda p: (p.seq, p.id))):
                    self.db.execute("""INSERT OR IGNORE INTO pages
                        (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)""",
                        (page.id, document_id, documents[document_id].title,
                         str(source), int(page.image in cached), rank))
        return self.db.total_changes - before

    def claim(self):
        """Take the next page to extract, in the order this class's docstring describes."""
        with self.db:
            row = self.db.execute("""SELECT * FROM pages WHERE status='pending' OR
                (status='retry' AND CAST(retry_after AS REAL)<=?)
                ORDER BY priority DESC, cached DESC, rank, document_id, id LIMIT 1""", (time.time(),)).fetchone()
            if row:
                self.db.execute("UPDATE pages SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",
                                (datetime.now(UTC).isoformat(), row["id"]))
        return dict(row) if row else None

    def prioritize(self, source: Path, counts: dict[str, int]) -> int:
        """Score every pending or retry page by how much the atlas still lacks its characters.

        A page's score is the sum, over the distinct scorable characters in its transcription
        lines, of `1 / (1 + counts.get(char, 0))`: a character with no crops yet in `counts`
        contributes 1, one the atlas already has plenty of contributes close to 0. Whitespace,
        punctuation and combining marks are not scored. `counts` maps a character to the crops
        the atlas already holds for it, typically loaded from a corpus-index characters table
        with `load_char_counts`; `source` is the transcribed-lines dataset (e.g. `work/honkoku-lines`)
        that `seed` read pages from. Complete, running and failed pages are left alone, and only
        `page_id` and `text` are read from the lines table to keep this cheap at tens of thousands
        of pages. Returns how many pages were scored.
        """
        import pyarrow.dataset as ds

        pending = [r[0] for r in self.db.execute(
            "SELECT id FROM pages WHERE status IN ('pending','retry')")]
        if not pending:
            return 0
        chars_by_page: dict[str, set[str]] = {page_id: set() for page_id in pending}
        lines_path = Path(source) / "lines"
        if not lines_path.exists():
            lines_path = Path(source) / "lines.parquet"
        scanner = ds.dataset(lines_path, format="parquet").scanner(columns=["page_id", "text"])
        for batch in scanner.to_batches():
            for page_id, text in zip(batch.column("page_id").to_pylist(),
                                     batch.column("text").to_pylist(), strict=True):
                found = chars_by_page.get(page_id)
                if found is not None and text:
                    found.update(scorable_chars(text))
        scores = [(sum(1 / (1 + counts.get(ch, 0)) for ch in chars), page_id)
                  for page_id, chars in chars_by_page.items()]
        with self.db:
            self.db.executemany("UPDATE pages SET priority=? WHERE id=?", scores)
        return len(scores)

    def recover(self):
        """Return pages a stopped worker left running, and stop retrying one that keeps stopping it.

        A page that kills the process outright never reaches `fail`, so its attempts are counted
        here; without that it would be claimed first after every restart and nothing else would run.
        """
        now = datetime.now(UTC).isoformat()
        with self.db:
            self.db.execute("""UPDATE pages SET status='failed',updated_at=?,
                error='the worker stopped while extracting this page ' || attempts || ' times'
                WHERE status='running' AND attempts>=?""", (now, MAX_ATTEMPTS))
            self.db.execute("UPDATE pages SET status='pending' WHERE status='running'")

    def finish(self, ident, report, output):
        with self.db:
            self.db.execute("""UPDATE pages SET status='complete',output=?,accepted=?,examined=?,
                error=NULL,updated_at=? WHERE id=?""",
                (str(output.relative_to(self.root)), report["accepted"], report["examined"],
                 datetime.now(UTC).isoformat(), ident))

    def fail(self, ident, reason, *, retryable=False):
        attempts = self.db.execute("SELECT attempts FROM pages WHERE id=?",(ident,)).fetchone()[0]
        status = "retry" if retryable and attempts < MAX_ATTEMPTS else "failed"
        retry_after = time.time() + 30 * 2**max(0, attempts-1) if status == "retry" else None
        with self.db:
            self.db.execute("UPDATE pages SET status=?,error=?,updated_at=?,retry_after=? WHERE id=?",
                            (status,reason, datetime.now(UTC).isoformat(),retry_after,ident))

    def status(self, *, state="idle", error=None):
        counts = dict(self.db.execute("SELECT status,count(*) FROM pages GROUP BY status"))
        result = {"policy": POLICY, "state": state, "worker_error":error, "counts": counts,
                  "character_crops": self.db.execute("SELECT coalesce(sum(accepted),0) FROM pages").fetchone()[0],
                  "published_pages": self.db.execute("SELECT count(*) FROM pages WHERE published_at IS NOT NULL").fetchone()[0],
                  "published_crops": self.db.execute("SELECT coalesce(sum(accepted),0) FROM pages WHERE published_at IS NOT NULL").fetchone()[0],
                  "publication_failures": self.db.execute("SELECT count(*) FROM pages WHERE publish_error IS NOT NULL").fetchone()[0],
                  "books_with_crops": self.db.execute("SELECT count(DISTINCT document_id) FROM pages WHERE accepted>0").fetchone()[0],
                  "updated_at": datetime.now(UTC).isoformat(),
                  "recent": [dict(r) for r in self.db.execute("""SELECT id,title,status,accepted,examined,output,error,published_at,publish_error,retry_after
                      FROM pages WHERE status!='pending' ORDER BY updated_at DESC LIMIT 12""")]}
        atomic_json(self.root / "status.json", result)
        return result


def quality_reason(unit, votes, size):
    """Conservative publication gate. Both independent visual models must agree."""
    if unit.review != ReviewState.MACHINE:
        return "alignment-uncertain"
    expected = unicodedata.normalize("NFC", unit.text_source or "")
    if unit.kind != UnitKind.CHAR or unit.granularity != "char" or len(expected) != 1:
        return "not-one-character"
    b = unit.box
    if b is None or min(b.w, b.h) < 8 or b.x < 0 or b.y < 0 or b.x+b.w > size[0] or b.y+b.h > size[1]:
        return "invalid-geometry"
    if not .25 <= b.w / b.h <= 2.2:
        return "elongated-crop"
    by_engine = {v["engine"]: v for v in votes}
    for name, threshold in (("Atlas classifier", .80), ("NDLkotenOCR", .70)):
        vote = by_engine.get(name)
        if (not vote or vote.get("identity_scope", "character") != "character"
                or unicodedata.normalize("NFC", vote.get("text") or "") != expected):
            return "visual-disagreement"
        score = vote.get("score")
        if (not isinstance(score, (float, int)) or isinstance(score, bool)
                or not math.isfinite(score) or not 0 <= score <= 1 or score < threshold):
            return "visual-uncertain"
    return None


def overlaps(a, b):
    area = max(0, min(a.x+a.w,b.x+b.w)-max(a.x,b.x))*max(0,min(a.y+a.h,b.y+b.h)-max(a.y,b.y))
    return area / min(a.w*a.h,b.w*b.h) >= .5


def unique_units(units):
    """Reject all overlapping candidates, including conflicts from adjacent line boxes."""
    bad = set()
    for i, a in enumerate(units):
        for j in range(i):
            if overlaps(a.box, units[j].box):
                bad.update((i,j))
    return [u for i,u in enumerate(units) if i not in bad], len(bad)


def source_dimensions(page, *, info_loader=None):
    """Line rectangles are in the original image coordinate space, never inferred from a cache."""
    if page.width > 0 and page.height > 0:
        dimensions = (page.width, page.height)
    else:
        if "?IIIF=" in page.image:
            import tempfile

            from . import net
            base = page.image.rsplit("/", 4)[0]
            with tempfile.TemporaryDirectory(prefix="atlas-source-info-") as scratch:
                path = Path(scratch)/"info.json"
                net.download(base+"/info.json",path,expected="json")
                info = json.loads(path.read_text())
            dimensions = (int(info["width"]),int(info["height"]))
        elif images.service_of(page.image) is None:
            raise ValueError("original page dimensions unavailable; extraction withheld")
        else:
            info = (info_loader or images.info)(page.image)
            dimensions = (int(info["width"]), int(info["height"]))
    if min(dimensions) <= 0 or dimensions[0]*dimensions[1] > 40_000_000:
        raise ValueError("original page exceeds 40 megapixel resource cap")
    return dimensions


def check_coordinate_space(original, cached):
    if tuple(original) != tuple(cached):
        raise ValueError("cached image dimensions differ from source coordinates; extraction withheld")


class Engine:
    def __init__(self):
        import onnxruntime as ort

        from .detect import Detector
        from .review.suggestions import Recognizer
        self.run = align.load_run(Path("models/align/runs/pilot-v1.yaml"), "collection-v1")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("CUDA provider required for this bounded worker")
        ort.preload_dlls()
        session = ort.InferenceSession(self.run.detector, sess_options=options, providers=[
            ("CUDAExecutionProvider", {"gpu_mem_limit":1024*1024*1024,
                                      "arena_extend_strategy":"kSameAsRequested"}), "CPUExecutionProvider"])
        if session.get_providers()[0] != "CUDAExecutionProvider":
            raise RuntimeError("detector CUDA initialization failed")
        self.detector = Detector(self.run.detector, score=self.run.score, nms=self.run.nms, session=session)
        self.reader = Recognizer()  # two capped 768 MiB sessions, classifier shared with alignment
        if self.reader.sequence is None or self.reader.classifier is None:
            raise RuntimeError("both sequence and single-character models are required")
        if any(e["provider"] != "CUDAExecutionProvider" for e in self.reader.engines):
            raise RuntimeError("recognizer CUDA initialization failed")
        self.classifier = self.reader.classifier
        self.models = {"detector": hashlib.sha256(Path(self.run.detector).read_bytes()).hexdigest(),
                       "recognizers":self.reader.engines,
                       "classifier_classes":digest(self.classifier.classes),
                       "sequence_alphabet":digest(self.reader.alphabet)}

    def extract(self, job, root, *, max_lines=64):
        from PIL import Image
        dataset = tables.Dataset(Path(job["source"]))
        page = next(p for p in dataset.read("pages") if p.id == job["id"])
        document = next(d for d in dataset.read("documents") if d.id == page.document_id)
        lines = [line for batch in dataset.scan("lines", keep=tables.In("page_id", {page.id}))
                 for line in batch if line.box is not None]
        lines = sorted(lines, key=lambda x:(x.seq,x.id))
        if len(lines) > max_lines:
            raise ValueError("page exceeds transcription-line resource cap")
        if not lines:
            raise ValueError("page has no located transcription lines")
        original_dimensions = source_dimensions(page)
        path = images.path_for(page.image)
        if path is None:
            images.fetch(page.image)
            path = images.path_for(page.image)
        if path is None:
            raise ValueError("page image unavailable")
        with Image.open(path) as handle:
            check_coordinate_space(original_dimensions, handle.size)
            image = handle.convert("RGB")
        page = page.model_copy(update={"width":image.width,"height":image.height,
                                       "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "meta":{**page.meta,"extraction_source_dimensions":list(original_dimensions)}})
        located_line_count = len(lines)
        lines = [line for line in lines if line.box.x >= 0 and line.box.y >= 0
                 and line.box.x+line.box.w <= image.width and line.box.y+line.box.h <= image.height]
        def crop_of(page_id, box):
            return image.crop((box.x,box.y,box.x+box.w,box.y+box.h))
        candidates = []
        reasons = Counter()
        examined = 0
        identity = digest({"policy":POLICY,"models":self.models,"run":self.run.model_dump(),
                           "page":page.model_dump(),"document":document.model_dump(),
                           "lines":[l.model_dump() for l in lines]})
        output = root/"pages"/identity
        if output.exists():
            return committed_report(output, identity, page), output
        detections = [align.Detection(box=box, score=score) for box,score in self.detector.boxes(image)]
        for line in lines:
            found, _ = align.align_line(line, detections, run=self.run, classifier=self.classifier,
                                        crop_of=crop_of)
            for unit in found:
                examined += 1
                prelim = quality_reason(unit, [], image.size)
                if prelim not in (None,"visual-disagreement"):
                    reasons[prelim] += 1
                    continue
                result = self.reader.read(crop_of(page.id, unit.box))
                reason = quality_reason(unit, result["votes"], image.size)
                if reason:
                    reasons[reason] += 1
                    continue
                label = unicodedata.normalize("NFC", unit.text_source)
                unit = unit.model_copy(update={
                    "id":"ex:"+identity[:16]+":"+digest(unit.id)[:20],
                    "document_id":document.id,"unicode":f"U+{ord(label):04X}",
                    "classification":Classification.IDENTIFIED,"group_id":None,
                    "meta":{"extraction":{"policy":POLICY,"source_unit_id":unit.id,
                        "source_page_id":page.id,"source_line_id":line.id,"page_sha256":page.sha256,
                        "generation":identity,"visual_votes":result["votes"],
                        "verified":False,"quiz":True}},
                    "upstream":{**unit.upstream,"source":line.meta.get("source", "honkoku-lines"),
                                "extraction_policy":POLICY}})
                candidates.append(unit)
        align.clear_crop_cache()
        accepted, duplicates = unique_units(candidates)
        reasons["overlapping-crops"] += duplicates
        report = {"policy":POLICY,"generation":identity,"page_id":page.id,"document_id":document.id,
                  "title":document.title,"page_sha256":page.sha256,"models":self.models,
                  "examined":examined,"accepted":len(accepted),"withheld":dict(reasons),
                  "source_dimensions":list(original_dimensions),"lines":len(lines),"detected":len(detections),"complete_page":len(lines)==located_line_count,
                  "withheld_lines":{"invalid_geometry":located_line_count-len(lines)},
                  "created_at":datetime.now(UTC).isoformat()}
        output = root/"pages"/identity
        if not output.exists():
            stage = root/".staging"/identity
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True)
            records = {"documents":[document],"pages":[page],"lines":lines,"units":accepted}
            for name, rows in records.items():
                tables.write(stage/f"{name}.parquet", rows, tables.TABLES[name])
            issues = tables.Dataset(stage).validate()
            if issues:
                raise ValueError("invalid extracted dataset: "+"; ".join(issues[:3]))
            atomic_json(stage/"report.json",report)
            output.parent.mkdir(parents=True,exist_ok=True)
            for file in stage.glob("*.parquet"):
                with file.open("rb") as handle:
                    os.fsync(handle.fileno())
            stage.replace(output)
            descriptor = os.open(output.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return report, output


def committed_report(output, identity, page):
    report = json.loads((output/"report.json").read_text())
    if (report.get("generation") != identity or report.get("page_id") != page.id
            or report.get("page_sha256") != page.sha256):
        raise ValueError("committed extraction identity mismatch")
    dataset = tables.Dataset(output)
    if dataset.validate() or len(dataset.read("units")) != report.get("accepted"):
        raise ValueError("committed extraction tables mismatch")
    return report


def require_storage(root, *, minimum_gib=5):
    for path in {Path(root),images.images_root()}:
        if shutil.disk_usage(path).free < minimum_gib*2**30:
            raise OSError("extraction paused: less than 5 GiB free on data or image-cache volume")


def publish_completed(queue, store):
    """Retry insert-only imports after a crash, without repeating OCR or changing reviews.

    A page is tried at most `MAX_ATTEMPTS` times, and once only when the store refuses it outright,
    so a page that can never be imported does not have its crops rendered again on every pass.
    """
    from .review.media import prepare_dataset
    from .review.store import BadRequest, Conflict

    published = 0
    rows = list(queue.db.execute("""SELECT id,output FROM pages WHERE status='complete'
        AND published_at IS NULL AND publish_attempts<?""", (MAX_ATTEMPTS,)))
    for row in rows:
        try:
            prepare_dataset(queue.root / row["output"])
            store.import_dataset(queue.root / row["output"])
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            attempts = MAX_ATTEMPTS if isinstance(exc, BadRequest | Conflict) else None
            with queue.db:
                queue.db.execute("""UPDATE pages SET publish_error=?,
                    publish_attempts=COALESCE(?, publish_attempts + 1) WHERE id=?""",
                                 (type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500],
                                  attempts, row["id"]))
            continue
        with queue.db:
            queue.db.execute("UPDATE pages SET published_at=?,publish_error=NULL WHERE id=?",
                             (datetime.now(UTC).isoformat(),row["id"]))
        published += 1
    return published


def run(queue, engine, *, pages=3, seconds=600, pause=10, max_lines=64, store=None):
    started = time.monotonic()
    queue.recover()  # caller holds the exclusive worker lock
    if store is not None:
        publish_completed(queue, store)
    queue.status(state="running")
    done = 0
    while done < pages and time.monotonic()-started < seconds:
        try:
            require_storage(queue.root)
            if store is not None:
                require_storage(store.directory)
        except OSError:
            return queue.status(state="paused-low-storage")
        job = queue.claim()
        if job is None:
            break
        try:
            report, output = engine.extract(job, queue.root, max_lines=max_lines)
            queue.finish(job["id"],report,output)
        except Exception as exc:  # noqa: BLE001 — persist a failed page and keep the bounded queue moving
            # Avoid leaking local paths from exception text into durable status.
            import httpx

            from . import net
            queue.fail(job["id"], type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500],
                       retryable=isinstance(exc,(net.DownloadError,httpx.HTTPError,OSError)))
        if store is not None:
            publish_completed(queue, store)
        done += 1
        queue.status(state="running")
        if done < pages and time.monotonic()-started+pause < seconds:
            time.sleep(pause)
    return queue.status()
