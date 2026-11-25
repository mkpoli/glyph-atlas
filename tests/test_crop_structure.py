"""A crop's structure given with no form: the claim, what it takes back, and taking it back."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from test_review_atlas import LINE, dataset  # noqa: F401

from glyph_atlas.review.server import create_app

UNIT = LINE + ":u0"


def test_a_crop_is_given_a_structure_with_no_form(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    record = lambda: client.get("/atlas/characters/" + UNIT).json()
    version = record()["crop_version"]
    post = lambda kind, status=200, **body: _post(client, kind, version, status, **body)
    assert record()["structure"] is None
    post("form", form="⿰山夆")
    saved = post("structure", char="峰")
    assert (saved["replaced"], saved["replaced_form"], saved["form"]) == (None, "⿰山夆", None)
    assert [(value["label"], value["by"]) for value in saved["structure"]["values"]] == [("⿰山夆", ["ann"])]
    assert record()["structure"] == saved["structure"] and record()["form"] is None
    beside = saved["structure"]["values"][0]["structure"]
    assert post("structure", char="峯")["replaced"] == beside
    # A form chosen later leaves it; another reader who names another structure disagrees.
    assert post("form", form="⿱山夆")["form"]["status"] == "asserted"
    assert record()["structure"]["values"][0]["label"] == "⿱山夆"
    assert post("structure", client_id="bob", char="峰")["structure"]["status"] == "disputed"
    assert [value["label"] for value in post("structure")["structure"]["values"]] == ["⿰山夆"]
    post("structure", status=409)
    assert _post(client, "structure", "old", 409, char="峰")


def _post(client: TestClient, kind: str, version: str, status: int, client_id: str = "ann", **body) -> dict:
    response = client.post(f"/atlas/characters/{UNIT}/{kind}", json={"id": str(uuid4()), "client_id": client_id, "crop_version": version, **body})
    assert response.status_code == status, response.text
    return response.json()
