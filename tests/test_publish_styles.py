import importlib
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import style

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
publish = importlib.import_module("publish_styles")

REVIEWED = "evidence:\n      - source: reviewer\n        reviewed: 2026-09-26"


def corpus(root, name, units, pages=()):
    directory = root / name
    directory.mkdir(parents=True)
    pq.write_table(pa.table({"id": [u[0] for u in units], "document_id": [u[1] for u in units]}), directory / "units.parquet")
    if pages:
        pq.write_table(pa.table({"id": [p[0] for p in pages], "document_id": [p[1] for p in pages],
                                 "style": [p[2] for p in pages]}), directory / "pages.parquet")


def test_a_documents_glyphs_are_its_id_range(tmp_path):
    corpus(tmp_path, "codh", [("codh:1:a", "codh:1"), ("codh:1:b", "codh:1"), ("codh:10:a", "codh:10")])
    assert publish.corpus_ranges({"codh:1"}, tmp_path) == {"codh:1": "codh:1:"}
    assert publish.upper("codh:1:") == "codh:1;"


def test_a_document_in_no_corpus_is_refused(tmp_path):
    corpus(tmp_path, "codh", [("codh:1:a", "codh:1")])
    with pytest.raises(publish.Refused, match="codh:2"):
        publish.corpus_ranges({"codh:1", "codh:2"}, tmp_path)


def test_a_crop_with_a_style_of_its_own_is_refused(tmp_path):
    directory = tmp_path / "codh"
    directory.mkdir()
    pq.write_table(pa.table({"id": ["codh:1:a", "codh:1:b"], "document_id": ["codh:1", "codh:1"],
                             "style": ["unassessed", "regular"]}), directory / "units.parquet")
    with pytest.raises(publish.Refused, match="codh:1:b"):
        publish.corpus_ranges({"codh:1"}, tmp_path)


def test_a_range_that_misses_a_glyph_or_takes_another_documents_is_refused(tmp_path):
    corpus(tmp_path, "a", [("x:9", "codh:1")])
    with pytest.raises(publish.Refused, match="codh:1"):
        publish.corpus_ranges({"codh:1"}, tmp_path)
    corpus(tmp_path, "b", [("hng:dng:1", "hng:other")])
    with pytest.raises(publish.Refused, match="hng:dng"):
        publish.corpus_ranges({"hng:dng"}, tmp_path)


def test_a_page_with_its_own_style_is_found(tmp_path):
    corpus(tmp_path, "c", [("c:1:a", "c:1")], pages=[("c:1:p1", "c:1", "unassessed"), ("c:1:p2", "c:1", "regular")])
    assert publish.page_styles({"c:1"}, tmp_path) == {"c:1:p2": "regular"}


def test_the_statements_set_local_crops_by_document_and_corpus_glyphs_by_range(tmp_path, monkeypatch):
    confirmed = tmp_path / "document-styles.yaml"
    confirmed.write_text(f"documents:\n  codh:1:\n    style: cursive\n    {REVIEWED}\n"
                         f"  hk:2:\n    style: mixed\n    {REVIEWED}\n", encoding="utf-8")
    monkeypatch.setattr(style, "DOCUMENTS", confirmed)
    lines = publish.statements(style.confirmed(), {"codh:1": "codh:1:"})
    assert lines == [
        "UPDATE units SET style='cursive' WHERE origin='local' AND document='codh:1';",
        "UPDATE corpus_units SET style='cursive' WHERE id>='codh:1:' AND id<'codh:1;';",
        "UPDATE units SET style='unassessed' WHERE origin='local' AND document='hk:2';",
    ]
