"""A form written for a grapheme: the claim, the grapheme card it joins, and taking the claim back."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from test_review_atlas import LINE, dataset  # noqa: F401

from glyph_atlas.review.server import create_app

UNIT = LINE + ":u0"
GRAPHEME = "U+3042"
FORM = "⿰木木"


def form_of(client: TestClient) -> str:
    """A described form on the unit's crop, and its form id."""
    record = client.get("/atlas/characters/" + UNIT).json()
    made = client.post(
        "/atlas/characters/" + UNIT + "/form",
        json={"id": str(uuid4()), "client_id": "ann", "crop_version": record["crop_version"], "form": FORM},
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


def test_a_form_joins_the_graphemes_card_and_is_taken_back(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    form = form_of(client)
    first = link(client, form)
    assert first["written_for"] == [{"grapheme": GRAPHEME, "char": "あ", "by": ["ann"]}]
    assert client.get("/layers/characters/" + GRAPHEME).json()["variants"]["forms"] == [
        {"char": FORM, "code_point": None, "form": form, "scheme": "ids", "count": 0, "corpus_count": 0}
    ]
    # A second reviewer joins the claim; a repeat of their save changes nothing.
    assert link(client, form, reviewer="bob")["written_for"][0]["by"] == ["ann", "bob"]
    assert link(client, form, reviewer="bob")["written_for"][0]["by"] == ["ann", "bob"]
    # Only the reader's own claim is theirs to take back.
    assert link(client, form, remove=True, reviewer="bob")["written_for"][0]["by"] == ["ann"]
    link(client, form, remove=True, reviewer="bob", status=409)
    assert link(client, form, remove=True)["written_for"] == []
    assert client.get("/layers/characters/" + GRAPHEME).json()["variants"]["forms"] == []
    history = client.get("/atlas/claims", params={"subject": form}).json()
    assert [row for row in history["current"] if row["predicate"] == "written_for"] == []
    assert len([row for row in history["history"] if row["predicate"] == "written_for"]) == 2


def test_a_claim_names_a_family_head_and_a_form_the_site_holds(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    form = form_of(client)
    # ア is filed under あ's family, and names no family of its own.
    link(client, form, grapheme="U+30A2", status=422)
    link(client, form, grapheme="U+1F600", status=422)
    link(client, "fm:" + "0" * 32, status=404)
