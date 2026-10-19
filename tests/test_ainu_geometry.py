"""ainu-records' geometry flags reach the `ar:` crops still holding the box they were measured on."""
import json

from glyph_atlas import ainu_geometry, tables
from glyph_atlas.schema import Box, Document, Line, Page, Unit

TALL = Box(x=100, y=50, w=80, h=320)


def dataset(tmp_path, box=TALL):
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="Example")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x", width=400, height=400)], Page)
    tables.write(root / "lines.parquet", [Line(id="l", page_id="p", seq=0, text="ヤ", text_raw="ヤ")], Line)
    tables.write(root / "units.parquet", [Unit(id="ar:moshiogusa--ninjal-2:17-l2-6", document_id="d", page_id="p", line_id="l",
                                               unicode="U+30E4", text_source="ヤ", box=box)], Unit)
    return root


def records(tmp_path, flags=("tall-crop", "blank-band"), box=(100, 50, 80, 320)):
    copy = tmp_path / "records" / "data" / "characters" / "moshiogusa--ninjal-2"
    copy.mkdir(parents=True)
    copy.joinpath("quality.json").write_text(json.dumps({"records": [
        {"id": "17-l2-6", "box": list(box), "flags": list(flags)}, {"id": "17-l2-7", "box": [0, 0, 9, 9], "flags": []}]}))
    return tmp_path / "records"


def test_a_crop_takes_the_flags_measured_on_its_box(tmp_path):
    root = dataset(tmp_path)
    result = ainu_geometry.run(root, records(tmp_path), apply=True)
    assert result["counts"] == {"flagged": 1, "tall-crop": 1, "blank-band": 1}
    assert ainu_geometry.load(root)["ar:moshiogusa--ninjal-2:17-l2-6"] == {
        "flags": ["tall-crop", "blank-band"], "box": {"x": 100, "y": 50, "w": 80, "h": 320}}


def test_a_crop_recut_since_has_no_flags(tmp_path):
    root = dataset(tmp_path, box=Box(x=110, y=50, w=52, h=34))
    assert ainu_geometry.run(root, records(tmp_path), apply=True)["counts"] == {"box changed since": 1}
    mark = {"flags": ["tall-crop"], "box": {"x": 100, "y": 50, "w": 80, "h": 320}}
    assert ainu_geometry.current(mark, {"x": 100, "y": 50, "w": 80, "h": 320}) == mark
    assert ainu_geometry.current(mark, {"x": 110, "y": 50, "w": 52, "h": 34}) is None


def test_only_sparse_ink_keeps_a_crop_out_of_the_quiz(tmp_path):
    assert ainu_geometry.out_of_quiz({"flags": ["sparse-ink"], "box": {}})
    assert not ainu_geometry.out_of_quiz({"flags": ["tall-crop", "blank-band", "large-margin", "wide-crop"], "box": {}})
    assert not ainu_geometry.out_of_quiz(None)

