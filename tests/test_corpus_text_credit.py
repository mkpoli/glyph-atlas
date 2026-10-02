"""A located unit carries the credit of the transcription that labels it, beside its image credit."""

from __future__ import annotations

import json
from pathlib import Path

from glyph_atlas.corpus.index import _unit_row
from glyph_atlas.corpus.sources import Corpus


class Context:
    """The two lookups `_unit_row` makes, from fixed rows."""

    def __init__(self, document: dict) -> None:
        self.row = document

    def document(self, _: str) -> dict:
        return self.row

    def page(self, _: str) -> dict:
        return {"image": "https://example.org/iiif/p1"}


def unit(document_rights: dict) -> dict:
    corpus = Corpus(name="hdic-krm", directory=Path("."))
    row = {"id": "krm:F00001:0", "document_id": "d", "page_id": "p", "text_source": "人", "unicode": "U+4EBA",
           "box": {"x": 1, "y": 2, "w": 3, "h": 4}}
    return _unit_row(corpus, Context({"title": "t", **document_rights}), row, "人", "U+4EBA")


def test_the_labels_credit_travels_with_the_unit() -> None:
    credit = "HDIC project, KRM, CC BY-SA 4.0"
    joined = unit({"image_rights": {"licence": "PDM-1.0", "attribution": "NDL"},
                   "text_rights": json.dumps({"licence": "CC-BY-SA-4.0", "attribution": credit})})
    assert joined["text_attribution"] == credit
    assert joined["image_rights"]["attribution"] == "NDL"


def test_a_unit_without_text_rights_has_no_label_credit() -> None:
    assert unit({"image_rights": {"licence": "PDM-1.0"}})["text_attribution"] is None
