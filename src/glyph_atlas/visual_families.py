"""Versioned image-group proposals, kept separate from imported transcriptions.

An upstream code point can denote an orthographic family. Image clusters describe
visual similarity; a cluster acquires a written identity only through explicit
visual evidence. These proposals never constitute human verification.
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from . import refs

ROOT = Path(__file__).resolve().parents[2]


def directory() -> Path:
    return Path(os.environ.get("ATLAS_VISUAL_FAMILIES_DIR", ROOT / "work/visual-families"))


@lru_cache(maxsize=2)
def _load(path: str, stamp: int, size: int) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("version") != 1 or not isinstance(data.get("assignments"), dict):
        raise ValueError("Invalid visual-family registry")
    return data


def registry() -> dict:
    path = directory() / "assignments.json"
    try:
        stat = path.stat()
        return _load(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return {"version": 1, "assignments": {}, "families": {}, "status": "not_analyzed"}


def assignment_for(identity: str, source_revision: str | None = None, *,
                   source_label: str | None = None, crop_sha256: str | None = None,
                   source_signature: str | None = None) -> dict | None:
    row = registry().get("assignments", {}).get(identity)
    if row is None:
        return None
    if row.get("source_signature") and row["source_signature"] != source_signature:
        return None
    for key, current in (("source_revision", source_revision), ("source_label", source_label),
                         ("crop_sha256", crop_sha256)):
        if current is not None and row.get(key) is not None and row[key] != current:
            return None
    result = deepcopy(row)
    result["verified"] = False
    result["confirmed_by_human"] = False
    result["identity_basis"] = "visual_model"
    return result


def evidence_signature(identity: str, source_code_point: str | None, page_id: str | None,
                       box, crop: str | None = None) -> str:
    bounds = box.model_dump() if hasattr(box, "model_dump") else box
    if isinstance(bounds, str):
        bounds = json.loads(bounds)
    if bounds:
        bounds = {key: float(bounds[key]) for key in ("x", "y", "w", "h")}
    data = [identity, source_code_point or None, page_id or None, bounds or None, crop or None]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def family_analysis(code_point: str) -> dict:
    family = refs.grapheme(code_point) or code_point
    data = registry()
    result = deepcopy(data.get("families", {}).get(family, {}))
    return {"status": "ready" if result else "not_analyzed", "family": family,
            "model_revision": data.get("model_revision"), "sample_count": 0,
            "assigned_count": 0, "unassigned_count": 0, "groups": [], **result}


def group_assignments(group_id: str) -> list[dict]:
    return [deepcopy(row) for row in registry().get("assignments", {}).values()
            if row.get("visual_group", {}).get("id") == group_id]


@lru_cache(maxsize=2)
def _samples(path: str, stamp: int, size: int) -> dict:
    return {row["id"]: row for line in Path(path).read_text().splitlines()
            if line.strip() and (row := json.loads(line))}


def get_sample_image(identity: str) -> Path | None:
    """Serve only a registered prepared crop; never an arbitrary filesystem path."""
    root = directory().resolve()
    manifest = root / "samples.jsonl"
    try:
        stat = manifest.stat()
        row = _samples(str(manifest), stat.st_mtime_ns, stat.st_size).get(identity)
        if not row:
            return None
        path = (root / row["image_path"]).resolve()
        path.relative_to(root)
        if path.is_file() and path.stat().st_size < 20 * 1024 * 1024:
            return path
    except (OSError, ValueError, KeyError):
        pass
    return None
