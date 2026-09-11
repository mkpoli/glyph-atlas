"""Export a trained detector to ONNX and check it against the PyTorch model.

The export takes the checkpoint `train.py` writes, rebuilds the model from the configuration stored
in it, and writes one ONNX file with a fixed input, `pixel_values` of shape (1, 3, 1024, 1024) in
float32, and two outputs, `logits` (1, queries, classes) and `pred_boxes` (1, queries, 4) in centre
form normalised to the tile. That is the layout `kuzushiji_atlas.detect.Detector` decodes, and a
tile is always 1024 square, so the batch and the size are fixed rather than dynamic.

    python models/detector/export_onnx.py
    python models/detector/export_onnx.py --checkpoint models/detector/artifacts/best.pt --parity 10

`--parity` runs the same tiles through PyTorch and through the exported file and reports the largest
difference: the card asks for the same boxes within 1 px on ten pages. It reads the tiles of the
`val` split, so it is skipped with a message while T20's data is not there.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
import train

from kuzushiji_atlas import detect

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_NAMES = ("logits", "pred_boxes")
MATCH_IOU = 0.99
"""IoU at which a PyTorch detection and an ONNX detection count as the same box."""

SLACK = 2
"""Detections either side may have and the other not, whatever the sample size."""


class Wrapper(torch.nn.Module):
    """The model as a function of one tensor, so the exporter sees plain outputs."""

    def __init__(self, model: Any) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        outputs = self.model(pixel_values=pixel_values)
        return outputs.logits, outputs.pred_boxes


def build(checkpoint: Path | None, device: torch.device, config_path: Path) -> tuple[Any, dict]:
    """The model a checkpoint holds, rebuilt from the configuration saved with it.

    Without a checkpoint the base model is built as it comes from Hugging Face, which is a trained
    detector and so a subject for the parity check before this card's training has run.
    """
    from transformers import RTDetrForObjectDetection

    if checkpoint is None:
        config = train.load_config(config_path)
        model = RTDetrForObjectDetection.from_pretrained(
            config["model"]["checkpoint"], revision=config["model"]["revision"]
        )
    else:
        state = torch.load(checkpoint, map_location=device, weights_only=False)
        config = state.get("config") or train.load_config(config_path)
        model = train.build_model(config)
        model.load_state_dict(state["model"])
    model.eval()
    return model.to(device), config


def export(model: Any, path: Path, size: int, opset: int, dynamo: bool) -> dict:
    """Write the ONNX file and report how it was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapper = Wrapper(model).eval()
    argument = torch.zeros(1, 3, size, size, dtype=torch.float32, device=model.device)
    torch.onnx.export(
        wrapper,
        (argument,),
        str(path),
        input_names=["pixel_values"],
        output_names=list(OUTPUT_NAMES),
        opset_version=opset,
        dynamo=dynamo,
        do_constant_folding=True,
    )
    return {"dynamo": dynamo, "opset": opset, "size": size, "bytes": path.stat().st_size}


def name_outputs(path: Path) -> list[str]:
    """Force the two output names the detector decodes, and check the file."""
    import onnx

    model = onnx.load(str(path))
    current = [value.name for value in model.graph.output]
    if current == list(OUTPUT_NAMES):
        onnx.checker.check_model(model)
        return current
    if len(current) != len(OUTPUT_NAMES):
        raise SystemExit(f"the export wrote {len(current)} outputs, expected two")
    mapping = dict(zip(current, OUTPUT_NAMES, strict=True))
    for node in model.graph.node:
        for index, name in enumerate(node.output):
            if name in mapping:
                node.output[index] = mapping[name]
    for entry in list(model.graph.value_info) + list(model.graph.output):
        if entry.name in mapping:
            entry.name = mapping[entry.name]
    onnx.checker.check_model(model)
    onnx.save(model, str(path))
    return [value.name for value in model.graph.output]


