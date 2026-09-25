"""Train the script-style teacher on Calli-Tongji and measure it on calligraphers it never saw.

The data is the open subset of Calli-Tongji (`data/sources/calli-tongji.yaml`): 5,000 binarised
characters in 50 folders named 作者-书体. The classes are the five scripts it labels, mapped onto
`data/vocab/style.yaml` by `glyph_atlas.style_teacher.STYLES`; the calligrapher is only used to
split. A split by image, or by calligrapher and script, would put one hand in two splits (欧阳询
writes both 楷 and 行), and the test would then measure how well the model knows a calligrapher
rather than a script. So every calligrapher goes to one split whole: taking the scripts with the
fewest calligraphers first and the calligraphers in `sha1(name)` order, `test` receives a fifth of
each script's calligraphers (at least one), `val` one more, and the rest go to `train`.

The backbone is ConvNeXt-tiny from `timm`, started from `convnext_tiny.fb_in22k_ft_in1k`
(Apache-2.0), with a five-way head. Training draws every script equally often, augments with a
small rotation, scaling and shift and with stroke thinning or thickening (the scans this teacher will be used on vary
in ink weight far more than the binarised training images), and keeps the checkpoint with the best
macro F1 on `val`. The test report gives accuracy, macro F1, recall per script, the confusion
matrix, and each held-out calligrapher's accuracy and predictions.

The images and the model derive from a CC BY-NC 4.0 dataset. The checkpoint stays under
`models/style/artifacts/`, which is not committed, and nothing trained here enters a CC BY-SA
release.

    python models/style/train.py --zip cache/calli-tongji/Calli-Tongji.zip
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from glyph_atlas.style_teacher import CLASSES, STYLES, prepare, tensor

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = "convnext_tiny.fb_in22k_ft_in1k"


def records(archive: Path) -> list[dict]:
    """Every image of the archive with its calligrapher, script and style, from `dataset.txt`."""
    with zipfile.ZipFile(archive) as z:
        table = z.read("Calli-Tongji/dataset.txt").decode("utf-8").splitlines()
    rows = []
    for line in table[1:]:
        if not line.strip():
            continue
        number, path, label, code, dynasty = line.split()
        writer, script = label.rsplit("-", 1)
        rows.append({"id": int(number), "member": "Calli-Tongji/" + path.replace("\\", "/"), "writer": writer,
                     "script": script, "style": STYLES[script], "code": code, "dynasty": dynasty})
    return rows


def split(rows: list[dict]) -> dict[str, str]:
    """Each calligrapher to `train`, `val` or `test`, with every script in each of the three."""
    writers = defaultdict(set)
    for row in rows:
        writers[row["style"]].add(row["writer"])
    order = {style: sorted(names, key=lambda name: hashlib.sha1(name.encode()).hexdigest())
             for style, names in writers.items()}
    assigned: dict[str, str] = {}
    for part in ("test", "val"):
        for style in sorted(order, key=lambda style: (len(order[style]), style)):
            wanted = max(1, round(len(order[style]) / 5)) if part == "test" else 1
            held = sum(assigned.get(name) == part for name in order[style])
            for name in order[style]:
                if held >= wanted:
                    break
                if name not in assigned:
                    assigned[name] = part
                    held += 1
    for names in order.values():
        for name in names:
            assigned.setdefault(name, "train")
    for style, names in order.items():
        missing = {"train", "val", "test"} - {assigned[name] for name in names}
        if missing:
            raise ValueError(f"{style} has no calligrapher left for {', '.join(sorted(missing))}")
    return assigned


def thicken(image: Image.Image, rng: random.Random) -> Image.Image:
    """Thin or thicken the strokes by one or two pixels, or leave them."""
    step = rng.choice([-2, -1, 0, 0, 1, 2])
    if step > 0:
        return image.filter(ImageFilter.MinFilter(2 * step + 1))
    if step < 0:
        return image.filter(ImageFilter.MaxFilter(-2 * step + 1))
    return image


def augment(image: Image.Image, rng: random.Random) -> Image.Image:
    """Stroke weight, then one affine map: a small rotation, a scaling about the centre and a shift."""
    image = thicken(image, rng)
    angle, scale = math.radians(rng.uniform(-6, 6)), rng.uniform(0.85, 1.1)
    dx, dy = (rng.uniform(-0.06, 0.06) * side for side in image.size)
    cx, cy = image.width / 2, image.height / 2
    # PIL maps each output pixel back to the input, so the matrix is the inverse of the transform.
    a, b = math.cos(angle) / scale, math.sin(angle) / scale
    c = cx - a * (cx + dx) - b * (cy + dy)
    d, e = -math.sin(angle) / scale, math.cos(angle) / scale
    f = cy - d * (cx + dx) - e * (cy + dy)
    return image.transform(image.size, Image.Transform.AFFINE, (a, b, c, d, e, f),
                           resample=Image.Resampling.BILINEAR, fillcolor=255)


def load(archive: Path, rows: list[dict]) -> dict[int, Image.Image]:
    with zipfile.ZipFile(archive) as z:
        return {row["id"]: prepare(Image.open(io.BytesIO(z.read(row["member"])))) for row in rows}


def batches(rows, images, *, size, rng, train):
    order = rows[:]
    if train:
        counts = Counter(row["style"] for row in rows)
        weights = [1 / counts[row["style"]] for row in rows]
        order = rng.choices(rows, weights=weights, k=len(rows))
    for start in range(0, len(order), size):
        chunk = order[start:start + size]
        arrays = [tensor(augment(images[row["id"]], rng) if train else images[row["id"]]) for row in chunk]
        yield np.stack(arrays), np.array([CLASSES.index(row["style"]) for row in chunk])


def scores(truth: list[int], guess: list[int]) -> dict:
    confusion = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for t, g in zip(truth, guess, strict=True):
        confusion[t, g] += 1
    recall, f1 = {}, []
    for index, name in enumerate(CLASSES):
        tp, support, predicted = confusion[index, index], confusion[index].sum(), confusion[:, index].sum()
        r = tp / support if support else math.nan
        p = tp / predicted if predicted else 0.0
        recall[name] = round(float(r), 4)
        f1.append(0.0 if not tp else 2 * p * r / (p + r))
    return {"accuracy": round(float(np.trace(confusion) / confusion.sum()), 4),
            "macro_f1": round(float(np.mean(f1)), 4), "recall": recall,
            "confusion": {"rows_truth_columns_prediction": CLASSES, "matrix": confusion.tolist()}}


def predict(model, rows, images, device, size=128):
    import torch
    guesses = []
    model.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        for x, _ in batches(rows, images, size=size, rng=random.Random(0), train=False):
            guesses.extend(model(torch.as_tensor(x, device=device)).float().argmax(1).tolist())
    return guesses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path, default=ROOT / "cache/calli-tongji/Calli-Tongji.zip")
    parser.add_argument("--out", type=Path, default=ROOT / "models/style/artifacts")
    parser.add_argument("--metrics", type=Path, default=ROOT / "models/style/metrics.json")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    import timm
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required")
    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    rows = records(args.zip)
    parts = split(rows)
    for row in rows:
        row["split"] = parts[row["writer"]]
    images = load(args.zip, rows)
    by = {name: [row for row in rows if row["split"] == name] for name in ("train", "val", "test")}
    device = "cuda"
    model = timm.create_model(CHECKPOINT, pretrained=True, num_classes=len(CLASSES)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.05)
    steps = args.epochs * math.ceil(len(by["train"]) / args.batch)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=args.lr, total_steps=steps, pct_start=0.1)
    loss_of = torch.nn.CrossEntropyLoss(label_smoothing=0.1)
    args.out.mkdir(parents=True, exist_ok=True)
    best, started, history = -1.0, time.time(), []
    for epoch in range(args.epochs):
        model.train()
        losses = []
        for x, y in batches(by["train"], images, size=args.batch, rng=rng, train=True):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = loss_of(model(torch.as_tensor(x, device=device)), torch.as_tensor(y, device=device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            schedule.step()
            losses.append(loss.item())
        val = scores([CLASSES.index(r["style"]) for r in by["val"]], predict(model, by["val"], images, device))
        history.append({"epoch": epoch + 1, "mean_train_loss": round(sum(losses) / len(losses), 4), "val_macro_f1": val["macro_f1"]})
        print(json.dumps(history[-1]), flush=True)
        if val["macro_f1"] > best:
            best = val["macro_f1"]
            torch.save({"model": model.state_dict(), "classes": CLASSES, "checkpoint": CHECKPOINT,
                        "epoch": epoch + 1}, args.out / "best.pt")
    model.load_state_dict(torch.load(args.out / "best.pt", map_location=device)["model"])
    guesses = predict(model, by["test"], images, device)
    truth = [CLASSES.index(r["style"]) for r in by["test"]]
    per_writer = defaultdict(Counter)
    for row, guess in zip(by["test"], guesses, strict=True):
        per_writer[f'{row["writer"]}-{row["script"]}'][CLASSES[guess]] += 1
    archive_sha = hashlib.sha256(args.zip.read_bytes()).hexdigest()
    report = {
        "data": {"source": "calli-tongji", "archive_sha256": archive_sha,
                 "images": {name: len(part) for name, part in by.items()},
                 "calligraphers": {name: sorted({r["writer"] for r in part}) for name, part in by.items()},
                 "classes": {name: sorted({f'{r["writer"]}-{r["script"]}' for r in part}) for name, part in by.items()}},
        "model": {"checkpoint": CHECKPOINT, "size": 128, "epochs": args.epochs, "seed": args.seed,
                  "best_val_macro_f1": best, "device": torch.cuda.get_device_name(0),
                  "seconds": round(time.time() - started, 1)},
        "history": history,
        "test": scores(truth, guesses),
        "test_per_calligrapher": {
            name: {"accuracy": round(counts[STYLES[name.rsplit("-", 1)[1]]] / sum(counts.values()), 4),
                   "predicted": {style: counts[style] for style in CLASSES if counts[style]}}
            for name, counts in sorted(per_writer.items())},
    }
    args.metrics.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report["test"], ensure_ascii=False))


if __name__ == "__main__":
    main()
