"""Train the character classifier and measure it.

The data is the manifests `models/classifier/build_manifests.py` writes under `work/classifier/`:
one crop per CODH unit, cut to the unit's own box, with the code point it is and the split of its
book. The classes are the code points with at least `min_train_crops` crops in `train`, plus `other`
for everything below that line. The backbone is ConvNeXt-tiny from `timm`, started from
`convnext_tiny.fb_in22k_ft_in1k` (ImageNet-22k, fine-tuned on ImageNet-1k, Apache-2.0), with its
1000-class head replaced by one linear layer over the class list.

One crop is 96x96 grey, the aspect ratio kept by padding to a square, normalised with the grey
equivalent of the ImageNet statistics; `glyph_atlas.classify` holds that preprocessing and the
training loader calls it, so a run and the served export cannot drift apart.

Training draws every class as often as every other (`WeightedRandomSampler`, weight
`1 / crops of the class in train`), because the corpus is Zipf: the commonest kana holds 22,000
crops and a class at the cut-off holds 20. The loss is cross entropy with label smoothing, the
precision is bfloat16 autocast, the schedule is cosine with warmup, and the checkpoint kept is the
one with the best top-1 on `val`.

Calibration is temperature scaling, fitted on the `val` crops as they come, which is the served
frequency and not the balanced one the sampler draws. The expected calibration error over 15 bins is
reported before and after. The temperature is stored in the checkpoint and baked into the export by
`export_onnx.py`, so `classify.Classifier` serves calibrated probabilities without a second file.

    python models/classifier/train.py --config models/classifier/config.yaml
    python models/classifier/train.py --config models/classifier/config.yaml --profile
    python models/classifier/train.py --config models/classifier/config.yaml --test

`--test` measures the checkpoint on `test`: top-1 and top-5 over the crops whose class is in
`classes.json`, the accuracy over all test crops beside them, macro F1, the calibration error, and
the same numbers by production type. The checkpoint is fetched from Hugging Face on the first run;
`HF_HOME=cache/huggingface` keeps it under `cache/`, as the conventions ask.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from build_manifests import Crop
from PIL import Image

from glyph_atlas import classify, tables

ROOT = Path(__file__).resolve().parents[2]

#: The class the sampler and the metrics treat as an abstention rather than an identification.
OTHER = "other"


# --- data ------------------------------------------------------------------------------------


@dataclass
class Data:
    """The crops of one split, as compact arrays, and the class list they are indexed by.

    A DataLoader worker forks the process and then reads the whole split, one crop at a time, so the
    split is not a list of Python objects: every read of a small object would touch its reference
    count, and the page that holds it would be copied into every worker. A training split of 917,309
    crops would then cost gigabytes per worker. The paths live in one bytes blob with an offset per
    crop and the labels in one array; a worker reads both without writing to them.
    """

    classes: list[str] = field(default_factory=list)
    other: int = -1
    paths: bytes = b""
    offsets: np.ndarray = field(default_factory=lambda: np.zeros(1, dtype=np.int64))
    labels: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    code_points: list[str] = field(default_factory=list)
    productions: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return int(self.labels.shape[0])

    def path(self, index: int) -> str:
        """The crop file of one row, decoded from the blob."""
        return self.paths[self.offsets[index] : self.offsets[index + 1]].decode()

    def identified(self) -> np.ndarray:
        """Which crops are an identification: their class is not `other`."""
        return self.labels != self.other

    def counts(self) -> Counter:
        """How many crops each class holds, by class position."""
        return Counter(self.labels.tolist())

    def head(self, limit: int) -> Data:
        """The first `limit` crops, for a run that trains on a sample of the split."""
        if limit >= len(self):
            return self
        return self.take(np.arange(limit, dtype=np.int64))

    def take(self, indices: np.ndarray) -> Data:
        """The crops at `indices`, in that order, as another split."""
        chosen = np.asarray(indices, dtype=np.int64)
        pieces = [self.paths[self.offsets[index] : self.offsets[index + 1]] for index in chosen]
        offsets = np.zeros(len(pieces) + 1, dtype=np.int64)
        np.cumsum([len(piece) for piece in pieces], out=offsets[1:])
        return Data(
            classes=self.classes,
            other=self.other,
            paths=b"".join(pieces),
            offsets=offsets,
            labels=self.labels[chosen],
            code_points=[self.code_points[index] for index in chosen] if self.code_points else [],
            productions=[self.productions[index] for index in chosen] if self.productions else [],
        )

    def concat(self, other: Data) -> Data:
        """This split and another as one, both indexed by the same class list."""
        offsets = np.concatenate([self.offsets, self.offsets[-1] + other.offsets[1:]]).astype(np.int64)
        return Data(
            classes=self.classes,
            other=self.other,
            paths=self.paths + other.paths,
            offsets=offsets,
            labels=np.concatenate([self.labels, other.labels]),
            code_points=self.code_points + other.code_points,
            productions=self.productions + other.productions,
        )


def load_classes(path: Path) -> list[str]:
    """The class order of `classes.json`, which the manifests and the export both use."""
    return classify.read_classes(path)


def read_manifest(path: Path, classes: list[str], *, keep: bool = True) -> Data:
    """One manifest as a split, with the label of every crop its position in the class list.

    `keep` also reads the code point and the production type of every crop, which the breakdown of
    the metrics needs; a training split is read without them. The two strings repeat over the split,
    so the rows share one object per distinct value.
    """
    index = {name: position for position, name in enumerate(classes)}
    other = index.get(OTHER, -1)
    if not path.exists():
        raise SystemExit(f"{path} is missing: build the manifests with models/classifier/build_manifests.py")
    pieces: list[bytes] = []
    lengths: list[int] = []
    labels: list[int] = []
    code_points: list[str] = []
    productions: list[str] = []
    shared: dict[str, str] = {}
    for batch in tables.scan(path, Crop):
        for record in batch:
            crop = str(ROOT / record.crop).encode("utf-8")
            pieces.append(crop)
            lengths.append(len(crop))
            labels.append(index.get(record.label, other))
            if keep:
                code_points.append(shared.setdefault(record.code_point, record.code_point))
                productions.append(shared.setdefault(record.production, record.production))
    offsets = np.zeros(len(pieces) + 1, dtype=np.int64)
    np.cumsum(lengths, out=offsets[1:])
    return Data(
        classes=list(classes),
        other=other,
        paths=b"".join(pieces),
        offsets=offsets,
        labels=np.asarray(labels, dtype=np.int32),
        code_points=code_points,
        productions=productions,
    )


def augment_grey(array: np.ndarray, rng: random.Random) -> np.ndarray:
    """Brightness and contrast jitter of a grey crop, the one augmentation a character survives.

    Scans differ in contrast and exposure and a character keeps its shape under both. A character
    mirrored or rotated is a different character, so neither is used.
    """
    mean = float(array.mean())
    array = np.clip((array - mean) * rng.uniform(0.85, 1.15) + mean, 0.0, 255.0)
    return np.clip(array * rng.uniform(0.85, 1.15), 0.0, 255.0)


class Crops(torch.utils.data.Dataset):
    """The crops of one split, preprocessed the way the export is served.

    `augment` jitters exposure for training. The draws come from each loader worker's own
    generator, which torch seeds from the run's seed, so every epoch sees new jitter. (Box jitter,
    slant and stroke-width changes were tried and lowered accuracy on every held-out set.)
    """

    def __init__(self, data: Data, *, augment: bool, size: int, seed: int = 0) -> None:
        self.data = data
        self.augment = augment
        self.size = size
        self.seed = seed

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        with Image.open(self.data.path(index)) as handle:
            image = handle.convert("L")
            if self.augment:
                image = Image.fromarray(augment_grey(np.asarray(image, dtype=np.float32), random).astype(np.uint8))
            pixels = classify.crop_array(image, size=self.size)
        return torch.from_numpy(pixels[0]), int(self.data.labels[index])


def loader_for(
    data: Data,
    *,
    augment: bool,
    batch_size: int,
    workers: int,
    size: int,
    seed: int,
    sampler: Any | None = None,
) -> Any:
    return torch.utils.data.DataLoader(
        Crops(data, augment=augment, size=size, seed=seed),
        batch_size=batch_size,
        shuffle=False,
        sampler=sampler,
        num_workers=workers,
        pin_memory=torch.cuda.is_available(),
        drop_last=False,
        persistent_workers=workers > 0,
    )


def balanced_sampler(data: Data, *, seed: int) -> Any:
    """A sampler that draws every class as often as every other, with replacement."""
    labels = data.labels
    counts = np.bincount(labels, minlength=len(data.classes)).astype(np.float64)
    weights = 1.0 / np.maximum(counts[labels], 1.0)
    generator = torch.Generator()
    generator.manual_seed(seed)
    return torch.utils.data.WeightedRandomSampler(
        torch.from_numpy(weights), num_samples=len(labels), replacement=True, generator=generator
    )


# --- metrics ---------------------------------------------------------------------------------


def softmax(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=np.float64)
    values = values - values.max(axis=-1, keepdims=True)
    exponent = np.exp(values)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def top_k_accuracy(logits: np.ndarray, labels: np.ndarray, k: int) -> float:
    if len(labels) == 0:
        return 0.0
    order = np.argsort(-np.asarray(logits, dtype=np.float64), axis=-1)[:, :k]
    return float(np.mean((order == labels[:, None]).any(axis=1)))


def confusion(logits: np.ndarray, labels: np.ndarray, n_classes: int) -> np.ndarray:
    predicted = np.argmax(np.asarray(logits, dtype=np.float64), axis=-1) if len(labels) else np.zeros(0, dtype=int)
    matrix = np.zeros((n_classes, n_classes), dtype=np.int64)
    np.add.at(matrix, (labels, predicted), 1)
    return matrix


def macro_f1(matrix: np.ndarray) -> float:
    """Macro F1 over the classes the truth holds, so a class with no test crop does not count."""
    truth = matrix.sum(axis=1)
    tp = np.diag(matrix).astype(np.float64)
    predicted = matrix.sum(axis=0).astype(np.float64)
    denominator = truth + predicted
    f1 = np.divide(2 * tp, denominator, out=np.zeros_like(tp), where=denominator > 0)
    present = truth > 0
    return float(f1[present].mean()) if present.any() else 0.0


def expected_calibration_error(
    probabilities: np.ndarray, labels: np.ndarray, *, bins: int = 15
) -> float:
    """Top-1 expected calibration error over `bins` equal-width confidence bins."""
    if len(labels) == 0:
        return 0.0
    confidence = probabilities.max(axis=-1)
    predicted = probabilities.argmax(axis=-1)
    correct = (predicted == labels).astype(np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(labels))
    error = 0.0
    for index in range(bins):
        low, high = edges[index], edges[index + 1]
        inside = (confidence > low) & (confidence <= high) if index else (confidence >= low) & (confidence <= high)
        count = float(inside.sum())
        if count == 0:
            continue
        error += count / total * abs(correct[inside].mean() - confidence[inside].mean())
    return float(error)


def negative_log_likelihood(logits: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    if len(labels) == 0:
        return 0.0
    probabilities = softmax(np.asarray(logits, dtype=np.float64) / temperature)
    picked = probabilities[np.arange(len(labels)), labels]
    return float(-np.log(np.maximum(picked, 1e-12)).mean())


def fit_temperature(logits: np.ndarray, labels: np.ndarray, *, iterations: int = 200) -> tuple[float, dict]:
    """The temperature that minimises the negative log-likelihood of `logits` on `labels`.

    Fitted on the val crops as they come, which is the frequency the classifier is served at; the
    training sampler is class-balanced, so the two distributions differ and this is the one number
    that reconciles them.
    """
    before = negative_log_likelihood(logits, labels, 1.0)
    before_ece = expected_calibration_error(softmax(logits), labels)
    if len(labels) == 0:
        return 1.0, {"nll_before": 0.0, "nll_after": 0.0, "ece_before": 0.0, "ece_after": 0.0, "crops": 0}
    values = torch.from_numpy(np.asarray(logits, dtype=np.float64))
    truth = torch.from_numpy(np.asarray(labels, dtype=np.int64))
    log_temperature = torch.zeros((), dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=iterations, line_search_fn="strong_wolfe")

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(values / torch.exp(log_temperature), truth)
        loss.backward()
        return loss

    optimizer.step(closure)
    temperature = float(torch.exp(log_temperature).item())
    after = negative_log_likelihood(logits, labels, temperature)
    after_ece = expected_calibration_error(softmax(np.asarray(logits, dtype=np.float64) / temperature), labels)
    return temperature, {
        "nll_before": before,
        "nll_after": after,
        "ece_before": before_ece,
        "ece_after": after_ece,
        "crops": len(labels),
    }


def metrics_for(
    logits: np.ndarray,
    labels: np.ndarray,
    *,
    identified: np.ndarray | None = None,
    classes: int = 0,
    top_k: int = 5,
    temperature: float = 1.0,
    bins: int = 15,
) -> dict[str, float]:
    """Every number the target asks for, over one set of crops.

    `identified` marks the crops whose class is in `classes.json`; the top-1, top-5 and macro F1 are
    reported over those, because `other` is an abstention and not an identification. The accuracy
    over all the crops is reported beside them.
    """
    labels = np.asarray(labels, dtype=np.int64)
    probabilities = softmax(np.asarray(logits, dtype=np.float64) / temperature)
    chosen = np.ones(len(labels), dtype=bool) if identified is None else np.asarray(identified, dtype=bool)
    subset = probabilities[chosen], labels[chosen]
    report = {
        "crops": len(labels),
        "identified_crops": int(chosen.sum()),
        "accuracy_all": float(np.mean(probabilities.argmax(axis=-1) == labels)) if len(labels) else 0.0,
        "top1": top_k_accuracy(subset[0], subset[1], 1),
        f"top{top_k}": top_k_accuracy(subset[0], subset[1], top_k),
        "macro_f1": macro_f1(confusion(subset[0], subset[1], classes)),
        "ece": expected_calibration_error(probabilities[chosen], labels[chosen], bins=bins),
        "ece_all": expected_calibration_error(probabilities, labels, bins=bins),
        "mean_confidence": float(probabilities.max(axis=-1).mean()) if len(labels) else 0.0,
        "temperature": float(temperature),
    }
    return report


def by_production(
    logits: np.ndarray,
    labels: np.ndarray,
    identified: np.ndarray,
    kinds: list[str],
    *,
    classes: int,
    top_k: int,
    temperature: float,
    bins: int,
) -> dict[str, dict[str, float]]:
    """The same metrics split by production type, so print and manuscript are seen apart."""
    groups: dict[str, list[int]] = {}
    for index, name in enumerate(kinds):
        groups.setdefault(name, []).append(index)
    out: dict[str, dict[str, float]] = {}
    for name, members in sorted(groups.items()):
        rows = np.asarray(members, dtype=int)
        out[name] = metrics_for(
            np.asarray(logits)[rows],
            np.asarray(labels)[rows],
            identified=np.asarray(identified)[rows],
            classes=classes,
            top_k=top_k,
            temperature=temperature,
            bins=bins,
        )
    return out


def confusion_pairs(
    logits: np.ndarray, labels: np.ndarray, classes: list[str], *, limit: int = 20
) -> list[dict[str, Any]]:
    """The pairs a class is most often taken for, for the README's failure cases."""
    matrix = confusion(logits, labels, len(classes))
    pairs: list[dict[str, Any]] = []
    for truth in range(len(classes)):
        row = matrix[truth].copy()
        row[truth] = 0
        for predicted in np.argsort(-row)[:1]:
            if row[predicted] > 0:
                pairs.append(
                    {
                        "truth": classes[truth],
                        "predicted": classes[predicted],
                        "count": int(row[predicted]),
                        "of": int(matrix[truth].sum()),
                    }
                )
    pairs.sort(key=lambda row: (-row["count"], row["truth"]))
    return pairs[:limit]


