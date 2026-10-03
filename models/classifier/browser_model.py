"""Build the model the browser's image search downloads: the classifier in float16 and its classes.

    python models/classifier/browser_model.py
    python models/classifier/browser_model.py --checkpoint models/classifier/artifacts/best.pt --parity 300

Writes `<out>/<version>/`:

- `classifier.onnx`: the checkpoint exported with `export_onnx.py --half`. One float32 input,
  `pixel_values` (batch, 3, size, size), and the float32 outputs `logits`, `probs` (calibrated) and
  `features` (the vectors `atlas similar index` embeds every published crop with);
- `classes.json`: one entry per output class, in output order: `{"class", "chars", "family"}`.
  `chars` are the characters a class stands for: one, or every member of a family CODH trained as
  one class (`suggestions.MERGED_RELATIONS`), whose `family` names it; `other` has none;
- `model.json`: what a reader needs to use the two files: the version, the digest of the checkpoint
  they come from (the `encoder` of a similar-crop index built with it), the preprocessing, each
  file's size and SHA-256, and the parity check against the float32 PyTorch model.

The version is the first 16 hex digits of the SHA-256 over both files, the encoder and the
preprocessing, so a new export, class list or checkpoint is a new version. A build whose parity check
does not pass writes nothing. `scripts/publish_browser_model.sh` uploads a version to R2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import export_onnx
import torch
import train

from glyph_atlas import classify
from glyph_atlas.review.suggestions import _class_family

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def feature_width(path: Path) -> int:
    """The width of the export's `features` output, read from the graph."""
    import onnx

    output = next(value for value in onnx.load(str(path)).graph.output if value.name == "features")
    width = int(output.type.tensor_type.shape.dim[1].dim_value)
    if width < 1:
        raise SystemExit(f"{path}: the `features` output has no fixed width")
    return width


def class_entries(classes: list[str]) -> list[dict]:
    """Each class with the characters it stands for, as the review panel groups them."""
    entries = []
    for name in classes:
        family, members = _class_family(name)
        entries.append({"class": name, "chars": list(members),
                        "family": family if len(members) > 1 else None})
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models/classifier/artifacts/best.pt")
    parser.add_argument("--out", type=Path, default=ROOT / "work/browser-model")
    parser.add_argument("--data", type=Path, default=None, help="the manifests the parity check reads")
    parser.add_argument("--parity", type=int, default=200, help="val crops to compare with the float32 model")
    args = parser.parse_args()

    if not args.checkpoint.is_file():
        raise SystemExit(f"{args.checkpoint} is missing")
    if args.parity < 1:
        raise SystemExit("--parity must compare at least one crop: a model the browser downloads is always checked")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, classes, temperature, config = export_onnx.build(args.checkpoint, device, args.config)
    train.check_classes(model, classes, where=str(args.checkpoint))
    size = int(config["preprocessing"]["size"])

    args.out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".partial-", dir=args.out))
    onnx_path = staging / "classifier.onnx"
    report = export_onnx.export(model, onnx_path, temperature, size, int(config["artifacts"]["opset"]),
                                str(config["artifacts"]["exporter"]) == "dynamo", half=True)
    import onnx

    onnx.checker.check_model(str(onnx_path))
    width = export_onnx.exported_classes(onnx_path)
    if width != len(classes):
        raise SystemExit(f"{onnx_path}: the graph returns {width} classes and the class list names {len(classes)}")
    classes_path = staging / "classes.json"
    classes_path.write_text(json.dumps(class_entries(classes), ensure_ascii=False, separators=(",", ":")) + "\n",
                            encoding="utf-8")

    data = args.data or (ROOT / config["data"]["directory"])
    compared = export_onnx.parity(model, classes, onnx_path, data, config, count=args.parity, split="val",
                                  device=device, precision="fp32", temperature=temperature,
                                  tolerance=export_onnx.HALF_TOLERANCE)
    print(json.dumps(compared, indent=2), flush=True)
    if compared.get("passed") is not True:
        shutil.rmtree(staging, ignore_errors=True)
        raise SystemExit("the float16 export does not reproduce the PyTorch model, or no crop was compared")

    files = {name: {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
             for name, path in (("model", onnx_path), ("classes", classes_path))}
    encoder = digest(args.checkpoint)
    preprocessing = {"size": size, "grey": True, "pad": classify.PAD, "mean": classify.MEAN, "std": classify.STD,
                     "resample": "bilinear"}
    version = hashlib.sha256(json.dumps([files["model"]["sha256"], files["classes"]["sha256"], encoder, preprocessing],
                                        sort_keys=True).encode()).hexdigest()[:16]
    manifest = {
        "version": version,
        "encoder": encoder,
        "precision": report["precision"],
        "opset": report["opset"],
        "classes": len(classes),
        "features": feature_width(onnx_path),
        "preprocessing": preprocessing,
        "files": files,
        "parity": compared,
    }
    (staging / "model.json").write_text(json.dumps(manifest, indent=1, allow_nan=False) + "\n")
    target = args.out / version
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    print(json.dumps({"directory": str(target), **{k: manifest[k] for k in ("version", "encoder")},
                      "bytes": files["model"]["bytes"]}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
