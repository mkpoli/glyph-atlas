"""Verify live ONNX feature parity on prepared real crops."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from glyph_atlas.classify import Classifier


def main(*, disable_tf32=False):
    root = Path("work/visual-families")
    rows = [json.loads(line) for line in (root / "samples.jsonl").read_text().splitlines()]
    vectors = np.load(root / "embeddings.npy", allow_pickle=False)
    metadata = json.loads((root / "embeddings-metadata.json").read_text())
    if hashlib.sha256((root / "samples.jsonl").read_bytes()).hexdigest() != metadata["manifest_sha256"]:
        raise ValueError("embedding manifest changed")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDA unavailable; validation deferred")
    ort.preload_dlls()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    providers = [("CUDAExecutionProvider", {"gpu_mem_limit": 512 * 1024**2,
                                            "arena_extend_strategy": "kSameAsRequested",
                                            "use_tf32": int(not disable_tf32)})]
    path = root / "encoder.onnx"
    session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("ONNX CUDA initialization failed")
    encoder = Classifier(path, classes="models/classifier/classes.json", session=session)
    with path.open("rb") as handle:
        model_hashes = {path.name: hashlib.file_digest(handle, "sha256").hexdigest()}
    comparisons = []
    indexes = np.linspace(0, len(rows) - 1, 12, dtype=int)
    for index in indexes:
        row = rows[index]
        path = root / row["image_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["crop_sha256"]:
            raise ValueError("validation crop changed")
        with Image.open(path) as image:
            features = encoder.features(image)
        if features is None:
            raise ValueError("ONNX export lacks features")
        comparisons.append({"id": row["id"], "cosine": float(features @ vectors[index]),
                            "feature_max_abs": float(np.max(np.abs(features - vectors[index])))})
    # TF32 kernels differ slightly from PyTorch float32 on individual coordinates;
    # angular agreement is the relevant quantity for the normalized feature space.
    feature_tolerance = 1e-4
    report = {"samples": len(comparisons), "device": "CUDAExecutionProvider", "tf32": not disable_tf32,
              "onnx_sha256": model_hashes, "checkpoint_sha256": metadata["checkpoint_sha256"],
              "manifest_sha256": metadata["manifest_sha256"], "comparisons": comparisons,
              "feature_max_abs_tolerance": feature_tolerance,
              "passed": all(row["cosine"] >= .99999 and row["feature_max_abs"] < feature_tolerance
                            for row in comparisons)}
    report_name = "onnx-parity-fp32.json" if disable_tf32 else "onnx-parity.json"
    (root / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise ValueError("ONNX feature parity failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disable-tf32", action="store_true")
    main(**vars(parser.parse_args()))
