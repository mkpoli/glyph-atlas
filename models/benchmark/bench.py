"""Measure single-crop recognizers on held-out crops, the way the review panel uses them.

A recognizer answers up to five characters for one crop, best first. A crop is read correctly at
k when its character is among the first k answers. A character the recognizer cannot name (outside
its class list or alphabet) counts as a miss: the reviewer gets no useful suggestion either way.
`family` also accepts a character of the same 新字・旧字 family, because CODH merged those forms.

Sets, all held out from the Atlas classifier's training:

- `codh-test`: the four CODH test books of `data/splits/codh.tsv`; every kanji crop and a fixed
  sample of the kana and symbol crops.
- `hilab-test`: the HI Lab crops in the held-out tenth of `hilab_split`. The dataset records no
  book; ids that are close together share a character folder and, it seems, a source, so the
  tenth is drawn by blocks of 200 ids. A hand may still fall on both sides, so the set measures
  breadth of vocabulary more than transfer to a new hand. Training that uses HI Lab crops takes
  only `hilab_rows` marked `train`.
- `atlas-reviewed`: crops a person confirmed or corrected on the review site (`reviewed.jsonl`).

    python models/benchmark/bench.py --set codh-test --model ndl ndl-pad atlas --out results/x.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from glyph_atlas import refs
from glyph_atlas.review.suggestions import MODEL, decode

KANA_SAMPLE = 12_000


def code_point(char: str) -> str:
    return f"U+{ord(char):04X}"


def family(cp: str) -> str:
    info = refs.grapheme_info(cp)
    return info["code_point"] if info and info["relation"] == "shinjitai-kyujitai" else cp


HILAB_SCRIPTS = {"han": "kanji", "hiragana": "hiragana", "katakana": "katakana", "hentaigana": "hiragana"}


def hilab_split(numeric_id: str) -> str:
    block = int(numeric_id) // 200
    return "test" if int(hashlib.sha256(str(block).encode()).hexdigest(), 16) % 10 == 0 else "train"


def hilab_rows() -> list[dict]:
    """Every downloaded HI Lab crop as a manifest row, in the columns of `work/classifier`."""
    import pyarrow.parquet as pq
    rows = []
    for unit in pq.read_table(ROOT / "work/hilab/units.parquet", columns=["id", "unicode", "script"]).to_pylist():
        numeric, cp = unit["id"].removeprefix("hi:"), unit["unicode"]
        path = ROOT / "cache/hilab/all/characters" / str(cp) / f"{numeric}.jpg"
        if not cp or not path.is_file():
            continue
        rows.append({"unit_id": unit["id"], "crop": str(path.relative_to(ROOT)), "label": cp, "code_point": cp,
                     "document_id": "hi-lab", "page_id": "", "split": hilab_split(numeric),
                     "production": "manuscript", "kind": "char",
                     "script": HILAB_SCRIPTS.get(str(unit["script"]), "symbol")})
    return rows


def load_set(name: str) -> list[dict]:
    import pyarrow.parquet as pq
    if name == "codh-test":
        rows = pq.read_table(ROOT / "work/classifier/test.parquet",
                             columns=["unit_id", "crop", "code_point", "script", "production"]).to_pylist()
        kanji = [r for r in rows if r["script"] == "kanji"]
        rest = [r for r in rows if r["script"] != "kanji"]
        random.Random(0).shuffle(rest)
        return [{"id": r["unit_id"], "path": ROOT / r["crop"], "truth": r["code_point"], "script": r["script"]}
                for r in kanji + rest[:KANA_SAMPLE]]
    if name == "hilab-test":
        return [{"id": r["unit_id"], "path": ROOT / r["crop"], "truth": r["code_point"], "script": r["script"]}
                for r in hilab_rows() if r["split"] == "test"]
    if name == "atlas-reviewed":
        out = []
        for line in (Path(__file__).parent / "reviewed.jsonl").read_text().splitlines():
            r = json.loads(line)
            key = r["image"].rsplit("/", 1)[-1].removesuffix(".webp")
            out.append({"id": r["id"], "path": ROOT / "cache/display-crops" / key[:2] / (key + ".webp"),
                        "truth": code_point(r["truth"]), "script": r["script"]})
        return out
    raise ValueError(name)


class NDL:
    """NDLkotenOCR-Lite PARSeq over one crop. `pad` puts the crop on a line-shaped white canvas
    instead of stretching it to the model's 12:1 input, which is how the served wrapper reads it."""

    def __init__(self, *, pad: bool):
        import onnxruntime as ort
        import yaml
        ort.preload_dlls()
        self.session = ort.InferenceSession(str(MODEL / "parseq.onnx"), providers=["CUDAExecutionProvider"])
        self.alphabet = yaml.safe_load((MODEL / "characters.yaml").read_text())["model"]["charset_train"]
        self.pad = pad
        shape = self.session.get_inputs()[0].shape
        self.size = (shape[3], shape[2])

    def pixels(self, image: Image.Image) -> np.ndarray:
        image = image.convert("RGB")
        if image.height > image.width:
            image = image.transpose(Image.Transpose.ROTATE_90)
        if self.pad:
            width, height = self.size
            scale = height / image.height
            fitted = image.resize((max(1, min(width, round(image.width * scale))), height))
            canvas = Image.new("RGB", self.size, "white")
            canvas.paste(fitted, (0, 0))
            image = canvas
        else:
            image = image.resize(self.size)
        pixels = np.asarray(image, dtype=np.float32)[:, :, ::-1] / 127.5 - 1
        return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])

    def top(self, images: list[Image.Image]) -> list[list[str]]:
        name = self.session.get_inputs()[0].name
        out = []
        for image in images:
            logits = self.session.run(None, {name: self.pixels(image)})[0]
            texts = [c["text"] for c in decode(logits, self.alphabet)]
            # A single-crop answer is one character; a longer reading names its first one.
            out.append(list(dict.fromkeys(code_point(t[0]) for t in texts if t)))
        return out


