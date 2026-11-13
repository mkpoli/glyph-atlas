import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from glyph_atlas import allograph_pack, form_clusters


def test_write_packs_the_family_and_leaves_out_per_holder_images(tmp_path, monkeypatch):
    forms = tmp_path / "forms"
    forms.mkdir()
    ids = ["codh:a", "hi:b", "hl:c", "codh:other"]
    pq.write_table(pa.table({"id": ids, "family": ["U+5019"] * 3 + ["U+4E00"], "cluster": ["small", "big", "big", "x"]}),
                   forms / "units.parquet")
    (forms / "clusters.json").write_text(json.dumps({"families": {"U+5019": {"clusters": [{"id": "big"}, {"id": "small"}]}}}))
    np.save(forms / "embeddings.npy", np.arange(8, dtype=np.float32).reshape(4, 2))
    (forms / "manifest.json").write_text(json.dumps({"revision": "rev1"}))
    Image.new("L", (40, 80), 30).save(tmp_path / "a.png")

    corpus = {"codh:a": "codh-full", "hi:b": "hilab", "hl:c": "honkoku-lines"}
    monkeypatch.setattr(form_clusters, "glyphs", lambda root, wanted: [{"id": i, "corpus": corpus[i]} for i in sorted(wanted)])
    monkeypatch.setattr(form_clusters, "Pixels", lambda root: lambda glyph: (tmp_path / "a.png", None) if glyph["id"] == "codh:a" else None)

    stats = allograph_pack.write(tmp_path, forms, "U+5019", tmp_path / "out")
    assert stats == {"pack": str(tmp_path / "out" / "U+5019.npz"), "crops": 2, "with_pixels": 1, "left_out": 1, "revision": "rev1"}
    pack = np.load(stats["pack"])
    assert pack["ids"].tolist() == ["codh:a", "hi:b"]
    assert pack["classifier_cluster"].tolist() == [2, 1]
    assert pack["embedding"].tolist() == [[0, 1], [2, 3]]
    assert pack["size"].tolist() == [[80, 40], [0, 0]]
    assert pack["grey"][0, :80, :40].max() == 30 and pack["grey"][0, 80:, :].min() == 255
    assert str(pack["code_point"]) == "U+5019" and str(pack["revision"]) == "rev1"
