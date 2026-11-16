"""A form filed under a 構形: the claim, the group it joins, moving it and taking the claim back."""

from __future__ import annotations

from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
from test_review_atlas import dataset  # noqa: F401

from glyph_atlas import structures
from glyph_atlas.review.server import create_app

BESIDE, ABOVE = structures.for_character("峰"), structures.for_character("峯")
FORM = "⿰山夅"


def file(client: TestClient, form: str, *, reviewer: str = "ann", status: int = 200, **named) -> dict:
    response = client.post(
        "/atlas/forms/" + quote(form, safe="") + "/structure",
        json={"id": str(uuid4()), "client_id": reviewer, **named},
    )
    assert response.status_code == status, response.text
    return response.json()


def groups(client: TestClient, chars: str) -> list[dict]:
    return client.get("/layers/structures", params={"c": chars}).json()["items"]


def test_a_written_value_is_filed_under_a_structure_and_moves(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    assert BESIDE.id != ABOVE.id
    # 峯 stands with 峰 once a reader files it there, whatever its description gives.
    assert [group["chars"] for group in groups(client, "峰峯")] == [["峰"], ["峯"]]
    first = file(client, "峯", char="峰", replace=True)
    assert first["structures"] == [{"structure": BESIDE.id, "label": BESIDE.label, "by": ["ann"]}]
    assert first["replaced"] == []
    assert groups(client, "峰峯") == [
        {"id": BESIDE.id, "label": BESIDE.label, "chars": ["峰", "峯"], "claimed": {"峯": ["ann"]}}
    ]
    # A description nobody chose gets its form with the claim, and a move takes the earlier claim back.
    assert file(client, FORM, char="峯")["structures"][0]["structure"] == ABOVE.id
    moved = file(client, FORM, char="峰", replace=True)
    assert moved["replaced"] == [ABOVE.id]
    assert [value["structure"] for value in moved["structures"]] == [BESIDE.id]
    # A structure a claim has named is named by its id; one no claim has named is refused. A form two
    # readers filed apart stands under one structure, and the other is listed.
    assert file(client, FORM, structure=ABOVE.id, reviewer="bob")["id"] == moved["id"]
    first, second = sorted([(BESIDE, "ann"), (ABOVE, "bob")], key=lambda pair: pair[0].id)
    answer = client.get("/layers/structures", params={"c": FORM}).json()
    assert answer["items"] == [{"id": first[0].id, "label": first[0].label, "chars": [FORM], "claimed": {FORM: [first[1]]}}]
    assert answer["filed"] == {FORM: [{"id": second[0].id, "label": second[0].label, "by": [second[1]]}]}
    file(client, FORM, structure="st:" + "0" * 32, status=422)
    # Only the reader's own claim is theirs to take back, and the form returns to what it is built as.
    file(client, "峯", structure=BESIDE.id, remove=True, reviewer="bob", status=409)
    assert file(client, "峯", structure=BESIDE.id, remove=True)["replaced"] == [BESIDE.id]
    assert [group["chars"] for group in groups(client, "峰峯")] == [["峰"], ["峯"]]


def test_a_filing_refuses_what_names_nothing(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    file(client, "⿰山", char="峰", status=422)
    file(client, "峯", char="峰峯", status=422)
    file(client, "fm:" + "0" * 32, char="峰", status=404)