# --- training --------------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_model(config: dict[str, Any], n_classes: int, *, pretrained: bool | None = None) -> Any:
    """The backbone with a fresh head over the class list, started from the named checkpoint.

    `checkpoint` is the timm name with its pretrained tag; the bare architecture name would load
    whichever tag timm makes the default.
    """
    import timm

    model_config = config["model"]
    return timm.create_model(
        model_config["checkpoint"],
        pretrained=bool(model_config.get("pretrained", True)) if pretrained is None else pretrained,
        num_classes=n_classes,
        # e.g. a transformer's input size, which sets its position embeddings
        **model_config.get("options", {}),
    )


def head_classes(model: Any) -> int:
    """How many classes the classifier head of a timm model returns."""
    head = model.get_classifier()
    return int(head.out_features if hasattr(head, "out_features") else head.fc2.out_features)


def check_classes(model: Any, classes: list[str], *, where: str) -> None:
    """Refuse to train, measure or export when the head and the class list disagree.

    The class list is the order of the model's outputs, so a head of a different width would name
    every probability wrongly. The mismatch is silent in the numbers and visible only in the names,
    which is why it is checked where the two meet, before anything is written.
    """
    width = head_classes(model)
    if width != len(classes):
        raise SystemExit(
            f"{where}: the model head returns {width} classes and the class list names {len(classes)}"
        )


