import hashlib
import json
import zipfile

import pytest
import yaml

from glyph_atlas import tables
from glyph_atlas.importers import kyodokenkyu as k
from glyph_atlas.schema import Licence


def conllu(text="松島", form="松島"):
    return f"# text = {text}\n1\t{form}\t島\tNOUN\t_\t_\t0\troot\t_\tSpaceAfter=No\n"


def test_sentence_text_preserves_spelling_and_full_annotation():
    raw = conllu("梦中", "夢") + "\n" + conllu("島")
    parsed = k.sentences(raw)
    assert [text for text, _ in parsed] == ["梦中", "島"]
    assert "\t夢\t" in parsed[0][1]
    with pytest.raises(ValueError, match="ten columns"):
        k.sentences("# text = 島\n1\t島\n")
    with pytest.raises(ValueError, match="one text comment"):
        k.sentences(conllu().replace("# text = 松島\n", ""))


def test_editor_sections_handles_menus_and_fixed_paths():
    assert k.editor_sections('<select><option value="sendai/001">卷之上</option></select>') == {
        "sendai/001": "卷之上"}
    assert k.editor_sections('Bunhen.volpath="sorai/05F"') == {"sorai/05F": ""}
    with pytest.raises(ValueError, match="no corpus sections"):
        k.editor_sections("<html>unavailable</html>")


def test_import_pinned_corpus_without_inventing_alignment(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    def pin(data, url):
        digest = hashlib.sha256(data).hexdigest()
        (cache / digest).write_bytes(data)
        return {"url": url, "sha256": digest, "bytes": len(data)}

    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("snapshot/book/001/000.txt", conllu())
        z.writestr("snapshot/book/001/001.txt", conllu("梦中", "夢"))
        z.writestr("snapshot/unrelated/001.txt", "Not CoNLL-U")
        z.writestr("snapshot/book/001/README.md", "Not CoNLL-U")
    config = {
        "url": "https://example.org/", "index": pin(b'<a href="book">Book</a>', "https://example.org/"),
        "repositories": {"repo": {"url": "https://example.org/repo", "revision": "abc123",
                                   "archive": pin(archive.read_bytes(), "https://example.org/repo.zip")}},
        "corpora": [{"id": "book", "title": "Book", "repository": "repo", "editor": pin(
            b'<option value="book/001">One</option>', "https://example.org/editor")}],
        "scans": [{"id": "gallery", "title": "Scans", "format": "gallery", "holder": "Library",
                   "image_licence": "unknown", "catalogue": "https://example.org/gallery/",
                   "source": pin(b'<img src="02.jpg"><img src="01.jpg"><img src="02.jpg">',
                                 "https://example.org/gallery/")}],
    }
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(k.net, "download", lambda url, path: path if path.exists() else pytest.fail("network"))
    out = tmp_path / "out"
    result = k.collect(source, out, cache)
    assert (result["documents"], result["text_segments"], result["sentences"], result["scan_pages"]) == (2, 2, 2, 2)
    dataset = tables.Dataset(out)
    assert dataset.validate() == []
    pages = list(dataset.read("pages"))
    text_pages = [p for p in pages if p.meta.get("record_kind") == "text-segment"]
    assert all(p.image == "" and p.width == p.height == 0 for p in text_pages)
    assert all(p.transcription["revision"] == "abc123" for p in text_pages)
    docs = list(dataset.read("documents"))
    assert next(d for d in docs if d.text_rights).text_rights.licence == Licence.UNKNOWN
    scans = [p for p in pages if p.image]
    assert [p.image for p in sorted(scans, key=lambda p: p.seq)] == [
        "https://example.org/gallery/02.jpg", "https://example.org/gallery/01.jpg"]
    assert list(dataset.read("lines"))[1].text == "梦中"
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("*.parquet")}
    assert k.collect(source, out, cache) == result
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("*.parquet")}
    (cache / config["corpora"][0]["editor"]["sha256"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Snapshot mismatch"):
        k.collect(source, out, cache)
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("*.parquet")}


def test_scan_iiif_retains_holder_statement_and_dimensions():
    book = {"id": "iiif", "title": "Book", "format": "iiif", "holder": "Library",
            "image_licence": "CC-BY-NC-4.0", "catalogue": "https://example.org/book",
            "source": {"url": "https://example.org/manifest"}}
    manifest = {"label": {"ja": ["本"]}, "rights": "https://creativecommons.org/licenses/by-nc/4.0/",
                "requiredStatement": {"label": {"en": ["Credit"]}, "value": {"en": ["Library"]}},
                "items": [{"id": "https://example.org/canvas/1", "type": "Canvas", "width": 100,
                           "height": 200, "items": [{"type": "AnnotationPage", "items": [
                               {"type": "Annotation", "body": {"id": "https://example.org/1.jpg",
                                "type": "Image", "width": 100, "height": 200}}]}]}]}
    doc, pages = k.scan_records(book, json.dumps(manifest).encode())
    assert (pages[0].width, pages[0].height) == (100, 200)
    assert doc.image_rights.holder_terms == Licence.CC_BY_NC_4
    assert doc.meta["holder_statement"] == manifest["requiredStatement"]
    assert doc.meta["holder_rights_url"] == manifest["rights"]
