"""A publication stores image OCR for every crop it ships."""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from glyph_atlas.review import suggestions

SCHEMA = "\n".join(p.read_text() for p in sorted(Path("apps/cloudflare/migrations").glob("*.sql")))
ENGINES = [{"name": "NDLkotenOCR", "sha256": "x", "provider": "CUDAExecutionProvider"}]
READY = {"status": "ready", "candidates": [{"text": "天"}], "engines": ENGINES}


class Media:
    def __init__(self, directory):
        self.directory = directory

    def materialize(self, key):
        path = self.directory / (key + ".webp")
        Image.new("RGB", (8, 8), "white").save(path)
        return path


class Model:
    reads = 0

    def __init__(self, provider="CUDAExecutionProvider"):
        self.engines = [{**ENGINES[0], "provider": provider}]

    def read(self, image):
        Model.reads += 1
        return {"status": "ready", "candidates": [{"text": "大"}], "engines": self.engines}


@pytest.fixture
def export(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    monkeypatch.chdir(tmp_path)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    older = {**READY, "engines": [{**ENGINES[0], "sha256": "old"}]}
    for identity, visual in (("unread", {"status": "unavailable", "candidates": []}), ("read", READY),
                             ("older", older)):
        data = json.dumps({"image": f"/atlas/media/{identity[0] * 64}.webp"})
        db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, "local", "天", "天", None, None, "print", "han", "pending", 0, 1, 1, 0,
            data, "{}", "{}", json.dumps(visual)))
    Model.reads = 0
    return module, db, Media(tmp_path)


def visual(db, identity):
    return json.loads(db.execute("SELECT visual FROM units WHERE id=?", (identity,)).fetchone()[0])


def test_unread_crops_are_read_once_and_cached(export, monkeypatch):
    module, db, media = export
    monkeypatch.setattr(suggestions, "Recognizer", Model)
    module.read_crops(db, media)
    assert visual(db, "unread")["candidates"] == [{"text": "大"}]
    assert visual(db, "read") == READY
    assert visual(db, "older")["candidates"] == [{"text": "大"}]
    assert not list(Path().rglob("*.partial"))
    db.execute("UPDATE units SET visual='{\"status\":\"unavailable\",\"candidates\":[]}' WHERE id='unread'")
    module.read_crops(db, media)
    assert Model.reads == 2 and visual(db, "unread")["status"] == "ready"


def test_publication_refuses_without_cuda(export, monkeypatch):
    module, db, media = export
    monkeypatch.setattr(suggestions, "Recognizer", lambda: Model("CPUExecutionProvider"))
    with pytest.raises(RuntimeError):
        module.read_crops(db, media)
