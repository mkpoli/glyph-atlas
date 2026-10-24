"""A page record says where the page opens at its source: manifest, canvas and the holder's record."""
import hashlib
import json

from glyph_atlas.page_sources import catalogue_urls, manifest_record, page_sources

M2 = "https://x.org/a/manifest"


def cache_manifest(tmp_path, url, manifest):
    directory = tmp_path / "manifests"
    directory.mkdir(exist_ok=True)
    (directory / f"{hashlib.sha256(url.encode()).hexdigest()}.json").write_text("﻿" + json.dumps(manifest), encoding="utf-8")


def v2(*services, **extra):
    canvases = [{"@id": f"https://x.org/a/canvas/{n + 1}", "@type": "sc:Canvas",
                 "images": [{"resource": {"@type": "dctypes:Image", "service": {"@id": service}}}]} for n, service in enumerate(services)]
    return {"@id": M2, "sequences": [{"canvases": canvases}], **extra}


def test_a_page_is_placed_by_its_canvas_or_else_by_its_image_service(tmp_path):
    cache_manifest(tmp_path, M2, v2("https://x.org/i/1", "https://x.org/i/2", "https://x.org/i/3"))
    refs = {"iiif-manifest": M2}
    assert page_sources(refs, "https://x.org/a/canvas/3", None, cache=tmp_path) == {
        "manifest": M2, "canvas": "https://x.org/a/canvas/3", "canvas_index": 2, "catalogue_url": None}
    assert page_sources(refs, None, "https://x.org/i/2/", "https://x.org/rec", cache=tmp_path) == {
        "manifest": M2, "canvas": "https://x.org/a/canvas/2", "canvas_index": 1, "catalogue_url": "https://x.org/rec"}
    # A page found in neither way keeps the manifest and has no position: a page number proves nothing.
    assert page_sources(refs, None, "https://x.org/i/9", cache=tmp_path)["canvas_index"] is None


def test_a_manifest_the_cache_never_held_is_not_offered(tmp_path):
    assert page_sources({"iiif-manifest": "https://x.org/gone"}, "c", None, cache=tmp_path) == {
        "manifest": None, "canvas": "c", "canvas_index": None, "catalogue_url": None}


def test_a_manifest_names_its_record_by_related_or_a_url_metadata_entry():
    assert manifest_record({"related": {"@id": "https://x.org/item/1", "format": "text/html"}}) == "https://x.org/item/1"
    assert manifest_record({"related": {"@id": "https://x.org/1.pdf", "format": "application/pdf"}}) is None
    assert manifest_record({"metadata": [{"label": "URL", "value": "https://dl.ndl.go.jp/info:ndljp/pid/1"}]}) == "https://dl.ndl.go.jp/info:ndljp/pid/1"
    assert manifest_record({"metadata": [{"label": {"ja": ["ソース"], "en": ["Source"]},
                                          "value": {"none": ['<a href="https://hdl.handle.net/2324/1">https://hdl.handle.net/2324/1</a>']}}]}) \
        == "https://hdl.handle.net/2324/1"
    assert manifest_record({"metadata": [{"label": "Manifest URI", "value": M2}]}) is None


def test_a_link_two_books_manifests_share_is_the_site_and_no_record(tmp_path):
    shared = {"@id": "https://x.org/repo", "format": "text/html"}
    for name in ("a", "b"):
        cache_manifest(tmp_path, f"https://x.org/{name}.json", {"related": shared})
    cache_manifest(tmp_path, "https://x.org/c.json", {"related": "https://x.org/item/c"})
    documents = [("a", {"iiif-manifest": "https://x.org/a.json"}, {}), ("b", {"iiif-manifest": "https://x.org/b.json"}, {}),
                 ("c", {"iiif-manifest": "https://x.org/c.json"}, {}),
                 ("d", {"catalogue": "https://x.org/catalogue/d"}, {}),
                 ("e", {}, {"kokusho": {"record_url": "https://kokusho.nijl.ac.jp/biblio/1"}})]
    assert catalogue_urls(documents, tmp_path) == {"c": "https://x.org/item/c", "d": "https://x.org/catalogue/d",
                                                    "e": "https://kokusho.nijl.ac.jp/biblio/1"}
