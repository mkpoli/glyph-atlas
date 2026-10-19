"""The characters a page's columns imply where its boxes leave a gap: the cells, the gates and the command.

The page is drawn: four columns of ten square characters on paper, the detector's boxes standing on
some of them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from glyph_atlas import column_proposals, tables
from glyph_atlas.review.store import Store
from glyph_atlas.schema import Box, Document, Page

COLUMNS = (100, 200, 300, 400)
ROWS = 10
TOP = 100
PITCH = 60
SIDE = 40


def cell(column: int, row: int) -> Box:
    return Box(x=COLUMNS[column], y=TOP + row * PITCH, w=SIDE, h=SIDE)


def page(cells=None) -> Image.Image:
    """A page of paper with a character drawn in each of `cells`, every cell of the grid by default."""
    image = Image.new("RGB", (560, 900), (225, 215, 190))
    draw = ImageDraw.Draw(image)
    for box in cells if cells is not None else [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS)]:
        # A character of strokes: two bars and a post.
        draw.rectangle([box.x, box.y + 4, box.x + box.w, box.y + 10], fill=(40, 40, 40))
        draw.rectangle([box.x, box.y + box.h - 10, box.x + box.w, box.y + box.h - 4], fill=(40, 40, 40))
        draw.rectangle([box.x + box.w // 2 - 3, box.y, box.x + box.w // 2 + 3, box.y + box.h], fill=(40, 40, 40))
    return image


def found(proposals) -> set[tuple[int, int]]:
    """The grid cells the proposals stand on."""
    return {(COLUMNS.index(min(COLUMNS, key=lambda x: abs(x - p.x))), round((p.y - TOP) / PITCH)) for p in proposals}


def test_the_cells_a_column_holds_between_and_beyond_its_boxes_are_proposed() -> None:
    missed = {(0, 4), (0, 5), (1, 0), (2, 9), (3, 3), (3, 4), (3, 5), (3, 6)}
    boxes = [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS) if (c, r) not in missed]
    proposals = column_proposals.propose(page(), boxes)
    assert found(proposals) == missed
    assert all(abs(p.w - SIDE) <= 2 and abs(p.h - SIDE) <= 2 for p in proposals)


def test_an_empty_cell_and_a_standing_box_get_no_proposal() -> None:
    blank = {(1, 3), (2, 6)}
    drawn = [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS) if (c, r) not in blank]
    boxes = [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS)]
    assert column_proposals.propose(page(drawn), [box for box in boxes if (box.x, box.y) not in
                                                 {(cell(*b).x, cell(*b).y) for b in blank}]) == []


def test_a_page_whose_boxes_form_no_columns_gets_nothing() -> None:
    scattered = [Box(x=60 + (n * 97) % 420, y=80 + (n * 131) % 700, w=SIDE, h=SIDE) for n in range(30)]
    assert column_proposals.propose(page(), scattered) == []
    two = [cell(c, r) for c in (0, 1) for r in range(ROWS) if r != 4]
    assert column_proposals.propose(page(), two) == []


def test_boxes_below_the_text_do_not_stretch_a_column() -> None:
    boxes = [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS - 2)]
    chart = [Box(x=COLUMNS[0], y=820, w=SIDE, h=SIDE), Box(x=COLUMNS[0] + 2, y=822, w=SIDE, h=SIDE)]
    drawn = [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS - 2)] + chart
    assert column_proposals.propose(page(drawn), boxes + chart) == []


def grid_without(*missed: tuple[int, int]) -> list[Box]:
    return [cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS) if (c, r) not in missed]


def test_a_stroke_reaching_out_of_the_column_stays_in_the_box() -> None:
    image = page()
    box = cell(1, 5)
    ImageDraw.Draw(image).rectangle([box.x - 10, box.y + 18, box.x + box.w + 10, box.y + 24], fill=(40, 40, 40))
    (proposal,) = column_proposals.propose(image, grid_without((1, 5)))
    assert proposal.x <= box.x - 9 and proposal.x + proposal.w >= box.x + box.w + 9


def test_touching_characters_are_cut_apart() -> None:
    image = page()
    upper, lower = cell(2, 4), cell(2, 5)
    ImageDraw.Draw(image).rectangle([upper.x + 17, upper.y, upper.x + 23, lower.y + 10], fill=(40, 40, 40))
    assert found(column_proposals.propose(image, grid_without((2, 4), (2, 5)))) == {(2, 4), (2, 5)}


def test_a_character_its_box_holds_in_part_gets_no_second_box() -> None:
    boxes = grid_without()
    boxes[boxes.index(cell(0, 4))] = Box(x=cell(0, 4).x, y=cell(0, 4).y + SIDE // 2, w=SIDE, h=SIDE // 2)
    assert column_proposals.propose(page(), boxes) == []


def test_flat_strata_and_a_crack_hold_no_character() -> None:
    blank = {(1, 3), (3, 6)}
    image = page([cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS) if (c, r) not in blank])
    draw = ImageDraw.Draw(image)
    strata = cell(1, 3)
    for y in range(strata.y, strata.y + strata.h, 8):
        draw.rectangle([strata.x - 4, y, strata.x + strata.w + 4, y + 3], fill=(40, 40, 40))
    crack = cell(3, 6)
    draw.line([crack.x + 10, crack.y, crack.x + 26, crack.y + crack.h], fill=(250, 250, 245), width=3)
    assert column_proposals.propose(image, grid_without(*blank)) == []


def test_the_version_names_the_family() -> None:
    assert column_proposals.proposer().startswith("columns-")


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    image = tmp_path / "photo.png"
    page().save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / f"{digest}.png").write_bytes(image.read_bytes())
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="eap140:fixture", title="Sutra")], Document)
    # The record is twice the size of the photo, so boxes are scaled both ways.
    tables.write(root / "pages.parquet", [
        Page(id="eap140:fixture:1", document_id="eap140:fixture", seq=1, image="https://example.org/1.jpg",
             width=1120, height=1800, sha256=digest),
    ], Page)
    return root


def test_the_command_adds_the_missed_characters_beside_the_detectors(dataset: Path) -> None:
    from glyph_atlas.character_proposals import Proposal
    from glyph_atlas.cli import app

    store = Store(dataset)
    detected = [Proposal(x=b.x, y=b.y, w=b.w, h=b.h, score=0.05)
                for b in (cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS)) if (b.x, b.y) != (300, 280)]
    store.propose_units("eap140:fixture:1", detected, "characters-abc", image_size=(560, 900))
    runner = CliRunner()
    first = runner.invoke(app, ["review", "propose-columns", str(dataset), "--script", "tangut"])
    assert first.exit_code == 0, first.output
    added = [unit for unit in Store(dataset).units_of_page("eap140:fixture:1")
             if unit.meta["proposer"] == column_proposals.proposer()]
    assert len(added) == 1 and abs(added[0].box.x - 600) <= 4 and abs(added[0].box.y - 560) <= 4
    assert str(added[0].script) == "tangut"
    assert len(Store(dataset).units_of_page("eap140:fixture:1")) == len(COLUMNS) * ROWS
    again = runner.invoke(app, ["review", "propose-columns", str(dataset)])
    assert again.exit_code == 0 and "already proposed" in again.output
    for arguments, message in ((["--script", "klingon"], "no script"), (["--page", "nope"], "no page")):
        refused = runner.invoke(app, ["review", "propose-columns", str(dataset), *arguments])
        assert refused.exit_code == 2 and message in refused.output, refused.output


def test_degenerate_and_outlying_boxes_propose_nothing_and_raise_nothing() -> None:
    flat = [Box(x=x, y=y, w=40, h=0) for x in COLUMNS[:3] for y in range(100, 700, 60)]
    assert column_proposals.propose(page(), flat) == []
    stacked = [Box(x=x, y=y, w=SIDE, h=SIDE) for x in COLUMNS[:3] for y in (100, 100, 100, 100, 200, 260)]
    column_proposals.propose(page(), stacked)
    below = [Box(x=x, y=y, w=SIDE, h=SIDE) for x in COLUMNS[:3] for y in (700, 760, 820, 1000, 1060, 1120)]
    column_proposals.propose(Image.new("RGB", (560, 900), "white"), below)


def test_a_new_version_puts_back_the_boxes_the_old_one_proposed(dataset: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from glyph_atlas.character_proposals import Proposal
    from glyph_atlas.cli import app

    store = Store(dataset)
    detected = [Proposal(x=b.x, y=b.y, w=b.w, h=b.h, score=0.05)
                for b in (cell(c, r) for c in range(len(COLUMNS)) for r in range(ROWS)) if (b.x, b.y) != (300, 280)]
    store.propose_units("eap140:fixture:1", detected, "characters-abc", image_size=(560, 900))
    runner = CliRunner()
    assert runner.invoke(app, ["review", "propose-columns", str(dataset)]).exit_code == 0
    monkeypatch.setattr(column_proposals, "proposer", lambda: "columns-next")
    upgraded = runner.invoke(app, ["review", "propose-columns", str(dataset)])
    assert upgraded.exit_code == 0, upgraded.output
    units = Store(dataset).units_of_page("eap140:fixture:1")
    assert len(units) == len(COLUMNS) * ROWS
    assert [unit.meta["proposer"] for unit in units if unit.meta["proposer"].startswith("columns-")] == ["columns-next"]
