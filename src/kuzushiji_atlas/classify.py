"""Calibrated character probabilities from an ONNX classifier.

One crop is one written character. The module turns it into the tensor the exported model takes and
returns a probability for every class of `classes.json`: a code point the model identifies, or the
residual class `other`. `score_set` sums those probabilities over a set of code points, which is what
the alignment of T23 scores a detection with: hentaigana forms of one kana and 旧字/新字 pairs are
several code points, and the transcribed character is one of them.

Preprocessing is the one `models/classifier/train.py` trains with: the crop is converted to grey, the
aspect ratio is kept by padding it to a square with white, the square is resized to 96x96 and the
grey channel is replicated to the three channels the model takes, scaled to [0, 1] and normalised
with the grey equivalent of the ImageNet statistics.

`other` is a target during training and an abstention at inference. A crop the model cannot identify
gets its mass there, and a caller that reads the probabilities as identifications leaves `other` out.
`score_set` counts it as the mass of every code point the classifier was not trained on, so a set of
code points that all lie outside the class list still scores at the abstention probability instead of
zero.

The model takes one float32 tensor `pixel_values` of shape (1, 3, 96, 96) and returns either `probs`
(1, classes), a distribution over the classes of `classes.json`, or `logits` of the same shape, which
the module turns into a distribution itself. The class list is read from `classes.json`, beside the
export or one directory above it, which is how `models/classifier/artifacts/classifier.onnx` finds
`models/classifier/classes.json`.

The session runs on the CUDA provider when onnxruntime offers it and on the CPU provider otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

#: The side of the square the model takes.
SIZE = 96

#: The white a crop is padded with.
PAD = 255

#: Mean and standard deviation of the grey channel: the ImageNet statistics averaged over the three
#: channels, which is what a single-channel image normalised as ImageNet is.
MEAN = 0.449
STD = 0.226

#: The class that carries every code point the classifier does not identify.
OTHER = "other"

#: The name of the class list, beside the export or one directory above it.
CLASSES_NAME = "classes.json"


class ClassifierError(RuntimeError):
    """An export or a class list that cannot be used."""


def grey(crop: Image.Image) -> Image.Image:
    """The crop as one channel on white.

    A crop with transparency is put on a white ground first, because dropping the alpha channel
    would read a transparent pixel as black.
    """
    image = crop
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        ground = Image.new("RGBA", image.size, (PAD, PAD, PAD, 255))
        ground.alpha_composite(image.convert("RGBA"))
        image = ground
    return image.convert("L")


def square(crop: Image.Image, *, pad: int = PAD) -> Image.Image:
    """The crop padded with `pad` to a square, centred, its aspect ratio unchanged."""
    side = max(crop.size)
    canvas = Image.new(crop.mode, (side, side), pad)
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas


def preprocess(crop: Image.Image, *, size: int = SIZE) -> Image.Image:
    """A crop as the grey `size` x `size` square the model takes.

    The character keeps its shape: the crop is padded to a square rather than stretched, so a wide
    character stays wide and the model never sees a glyph distorted differently from training.
    """
    if crop.width < 1 or crop.height < 1:
        raise ValueError(f"crop must have a positive size, got {crop.width}x{crop.height}")
    return square(grey(crop)).resize((size, size), Image.Resampling.BILINEAR)


def crop_array(crop: Image.Image, *, size: int = SIZE) -> np.ndarray:
    """A crop as the (1, 3, `size`, `size`) float32 tensor the model takes.

    The grey value is scaled to [0, 1], normalised with `MEAN` and `STD` and replicated to three
    channels, which is the input layout the timm backbone was trained on.
    """
    array = np.asarray(preprocess(crop, size=size), dtype=np.float32) / np.float32(255.0)
    array = (array - np.float32(MEAN)) / np.float32(STD)
    return np.ascontiguousarray(np.repeat(array[None], 3, axis=0)[None])


def array_to_crops(array: np.ndarray) -> list[Image.Image]:
    """Undo `crop_array`, for a caller that keeps tensors rather than images."""
    scaled = np.asarray(array).reshape(-1, 3, array.shape[-2], array.shape[-1])[:, 0]
    scaled = scaled * np.float32(STD) + np.float32(MEAN)
    values = np.clip(scaled * 255.0, 0.0, 255.0).astype(np.uint8)
    return [Image.fromarray(frame, mode="L") for frame in values]


def read_classes(path: Path | str) -> list[str]:
    """The class order of a `classes.json`, which is a list or an object with a `classes` key."""
    file = Path(path)
    document = json.loads(file.read_text(encoding="utf-8"))
    classes = document.get("classes") if isinstance(document, dict) else document
    if not isinstance(classes, list) or not classes or not all(isinstance(name, str) for name in classes):
        raise ClassifierError(f"{file}: expected a list of class names, or an object with a 'classes' list")
    if len(set(classes)) != len(classes):
        raise ClassifierError(f"{file}: the class list repeats a name")
    return list(classes)


def classes_for(onnx_path: Path | str, classes: Sequence[str] | Path | str | None = None) -> list[str]:
    """The class list of an export: what the caller gave, or `classes.json` near the export."""
    if classes is not None and not isinstance(classes, (str, Path)):
        return [str(name) for name in classes]
    if isinstance(classes, (str, Path)):
        return read_classes(classes)
    export = Path(onnx_path)
    for candidate in (export.parent / CLASSES_NAME, export.parent.parent / CLASSES_NAME):
        if candidate.is_file():
            return read_classes(candidate)
    raise ClassifierError(
        f"no {CLASSES_NAME} beside {export} or its parent; pass the class list to Classifier"
    )


def softmax(logits: np.ndarray) -> np.ndarray:
    """A numerically stable softmax over the last axis."""
    values = np.asarray(logits, dtype=np.float64)
    values = values - values.max(axis=-1, keepdims=True)
    exponent = np.exp(values)
    return exponent / exponent.sum(axis=-1, keepdims=True)


class Classifier:
    """A classifier over an exported ONNX model and its class list.

    `classes` is the class order, a list or the path of a `classes.json`; without it the file beside
    the export, or one directory above it, is read. `session` replaces the onnxruntime session, which
    tests use to run a stub. `providers` names the execution providers to try, in order; `size`
    overrides the side of the square the export takes.
    """

    def __init__(
        self,
        onnx_path: str | Path,
        *,
        classes: Sequence[str] | Path | str | None = None,
        session: Any | None = None,
        providers: Sequence[str] | None = None,
        size: int = SIZE,
    ) -> None:
        if size < 1:
            raise ValueError(f"size must be positive, got {size}")
        self.onnx_path = Path(onnx_path)
        self.classes = classes_for(self.onnx_path, classes)
        self.size = int(size)
        self._session = session if session is not None else self._build_session(providers)

    def _build_session(self, providers: Sequence[str] | None) -> Any:
        import onnxruntime as ort

        wanted = list(providers or ("CUDAExecutionProvider", "CPUExecutionProvider"))
        offered = ort.get_available_providers()
        usable = [name for name in wanted if name in offered]
        if not usable:
            raise RuntimeError(f"onnxruntime offers {offered}, none of {wanted}")
        # onnxruntime-gpu finds cuDNN and cuBLAS through the wheel's own copies or through
        # LD_LIBRARY_PATH; when they come from the nvidia packages, as they do here, the CUDA
        # provider fails until they are preloaded.
        preload = getattr(ort, "preload_dlls", None)
        if preload is not None and any(name.startswith("CUDA") for name in usable):
            try:
                preload()
            except (ImportError, OSError, RuntimeError):
                # A machine without the CUDA libraries keeps the CPU provider, and a session with
                # the CUDA provider still raises if it cannot use them.
                pass
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(str(self.onnx_path), sess_options=options, providers=usable)

    @property
    def other(self) -> str:
        """The name of the abstention class, `other`."""
        return OTHER

    @property
    def input_name(self) -> str:
        inputs = getattr(self._session, "get_inputs", None)
        if inputs is None:
            return "pixel_values"
        described = inputs()
        return described[0].name if described else "pixel_values"

    def providers(self) -> list[str]:
        """The providers the session actually runs on."""
        described = getattr(self._session, "get_providers", None)
        return list(described()) if described is not None else []

    def probabilities(self, crop: Image.Image | np.ndarray) -> np.ndarray:
        """The class probabilities of one crop, in the order of `self.classes`."""
        return self.probabilities_many([crop])[0]

    def probabilities_many(self, crops: Iterable[Image.Image | np.ndarray]) -> np.ndarray:
        """The class probabilities of a batch of crops, as an (n, classes) array."""
        images = [self._pixels(crop) for crop in crops]
        if not images:
            return np.zeros((0, len(self.classes)), dtype=np.float64)
        batch = np.concatenate(images, axis=0)
        outputs = self._session.run(None, {self.input_name: batch})
        described = getattr(self._session, "get_outputs", None)
        names = [item.name for item in described()] if described is not None else []
        if len(names) != len(outputs):
            names = [f"output{index}" for index in range(len(outputs))]
        lowered = {name.lower(): np.asarray(value) for name, value in zip(names, outputs, strict=True)}
        if "probs" in lowered:
            values = np.asarray(lowered["probs"], dtype=np.float64)
        else:
            logits = next((value for name, value in lowered.items() if "logit" in name), None)
            if logits is None:
                logits = np.asarray(outputs[0], dtype=np.float64)
            values = softmax(logits)
        values = values.reshape(len(images), -1)
        if values.shape[1] != len(self.classes):
            raise ClassifierError(
                f"{self.onnx_path}: the model returns {values.shape[1]} classes and "
                f"{CLASSES_NAME} names {len(self.classes)}"
            )
        return values

    def scores(self, crop: Image.Image | np.ndarray) -> dict[str, float]:
        """One probability per class of `classes.json`, `other` included.

        A `dict` is what the caller of an alignment wants: `score_set(crop, {"U+304B"})` reads one
        entry, and a caller that wants an identification takes the highest class that is not
        `other`.
        """
        values = self.probabilities(crop)
        return {name: float(value) for name, value in zip(self.classes, values, strict=True)}

    def score_set(self, crop: Image.Image | np.ndarray, code_points: set[str] | Iterable[str]) -> float:
        """The probability that the crop holds one of `code_points`.

        The probabilities of the class list are summed. A code point outside the class list has no
        probability of its own, because the classifier was not trained on it; the mass of the
        abstention class stands in for every such code point, and is added once when the set holds at
        least one of them. With no code point outside the class list the sum is over the classes
        asked for alone, so `score_set` over the whole class list is 1.
        """
        wanted = set(code_points)
        values = self.probabilities(crop)
        index = {name: position for position, name in enumerate(self.classes)}
        total = 0.0
        unknown = False
        for name in wanted:
            position = index.get(name)
            if position is None:
                unknown = True
            else:
                total += float(values[position])
        if unknown and OTHER in index:
            total += float(values[index[OTHER]])
        return total

    def top(self, crop: Image.Image | np.ndarray, k: int = 5) -> list[tuple[str, float]]:
        """The `k` likeliest classes, highest first, `other` included."""
        values = self.probabilities(crop)
        order = np.argsort(-values, kind="stable")[: max(1, k)]
        return [(self.classes[position], float(values[position])) for position in order]

    def _pixels(self, crop: Image.Image | np.ndarray) -> np.ndarray:
        if isinstance(crop, np.ndarray):
            array = np.asarray(crop, dtype=np.float32)
            if array.ndim == 2:
                return crop_array(Image.fromarray(array.astype(np.uint8), mode="L"), size=self.size)
            raise ValueError(f"a numpy crop must be a 2-D grey array, got shape {array.shape}")
        return crop_array(crop, size=self.size)
