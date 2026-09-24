import json

import pytest

from glyph_atlas import tables
from glyph_atlas.importers.ndl_ocr import import_ocr
from glyph_atlas.schema import Document, Page


def source(tmp_path):
    tables.write(tmp_path / "documents.parquet", [Document(id="ndl:123", title="Book")], Document)
    tables.write(tmp_path / "pages.parquet", [Page(id="ndl:123:1", document_id="ndl:123", seq=1,
                 image="https://example.org/scan", width=100, height=200)], Page)
    (tmp_path / "MANIFEST.json").write_text('{"tables":{},"collection":{}}')
    return tmp_path


def payload(book="123"):
    segments = [{"id": 7, "contenttext": "假名", "xmin": 10.2, "ymin": 20.8, "xmax": 40.1, "ymax": 90.2},
                {"id": 8, "contenttext": "外", "xmin": 90, "ymin": 20, "xmax": 110, "ymax": 40}]
    return json.dumps({"list": [{"book": book, "page": 1, "id": "123_1", "coordjson": json.dumps(segments)}]}).encode()


def test_preserves_unverified_ocr_and_original_coordinates(tmp_path):
    result = import_ocr(source(tmp_path), payload(), "123")
    assert result == {"pages": 1, "segments": 1, "rejected": 1}
    dataset = tables.Dataset(tmp_path)
    line, = dataset.read("lines")
    assert line.text == "假名"
    assert line.box.model_dump() == {"x": 10, "y": 20, "w": 31, "h": 71}
    assert line.meta["verified"] is False
    assert line.meta["source_segment_id"] == 7
    assert dataset.read("pages")[0].transcription["source"] == "ndl-ocr"
    assert dataset.validate() == []
    import_ocr(tmp_path, payload(), "123")
    assert len(dataset.read("lines")) == 1


def test_wrong_book_does_not_replace_tables(tmp_path):
    source(tmp_path)
    before = (tmp_path / "pages.parquet").read_bytes()
    with pytest.raises(ValueError, match="do not match"):
        import_ocr(tmp_path, payload("456"), "123")
    assert (tmp_path / "pages.parquet").read_bytes() == before
    assert not (tmp_path / "lines.parquet").exists()
