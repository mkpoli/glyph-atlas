"""Train the box refiner on CODH's boxes and export it to ONNX.

    python models/refiner/train.py --steps 4000
    python models/refiner/train.py --evaluate models/refiner/artifacts/refiner.pt

A sample is one ground-truth box moved off its character the ways a detector moves it: every edge
jittered a little, and now and then one edge pushed into the next character, pulled back to cut the
character short, or the box taken around two characters. The network sees the moved box with its
surroundings (`glyph_atlas.refine.window`) and learns how far each edge is from the character the
box overlaps most, and the IoU of the two. A box that overlaps no character by 0.3 teaches the IoU
alone.

Pages come from `work/cut-bench` (`models/benchmark/cuts.py build` for the held-out books,
`train-pages.jsonl` for the training books). The split is by book, as everywhere.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import torch

from glyph_atlas import refine

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "work/cut-bench"
OUT = ROOT / "models/refiner/artifacts"
PER_PAGE = 48


def pages_of(name: str) -> list[dict]:
    rows = [json.loads(row) for row in (BENCH / name).read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if len(row["units"]) >= 4]


def overlaps(box, truth: np.ndarray) -> np.ndarray:
    """IoU of one `(x, y, w, h)` box with every row of `truth`."""
    left = np.maximum(box[0], truth[:, 0])
    top = np.maximum(box[1], truth[:, 1])
    right = np.minimum(box[0] + box[2], truth[:, 0] + truth[:, 2])
    bottom = np.minimum(box[1] + box[3], truth[:, 1] + truth[:, 3])
    shared = np.clip(right - left, 0, None) * np.clip(bottom - top, 0, None)
    return shared / (box[2] * box[3] + truth[:, 2] * truth[:, 3] - shared)


def disturbed(truth: np.ndarray, index: int, rng: random.Random) -> tuple[float, float, float, float]:
    """One ground-truth box moved the way a detector's box is off."""
    x, y, w, h = truth[index]
    left, top, right, bottom = x, y, x + w, y + h
    roll = rng.random()
    if roll < 0.08 and index + 1 < len(truth):
        other = truth[index + 1]
        if abs((other[0] + other[2] / 2) - (x + w / 2)) < w:  # the next character of the same column
            left, top = min(left, other[0]), min(top, other[1])
            right, bottom = max(right, other[0] + other[2]), max(bottom, other[1] + other[3])
    elif roll < 0.30:
        amount = rng.uniform(0.15, 0.9) * h
        if rng.random() < 0.5:
            top -= amount
        else:
            bottom += amount
    elif roll < 0.48:
        amount = rng.uniform(0.15, 0.55) * h
        if rng.random() < 0.5:
            top += amount
        else:
            bottom -= amount
    elif roll < 0.58:
        amount = rng.uniform(0.15, 0.8) * w
        if rng.random() < 0.5:
            left -= amount
        else:
            right += amount
    elif roll < 0.68:
        amount = rng.uniform(0.15, 0.45) * w
        if rng.random() < 0.5:
            left += amount
        else:
            right -= amount
    elif roll < 0.73:
        left += rng.uniform(-1.5, 1.5) * w
        top += rng.uniform(-1.5, 1.5) * h
        right, bottom = left + w * rng.uniform(0.6, 1.6), top + h * rng.uniform(0.6, 1.6)
    spread = 0.06
    left += rng.gauss(0, spread) * w
    right += rng.gauss(0, spread) * w
    top += rng.gauss(0, spread) * h
    bottom += rng.gauss(0, spread) * h
    if right - left < 4 or bottom - top < 4:
        return (x, y, w, h)
    return (left, top, right - left, bottom - top)


def samples_of(page: dict, rng: random.Random, count: int, *, augment: bool):
    """`count` samples of one page: windows, edge targets, whether the edges count, and the IoU."""
    from PIL import Image, ImageFilter

    with Image.open(BENCH / page["image"]) as handle:
        image = handle.convert("L")
        if augment and rng.random() < 0.3:
            image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.6)))
        grey = np.asarray(image)
    if augment:
        gain, shift = rng.uniform(0.75, 1.2), rng.uniform(-25, 25)
        grey = np.clip(grey.astype(np.float32) * gain + shift, 0, 255).astype(np.uint8)
    paper = int(np.median(grey[::16, ::16]))
    truth = np.array([[u["box"]["x"], u["box"]["y"], u["box"]["w"], u["box"]["h"]] for u in page["units"]],
                     dtype=np.float64)
    windows, targets, counted, ious = [], [], [], []
    for index in (rng.randrange(len(truth)) for _ in range(count)):
        box = disturbed(truth, index, rng)
        found = overlaps(box, truth)
        best = int(np.argmax(found))
        goal = truth[best]
        windows.append(refine.window(grey, box, paper))
        targets.append([(goal[0] - box[0]) / box[2], (goal[1] - box[1]) / box[3],
                        (goal[0] + goal[2] - box[0] - box[2]) / box[2],
                        (goal[1] + goal[3] - box[1] - box[3]) / box[3]])
        counted.append(found[best] >= 0.3)
        ious.append(found[best])
    return (np.stack(windows), np.clip(np.array(targets, dtype=np.float32), -refine.REACH, refine.REACH),
            np.array(counted, dtype=np.float32), np.array(ious, dtype=np.float32))