def parity(
    model: Any,
    onnx_path: Path,
    tiles: list[train.Tile],
    *,
    limit: int,
    score: float,
    tolerance: float,
    allowance: float,
) -> dict:
    """Run `limit` tiles through both models and report how far the two disagree.

    The queries of an RT-DETR decoder have no fixed meaning, and the encoder selects its `topk`
    tokens from scores that trace to within 1e-4 but not exactly, so a near tie can send a query to
    a different reference point on either side. The comparison is therefore between the two sets of
    detections above `score`: one-to-one, best score first, a pair being the same box when their IoU
    is at least `MATCH_IOU`. The report carries how many pairs matched, how many detections only one
    side had, and the largest corner and score differences over the matched pairs. The check passes
    when no matched pair differs by more than `tolerance` pixels and the unmatched share of the
    detections is at most `allowance`. `max_corner_share` is the worst difference as a share of the
    box's greater side, which is what the pixel tolerance means for a box of a different size.
    """
    import onnxruntime as ort

    session = ort.InferenceSession(
        str(onnx_path),
        providers=[
            name
            for name in ("CUDAExecutionProvider", "CPUExecutionProvider")
            if name in ort.get_available_providers()
        ],
    )
    worst_corner, worst_score, worst_share = 0.0, 0.0, 0.0
    matched = beyond = only_torch = only_onnx = 0
    for tile in tiles[:limit]:
        pixels = train.tile_tensor(tile, train.tile_image(tile), augment=False)
        with torch.no_grad():
            torch_out = model(pixel_values=pixels[None].to(model.device))
        torch_boxes, torch_scores = train.decode(torch_out, [tile.size])[0]
        names = [value.name for value in session.get_outputs()]
        outputs = dict(
            zip(
                names,
                session.run(None, {"pixel_values": np.asarray(pixels[None], dtype=np.float32)}),
                strict=True,
            )
        )
        logits = np.asarray(outputs["logits"], dtype=np.float32)[0]
        onnx_boxes = detect.cxcywh_to_xyxy(np.asarray(outputs["pred_boxes"], dtype=np.float32)[0], tile.size)
        onnx_scores = (1.0 / (1.0 + np.exp(-logits))).max(axis=-1)
        keep_torch = np.flatnonzero(torch_scores >= score)
        keep_onnx = np.flatnonzero(onnx_scores >= score)
        taken = np.zeros(len(keep_onnx), dtype=bool)
        order = keep_torch[np.argsort(-torch_scores[keep_torch], kind="stable")]
        for index in order:
            best = -1
            if len(keep_onnx):
                overlaps = detect.iou(torch_boxes[index], onnx_boxes[keep_onnx])
                overlaps[taken] = 0.0
                best = int(np.argmax(overlaps))
            if best >= 0 and overlaps[best] >= MATCH_IOU:
                taken[best] = True
                matched += 1
                pair = torch_boxes[index] - onnx_boxes[keep_onnx][best]
                corner = float(np.max(np.abs(pair)))
                extent = float(
                    np.max(
                        [
                            torch_boxes[index][2] - torch_boxes[index][0],
                            torch_boxes[index][3] - torch_boxes[index][1],
                            1.0,
                        ]
                    )
                )
                worst_corner = max(worst_corner, corner)
                worst_share = max(worst_share, corner / extent)
                worst_score = max(
                    worst_score, abs(float(torch_scores[index]) - float(onnx_scores[keep_onnx][best]))
                )
                beyond += corner > tolerance
            else:
                only_torch += 1
        only_onnx += int((~taken).sum())
    total = matched + only_torch
    share = (only_torch + only_onnx) / total if total else 0.0
    allowed = max(SLACK, allowance * total)
    return {
        "tiles": min(limit, len(tiles)),
        "matched": matched,
        "beyond_tolerance": beyond,
        "only_torch": only_torch,
        "only_onnx": only_onnx,
        "unmatched_share": share,
        "max_corner_px": worst_corner,
        "max_corner_share": worst_share,
        "max_score_difference": worst_score,
        "tolerance_px": tolerance,
        "allowance": allowance,
        "allowed_unmatched": allowed,
        "passed": beyond == 0 and (only_torch + only_onnx) <= allowed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument(
        "--base",
        action="store_true",
        help="export the base checkpoint as it comes, without a trained state dictionary",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument("--dynamo", action="store_true", help="use the dynamo exporter")
    parser.add_argument("--parity", type=int, default=10, help="tiles to compare, 0 to skip")
    parser.add_argument(
        "--parity-score",
        type=float,
        default=0.05,
        help="score floor of the parity comparison; the base checkpoint scores low on these tiles",
    )
    parser.add_argument(
        "--allowance",
        type=float,
        default=0.02,
        help="share of detections one side may have and the other not, from query tie-breaking",
    )
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    config = train.load_config(args.config)
    checkpoint = args.checkpoint or ROOT / config["artifacts"]["directory"] / "best.pt"
    if not args.base and not checkpoint.exists():
        raise SystemExit(f"{checkpoint} is missing: train the detector first, or pass --base")
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))
    model, saved = build(None if args.base else checkpoint, device, args.config)
    size = int(saved["preprocessing"]["tile"])
    out = args.out or ROOT / saved["artifacts"]["export"]
    written = export(model, out, size, args.opset, args.dynamo)
    written["outputs"] = name_outputs(out)
    print(json.dumps(written, indent=2), flush=True)

    if args.parity:
        data = Path(saved["data"]["directory"])
        data = (args.data or (data if data.is_absolute() else ROOT / data)).resolve()
        split = data / saved["data"]["splits"]["val"]
        if not split.exists():
            print(f"{split} is missing: skipping the parity check", flush=True)
        else:
            tiles = train.read_split(
                split,
                cache=ROOT / saved["data"]["image_cache"],
                splits=train.read_splits(ROOT / saved["data"]["split_table"]),
                materialised=data / "tiles",
            )
            result = parity(
                model,
                out,
                tiles,
                limit=args.parity,
                score=args.parity_score,
                tolerance=1.0,
                allowance=args.allowance,
            )
            print(json.dumps(result, indent=2), flush=True)
            if not result["passed"]:
                raise SystemExit(
                    "the exported model disagrees with PyTorch: "
                    f"{result['max_corner_px']:.3f} px on matched boxes, "
                    f"{result['unmatched_share']:.4f} of the detections on one side only"
                )
    print(f"-> {out}", flush=True)


if __name__ == "__main__":
    main()
