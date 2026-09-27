"""A crop shows its page as seq + 1, and keeps the 0-based position beside it."""

import importlib
from pathlib import Path

from glyph_atlas.schema import Page


def test_a_page_at_position_zero_is_shown_as_page_one(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    first = Page(id="ws:ko:scan:abc:1", document_id="ws:ko:scan:abc", seq=0, image="", width=0, height=0)
    assert module.page_fields(first) == {"page_number": 1, "page_index": 0}
    assert module.page_fields(None) == {"page_number": None, "page_index": None}
