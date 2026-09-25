"""Persistent, immutable display crops shared by the gallery and review UI."""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from PIL import Image

from .. import images

VERSION = 1
#: A IIIF region request of a service whose full-size scan the image cache may hold.
_REGION = re.compile(r"(?P<service>https://.+)/(?P<x>\d+),(?P<y>\d+),(?P<w>\d+),(?P<h>\d+)/(?P<edge>\d+),/0/default\.jpg")
CACHE_CONTROL = "public, max-age=31536000, immutable"
_LOCKS = [threading.Lock() for _ in range(64)]


@lru_cache(maxsize=8192)
def _resolved(path, device, inode):
    # Inode/device changes invalidate symlink resolution, without walking the
    # mounted source directory again for every crop of the same page.
    return Path(path).resolve()


class MediaCache:
    def __init__(self, *, corpus_root=None, directory=None):
        self.directory = Path(directory) if directory else images.cache_root() / "display-crops"
        self.roots = {"images": images.images_root().resolve()}
        self.roots["hilab"] = (images.cache_root() / "hilab").resolve()
        if corpus_root is not None:
            self.roots["corpus"] = Path(corpus_root).resolve()
        self._known = set()

    def _register(self, spec):
        raw = json.dumps({"version": VERSION, **spec}, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(raw.encode()).hexdigest()
        if key not in self._known:
            descriptor = self.directory / key[:2] / f"{key}.json"
            if not descriptor.exists():
                descriptor.parent.mkdir(parents=True, exist_ok=True)
                self._write(descriptor, raw.encode())
            if len(self._known) > 32768:
                self._known.clear()
            self._known.add(key)
        return f"/atlas/media/{key}.webp"

    @staticmethod
    def _write(path, data):
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_bytes(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def local(self, path, box=None, *, context=False, edge=None, exact=False):
        path = Path(path)
        stat = path.stat()
        path = _resolved(str(path), stat.st_dev, stat.st_ino)
        for name, root in self.roots.items():
            if path.is_relative_to(root):
                spec = {"source": name, "path": path.relative_to(root).as_posix(),
                    "stamp": stat.st_mtime_ns, "size": stat.st_size,
                    "box": list(box) if box else None, "context": context, "edge": edge}
                if context:
                    # The reach is part of what a context image is, so a wider reach is a new image.
                    from .atlas import CONTEXT_REACH
                    spec["reach"] = list(CONTEXT_REACH)
                if exact:
                    # A requested region is cut as given, without the display margin.
                    spec["exact"] = True
                return self._register(spec)
        raise ValueError("display crop outside registered image roots")

    def region(self, url):
        """A holder IIIF region request, cut from the full-size scan the image cache holds."""
        found = _REGION.fullmatch(url or "")
        if not found:
            return None
        path = images.held(found["service"])
        if path is None:
            return None
        box = [int(found[k]) for k in ("x", "y", "w", "h")]
        return self.local(path, box, exact=True, edge=int(found["edge"]))

    def materialize(self, key):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise FileNotFoundError("unknown display crop")
        output = self.directory / key[:2] / f"{key}.webp"
        if output.is_file():
            return output
        with _LOCKS[int(key[:2], 16) % len(_LOCKS)]:
            if output.is_file():
                return output
            spec = json.loads(output.with_suffix(".json").read_text())
            from .atlas import _IMAGE_SLOTS, crop_bounds, decoded_image
            root = self.roots[spec["source"]]
            source = (root / spec["path"]).resolve()
            if not source.is_relative_to(root):
                raise ValueError("invalid display source")
            stat = source.stat()
            if (stat.st_mtime_ns, stat.st_size) != (spec["stamp"], spec["size"]):
                raise ValueError("display source changed before rendering")
            with _IMAGE_SLOTS:
                page = decoded_image(str(source), spec["stamp"])
                if not spec["box"]:
                    picture = page.copy()
                elif spec.get("exact"):
                    x, y, w, h = spec["box"]
                    bounds = (max(0, x), max(0, y), min(page.width, x + w), min(page.height, y + h))
                    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                        raise ValueError("The region falls outside the image.")
                    picture = page.crop(bounds)
                else:
                    picture = page.crop(crop_bounds(page, spec["box"], spec["context"]))
            edge = spec["edge"]
            bounds = (edge, edge) if edge else (960, 1280) if spec["context"] else (240, 280)
            picture.thumbnail(bounds, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            picture.save(buffer, format="WEBP", quality=90, method=4)
            self._write(output, buffer.getvalue())
            return output

    def corpus_image(self, row, crops, *, edge=240):
        """Register a decorated corpus row directly, avoiding a table scan per image."""
        thumb = row.get("thumbnail") or {}
        from ..corpus.api import PROXYABLE
        if row.get("image_licence") not in PROXYABLE or not thumb.get("available"):
            return None
        path = crops.archive_member_path(row.get("crop")) if row.get("crop") else None
        if path:
            return self.local(path, edge=edge)
        path = crops.page_image_path(row.get("image"), row.get("corpus"))
        box = row.get("box")
        if path and box:
            return self.local(path, [box[k] for k in ("x", "y", "w", "h")], edge=edge)
        return None


def prepare_dataset(directory):
    """Render a newly extracted page before its characters enter the live gallery."""
    import shutil

    from .. import tables
    from ..schema import Page, Unit
    from .atlas import image_size
    from .server import cached_image

    media = MediaCache()
    if shutil.disk_usage(images.cache_root()).free < 1024**3:
        raise OSError("display crop storage has less than 1 GiB free")
    directory = Path(directory)
    pages = {p.id: p for p in tables.read(directory / "pages.parquet", Page)}
    prepared = 0
    for unit in tables.read(directory / "units.parquet", Unit):
        page = pages.get(unit.page_id)
        if not unit.active or unit.granularity != "char" or not page or not unit.box:
            continue
        path = cached_image(unit.crop_sha256 or page.sha256 or "")
        if path is None:
            continue
        size = image_size(str(path), path.stat().st_mtime_ns)
        if not size or not page.width or not page.height:
            continue
        box = None if unit.crop_sha256 else (
            unit.box.x * size[0] / page.width, unit.box.y * size[1] / page.height,
            unit.box.w * size[0] / page.width, unit.box.h * size[1] / page.height)
        for context in ([False, True] if box else [False]):
            url = media.local(path, box, context=context)
            media.materialize(url.rsplit("/", 1)[1].removesuffix(".webp"))
        prepared += 1
    return prepared


def router(media):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse, Response

    api = APIRouter()

    @api.get("/atlas/media/{key}.webp")
    def display_crop(key: str, request: Request):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise HTTPException(404, "Unknown display crop")
        try:
            path = media.materialize(key)
        except FileNotFoundError:
            raise HTTPException(404, "Display crop unavailable") from None
        except (OSError, ValueError, KeyError, Image.DecompressionBombError):
            raise HTTPException(422, "Display crop could not be prepared") from None
        headers = {"Cache-Control": CACHE_CONTROL, "ETag": f'"{key}"'}
        if request.headers.get("if-none-match") in (headers["ETag"], f'W/{headers["ETag"]}', "*"):
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/webp", headers=headers)

    return api
