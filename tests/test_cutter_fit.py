import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "models/benchmark"), str(ROOT / "models/cutter")]

import cuts
import fit


def box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def review(crop, verdict, issue=None, *, current=True, at="2026-11-01T00:00:00Z", evidence_as_text=False):
    evidence = {"kind": "character-review", "verdict": verdict, "issue": issue}
    return {"event": {"target_id": crop, "at": at, "evidence": json.dumps(evidence) if evidence_as_text else evidence},
            "current": current,
            "reviewed": {"character": {"box": box(10, 10, 40, 40), "line_id": "hl:D_1_000", "page_id": "hl:D:1"}}}


def test_a_review_judges_the_cut_when_it_matches_the_crop_or_reports_it():
    export = {"reviews": [
        review("ex:a:1", "match"),
        review("ex:a:2", "match", "reading", evidence_as_text=True),
        review("ex:a:3", "wrong", "merged"),
        review("ex:a:4", "wrong", "character"),
        review("ex:a:5", "match", current=False),
        review("ar:a:6", "match"),
        review("ex:a:7", "match"),
        review("ex:a:7", "wrong", "crop", at="2026-11-02T00:00:00Z"),
    ]}
    judged = cuts.judged(export)
    assert {crop: row["cut_right"] for crop, row in judged.items()} == {
        "ex:a:1": True, "ex:a:2": True, "ex:a:3": False, "ex:a:7": False}, \
        "the latest review counts; a wrong character, an outdated crop and another source's crop say nothing"


def test_a_reviewed_page_gives_each_token_its_reviewed_unit():
    line = {"id": "hl:D_1_000", "page_id": "hl:D:1", "seq": 0, "box": box(0, 0, 60, 300), "vertical": True,
            "text_raw": "いろは", "text": "いろは"}
    outside = {**line, "id": "hl:D_1_001", "seq": 1, "box": box(380, 0, 60, 300)}
    page = {"lines": [line, outside],
            "units": [{"id": "ex:a:1", "box": box(5, 5, 50, 60), "line": "hl:D_1_000", "token": 0}],
            "rejected": [{"id": "ex:a:2", "box": box(5, 140, 50, 150), "line": "hl:D_1_000", "token": 2}]}
    (found, units), = cuts.reviewed_lines_of(page, (400, 400))
    assert found.id == "hl:D_1_000", "a line that leaves the image is not cut"
    assert [unit and unit["id"] for unit in units] == ["ex:a:1", None, "ex:a:2"]
    assert not units[0].get("wrong") and units[2]["wrong"]


def test_a_page_that_knows_some_boxes_judges_only_the_candidates_on_them():
    record = {"partial": True, "page_truth": [box(100, 100, 50, 50)], "page_rejected": [box(100, 300, 50, 90)]}
    assert fit.framed(record, box(102, 98, 50, 52)) is True
    assert fit.framed(record, box(100, 100, 50, 110)) is False, "the character and the next one"
    assert fit.framed(record, box(100, 100, 50, 22)) is False, "the top of the character"
    assert fit.framed(record, box(100, 302, 50, 88)) is False, "the box a reviewer reported"
    assert fit.framed(record, box(100, 160, 50, 50)) is None, "its neighbour: the page does not say"
    whole = {"page_truth": [box(100, 100, 50, 50)]}
    assert fit.framed(whole, box(100, 160, 50, 50)) is False, "a page that knows every box does"


def test_reviewed_rows_weigh_a_share_of_the_boxed_pages_rows():
    assert fit.shares((4, 0), 0.5) == [1.0] * 4
    weights = fit.shares((4, 2), 0.5)
    assert weights[:4] == [1.0] * 4 and sum(weights[4:]) == pytest.approx(2.0)
