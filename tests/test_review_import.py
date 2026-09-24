"""Background acquisition must never overwrite a live review or vanish on replay."""
from pathlib import Path

import pytest

from glyph_atlas import tables
from glyph_atlas.review.store import BadRequest, ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit


def dataset(root: Path, prefix: str) -> Path:
    root.mkdir()
    doc = Document(id=prefix, title=prefix)
    page = Page(id=f"{prefix}:p", document_id=prefix, seq=0, canvas="c", image="i",
                width=100, height=100)
    line = Line(id=f"{prefix}:l", page_id=page.id, seq=0, text="仮", text_raw="仮")
    unit = Unit(id=f"{prefix}:u", page_id=page.id, document_id=prefix, line_id=line.id,
                seq=0, reading="仮", box=Box(x=1, y=1, w=10, h=10))
    for name, row in (("documents", doc), ("pages", page), ("lines", line), ("units", unit)):
        tables.write(root / f"{name}.parquet", [row], type(row))
    return root


def test_import_preserves_review_retries_replay_and_live_metadata(tmp_path):
    root = dataset(tmp_path / "live", "original")
    new = dataset(tmp_path / "new", "new")
    live = Store(root)
    assert len(live.pages()) == len(live.documents()) == 1
    publisher = Store(root)
    assert publisher.import_dataset(new) == {"documents": 1, "pages": 1, "lines": 1, "units": 1}
    assert live.page("new:p") and live.document("new")
    live.record(ReviewRequest(target_type="unit", target_id="new:u", field="reading", new="假",
                              base_revision=0, client_id="test", idempotency_key="one"))
    assert publisher.import_dataset(new) == {"documents": 0, "pages": 0, "lines": 0, "units": 0}
    assert live.unit("new:u").reading == "假"
    assert live.revision("new:u") == 1 and len(live.events()) == 1
    live.rebuild()
    assert Store(root).unit("new:u").reading == "假"
    assert Store(root).page("new:p") is not None
    live.export()
    # An exported dataset must be portable without the database's imported baseline.
    from glyph_atlas.review.store import replay

    live.path.unlink()
    replay(root)
    restored = Store(root)
    assert restored.document("new") and restored.page("new:p")
    assert restored.unit("new:u").reading == "假"


def test_conflicting_or_orphan_import_is_atomic(tmp_path):
    root = dataset(tmp_path / "live", "original")
    new = dataset(tmp_path / "new", "new")
    store = Store(root)
    units = list(tables.read(new / "units.parquet", Unit))
    units[0].line_id = "missing"
    tables.write(new / "units.parquet", units, Unit)
    with pytest.raises(BadRequest, match="parents"):
        store.import_dataset(new)
    assert store.document("new") is None and store.line("new:l") is None
    units[0].line_id = "new:l"
    tables.write(new / "units.parquet", units, Unit)
    store.import_dataset(new)
    units[0].reading = "違"
    tables.write(new / "units.parquet", units, Unit)
    with pytest.raises(BadRequest, match="Conflicting"):
        store.import_dataset(new)
    assert store.unit("new:u").reading == "仮"


@pytest.mark.parametrize("name", ["pages", "documents"])
def test_concurrent_metadata_readers_share_cold_load_after_import(tmp_path, monkeypatch, name):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import sleep

    root = dataset(tmp_path / "live", "original")
    incoming = dataset(tmp_path / "new", "new")
    live = Store(root)
    setattr(live, "_" + name, None)
    reader = getattr(live, name)
    original = live._table_records
    calls = []

    def load(table_name):
        if table_name == name:
            calls.append(table_name)
            sleep(0.03)  # Let simultaneous requests reach the cold cache.
        return original(table_name)

    monkeypatch.setattr(live, "_table_records", load)

    def simultaneous():
        start = Barrier(6)
        def read(_):
            start.wait(timeout=5)
            return len(reader())
        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(read, range(6)))

    assert simultaneous() == [1] * 6
    assert calls == [name]
    Store(root).import_dataset(incoming)
    assert simultaneous() == [2] * 6
    assert calls == [name, name], "one source reload per import, shared by all readers"
