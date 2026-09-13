"""The review service, end to end, against a dataset built in `tmp_path`.

The fixture writes two documents, three pages, six lines and eighteen units through `tables.write`,
so the tests read exactly what an importer would leave on disk. The image cache is redirected to
`tmp_path` through `KUZUSHIJI_ATLAS_CACHE`; nothing here touches the network or the repository
cache.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kuzushiji_atlas import tables
from kuzushiji_atlas.review import create_app
from kuzushiji_atlas.review.store import Store, apply, replay
from kuzushiji_atlas.schema import (
    Box,
    Candidate,
    Classification,
    Document,
    Line,
    Page,
    Script,
    Unit,
)

#: A stand-in for a cached page image; the endpoint serves the bytes as they are.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"
SHA256 = hashlib.sha256(JPEG).hexdigest()
UNITS_PER_LINE = 3


@dataclass
class Fixture:
    """The ids of the dataset the tests work on."""

    directory: Path
    documents: list[str]
    page: str
    other_page: str
    line: str
    other_line: str
    unit: str
    ambiguous: str


def build(directory: Path) -> Fixture:
    """Write the fixture dataset and return the ids the tests need."""
    directory.mkdir(parents=True, exist_ok=True)
    documents = [Document(id=f"doc-{number}", title=f"Book {number}") for number in (1, 2)]
    pages = [
        Page(
            id="doc-1:p1",
            document_id="doc-1",
            seq=1,
            image="https://example.org/doc-1/1.jpg",
            width=1000,
            height=1500,
            sha256=SHA256,
        ),
        Page(
            id="doc-1:p2",
            document_id="doc-1",
            seq=2,
            image="https://example.org/doc-1/2.jpg",
            width=1000,
            height=1500,
        ),
        Page(
            id="doc-2:p1",
            document_id="doc-2",
            seq=1,
            image="https://example.org/doc-2/1.jpg",
            width=1000,
            height=1500,
        ),
    ]
    lines: list[Line] = []
    units: list[Unit] = []
    for page in pages:
        for seq in range(2):
            line_id = f"{page.id}:line{seq}"
            lines.append(
                Line(
                    id=line_id,
                    page_id=page.id,
                    seq=seq,
                    box=Box(x=100, y=50 + 400 * seq, w=400, h=300),
                    text_raw=f"あいう{seq}",
                    text=f"あいう{seq}",
                )
            )
            for number in range(UNITS_PER_LINE):
                unit_id = f"{line_id}:u{number}"
                units.append(
                    Unit(
                        id=unit_id,
                        document_id=page.document_id,
                        page_id=page.id,
                        line_id=line_id,
                        seq=number,
                        box=Box(x=100 + 120 * number, y=60 + 400 * seq, w=100, h=100),
                        reading="あいう"[number],
                        text_source="あいう"[number],
                        unicode=f"U+304{2 + 2 * number}",
                        classification=Classification.IDENTIFIED,
                        script=Script.HIRAGANA,
                    )
                )
    ambiguous = "doc-1:p1:line0:u0"
    units[0] = units[0].model_copy(
        update={
            "classification": Classification.AMBIGUOUS,
            "candidates": [
                Candidate(unicode="U+3042", p=0.4),
                Candidate(unicode="U+1B002", p=0.6, jibo="安"),
            ],
        }
    )
    for name, records, model in (
        ("documents", documents, Document),
        ("pages", pages, Page),
        ("lines", lines, Line),
        ("units", units, Unit),
    ):
        tables.write(directory / f"{name}.parquet", records, model)
    return Fixture(
        directory=directory,
        documents=["doc-1", "doc-2"],
        page="doc-1:p1",
        other_page="doc-2:p1",
        line="doc-1:p1:line0",
        other_line="doc-1:p1:line1",
        unit=ambiguous,
        ambiguous=ambiguous,
    )


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return build(tmp_path / "dataset")


@pytest.fixture
def client(fixture: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client over the fixture dataset, with the image cache under `tmp_path`."""
    monkeypatch.setenv("KUZUSHIJI_ATLAS_CACHE", str(tmp_path / "cache"))
    with TestClient(create_app(fixture.directory)) as test_client:
        yield test_client


def review(client: TestClient, **body: Any) -> tuple[int, dict[str, Any]]:
    """Post one review and return its status and its single result."""
    response = client.post("/reviews", json=body)
    payload = response.json()
    return response.status_code, (payload["results"][0] if "results" in payload else payload["detail"])


