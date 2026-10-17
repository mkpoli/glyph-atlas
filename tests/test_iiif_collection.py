
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
    assert (pages[0].id, pages[0].seq) == ("ndl:123:1", 0)
    assert pages[0].image == "https://example.org/image/1"
    assert not (out / "page_texts.parquet").exists()
    assert not (out / "units.parquet").exists()
    import json
    assert json.loads((out / "MANIFEST.json").read_text())["schema_version"] == tables.SCHEMA_VERSION
    manifest["metadata"] = []
    subject.collect(source, out)
    assert next(iter(tables.read(out / "documents.parquet", Document))).image_rights.holder_terms == Licence.UNKNOWN


def test_collect_reads_a_presentation_3_manifest_and_keeps_its_statement(tmp_path, monkeypatch):
    source = tmp_path / "source.yaml"
    source.write_text("id: eap\nname: Tangut\nholder: IOM RAS\nurl: https://example.org/eap\nimage_licence: restricted\n"
                      "attribution: British Library, EAP\nbooks:\n  - id: eap140:1-52\n    title: Sutra\n"
                      "    manifest: https://example.org/manifest\n    source_url: https://example.org/52\n")
    statement = "Except as otherwise permitted under your national copyright law this material may not be copied"
    manifest = {"label": {"en": ['"Sutra, Tang.334/233"']},
                "requiredStatement": {"label": {"en": ["Attribution"]}, "value": {"en": [statement]}},
                "metadata": [{"label": {"en": ["Language"]}, "value": {"en": ["Tangut"]}}],
                "items": [{"id": "canvas:1", "type": "Canvas", "width": 1612, "height": 2466, "items": [{"type": "AnnotationPage",
                    "items": [{"type": "Annotation", "body": {"type": "Image", "id": "https://example.org/1.jp2/full/full/0/default.jpg",
                        "service": [{"id": "https://example.org/1.jp2", "@type": "ImageService2"}]}}]}]}]}
    monkeypatch.setattr(subject, "fetch_manifest", lambda *a, **k: manifest)
    subject.collect(source, tmp_path / "out")
    doc = next(iter(tables.read(tmp_path / "out" / "documents.parquet", Document)))
    page = next(iter(tables.read(tmp_path / "out" / "pages.parquet", Page)))
    assert doc.title == "Sutra, Tang.334/233" and doc.meta["catalogue"] == {"Language": "Tangut"}
    assert (doc.image_rights.licence, doc.image_rights.holder_terms) == (Licence.PUBLIC_DOMAIN, Licence.RESTRICTED)
    assert doc.meta["holder_statement"] == statement and doc.image_rights.attribution == "British Library, EAP"
    assert (page.image, page.width, page.height) == ("https://example.org/1.jp2", 1612, 2466)


def test_text_takes_one_language_and_a_tagged_value():
    assert subject.text({"ja": ["和書"], "en": ["Japanese book"]}) == "和書"
    assert subject.text({"@value": "Foo", "@language": "en"}) == "Foo"
    assert subject.text([{"@value": "a"}, "b"]) == "a; b"
    assert (subject.unquoted('"Sutra"'), subject.unquoted('"Foo" bar')) == ("Sutra", '"Foo" bar')