class Classifier:
    """An exported Atlas classifier; `other` is an abstention and never an answer."""

    def __init__(self, path: Path):
        import onnx

        from glyph_atlas.classify import Classifier as Served
        side = onnx.load(str(path), load_external_data=False).graph.input[0].type.tensor_type.shape.dim[2].dim_value
        self.model = Served(path, size=side or 96)

    def top(self, images: list[Image.Image]) -> list[list[str]]:
        probs = self.model.probabilities_many(images)
        classes = self.model.classes
        return [[classes[i] for i in np.argsort(p)[::-1][:6] if classes[i] != "other"][:5] for p in probs]


class Metom:
    """SakanaAI's Metom, a ViT classifier over 2,703 CODH characters (Apache-2.0).

    Metom was trained on a random 3:1:1 split of all of CODH, so it has seen crops from CODH's test
    books; its `codh-test` score is not a held-out measure. `hilab-test` and `atlas-reviewed` are.
    `METOM` names a local snapshot of https://huggingface.co/SakanaAI/Metom; it needs `einops`.
    """

    def __init__(self):
        import os

        import torch
        from transformers import AutoProcessor
        path = os.environ.get("METOM", "SakanaAI/Metom")
        self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True)
        # The remote code predates transformers 5, whose `from_pretrained` it cannot pass, so the
        # model is built from its config and the weights are loaded directly.
        from safetensors.torch import load_file
        from transformers import AutoConfig
        from transformers.dynamic_module_utils import get_class_from_dynamic_module
        config = AutoConfig.from_pretrained(path, trust_remote_code=True)
        config._attn_implementation = "eager"
        cls = get_class_from_dynamic_module(config.auto_map["AutoModel"], path)
        self.model = cls(config)
        self.model.load_state_dict(load_file(str(Path(path) / "model.safetensors")))
        self.model = self.model.cuda().eval()
        self.torch = torch

    def top(self, images):
        pixels = self.processor(images=images, return_tensors="pt")["pixel_values"].cuda()
        with self.torch.inference_mode():
            labels = self.model.get_topk_labels(pixels, k=5)
        return [[code_point(t) for t in row if len(t) == 1] for row in labels]


#: Lists merged from two models' answers. `served` is the order the review panel shows: the first
#: model's answers, then the second's it lacks. `interleave` alternates them, first model leading.
MERGES = {"served": ("ndl", "atlas", "append"), "interleave": ("atlas", "ndl", "interleave"),
          "atlas+metom": ("atlas", "metom", "interleave")}


def merge(first: list[str], second: list[str], how: str) -> list[str]:
    if how == "interleave":
        pairs = [x for i in range(max(len(first), len(second))) for x in (first[i:i + 1] + second[i:i + 1])]
        return list(dict.fromkeys(pairs))[:5]
    return list(dict.fromkeys(first + second))[:5]


def build(name: str, cache: dict):
    if name in cache:
        return cache[name]
    if name == "ndl":
        model = NDL(pad=False)
    elif name == "ndl-pad":
        model = NDL(pad=True)
    elif name == "atlas":
        model = Classifier(ROOT / "models/classifier/artifacts/classifier.onnx")
    elif name == "metom":
        model = Metom()
    elif name.startswith("onnx:"):
        model = Classifier(Path(name.removeprefix("onnx:")))
    else:
        raise ValueError(name)
    cache[name] = model
    return model


def score(rows: list[dict], answers: list[list[str]]) -> dict:
    groups = defaultdict(lambda: {"n": 0, "top1": 0, "top5": 0, "family1": 0, "family5": 0, "empty": 0})
    for row, top in zip(rows, answers, strict=True):
        fam = family(row["truth"])
        fams = [family(a) for a in top]
        for key in ("all", row["script"]):
            g = groups[key]
            g["n"] += 1
            g["top1"] += bool(top) and top[0] == row["truth"]
            g["top5"] += row["truth"] in top[:5]
            g["family1"] += bool(fams) and fams[0] == fam
            g["family5"] += fam in fams[:5]
            g["empty"] += not top
    return {k: {"n": g["n"], **{m: round(g[m] / g["n"], 4) for m in g if m != "n"}} for k, g in sorted(groups.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", required=True, choices=["codh-test", "hilab-test", "atlas-reviewed"])
    parser.add_argument("--model", nargs="+", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = load_set(args.set)
    if args.limit:
        random.Random(1).shuffle(rows)
        rows = rows[:args.limit]
    cache, report = {}, {"set": args.set, "crops": len(rows), "models": {}}
    answers = {}

    def read(name):
        if name not in answers:
            model, found, started = build(name, cache), [], time.time()
            for i in range(0, len(rows), 256):
                images = []
                for row in rows[i:i + 256]:
                    with Image.open(row["path"]) as image:
                        images.append(image.convert("RGB"))
                found.extend(model.top(images))
            answers[name] = found
            report["models"][name] = {"seconds": round(time.time() - started, 1)}
        return answers[name]

    for name in args.model:
        if name in MERGES:
            a, b, how = MERGES[name]
            answers[name] = [merge(x, y, how) for x, y in zip(read(a), read(b), strict=True)]
            report["models"][name] = {}
        report["models"][name]["scores"] = score(rows, read(name))
        print(json.dumps({name: report["models"][name]["scores"]["all"]}), flush=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