def optimizer_for(model: Any, config: dict[str, Any]) -> torch.optim.Optimizer:
    """AdamW over two groups: the pretrained backbone turned slowly, the new head quickly."""
    training = config["training"]
    backbone, head = [], []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        (head if name.startswith("head.") else backbone).append(parameter)
    groups = [
        {"params": head, "lr": float(training["lr"])},
        {"params": backbone, "lr": float(training["backbone_lr"])},
    ]
    return torch.optim.AdamW(groups, weight_decay=float(training["weight_decay"]))


def scheduler_for(optimizer: torch.optim.Optimizer, config: dict[str, Any], steps: int) -> Any:
    from transformers.optimization import get_cosine_schedule_with_warmup

    return get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(config["training"]["warmup_steps"]),
        num_training_steps=max(1, steps),
    )


def autocast_of(precision: str) -> Any:
    if precision == "bf16":
        return torch.autocast("cuda", dtype=torch.bfloat16)
    if precision == "fp16":
        return torch.autocast("cuda", dtype=torch.float16)
    return torch.autocast("cuda", enabled=False)


def run_model(model: Any, loader: Any, *, precision: str, device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    """The logits and labels of a whole split, in loader order."""
    logits: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for pixels, target in loader:
            pixels = pixels.to(device, non_blocking=True)
            with autocast_of(precision):
                output = model(pixels)
            logits.append(output.detach().float().cpu().numpy())
            labels.append(target.numpy())
    if not logits:
        return np.zeros((0, 0), dtype=np.float32), np.zeros((0,), dtype=np.int64)
    return np.concatenate(logits, axis=0), np.concatenate(labels, axis=0)


def peak_vram() -> str:
    if not torch.cuda.is_available():
        return "no cuda"
    return f"peak {torch.cuda.max_memory_allocated() / 2**30:.2f}GiB"


def snapshot(
    model: Any, optimizer: Any, scheduler: Any, config: dict[str, Any], classes: list[str], epoch: int, metrics: dict
) -> dict[str, Any]:
    return {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "epoch": epoch,
        "config": config,
        "classes": classes,
        "metrics": metrics,
    }


def measure(
    model: Any,
    data: Data,
    *,
    config: dict[str, Any],
    device: torch.device,
    precision: str,
    batch_size: int,
    workers: int,
    size: int,
    seed: int,
    temperature: float = 1.0,
    limit: int | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Run one split and report its metrics."""
    if limit is not None:
        data = data.head(limit)
    loader = loader_for(
        data, augment=False, batch_size=batch_size, workers=workers, size=size, seed=seed
    )
    logits, labels = run_model(model, loader, precision=precision, device=device)
    report = metrics_for(
        logits,
        labels,
        identified=data.identified(),
        classes=len(data.classes),
        top_k=int(config["evaluation"]["top_k"]),
        temperature=temperature,
        bins=int(config["calibration"]["bins"]),
    )
    return logits, labels, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--data", type=Path, default=None, help="override data.directory")
    parser.add_argument("--classes", type=Path, default=None, help="override data.classes")
    parser.add_argument("--out", type=Path, default=None, help="override artifacts.directory")
    parser.add_argument("--checkpoint", type=Path, default=None, help="the checkpoint to test")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--eval-batch-size", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="train crops to use")
    parser.add_argument("--val-limit", type=int, default=None)
    parser.add_argument("--test-limit", type=int, default=None)
    parser.add_argument("--sample", type=float, default=None, help="share of train drawn per epoch")
    parser.add_argument("--precision", choices=["bf16", "fp16", "fp32"], default=None)
    parser.add_argument("--device", default=None, help="cuda, cuda:1, cpu")
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--test", action="store_true", help="measure val and test and stop")
    parser.add_argument("--profile", action="store_true", help="time a few steps and stop")
    parser.add_argument("--profile-steps", type=int, default=10)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--no-pretrained", action="store_true", help="random weights, for a smoke run")
    args = parser.parse_args()

    config = load_config(args.config)
    training = config["training"]
    evaluation = config["evaluation"]
    calibration = config["calibration"]
    preprocessing = config["preprocessing"]
    size = int(preprocessing["size"])
    if abs(float(preprocessing["mean"]) - classify.MEAN) > 1e-9 or abs(
        float(preprocessing["std"]) - classify.STD
    ) > 1e-9:
        raise SystemExit(f"{args.config} normalises differently from glyph_atlas.classify")
    epochs = args.epochs if args.epochs is not None else int(training["epochs"])
    batch_size = args.batch_size if args.batch_size is not None else int(training["batch_size"])
    eval_batch_size = args.eval_batch_size if args.eval_batch_size is not None else max(batch_size, 128)
    precision = args.precision or str(training["precision"])
    workers = args.workers if args.workers is not None else int(training["num_workers"])
    seed = int(training["seed"])
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    data_directory = args.data or (ROOT / config["data"]["directory"])
    out = args.out or (ROOT / config["artifacts"]["directory"])
    out.mkdir(parents=True, exist_ok=True)
    classes = load_classes(args.classes or (ROOT / config["data"]["classes"]))
    splits = config["data"]["splits"]
    metrics_path = out / "metrics.json"

    def read(name: str, limit: int | None = None) -> Data:
        # The training split is not broken down by production, so its two per-crop strings are not read.
        data = read_manifest(data_directory / splits[name], classes, keep=name != "train")
        if limit is not None:
            data = data.head(limit)
        counted = data.counts()
        minority = min((count for label, count in counted.items() if label != data.other), default=0)
        print(
            f"{name}: {len(data)} crops, {len(counted)} classes, smallest class {minority} crops",
            flush=True,
        )
        return data

    device = torch.device(args.device or "cuda") if torch.cuda.is_available() and args.device != "cpu" else torch.device("cpu")
    model = build_model(config, len(classes), pretrained=False if args.no_pretrained else None).to(device)
    check_classes(model, classes, where=str(args.config))
    print(
        f"{config['model']['checkpoint']} on {device}, "
        f"{sum(p.numel() for p in model.parameters()) / 1e6:.1f}M parameters, "
        f"head {head_classes(model)} classes, {size}x{size} grey, {precision}",
        flush=True,
    )
    report: dict[str, Any] = {"config": str(args.config), "classes": len(classes), "epochs": [], "test": None}

    if args.test:
        if metrics_path.exists():
            report = json.loads(metrics_path.read_text(encoding="utf-8"))
            report.setdefault("epochs", [])
        checkpoint = args.checkpoint or out / "best.pt"
        if not checkpoint.exists():
            raise SystemExit(f"{checkpoint} is missing: train first, or pass --checkpoint")
        state = torch.load(checkpoint, map_location=device, weights_only=False)
        # The checkpoint's own configuration: an older one may be another backbone or size.
        config = state.get("config", config)
        size = int(config["preprocessing"]["size"])
        classes = state.get("classes", classes)
        model = build_model(config, len(classes), pretrained=False).to(device)
        model.load_state_dict(state["model"])
        check_classes(model, classes, where=str(checkpoint))
        temperature = float(state.get("temperature", state.get("metrics", {}).get("temperature", 1.0)))
        print(f"{checkpoint}: epoch {state.get('epoch')}, temperature {temperature:.4f}", flush=True)

        _, _, val = measure(
            model,
            read("val", args.val_limit),
            config=config,
            device=device,
            precision=precision,
            batch_size=eval_batch_size,
            workers=workers,
            size=size,
            seed=seed,
            temperature=temperature,
            limit=args.val_limit,
        )
        test_data = read("test", args.test_limit)
        logits, labels, test = measure(
            model,
            test_data,
            config=config,
            device=device,
            precision=precision,
            batch_size=eval_batch_size,
            workers=workers,
            size=size,
            seed=seed,
            temperature=temperature,
        )
        report["val"] = val
        report["test"] = {
            **test,
            "by_production": by_production(
                logits,
                labels,
                test_data.identified(),
                test_data.productions,
                classes=len(classes),
                top_k=int(evaluation["top_k"]),
                temperature=temperature,
                bins=int(calibration["bins"]),
            ),
            "confusions": confusion_pairs(logits, labels, classes),
        }
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["test"], indent=2), flush=True)
        print(f"-> {metrics_path}", flush=True)
        return

    train_data = read("train", args.limit)
    val_data = read("val", args.val_limit)
    report["data"] = {
        "directory": str(data_directory),
        "classes": len(classes),
        "crops": {"train": len(train_data), "val": len(val_data)},
        "labels": {
            "train": {classes[label]: int(count) for label, count in sorted(train_data.counts().items())},
            "val": {classes[label]: int(count) for label, count in sorted(val_data.counts().items())},
        },
    }
    sample = args.sample
    steps_per_epoch = math.ceil(len(train_data) * (sample if sample is not None else 1.0) / batch_size)
    sampler = balanced_sampler(train_data, seed=seed)
    if sample is not None:
        sampler.num_samples = int(len(train_data) * sample)
    train_loader = loader_for(
        train_data,
        augment=True,
        batch_size=batch_size,
        workers=workers,
        size=size,
        seed=seed,
        sampler=sampler,
    )
    optimizer = optimizer_for(model, config)
    steps = max(1, steps_per_epoch) * epochs
    scheduler = scheduler_for(optimizer, config, steps)
    scaler = torch.amp.GradScaler("cuda", enabled=precision == "fp16")
    criterion = torch.nn.CrossEntropyLoss(label_smoothing=float(training["label_smoothing"]))
    start = 0
    if args.resume is not None:
        state = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start = int(state["epoch"]) + 1
        print(f"resumed {args.resume} at epoch {start}", flush=True)

    best = -1.0
    for epoch in range(start, epochs):
        model.train()
        # a fresh jitter every epoch, so a crop drawn again is not the same image twice
        train_loader.dataset.seed = seed * 1000 + epoch
        running, seen, started = 0.0, 0, time.time()
        for step, (pixels, target) in enumerate(train_loader, start=1):
            pixels, target = pixels.to(device, non_blocking=True), target.to(device, non_blocking=True)
            with autocast_of(precision):
                loss = criterion(model(pixels), target)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(training["grad_clip"]))
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            scheduler.step()
            running += float(loss.detach())
            seen += 1
            if step % 100 == 0 or step == len(train_loader):
                print(
                    f"epoch {epoch} step {step}/{len(train_loader)} loss {running / seen:.4f} "
                    f"{(time.time() - started) / seen:.3f}s/it {peak_vram()}",
                    flush=True,
                )
            if args.profile and step >= args.profile_steps:
                break
        if args.profile:
            print(
                f"profiled {min(args.profile_steps, len(train_loader))} steps at batch {batch_size}, "
                f"precision {precision}, {(time.time() - started) / max(seen, 1):.3f}s/it, {peak_vram()}",
                flush=True,
            )
            return

        rates: dict[str, float] = {"loss": running / max(seen, 1), "seconds": time.time() - started}
        if len(val_data):
            _, _, rates_val = measure(
                model,
                val_data,
                config=config,
                device=device,
                precision=precision,
                batch_size=eval_batch_size,
                workers=workers,
                size=size,
                seed=seed,
            )
            rates.update(rates_val)
        report["epochs"].append({"epoch": epoch, **rates})
        print(
            f"epoch {epoch}: loss {rates['loss']:.4f}"
            + (
                f" val top1 {rates.get('top1', 0.0):.4f} top{evaluation['top_k']} "
                f"{rates.get(f'top{evaluation['top_k']}', 0.0):.4f} ece {rates.get('ece', 0.0):.4f} "
                f"({rates['seconds']:.0f}s)"
                if len(val_data)
                else f" ({rates['seconds']:.0f}s, no val crops)"
            ),
            flush=True,
        )
        state = snapshot(model, optimizer, scheduler, config, classes, epoch, rates)
        torch.save(state, out / "last.pt")
        # Without val crops there is nothing to choose a best epoch by, so the last one stands in.
        if rates.get("top1", 0.0) > best or not len(val_data):
            best = rates.get("top1", 0.0)
            torch.save(state, out / "best.pt")
            print(f"epoch {epoch}: best val top-1 {best:.4f} -> {out / 'best.pt'}", flush=True)
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # Calibration: the temperature is fitted on the val crops as they come, never on test.
    checkpoint = out / "best.pt" if (out / "best.pt").exists() else out / "last.pt"
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(state["model"])
    if len(val_data):
        logits, labels, _ = measure(
            model,
            val_data,
            config=config,
            device=device,
            precision=precision,
            batch_size=eval_batch_size,
            workers=workers,
            size=size,
            seed=seed,
        )
        temperature, fitting = fit_temperature(logits, labels)
        state["temperature"] = temperature
        torch.save(state, checkpoint)
        report["calibration"] = {"method": calibration["method"], "temperature": temperature, **fitting}
        _, _, val = measure(
            model,
            val_data,
            config=config,
            device=device,
            precision=precision,
            batch_size=eval_batch_size,
            workers=workers,
            size=size,
            seed=seed,
            temperature=temperature,
        )
        report["val"] = val
        print(
            f"temperature {temperature:.4f}: val NLL {fitting['nll_before']:.4f} -> {fitting['nll_after']:.4f}, "
            f"ECE {fitting['ece_before']:.4f} -> {fitting['ece_after']:.4f}",
            flush=True,
        )
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    else:
        print("no val crops: the temperature stays 1 and the calibration error is not measured", flush=True)
    print(f"-> {metrics_path}", flush=True)


if __name__ == "__main__":
    main()
