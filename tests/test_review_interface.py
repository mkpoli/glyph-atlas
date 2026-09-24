"""Refreshing the interface must revalidate its entry page after publication."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glyph_atlas.review.server import ReviewInterface


def test_entry_page_revalidates_after_a_new_build(tmp_path):
    index = tmp_path / "index.html"
    index.write_text('<script src="/assets/old.js"></script>')
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "old.js").write_text("old build")
    app = FastAPI()
    app.mount("/", ReviewInterface(directory=tmp_path, html=True))
    client = TestClient(app)
    for path in ("/", "/index.html", "/?v=per-crop-review"):
        first = client.get(path)
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-cache, must-revalidate"
    etag = first.headers["etag"]
    unchanged = client.get("/", headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.headers["cache-control"] == "no-cache, must-revalidate"

    index.write_text('<script src="/assets/new-per-crop-review.js"></script>')
    refreshed = client.get("/", headers={"If-None-Match": etag})
    assert refreshed.status_code == 200
    assert "new-per-crop-review.js" in refreshed.text
    assert refreshed.headers["etag"] != etag
    assert client.get("/assets/old.js").status_code == 200
