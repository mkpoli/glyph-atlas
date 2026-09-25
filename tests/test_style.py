import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from glyph_atlas import style, tables
from glyph_atlas.schema import Document, Page, Unit


def page(**fields):
    return Page(id="p", document_id="d", seq=0, image="https://example.org/p.jpg", width=10, height=10, **fields)


def test_every_node_has_a_label_and_a_definition():
    for node in style.vocabulary().values():
        assert node["en"] and node["definition"]


def test_a_value_outside_the_vocabulary_is_refused():
    with pytest.raises(ValidationError):
        Unit(id="u", style="sosho")
    with pytest.raises(ValidationError):
        page(style="kaisho")


def test_a_unit_without_a_style_takes_its_page_then_its_document():
    book = Document(id="d", title="t", style="running")
    assert style.style_of(Unit(id="u"), page(style="cursive"), book) == "cursive"
    assert style.style_of(Unit(id="u", style="regular"), page(style="cursive"), book) == "regular"
    assert style.style_of(Unit(id="u"), page(), book) == "running"
    assert style.style_of(Unit(id="u")) == "unassessed"


def test_a_mixed_page_or_document_passes_nothing_down():
    assert style.style_of(Unit(id="u"), page(style="mixed"), Document(id="d", title="t", style="running")) == "unassessed"
    assert style.style_of(Unit(id="u"), page(), Document(id="d", title="t", style="mixed")) == "unassessed"



def test_a_table_written_before_the_column_reads_as_unassessed(tmp_path):
    units = tmp_path / "units.parquet"
    pq.write_table(pa.table({"id": ["u"], "reading": ["あ"]}), units)
    assert [u.style for u in tables.read(units, Unit)] == ["unassessed"]
    pages = tmp_path / "pages.parquet"
    pq.write_table(pa.table({"id": ["p"], "document_id": ["d"], "seq": [0], "image": ["x"], "width": [1], "height": [1]}), pages)
    assert [p.style for p in tables.read(pages, Page)] == ["unassessed"]


def test_a_written_style_reads_back(tmp_path):
    tables.write(tmp_path / "units.parquet", [Unit(id="u", style="seal")], Unit)
    tables.write(tmp_path / "pages.parquet", [page(style="mixed")], Page)
    assert tables.read(tmp_path / "units.parquet", Unit)[0].style == "seal"
    assert tables.read(tmp_path / "pages.parquet", Page)[0].style == "mixed"


def test_a_confirmed_document_style_is_used_and_checked(tmp_path, monkeypatch):
    confirmed = tmp_path / "document-styles.yaml"
    confirmed.write_text("documents:\n  d:\n    style: cursive\n    evidence: [{source: reviewer}]\n", encoding="utf-8")
    monkeypatch.setattr(style, "DOCUMENTS", confirmed)
    book = Document(id="d", title="t")
    assert style.document_style(book) == "cursive"
    assert style.style_of(Unit(id="u"), page(), book) == "cursive"
    assert style.document_style(Document(id="other", title="t", style="running")) == "running"
    confirmed.write_text("documents:\n  d:\n    style: sosho\n    evidence: [{source: reviewer}]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        style.document_style(book)
    confirmed.write_text("documents:\n  d:\n    style: cursive\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no evidence"):
        style.document_style(book)


def test_the_confirmed_file_in_the_repository_is_valid():
    style.confirmed()
