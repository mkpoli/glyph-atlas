"""A tiny stand-in for the browser model, in the served model's layout, for tools/image-search-check.mjs.

    python apps/review/tools/image-search-fixture.py <directory>

Writes `classifier.onnx` (float32 `pixel_values` (1, 3, 128, 128) in; `logits`, `probs` over four
classes and 512-wide `features` out), `classes.json` and a `model.json` manifest as
models/classifier/browser_model.py writes them, so the page downloads, checks and runs it as it would
the real one. The model averages the image and answers 字 for any input.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(0)
weights = [
    numpy_helper.from_array(rng.standard_normal((3, 512)).astype(np.float32), "w1"),
    numpy_helper.from_array(rng.standard_normal(512).astype(np.float32) + 1, "b1"),
    numpy_helper.from_array(np.zeros((512, 4), dtype=np.float32), "w2"),
    numpy_helper.from_array(np.array([4, 1, 0, 0], dtype=np.float32), "b2"),
]
nodes = [
    helper.make_node("ReduceMean", ["pixel_values"], ["pooled"], axes=[2, 3], keepdims=0),
    helper.make_node("MatMul", ["pooled", "w1"], ["raw"]),
    helper.make_node("Add", ["raw", "b1"], ["features"]),
    helper.make_node("MatMul", ["features", "w2"], ["scaled"]),
    helper.make_node("Add", ["scaled", "b2"], ["logits"]),
    helper.make_node("Softmax", ["logits"], ["probs"], axis=-1),
]
graph = helper.make_graph(
    nodes, "image-search-fixture",
    [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, [1, 3, 128, 128])],
    [helper.make_tensor_value_info(name, TensorProto.FLOAT, [1, width])
     for name, width in (("logits", 4), ("probs", 4), ("features", 512))],
    weights,
)
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
model.ir_version = 8
onnx.checker.check_model(model)
(out / "classifier.onnx").write_bytes(model.SerializeToString())
classes = [{"class": "U+5B57", "chars": ["字"], "family": None},
           {"class": "U+56FD", "chars": ["国", "國"], "family": "U+56FD"},
           {"class": "U+304B", "chars": ["か"], "family": None},
           {"class": "other", "chars": [], "family": None}]
(out / "classes.json").write_text(json.dumps(classes, ensure_ascii=False))


def described(path: Path) -> dict:
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


files = {"model": described(out / "classifier.onnx"), "classes": described(out / "classes.json")}
manifest = {"version": hashlib.sha256((files["model"]["sha256"] + files["classes"]["sha256"]).encode()).hexdigest()[:16],
            "encoder": "f" * 64, "precision": "float32", "classes": 4, "features": 512,
            "preprocessing": {"size": 128, "grey": True, "pad": 255, "mean": 0.449, "std": 0.226, "resample": "bilinear"},
            "files": files, "parity": {"passed": True}}
(out / "model.json").write_text(json.dumps(manifest))
print(json.dumps({"directory": str(out), "version": manifest["version"]}))