def state(client: TestClient, unit_id: str) -> Unit:
    return client.app.state.store.unit(unit_id)


def active_units(client: TestClient, line_id: str) -> list[str]:
    body = client.get(f"/lines/{line_id}/units").json()
    return [unit["id"] for unit in body["items"]]


# -- reading the dataset -------------------------------------------------------------------------


def test_documents_carry_their_counts_and_paginate(client: TestClient, fixture: Fixture) -> None:
    body = client.get("/documents").json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == fixture.documents
    first = body["items"][0]
    assert (first["pages"], first["lines"], first["units"], first["reviewed"]) == (2, 4, 12, 0)
    window = client.get("/documents", params={"limit": 1, "offset": 1}).json()
    assert window["limit"] == 1 and window["offset"] == 1 and window["total"] == 2
    assert [item["id"] for item in window["items"]] == ["doc-2"]


def test_page_carries_its_counts_and_image_url(client: TestClient, fixture: Fixture) -> None:
    body = client.get(f"/pages/{fixture.page}").json()
    assert body["id"] == fixture.page and body["document_id"] == "doc-1"
    assert (body["lines"], body["units"], body["reviewed"]) == (2, 6, 0)
    assert body["image_url"] == f"/images/{SHA256}"
    uncached = client.get(f"/pages/{fixture.other_page}").json()
    assert uncached["image_url"] == "https://example.org/doc-2/1.jpg"
    assert client.get("/pages/nope").status_code == 404


