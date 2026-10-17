from pathlib import Path

import numpy as np
import pytest

from glyph_atlas import tables
from glyph_atlas.review import shift_repair
from glyph_atlas.review import source_duplicates as sd
from glyph_atlas.schema import Box, Document, Line, Page, Unit

PAGE = "hk:d:1"
HELD = {"status": "uncertain", "withheld": True, "quiz": False, "reliable": False, "machine": True}
RELEASED = {"status": "released", "withheld": False, "quiz": True, "reliable": True, "machine": True,
            "method": "withheld-release-v1"}


def unit(identity: str, char: str, box: Box, note: dict | None = None) -> Unit:
    return Unit(id=identity, document_id="d", page_id=PAGE, line_id=f"{identity[:3]}L", seq=0,
                unicode=f"U+{ord(char):04X}", text_source=char, box=box,
                meta={"alignment_repair": note} if note is not None else {})


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="資料")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0, image="x", width=300, height=400,
                                               sha256="0" * 64)], Page)
    tables.write(root / "lines.parquet", [Line(id=f"{p}L", page_id=PAGE, seq=i, text_raw="", text="",
                                               box=Box(x=0, y=0, w=300, h=400)) for i, p in enumerate(("ar:", "hk:"))],
                 Line)
    tables.write(root / "units.parquet", [
        # The same タ cut twice: the hk: copy has no repair note.
        unit("ar:a1", "タ", Box(x=10, y=10, w=40, h=40)),
        unit("hk:h1", "タ", Box(x=14, y=12, w=36, h=36)),
        # The same カ, the hk: copy released by the withheld release.
        unit("ar:a2", "カ", Box(x=10, y=60, w=40, h=40)),
        unit("hk:h2", "カ", Box(x=10, y=62, w=40, h=40), RELEASED),
        # One ink, two labels: no duplicate.
        unit("ar:a3", "レ", Box(x=10, y=110, w=40, h=40)),
        unit("hk:h3", "ル", Box(x=10, y=110, w=40, h=40)),
        # The ar: copy is withheld, so the hk: crop is the one shown.
        unit("ar:a4", "ナ", Box(x=10, y=160, w=40, h=40), HELD),
        unit("hk:h4", "ナ", Box(x=10, y=160, w=40, h=40)),
        # Same label, a box apart.
        unit("ar:a5", "ニ", Box(x=10, y=210, w=40, h=40)),
        unit("hk:h5", "ニ", Box(x=10, y=255, w=40, h=40)),
    ], Unit)
    return root


def test_overlap_is_a_share_of_the_smaller_box():
    big, small = Box(x=0, y=0, w=40, h=40), Box(x=10, y=10, w=10, h=10)
    assert sd.overlap(big, small) == 1.0
    assert sd.overlap(big, Box(x=20, y=0, w=40, h=40)) == 0.5
    assert sd.overlap(big, Box(x=40, y=0, w=10, h=10)) == 0.0


def reading(monkeypatch, **p):
    """The classifier reads every crop at 0.9 except those given."""
    def shown(store, blocks, checkpoint, **_):
        return {identity: np.array([p.get(identity.replace(":", "_"), 0.9)]) for identity in blocks}
    monkeypatch.setattr(shift_repair, "_shown", shown)


def test_the_crop_read_less_surely_is_withheld():
    assert sd.worse({"hk:h": 0.5, "ar:a": 0.9}, "hk:h", "ar:a") == "hk:h"
    assert sd.worse({"hk:h": 0.95, "ar:a": 0.9}, "hk:h", "ar:a") == "ar:a"
    assert sd.worse({"hk:h": 0.9, "ar:a": 0.9}, "hk:h", "ar:a") == "hk:h"
    assert sd.worse({"hk:h": 0.9}, "hk:h", "ar:a") == "ar:a"
    assert sd.worse({}, "hk:h", "ar:a") == "hk:h"


def test_run_withholds_the_worse_copy_and_undo_puts_back_its_note(dataset: Path, monkeypatch):
    from glyph_atlas.review.atlas import repair_withheld
    from glyph_atlas.review.store import Store

    # The ar: cut of カ takes in a neighbour, so the classifier reads it less surely.
    reading(monkeypatch, ar_a2=0.4)
    dry = sd.run(dataset, checkpoint=Path("unused"))
    assert [(i["unit_id"], i["duplicate_of"]) for i in dry["items"]] == [("ar:a2", "hk:h2"), ("hk:h1", "ar:a1")]
    assert dry["counts"] == {"proposed": 2}

    assert sd.run(dataset, checkpoint=Path("unused"), apply=True)["counts"] == {"withheld": 2}
    units = {u.id: u for u, _ in Store(dataset).unit_snapshot()}
    assert repair_withheld(units["hk:h1"]) and repair_withheld(units["ar:a2"])
    assert units["ar:a2"].meta["alignment_repair"]["duplicate_of"] == "hk:h2"
    assert not any(repair_withheld(units[i]) for i in ("ar:a1", "hk:h2", "hk:h3", "hk:h4", "hk:h5"))
    assert sd.run(dataset, checkpoint=Path("unused"), apply=True)["counts"] == {}

    assert sd.undo(dataset)["counts"] == {"to restore": 2}
    assert sd.undo(dataset, apply=True)["counts"] == {"restored": 2}
    units = {u.id: u for u, _ in Store(dataset).unit_snapshot()}
    assert "alignment_repair" not in units["hk:h1"].meta and "alignment_repair" not in units["ar:a2"].meta
    assert units["hk:h2"].meta["alignment_repair"] == RELEASED
    assert sd.undo(dataset, apply=True)["counts"] == {}


def test_a_released_copy_gets_its_note_back(dataset: Path, monkeypatch):
    from glyph_atlas.review.store import Store

    reading(monkeypatch, hk_h2=0.4)
    assert sd.run(dataset, checkpoint=Path("unused"), apply=True)["counts"] == {"withheld": 2}
    assert sd.undo(dataset, apply=True)["counts"] == {"restored": 2}
    assert Store(dataset).unit_snapshot("hk:h2")[0][0].meta["alignment_repair"] == RELEASED


def test_a_protected_pair_is_left(dataset: Path, monkeypatch):
    reading(monkeypatch)
    result = sd.run(dataset, checkpoint=Path("unused"), protect=["ar:a1"])
    assert [i["unit_id"] for i in result["items"]] == ["hk:h2"]
    assert result["skipped"] == {"protected": 1}


def test_a_run_that_reads_no_crop_fails(dataset: Path, monkeypatch):
    monkeypatch.setattr(shift_repair, "_shown", lambda store, blocks, checkpoint, **_: {})
    with pytest.raises(RuntimeError, match="could be read"):
        sd.run(dataset, checkpoint=Path("unused"))


def test_an_overlap_outside_zero_to_one_is_refused(dataset: Path):
    with pytest.raises(ValueError):
        sd.run(dataset, checkpoint=Path("unused"), min_overlap=0)
