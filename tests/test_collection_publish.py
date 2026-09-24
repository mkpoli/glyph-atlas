import json

from glyph_atlas import tables
from glyph_atlas.corpus.collection import publish
from glyph_atlas.corpus.sources import discover
from glyph_atlas.schema import Document, Page, PageText


def dataset(path, entry, text):
    path.mkdir(parents=True, exist_ok=True)
    tables.write(path / "documents.parquet", [Document(id=f"hk:{entry}", title=entry)], Document)
    tables.write(path / "pages.parquet", [Page(id=f"hk:{entry}:1", document_id=f"hk:{entry}", seq=1,
                                              image="", width=0, height=0)], Page)
    tables.write(path / "page_texts.parquet", [PageText(page_id=f"hk:{entry}:1", source="honkoku-api", text_raw=text)], PageText)


def test_publication_replaces_book_without_duplicate_text(tmp_path):
    dataset(tmp_path / "honkoku-data", "one", "OLD")
    book = tmp_path / "honkoku-collection/books/one"
    dataset(book, "one", "NEW")
    listing = tmp_path / "honkoku-collection/index.json"
    listing.write_text(json.dumps({"books": [{"entry_id": "one", "dataset": "books/one"}]}))
    result = publish(tmp_path, rebuild_index=False, min_free_bytes=0)
    assert result["tables"] == {"documents": 1, "pages": 1, "page_texts": 1}
    found = next(c for c in discover(tmp_path) if c.name == "honkoku-data")
    assert [p.text_raw for p in tables.read(found.table("page_texts"), PageText)] == ["NEW"]
    assert [p.text_raw for p in tables.read(tmp_path / "honkoku-data/page_texts.parquet", PageText)] == ["OLD"]
    publish(tmp_path, rebuild_index=False, min_free_bytes=0)
    assert len(list(tables.read(found.table("documents"), Document))) == 1


def test_failed_generation_does_not_replace_current(tmp_path):
    import pytest
    dataset(tmp_path / "honkoku-data", "one", "OLD")
    book = tmp_path / "honkoku-collection/books/one"
    dataset(book, "one", "NEW")
    (book.parent.parent / "index.json").write_text(json.dumps({"books": [{"entry_id": "one", "dataset": "books/one"}]}))
    publish(tmp_path, rebuild_index=False, min_free_bytes=0)
    current = (book.parent.parent / "current").resolve()
    (book / "pages.parquet").unlink()
    with pytest.raises(FileNotFoundError):
        publish(tmp_path, rebuild_index=False, min_free_bytes=0)
    assert (book.parent.parent / "current").resolve() == current


def test_live_index_sees_new_generation(tmp_path):
    from glyph_atlas.corpus.index import CorpusIndex, build_chars
    dataset(tmp_path / "honkoku-data", "one", "ア")
    index_dir = tmp_path / "corpus-index"
    build_chars(tmp_path, index_dir)
    reader = CorpusIndex(index_dir, tmp_path)
    assert reader.summary("ア")["n_occurrences"] == 1
    book = tmp_path / "honkoku-collection/books/one"
    dataset(book, "one", "イイ")
    (book.parent.parent / "index.json").write_text(json.dumps({"books": [{"entry_id": "one", "dataset": "books/one"}]}))
    publish(tmp_path, min_free_bytes=0)
    assert reader.summary("イ")["n_occurrences"] == 2
    assert reader.summary("ア") is None


def test_archive_counts_exclude_duplicate_codh_and_joined_rows(tmp_path):
    from glyph_atlas.corpus.collection import archive_statistics
    from glyph_atlas.schema import Box, Unit
    for name in ("codh", "codh-full", "honkoku-data", "honkoku-lines"):
        dataset(tmp_path / name, "one", "ABC")
    for name, ident in (("honkoku-data", "hk:one"), ("honkoku-lines", "hl:one")):
        tables.write(tmp_path / name / "documents.parquet",
                     [Document(id=ident, title="One", source_refs={"honkoku-data": "one"}),
                      Document(id=ident+"-empty", title="No transcription")], Document)
        tables.write(tmp_path / name / "pages.parquet",
                     [Page(id="hk:one:1", document_id=ident, seq=0, image="", width=0, height=0)], Page)
    rows = [Unit(id="one", document_id="hk:one", page_id="hk:one:1", text_source="字",
                 unicode="U+5B57", box=Box(x=0, y=0, w=20, h=20)),
            Unit(id="joined", document_id="hk:one", page_id="hk:one:1", text_source="二字",
                 granularity="sequence", box=Box(x=30, y=0, w=20, h=40)),
            Unit(id="inactive", document_id="hk:one", page_id="hk:one:1", text_source="字",
                 unicode="U+5B57", active=False, box=Box(x=60, y=0, w=20, h=20))]
    for name in ("codh", "codh-full"):
        tables.write(tmp_path / name / "units.parquet", rows, Unit)
    result = archive_statistics(tmp_path)
    assert result["character_crops"] == 1
    assert result["works_with_crops"] == 1
    # Honkoku page and line datasets describe the same source work.
    assert result["text_works"] == 2
    assert "codh" not in result["sources"]
