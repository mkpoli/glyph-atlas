"""Expose the existing classifier's pre-logit features without changing its weights."""
import argparse
import hashlib
import json
from pathlib import Path

import onnx
from onnx import TensorProto, helper


def export(source, target):
    model = onnx.load(str(source))
    head = next(node for node in model.graph.node if node.op_type == "Gemm" and "logits" in node.output)
    weight = next(tensor for tensor in model.graph.initializer if tensor.name == head.input[1])
    width = weight.dims[1]
    model.graph.node.append(helper.make_node("Identity", [head.input[0]], ["features"],
                                            name="visual_features"))
    model.graph.output.append(helper.make_tensor_value_info("features", TensorProto.FLOAT, ["batch", width]))
    onnx.checker.check_model(model)
    onnx.save(model, str(target))
    target.with_suffix(".json").write_text(json.dumps({
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "export_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "embedding_dimension": width, "weights_changed": False,
    }, indent=2) + "\n")
    print(json.dumps({"dimension": width, "weights_changed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("models/classifier/artifacts/classifier.onnx"))
    parser.add_argument("--output", type=Path, default=Path("models/classifier/artifacts/classifier-with-features.onnx"))
    args = parser.parse_args()
    export(args.source, args.output)
