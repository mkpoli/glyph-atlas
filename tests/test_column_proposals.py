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
