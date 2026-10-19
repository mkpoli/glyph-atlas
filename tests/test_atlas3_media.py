import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import catalogue, layout, run, unit  # noqa: F401 -- `layout` is the fixture

KEY = "abc" + "1" * 61


def shard(root, prefix="abc"):
    return json.loads(gzip.decompress((root / "out" / "media" / f"{prefix}.json.gz").read_bytes()))


def module():
    sys.modules.pop("media_map", None)
    return importlib.import_module("media_map")


def test_the_map_grows_by_new_keys_and_refuses_a_key_moved(layout):  # noqa: F811
    root, _publish, _remote = layout
    media_map = module()
    other = "abc" + "2" * 61
    assert media_map.merge([(KEY, "packs/a.bin", 10, 5, "image/webp"), (other, "packs/b.bin", 0, 7, "image/png")]) == (2, 1)
    assert shard(root) == {"objects": ["packs/a.bin", "packs/b.bin"], "keys": {"1" * 61: [0, 10, 5], "2" * 61: [1, 0, 7, "image/png"]}}
    assert media_map.merge([(KEY, "packs/a.bin", 10, 5, "image/webp")]) == (0, 0)
    with pytest.raises(SystemExit):
        media_map.merge([("abd" + "0" * 61, "packs/c.bin", 0, 1, None), (KEY, "packs/a.bin", 11, 5, None)])
    # The refused run wrote nothing, not even the other shard's new key.
    assert not (root / "out" / "media" / "abd.json.gz").exists()


def test_a_shard_left_unsent_goes_with_the_next_send(layout):  # noqa: F811
    _root, _publish, _remote = layout
    media_map = module()
    media_map.merge([(KEY, "packs/a.bin", 10, 5, None)])
    sent = []

    def failing(files, prefix):
        raise SystemExit("network")

    with pytest.raises(SystemExit):
        media_map.send(failing)
    assert media_map.send(lambda files, prefix: sent.append((prefix, [f.name for f in files]))) == 1
    assert sent == [("v3/media/", ["abc.json.gz"])]
    assert media_map.send(lambda files, prefix: sent.append(prefix)) == 0


def test_a_publish_maps_its_catalogues_images_before_its_bundles(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:new", "hl:p3", {"x": 0, "y": 0, "w": 10, "h": 10})])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE media(key, object, offset, size, content_type)")
    db.execute("INSERT INTO media VALUES(?, 'packs/n.bin', 0, 9, 'image/webp')", (KEY,))
    db.commit()
    run(publish, monkeypatch, path)
    assert remote.prefixes[0] == "v3/media/" and remote.uploaded[0] == "abc.json.gz"
    assert "v3/pages/" in remote.prefixes[1:]
    assert shard(root)["keys"]["1" * 61] == [0, 0, 9]


def test_a_list_naming_a_shard_never_written_stops_the_send(layout):  # noqa: F811
    root, _publish, _remote = layout
    media_map = module()
    (root / "out" / "media").mkdir(parents=True, exist_ok=True)
    (root / "out" / "media" / "unsent").write_text("fff\n")
    with pytest.raises(SystemExit):
        media_map.send(lambda files, prefix: None)
