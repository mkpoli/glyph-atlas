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
    tables.write(root / "units.parquet", [Unit(id="ar:u", document_id="d", page_id="p", line_id="l", unicode="U+30E4",
                                               text_source="ヤ", box=box, review=review, meta={"ainu_records": PLACE})], Unit)
    return root


def ledger(tmp_path, before=(100, 50, 80, 320), after=(110, 50, 52, 34), name="2026-10-16-applied.json",
           status="applied"):
    path = tmp_path / name
    path.write_text(json.dumps({"version": 1, "policy": "crop-whitespace-v4", "status": status, "batches": [
        {"unit": PLACE["key"], "changes": [
            {"before": {"id": PLACE["id"], "box": list(before)}, "after": {"id": PLACE["id"], "box": list(after)}}]}]}))
    return path


def test_a_unit_holding_the_old_rectangle_takes_the_repair_once(tmp_path):
    root, path = dataset(tmp_path), ledger(tmp_path)
    assert ainu_boxes.run(root, path)["counts"] == {"proposed": 1}
    assert Store(root).unit("ar:u").box == TALL
    assert ainu_boxes.run(root, path, apply=True)["counts"] == {"recut": 1}
    assert Store(root).unit("ar:u").box == Box(x=110, y=50, w=52, h=34)
    assert ainu_boxes.run(root, path, apply=True)["counts"] == {"already repaired": 1}


def test_a_box_drawn_since_or_a_reviewed_unit_stays(tmp_path):
    root = dataset(tmp_path, box=Box(x=101, y=50, w=80, h=320))
    assert ainu_boxes.run(root, ledger(tmp_path), apply=True)["counts"] == {"box changed since": 1}
    other = tmp_path / "other"
    other.mkdir()
    root = dataset(other)
    store = Store(root)
    store.record_batch([ReviewRequest(target_id="ar:u", field="note", new="checked", base_revision=store.revision("ar:u"),
                                      client_id="reviewer", idempotency_key="k")], role="reviewer")
    assert ainu_boxes.run(root, ledger(other), apply=True)["counts"] == {"reviewed": 1}


def test_a_repair_leaving_the_page_is_refused(tmp_path):
    root = dataset(tmp_path)
    assert ainu_boxes.run(root, ledger(tmp_path, after=(380, 50, 52, 34)))["counts"] == {"off the page": 1}


def test_a_proposed_ledger_is_not_read(tmp_path):
    root = dataset(tmp_path)
    assert ainu_boxes.run(root, ledger(tmp_path, status=None), apply=True)["counts"] == {}
    assert Store(root).unit("ar:u").box == TALL


def test_a_unit_follows_later_repairs_of_the_box_it_holds(tmp_path):
    root, ledgers = dataset(tmp_path), tmp_path / "ledgers"
    ledgers.mkdir()
    ledger(ledgers, after=(100, 50, 80, 60), name="2026-09-09-applied.json")
    ledger(ledgers, before=(100, 50, 80, 60), after=(110, 50, 52, 34), name="2026-10-17-applied.json")
    assert ainu_boxes.run(root, ledgers, apply=True)["counts"] == {"recut": 1}
    assert Store(root).unit("ar:u").box == Box(x=110, y=50, w=52, h=34)


def test_a_crop_moved_back_since_is_recut_again(tmp_path):
    root, path = dataset(tmp_path), ledger(tmp_path)
    ainu_boxes.run(root, path, apply=True)
    store = Store(root)
    store.record_batch([ReviewRequest(target_id="ar:u", field="box", new=TALL.model_dump(mode="json"),
                                      base_revision=store.revision("ar:u"), client_id="other-pass",
                                      idempotency_key="back")], role="model")
    assert ainu_boxes.run(root, path, apply=True)["counts"] == {"recut": 1}
    assert Store(root).unit("ar:u").box == Box(x=110, y=50, w=52, h=34)
