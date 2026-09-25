
from glyph_atlas import tables
from glyph_atlas.importers import iiif_collection as subject
from glyph_atlas.schema import Document, Licence, Page


def test_collect_full_volume_without_fabricated_text(tmp_path, monkeypatch):
    source = tmp_path / "source.yaml"
    source.write_text("id: example\nname: Book\nholder: NDL\nurl: https://example.org/book\nbooks:\n  - id: ndl:123\n    title: Volume 2\n    manifest: https://example.org/manifest\n    source_url: https://example.org/book/2\n")
    manifest = {"label": "Volume 2", "metadata": [{"label": "Access Restrictions", "value": "PDM"}],
                "sequences": [{"canvases": [{"@id": "canvas:1", "width": 100, "height": 200,
                    "images": [{"resource": {"@type": "dctypes:Image", "service": {"@id": "https://example.org/image/1"}}}]}]}]}
    monkeypatch.setattr(subject, "fetch_manifest", lambda *a, **k: manifest)
    out = tmp_path / "book"
    assert subject.collect(source, out)["pages"] == 1
    assert subject.collect(source, out)["volumes"] == 1
    docs = list(tables.read(out / "documents.parquet", Document))
    pages = list(tables.read(out / "pages.parquet", Page))
    assert len(docs) == len(pages) == 1
    assert docs[0].image_rights.licence == Licence.PDM
    assert pages[0].id == "ndl:123:1"
    assert pages[0].image == "https://example.org/image/1"
    assert not (out / "page_texts.parquet").exists()
    assert not (out / "units.parquet").exists()
    import json
    assert json.loads((out / "MANIFEST.json").read_text())["schema_version"] == tables.SCHEMA_VERSION
    manifest["metadata"] = []
    subject.collect(source, out)
    assert next(iter(tables.read(out / "documents.parquet", Document))).image_rights.holder_terms == Licence.UNKNOWN
