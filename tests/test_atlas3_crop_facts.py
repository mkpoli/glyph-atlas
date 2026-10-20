import importlib
import json
import sqlite3

from test_atlas3_publish import (  # noqa: F401 -- `layout` is the fixture
    PAGE,
    UNITS,
    crops,
    ids,
    layout,
    run,
    unit,
    write_source,
)

# The scripts folder is on the path once test_atlas3_publish is imported.
votes = importlib.import_module("votes")

ENGINES = [{"name": "NDLkotenOCR", "sha256": "fd75", "provider": "CUDAExecutionProvider"},
           {"name": "Atlas classifier", "sha256": "4360", "provider": "CUDAExecutionProvider"}]


def visual(text="三", image="/atlas/media/a.webp"):
    return json.dumps({"status": "ready", "candidates": [
        {"text": text, "score": 0.77, "engine": "NDLkotenOCR"},
        {"text": "ミ", "engine": "Atlas classifier", "verified": False, "identity_scope": "character", "family": "U+30DF", "score": 0.1}],
        "engines": ENGINES, "votes": [{"text": text, "score": 0.88, "engine": "Atlas classifier", "identity_scope": "character",
                                       "family": "U+4E09", "members": [text]}], "input_image": image, "verified": False},
        ensure_ascii=False)


CONTEXT = json.dumps({"status": "ready", "basis": "transcription", "candidates": [
    {"text": "三", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "十日ノ旅"},
    {"text": "三十", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "日ノ旅行"}],
    "line_id": "hk:1:L13", "anchor": 9, "source_text": "三"}, ensure_ascii=False)
NONE = json.dumps({"status": "unavailable", "candidates": []})
NO_CONTEXT = json.dumps({"status": "unavailable", "candidates": [], "basis": "transcription"})


def test_votes_are_stored_without_what_every_row_repeats_and_restore_whole():
    key, stored = votes.visual(visual())
    guessed = votes.context(CONTEXT)
    found = votes.entry(0, 1, stored, guessed)
    assert json.loads(key) == ENGINES and "NDLkotenOCR" not in votes.compact(found)
    assert "Nearby transcription" not in votes.compact(found)
    back = votes.restore(json.loads(key), found)
    assert back["visual"] == {k: v for k, v in json.loads(visual()).items() if k != "input_image"}
    assert back["context"] == json.loads(CONTEXT)
    assert votes.visual(NONE) == (None, None) and votes.context(NO_CONTEXT) is None
    assert votes.entry(0, None, None, None) is None
    assert votes.restore(None, None) == {"visual": json.loads(NONE), "context": json.loads(NO_CONTEXT)}


def voted(path, units):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE units({','.join(UNITS)},visual,context)")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * (len(UNITS) + 2))})", units)
    db.commit()
    return path


def test_a_publish_keeps_each_crops_votes_for_its_cut(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    a1, a2 = ids(root)["ex:a1"], ids(root)["ex:a2"]
    page = a1 // PAGE

    def held():
        return votes.read(root / "out" / "votes", page)

    box1, box2 = {"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 0, "y": 20, "w": 10, "h": 10}
    run(publish, monkeypatch, voted(tmp_path / "c1.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), visual("二"), NO_CONTEXT)]))
    assert {s: ("context" in e, e["cut"]) for s, e in held().items()} == {a1 % PAGE: (True, 0), a2 % PAGE: (False, 0)}
    assert remote.prefixes[-1] == "v3/votes/" and remote.uploaded[-1] == f"{page}.json.gz"
    # The same readings change nothing.
    uploads = len(remote.uploaded)
    run(publish, monkeypatch, voted(tmp_path / "c2.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT)]))
    assert len(remote.uploaded) == uploads
    # A re-cut takes the new image's readings for its new cut; no reading removes the crop's entry.
    run(publish, monkeypatch, voted(tmp_path / "c3.sqlite", [(*unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9}), visual("王"), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), NONE, NO_CONTEXT)]))
    entry = held()[a1 % PAGE]
    assert entry["cut"] == crops(remote)[a1]["cut"] == 1 and entry["visual"]["votes"][0]["text"] == "王"
    assert a2 % PAGE not in held()
    # A retired crop's entry goes with it.
    gone = unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9})
    run(publish, monkeypatch, voted(tmp_path / "c4.sqlite", [((gone[0], "retired", *gone[2:]) + (NONE, NO_CONTEXT))]))
    assert held() == {}
