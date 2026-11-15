"""The export reads a frozen copy of the dataset, which holds everything the crop records are built from."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from glyph_atlas import ainu_geometry, tables
from glyph_atlas.review.store import ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "export_cloudflare.py"
sys.path.insert(0, str(SCRIPTS))


def load():
    spec = importlib.util.spec_from_file_location("export_cloudflare", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_frozen_copy_carries_the_crop_geometry_flags(tmp_path):
    dataset, frozen = tmp_path / "dataset", tmp_path / "frozen"
    dataset.mkdir(); frozen.mkdir()
    (dataset / "units.parquet").write_bytes(b"units")
    mark = {"flags": ["sparse-ink"], "box": {"x": 1, "y": 2, "w": 3, "h": 4}}
    (dataset / ainu_geometry.FILE).write_text(json.dumps({"method": ainu_geometry.METHOD, "units": {"ar:a:1": mark}}))
    load().freeze(dataset, frozen)
    assert (frozen / "units.parquet").read_bytes() == b"units"
    assert ainu_geometry.load(frozen) == {"ar:a:1": mark}


def book(root, name):
    """A dataset of one book of two pages, each with a line of two units."""
    root.mkdir()
    pages, lines, units = [], [], []
    for p in range(2):
        page = Page(id=f"{name}:p{p}", document_id=name, seq=p, canvas="c", image="i", width=100, height=100)
        line = Line(id=f"{page.id}:l", page_id=page.id, seq=0, text="仮名", text_raw="仮名")
        pages.append(page); lines.append(line)
        units += [Unit(id=f"{page.id}:u{n}", page_id=page.id, document_id=name, line_id=line.id, seq=n,
                       text_source="仮", box=Box(x=1 + 20 * n, y=1, w=10, h=10)) for n in range(2)]
    for table, rows, model in (("documents", [Document(id=name, title=name)], Document), ("pages", pages, Page),
                               ("lines", lines, Line), ("units", units, Unit)):
        tables.write(root / f"{table}.parquet", rows, model)
    return root


def reviewed_store(tmp_path):
    """A store of its own book and two imported ones, with reviews of units, a page and a document."""
    store = Store(book(tmp_path / "dataset", "own"))
    for name in ("a", "b"):
        store.import_dataset(book(tmp_path / name, name))
    for n, (kind, target, field, new) in enumerate((("unit", "a:p0:u0", "text_source", "假"), ("unit", "a:p1:u1", "text_source", "假"),
                                                    ("unit", "b:p0:u0", "text_source", "假"), ("unit", "own:p0:u0", "text_source", "假"),
                                                    ("page", "a:p0", "note", "x"), ("document", "a", "note", "x"))):
        store.record(ReviewRequest(target_type=kind, target_id=target, field=field, new=new, base_revision=0,
                                   client_id="t", idempotency_key=f"k{n}"))
    return store


def test_a_copy_of_chosen_pages_opens_as_a_store_of_those_pages(tmp_path):
    live = reviewed_store(tmp_path)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    load().freeze(tmp_path / "dataset", frozen, {"a:p0", "own:p0"})
    copy = Store(frozen)
    chosen = {unit.id: (unit, revision) for unit, revision in live.unit_snapshot() if unit.page_id in ("a:p0", "own:p0")}
    assert {unit.id: (unit, revision) for unit, revision in copy.unit_snapshot()} == chosen
    assert chosen["a:p0:u0"][0].text_source == "假" and chosen["a:p0:u0"][1] == 1
    with copy._connection() as conn:
        targets = sorted(r[0] for r in conn.execute("SELECT target_id FROM events"))
    # The reviews of the chosen units, of their pages and of their books come along; no other page's do.
    assert targets == ["a", "a:p0", "a:p0:u0", "own:p0:u0"]
    # Every imported book is known, so what a book shares with the others reads as it does in the whole store.
    assert {document.id for document in copy.documents()} == {"own", "a", "b"}
    assert not (frozen / "review.sqlite.part").exists()


def test_a_page_the_store_lacks_stops_the_copy(tmp_path):
    reviewed_store(tmp_path)
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    with pytest.raises(SystemExit, match="1 of the 2 pages are not in"):
        load().freeze(tmp_path / "dataset", frozen, {"a:p0", "a:p9"})
    assert not (frozen / "review.sqlite").exists()


def test_pages_cannot_be_taken_from_a_dataset_without_a_store(tmp_path):
    dataset, frozen = book(tmp_path / "dataset", "own"), tmp_path / "frozen"
    frozen.mkdir()
    with pytest.raises(SystemExit, match="no review store"):
        load().freeze(dataset, frozen, {"own:p0"})
