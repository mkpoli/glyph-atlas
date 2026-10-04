"""Where a whole character's two operands go, learned from every character the font draws.

Each drawn character whose sequence starts ⿰ or ⿱ and whose glyph cuts cleanly into its two
operands (`Composer._host_node`) is one example: the operands' boxes in the em are the answer. The
features of an operand are where the font usually puts it in that place (the mean and spread of its
boxes over the other characters holding it there, and how many those are), and the shape of its
own glyph (box, ink, proportions, outline count, and its ink on an 8 × 8 grid). A small network
reads both operands' features and gives both boxes.

Five models are trained, each without one fifth of the characters, and a sixth on all of them. A
character redrawn to test is laid out by the model that never saw it, with features measured
without it; anything else by the sixth.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import compose as C

SLOT = {"⿰": "LR", "⿱": "TB"}
FOLDS = 5
SEEDS = 3
#: The cached model's name; a change to the features or training makes a new one.
LAYOUT_FILE = "compose-layout-v1.npz"
#: Features per operand: 4 mean, 4 spread, count, outline count; 1 flag, 4 box, ink, proportions, 8 × 8 ink.
SHAPE = 71


def shape(composer: C.Composer, kid: str, cache: dict) -> list[float]:
    """An operand's own glyph: present, its box, ink, proportions and ink on an 8 × 8 grid."""
    if kid not in cache:
        name = kid if len(kid) == 1 else composer.by_sequence.get(kid)
        glyph = composer.font.glyph(name) if name else None
        if glyph is None:
            cache[kid] = [0.0] * SHAPE
        else:
            b = glyph.box
            rings = C._rings(glyph.contours, [c.light for c in glyph.contours], steps=2)
            grid = C.fill(rings, 32, b).astype(float).reshape(8, 4, 8, 4).mean(axis=(1, 3)).ravel()
            cache[kid] = [1.0, *(np.array(b) / 1000), math.log(max(composer.ink("", glyph), 1)) / 10,
                          math.log(max(b[2] - b[0], 1) / max(b[3] - b[1], 1)), *grid]
    return cache[kid]


def outlines(composer: C.Composer, kid: str) -> int:
    """An operand's own glyph's outline count: what is known of its strokes before it is drawn."""
    name = kid if len(kid) == 1 else composer.by_sequence.get(kid)
    glyph = composer.font.glyph(name) if name else None
    return len(glyph.contours) if glyph else 0


@dataclass
class Table:
    """Sums of operands' boxes per (place, operand), to give means and spreads with any one left out."""

    sums: dict[tuple[str, str], np.ndarray]
    squares: dict[tuple[str, str], np.ndarray]
    counts: dict[tuple[str, str], int]
    defaults: dict[str, np.ndarray]

    @classmethod
    def of(cls, rows: list[dict]) -> Table:
        sums, squares, counts, by_place = {}, {}, {}, {}
        for r in rows:
            for i, kid in enumerate(r["kids"]):
                k, box = (SLOT[r["op"]][i], kid), np.array(r["boxes"][i])
                sums[k] = sums.get(k, 0) + box
                squares[k] = squares.get(k, 0) + box ** 2
                counts[k] = counts.get(k, 0) + 1
                by_place.setdefault(k[0], []).append(box)
        return cls(sums, squares, counts, {p: np.median(np.array(v), axis=0) for p, v in by_place.items()})

    def usual(self, place: str, kid: str, leave: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, int]:
        k = (place, kid)
        n = self.counts.get(k, 0)
        total, square = self.sums.get(k), self.squares.get(k)
        if leave is not None and n:
            n, total, square = n - 1, total - leave, square - leave ** 2
        if n <= 0:
            return self.defaults[place], np.zeros(4), 0
        mean = total / n
        return mean, np.sqrt(np.maximum(square / n - mean ** 2, 0)), n


def features(composer: C.Composer, op: str, kids: list[str], table: Table, cache: dict,
             own: list[np.ndarray] | None = None) -> list[float]:
    out = [1.0 if op == "⿰" else 0.0]
    for i, kid in enumerate(kids):
        mean, spread, n = table.usual(SLOT[op][i], kid, None if own is None else own[i])
        out += [*(mean / 1000), *(spread / 100), math.log1p(n) / 5, math.log1p(outlines(composer, kid)) / 3,
                *shape(composer, kid, cache)]
    return out


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1 + np.tanh(0.7978845608028654 * (x + 0.044715 * x ** 3)))


