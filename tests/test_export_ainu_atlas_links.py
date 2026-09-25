"""The links from ainu-records' character pages to the atlas units that show the same ink."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.schema import Page

SCRIPT = Path(__file__).parent.parent / "scripts" / "export_ainu_atlas_links.py"
ENTRY = "3daea514503efa7c8ec5ccc61c9be9d8"


def load():
    spec = importlib.util.spec_from_file_location("export_ainu_atlas_links", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path, *, atlas_width: int = 1000) -> tuple[Path, Path, Path]:
    records = tmp_path / "ainu-records"
    (records / "data/characters/moshiogusa--ninjal-1").mkdir(parents=True)
    (records / "data/sources.yaml").write_text(
        "sources:\n  - slug: moshiogusa\n    witnesses:\n      - slug: ninjal\n        parts:\n"
        f"          - entry: {ENTRY}\n          - entry: 62c6743982041882d0aefd6582ac6a84\n", encoding="utf-8")
    samples = {"key": "moshiogusa/ninjal-1", "pages": [{"n": 1, "width": 1000, "height": 800}],
               "samples": [{"id": "1-l1-0", "page": 1, "box": [100, 100, 40, 50], "proposed": "シ"},
                           {"id": "1-l1-1", "page": 1, "box": [100, 160, 40, 50], "proposed": "ト"},
                           {"id": "1-l1-2", "page": 1, "box": [300, 300, 40, 50], "proposed": "コ"},
                           {"id": "1-l1-3", "page": 1, "box": [500, 100, 40, 50], "proposed": "ト"}]}
    (records / "data/characters/moshiogusa--ninjal-1/samples.json").write_text(json.dumps(samples), encoding="utf-8")
    catalogue = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.execute("CREATE TABLE units (id TEXT, origin TEXT, data TEXT)")
        for uid, box, label in [(f"hk:{ENTRY}:0:L0:r:1", {"x": 101, "y": 101, "w": 40, "h": 50}, "シ"),  # the same ink
                                (f"hk:{ENTRY}:0:L0:r:2", {"x": 100, "y": 190, "w": 40, "h": 50}, "ト"),  # weak overlap
                                (f"hk:{ENTRY}:1:L0:r:1", {"x": 300, "y": 300, "w": 40, "h": 50}, "コ"),  # another page
                                (f"hk:{ENTRY}:0:L0:r:3", {"x": 500, "y": 100, "w": 40, "h": 50}, "シ")]:  # the neighbour's name
            page = ":".join(uid.split(":")[:3])
            db.execute("INSERT INTO units VALUES (?, 'local', ?)",
                       (uid, json.dumps({"id": uid, "page_id": page, "box": box, "label": label})))
    pages = tmp_path / "pages.parquet"
    tables.write(pages, [Page(id=f"hk:{ENTRY}:0", document_id="d", seq=0, image="i", width=atlas_width, height=800),
                         Page(id=f"hk:{ENTRY}:1", document_id="d", seq=1, image="i", width=1000, height=800)], Page)
    return catalogue, pages, records


def test_an_occurrence_links_only_to_the_same_ink_on_the_same_page(tmp_path):
    module = load()
    catalogue, pages, records = fixture(tmp_path)
    result = module.links(catalogue, pages, records, 0.5)["moshiogusa/ninjal-1"]
    assert result["entry"] == ENTRY
    assert result["links"] == {"1-l1-0": f"hk:{ENTRY}:0:L0:r:1"}, "weak overlaps and other pages stay unlinked"


def test_a_page_whose_image_size_differs_is_not_linked(tmp_path):
    module = load()
    catalogue, pages, records = fixture(tmp_path, atlas_width=1200)
    assert module.links(catalogue, pages, records, 0.5)["moshiogusa/ninjal-1"]["links"] == {}


def test_parts_of_a_witness_are_numbered_as_ainu_records_names_them(tmp_path):
    module = load()
    _, _, records = fixture(tmp_path)
    assert module.entries(records) == {"moshiogusa/ninjal-1": ENTRY,
                                       "moshiogusa/ninjal-2": "62c6743982041882d0aefd6582ac6a84"}


def test_a_pair_the_atlas_labels_as_another_character_is_reported_not_linked(tmp_path):
    module = load()
    catalogue, pages, records = fixture(tmp_path)
    result = module.links(catalogue, pages, records, 0.5)["moshiogusa/ninjal-1"]
    assert "1-l1-3" not in result["links"]
    assert result["disagree"] == [{"sample": "1-l1-3", "unit": f"hk:{ENTRY}:0:L0:r:3", "records": "ト",
                                   "atlas": "シ", "box": [500, 100, 40, 50], "iou": 1.0}]
