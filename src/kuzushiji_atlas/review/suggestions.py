"""Local image-only suggestions. No transcription or existing label is used as model input.

NDLkotenOCR-Lite PARSeq preprocessing/decoding follows ndl-lab/ndlkotenocr-lite,
revision ede4283845cdc0ba2bda8b7ebfc3dc80b33c92c8 (National Diet Library, CC BY 4.0).
The optional Atlas classifier supplies alternatives for individual characters.
"""
from __future__ import annotations

import hashlib
import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "cache/models/ndlkotenocr-lite"
LOCK = threading.Lock()


def decode(logits: np.ndarray, alphabet: str) -> list[dict]:
    """Decode EOS-terminated tokens and a few single-position alternatives.

    These are model suggestions, not calibrated confidence estimates or separate OCR runs.
    """
    values = np.asarray(logits)[0]
    if values.ndim != 2 or values.shape[1] != len(alphabet) + 1:
        raise ValueError("OCR alphabet and output disagree")
    values = values.astype(np.float64)
    values -= values.max(axis=1, keepdims=True)
    probs = np.exp(values)
    probs /= probs.sum(axis=1, keepdims=True)
    tokens = probs.argmax(axis=1).tolist()
    length = tokens.index(0) if 0 in tokens else len(tokens)
    length = min(length, 32)
    if not length:
        return []
    best = tokens[:length]
    candidates = [(best, float(np.log(probs[np.arange(length), best]).clip(-100).mean()))]
    for position in range(length):
        for token in np.argsort(probs[position])[-3:][::-1]:
            if token == 0 or token == best[position] or probs[position, token] < .015:
                continue
            alternative = best.copy()
            alternative[position] = int(token)
            score = float(np.log(probs[np.arange(length), alternative]).clip(-100).mean())
            candidates.append((alternative, score))
    output = []
    for seq, score in sorted(candidates, key=lambda c: -c[1]):
        text = "".join(alphabet[i - 1] for i in seq).strip()
        if text and text not in [c["text"] for c in output]:
            output.append({"text": text, "score": round(float(np.exp(score)), 5), "engine": "NDLkotenOCR"})
    return output[:3]


def preprocess(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    image = image.convert("RGB")
    if image.height > image.width:
        image = image.transpose(Image.Transpose.ROTATE_90)
    # Match the upstream RGB-to-BGR channel order and [-1, 1] normalisation.
    pixels = np.asarray(image.resize(size), dtype=np.float32)[:, :, ::-1] / 127.5 - 1
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])


class Recognizer:
    def __init__(self):
        import onnxruntime as ort
        import yaml

        ort.set_default_logger_severity(3)
        directory = Path(os.environ.get("ATLAS_OCR_MODEL_DIR", MODEL))
        self.sequence = None
        self.classifier = None
        self.engines = []
        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in available:
            ort.preload_dlls()
            providers = [("CUDAExecutionProvider", {"gpu_mem_limit": 768 * 1024 * 1024,
                                                   "arena_extend_strategy": "kSameAsRequested"}),
                         "CPUExecutionProvider"]
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        model = directory / "parseq.onnx"
        alphabet = directory / "characters.yaml"
        if model.is_file() and alphabet.is_file():
            self.sequence = ort.InferenceSession(str(model), sess_options=options, providers=providers)
            self.alphabet = yaml.safe_load(alphabet.read_text())["model"]["charset_train"]
            self.engines.append({"name": "NDLkotenOCR", "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                                 "provider": self.sequence.get_providers()[0]})
        classifier = Path(os.environ.get("ATLAS_CLASSIFIER_MODEL", ROOT / "models/classifier/artifacts/classifier.onnx"))
        if classifier.is_file():
            from ..classify import Classifier
            session = ort.InferenceSession(str(classifier), sess_options=options, providers=providers)
            self.classifier = Classifier(classifier, session=session)
            self.engines.append({"name": "Atlas classifier", "sha256": hashlib.sha256(classifier.read_bytes()).hexdigest(),
                                 "provider": session.get_providers()[0]})

    def read(self, image: Image.Image) -> dict:
        candidates = []
        if self.sequence is not None:
            input = self.sequence.get_inputs()[0]
            pixels = preprocess(image, (input.shape[3], input.shape[2]))
            output = self.sequence.run(None, {input.name: pixels})[0]
            candidates.extend(decode(output, self.alphabet))
        if self.classifier is not None:
            probabilities = self.classifier.probabilities(image)
            for index in np.argsort(probabilities)[-5:][::-1]:
                name = self.classifier.classes[index]
                if not name.startswith("U+") or probabilities[index] < .015:
                    continue
                text = chr(int(name[2:], 16))  # noqa: FURB166 — U+ is a Unicode label, not a numeric prefix
                if text not in [c["text"] for c in candidates]:
                    candidates.append({"text": text, "score": round(float(probabilities[index]), 5),
                                       "engine": "Atlas classifier"})
        return {"status": "ready" if self.engines else "unavailable", "candidates": candidates[:6],
                "engines": self.engines}


@lru_cache(maxsize=1)
def recognizer() -> Recognizer:
    return Recognizer()


@lru_cache(maxsize=512)
def infer(path: str, stamp: int, box: tuple[float, ...] | None) -> dict:
    """Serialize GPU work and cache by immutable image identity and exact crop geometry."""
    with LOCK:
        from .atlas import decoded_image
        source = decoded_image(path, stamp)
        if box:
            x, y, w, h = box
            source = source.crop((max(0, int(x)), max(0, int(y)), min(source.width, int(x + w)),
                                  min(source.height, int(y + h))))
        return recognizer().read(source)
