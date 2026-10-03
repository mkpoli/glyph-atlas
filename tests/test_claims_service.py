"""The review service's claim routes: the ledger in the store, checked against the crop's evidence version."""
from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from test_review_atlas import LINE, dataset  # noqa: F401

from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import Store

UNIT = LINE + ":u0"


def claim(client, version, *values, reviewer="ann", status=200, **extra):
    body = {"id": str(uuid4()), "client_id": reviewer, "subject": UNIT, "predicate": "has_form", "crop_version": version,
            "claims": [{"value": v} for v in values], **extra}
    response = client.post("/atlas/claims", json=body)
    assert response.status_code == status, response.text
    return response.json(), body


def test_a_claim_names_the_version_the_reviewer_saw_and_resolves_its_slot(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    record = client.get("/atlas/characters/" + UNIT).json()
    version = record["crop_version"]
    made, body = claim(client, version, "unresolved")
    assert (made["current"][0]["status"], made["current"][0]["value"], made["current"][0]["crop_version"]) == ("asserted", "unresolved", version)
    assert client.post("/atlas/claims", json=body).json() == made, "a retry answers with the first response"
    assert client.post("/atlas/claims", json={**body, "claims": [{"value": "unreadable"}]}).status_code == 409
    claim(client, UNIT + "@x@1,2,3,4", "unresolved", status=409)
    claim(client, version, "legible", status=422)
    rival, _ = claim(client, version, "unreadable", reviewer="bob")
    assert rival["current"][0]["status"] == "disputed"
    action = {"id": str(uuid4()), "client_id": "bob", "action": "retract"}
    assert client.post(f"/atlas/claims/{made['assertions'][0]}/actions", json=action).status_code == 403
    adjudication = {"id": str(uuid4()), "client_id": "cat", "action": "adjudicate"}
    assert client.post(f"/atlas/claims/{rival['assertions'][0]}/actions", json=adjudication).status_code == 403, \
        "adjudication is the site's admins' to make"
    retracted = client.post(f"/atlas/claims/{made['assertions'][0]}/actions",
                            json={"id": str(uuid4()), "client_id": "ann", "action": "retract"}).json()
    assert (retracted["current"][0]["status"], retracted["current"][0]["value"]) == ("asserted", "unreadable")
    listed = client.get("/atlas/claims", params={"subject": UNIT}).json()
    assert [len(listed["current"]), len(listed["history"])] == [1, 2]
    assert listed["history"][0]["actions"][0]["action"] == "retract"


def test_a_recut_crop_has_no_standing_claim_until_someone_looks_again(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    record = client.get("/atlas/characters/" + UNIT).json()
    claim(client, record["crop_version"], "unreadable")
    box = {**record["box"], "w": record["box"]["w"] + 4}
    saved = client.post("/atlas/characters/" + UNIT, json={
        "id": str(uuid4()), "client_id": "dragger", "revision": record["revision"], "image_sha256": record["image_sha256"],
        "verdict": "match", "box": box})
    assert saved.status_code == 200, saved.text
    recut = client.get("/atlas/characters/" + UNIT).json()["crop_version"]
    listed = client.get("/atlas/claims", params={"subject": UNIT}).json()
    assert listed["current"] == [] and len(listed["history"]) == 1
    claim(client, record["crop_version"], "unreadable", reviewer="bob", status=409)
    renewed, _ = claim(client, recut, "unreadable", reviewer="bob")
    assert renewed["current"][0]["supporting"] == renewed["assertions"]


def test_the_ledger_survives_a_reopened_store_and_rebuilds_to_the_same_rows(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    version = client.get("/atlas/characters/" + UNIT).json()["crop_version"]
    claim(client, version, "unresolved")
    claim(client, version, "unreadable", reviewer="bob")
    store = Store(dataset)
    with store._connection() as conn:
        before = conn.execute("SELECT * FROM current_claims").fetchall()
    assert store.resolve_claims(lambda unit: version if unit == UNIT else None) == 1
    with store._connection() as conn:
        assert conn.execute("SELECT * FROM current_claims").fetchall() == before
