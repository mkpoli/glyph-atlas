"""Bounded CUDA penultimate embeddings for a finalized visual-family manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from glyph_atlas.classify import MEAN, STD, crop_array, load_checkpoint


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def image_paths(root, rows):
    root = Path(root).resolve()
    result = []
    for index, row in enumerate(rows):
        if row.get("row_index") != index:
            raise ValueError("sample rows must be contiguous and ordered")
        path = (root / row["image_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("sample image is missing or outside the sample directory")
        if digest(path) != row["crop_sha256"]:
            raise ValueError("sample image changed since preparation")
        result.append(path)
    return result


def embed(root, checkpoint, batch_size=32):
    import timm
    import torch

    root = Path(root)
    manifest = root / "samples.jsonl"
    manifest_hash = digest(manifest)
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    if not 1 <= len(rows) <= 3000 or not 1 <= batch_size <= 32:
        raise ValueError("bounded embedding run requires 1..3000 samples and batch size 1..32")
    paths = image_paths(root, rows)
    if not torch.cuda.is_available():
        raise RuntimeError("this bounded batch requires CUDA; no implicit CPU fallback")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    free, total = torch.cuda.mem_get_info()
    if free < 3 * 1024**3:
        raise RuntimeError("less than 3 GiB of free CUDA memory; batch deferred")
    torch.cuda.set_per_process_memory_fraction(min(.95, 2.5 * 1024**3 / total))
    torch.cuda.reset_peak_memory_stats()
    checkpoint_hash = digest(checkpoint)
    trained = load_checkpoint(checkpoint)
    model, size = trained.model, trained.size
    model = model.eval().cuda()
    started = time.monotonic()
    vectors = []
    with torch.inference_mode():
        for start in range(0, len(paths), batch_size):
            images = []
            for path in paths[start:start + batch_size]:
                with Image.open(path) as source:
                    images.append(crop_array(source, size=size))
            batch = torch.from_numpy(np.concatenate(images)).cuda()
            # Penultimate representations preserve shape information that class logits discard.
            features = model.forward_head(model.forward_features(batch), pre_logits=True).float()
            norms = features.norm(p=2, dim=1, keepdim=True)
            if not torch.isfinite(features).all() or not torch.isfinite(norms).all() or (norms <= 0).any():
                raise RuntimeError("model returned invalid penultimate features")
            vectors.append((features / norms).cpu().numpy())
            if start == 0 or start // batch_size % 10 == 0:
                print(json.dumps({"embedded": min(start + batch_size, len(paths)), "total": len(paths)}), flush=True)
    result = np.concatenate(vectors).astype(np.float32)
    if digest(manifest) != manifest_hash:
        raise RuntimeError("sample manifest changed during embedding; output withheld")
    temporary = root / "embeddings.pending.npy"
    with temporary.open("wb") as handle:
        np.save(handle, result, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(root / "embeddings.npy")
    report = {
        "state": "ready", "samples": len(rows), "dimensions": int(result.shape[1]),
        "dtype": "float32", "normalized": "L2", "manifest_sha256": manifest_hash,
        "embeddings_sha256": digest(root / "embeddings.npy"), "checkpoint_sha256": checkpoint_hash,
        "architecture": trained.backbone,
        "representation": "forward_head(forward_features(image), pre_logits=True)",
        "preprocessing": {"size": size, "mean": MEAN, "std": STD,
                          "grey": True, "square_padding": "white", "resize": "bilinear"},
        "device": torch.cuda.get_device_name(), "torch": torch.__version__, "timm": timm.__version__,
        "batch_size": batch_size, "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
        "seconds": round(time.monotonic() - started, 3),
        "interpretation": "visual features only; upstream labels and visual groups are not verified identities",
    }
    temporary = root / "embeddings-metadata.pending.json"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(root / "embeddings-metadata.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("work/visual-families"))
    parser.add_argument("--checkpoint", type=Path, default=Path("models/classifier/artifacts/best.pt"))
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(embed(args.root, args.checkpoint, args.batch_size), ensure_ascii=False, indent=2))