def test_page_image_is_served_from_the_cache(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "cache" / "images" / SHA256[:2]
    folder.mkdir(parents=True)
    (folder / f"{SHA256}.jpg").write_bytes(JPEG)
    response = client.get(f"/images/{SHA256}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG
    assert client.get(f"/images/{'0' * 64}").status_code == 404
    assert client.get("/images/not-a-checksum").status_code == 404


def test_page_lines_and_line_units(client: TestClient, fixture: Fixture) -> None:
    lines = client.get(f"/pages/{fixture.page}/lines").json()
    assert lines["total"] == 2
    assert [line["seq"] for line in lines["items"]] == [0, 1]
    first = lines["items"][0]
    assert first["id"] == fixture.line and first["units"] == UNITS_PER_LINE and first["revision"] == 0
    units = client.get(f"/lines/{fixture.line}/units").json()
    assert units["total"] == UNITS_PER_LINE
    assert [unit["seq"] for unit in units["items"]] == [0, 1, 2]
    assert all(unit["revision"] == 0 for unit in units["items"])
    assert client.get("/lines/nope/units").status_code == 404


def test_candidates_carry_jibo_reference_glyphs_and_scores(client: TestClient, fixture: Fixture) -> None:
    body = client.get(f"/units/{fixture.ambiguous}/candidates").json()
    assert body["reading"] == "あ" and body["classification"] == "ambiguous"
    code_points = [candidate["unicode"] for candidate in body["candidates"]]
    assert code_points[0] == "U+3042"
    assert "U+1B002" in code_points
    hiragana = body["candidates"][0]
    assert hiragana["char"] == "あ" and hiragana["current"] is True and hiragana["score"] == 0.4
    reference = next(candidate for candidate in body["candidates"] if candidate["unicode"] == "U+1B002")
    assert reference["jibo"] == "安" and reference["score"] == 0.6
    assert reference["reference_url"].startswith("https://cid.ninjal.ac.jp/kana/detail/")
    assert reference["mj"].startswith("MJ")
    assert client.get("/units/nope/candidates").status_code == 404


def test_queue_strategies_filter_and_paginate(client: TestClient, fixture: Fixture) -> None:
    unreviewed = client.get("/queue", params={"strategy": "unreviewed"}).json()
    assert unreviewed["total"] == 6 and len(unreviewed["items"]) == 6
    disagreement = client.get("/queue", params={"strategy": "disagreement"}).json()
    assert [item["id"] for item in disagreement["items"]] == [fixture.ambiguous.rsplit(":", 1)[0]]
    assert disagreement["items"][0]["disagreements"] == 1
    shuffled = client.get("/queue", params={"strategy": "random", "seed": 7}).json()
    again = client.get("/queue", params={"strategy": "random", "seed": 7}).json()
    assert [item["id"] for item in shuffled["items"]] == [item["id"] for item in again["items"]]
    assert sorted(item["id"] for item in shuffled["items"]) == sorted(
        item["id"] for item in unreviewed["items"]
    )
    window = client.get("/queue", params={"limit": 2, "offset": 5}).json()
    assert window["total"] == 6 and len(window["items"]) == 1
    document = client.get("/queue", params={"document": "doc-2"}).json()
    assert document["total"] == 2 and {item["document_id"] for item in document["items"]} == {"doc-2"}
    assert client.get("/queue", params={"strategy": "nonsense"}).status_code == 422


# -- recording reviews ---------------------------------------------------------------------------


def test_box_move_and_reading_change(client: TestClient, fixture: Fixture) -> None:
    box = {"x": 510, "y": 80, "w": 40, "h": 40}
    moved = review(client, target_type="unit", target_id=fixture.unit, field="box", new=box, base_revision=0)
    status, result = moved
    assert status == 200 and result["revision"] == 1 and result["state"]["box"] == box
    assert result["review"]["old"] == {"x": 100, "y": 60, "w": 100, "h": 100}
    assert result["review"]["role"] == "reviewer" and result["review"]["at"]
    renamed = review(
        client, target_type="unit", target_id=fixture.unit, field="reading", new="き", base_revision=1
    )
    status, result = renamed
    assert status == 200 and result["revision"] == 2 and result["state"]["reading"] == "き"
    assert result["review"]["old"] == "あ"
    units = client.get(f"/lines/{fixture.line}/units").json()["items"]
    match = next(unit for unit in units if unit["id"] == fixture.unit)
    assert match["box"] == box and match["reading"] == "き" and match["revision"] == 2


def test_line_review_and_a_page_timing_event(client: TestClient, fixture: Fixture) -> None:
    status, result = review(
        client, target_type="line", target_id=fixture.line, field="text", new="かきく", base_revision=0
    )
    assert status == 200 and result["revision"] == 1 and result["review"]["old"] == "あいう0"
    assert result["state"]["text"] == "かきく" and result["state"]["text_raw"] == "あいう0"
    lines = client.get(f"/pages/{fixture.page}/lines").json()["items"]
    match = next(line for line in lines if line["id"] == fixture.line)
    assert match["text"] == "かきく" and match["revision"] == 1
    status, timing = review(
        client, target_type="page", target_id=fixture.page, field="timing", new={"closed_ms": 900}
    )
    assert status == 200 and timing["revision"] == 1 and timing["state"] is None
    assert review(client, target_type="page", target_id="nope", field="timing", new=1)[0] == 404


def test_stale_base_revision_answers_409_with_the_current_state(
    client: TestClient, fixture: Fixture
) -> None:
    review(client, target_type="unit", target_id=fixture.unit, field="reading", new="き", base_revision=0)
    status, detail = review(
        client, target_type="unit", target_id=fixture.unit, field="reading", new="く", base_revision=0
    )
    assert status == 409
    assert detail["error"] == "stale-revision"
    assert detail["base_revision"] == 0 and detail["revision"] == 1
    assert detail["state"]["reading"] == "き"
    assert state(client, fixture.unit).reading == "き"


def test_repeated_idempotency_key_answers_the_earlier_result(client: TestClient, fixture: Fixture) -> None:
    body = {
        "target_type": "unit",
        "target_id": fixture.unit,
        "field": "reading",
        "new": "き",
        "base_revision": 0,
        "client_id": "reviewer-1",
        "idempotency_key": "abc",
    }
    first = client.post("/reviews", json=body).json()["results"][0]
    again = client.post("/reviews", json=body).json()["results"][0]
    assert again["duplicate"] is True and first["duplicate"] is False
    assert again["id"] == first["id"] and again["revision"] == 1
    assert len(client.app.state.store.events()) == 1
    assert state(client, fixture.unit).reading == "き"


def test_split_then_merge(client: TestClient, fixture: Fixture) -> None:
    line, unit = fixture.line, fixture.unit
    split = review(
        client,
        target_type="unit",
        target_id=unit,
        field="segmentation",
        new={
            "split": [
                {"box": {"x": 100, "y": 60, "w": 50, "h": 100}, "reading": "か"},
                {"box": {"x": 150, "y": 60, "w": 50, "h": 100}, "reading": "か"},
            ]
        },
        base_revision=0,
    )
    status, result = split
    assert status == 200
    created = [f"{line}:m1", f"{line}:m2"]
    assert result["created"] == created and result["retired"] == [unit]
    assert result["warnings"] == []
    assert state(client, unit).active is False
    assert state(client, unit).split_into == created
    assert active_units(client, line) == [*created, f"{line}:u1", f"{line}:u2"]
    assert state(client, created[0]).seq == 0 and state(client, created[1]).seq == 1
    assert state(client, created[0]).reading == "か"
    assert state(client, created[0]).method == "manual"

    merge = review(
        client,
        target_type="unit",
        target_id=created[0],
        field="segmentation",
        new={"merge": created},
        base_revision=0,
    )
    status, result = merge
    assert status == 200
    merged = f"{line}:m3"
    assert result["created"] == [merged] and result["retired"] == created
    assert state(client, merged).active is True
    assert state(client, merged).reading == "かか"
    assert state(client, merged).kind == "ligature"
    assert state(client, merged).box == Box(x=100, y=60, w=100, h=100)
    assert [state(client, name).merged_into for name in created] == [merged, merged]
    assert active_units(client, line) == [merged, f"{line}:u1", f"{line}:u2"]


def test_split_outside_the_line_box_is_accepted_and_flagged(client: TestClient, fixture: Fixture) -> None:
    status, result = review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="segmentation",
        new={
            "split": [
                {"box": {"x": 10, "y": 10, "w": 40, "h": 40}, "reading": "か"},
                {"box": {"x": 600, "y": 60, "w": 40, "h": 40}, "reading": "か"},
            ]
        },
        base_revision=0,
    )
    assert status == 200
    assert len(result["warnings"]) == 2
    assert all("leaves line" in warning for warning in result["warnings"])
    assert state(client, result["created"][0]).active is True


def test_review_of_a_retired_unit_answers_409(client: TestClient, fixture: Fixture) -> None:
    review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="segmentation",
        new={
            "split": [
                {"box": {"x": 100, "y": 60, "w": 50, "h": 100}, "reading": "か"},
                {"box": {"x": 150, "y": 60, "w": 50, "h": 100}, "reading": "か"},
            ]
        },
        base_revision=0,
    )
    status, detail = review(
        client, target_type="unit", target_id=fixture.unit, field="reading", new="き", base_revision=1
    )
    assert status == 409
    assert detail["error"] == "retired"
    assert detail["state"]["active"] is False and detail["state"]["split_into"] == [
        f"{fixture.line}:m1",
        f"{fixture.line}:m2",
    ]


