"""Tests for the similar-crop index: where each crop's pixels come from, and reuse of vectors."""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image

from glyph_atlas import similar


def webp(shade: int, size=(20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size, shade).save(buffer, "WEBP")
    return buffer.getvalue()


def export(directory: Path, rows: list[tuple[str, str, str, bytes | None]]) -> Path:
    """A catalogue export whose units show `(id, label, key, bytes)`; `None` bytes means the pack is gone."""
    directory.mkdir(parents=True)
    db = sqlite3.connect(directory / "catalogue.sqlite")
    db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, origin TEXT, data TEXT)")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    pack = bytearray()
    for identity, label, key, data in rows:
        db.execute("INSERT INTO units VALUES (?,?,?)",
                   (identity, "local", json.dumps({"label": label, "image": f"/atlas/media/{key}.webp"})))
        object_name = "pack-0001.bin" if data is not None else "pack-0002.bin"
        db.execute("INSERT INTO media VALUES (?,?,?,?,?)",
                   (key, object_name, len(pack), len(data or b""), "image/webp"))
        pack += data or b""
    db.commit()
    db.close()
    (directory / "pack-0001.bin").write_bytes(bytes(pack))
    return directory / "catalogue.sqlite"


def test_local_crops_read_packs_and_expect_the_rest_from_the_site(tmp_path):
    first = export(tmp_path/"old", [("ex:1", "飍", "a" * 64, webp(10)), ("ex:2", "字", "b" * 64, webp(20)),
                                    ("ar:1", "ツ゚", "e" * 64, webp(40))])
    second = export(tmp_path/"new", [("ex:2", "宇", "c" * 64, None)])
    # Publication order decides, whatever the files' times.
    os.utime(first, (2_000_000_000, 2_000_000_000))
    crops = similar.local_crops([first, second], tmp_path/"fetched")
    assert crops["ar:1"]["label"] == "ツ゚", "a label of more than one code point is kept"
    assert crops["ex:1"]["path"] == str(tmp_path/"old"/"pack-0001.bin") and crops["ex:1"]["label"] == "飍"
    # The newer export's row wins, and its pack is gone, so the crop is fetched by key.
    assert crops["ex:2"]["label"] == "宇" and "range" not in crops["ex:2"]
    assert crops["ex:2"]["path"] == str(tmp_path/"fetched"/"cc"/f"{'c' * 64}.webp")


def test_a_locked_export_stops_the_run(tmp_path):
    import pytest
    catalogue = export(tmp_path/"busy", [("ex:1", "飍", "a" * 64, webp(10))])
    writer = sqlite3.connect(catalogue)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(similar.ExportUnreadable):
            similar.local_crops([catalogue], tmp_path/"fetched")
    finally:
        writer.rollback()
        writer.close()


def test_fetch_downloads_only_missing_display_crops(tmp_path, http_server):
    http_server.put(f"atlas/media/{'c' * 64}.webp", webp(30))
    http_server.put(f"atlas/media/{'e' * 64}.webp", b"<html>not an image</html>")
    crops = {"ex:2": {"key": "c" * 64, "path": str(tmp_path/"cc"/f"{'c' * 64}.webp")},
             "ex:3": {"key": "d" * 64, "path": str(tmp_path/"dd"/f"{'d' * 64}.webp")},
             "ex:4": {"key": "e" * 64, "path": str(tmp_path/"ee"/f"{'e' * 64}.webp")},
             "ex:1": {"key": "a" * 64, "path": "pack", "range": (0, 1)}}
    assert similar.fetch(crops, http_server.base_url) == {"held": 0, "fetched": 1, "failed": 2}
    assert similar.fetch(crops, http_server.base_url) == {"held": 1, "fetched": 0, "failed": 2}
    assert not (tmp_path/"ee").exists(), "a body that is not WebP is not kept"


def test_cut_reads_pack_ranges_page_boxes_and_whole_files(tmp_path):
    pack = tmp_path/"pack.bin"
    first, second = webp(10), webp(200)
    pack.write_bytes(first + second)
    ids, arrays = similar._cut((str(pack), [("a", None, (0, len(first))), ("b", None, (len(first), len(second)))]), 32)
    assert ids == ["a", "b"] and arrays.shape == (2, 32, 32)
    page = tmp_path/"page.png"
    Image.new("L", (100, 100), 255).save(page)
    ids, arrays = similar._cut((str(page), [("c", (10, 10, 20, 40), None), ("d", (200, 200, 5, 5), None)]), 32)
    assert ids == ["c"]
    ids, _ = similar._cut((str(page), [("e", None, None)]), 32)
    assert ids == ["e"]
    broken = tmp_path/"broken.png"
    broken.write_bytes(b"not an image")
    assert similar._cut((str(broken), [("f", None, None)]), 32) == ([], None)


