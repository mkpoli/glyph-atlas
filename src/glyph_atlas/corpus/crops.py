"""Controlled access to glyph crop bytes.

A gallery needs actual pixels, and "actual pixels" is exactly where a viewer turns
into an arbitrary-file-read. So resolution here is **by registered identity only**:

* a caller passes a unit id or an occurrence id, never a path;
* the id is looked up in the corpus tables, and the byte source comes from the row;
* the resolved file must sit under an allow-listed root, checked after ``resolve()``
  so ``..`` and symlinks cannot escape;
* the bytes must look like an image, and the licence must permit serving them.

Where no bytes exist locally, the resolver says so and names a *render path* instead
of pretending: a IIIF region URL the browser can fetch from the holder, or the
holder's record page. ``render_available`` is true only when one of those is real.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import sources as corpus_sources

#: Where a corpus's local page images live, relative to the corpus root. Scoped per
#: corpus on purpose: one corpus's image directory must not become another's.
CORPUS_IMAGE_DIRS = {
    "kokatsuji": "kokatsuji/images",
    "codh-full": "codh-full/images",
    "codh": "codh/images",
}

#: Where an extracted HI Lab archive would be. Its units name members, not files.
HILAB_EXTRACTION = "cache/hilab"

#: Longest edge served for a glyph crop, and the ceiling a caller may request.
DEFAULT_CROP_EDGE = 256
MAX_CROP_EDGE = 1024
#: Largest file this will open, so a mislabelled row cannot stream a huge file out.
MAX_SOURCE_BYTES = 64 * 1024 * 1024

MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
}


def sniff(data: bytes) -> str | None:
    """The media type of `data` from its leading bytes, or None."""
    for magic, media in MAGIC.items():
        if data.startswith(magic):
            return media
    return None


@dataclass
class CropResult:
    """What can be shown for one glyph, and the basis for saying so."""

    unit_id: str | None
    occurrence_id: str | None
    #: ``local_crop`` bytes are served by this API; ``remote_iiif`` by the holder;
    #: ``record_page`` is a provenance page; ``none`` is nothing at all.
    mode: str
    render_available: bool
    licence: str | None = None
    proxyable: bool = False
    region: str | None = None
    media_type: str | None = None
    crop_url: str | None = None
    iiif_url: str | None = None
    record_url: str | None = None
    bytes_data: bytes | None = None
    sha256: str | None = None
    reason: str | None = None
    source_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        payload = {
            "unit_id": self.unit_id,
            "occurrence_id": self.occurrence_id,
            "mode": self.mode,
            "render_available": self.render_available,
            "licence": self.licence,
            "proxyable": self.proxyable,
            "region": self.region,
            "media_type": self.media_type,
            "crop_url": self.crop_url,
            "iiif_url": self.iiif_url,
            "record_url": self.record_url,
            "sha256": self.sha256,
            "reason": self.reason,
            "meta": self.meta,
        }
        if include_bytes:
            payload["bytes"] = len(self.bytes_data or b"")
        return payload


class CropResolver:
    """Resolves registered ids to crop bytes or to an honest refusal."""

    def __init__(
        self,
        root: str | Path = corpus_sources.DEFAULT_ROOT,
        *,
        file_bases: Sequence[str | Path] | None = None,
        cache_size: int = 64,
    ):
        self.root = Path(root)
        # `file:` references in the tables are relative to wherever the import ran, so
        # more than one base has to be tried. Each candidate is still allow-listed.
        self.file_bases = [Path(b) for b in (file_bases or (root, Path.cwd()))]
        self.cache_size = cache_size
        self._cache: dict[str, CropResult] = {}
        self._capability: dict[str, str] = {}
        self._verified: dict[str, dict[str, Any]] = {}
        self._page_paths: dict[str, Path | None] = {}
        self._members: dict[str, Path | None] = {}

    # ------------------------------------------------------------- allow list
    def image_dir(self, corpus_name: str) -> Path | None:
        """The directory this corpus's local page images live in, if it has one."""
        relative = CORPUS_IMAGE_DIRS.get(corpus_name)
        if relative is None:
            return None
        candidate = self.root / relative
        return candidate if candidate.is_dir() else None

    def allowed(self, candidate: Path, corpus_name: str | None = None) -> bool:
        """Whether a resolved path sits under an allow-listed root for this corpus.

        ``resolve()`` runs first, so ``..`` and symlinks are already collapsed and
        cannot point outside the list. When ``corpus_name`` is given, only that
        corpus's own image directory qualifies.
        """
        try:
            real = candidate.resolve()
        except OSError:
            return False
        if not real.is_file():
            return False
        try:
            if real.stat().st_size > MAX_SOURCE_BYTES:
                return False
        except OSError:
            return False
        basenames = (
            [CORPUS_IMAGE_DIRS[corpus_name]]
            if corpus_name in CORPUS_IMAGE_DIRS
            else list(CORPUS_IMAGE_DIRS.values())
        )
        for relative in basenames:
            for base in self.file_bases:
                directory = (base / relative) if not str(relative).startswith("/") else Path(relative)
                try:
                    real.relative_to(directory.resolve())
                    return True
                except (ValueError, OSError):
                    continue
        return False

    # ------------------------------------------------------------ capability
    def capability(self, corpus: corpus_sources.Corpus) -> str:
        """How this corpus's glyphs can be shown, probed once per corpus.

        Probed at the corpus level rather than per row: whether Kokatsuji's page
        images were extracted, or whether the HI Lab archive has been unpacked, is a
        property of the import, not of an individual glyph.
        """
        if corpus.name in self._capability:
            return self._capability[corpus.name]
        mode = "none"
        # Scoped to this corpus's own image directory.
        directory = self.image_dir(corpus.name)
        if directory is not None and any(directory.glob("*")):
            mode = "local_crop"
        if mode == "none" and corpus.name == "hilab":
            mode = "archive_member"
        if mode == "none":
            # No local bytes. A units row still often carries a IIIF service through
            # its page, which the browser can fetch from the holder.
            pages = corpus.table("pages")
            if pages is not None:
                import pyarrow.parquet as pq

                try:
                    head = next(pq.ParquetFile(pages).iter_batches(batch_size=4, columns=["image"]))
                    if any(str(v or "").startswith("http") for v in head.to_pydict()["image"]):
                        mode = "remote_iiif"
                except (StopIteration, KeyError, OSError):
                    pass
        if mode == "none" and corpus.name == "hilab":
            mode = "record_page"
        self._capability[corpus.name] = mode
        return mode

    def verify(self, corpus: corpus_sources.Corpus) -> dict[str, Any]:
        """Prove the capability by resolving one real unit end to end.

        Probed once per corpus and cached. A capability claim that has not produced
        bytes at least once is a guess, and a gallery built on a guess shows blanks.
        """
        if corpus.name in self._verified:
            return self._verified[corpus.name]
        import pyarrow.dataset as ds

        from .index import _renderable_unit_row

        verdict: dict[str, Any] = {
            "mode": self.capability(corpus),
            "verified": False,
            "unit_id": None,
            "reason": None,
        }
        files = corpus.parquet_files("units")
        if files:
            dataset = ds.dataset([str(f) for f in files], format="parquet")
            columns = [c for c in ("id", "box", "crop", "unicode") if c in dataset.schema.names]
            for batch in dataset.scanner(columns=columns).to_batches():
                for row in batch.to_pylist():
                    if not _renderable_unit_row(row):
                        continue
                    probe = self.for_unit(row["id"], edge=64)
                    verdict.update(
                        {
                            "unit_id": row["id"],
                            "verified": probe.render_available,
                            "mode": probe.mode,
                            "reason": probe.reason,
                        }
                    )
                    break
                if verdict["unit_id"]:
                    break
        self._verified[corpus.name] = verdict
        return verdict

    # ---------------------------------------------------------------- resolve
    def resolve(self, result: CropResult, edge: int = DEFAULT_CROP_EDGE) -> CropResult:
        """Fill in servable bytes or a render path for `result`."""
        edge = max(32, min(MAX_CROP_EDGE, edge))
        if not result.proxyable:
            result.reason = result.reason or (
                f"licence {result.licence!r} does not permit serving this image"
            )
            result.render_available = False
            mode = result.mode
            # A non-proxyable image can still be *linked to* at the holder.
            if mode == "local_crop":
                result.mode = "holder_only"
            return result
        return result

    # ------------------------------------------------------------- page crop
    def local_page_crop(
        self,
        image: str | None,
        box: dict[str, int] | None,
        edge: int = DEFAULT_CROP_EDGE,
        corpus_name: str | None = None,
    ) -> tuple[bytes | None, str | None, str | None]:
        """Crop `box` out of a locally held page image.

        Returns ``(bytes, media_type, reason)``. The path comes from the corpus row
        and is resolved by `page_image_path`, which applies the allow list.
        """
        candidate = self.page_image_path(image, corpus_name)
        if candidate is None:
            return (
                None,
                None,
                (
                    "page image is not held locally"
                    + (f" for {corpus_name}" if corpus_name else "")
                ),
            )
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            return None, None, "Pillow is unavailable"
        try:
            with Image.open(candidate) as handle:
                handle.load()
                box = box or {"x": 0, "y": 0, "w": handle.width, "h": handle.height}
                x, y = max(0, box["x"]), max(0, box["y"])
                right = min(handle.width, x + max(1, box["w"]))
                bottom = min(handle.height, y + max(1, box["h"]))
                if right <= x or bottom <= y:
                    return None, None, "box does not intersect the page image"
                patch = handle.crop((x, y, right, bottom)).convert("RGB")
                scale = min(1.0, edge / max(patch.width, patch.height))
                if scale < 1.0:
                    patch = patch.resize(
                        (max(1, int(patch.width * scale)), max(1, int(patch.height * scale)))
                    )
                buffer = io.BytesIO()
                patch.save(buffer, format="JPEG", quality=88)
            return buffer.getvalue(), "image/jpeg", None
        except (OSError, ValueError) as exc:
            return None, None, f"could not crop the page image: {type(exc).__name__}"

    def archive_member(self, crop: str | None) -> Path | None:
        """The extracted file behind an ``<archive>!<member>`` crop reference."""
        if not crop or "!" not in crop:
            return None
        archive, member = crop.split("!", 1)
        if archive != "all.zip" or not re.fullmatch(
            r"all/characters/U\+[0-9A-Fa-f]{4,6}/[0-9]+\.jpg", member
        ):
            return None
        # The importer writes standalone crops below cache/hilab, not a page-image
        # directory. It may run from the corpus root or the repository root.
        for base in dict.fromkeys((self.root, *self.file_bases)):
            directory = base / HILAB_EXTRACTION
            try:
                candidate = (directory / member).resolve()
                candidate.relative_to(directory.resolve())
                if candidate.is_file() and candidate.stat().st_size <= MAX_SOURCE_BYTES:
                    return candidate
            except (ValueError, OSError):
                continue
        return None

    # ------------------------------------------------- per-row availability
    def page_image_path(self, image: str | None, corpus_name: str | None) -> Path | None:
        """The local file behind a page image, if it is servable.

        A ``file:`` reference resolves under this corpus's image directory. A holder URL
        resolves to the full-size scan the image cache holds for it, which the cache itself
        keeps current.

        A ``file:`` result is cached by the image reference, which every unit of a page
        shares, so a page of results costs one check rather than one per row.
        """
        if not image:
            return None
        if image.startswith("https://"):
            return self._held_page(image)
        key = f"{corpus_name}|{image}"
        if key in self._page_paths:
            return self._page_paths[key]
        found: Path | None = None
        if image.startswith("file:"):
            raw = Path(image[len("file:") :])
            candidates = [raw] if raw.is_absolute() else [b / raw for b in self.file_bases]
            directory = self.image_dir(corpus_name or "")
            if directory is not None:
                candidates.append(directory / raw.name)
                candidates.append(directory / raw.parent.name / raw.name)
            candidates.append(self.root / raw)
            found = next((c for c in candidates if self.allowed(c, corpus_name)), None)
        if len(self._page_paths) > self.cache_size * 4:
            self._page_paths.clear()
        self._page_paths[key] = found
        return found

    def _held_page(self, url: str) -> Path | None:
        from .. import images

        path = images.held(url)
        try:
            if path is None or path.stat().st_size > MAX_SOURCE_BYTES:
                return None
        except OSError:
            return None
        return path

    def archive_member_path(self, crop: str | None) -> Path | None:
        """The extracted file behind an archive reference, if it is on disk."""
        if not crop or "!" not in crop:
            return None
        if crop in self._members:
            return self._members[crop]
        member = self.archive_member(crop)
        if len(self._members) > self.cache_size * 4:
            self._members.clear()
        # A bounded collector can extract a missing member while the API is live.
        if member is not None:
            self._members[crop] = member
        return member

    def row_availability(
        self, row: dict[str, Any], corpus_name: str | None, page: dict[str, Any] | None = None
    ) -> tuple[bool, str | None, str]:
        """Whether this exact unit row can be rendered, and why not when it cannot.

        Corpus capability decides *how*; the row's own fields decide *whether*. A unit
        with a box on a page whose image was never extracted is not renderable, and
        saying otherwise would advertise a tile that renders blank.
        """
        box = row.get("box")
        has_box = bool(box and box.get("w") and box.get("h"))
        crop = row.get("crop")
        if not has_box and not crop:
            return False, "unit has neither a box nor a crop", "none"
        mode = self.capability_of(corpus_name)
        image = (page or {}).get("image") or row.get("image")
        if crop and "!" in str(crop):
            if corpus_name != "hilab":
                return False, "archive crops are not registered for this corpus", "none"
            member = self.archive_member_path(str(crop))
            if member is not None:
                return True, None, "local_crop"
            archive = str(crop).split("!", 1)[0]
            reason = f"crop bytes are not present: {archive} has not been extracted to {HILAB_EXTRACTION}"
            return False, reason, "archive_member"
        if has_box and image and str(image).startswith("file:"):
            if self.page_image_path(str(image), corpus_name) is not None:
                return True, None, "local_crop"
            return False, "page image is outside the served crop roots", mode
        if has_box and image and str(image).startswith("http"):
            from .api import PROXYABLE

            # Only an image this API may re-serve is cut from the held scan; any other
            # stays with the holder, which the browser loads directly.
            if row.get("image_licence") in PROXYABLE and self.page_image_path(str(image), corpus_name) is not None:
                return True, None, "local_crop"
            base = _service_base(image)
            if base:
                return True, None, "remote_iiif"
            return False, "page image is not an IIIF service", mode
        if crop:
            member = self.archive_member_path(str(crop))
            if member is not None:
                return True, None, "local_crop"
            return False, "crop file is not present", mode
        return False, "no local bytes and no IIIF service", mode

    def capability_of(self, corpus_name: str | None) -> str:
        return self._capability.get(corpus_name or "", "none")

    # ----------------------------------------------------------------- lookup
    @staticmethod
    def serve_url(unit_id: str, edge: int = DEFAULT_CROP_EDGE) -> str:
        return f"/api/corpus/crop?unit_id={quote(unit_id, safe='')}&w={edge}"

    def for_unit(self, unit_id: str, *, edge: int = DEFAULT_CROP_EDGE) -> CropResult:
        """Resolve one registered unit id to bytes or an honest refusal."""
        key = f"{unit_id}|{edge}"
        if key in self._cache:
            return self._cache[key]
        result = self._lookup(unit_id, edge)
        if len(self._cache) > self.cache_size:
            self._cache.pop(next(iter(self._cache)))
        if result.render_available:
            self._cache[key] = result
        return result

    def _lookup(self, unit_id: str, edge: int) -> CropResult:
        import pyarrow.dataset as ds

        from .index import UNIT_CORPORA, _MetaCache

        for corpus in corpus_sources.discover(self.root):
            if corpus.name not in UNIT_CORPORA:
                continue
            files = corpus.parquet_files("units")
            if not files:
                continue
            dataset = ds.dataset([str(f) for f in files], format="parquet")
            if "id" not in dataset.schema.names:
                continue
            table = dataset.to_table(filter=ds.field("id") == unit_id)
            if not table.num_rows:
                continue
            row = table.to_pylist()[0]
            context = _MetaCache(corpus)
            doc = context.document(row.get("document_id") or "")
            page = context.page(row.get("page_id") or "")
            rights = doc.get("image_rights") or {}
            if isinstance(rights, str):
                try:
                    import json

                    rights = json.loads(rights)
                except ValueError:
                    rights = {}
            licence = rights.get("licence")
            from .api import PROXYABLE

            mode = self.capability(corpus)
            box = row.get("box")
            box = {k: box[k] for k in ("x", "y", "w", "h")} if box and box.get("w") and box.get("h") else None
            region = f"{box['x']},{box['y']},{box['w']},{box['h']}" if box else None
            result = CropResult(
                unit_id=unit_id,
                occurrence_id=None,
                mode=mode,
                render_available=False,
                licence=licence,
                proxyable=licence in PROXYABLE,
                region=region,
                meta={"corpus": corpus.name, "title": doc.get("title"), "holder": doc.get("holder")},
            )
            upstream = row.get("upstream") or {}
            if isinstance(upstream, str):
                try:
                    import json

                    upstream = json.loads(upstream)
                except ValueError:
                    upstream = {}
            if upstream.get("url"):
                result.record_url = upstream["url"]

            # The row's own fields decide whether this exact unit can be shown, and
            # the endpoint and the descriptor must agree: both call row_availability.
            available, why, how = self.row_availability(
                {"box": box, "crop": row.get("crop"), "image": page.get("image"), "image_licence": licence},
                corpus.name, page
            )
            result.mode = how
            if not available:
                result.reason = why
                return result
            if not result.proxyable:
                # We must not re-serve it. The holder may, and a link is not serving.
                image = page.get("image")
                if image and str(image).startswith("http") and box:
                    base = _service_base(image)
                    if base:
                        result.iiif_url = f"{base}/{region}/{edge},/0/default.jpg"
                        result.mode = "remote_iiif"
                        result.render_available = True
                        result.meta["served_by"] = "holder"
                        result.reason = (
                            f"licence {licence!r}: not proxied by this API; "
                            f"the browser loads it from the holder"
                        )
                        return result
                result.reason = (
                    f"licence {licence!r} does not permit serving this image; use the holder's own viewer"
                )
                return result

            if row.get("crop") and "!" in str(row["crop"]):
                member = self.archive_member_path(str(row["crop"]))
                data = member.read_bytes() if member else None
                media = sniff(data) if data else None
                if data and media:
                    result.bytes_data = data
                    result.media_type = media
                    result.sha256 = hashlib.sha256(data).hexdigest()
                    result.crop_url = self.serve_url(unit_id, edge)
                    result.render_available = True
                    return result
                result.reason = why or "crop file is not present"
                return result
            if how == "local_crop" and box:
                data, media, why = self.local_page_crop(page.get("image"), box, edge, corpus.name)
                if data:
                    result.bytes_data = data
                    result.media_type = media
                    result.sha256 = hashlib.sha256(data).hexdigest()
                    result.crop_url = self.serve_url(unit_id, edge)
                    result.render_available = True
                else:
                    result.reason = why
                return result

            image = page.get("image")
            if image and str(image).startswith("http") and box:
                base = _service_base(image)
                if base:
                    result.iiif_url = f"{base}/{region}/{edge},/0/default.jpg"
                    result.mode = "remote_iiif"
                    result.render_available = True
                    return result
            result.reason = result.reason or "no local bytes and no IIIF service"
            return result
        # Nothing in any corpus's unit table carries this id.
        return CropResult(
            unit_id=unit_id,
            occurrence_id=None,
            mode="unknown",
            render_available=False,
            reason="no such unit id in any indexed corpus; ids come from /api/corpus/glyphs",
            meta={"corpora_searched": sorted(UNIT_CORPORA)},
        )


@lru_cache(maxsize=256)
def _service_base_cached(image: str) -> str | None:
    try:
        from ..images import service_of
    except ImportError:  # pragma: no cover
        return None
    return service_of(image)


def _service_base(image: Any) -> str | None:
    return _service_base_cached(str(image)) if image else None
