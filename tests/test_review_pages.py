"""Drawing character boxes on a page photo, against a dataset with pages and no lines.

The fixture is what a photograph-only source leaves on disk: documents and pages, no `lines` or
`units` table. The page image is a real JPEG in a cache under `tmp_path`, because setting a character
checks the crop can be cut from it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from glyph_atlas import tables
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import Store, apply, replay
from glyph_atlas.schema import Document, Line, Page, Unit

DOCUMENT = "khs:fixture"
PAGE = f"{DOCUMENT}:1"
OTHER = f"{DOCUMENT}:2"
CLIENT = "reviewer-fixture"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), "white").save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / f"{digest}.jpg").write_bytes(image.read_bytes())
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id=DOCUMENT, title="Photographs")], Document)
    tables.write(root / "pages.parquet", [
        # The record is twice the cached photo's size: boxes are in the record's pixels.
        Page(id=OTHER, document_id=DOCUMENT, seq=2, image="https://example.org/2.jpg", width=800, height=600),
        Page(id=PAGE, document_id=DOCUMENT, seq=1, image="https://example.org/1.jpg", width=800, height=600,
             sha256=digest),
    ], Page)
    return root


@pytest.fixture
def api(dataset: Path) -> TestClient:
    return TestClient(create_app(dataset, corpus=None))


def draw(api: TestClient, box: dict, page: str = PAGE, key: str | None = None):
    return api.post(f"/atlas/pages/{page}/units",
                    json={"box": box, "client_id": CLIENT, "idempotency_key": key or str(uuid4())})


def test_pages_lists_every_page_of_a_dataset_without_units(api: TestClient) -> None:
    body = api.get("/atlas/pages").json()
    assert body["documents"] == [{"id": DOCUMENT, "title": "Photographs", "units": 0, "pages": [
        {"id": PAGE, "seq": 1, "units": 0}, {"id": OTHER, "seq": 2, "units": 0}]}]


def test_a_page_names_its_photo_and_whether_boxes_can_be_drawn(api: TestClient) -> None:
    page = api.get(f"/atlas/pages/{PAGE}").json()
    assert page["image_url"] == f"/images/{page['image_sha256']}"
    assert api.get(page["image_url"]).status_code == 200
    assert (page["width"], page["height"], page["drawable"]) == (800, 600, True)
    assert (page["previous"], page["next"], page["units"]) == (None, OTHER, [])
    uncached = api.get(f"/atlas/pages/{OTHER}").json()
    assert uncached["image_url"] is None and uncached["drawable"] is False
    assert api.get("/atlas/pages/nope").status_code == 404


def test_the_first_box_creates_the_page_line_and_later_boxes_reuse_it(api: TestClient, dataset: Path) -> None:
    first = draw(api, {"x": 100, "y": 50, "w": 30, "h": 40})
    assert first.status_code == 201
    body = first.json()
    line = body["line"]["state"]
    assert line["id"] == f"{PAGE}:l1" and line["role"] == "other" and line["meta"] == {"scope": "page"}
    assert line["box"] == {"x": 0, "y": 0, "w": 800, "h": 600} and line["match_method"] == "manual"
    unit = body["unit"]["state"]
    assert unit["id"] == f"{PAGE}:l1:m1" and unit["method"] == "manual" and unit["review"] == "transcriber"
    assert unit["classification"] == "unassessed" and unit["unicode"] is None
    assert body["item"] == {"id": unit["id"], "line_id": f"{PAGE}:l1",
                            "box": {"x": 100, "y": 50, "w": 30, "h": 40}, "character": None,
                            "code_point": None, "reading": None, "manual": True, "detected": False,
                            "proposed": False, "revision": 1}

    second = draw(api, {"x": 200, "y": 50, "w": 30, "h": 40}).json()
    assert second["line"] is None and second["unit"]["target_id"] == f"{PAGE}:l1:m2"
    assert len(Store(dataset).lines_of_page(PAGE)) == 1
    page = api.get(f"/atlas/pages/{PAGE}").json()
    assert [unit["id"] for unit in page["units"]] == [f"{PAGE}:l1:m1", f"{PAGE}:l1:m2"]
    assert api.get("/atlas/pages").json()["documents"][0]["pages"][0]["units"] == 2


def test_a_repeated_draw_answers_the_first_unit(api: TestClient, dataset: Path) -> None:
    box = {"x": 10, "y": 10, "w": 5, "h": 5}
    first = draw(api, box, key="same").json()
    again = draw(api, box, key="same").json()
    assert again["unit"]["duplicate"] is True and again["line"] is None
    assert again["unit"]["target_id"] == first["unit"]["target_id"]
    assert len(Store(dataset).units_of_page(PAGE)) == 1


@pytest.mark.parametrize("box", [
    {"x": 790, "y": 10, "w": 20, "h": 20},
    {"x": -1, "y": 10, "w": 20, "h": 20},
    {"x": 10, "y": 10, "w": 0, "h": 20},
])
def test_a_box_outside_the_page_is_refused(api: TestClient, box: dict) -> None:
    assert draw(api, box).status_code == 422
    assert api.get(f"/atlas/pages/{PAGE}").json()["units"] == []


def test_an_unknown_page_is_not_found(api: TestClient) -> None:
    assert draw(api, {"x": 1, "y": 1, "w": 2, "h": 2}, page="nope").status_code == 404


def test_a_drawn_box_takes_its_character_through_the_character_layer(api: TestClient) -> None:
    drawn = draw(api, {"x": 100, "y": 50, "w": 30, "h": 40}).json()["item"]
    page = api.get(f"/atlas/pages/{PAGE}").json()
    answer = api.post(f"/layers/units/{drawn['id']}", json={
        "id": str(uuid4()), "client_id": CLIENT, "revision": drawn["revision"],
        "image_sha256": page["image_sha256"], "character": "U+F67F",
        "verdict": "wrong", "issue": "character"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["resolved"] is True
    unit = api.get(f"/atlas/pages/{PAGE}").json()["units"][0]
    assert unit["code_point"] == "U+F67F" and unit["character"] == ""
    stored = api.app.state.store.unit(drawn["id"])
    assert stored.script == "gugyeol" and stored.review == "reviewed"


def test_a_retired_box_leaves_the_page_and_apply_writes_it(api: TestClient, dataset: Path) -> None:
    kept = draw(api, {"x": 100, "y": 50, "w": 30, "h": 40}).json()["item"]
    mistaken = draw(api, {"x": 300, "y": 50, "w": 30, "h": 40}).json()["item"]
    answer = api.post("/reviews", json={"target_type": "unit", "target_id": mistaken["id"], "field": "active",
                                        "new": False, "base_revision": mistaken["revision"],
                                        "client_id": CLIENT})
    assert answer.status_code == 200
    assert [unit["id"] for unit in api.get(f"/atlas/pages/{PAGE}").json()["units"]] == [kept["id"]]

    counts = apply(dataset)
    assert counts["lines"] == 1 and counts["units"] == 2
    lines = list(tables.read(dataset / "lines.parquet", Line))
    units = {unit.id: unit for unit in tables.read(dataset / "units.parquet", Unit)}
    assert lines[0].meta == {"scope": "page"}
    assert units[kept["id"]].active and not units[mistaken["id"]].active
    assert replay(dataset)["repaired"] == 0


def test_a_drawn_box_does_not_make_the_page_look_transcribed_or_aligned(api: TestClient) -> None:
    """The page line holds drawn boxes, not text: the page queue and the document totals still show
    a photograph nobody has transcribed or aligned."""
    assert draw(api, {"x": 40, "y": 40, "w": 20, "h": 30}).status_code == 201
    (item,) = [page for page in api.get("/pages").json()["items"] if page["id"] == PAGE]
    assert (item["state"], item["lines"], item["boxed_lines"]) == ("empty", 0, 0)
    assert api.get(f"/pages/{PAGE}").json()["lines"] == 0
    project = api.get("/project").json()
    (document,) = [d for d in project["documents"] if d["id"] == DOCUMENT]
    assert (document["lines"], document["boxed_pages"]) == (0, 0)


def test_the_page_line_is_not_a_text_line() -> None:
    page_line = Line(id=f"{PAGE}:l1", page_id=PAGE, seq=1, text_raw="", text="", meta={"scope": "page"})
    text_line = Line(id=f"{PAGE}:l2", page_id=PAGE, seq=2, text_raw="天", text="天")
    assert page_line.page_scope and not text_line.page_scope
