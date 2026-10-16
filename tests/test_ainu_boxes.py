"""ainu-records' crop repairs reach the atlas units still holding the rectangle they replaced."""
import json

from glyph_atlas import ainu_boxes, tables
from glyph_atlas.review.store import ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit

PLACE = {"key": "moshiogusa/ninjal-2", "id": "17-l2-6"}
TALL = Box(x=100, y=50, w=80, h=320)


def dataset(tmp_path, box=TALL, review="machine"):
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="Example")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x", width=400, height=400)], Page)
    tables.write(root / "lines.parquet", [Line(id="l", page_id="p", seq=0, text="ヤ", text_raw="ヤ")], Line)
    tables.write(root / "units.parquet", [Unit(id="u", document_id="d", page_id="p", line_id="l", unicode="U+30E4",
                                               text_source="ヤ", box=box, review=review, meta={"ainu_records": PLACE})], Unit)
    return root


def ledger(tmp_path, before=(100, 50, 80, 320), after=(110, 50, 52, 34)):
    path = tmp_path / "2026-10-16-applied.json"
    path.write_text(json.dumps({"version": 1, "policy": "crop-whitespace-v4", "batches": [{"unit": PLACE["key"], "changes": [
        {"before": {"id": PLACE["id"], "box": list(before)}, "after": {"id": PLACE["id"], "box": list(after)}}]}]}))
    return path


def test_a_unit_holding_the_old_rectangle_takes_the_repair_once(tmp_path):
    root, path = dataset(tmp_path), ledger(tmp_path)
    assert ainu_boxes.run(root, path)["counts"] == {"proposed": 1}
    assert Store(root).unit("u").box == TALL
    assert ainu_boxes.run(root, path, apply=True)["counts"] == {"recut": 1}
    assert Store(root).unit("u").box == Box(x=110, y=50, w=52, h=34)
    assert ainu_boxes.run(root, path, apply=True)["counts"] == {"already repaired": 1}


def test_a_box_drawn_since_or_a_reviewed_unit_stays(tmp_path):
    root = dataset(tmp_path, box=Box(x=101, y=50, w=80, h=320))
    assert ainu_boxes.run(root, ledger(tmp_path), apply=True)["counts"] == {"box changed since": 1}
    other = tmp_path / "other"
    other.mkdir()
    root = dataset(other)
    store = Store(root)
    store.record_batch([ReviewRequest(target_id="u", field="note", new="checked", base_revision=store.revision("u"),
                                      client_id="reviewer", idempotency_key="k")], role="reviewer")
    assert ainu_boxes.run(root, ledger(other), apply=True)["counts"] == {"reviewed": 1}


def test_a_repair_leaving_the_page_is_refused(tmp_path):
    root = dataset(tmp_path)
    assert ainu_boxes.run(root, ledger(tmp_path, after=(380, 50, 52, 34)))["counts"] == {"off the page": 1}
