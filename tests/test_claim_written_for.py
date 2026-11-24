"""A form written for a grapheme: the claim, the grapheme card it joins, and taking the claim back."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from test_review_atlas import LINE, dataset  # noqa: F401

from glyph_atlas.review.server import create_app

UNIT = LINE + ":u0"
GRAPHEME = "U+3042"
FORM = "⿰木木"


def form_of(client: TestClient, value: str = FORM) -> str:
    """A form on the unit's crop, described unless `value` names another, and its form id."""
    record = client.get("/atlas/characters/" + UNIT).json()
    made = client.post(
        "/atlas/characters/" + UNIT + "/form",
        json={"id": str(uuid4()), "client_id": "ann", "crop_version": record["crop_version"], "form": value},
    )
    assert made.status_code == 200, made.text
    return made.json()["form"]["values"][0]["form"]


def link(
    client: TestClient,
    form: str,
    *,
    grapheme: str = GRAPHEME,
    remove: bool = False,
    reviewer: str = "ann",
    status: int = 200,
) -> dict:
    response = client.post(
        "/atlas/forms/" + form + "/written_for",
        json={"id": str(uuid4()), "client_id": reviewer, "grapheme": grapheme, "remove": remove},
    )
    assert response.status_code == status, response.text
    return response.json()


def test_a_form_is_written_for_a_grapheme_and_taken_back(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    form = form_of(client)
    first = link(client, form)
    assert first["written_for"] == [{"grapheme": GRAPHEME, "char": "あ", "by": ["ann"]}]
    # A second reviewer joins the claim; a repeat of their save changes nothing.
    assert link(client, form, reviewer="bob")["written_for"][0]["by"] == ["ann", "bob"]
    assert link(client, form, reviewer="bob")["written_for"][0]["by"] == ["ann", "bob"]
    # Only the reader's own claim is theirs to take back.
    assert link(client, form, remove=True, reviewer="bob")["written_for"][0]["by"] == ["ann"]
    link(client, form, remove=True, reviewer="bob", status=409)
    assert link(client, form, remove=True)["written_for"] == []
    history = client.get("/atlas/claims", params={"subject": form}).json()
    assert [row for row in history["current"] if row["predicate"] == "written_for"] == []
    assert len([row for row in history["history"] if row["predicate"] == "written_for"]) == 2


def test_a_form_a_crop_carries_is_on_its_graphemes_card(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    listed = lambda: client.get("/layers/characters/" + GRAPHEME).json()["variants"]["forms"]
    assert listed() == []
    form = form_of(client)
    assert listed() == [{"char": FORM, "code_point": None, "form": form, "scheme": "ids", "count": 1, "corpus_count": 0}]
    # A registered figure joins it as a description does, and a form leaves with the last crop that has it.
    figure = "突\U000e0103"
    other = form_of(client, figure)
    assert listed() == [{"char": figure, "code_point": None, "form": other, "scheme": "ivs", "count": 1, "corpus_count": 0}]


def test_a_claim_names_the_head_its_key_is_filed_under_and_a_form_the_site_holds(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    form = form_of(client)
    # ア is filed under あ: a claim under its key is あ's, is taken back by either, and is the same claim sent again.
    sent = {"id": str(uuid4()), "client_id": "ann", "grapheme": "U+30A2", "remove": False}
    first = client.post("/atlas/forms/" + form + "/written_for", json=sent).json()
    assert first == {"id": form, "grapheme": GRAPHEME, "written_for": [{"grapheme": GRAPHEME, "char": "あ", "by": ["ann"]}]}
    assert client.post("/atlas/forms/" + form + "/written_for", json=sent).json()["written_for"] == first["written_for"]
    assert client.post("/atlas/forms/" + form + "/written_for", json={**sent, "grapheme": GRAPHEME}).status_code == 409
    assert link(client, form, grapheme="U+30A2", remove=True)["written_for"] == []
    link(client, form, grapheme="U+1F600", remove=True, status=409)
    link(client, form, grapheme="U+1F600", status=422)
    link(client, "fm:" + "0" * 32, status=404)
