from pathlib import Path

import numpy as np
import pytest

from glyph_atlas import tables
from glyph_atlas.review import shift_repair
from glyph_atlas.review import withheld_release as wr
from glyph_atlas.review.shift_repair import FLOOR, OFFSETS
from glyph_atlas.schema import Box, Document, Line, Page, Unit


def shows(offset: int, p: float = 0.95) -> np.ndarray:
    row = np.full(len(OFFSETS), FLOOR)
    row[OFFSETS.index(offset)] = p
    return row


DOUBT = "the ink at the corrected position scores 9.18 nats worse than another crop of the line; the pass does not " \
        "move a character onto ink that looks like a different one"


def note(reason: str = DOUBT, **extra) -> dict:
    return {"status": "uncertain", "machine": True, "verified": False, "reliable": False, "withheld": True,
            "quiz": False, "reason": reason, "layer": "deterministic", **extra}


def test_a_crop_is_released_only_when_it_shows_its_own_label_best():
    assert wr.decide(shows(0).tolist())
    assert not wr.decide(shows(0, 0.85).tolist())
    beside = shows(0, 0.92)
    beside[OFFSETS.index(1)] = 0.95
    assert not wr.decide(beside.tolist())
    # A label the classifier has no class for says nothing either way.
    unknown = shows(0)
    unknown[OFFSETS.index(-1)] = float("nan")
    assert wr.decide(unknown.tolist())
    assert not wr.decide([float("nan")] * len(OFFSETS))


def test_only_the_repairs_own_doubt_is_releasable():
    assert wr.releasable(note())
    assert not wr.releasable(note("saved crop issue awaiting extraction repair"))
    assert not wr.releasable(note("a reviewer reported a merged crop on the line; the line is withheld as one sequence"))
    assert not wr.releasable({**note(), "status": "contested"})
    assert not wr.releasable({**note(), "quiz": True})


def test_a_released_note_is_put_back_exactly():
    before = note(hypothesis="reading-order")
    released = wr.release_note(before, 0.97)
    assert released["quiz"] is True and released["withheld"] is False and released["status"] == "released"
    assert released["hypothesis"] == "reading-order"
    assert released["before"]["reason"] == DOUBT
    assert wr.restored_note(released) == before


def test_a_note_with_its_own_before_is_put_back_exactly():
    before = {**note(), "before": {"status": "contested"}}
    assert wr.restored_note(wr.release_note(before, 0.97)) == before


def test_a_threshold_outside_zero_to_one_is_refused(tmp_path):
    with pytest.raises(ValueError):
        wr.run(tmp_path, checkpoint=tmp_path / "best.pt", min_p=-1)


PAGE, LINE = "hk:d:1", "hk:d:1:L1"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="資料")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0, image="x", width=300, height=400,
                                               sha256="0" * 64)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0, text_raw="レタカナ", text="レタカナ",
                                               box=Box(x=0, y=0, w=300, h=400))], Line)
    notes = [note(), note(), note("saved crop issue awaiting extraction repair"), note()]
    codes = (("レ", "U+30EC"), ("タ", "U+30BF"), ("カ", "U+30AB"), ("ナ", "U+30CA"))
    tables.write(root / "units.parquet", [
        Unit(id=f"{LINE}:u{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=i, unicode=code, text_source=char,
             box=Box(x=20, y=10 + 50 * i, w=40, h=40 if i != 3 else 6), meta={"alignment_repair": notes[i]})
        for i, (char, code) in enumerate(codes)], Unit)
    return root


def test_run_releases_records_and_undoes(dataset: Path, monkeypatch):
    from glyph_atlas.review.atlas import repair_withheld
    from glyph_atlas.review.store import Store

    # u0 shows its own label, u1 the next one's; u2 has a crop issue and u3 is too small.
    reads = {f"{LINE}:u0": shows(0), f"{LINE}:u1": shows(1), f"{LINE}:u2": shows(0), f"{LINE}:u3": shows(0)}
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: reads)

    dry = wr.run(dataset, checkpoint=Path("unused"))
    assert dry["skipped"] == {"reported": 1, "small": 1}
    assert dry["counts"] == {"kept": 1, "proposed": 1}
    assert repair_withheld(Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0])

    result = wr.run(dataset, checkpoint=Path("unused"), apply=True)
    assert [item["unit_id"] for item in result["items"]] == [f"{LINE}:u0"]
    assert result["counts"] == {"kept": 1, "released": 1}
    units = {unit.id: unit for unit, _ in Store(dataset).unit_snapshot()}
    assert not repair_withheld(units[f"{LINE}:u0"])
    assert units[f"{LINE}:u0"].meta["alignment_repair"]["method"] == wr.METHOD
    assert all(repair_withheld(units[f"{LINE}:u{i}"]) for i in (1, 2, 3))
    # Released once, a rerun finds nothing left to release on that unit.
    assert wr.run(dataset, checkpoint=Path("unused"), apply=True)["counts"] == {"kept": 1}

    assert wr.undo(dataset)["counts"] == {"to restore": 1}
    assert wr.undo(dataset, apply=True)["counts"] == {"restored": 1}
    unit = Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0]
    assert repair_withheld(unit) and unit.meta["alignment_repair"] == note()
    assert wr.undo(dataset, apply=True)["counts"] == {}


def test_a_reviewed_unit_is_never_released(dataset: Path, monkeypatch):
    reads = {f"{LINE}:u{i}": shows(0) for i in range(4)}
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: reads)
    result = wr.run(dataset, checkpoint=Path("unused"), protect=[f"{LINE}:u0"])
    assert [item["unit_id"] for item in result["items"]] == [f"{LINE}:u1"]
    assert result["skipped"]["protected"] == 1


def test_a_release_and_undo_made_again_are_recorded_again(dataset: Path, monkeypatch):
    from glyph_atlas.review.atlas import repair_withheld
    from glyph_atlas.review.store import Store

    reads = {f"{LINE}:u{i}": shows(0) for i in range(4)}
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: reads)
    for _ in range(2):
        assert wr.run(dataset, checkpoint=Path("unused"), apply=True)["counts"] == {"released": 2}
        assert not repair_withheld(Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0])
        assert wr.undo(dataset, apply=True)["counts"] == {"restored": 2}
        assert repair_withheld(Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0])


def test_a_relabelled_unit_is_not_released_on_its_texts_read(dataset: Path, monkeypatch):
    from glyph_atlas.review.refine import _changes
    from glyph_atlas.review.store import Store

    store = Store(dataset)
    unit, revision = store.unit_snapshot(f"{LINE}:u0")[0]
    _changes(store, unit, {"unicode": "U+30BF"}, {"kind": "test"}, base_revision=revision)
    reads = {f"{LINE}:u{i}": shows(0) for i in range(4)}
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: reads)
    result = wr.run(dataset, checkpoint=Path("unused"))
    assert [item["unit_id"] for item in result["items"]] == [f"{LINE}:u1"]
    assert result["skipped"]["relabelled"] == 1


def test_a_run_that_reads_no_crop_fails(dataset: Path, monkeypatch):
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: {})
    with pytest.raises(RuntimeError, match="could be read"):
        wr.run(dataset, checkpoint=Path("unused"))


def test_the_method_is_named():
    assert wr.METHOD == "withheld-release-v1"