@dataclass
class LayoutModel:
    """Five fold models and one on everything, each `SEEDS` networks averaged, with their tables."""

    tables: dict[str, Table]
    weights: dict[str, list[list[np.ndarray]]]
    fold_of: dict[str, int]
    _shapes: dict | None = None

    def predict(self, composer: C.Composer, op: str, kids: list[str], char: str | None = None) -> list[tuple]:
        """Both operands' boxes in the em; `char`, being redrawn to test, is laid out by the model
        that never saw it."""
        name = str(self.fold_of[char]) if char in self.fold_of else "all"
        if self._shapes is None:
            self._shapes = {}
        x = np.array(features(composer, op, kids, self.tables[name], self._shapes))
        runs = []
        for layers in self.weights[name]:
            h = x
            for k in range(0, len(layers) - 2, 2):
                h = _gelu(h @ layers[k] + layers[k + 1])
            runs.append((h @ layers[-2] + layers[-1]) * 1000)
        p = np.mean(runs, axis=0)
        return [tuple(float(v) for v in p[:4]), tuple(float(v) for v in p[4:])]

    def save(self, path: Path) -> None:
        arrays, meta = {}, {"fold_of": self.fold_of, "tables": {}, "weights": {}}
        for name, table in self.tables.items():
            keys = sorted(table.counts)
            meta["tables"][name] = {"keys": [f"{p}\t{k}" for p, k in keys], "defaults": {p: v.tolist() for p, v in table.defaults.items()}}
            arrays[f"{name}.sums"] = np.array([table.sums[k] for k in keys])
            arrays[f"{name}.squares"] = np.array([table.squares[k] for k in keys])
            arrays[f"{name}.counts"] = np.array([table.counts[k] for k in keys])
        for name, runs in self.weights.items():
            meta["weights"][name] = [len(layers) for layers in runs]
            for s, layers in enumerate(runs):
                for k, a in enumerate(layers):
                    arrays[f"{name}.{s}.{k}"] = a
        arrays["meta"] = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode(), dtype=np.uint8)
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: Path) -> LayoutModel:
        data = np.load(path)
        meta = json.loads(bytes(data["meta"]).decode())
        tables = {}
        for name, t in meta["tables"].items():
            keys = [tuple(k.split("\t", 1)) for k in t["keys"]]
            tables[name] = Table(dict(zip(keys, data[f"{name}.sums"])), dict(zip(keys, data[f"{name}.squares"])),
                                 dict(zip(keys, data[f"{name}.counts"].tolist())),
                                 {p: np.array(v) for p, v in t["defaults"].items()})
        weights = {name: [[data[f"{name}.{s}.{k}"] for k in range(n)] for s, n in enumerate(runs)]
                   for name, runs in meta["weights"].items()}
        return cls(tables, weights, meta["fold_of"])


def examples(composer: C.Composer, workers: int = 3) -> list[dict]:
    """Every drawn character whose ⿰ or ⿱ sequence cuts cleanly: its operands and their boxes."""
    import multiprocessing as mp

    global _COMPOSER
    _COMPOSER = composer
    _ = composer._index
    chars = sorted(ch for ch, seq in composer.sequences.items() if composer.font.has(ch) and seq[0] in SLOT)
    with mp.get_context("fork").Pool(workers) as pool:
        rows = [r for r in pool.imap_unordered(_example, chars, chunksize=64) if r]
    return sorted(rows, key=lambda r: r["c"])


_COMPOSER: C.Composer | None = None


def _example(char: str) -> dict | None:
    try:
        tree = C.parse(_COMPOSER.sequences[char])
        if len(tree) != 3:
            return None
        cut = _COMPOSER._host_node(char, tree, ())
    except (ValueError, LookupError):
        return None
    if cut is None:
        return None
    return {"c": char, "op": tree[0], "kids": [C.key(n) for n in tree[1:]],
            "boxes": [[float(v) for v in p.box] for p in cut.pieces]}


def train(composer: C.Composer, rows: list[dict], epochs: int = 600) -> LayoutModel:
    """The fold models and the model on everything (needs PyTorch, on CUDA when available)."""
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    order = list(rows)
    random.Random(0).shuffle(order)
    fold_of = {r["c"]: k % FOLDS for k, r in enumerate(order)}
    cache: dict = {}
    tables, weights = {}, {}
    for name in [*map(str, range(FOLDS)), "all"]:
        train_rows = [r for r in rows if name == "all" or fold_of[r["c"]] != int(name)]
        table = Table.of(train_rows)
        X = torch.tensor([features(composer, r["op"], r["kids"], table, cache, [np.array(b) for b in r["boxes"]])
                          for r in train_rows], dtype=torch.float32, device=device)
        Y = torch.tensor([[v / 1000 for b in r["boxes"] for v in b] for r in train_rows], dtype=torch.float32, device=device)
        runs = []
        for seed in range(SEEDS):
            torch.manual_seed(seed)
            gelu = lambda: torch.nn.GELU(approximate="tanh")
            model = torch.nn.Sequential(torch.nn.Linear(X.shape[1], 256), gelu(), torch.nn.Dropout(0.1),
                                        torch.nn.Linear(256, 256), gelu(), torch.nn.Dropout(0.1),
                                        torch.nn.Linear(256, 128), gelu(), torch.nn.Linear(128, 8)).to(device)
            opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
            sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
            model.train()
            for _ in range(epochs):
                perm = torch.randperm(len(X), device=device)
                for b in range(0, len(X), 512):
                    idx = perm[b:b + 512]
                    loss = torch.nn.functional.smooth_l1_loss(model(X[idx]), Y[idx], beta=0.01)
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                sched.step()
            linears = [m for m in model if isinstance(m, torch.nn.Linear)]
            runs.append([a for m in linears for a in (m.weight.detach().cpu().numpy().T, m.bias.detach().cpu().numpy())])
        tables[name], weights[name] = table, runs
    return LayoutModel(tables, weights, fold_of)
