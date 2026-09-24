"""What a reviewer's answer does to an occurrence's state: resolved, flagged, or left alone.

An explicit wrong-character correction — verdict `wrong`, issue `character`, and a character that
differs from the one on the record — is a resolution: the identity now says what a person says it is,
so the occurrence is `checked` and no longer sits in the review queue. Every other answer keeps the
existing contract: a crop, joined characters, a blank, a reading-only edit and `unclear` are
`disputed`, and a wrong-character issue that names the character already on the record is a
contradiction rather than a correction, so it never confirms anything on its own.

The tests run the real service over a temporary dataset. `glyph_atlas.review.characters` is loaded
from this worktree, so the file under test is this one; every other module is the installed package.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import glyph_atlas.review  # noqa: F401 — the package first, so the loader below wins the name
from glyph_atlas import tables
from glyph_atlas.review import characters
from glyph_atlas.review.server import create_app
from glyph_atlas.schema import Box, Document, Line, Page, Unit

PAGE = "hk:entry:1"
LINE = f"{PAGE}:L1"
CHI, KO, TSU, MA = "チ", "こ", "つ", "マ"
CHI_POINT, KO_POINT = "U+30C1", "U+3053"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    image = tmp_path / "scan.jpg"
    picture = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(picture)
    for i in range(4):
        draw.line([(20 + i * 60, 10), (30 + i * 60, 200)], fill="black", width=4)
    picture.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())

    tables.write(root / "documents.parquet", [Document(id="d", title="資料")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0, image="x",
                 width=300, height=300, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0, text_raw="チ",
                 text="チ", box=Box(x=0, y=0, w=300, h=300))], Line)
    tables.write(root / "units.parquet", [
        Unit(id=f"{LINE}:u0", document_id="d", page_id=PAGE, line_id=LINE, seq=0, unicode=CHI_POINT,
             reading="ち", text_source="チ", box=Box(x=20, y=10, w=40, h=60)),
        Unit(id=f"{LINE}:u1", document_id="d", page_id=PAGE, line_id=LINE, seq=1, unicode=CHI_POINT,
             reading="ち", text_source="チ", box=Box(x=80, y=10, w=40, h=60)),
    ], Unit)
    return {"root": root, "digest": digest, "client": TestClient(create_app(root))}


def edit(unit_id: str, revision: int, digest: str, **overrides) -> dict:
    payload = {"id": str(uuid4()), "client_id": "reviewer-1", "revision": revision,
               "image_sha256": digest, "verdict": "wrong", "issue": "character",
               "character": KO, "reading": None, "box": None, "note": ""}
    payload.update(overrides)
    return payload


def sample(client, unit_id: str) -> dict:
    detail = client.get(f"/atlas/characters/{unit_id}").json()
    return detail


def state_of(client, unit_id: str) -> str:
    return client.get(f"/atlas/characters/{unit_id}").json()["state"]


def revision_of(client, unit_id: str) -> int:
    return client.get(f"/atlas/characters/{unit_id}").json()["revision"]


def journal(root: Path) -> list[dict]:
    path = root / "reviews.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def events_for(client, unit_id: str, module) -> list:
    """The journal as the store replays it, without applying anything to the tables."""
    from glyph_atlas.review.store import Store

    return [event for event in Store(client.app.state.directory).events()
            if event.target_id == unit_id]


# The resolution ---------------------------------------------------------------------------------

def test_an_explicit_character_correction_resolves_the_occurrence(dataset):
    client, digest = dataset["client"], dataset["digest"]
    unit = f"{LINE}:u0"
    assert state_of(client, unit) == "pending"

    answer = client.post(f"/layers/units/{unit}", json=edit(unit, 0, digest)).json()
    assert answer["resolved"] is True
    assert answer["changed"] == ["character", "script"], "the identity and the script it implies"
    assert [row["field"] for row in answer["results"]] == ["unicode", "script", "review"]
    assert answer["results"][-1]["review"]["new"] == "reviewed", "the review confirms the record"
    assert answer["layers"]["code_point"] == KO_POINT and answer["layers"]["character"] == KO

    detail = sample(client, unit)
    assert detail["label"] == KO and detail["reading"] == "ち", "the reading is untouched"
    assert detail["state"] == "checked", "a resolved occurrence leaves the queue"
    names = [event.field for event in events_for(client, unit, characters)]
    assert names == ["unicode", "script", "review"]
    review = next(event for event in events_for(client, unit, characters) if event.field == "review")
    evidence = json.loads(review.evidence)
    assert evidence["resolved"] is True and evidence["issue"] == "character"
    assert evidence["layer_correction"]["code_point"] == KO_POINT
    assert evidence["layer_correction"]["reading"] == "ち", "the reading layer is recorded as it was"


def test_the_reading_is_written_only_when_it_is_edited(dataset):
    client, digest = dataset["client"], dataset["digest"]
    unit = f"{LINE}:u0"
    client.post(f"/layers/units/{unit}", json=edit(unit, 0, digest))
    stored = events_for(client, unit, characters)
    assert not [event for event in stored if event.field == "reading"]

    other = f"{LINE}:u1"
    answer = client.post(f"/layers/units/{other}", json=edit(
        other, 0, digest, character=TSU, reading="つ")).json()
    assert answer["resolved"] is True
    assert answer["changed"] == ["character", "script", "reading"]
    assert answer["layers"]["reading"] == "つ"
    assert state_of(client, other) == "checked"


def test_a_wrong_character_issue_that_names_the_stored_character_confirms_nothing(dataset):
    """The character did not move, so the answer is a contradiction, not a resolution."""
    client, digest = dataset["client"], dataset["digest"]
    unit = f"{LINE}:u0"
    answer = client.post(f"/layers/units/{unit}", json=edit(
        unit, 0, digest, character=CHI, box={"x": 25, "y": 10, "w": 40, "h": 60})).json()
    assert answer["resolved"] is False
    assert answer["changed"] == ["crop"], "only the crop moved"
    assert answer["results"][-1]["review"]["new"] == "disputed"
    assert state_of(client, unit) == "flagged"
    detail = sample(client, unit)
    assert detail["label"] == CHI

    # Naming the stored character and changing nothing else is refused outright: there is no
    # correction in the request, so nothing is confirmed.
    alone = client.post(f"/layers/units/{unit}",
                        json=edit(unit, revision_of(client, unit), digest, character=CHI))
    assert alone.status_code == 422, alone.text
    assert state_of(client, unit) == "flagged"


def test_every_other_answer_stays_disputed(dataset):
    """A crop, joined characters, a blank and an unclear answer flag the record, never confirm it."""
    client, digest = dataset["client"], dataset["digest"]
    moved = {"x": 25, "y": 12, "w": 40, "h": 60}
    answers = [
        ("crop", f"{LINE}:u0", edit(f"{LINE}:u0", 0, digest, issue="crop", character=None, box=moved)),
        ("merged", f"{LINE}:u1", edit(f"{LINE}:u1", 0, digest, issue="merged", character=None,
                                      reading="つ")),
        ("blank", f"{LINE}:u1", edit(f"{LINE}:u1", 0, digest, issue="blank", character=None,
                                     box=moved)),
        ("unclear", f"{LINE}:u1", edit(f"{LINE}:u1", 0, digest, verdict="unsure", issue="unclear",
                                       character=None, box={"x": 30, "y": 14, "w": 40, "h": 60})),
    ]
    for name, unit, payload in answers:
        payload = {**payload, "revision": revision_of(client, unit)}
        answer = client.post(f"/layers/units/{unit}", json=payload)
        assert answer.status_code == 200, f"{name}: {answer.text}"
        body = answer.json()
        assert body["resolved"] is False, name
        assert body["results"][-1]["review"]["new"] == "disputed", name
        assert state_of(client, unit) == "flagged", name
        assert "character" not in body["changed"], name


def test_a_reading_only_edit_is_disputed(dataset):
    """A phonetic edit is recorded where it belongs and does not move the written identity."""
    client, digest = dataset["client"], dataset["digest"]
    unit = f"{LINE}:u0"
    reading = client.post(f"/layers/units/{unit}", json=edit(
        unit, 0, digest, issue="reading", character=None, reading="つ", verdict="wrong"))
    assert reading.status_code == 200, reading.text
    body = reading.json()
    assert body["resolved"] is False
    assert body["changed"] == ["reading"]
    assert body["results"][-1]["review"]["new"] == "disputed"
    detail = sample(client, unit)
    assert detail["label"] == CHI, "the identity is the source's, not the reading's"
    assert detail["reading"] == "つ"
    assert detail["state"] == "flagged"


# The journal, the export and the retry -----------------------------------------------------------

def test_the_export_reports_the_resolution_and_the_retry_answers_it(dataset):
    client, digest = dataset["client"], dataset["digest"]
    unit = f"{LINE}:u0"
    payload = edit(unit, 0, digest)
    first = client.post(f"/layers/units/{unit}", json=payload)
    assert first.status_code == 200, first.text

    replay = client.post(f"/layers/units/{unit}", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["duplicate"] is True and replay.json()["resolved"] is True
    assert replay.json()["layers"]["code_point"] == KO_POINT
    assert len(events_for(client, unit, characters)) == 3, "a retry writes no event"

    exported = client.get("/atlas/reviews").json()["reviews"]
    row = next(row for row in exported if row["event"]["target_id"] == unit)
    assert row["current"] is True, "the review stands"
    assert json.loads(row["event"]["evidence"])["resolved"] is True

    # A stale revision is still refused: the resolution is a decision like any other.
    assert client.post(f"/layers/units/{unit}", json=edit(unit, 0, digest)).status_code == 409
