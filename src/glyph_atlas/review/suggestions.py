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

from .. import refs

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


def classifier_path() -> Path:
    override = os.environ.get("ATLAS_CLASSIFIER_MODEL")
    if override:
        return Path(override)
    directory = ROOT / "models/classifier/artifacts"
    features = directory / "classifier-with-features.onnx"
    return features if features.is_file() else directory / "classifier.onnx"


#: The families whose forms CODH trained as one class. A kana family is not one of them: か, its
#: hentaigana and カ are separate classes, and a vote for か is a vote for か alone.
MERGED_RELATIONS = frozenset({"shinjitai-kyujitai"})


@lru_cache(maxsize=8192)
def _class_family(name: str) -> tuple[str, tuple[str, ...]]:
    if not name.startswith("U+"):
        return name, ()
    info = refs.grapheme_info(name)
    if info is None or info["relation"] not in MERGED_RELATIONS:
        return name, (refs.to_char(name),)
    return info["code_point"], tuple(member["char"] for member in info["members"])


def classifier_results(classes, probabilities) -> tuple[list[dict], dict]:
    """CODH-trained class probabilities support a family when its forms were merged."""
    groups = {}
    for name, probability in zip(classes, probabilities, strict=True):
        family, members = _class_family(name)
        group = groups.setdefault(family, {"family": family, "members": members, "score": 0.})
        group["score"] += float(probability)
    ordered = sorted(groups.values(), key=lambda group: -group["score"])
    top = ordered[0]
    scope = "family" if len(top["members"]) > 1 else "character" if top["members"] else "other"
    vote = {"text": top["members"][0] if scope == "character" else None,
            "score": round(top["score"], 5), "engine": "Atlas classifier", "identity_scope": scope}
    if top["members"]:
        vote.update(family=top["family"], members=list(top["members"]))
    candidates = []
    for group in ordered[:5]:
        if group["score"] < .015:
            continue
        ambiguous = len(group["members"]) > 1
        for member in group["members"][:5]:
            candidate = {"text": member, "engine": "Atlas classifier", "verified": False,
                         "identity_scope": "family" if ambiguous else "character", "family": group["family"]}
            if ambiguous:
                candidate["family_score"] = round(group["score"], 5)
            else:
                candidate["score"] = round(group["score"], 5)
            candidates.append(candidate)
    return candidates, vote


@lru_cache(maxsize=2)
def _visual_classifier(directory: str, stamp: int, size: int):
    from ..visual_classifier import VisualClassifier
    return VisualClassifier(Path(directory))


def visual_candidate(classifier, image: Image.Image, vote: dict, engines: list[dict]) -> dict | None:
    """An anchored embedding result remains a proposal, with distances kept separate."""
    if vote.get("identity_scope") != "family" or not hasattr(classifier, "features"):
        return None
    from ..visual_families import directory
    path = directory() / "classifier.json"
    try:
        stat = path.stat()
        head = _visual_classifier(str(path.parent), stat.st_mtime_ns, stat.st_size)
        encoder = next((engine.get("sha256") for engine in engines if engine["name"] == "Atlas classifier"), None)
        if not encoder or head.metadata.get("encoder_sha256") != encoder:
            return None
        embedding = classifier.features(image)
        if embedding is None:
            return None
        prediction = head.predict(embedding, vote["family"])
    except (OSError, ValueError, KeyError, RuntimeError):
        return None
    text = prediction.get("written_character")
    if not text or text not in vote["members"] or not prediction.get("within_support"):
        return None
    return {"text": text, "engine": "Atlas visual form", "identity_scope": "character",
            "verified": False, "family": vote["family"], "visual_prediction": prediction}


def rank(candidates: list[dict]) -> list[dict]:
    """The order a reviewer sees: symbol readings, then the two models' answers alternately.

    On single crops the Atlas classifier's first answer is right far more often than NDL's, whose
    model reads lines (on CODH's held-out books, 88% against 53% top-1; see
    `models/benchmark/`), so the classifier leads each pair. NDL's answers stay in the list,
    where they add characters the classifier cannot name. Ranking a stored result again gives the
    same order.
    """
    symbols = [c for c in candidates if c.get("basis") == "symbol-parts"]
    atlas = [c for c in candidates if c.get("basis") != "symbol-parts" and c.get("engine") != "NDLkotenOCR"]
    ndl = [c for c in candidates if c.get("basis") != "symbol-parts" and c.get("engine") == "NDLkotenOCR"]
    pairs = [c for i in range(max(len(atlas), len(ndl))) for c in (atlas[i:i + 1] + ndl[i:i + 1])]
    return symbols + pairs


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
        classifier = classifier_path()
        if classifier.is_file():
            from ..classify import Classifier
            session = ort.InferenceSession(str(classifier), sess_options=options, providers=providers)
            self.classifier = Classifier(classifier, session=session)
            self.engines.append({"name": "Atlas classifier", "sha256": hashlib.sha256(classifier.read_bytes()).hexdigest(),
                                 "provider": session.get_providers()[0]})

    def read(self, image: Image.Image) -> dict:
        from ..symbol_parts import recognize_parts

        result = self._read_base(image)
        partition = recognize_parts(image, self._read_base)
        if partition:
            result["symbol_partition"] = partition
            combined = [*partition["candidates"], *result["candidates"]]
            seen = set()
            result["candidates"] = [c for c in combined if not (c["text"] in seen or seen.add(c["text"]))][:6]
        result["candidates"] = rank(result["candidates"])
        return result

    def _read_base(self, image: Image.Image) -> dict:
        candidates = []
        votes = []
        if self.sequence is not None:
            input = self.sequence.get_inputs()[0]
            pixels = preprocess(image, (input.shape[3], input.shape[2]))
            output = self.sequence.run(None, {input.name: pixels})[0]
            decoded = decode(output, self.alphabet)
            candidates.extend(decoded)
            if decoded:
                votes.append(decoded[0])
        if self.classifier is not None:
            probabilities = self.classifier.probabilities(image)
            alternatives, vote = classifier_results(self.classifier.classes, probabilities)
            votes.append(vote)
            visual = visual_candidate(self.classifier, image, vote, self.engines)
            if visual:
                alternatives.insert(0, visual)
            for candidate in alternatives:
                if candidate["text"] not in [c["text"] for c in candidates]:
                    candidates.append(candidate)
        return {"status": "ready" if self.engines else "unavailable", "candidates": candidates[:6],
                "engines": self.engines, "votes": votes}


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
