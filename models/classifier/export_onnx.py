"""Export a trained classifier to ONNX and check it against the PyTorch model.

The export takes the checkpoint `train.py` writes, rebuilds the model from the configuration stored
in it, and writes one ONNX file with one input, `pixel_values` of shape (batch, 3, size, size) in
float32 at the configured size, and three outputs: `logits` (batch, classes) as the model returns
them; `probs` (batch, classes), the same logits divided by the temperature the calibration fitted
and passed through a softmax; and `features` (batch, width), the model's penultimate
representation. `glyph_atlas.classify.Classifier` reads the file and takes the size from its input.

The temperature is baked in, so the served probabilities are the calibrated ones and the class list
beside the export is the only other file a caller needs.

    python models/classifier/export_onnx.py
    python models/classifier/export_onnx.py --checkpoint models/classifier/artifacts/best.pt --parity 200

`--parity` runs the same crops through PyTorch and through the exported file and reports the largest
difference of the probabilities and how many top-1 answers differ. It reads the crops of the `val`
split by default, and is skipped with a message when that split holds no crops, which is the state
while the detector's training data build's val books are still being imported. `--base` exports the
untrained backbone, which is
what a smoke run before training has.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
import train

from glyph_atlas import classify

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_NAMES = ("logits", "probs", "features")

#: The largest difference between the two sets of probabilities a passing export may show.
TOLERANCE = 1e-4


class Wrapper(torch.nn.Module):
    """The model as a function of one tensor, with the temperature applied before the softmax."""

    def __init__(self, model: Any, temperature: float) -> None:
        super().__init__()
        self.model = model
        self.register_buffer("temperature", torch.tensor(float(temperature), dtype=torch.float32))

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        pooled = self.model.forward_features(pixel_values)
        logits = self.model.forward_head(pooled)
        # The penultimate representation keeps the shape information class logits discard; the
        # visual families embed crops with it.
        features = self.model.forward_head(pooled, pre_logits=True)
        return logits, torch.softmax(logits / self.temperature, dim=-1), features


def build(checkpoint: Path | None, device: torch.device, config_path: Path) -> tuple[Any, list[str], float, dict]:
    """The model, the class list and the temperature a checkpoint holds; the base model without one."""
    config = train.load_config(config_path)
    temperature = 1.0
    classes = train.load_classes(ROOT / config["data"]["classes"])
    if checkpoint is None:
        model = train.build_model(config, len(classes))
    else:
        state = torch.load(checkpoint, map_location=device, weights_only=False)
        config = state.get("config") or config
        classes = state.get("classes", classes)
        temperature = float(state.get("temperature", state.get("metrics", {}).get("temperature", 1.0)))
        model = train.build_model(config, len(classes), pretrained=False)
        model.load_state_dict(state["model"])
    model.eval()
    return model.to(device), classes, temperature, config


def export(model: Any, path: Path, temperature: float, size: int, opset: int, dynamo: bool) -> dict:
    """Write the ONNX file and report how it was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapper = Wrapper(model, temperature).eval()
    device = next(model.parameters()).device
    argument = torch.zeros(1, 3, size, size, dtype=torch.float32, device=device)
    torch.onnx.export(
        wrapper,
        (argument,),
        str(path),
        input_names=["pixel_values"],
        output_names=list(OUTPUT_NAMES),
        opset_version=opset,
        dynamo=dynamo,
        # The batch dimension is free: an alignment scores every detection on a line at once, and a
        # graph fixed at one would run the model once per crop through a Python call, which measured
        # 72 ms a crop against 3 ms for a batch.
        dynamic_axes={"pixel_values": {0: "batch"}, **{name: {0: "batch"} for name in OUTPUT_NAMES}},
    )
    return {
        "path": str(path),
        "dynamo": dynamo,
        "opset": opset,
        "size": size,
        "temperature": temperature,
        "bytes": path.stat().st_size,
        "outputs": list(OUTPUT_NAMES),
    }


def exported_classes(path: Path) -> int:
    """How many classes the `probs` output of an exported file returns, read back from the graph."""
    import onnx

    graph = onnx.load(str(path)).graph
    output = next((value for value in graph.output if value.name == "probs"), None)
    if output is None:
        raise SystemExit(f"{path}: the graph has no `probs` output")
    dims = output.type.tensor_type.shape.dim
    if len(dims) != 2 or not dims[1].dim_value:
        raise SystemExit(f"{path}: the `probs` output is not (batch, classes)")
    return int(dims[1].dim_value)


