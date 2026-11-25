"""The source registry: one YAML file per upstream in data/sources/."""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import Source

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data" / "sources"


def load(folder: Path = SOURCES) -> list[Source]:
    found = []
    for path in sorted(folder.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        found.append(Source.model_validate({k: raw[k] for k in Source.model_fields if k in raw}))
    return found


def hosted_documents(folder: Path = SOURCES) -> frozenset[str]:
    """The documents whose page images the site serves from its own copy: every source's `hosted_pages`."""
    return frozenset(document for source in load(folder) for document in source.hosted_pages)
