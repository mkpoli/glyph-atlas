"""Name the three small seal characters whose drawing is nearest to each seal form CODH cut.

A title of CODH's 篆書字体データセット other than 説文解字 prints several forms under a 親字: 聯珠篆文 two
under nearly every one, 新撰篆書字典 one to a dozen. Some are the small seal form and others bronze, stone
or old script forms, and the dataset does not say which. `scripts/build_tensho_equivalents.py` takes the
small seal forms from among them by what this writes.

The model is a ResNet-18 over 96 px binarised forms, with one cosine prototype for each of Unicode's
11,328 small seal characters. A first one is trained from nothing on two drawings of a character: its
glyph in Sealie (OFL 1.1), and its crop of 説文解字 (TE00039) where `data/tensho/TE00039/characters.tsv`
places one. Training bends, scales and thins or thickens the drawings.

The hands of the other titles differ from both, so a second round trains on them too. The 親字 of those
titles are split in two, and for each half a model is trained as before and on the crops of the other
half whose first answer is a seal character of their 親字. It answers for its own half. So no crop is
answered for by a model that saw a crop of its 親字 outside 説文解字, and the crops a title files alone under
the 親字 of one seal character measure the models; every tenth crop of 説文解字, kept back throughout,
measures them on that book.

    python models/seal/match.py work/tensho/dl --font cache/fonts/Sealie-Medium.ttf \\
        --seal-sources cache/ucd/SealSources.txt

It reads `<book>.zip` of each book of the source record, each the one the record pins, and the table of
説文解字. A book whose own table is not built yet is matched all the same, and measures nothing. It writes
`data/tensho/ink.tsv` and `models/seal/metrics.json`. The checkpoints go to
`models/seal/artifacts/`, which is not committed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
import torchvision
import yaml
from PIL import Image, ImageDraw, ImageFont
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_seal_font import BLOCK, FONT
from build_tensho_equivalents import (
    CHARACTERS,
    GETA,
    equivalents,
    graphemes,
    number,
    seals_by_parent,
    unified,
)

from glyph_atlas.importers.tensho import DEFAULT_DATA, DEFAULT_SOURCE, crop_name, read_tsv

SIZE = 96
#: The longer side of a form within the square.
FORM = 84
CLASSES = BLOCK[1] - BLOCK[0] + 1
SHUOWEN = "TE00039"
WIDTH = 384
NEAREST = 3
WORKERS = 4


def binarised(data: bytes) -> np.ndarray:
    """The ink of a crop, its specks left out."""
    grey = np.asarray(Image.open(io.BytesIO(data)).convert("L"))
    if max(grey.shape) > 200:
        scale = 200 / max(grey.shape)
        grey = np.asarray(Image.fromarray(grey).resize(
            (max(4, round(grey.shape[1] * scale)), max(4, round(grey.shape[0] * scale))), Image.Resampling.LANCZOS))
    _, ink = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=4)
    small = [label for label in range(1, count) if stats[label, cv2.CC_STAT_AREA] < 12]
    return (ink > 0) & ~np.isin(labels, small)


def squared(ink: np.ndarray) -> np.ndarray:
    """A form in the middle of the square, its proportions kept."""
    rows, columns = np.where(ink)
    if len(rows) < 5:
        return np.zeros((SIZE, SIZE), np.uint8)
    form = ink[rows.min():rows.max() + 1, columns.min():columns.max() + 1]
    scale = FORM / max(form.shape)
    image = Image.fromarray((form * 255).astype(np.uint8)).resize(
        (max(3, round(form.shape[1] * scale)), max(3, round(form.shape[0] * scale))), Image.Resampling.LANCZOS)
    square = Image.new("L", (SIZE, SIZE), 0)
    square.paste(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2))
    return np.asarray(square)


def drawn(font: Path, points: list[int]) -> list[np.ndarray]:
    face = ImageFont.truetype(str(font), 160)
    forms = []
    for point in points:
        image = Image.new("L", (260, 260), 0)
        ImageDraw.Draw(image).text((40, 20), chr(point), font=face, fill=255)
        forms.append(squared(np.asarray(image) > 128))
    return forms


def cut(archive: Path, members: list[str]) -> list[np.ndarray]:
    with zipfile.ZipFile(archive) as held:
        return [squared(binarised(held.read(member))) for member in members]


def in_parts(pool: ProcessPoolExecutor, work, items: list, part: int = 300) -> np.ndarray:
    return np.stack([form for forms in pool.map(work, [items[i:i + part] for i in range(0, len(items), part)])
                     for form in forms])


class Matcher(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.body = torchvision.models.resnet18(weights=None)
        self.body.conv1 = nn.Conv2d(1, 64, 5, 2, 2, bias=False)
        self.body.fc = nn.Linear(512, WIDTH)
        self.prototypes = nn.Parameter(torch.randn(CLASSES, WIDTH) * 0.02)

    def forward(self, forms: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.body(forms)) @ F.normalize(self.prototypes).T


def softened(forms: torch.Tensor) -> torch.Tensor:
    return F.avg_pool2d(forms.float().unsqueeze(1) / 255, 3, 1, 1)


def bent(forms: torch.Tensor) -> torch.Tensor:
    """The forms turned, scaled, slanted, shifted and warped, their strokes thickened or thinned."""
    count, device = len(forms), forms.device

    def between(low: float, high: float, *shape: int) -> torch.Tensor:
        return low + (high - low) * torch.rand(count, *shape, device=device)

    images = forms.float().unsqueeze(1) / 255
    turn, wide, tall, slant = between(-0.15, 0.15), between(0.75, 1.15), between(0.75, 1.15), between(-0.15, 0.15)
    matrix = torch.stack([
        torch.stack([turn.cos() / wide, -turn.sin() / tall + slant, between(-0.06, 0.06)], 1),
        torch.stack([turn.sin() / wide, turn.cos() / tall, between(-0.06, 0.06)], 1)], 1)
    grid = F.affine_grid(matrix, images.shape, align_corners=False)
    warp = F.interpolate(between(-0.08, 0.08, 2, 6, 6), size=(SIZE, SIZE), mode="bicubic", align_corners=True)
    images = F.grid_sample(images, grid + warp.permute(0, 2, 3, 1), align_corners=False)
    thick = F.max_pool2d(images, 3, 1, 1)
    thin = -F.max_pool2d(-images, 3, 1, 1)
    thin = torch.where(thin.amax((1, 2, 3), keepdim=True) > 0.3, (thin + images) / 2, images)
    pick = between(0, 1, 1, 1, 1)
    images = torch.where(pick < 0.3, thick, torch.where(pick > 0.75, thin, images))
    return F.avg_pool2d(images, 3, 1, 1) * between(0.7, 1.3, 1, 1, 1) + 0.08 * torch.randn_like(images)


def train(forms: np.ndarray, classes: list[int], epochs: int, batch: int = 256, rate: float = 2e-3) -> Matcher:
    device = torch.device("cuda")
    net = Matcher().to(device).to(memory_format=torch.channels_last)
    held, target = torch.from_numpy(forms).to(device), torch.tensor(classes, device=device)
    steps = len(held) // batch
    optimiser = torch.optim.AdamW(net.parameters(), lr=rate, weight_decay=0.02)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimiser, rate, total_steps=epochs * steps, pct_start=0.1)
    for epoch in range(epochs):
        order, total = torch.randperm(len(held), device=device), 0.0
        for step in range(steps):
            index = order[step * batch:(step + 1) * batch]
            with torch.autocast("cuda", torch.bfloat16):
                cosine = net(bent(held[index]).to(memory_format=torch.channels_last))
            cosine = cosine.float() - 0.15 * F.one_hot(target[index], CLASSES)
            loss = F.cross_entropy(cosine * 28, target[index])
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            schedule.step()
            total += loss.item()
        if epoch % 10 == 9:
            print(f"epoch {epoch + 1}: loss {total / steps:.3f}", file=sys.stderr, flush=True)
    return net.eval()


@torch.no_grad()
def nearest(net: Matcher, forms: np.ndarray) -> np.ndarray:
    """The classes nearest to each form, nearest first."""
    found = []
    for start in range(0, len(forms), 512):
        with torch.autocast("cuda", torch.bfloat16):
            cosine = net(softened(torch.from_numpy(forms[start:start + 512]).cuda()))
        found.append(cosine.float().topk(NEAREST).indices.cpu().numpy())
    return np.concatenate(found)


def class_of(row: dict[str, str]) -> int:
    return number(row["code_point"]) - BLOCK[0]


def half_of(row: dict[str, str]) -> int:
    return hashlib.sha256(unified(row["parent"]).encode()).digest()[0] % 2


def found(near: np.ndarray, classes: list[int]) -> dict[str, float | int]:
    hits = near == np.asarray(classes)[:, None]
    return {"crops": len(classes), "first": round(float(hits[:, 0].mean()), 4),
            "first_three": round(float(hits.any(1).mean()), 4)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("zips", type=Path, help="the directory of the books' zips")
    parser.add_argument("--font", type=Path, required=True, help="Sealie Medium, the file build_seal_font.py pins")
    parser.add_argument("--seal-sources", type=Path, required=True, help="SealSources.txt of Unicode 18.0.0")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA / "ink.tsv")
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).parent / "artifacts")
    args = parser.parse_args(argv)
    if hashlib.sha256(args.font.read_bytes()).hexdigest() != FONT["sha256"]:
        raise SystemExit(f"{args.font}: not the font {FONT['url']} pins")

    rows: list[dict[str, str]] = []
    parts: list[np.ndarray] = []
    began = time.time()
    with ProcessPoolExecutor(WORKERS) as pool:
        glyphs = in_parts(pool, partial(drawn, args.font), list(range(BLOCK[0], BLOCK[1] + 1)))
        for book in yaml.safe_load(DEFAULT_SOURCE.read_text(encoding="utf-8"))["books"]:
            archive = args.zips / f"{book['id']}.zip"
            if hashlib.sha256(archive.read_bytes()).hexdigest() != book["zip_sha256"]:
                raise SystemExit(f"{archive}: not the zip the source record pins")
            table = DEFAULT_DATA / book["id"] / "characters.tsv"
            held = {crop_name(row): row for row in read_tsv(table)} if table.is_file() else {}
            with zipfile.ZipFile(archive) as files:
                members = sorted(name for name in files.namelist() if re.search(r"/characters/.+\.jpg$", name))
            parts.append(in_parts(pool, partial(cut, archive), members))
            for member in members:
                name = Path(member).stem
                row = held.get(name, {"placed": "", "code_point": ""})
                rows.append({"crop": name, "parent": name.split("_")[0].removeprefix("U+"), "book": book["id"], "placed": row["placed"], "code_point": row["code_point"]})
    forms = np.concatenate(parts)

    placed = [i for i, row in enumerate(rows) if row["book"] == SHUOWEN and row["placed"]]
    kept_back = set(placed[::10])
    shown = [i for i in placed if i not in kept_back]
    given = np.concatenate([glyphs, forms[shown]])
    classes = [*range(CLASSES), *(class_of(rows[i]) for i in shown)]
    torch.manual_seed(0)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    net = train(given, classes, args.epochs)
    torch.save(net.state_dict(), args.artifacts / "first.pt")
    first = nearest(net, forms)

    seals_of = seals_by_parent(equivalents(args.seal_sources), graphemes(CHARACTERS))
    others = [i for i, row in enumerate(rows) if row["book"] != SHUOWEN]
    agreed = [i for i in others if rows[i]["parent"] != GETA
              and f"U+{BLOCK[0] + first[i, 0]:04X}" in seals_of(unified(rows[i]["parent"]))]
    near = first.copy()
    back: list[int] = sorted(kept_back)
    for half in (0, 1):
        learnt = [i for i in agreed if half_of(rows[i]) != half]
        net = train(np.concatenate([given, forms[learnt]]), classes + [int(first[i, 0]) for i in learnt], args.epochs)
        torch.save(net.state_dict(), args.artifacts / f"half{half}.pt")
        asked = [i for i in others if half_of(rows[i]) == half]
        near[asked] = nearest(net, forms[asked])
        mine = back[half::2]
        near[mine] = nearest(net, forms[mine])

    report = {"epochs": args.epochs, "trained_on": {"glyphs": CLASSES, "crops": len(shown), "agreed": len(agreed)},
              "seconds": math.ceil(time.time() - began), "font": FONT["url"],
              "held_back": found(near[back], [class_of(rows[i]) for i in back]),
              "held_back_first_round": found(first[back], [class_of(rows[i]) for i in back])}
    alone = [i for i, row in enumerate(rows) if row["placed"] == "equivalent"]
    for book in sorted({rows[i]["book"] for i in alone}):
        these = [i for i in alone if rows[i]["book"] == book]
        report[f"alone_{book}"] = found(near[these], [class_of(rows[i]) for i in these])
        report[f"alone_{book}_first_round"] = found(first[these], [class_of(rows[i]) for i in these])
    (Path(__file__).parent / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), file=sys.stderr)

    header = (
        "# The three small seal characters of Unicode 18.0 nearest in drawing to each seal form CODH cut from a",
        "# title other than 説文解字, nearest first. Written by models/seal/match.py; crop is the crop file's name",
        "# in 篆書字体データセット.",
    )
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\ncrop\tnearest\n")
        for row, classes in zip(rows, near, strict=True):
            if row["book"] != SHUOWEN:
                handle.write(f"{row['crop']}\t{' '.join(f'U+{BLOCK[0] + c:04X}' for c in classes)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