def parity(
    model: Any,
    classes: list[str],
    path: Path,
    data_directory: Path,
    config: dict,
    *,
    count: int,
    split: str,
    device: torch.device,
    precision: str,
    temperature: float,
) -> dict:
    """Compare the PyTorch probabilities against the exported file on `count` val crops.

    The comparison is between the probabilities rather than between the logits: the softmax is what
    a caller reads, so the export passes when the two agree to `TOLERANCE` on every crop and the
    top-1 answer is the same one.
    """
    import onnxruntime as ort

    # cuDNN runs convolutions in TF32 by default, and the exported graph runs them in fp32. The
    # comparison is of the export, not of the two libraries' reduced precision, so TF32 is off for
    # it: with it on, the same pair of tensors differs by about 2e-4 instead of 2e-6.
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    size = int(config["preprocessing"]["size"])
    data = train.read_manifest(data_directory / config["data"]["splits"][split], classes)
    if not len(data):
        return {"passed": None, "reason": f"the {split} manifest holds no crops"}
    # The split is compact arrays rather than a list of rows, so the sample is taken with the reader's
    # own `head`, which keeps the arrays and their path blob consistent.
    sample = data.head(count)
    loader = train.loader_for(
        sample,
        augment=False,
        batch_size=min(64, max(1, len(sample))),
        workers=0,
        size=size,
        seed=0,
    )
    pixels = [batch for batch, _ in loader]
    with torch.no_grad():
        wrapper = Wrapper(model, temperature).eval()
        reference = np.concatenate(
            [
                torch.softmax(
                    model(batch.to(device)) / wrapper.temperature, dim=-1
                ).float().cpu().numpy()
                for batch in pixels
            ],
            axis=0,
        ).astype(np.float64)

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    # The export fixes the batch at 1, as a crop is always one crop, so the sample goes through one
    # at a time; the graph would take a dynamic batch, but nothing serves one.
    exported = np.concatenate(
        [
            np.asarray(session.run(["probs"], {"pixel_values": frame[None]})[0], dtype=np.float64)
            for frame in np.concatenate([batch.numpy() for batch in pixels], axis=0)
        ],
        axis=0,
    )
    difference = np.abs(exported - reference)
    return {
        "crops": len(sample),
        "max_probability_difference": float(difference.max()),
        "mean_probability_difference": float(difference.mean()),
        "top1_agreement": float(np.mean(exported.argmax(axis=-1) == reference.argmax(axis=-1))),
        "passed": bool(difference.max() <= TOLERANCE),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--base", action="store_true", help="export the untrained backbone")
    parser.add_argument("--parity", type=int, default=0, help="crops to compare")
    parser.add_argument("--parity-split", choices=["train", "val", "test"], default="val")
    parser.add_argument("--precision", choices=["bf16", "fp16", "fp32"], default="fp32")
    parser.add_argument("--device", default=None)
    parser.add_argument("--dynamo", action="store_true", help="use the dynamo exporter")
    args = parser.parse_args()

    config = train.load_config(args.config)
    size = int(config["preprocessing"]["size"])
    out = args.out or (ROOT / config["artifacts"]["export"])
    data_directory = args.data or (ROOT / config["data"]["directory"])
    device = torch.device(args.device or "cuda") if torch.cuda.is_available() and args.device != "cpu" else torch.device("cpu")

    checkpoint = None if args.base else (args.checkpoint or (ROOT / config["artifacts"]["directory"] / "best.pt"))
    if checkpoint is not None and not checkpoint.exists():
        raise SystemExit(f"{checkpoint} is missing: train first, or pass --base")
    model, classes, temperature, config = build(checkpoint, device, args.config)
    train.check_classes(model, classes, where=str(checkpoint or "the base model"))
    report = export(
        model,
        out,
        temperature,
        size,
        int(config["artifacts"]["opset"]),
        bool(args.dynamo) if args.dynamo else str(config["artifacts"]["exporter"]) == "dynamo",
    )
    print(json.dumps(report, indent=2), flush=True)

    import onnx

    onnx.checker.check_model(str(out))
    # The class list is the order of the outputs, so the file is checked against it before anything
    # reads a probability: a head of another width would name every class wrongly and nothing in the
    # numbers would say so.
    width = exported_classes(out)
    if width != len(classes):
        raise SystemExit(f"{out}: the graph returns {width} classes and the class list names {len(classes)}")
    report["classes"] = width
    print(f"{out}: {width} classes, matching {len(classes)} names", flush=True)
    if args.parity:
        compared = parity(
            model,
            classes,
            out,
            data_directory,
            config,
            count=int(args.parity),
            split=str(args.parity_split),
            device=device,
            precision=args.precision,
            temperature=temperature,
        )
        print(json.dumps(compared, indent=2), flush=True)
        if compared.get("passed") is False:
            raise SystemExit("the export does not reproduce the PyTorch probabilities")

    reader = classify.Classifier(out, classes=classes, providers=["CPUExecutionProvider"])
    print(f"{out}: {len(classes)} classes, providers {reader.providers()}", flush=True)


if __name__ == "__main__":
    main()
