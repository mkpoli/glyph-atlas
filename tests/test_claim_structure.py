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


def test_a_regional_shape_has_its_characters_structure(dataset):  # noqa: F811
    """杮's K shape ⿰木市 stands under 杮's ⿰木巿; written alone it is 柿's."""
    client = TestClient(create_app(dataset))
    shingle, alone = structures.for_character("杮"), structures.for_ids("⿰木市")
    assert shingle.id != alone.id
    assert client.get("/layers/structures", params={"c": "⿰木市", "of": "杮"}).json()["items"] == [
        {"id": shingle.id, "label": shingle.label, "chars": ["⿰木市"]}]
    assert groups(client, "⿰木市")[0]["id"] == alone.id
    assert file(client, "⿰木市", char="⿰木市", of="杮")["structures"][0]["structure"] == shingle.id
    file(client, "⿰木市", char="⿱山夆", of="杮", status=422)


def fold(client: TestClient, source: dict, into: dict | None, *, reviewer: str = "ann", status: int = 200) -> dict:
    body = {"id": str(uuid4()), "client_id": reviewer, "from": source, **({"into": into} if into else {"remove": True})}
    response = client.post("/atlas/structures/folds", json=body)
    assert response.status_code == status, response.text
    return response.json()


def test_a_layout_stands_under_the_structure_it_is_one_of(dataset):  # noqa: F811
    """𽓻, the seal character of 遇, is ⿰辵禺 and stands under ⿺辶禺."""
    client = TestClient(create_app(dataset))
    meet, beside = structures.for_character("遇"), structures.for_character("𽓻")
    part = {"id": beside.id, "label": "⿰辵禺", "by": [], "chars": ["𽓻"]}
    assert groups(client, "遇𽓻") == [{"id": meet.id, "label": "⿺辶禺", "chars": ["遇", "𽓻"], "folded": [part]}]
    assert client.get("/layers/ids", params={"c": "𽓻"}).json()["structure"] == {
        "id": beside.id, "label": "⿰辵禺"}
    # A reader's filing of the seal character under the structure holds its place there.
    file(client, "𽓻", char="𽓻")
    assert groups(client, "遇𽓻")[0]["folded"] == [part]
    # What stands there does not depend on what else is asked for.
    file(client, "峰", char="𽓻")
    assert [(group["id"], group["chars"]) for group in groups(client, "峰")] == [(meet.id, ["峰"])]
    # Folds and layouts are followed to one end: 遇's structure folded into ⿰辵丌, which stands under ⿺辶丌.
    fold(client, {"char": "遇"}, {"char": "\U0003ddb3"})
    end = structures.for_character("䢋")
    assert [(group["id"], group["chars"]) for group in groups(client, "遇𽓻")] == [(end.id, ["遇", "𽓻"])]
    # A reader's fold of 遇's structure is followed on, and one under the layout wins.
    fold(client, {"char": "遇"}, {"char": "嵐"})
    assert [(group["id"], group["chars"]) for group in groups(client, "嵐遇𽓻")] == [(structures.for_character("嵐").id, ["嵐", "遇", "𽓻"])]
    fold(client, {"char": "遇"}, {"char": "𽓻"})
    assert [(group["id"], group["chars"]) for group in groups(client, "遇𽓻")] == [(beside.id, ["遇", "𽓻"])]


def test_a_structure_folds_into_another_and_its_forms_join_that_group(dataset):  # noqa: F811
    client = TestClient(create_app(dataset))
    wind = structures.for_character("嵐")
    folded = fold(client, {"char": "峯"}, {"char": "峰"})
    assert folded == {"structure": ABOVE.id, "replaced": None,
                      "folds_into": {"id": BESIDE.id, "label": BESIDE.label, "by": ["ann"]}}
    assert groups(client, "峰峯") == [{
        "id": BESIDE.id, "label": BESIDE.label, "chars": ["峰", "峯"],
        "folded": [{"id": ABOVE.id, "label": ABOVE.label, "by": ["ann"], "chars": ["峯"]}],
    }]
    # Folds are followed to their end, and one that would lead back is refused.
    fold(client, {"structure": BESIDE.id}, {"char": "嵐"})
    assert [(group["id"], group["chars"]) for group in groups(client, "峰峯嵐")] == [(wind.id, ["峰", "峯", "嵐"])]
    fold(client, {"char": "嵐"}, {"char": "峯"}, status=409)
    fold(client, {"char": "峰"}, {"char": "峯"}, status=409)
    fold(client, {"char": "峰"}, {"char": "峰"}, status=422)
    assert fold(client, {"char": "峯"}, {"char": "峰"})["replaced"] is None
    # A reader's new fold replaces their earlier one; only their own fold is theirs to take back.
    assert fold(client, {"char": "峯"}, {"char": "嵐"})["replaced"] == BESIDE.id
    fold(client, {"structure": ABOVE.id}, None, reviewer="bob", status=409)
    assert fold(client, {"structure": ABOVE.id}, None)["folds_into"] is None
    assert fold(client, {"structure": BESIDE.id}, None)["folds_into"] is None
    assert [group["chars"] for group in groups(client, "峰峯嵐")] == [["峰"], ["峯"], ["嵐"]]
    # Readers who disagree fold the structure nowhere.
    fold(client, {"char": "峯"}, {"char": "峰"})
    fold(client, {"char": "峯"}, {"char": "嵐"}, reviewer="bob")
    assert [group["chars"] for group in groups(client, "峰峯嵐")] == [["峰"], ["峯"], ["嵐"]]
