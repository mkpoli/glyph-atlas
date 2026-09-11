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
