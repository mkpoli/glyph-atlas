"""Documents withdrawn from the atlas, listed with their reasons in `data/vocab/withdrawn.yaml`."""
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[2] / "data/vocab/withdrawn.yaml"


@lru_cache(maxsize=1)
def documents() -> frozenset[str]:
    """The ids of every withdrawn document."""
    data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    return frozenset(doc for entry in data.get("withdrawals", []) for doc in entry["documents"])


def corpus_range(document: str) -> tuple[str, str]:
    """The id range `[low, high)` a corpus glyph of `document` falls in: `<document>_<page>_<line>:…`."""
    return document + "_", document + "`"