class Pages(torch.utils.data.IterableDataset):
    def __init__(self, pages: list[dict], *, seed: int, augment: bool, endless: bool):
        self.pages, self.seed, self.augment, self.endless = pages, seed, augment, endless

    def __iter__(self):
        info = torch.utils.data.get_worker_info()
        worker = info.id if info is not None else 0
        workers = info.num_workers if info is not None else 1
        rng = random.Random(self.seed * 1000 + worker)
        own = self.pages[worker::workers]
        while True:
            order = list(own)
            if self.endless:
                rng.shuffle(order)
            for page in order:
                windows, targets, counted, ious = samples_of(page, rng, PER_PAGE, augment=self.augment)
                yield (torch.from_numpy(refine.pixels(windows)), torch.from_numpy(targets),
                       torch.from_numpy(counted), torch.from_numpy(ious))
            if not self.endless:
                return


def joined(rows):
    """A batch from several pages' samples."""
    return tuple(torch.cat(part) for part in zip(*rows, strict=True))


def network():
    import timm

    return timm.create_model("resnet18", pretrained=False, in_chans=1, num_classes=5)


class Exported(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, pixels):
        out = self.model(pixels)
        return out[:, :4], torch.sigmoid(out[:, 4])


def loss_of(out, targets, counted, ious):
    edges = torch.nn.functional.smooth_l1_loss(out[:, :4], targets, beta=0.05, reduction="none").sum(dim=1)
    edges = (edges * counted).sum() / counted.sum().clamp(min=1)
    quality = torch.nn.functional.binary_cross_entropy_with_logits(out[:, 4], ious)
    return 4 * edges + quality


def evaluate(model, device, *, pages: int = 120) -> dict:
    """Held-out books: the IoU of disturbed boxes before and after one and two rounds of refinement."""
    chosen = [p for p in pages_of("pages.jsonl") if p["split"] == "val"]
    random.Random(0).shuffle(chosen)
    rng = random.Random(0)
    model.eval()
    before, once, twice, expected = [], [], [], []
    from PIL import Image

    for page in chosen[:pages]:
        with Image.open(BENCH / page["image"]) as handle:
            grey = np.asarray(handle.convert("L"))
        paper = int(np.median(grey[::16, ::16]))
        truth = np.array([[u["box"]["x"], u["box"]["y"], u["box"]["w"], u["box"]["h"]] for u in page["units"]],
                         dtype=np.float64)
        boxes = [disturbed(truth, rng.randrange(len(truth)), rng) for _ in range(PER_PAGE)]
        goals = [int(np.argmax(overlaps(box, truth))) for box in boxes]
        before += [float(overlaps(box, truth)[goal]) for box, goal in zip(boxes, goals, strict=True)]
        current = boxes
        for rounds in (1, 2, 3):
            batch = torch.from_numpy(refine.pixels(np.stack([refine.window(grey, box, paper) for box in current])))
            with torch.no_grad():
                out = model(batch.to(device)).float().cpu().numpy()
            if rounds == 3:
                expected += [float(1 / (1 + math.exp(-value))) for value in out[:, 4]]
                break
            current = [refine.moved(box, delta) for box, delta in zip(current, out[:, :4], strict=True)]
            scores = [float(overlaps(box, truth)[goal]) for box, goal in zip(current, goals, strict=True)]
            (once if rounds == 1 else twice).extend(scores)
    report = {"boxes": len(before)}
    for name, values in (("given", before), ("one_round", once), ("two_rounds", twice)):
        values = np.array(values)
        report[name] = {"mean_iou": round(float(values.mean()), 4), "iou_0.7": round(float((values >= 0.7).mean()), 4),
                        "iou_0.85": round(float((values >= 0.85).mean()), 4)}
    report["expected_iou_error"] = round(float(np.abs(np.array(expected) - np.array(twice)).mean()), 4)
    return report


def export(model, path: Path):
    wrapped = Exported(model).eval().cpu().float()
    torch.onnx.export(wrapped, torch.zeros(2, 1, refine.SIZE, refine.SIZE), str(path), input_names=["pixels"],
                      output_names=["deltas", "quality"], opset_version=17, dynamo=False,
                      dynamic_axes={"pixels": {0: "batch"}, "deltas": {0: "batch"}, "quality": {0: "batch"}})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=4000)
    parser.add_argument("--batch-pages", type=int, default=6, help=f"pages a batch, {PER_PAGE} boxes each")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--evaluate", type=Path)
    args = parser.parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = network().to(device)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.evaluate:
        model.load_state_dict(torch.load(args.evaluate, map_location=device))
        print(json.dumps(evaluate(model, device), indent=1))
        return
    loader = torch.utils.data.DataLoader(
        Pages(pages_of("train-pages.jsonl"), seed=0, augment=True, endless=True), batch_size=args.batch_pages,
        num_workers=args.workers, collate_fn=joined)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=args.lr, total_steps=args.steps, pct_start=0.05)
    started = time.time()
    running = 0.0
    model.train()
    for step, (pixels, targets, counted, ious) in enumerate(loader, start=1):
        with torch.autocast(device, dtype=torch.bfloat16, enabled=device == "cuda"):
            out = model(pixels.to(device, non_blocking=True))
        loss = loss_of(out.float(), targets.to(device), counted.to(device), ious.to(device))
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        schedule.step()
        running += float(loss)
        if step % 200 == 0:
            print(f"step {step}/{args.steps} loss {running / 200:.4f} {time.time() - started:.0f} s", flush=True)
            running = 0.0
        if step >= args.steps:
            break
    torch.save(model.state_dict(), OUT / "refiner.pt")
    report = evaluate(model, device)
    report["steps"] = args.steps
    report["boxes_seen"] = args.steps * args.batch_pages * PER_PAGE
    (OUT / "metrics.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))
    export(model, OUT / "refiner.onnx")


if __name__ == "__main__":
    main()
