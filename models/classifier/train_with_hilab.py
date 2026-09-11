"""Fine-tune the classifier on the HI Lab crops of T12. A separate experiment, never the baseline.

`work/codh-full` is one dataset of characters on pages, cut by CODH's own boxes, split by book. The
東京大学史料編纂所 くずし字データセット (T12, `work/hilab/units.parquet`) is another: 325,261 crops
of individual characters, several hands, cropped and labelled by the holder, with no page placement
and no book to split by. The card keeps them apart, so this script never touches
`work/classifier/{train,val,test}.parquet` and never writes `models/classifier/classes.json`. It
writes its own manifests, `work/classifier/hilab-{train,val}.parquet`, and its own metrics,
`artifacts/hilab-metrics.json`.

The experiment is the one a second source can answer: does fine-tuning the baseline on the HI Lab
crops change what it reads on the CODH test books, and how well does it read the hilab crops
themselves? The classes are the baseline's, so a hilab crop of a code point the baseline was not
trained on is an `other` crop, exactly as it would be at inference. The holdout is by numeric id, so
no crop is in both halves.

The crops are the ones `atlas import hilab --download` extracts below `cache/hilab/`; without them
the listing alone is imported and there is nothing to train on, which is what this script reports and
stops with. Never a silent skip: an experiment that did not run says so, and the README says so too.

    python models/classifier/train_with_hilab.py --build
    python models/classifier/train_with_hilab.py --epochs 3
    python models/classifier/train_with_hilab.py --test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import torch
import train
from build_manifests import Crop

from kuzushiji_atlas import tables

ROOT = Path(__file__).resolve().parents[2]

#: Where T12 puts the extracted crops, below the root of the archive.
MEMBER_ROOT = ("all", "characters")


def hilab_crop(root: Path, crop: str) -> Path:
    """The file of a hilab unit: the member path of `unit.crop`, below the extraction root."""
    member = crop.split("!", 1)[1] if "!" in crop else crop
    return root / Path(*member.split("/"))


def holdout(numeric_id: str, *, share: float, seed: int) -> bool:
    """Whether a crop is in the holdout: a stable draw over the id, so runs agree."""
    digest = hashlib.sha256(f"{seed}:{numeric_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < share


def build(
    units_path: Path,
    crops: Path,
    classes: list[str],
    out: Path,
    *,
    share: float,
    seed: int,
    limit: int | None,
    command: str,
) -> dict[str, int]:
    """Write `hilab-train.parquet` and `hilab-val.parquet` from the T12 units that are on disk."""
    index = {name: position for position, name in enumerate(classes)}
    members: list[Crop] = []
    skipped = Counter()
    for batch in tables.scan(units_path, _unit_model()):
        for unit in batch:
            if not unit.crop or not unit.unicode:
                skipped["no crop or no code point"] += 1
                continue
            path = hilab_crop(crops, unit.crop)
            if not path.is_file():
                skipped["crop not extracted"] += 1
                continue
            members.append(
                Crop(
                    unit_id=unit.id,
                    crop=str(path),
                    label=unit.unicode if unit.unicode in index else train.OTHER,
                    code_point=unit.unicode,
                    document_id=unit.document_id,
                    page_id="",
                    split="val" if holdout(unit.id.split(":")[-1], share=share, seed=seed) else "train",
                    production="kokatsuji",
                    kind=str(unit.kind),
                    script=str(unit.script) if unit.script else None,
                )
            )
            if limit is not None and len(members) >= limit:
                break
        if limit is not None and len(members) >= limit:
            break
    counts: dict[str, int] = {}
    for split in ("train", "val"):
        records = [record for record in members if record.split == split]
        target = out / f"hilab-{split}.parquet"
        counts[split] = tables.write(target, records, Crop, command=command)
        print(f"-> {target}: {counts[split]} crops", flush=True)
    labels = Counter(record.label for record in members)
    counts["classes"] = len(labels)
    counts["other"] = labels.get(train.OTHER, 0)
    for reason, count in sorted(skipped.items()):
        print(f"  left out: {count} units, {reason}", flush=True)
    print(f"hilab: {len(members)} crops, {counts['classes']} labels, {counts['other']} as {train.OTHER}", flush=True)
    return counts


def _unit_model() -> Any:
    """The `Unit` model, without importing the schema at module level for no reason."""
    from kuzushiji_atlas.schema import Unit

    return Unit


def combined(baseline: train.Data, extra: train.Data, *, limit: int | None = None) -> train.Data:
    """The baseline crops and the HI Lab crops as one split, keeping the baseline's class order."""
    data = baseline.concat(extra)
    return data.head(limit) if limit is not None else data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--units", type=Path, default=ROOT / "work" / "hilab" / "units.parquet")
    parser.add_argument("--crops", type=Path, default=ROOT / "cache" / "hilab")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--checkpoint", type=Path, default=None, help="the baseline to start from")
    parser.add_argument("--build", action="store_true", help="rebuild the HI Lab manifests")
    parser.add_argument("--share", type=float, default=0.1, help="share of the crops held out")
    parser.add_argument("--limit", type=int, default=None, help="HI Lab crops to use")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--test", action="store_true", help="measure the checkpoint and stop")
    parser.add_argument("--no-crops", action="store_true", help="skip the experiment even if the crops are there")
    args = parser.parse_args(argv)

    config = train.load_config(args.config)
    training = config["training"]
    data_directory = ROOT / config["data"]["directory"]
    baseline_directory = ROOT / config["artifacts"]["directory"]
    out = args.out or baseline_directory
    out.mkdir(parents=True, exist_ok=True)
    command = "python " + " ".join(shlex.quote(part) for part in (argv if argv is not None else sys.argv[1:]))
    classes = train.load_classes(ROOT / config["data"]["classes"])
    crops = args.crops
    extracted = sum(1 for _ in crops.glob(f"{'/'.join(MEMBER_ROOT)}/U+*/*.jpg")) if crops.is_dir() else 0
    if args.no_crops or not extracted:
        raise SystemExit(
            f"{crops} holds no HI Lab crops: extract them with `atlas import hilab --download`, "
            "then rerun; the baseline is unaffected and this experiment stays unrun"
        )
    print(f"{extracted} HI Lab crops below {crops}", flush=True)

    manifest = data_directory / "hilab-train.parquet"
    if args.build or not manifest.exists():
        build(
            args.units,
            crops,
            classes,
            data_directory,
            share=args.share,
            seed=int(training["seed"]),
            limit=args.limit,
            command=command,
        )
    hilab_train = train.read_manifest(data_directory / "hilab-train.parquet", classes)
    hilab_val = train.read_manifest(data_directory / "hilab-val.parquet", classes)
    print(f"HI Lab: {len(hilab_train)} train crops, {len(hilab_val)} holdout crops", flush=True)

    device = (
        torch.device(args.device or "cuda")
        if torch.cuda.is_available() and args.device != "cpu"
        else torch.device("cpu")
    )
    batch_size = args.batch_size or int(training["batch_size"])
    workers = args.workers if args.workers is not None else int(training["num_workers"])
    precision = str(training["precision"])
    size = int(config["preprocessing"]["size"])
    val_data = train.read_manifest(data_directory / config["data"]["splits"]["val"], classes)
    test_data = train.read_manifest(data_directory / config["data"]["splits"]["test"], classes)
    metrics_path = out / "hilab-metrics.json"
    report: dict[str, Any] = {"command": command, "hilab_crops": extracted, "epochs": []}

    checkpoint = args.checkpoint or baseline_directory / "best.pt"
    model = train.build_model(config, len(classes), pretrained=not checkpoint.exists()).to(device)
    if checkpoint.exists():
        state = torch.load(checkpoint, map_location=device, weights_only=False)
        model.load_state_dict(state["model"])
        temperature = float(state.get("temperature", 1.0))
        print(f"started from {checkpoint}, temperature {temperature:.4f}", flush=True)
    else:
        temperature = 1.0
        print(f"{checkpoint} is missing: starting from the ImageNet checkpoint", flush=True)

    if args.test:
        if metrics_path.exists():
            report = json.loads(metrics_path.read_text(encoding="utf-8"))
        for name, data in (("codh_test", test_data), ("hilab_holdout", hilab_val)):
            _, _, rates = train.measure(
                model,
                data,
                config=config,
                device=device,
                precision=precision,
                batch_size=max(batch_size, 128),
                workers=workers,
                size=size,
                seed=int(training["seed"]),
                temperature=temperature,
            )
            report[name] = rates
            print(f"{name}: {json.dumps(rates)}", flush=True)
        metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"-> {metrics_path}", flush=True)
        return

    baseline = train.read_manifest(data_directory / config["data"]["splits"]["train"], classes)
    data = combined(baseline, hilab_train, limit=args.limit)
    print(f"fine-tuning on {len(baseline)} CODH crops and {len(hilab_train)} HI Lab crops", flush=True)
    loader = train.loader_for(
        data,
        augment=True,
        batch_size=batch_size,
        workers=workers,
        size=size,
        seed=int(training["seed"]),
        sampler=train.balanced_sampler(data, seed=int(training["seed"])),
    )
    optimizer = train.optimizer_for(model, config)
    steps = len(loader) * args.epochs
    scheduler = train.scheduler_for(optimizer, config, steps)
    criterion = torch.nn.CrossEntropyLoss(label_smoothing=float(training["label_smoothing"]))
    started = time.time()
    for epoch in range(args.epochs):
        model.train()
        loader.dataset.seed = int(training["seed"]) * 1000 + epoch
        running, seen = 0.0, 0
        for step, (pixels, target) in enumerate(loader, start=1):
            pixels, target = pixels.to(device, non_blocking=True), target.to(device, non_blocking=True)
            with train.autocast_of(precision):
                loss = criterion(model(pixels), target)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(training["grad_clip"]))
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            scheduler.step()
            running += float(loss.detach())
            seen += 1
            if step % 200 == 0:
                print(
                    f"epoch {epoch} step {step}/{len(loader)} loss {running / seen:.4f} "
                    f"{train.peak_vram()}",
                    flush=True,
                )
        _, _, rates = train.measure(
            model,
            hilab_val,
            config=config,
            device=device,
            precision=precision,
            batch_size=max(batch_size, 128),
            workers=workers,
            size=size,
            seed=int(training["seed"]),
        )
        report["epochs"].append({"epoch": epoch, "loss": running / max(seen, 1), **rates})
        print(f"epoch {epoch}: loss {running / max(seen, 1):.4f} hilab holdout top1 {rates['top1']:.4f}", flush=True)
        torch.save(
            train.snapshot(model, optimizer, scheduler, config, classes, epoch, rates),
            out / "hilab-last.pt",
        )

    # The temperature is fitted on the CODH val crops, the same ones the baseline used, so the two
    # experiments are calibrated against the same distribution.
    if len(val_data):
        logits, labels, _ = train.measure(
            model,
            val_data,
            config=config,
            device=device,
            precision=precision,
            batch_size=max(batch_size, 128),
            workers=workers,
            size=size,
            seed=int(training["seed"]),
        )
        temperature, fitting = train.fit_temperature(logits, labels)
        report["calibration"] = {"temperature": temperature, **fitting}
        print(
            f"temperature {temperature:.4f}: val ECE {fitting['ece_before']:.4f} -> {fitting['ece_after']:.4f}",
            flush=True,
        )
    for name, data in (("codh_test", test_data), ("hilab_holdout", hilab_val)):
        logits, labels, rates = train.measure(
            model,
            data,
            config=config,
            device=device,
            precision=precision,
            batch_size=max(batch_size, 128),
            workers=workers,
            size=size,
            seed=int(training["seed"]),
            temperature=temperature,
        )
        report[name] = {**rates, "confusions": train.confusion_pairs(logits, labels, classes)}
        print(f"{name}: {json.dumps(rates)}", flush=True)
    report["seconds"] = time.time() - started
    metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"-> {metrics_path}", flush=True)


if __name__ == "__main__":
    main()
