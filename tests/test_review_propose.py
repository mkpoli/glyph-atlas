"""Proposed interlinear marks as machine units: the store, the review log, the command and the page route.

The dataset is a photograph-only source, documents and pages with no lines or units, and the page
record is twice the size of the cached photo, so every proposed box is scaled to the record's pixels.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from typer.testing import CliRunner

from glyph_atlas import tables
from glyph_atlas.interlinear import Proposal
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import DETECT, DrawRequest, Store, apply, replay
from glyph_atlas.schema import Box, Document, Page, Unit

DOCUMENT = "khs:fixture"
PAGE = f"{DOCUMENT}:1"
CLIENT = "reviewer-fixture"
VERSION = "interlinear-test-1"
MARK = Proposal(x=100, y=40, w=12, h=14, kind="mark", score=0.6, features={"unit": 60.0})
CIRCLE = Proposal(x=200, y=90, w=10, h=10, kind="circle", score=0.8, features={"unit": 60.0})


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    image = tmp_path / "photo.png"
    Image.new("RGB", (400, 300), "white").save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / "images" / digest[:2]
    folder.mkdir(parents=True)
    shutil.copy(image, folder / f"{digest}.png")
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id=DOCUMENT, title="Photographs")], Document)
    tables.write(root / "pages.parquet", [
        Page(id=PAGE, document_id=DOCUMENT, seq=1, image="https://example.org/1.jpg", width=800, height=600,
             sha256=digest),
    ], Page)
    return root


def propose(store: Store, proposals=(MARK, CIRCLE), version: str = VERSION) -> dict:
    return store.propose_units(PAGE, list(proposals), version, image_size=(400, 300))


def test_proposals_become_machine_units_on_the_page_line(dataset: Path) -> None:
    store = Store(dataset)
    result = propose(store)
    assert result["skipped"] is False and result["retired"] == [] and len(result["created"]) == 2
    (line,) = store.lines_of_page(PAGE)
    assert line.page_scope and line.box == Box(x=0, y=0, w=800, h=600)
    units = {unit.id: unit for unit in store.units_of_page(PAGE)}
    mark, circle = (units[identifier] for identifier in result["created"])
    assert mark.id.startswith(f"{line.id}:") and mark.line_id == line.id
    assert mark.box == Box(x=200, y=80, w=24, h=28) and circle.box == Box(x=400, y=180, w=20, h=20)
    assert (mark.method, mark.review, mark.classification, mark.unicode) == (DETECT, "machine", "unassessed", None)
    assert (str(mark.kind), str(circle.kind)) == ("char", "punctuation")
    assert mark.meta == {"proposer": VERSION, "proposal": "mark", "unit": 60.0}
    assert mark.confidence.detection == 0.6 and mark.confidence.model == VERSION
    events = store.events()
    assert [event.field for event in events] == ["create"] * 3 and {event.role for event in events} == {"model"}


def test_a_second_run_of_the_same_version_writes_nothing(dataset: Path) -> None:
    store = Store(dataset)
    first = propose(store)
    retired = first["created"][0]
    store.record(_retire(store, retired))
    again = propose(store)
    assert again == {"page": PAGE, "created": [], "retired": [], "skipped": True}
    assert [unit.id for unit in store.units_of_page(PAGE)] == [first["created"][1]]


def test_a_proposal_over_a_standing_box_is_left_out_and_the_box_is_untouched(dataset: Path) -> None:
    store = Store(dataset)
    drawn = store.draw_unit(PAGE, DrawRequest(box=Box(x=198, y=78, w=26, h=30), client_id=CLIENT))
    before = store.unit(drawn["unit"]["target_id"])
    result = propose(store)
    assert len(result["created"]) == 1
    assert store.unit(before.id) == before
    assert len(store.lines_of_page(PAGE)) == 1


def test_a_new_version_retires_only_untouched_proposals_of_the_old_one(dataset: Path, tmp_path: Path) -> None:
    app = create_app(dataset, corpus=None)
    api, store = TestClient(app), app.state.store
    old = propose(store, version="interlinear-test-0")["created"]
    named, untouched = old
    page = api.get(f"/atlas/pages/{PAGE}").json()
    item = next(unit for unit in page["units"] if unit["id"] == named)
    answer = api.post(f"/layers/units/{named}", json={
        "id": str(uuid4()), "client_id": CLIENT, "revision": item["revision"],
        "image_sha256": page["image_sha256"], "character": "U+F67F", "verdict": "wrong", "issue": "character"})
    assert answer.status_code == 200, answer.text

    result = propose(store, proposals=[Proposal(x=300, y=200, w=10, h=12, kind="mark", score=0.5)])
    assert result["retired"] == [untouched] and len(result["created"]) == 1
    assert store.unit(named).active and store.unit(named).review == "reviewed"
    assert not store.unit(untouched).active


def test_the_review_log_reproduces_the_proposals(dataset: Path, tmp_path: Path) -> None:
    store = Store(dataset)
    created = propose(store)["created"]
    counts = apply(dataset)
    assert counts["units"] == 2 and counts["lines"] == 1
    written = {unit.id: unit for unit in tables.read(dataset / "units.parquet", Unit)}
    assert set(written) == set(created) and all(unit.method == DETECT for unit in written.values())

    # The log alone, over the tables as they were before, brings the same units back.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    for name in ("documents.parquet", "pages.parquet", "reviews.jsonl"):
        shutil.copy(dataset / name, fresh / name)
    counts = replay(fresh)
    assert counts["adopted"] == 3 and counts["units"] == 2
    assert {unit.id: unit for unit in Store(fresh).units_of_page(PAGE)} == written


def test_the_page_route_shows_a_proposal_until_it_is_named(dataset: Path) -> None:
    app = create_app(dataset, corpus=None)
    api = TestClient(app)
    mark, circle = propose(app.state.store)["created"]
    page = api.get(f"/atlas/pages/{PAGE}").json()
    items = {unit["id"]: unit for unit in page["units"]}
    assert items[mark]["proposed"] and items[mark]["detected"] and not items[mark]["manual"]
    answer = api.post(f"/layers/units/{mark}", json={
        "id": str(uuid4()), "client_id": CLIENT, "revision": items[mark]["revision"],
        "image_sha256": page["image_sha256"], "character": "U+F67F", "verdict": "wrong", "issue": "character"})
    assert answer.status_code == 200, answer.text
    retired = api.post("/reviews", json={"target_type": "unit", "target_id": circle, "field": "active",
                                         "new": False, "base_revision": items[circle]["revision"],
                                         "client_id": CLIENT})
    assert retired.status_code == 200
    (named,) = api.get(f"/atlas/pages/{PAGE}").json()["units"]
    assert named["id"] == mark and named["code_point"] == "U+F67F"
    assert named["detected"] and not named["proposed"]


def test_the_command_proposes_marks_once_per_version(dataset: Path, tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    from test_interlinear import page

    from glyph_atlas.cli import app

    photo = tmp_path / "text.png"
    page().save(photo)
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / "images" / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(photo, folder / f"{digest}.png")
    text = Page(id=f"{DOCUMENT}:2", document_id=DOCUMENT, seq=2, image="https://example.org/2.jpg",
                width=1700, height=1400, sha256=digest)
    tables.write(dataset / "pages.parquet", [*tables.read(dataset / "pages.parquet", Page), text], Page)

    runner = CliRunner()
    first = runner.invoke(app, ["review", "propose-marks", str(dataset), "--document", DOCUMENT])
    assert first.exit_code == 0, first.output
    lines = dict(line.split(None, 1) for line in first.output.splitlines())
    assert lines[PAGE].split()[:4] == ["0", "marks", "0", "circles"]
    assert lines[text.id].split()[:6] == ["3", "marks", "1", "circles", "4", "recorded"]
    assert len(Store(dataset).units_of_page(text.id)) == 4

    again = runner.invoke(app, ["review", "propose-marks", str(dataset), "--page", text.id])
    assert again.exit_code == 0 and "already proposed" in again.output
    assert len(Store(dataset).units_of_page(text.id)) == 4
    missing = runner.invoke(app, ["review", "propose-marks", str(dataset), "--page", "nope"])
    assert missing.exit_code != 0


def _retire(store: Store, unit_id: str):
    from glyph_atlas.review.store import ReviewRequest

    return ReviewRequest(target_type="unit", target_id=unit_id, field="active", new=False,
                         base_revision=store.revisions([unit_id])[unit_id], client_id=CLIENT)


def _request(store: Store, unit_id: str, field: str, new):
    from glyph_atlas.review.store import ReviewRequest

    return ReviewRequest(target_type="unit", target_id=unit_id, field=field, new=new,
                         base_revision=store.revisions([unit_id])[unit_id], client_id=CLIENT)


def test_a_kept_proposal_survives_a_new_version(dataset: Path) -> None:
    store = Store(dataset)
    mark, circle = propose(store)["created"]
    store.record(_request(store, mark, "review", "reviewed"))
    result = propose(store, version="interlinear-test-2")
    assert result["retired"] == [circle], "only the proposal nobody touched is retired"
    kept = {unit.id: unit for unit in store.units_of_page(PAGE)}[mark]
    assert kept.active and kept.review == "reviewed"


def test_a_proposal_a_reviewer_removed_does_not_come_back_with_a_new_version(dataset: Path) -> None:
    store = Store(dataset)
    mark, _ = propose(store)["created"]
    store.record(_request(store, mark, "active", False))
    result = propose(store, version="interlinear-test-2")
    new = [unit for unit in store.units_of_page(PAGE) if unit.id in result["created"]]
    assert all(unit.meta["proposal"] != "mark" for unit in new), "the removed mark is not proposed again"


def test_the_command_says_which_extra_it_needs(dataset: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from glyph_atlas.cli import app

    real = builtins.__import__

    def without_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("No module named 'cv2'", name="cv2")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_cv2)
    result = CliRunner().invoke(app, ["review", "propose-marks", str(dataset)])
    assert result.exit_code == 2 and "uv sync --extra marks" in result.output
