"""Tests of `atlas pilot export`: what a package holds and what it says about its page.

The page's pixel size is the part worth pinning. The Honkoku-Lines import records the IIIF URL of a
page and no width or height, and the review interface scales every line box by `page.width`, so a
package that shipped a zero drew every box on every pilot page at the wrong scale. The export fills
the size in from the image the package carries, and these tests hold it to that, including the case
where the upstream did state a size and the export must not overrule it.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from glyph_atlas import pilot, tables
from glyph_atlas.schema import Box, Document, Line, Page, Production, Unit, UnitKind

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "data" / "pilot" / "items.tsv"
SIZE = (137, 211)


def png(width: int = SIZE[0], height: int = SIZE[1]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 190, 180)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """A one-page dataset: a line with a box and two units, and no page size stated."""
    directory = tmp_path / "dataset"
    directory.mkdir()
    document = Document(id="d1", title="a book", production=Production.WOODBLOCK, source="test")
    page = Page(id="hl:d1:0", document_id="d1", seq=0, canvas="c", image=f"{tmp_path.name}/0.png",
                width=0, height=0)
    line = Line(id="hl:d1:0:L0", page_id="hl:d1:0", seq=0, box=Box(x=10, y=10, w=100, h=20),
                text_raw="あい", text="あい")
    units = [
        Unit(id="hl:d1:0:L0:f:0", page_id="hl:d1:0", document_id="d1", line_id=line.id, seq=0,
             box=Box(x=10, y=10, w=20, h=20), text_source="あ", kind=UnitKind.CHAR, method="detect-align"),
        Unit(id="hl:d1:0:L0:f:1", page_id="hl:d1:0", document_id="d1", line_id=line.id, seq=1,
             box=Box(x=40, y=10, w=20, h=20), text_source="い", kind=UnitKind.CHAR, method="detect-align"),
    ]
    tables.write(directory / "documents.parquet", [document], Document)
    tables.write(directory / "pages.parquet", [page], Page)
    tables.write(directory / "lines.parquet", [line], Line)
    tables.write(directory / "units.parquet", units, Unit)
    return directory


@pytest.fixture
def selection(tmp_path: Path) -> Path:
    """A pilot selection of one page, in the columns `data/pilot/items.tsv` uses."""
    path = tmp_path / "items.tsv"
    header = pilot.selection(SELECTION)[0].keys()
    row = {name: "" for name in header}
    row.update({"page_id": "hl:d1:0", "item_id": "d1", "group": "calibration", "reason": "test",
                "image_index": "0"})
    path.write_text(
        "# a comment line, which the reader skips\n"
        + "\t".join(header) + "\n" + "\t".join(row[name] for name in header) + "\n",
        encoding="utf-8",
    )
    return path


def test_a_package_carries_its_image_and_the_size_that_goes_with_it(
    tmp_path: Path, dataset: Path, selection: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page record's size comes from the image, and the box and text of the line come along."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    served = tmp_path / "cache" / "images" / "aa" / "aa.png"
    served.parent.mkdir(parents=True)
    served.write_bytes(png())

    def fetch(page: Page, target: Path) -> str:
        target.write_bytes(png())
        return hashlib.sha256(target.read_bytes()).hexdigest()

    monkeypatch.setattr(pilot, "_fetch_image", fetch)
    counts = pilot.export(tmp_path / "out", dataset, group="calibration", selection_path=selection)
    assert counts == {"pages": 1, "lines": 1, "units": 2, "images": 1}

    folder = tmp_path / "out" / "hl_d1_0"
    page = tables.read(folder / "pages.parquet", Page)[0]
    assert (page.width, page.height) == SIZE, "the size is read from the image the package holds"
    assert page.sha256 and (folder / "image.jpg").exists()

    line = tables.read(folder / "lines.parquet", Line)[0]
    assert line.box == Box(x=10, y=10, w=100, h=20)
    units = tables.read(folder / "units.parquet", Unit)
    assert [unit.text_source for unit in units] == ["あ", "い"]

    record = json.loads((folder / "page.json").read_text(encoding="utf-8"))
    assert record["group"] == "calibration" and record["item_id"] == "d1"
    assert record["page"]["width"] == SIZE[0], "the JSON beside the table carries the size too"


def test_a_stated_size_is_left_alone(
    tmp_path: Path, dataset: Path, selection: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dataset that knows its page size keeps it; only a zero is filled in."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    page = tables.read(dataset / "pages.parquet", Page)[0]
    page.width, page.height = 4000, 3000
    tables.write(dataset / "pages.parquet", [page], Page)
    monkeypatch.setattr(pilot, "_fetch_image", lambda page, target: (target.write_bytes(png()), "x")[1])

    pilot.export(tmp_path / "out", dataset, group="calibration", selection_path=selection)
    written = tables.read(tmp_path / "out" / "hl_d1_0" / "pages.parquet", Page)[0]
    assert (written.width, written.height) == (4000, 3000)


def test_the_export_without_images_states_no_size(
    tmp_path: Path, dataset: Path, selection: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package with no image has nothing to take a size from and says so rather than guessing."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    counts = pilot.export(tmp_path / "out", dataset, group="calibration", images=False,
                          selection_path=selection)
    assert counts["images"] == 0
    written = tables.read(tmp_path / "out" / "hl_d1_0" / "pages.parquet", Page)[0]
    assert (written.width, written.height) == (0, 0)
    assert not (tmp_path / "out" / "hl_d1_0" / "image.jpg").exists()