class Encoder:
    size = 16
    calls = 0

    def __init__(self, checkpoint):
        pass

    def __call__(self, pixels):
        Encoder.calls += len(pixels)
        vectors = np.zeros((len(pixels), 4), dtype=np.float32)
        vectors[:, 0] = pixels.reshape(len(pixels), -1).mean(1) / 255 + 0.1
        vectors[:, 1] = 1
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_index_embeds_new_crops_and_reuses_unchanged_ones(tmp_path, monkeypatch):
    from glyph_atlas import form_clusters
    monkeypatch.setattr(form_clusters, "Encoder", Encoder)
    checkpoint = tmp_path/"best.pt"
    checkpoint.write_bytes(b"weights")
    page = tmp_path/"page.png"
    Image.new("L", (100, 100), 255).save(page)
    corpus = {"hi:1": {"path": str(page), "box": (0, 0, 10, 10), "label": "字", "origin": "corpus", "source": "hilab"}}
    monkeypatch.setattr(similar, "corpus_crops", lambda root: dict(corpus))
    catalogue = export(tmp_path/"export", [("ex:1", "飍", "a" * 64, webp(10))])
    out = tmp_path/"similar"

    first = similar.index(tmp_path, [catalogue], out, checkpoint=checkpoint, workers=1, base="http://unused")
    assert first["crops"] == 2 and first["embedded"] == 2 and Encoder.calls == 2
    rows = pq.read_table(out/"current"/"units.parquet").to_pydict()
    assert rows["id"] == ["ex:1", "hi:1"] and rows["origin"] == ["local", "corpus"]
    assert np.load(out/"current"/"vectors.npy").shape == (2, 4)

    corpus["hi:2"] = {"path": str(page), "box": (20, 20, 10, 10), "label": "宇", "origin": "corpus", "source": "hilab"}
    second = similar.index(tmp_path, [catalogue], out, checkpoint=checkpoint, workers=1, base="http://unused")
    assert second["embedded"] == 1 and second["reused"] == 2 and Encoder.calls == 3
    assert second["revision"] != first["revision"]
    assert (out/"current").resolve().name == second["revision"]

    # A label that changes, with the same pixels, is a new revision without re-embedding.
    corpus["hi:2"]["label"] = "字"
    third = similar.index(tmp_path, [catalogue], out, checkpoint=checkpoint, workers=1, base="http://unused")
    assert third["revision"] != second["revision"] and third["embedded"] == 0 and Encoder.calls == 3
    assert pq.read_table(out/"current"/"units.parquet").to_pydict()["label"] == ["飍", "字", "字"]


def test_neighbours_list_the_nearest_and_the_nearest_filed_differently(tmp_path):
    import pyarrow as pa
    import pytest
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("neighbours run on CUDA")
    directory = tmp_path/"rev"
    directory.mkdir()
    angles = [0.0, 0.08, 0.12, 1.0, 1.05, 0.03, 0.05]
    vectors = np.array([[np.cos(a), np.sin(a)] for a in angles], dtype=np.float16)
    np.save(directory/"vectors.npy", vectors)
    pq.write_table(pa.table({"id": ["a", "b", "c", "d", "e", "f", "g"], "label": ["字", "字", "宇", "字", None, "字", "字"]}),
                   directory/"units.parquet")
    # A gallery chunk of two forces the merge across chunks.
    manifest = similar.neighbours(directory, k=2, block=2, gallery=2, digits=1)
    entries = {}
    for shard in (directory/"neighbours").glob("?.json"):
        entries.update(json.loads(shard.read_text()))
    assert manifest["crops"] == 7 and set(entries) == {"a", "b", "c", "d", "e", "f", "g"}
    assert [n for n, _ in entries["a"]["similar"]] == ["f", "g"]
    # Its nearest crops are all 字, and the differently filed list is searched on its own.
    assert [n for n, _ in entries["a"]["filed_differently"]] == ["c"]
    assert [n for n, _ in entries["d"]["similar"]] == ["e", "c"]
    # An unlabelled crop is a neighbour, but has no list of its own of differently filed ones.
    assert "filed_differently" not in entries["e"]