def test_a_line_the_detector_missed(client: TestClient, fixture: Fixture) -> None:
    response = client.post(
        "/lines",
        json={
            "page_id": fixture.page,
            "box": {"x": 600, "y": 50, "w": 200, "h": 300},
            "text_raw": "えお",
            "text": "えお",
            "client_id": "reviewer-2",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["target_id"] == f"{fixture.page}:l1" and body["revision"] == 1
    assert body["state"]["match_method"] == "manual" and body["state"]["seq"] == 2
    assert body["review"]["role"] == "transcriber" and body["review"]["field"] == "create"
    lines = client.get(f"/pages/{fixture.page}/lines").json()
    assert lines["total"] == 3 and [line["id"] for line in lines["items"]][-1] == body["target_id"]
    assert client.post("/lines", json={"page_id": "nope", "box": {"x": 1, "y": 1, "w": 2, "h": 2}}).status_code == 404


def test_a_unit_on_an_existing_line(client: TestClient, fixture: Fixture) -> None:
    line = fixture.line
    response = client.post(
        "/units",
        json={
            "line_id": line,
            "box": {"x": 520, "y": 80, "w": 60, "h": 60},
            "reading": "え",
            "unicode": "U+3048",
            "client_id": "reviewer-2",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["target_id"] == f"{line}:m1" and body["revision"] == 1
    assert body["state"]["document_id"] == "doc-1" and body["state"]["page_id"] == fixture.page
    assert body["state"]["method"] == "manual" and body["state"]["review"] == "transcriber"
    assert active_units(client, line)[-1] == f"{line}:m1"
    assert client.post("/units", json={"line_id": "nope", "box": {"x": 1, "y": 1, "w": 2, "h": 2}}).status_code == 404


def test_unknown_field_and_a_timing_event(client: TestClient, fixture: Fixture) -> None:
    status, detail = review(
        client, target_type="unit", target_id=fixture.unit, field="colour", new="red", base_revision=0
    )
    assert status == 422 and "colour" in detail
    status, result = review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="timing",
        new={"opened_ms": 1200},
        base_revision=0,
    )
    assert status == 200 and result["revision"] == 1
    assert result["review"]["new"] == {"opened_ms": 1200}
    assert state(client, fixture.unit).box == Box(x=100, y=60, w=100, h=100)
    assert review(client, target_type="unit", target_id="nope", field="reading", new="x")[0] == 404


# -- apply and replay ----------------------------------------------------------------------------


def digests(directory: Path) -> dict[str, str]:
    """The sha256 of every table and of the review log."""
    files = sorted(directory.glob("*.parquet")) + [directory / "reviews.jsonl"]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.exists()}


def reviewed(client: TestClient, fixture: Fixture) -> None:
    """A move, a reading change, a split and a merge, so that apply has work to do."""
    review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="box",
        new={"x": 510, "y": 80, "w": 40, "h": 40},
        base_revision=0,
        client_id="reviewer-1",
    )
    split = review(
        client,
        target_type="unit",
        target_id=f"{fixture.line}:u1",
        field="segmentation",
        new={
            "split": [
                {"box": {"x": 220, "y": 60, "w": 50, "h": 100}, "reading": "い"},
                {"box": {"x": 270, "y": 60, "w": 50, "h": 100}, "reading": "い"},
            ]
        },
        base_revision=0,
    )[1]
    review(
        client,
        target_type="unit",
        target_id=split["created"][0],
        field="segmentation",
        new={"merge": split["created"]},
        base_revision=0,
    )


def test_apply_writes_the_tables_and_the_log(client: TestClient, fixture: Fixture) -> None:
    reviewed(client, fixture)
    counts = apply(fixture.directory)
    assert counts["lines"] == 6 and counts["units"] == 21 and counts["reviews"] == 3
    lines = tables.read(fixture.directory / "lines.parquet", Line)
    units = {unit.id: unit for unit in tables.read(fixture.directory / "units.parquet", Unit)}
    assert len(lines) == 6
    assert units[fixture.unit].box == Box(x=510, y=80, w=40, h=40)
    assert units[f"{fixture.line}:u1"].active is False
    assert units[f"{fixture.line}:u1"].split_into == [f"{fixture.line}:m1", f"{fixture.line}:m2"]
    assert units[f"{fixture.line}:m3"].merged_into is None
    assert units[f"{fixture.line}:m1"].merged_into == f"{fixture.line}:m3"
    log = (fixture.directory / "reviews.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in log] == ["rv00000001", "rv00000002", "rv00000003"]
    assert json.loads(log[1])["field"] == "segmentation"


def test_replay_equals_apply(client: TestClient, fixture: Fixture) -> None:
    reviewed(client, fixture)
    applied = apply(fixture.directory)
    before = digests(fixture.directory)
    rebuilt = replay(fixture.directory)
    assert rebuilt["repaired"] == 0 and rebuilt["adopted"] == 0
    assert rebuilt["events"] == applied["reviews"]
    assert rebuilt["lines"] == applied["lines"] and rebuilt["units"] == applied["units"]
    assert apply(fixture.directory) == applied
    assert digests(fixture.directory) == before
    assert client.app.state.store.unit(fixture.unit).box == Box(x=510, y=80, w=40, h=40)


def test_replay_repairs_a_state_a_crash_left_behind(client: TestClient, fixture: Fixture) -> None:
    review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="reading",
        new="き",
        base_revision=0,
        client_id="reviewer-1",
    )
    apply(fixture.directory)
    database = fixture.directory / "review.sqlite"
    conn = sqlite3.connect(database)
    stale = json.loads(conn.execute("SELECT data FROM units WHERE id = ?", (fixture.unit,)).fetchone()[0])
    stale["reading"] = "あ"
    conn.execute("UPDATE units SET data = ? WHERE id = ?", (json.dumps(stale, ensure_ascii=False), fixture.unit))
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'state_seq'")
    conn.commit()
    conn.close()
    assert client.app.state.store.unit(fixture.unit).reading == "あ"
    rebuilt = replay(fixture.directory)
    assert rebuilt["repaired"] == 1
    assert client.app.state.store.unit(fixture.unit).reading == "き"
    assert client.app.state.store.revision(fixture.unit) == 1


def test_replay_adds_an_event_the_state_never_saw(client: TestClient, fixture: Fixture) -> None:
    review(
        client,
        target_type="unit",
        target_id=fixture.unit,
        field="reading",
        new="き",
        base_revision=0,
        client_id="reviewer-1",
    )
    database = fixture.directory / "review.sqlite"
    conn = sqlite3.connect(database)
    conn.execute(
        "INSERT INTO events (id, target_type, target_id, field, old, new, role, actor, at, client_id) "
        "VALUES ('rv00000002', 'unit', ?, 'reading', ?, ?, 'reviewer', 'reviewer-1', ?, '')",
        (
            fixture.unit,
            json.dumps("き", ensure_ascii=False),
            json.dumps("く", ensure_ascii=False),
            "2026-09-11T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    rebuilt = replay(fixture.directory)
    assert rebuilt["repaired"] == 1 and rebuilt["events"] == 2
    assert client.app.state.store.unit(fixture.unit).reading == "く"
    assert client.app.state.store.revision(fixture.unit) == 2


def test_replay_takes_events_from_the_log_when_the_store_is_gone(
    client: TestClient, fixture: Fixture
) -> None:
    reviewed(client, fixture)
    apply(fixture.directory)
    before = digests(fixture.directory)
    (fixture.directory / "review.sqlite").unlink()
    rebuilt = replay(fixture.directory)
    assert rebuilt["adopted"] == 3 and rebuilt["repaired"] == 0 and rebuilt["events"] == 3
    assert client.app.state.store.unit(fixture.unit).box == Box(x=510, y=80, w=40, h=40)
    assert apply(fixture.directory) == {"lines": 6, "units": 21, "reviews": 3}
    assert digests(fixture.directory) == before


def test_rewritten_units_are_seen_by_a_reopened_store(fixture: Fixture) -> None:
    """A store opened on a dataset whose units were rewritten shows the new units, not its old copy.

    This is the case that made a served dataset report zero units after `atlas align` wrote the
    units the alignment had just produced.
    """
    original = Store(fixture.directory)
    assert original.units_of_line(fixture.line)
    before = len(original.units_of_line(fixture.line))

    path = fixture.directory / "units.parquet"
    records = tables.read(path, Unit)
    extra = records[0].model_copy(update={"id": f"{fixture.line}:deadbeef:99", "method": "detect-align"})
    tables.write(path, [*records, extra], Unit)

    reopened = Store(fixture.directory)
    ids = [unit.id for unit in reopened.units_of_line(fixture.line)]
    assert len(ids) == before + 1
    assert f"{fixture.line}:deadbeef:99" in ids


def test_apply_reaches_events_a_changed_table_would_hide(fixture: Fixture) -> None:
    """`apply` is the way out of the state the staleness guard describes, so it must still run.

    The guard tells a reader to run `apply`; if `apply` tripped it too, the events would be
    unreachable and the only exit would be deleting the database and losing them.
    """
    client = TestClient(create_app(fixture.directory))
    review(client, target_type="unit", target_id=fixture.unit, field="reading", new="き",
           base_revision=0, client_id="reviewer-1")
    # Rewrite the units table under the open store, as an alignment run would.
    path = fixture.directory / "units.parquet"
    records = tables.read(path, Unit)
    tables.write(path, [unit.model_copy(update={"active": True}) for unit in records], Unit)

    applied = apply(fixture.directory)
    assert applied["reviews"] >= 1
    log = fixture.directory / "reviews.jsonl"
    assert log.is_file() and log.read_text(encoding="utf-8").strip()


# -- the dashboard's own endpoints, which had no test when they were written ---------------------


@pytest.fixture
def ainu_dataset(tmp_path: Path) -> Path:
    """One document, one page with a unit and one page with only a transcription."""
    from kuzushiji_atlas import koji
    from kuzushiji_atlas.schema import (
        Box,
        Document,
        Line,
        Page,
        PageText,
        ReviewState,
        Unit,
        UnitKind,
    )

    directory = tmp_path / "ainu"
    directory.mkdir()
    tables.write(directory / "documents.parquet",
                 [Document(id="hk:d", title="蝦夷紀行", holder="龍谷大学図書館")], Document)
    pages = [
        Page(id="hk:d:0", document_id="hk:d", seq=0, image="file:a.jpg", width=100, height=100),
        # A page the alignment never reached: transcription, no lines, no boxes.
        Page(id="hk:d:1", document_id="hk:d", seq=1, image="file:b.jpg", width=100, height=100),
    ]
    line = Line(id="hk:d:0:L0", page_id="hk:d:0", seq=0, box=Box(x=1, y=2, w=3, h=4),
                text_raw="あ", text=koji.plain("あ"))
    unit = Unit(id="hk:d:0:L0:f:1", page_id="hk:d:0", document_id="hk:d", line_id=line.id, seq=1,
                box=Box(x=1, y=2, w=3, h=4), text_source="あ", kind=UnitKind.CHAR,
                method="detect-align", review=ReviewState.MACHINE)
    tables.write(directory / "pages.parquet", pages, Page)
    tables.write(directory / "lines.parquet", [line], Line)
    tables.write(directory / "units.parquet", [unit], Unit)
    tables.write(directory / "page_texts.parquet",
                 [PageText(page_id="hk:d:1", source="ainu-records", text_raw="【右丁】\nテシンを出\n")],
                 PageText)
    return directory


def test_the_project_endpoint_answers(ainu_dataset: Path):
    """`GET /project` returns counts derived from decisions, not from revision rows."""
    client = TestClient(create_app(ainu_dataset))
    answer = client.get("/project")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["imported"] == {"documents": 1, "pages": 2, "units": 1}
    assert body["counts"]["machine"] == 1 and body["counts"]["checked"] == 0
    assert body["quality"]["state"] == "unmeasured", "an unscored page has no measured precision"
    assert body["documents"][0]["counts"]["machine"] == 1
    assert body["documents"][0]["pages"] == 2


def test_the_project_endpoint_reports_an_unreadable_source(ainu_dataset: Path, tmp_path: Path):
    """A source path that is not a checkout is reported, not fatal: the atlas works without it."""
    client = TestClient(create_app(ainu_dataset))
    missing = tmp_path / "not-a-checkout"
    missing.mkdir()
    body = client.get("/project", params={"source": str(missing)}).json()
    assert body["source"] == {"readable": False, "path": "not-a-checkout"}


def test_the_page_listing_reaches_a_page_with_no_boxes(ainu_dataset: Path):
    """The page the alignment never reached is browsable, which no other route allowed."""
    client = TestClient(create_app(ainu_dataset))
    answer = client.get("/pages")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["total"] == 2
    empty = next(item for item in body["items"] if item["id"] == "hk:d:1")
    assert empty["lines"] == 0 and empty["counts"]["total"] == 0
    assert empty["transcribed"] is True, "its transcription is what makes it reviewable"
    assert "image_url" in empty

    only_pending = client.get("/pages", params={"pending": True}).json()
    assert [item["id"] for item in only_pending["items"]] == ["hk:d:0"], (
        "the pending filter keeps the page with machine output to check, and drops the empty one"
    )


def test_corrections_round_trip_through_the_api(ainu_dataset: Path):
    """Post a correction, read it back, retract it, and see the effective text follow."""
    client = TestClient(create_app(ainu_dataset))
    payload = {
        "id": "ezo-kiko-ryukoku-1-teshin", "target_id": "hk:d:1", "line": 1,
        "original": "テシン", "corrected": "テレン", "note": "原画像を確認。",
    }
    created = client.post("/corrections", json=payload)
    assert created.status_code == 201, created.text

    read = client.get("/pages/hk:d:1/corrections").json()
    assert read["total"] == 1 and read["items"][0]["status"] == "proposed"
    assert "テレン" in read["text"] and "テシン" not in read["text"]
    assert read["base"].startswith("【右丁】"), "the published text is still what it was"
    assert any(line.startswith("+") for line in read["items"][0]["diff"])

    retracted = client.post("/corrections/ezo-kiko-ryukoku-1-teshin/retract",
                            json={"page_id": "hk:d:1", "reason": "読み直した"})
    assert retracted.status_code == 200, retracted.text
    after = client.get("/pages/hk:d:1/corrections").json()
    assert after["total"] == 0 and "テシン" in after["text"]


def test_a_correction_that_cannot_be_placed_is_refused_with_its_reason(ainu_dataset: Path):
    """The API refuses what the source's build would refuse, and says why."""
    client = TestClient(create_app(ainu_dataset))
    answer = client.post("/corrections", json={
        "id": "bad-line", "target_id": "hk:d:1", "line": 9,
        "original": "テシン", "corrected": "テレン", "note": "n",
    })
    assert answer.status_code == 400
    assert "does not exist" in answer.json()["detail"]
