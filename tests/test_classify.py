"""Tests for the ONNX character classifier.

No trained checkpoint and no network are needed. The model is a tiny ONNX graph built here from
random weights: it reads the (1, 3, 96, 96) tensor the real export takes, turns the mean of the
normalised crop into one logit per class and writes both the logits and their softmax. That is
enough to exercise the real onnxruntime path, the class list, `score_set` and the preprocessing, and
it is what the real export is a larger version of.

The preprocessing tests do not go through the model: they read the grey square `classify.preprocess`
returns and look for the bar a wide or a tall crop leaves in it, which is how the aspect ratio is
seen to be kept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from PIL import Image, ImageDraw

from glyph_atlas import classify
from glyph_atlas.classify import Classifier

CLASSES = ["U+304B", "U+304C", "U+4E00", "U+3005", classify.OTHER]
BLACK = 0
WHITE = 255


def write_tiny_classifier(
    path: Path,
    classes: list[str] = CLASSES,
    *,
    temperature: float = 1.0,
    with_probs: bool = True,
    seed: int = 0,
) -> Path:
    """Write an ONNX classifier of `len(classes)` outputs from random weights.

    The graph reads the crop the real export reads, reduces it to its mean, multiplies that by a
    random weight per class and softmaxes it. With `with_probs` it returns `logits` and `probs` as
    the real export does; without it, `logits` alone, which is the layout `Classifier` turns into a
    distribution itself.
    """
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(seed)
    count = len(classes)
    weights = rng.normal(0.0, 1.0, size=(1, count)).astype(np.float32)
    bias = rng.normal(0.0, 0.2, size=(1, count)).astype(np.float32)
    outputs = [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, count])]
    nodes = [
        helper.make_node("ReduceMean", ["pixel_values"], ["level"], axes=[1, 2, 3], keepdims=1),
        helper.make_node("Reshape", ["level", "shape"], ["flat"]),
        helper.make_node("MatMul", ["flat", "weights"], ["raw"]),
        helper.make_node("Add", ["raw", "bias"], ["logits"]),
    ]
    if with_probs:
        nodes += [
            helper.make_node("Div", ["logits", "temperature"], ["scaled"]),
            helper.make_node("Softmax", ["scaled"], ["probs"], axis=-1),
        ]
        outputs.append(helper.make_tensor_value_info("probs", TensorProto.FLOAT, [1, count]))
    graph = helper.make_graph(
        nodes,
        "tiny_classifier",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, [1, 3, classify.SIZE, classify.SIZE])],
        outputs,
        [
            numpy_helper.from_array(weights, "weights"),
            numpy_helper.from_array(bias, "bias"),
            numpy_helper.from_array(np.asarray([1, 1], dtype=np.int64), "shape"),
            numpy_helper.from_array(np.asarray(temperature, dtype=np.float32), "temperature"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def write_classes(path: Path, classes: list[str] = CLASSES, *, key: bool = True) -> Path:
    """Write a class list, as an object with a `classes` key or as a bare list."""
    import json

    document = {"classes": classes, "other": classify.OTHER, "size": classify.SIZE} if key else classes
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def crop(width: int, height: int, *, mode: str = "L", solid: bool = False) -> Image.Image:
    """A white crop of the given size, with a black bar across its top half or filled black."""
    image = Image.new(mode, (width, height), WHITE if mode != "RGBA" else (WHITE, WHITE, WHITE, 255))
    draw = ImageDraw.Draw(image)
    fill = (BLACK, BLACK, BLACK) if mode in ("RGB", "RGBA") else BLACK
    draw.rectangle((0, 0, width - 1, height - 1 if solid else max(0, height // 2 - 1)), fill=fill)
    return image


CROPS = [
    (96, 96),
    (60, 120),
    (120, 60),
    (40, 40),
    (200, 150),
    (12, 96),
    (96, 12),
    (137, 88),
    (300, 300),
    (7, 5),
]


def classifier(tmp_path: Path, **fields: object) -> Classifier:
    """A classifier over a tiny ONNX model in `tmp_path`, with its class list beside it."""
    path = write_tiny_classifier(tmp_path / "classifier.onnx", **fields)  # type: ignore[arg-type]
    write_classes(tmp_path / "classes.json")
    return Classifier(path, providers=["CPUExecutionProvider"])


def test_the_onnx_model_returns_distributions_that_sum_to_one(tmp_path: Path) -> None:
    """Ten crops of different shapes, every one a distribution over the class list."""
    write_classes(tmp_path / "classes.json")
    path = write_tiny_classifier(tmp_path / "classifier.onnx")
    reader = Classifier(path, providers=["CPUExecutionProvider"])
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    for width, height in CROPS:
        pixels = classify.crop_array(crop(width, height))
        assert pixels.shape == (1, 3, classify.SIZE, classify.SIZE)
        probs = np.asarray(session.run(["probs"], {"pixel_values": pixels})[0], dtype=np.float64)
        assert probs.shape == (1, len(CLASSES))
        assert probs.min() >= 0.0
        assert probs.sum() == pytest.approx(1.0, abs=1e-5)
        scores = reader.scores(crop(width, height))
        assert list(scores) == CLASSES
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-5)
        assert reader.probabilities(crop(width, height)).sum() == pytest.approx(1.0, abs=1e-5)


def test_score_set_over_every_class_is_one(tmp_path: Path) -> None:
    reader = classifier(tmp_path)
    for width, height in CROPS[:4]:
        image = crop(width, height)
        assert reader.score_set(image, set(CLASSES)) == pytest.approx(1.0, abs=1e-5)
        scores = reader.scores(image)
        assert reader.score_set(image, {"U+304B"}) == pytest.approx(scores["U+304B"], abs=1e-9)
        assert reader.score_set(image, {"U+304B", "U+304C"}) == pytest.approx(
            scores["U+304B"] + scores["U+304C"], abs=1e-9
        )
        assert reader.score_set(image, set()) == 0.0
        # A code point the classifier was never trained on has no probability of its own: the
        # abstention class stands in for it.
        assert reader.score_set(image, {"U+FFFF"}) == pytest.approx(scores[classify.OTHER], abs=1e-9)
        assert reader.score_set(image, {"U+304B", "U+FFFF"}) == pytest.approx(
            scores["U+304B"] + scores[classify.OTHER], abs=1e-9
        )


def test_preprocessing_is_grey_and_square() -> None:
    square = classify.preprocess(crop(60, 120, mode="RGB"))
    assert square.mode == "L"
    assert square.size == (classify.SIZE, classify.SIZE)
    pixels = classify.crop_array(crop(60, 120))
    assert pixels.dtype == np.float32
    assert pixels.shape == (1, 3, classify.SIZE, classify.SIZE)
    # The three channels are the same grey one.
    assert np.array_equal(pixels[0, 0], pixels[0, 1])
    assert np.array_equal(pixels[0, 1], pixels[0, 2])


def test_preprocessing_keeps_the_aspect_ratio() -> None:
    """A wide crop stays wide and a tall crop stays tall: the pad is white and the content is centred."""
    wide = np.asarray(classify.preprocess(crop(120, 40, solid=True)))
    rows = np.flatnonzero((wide < 250).any(axis=1))
    columns = np.flatnonzero((wide < 250).any(axis=0))
    # 40 of 120 rows is 32 of the 96 the crop is padded and resized to, centred on the square.
    assert rows.max() - rows.min() + 1 == pytest.approx(classify.SIZE * 40 / 120, abs=2)
    assert (rows.min() + rows.max()) / 2 == pytest.approx((classify.SIZE - 1) / 2, abs=1)
    assert columns.min() == 0
    assert columns.max() == classify.SIZE - 1
    assert wide[0, 0] >= 250  # the padding above the crop is white

    tall = np.asarray(classify.preprocess(crop(40, 120, solid=True)))
    rows = np.flatnonzero((tall < 250).any(axis=1))
    columns = np.flatnonzero((tall < 250).any(axis=0))
    assert columns.max() - columns.min() + 1 == pytest.approx(classify.SIZE * 40 / 120, abs=2)
    assert (columns.min() + columns.max()) / 2 == pytest.approx((classify.SIZE - 1) / 2, abs=1)
    assert rows.min() == 0
    assert rows.max() == classify.SIZE - 1

    # A crop that is already square is not padded at all.
    even = np.asarray(classify.preprocess(crop(96, 96, solid=True)))
    assert (even < 250).all()


def test_a_tall_and_a_wide_crop_are_not_the_same_image() -> None:
    """Stretching both to the square would make these identical; padding keeps them apart."""
    wide = np.asarray(classify.preprocess(crop(120, 40, solid=True)))
    tall = np.asarray(classify.preprocess(crop(120, 40).transpose(Image.Transpose.ROTATE_90)))
    assert not np.array_equal(wide, tall)


def test_alpha_is_put_on_white(tmp_path: Path) -> None:
    """A transparent crop reads as white paper, not as the black a dropped alpha would give."""
    transparent = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    assert np.asarray(classify.grey(transparent)).min() >= 250
    # Pillow's grey conversion is ITU-R 601-2: 0.299 * 10 + 0.587 * 20 + 0.114 * 30.
    assert np.asarray(classify.grey(Image.new("RGB", (96, 96), (10, 20, 30)))).mean() == pytest.approx(
        18.0, abs=1.0
    )


def test_the_class_list_is_read_beside_the_export(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = write_tiny_classifier(artifacts / "classifier.onnx")
    write_classes(tmp_path / "classes.json")
    reader = Classifier(path, providers=["CPUExecutionProvider"])
    assert reader.classes == CLASSES
    assert reader.other == classify.OTHER
    assert reader.providers() == ["CPUExecutionProvider"]
    assert reader.input_name == "pixel_values"
    # A bare list is a class list too, and so is a list given directly.
    write_classes(tmp_path / "classes.json", key=False)
    assert Classifier(path, providers=["CPUExecutionProvider"]).classes == CLASSES
    assert Classifier(path, classes=["a", "b"], providers=["CPUExecutionProvider"]).classes == ["a", "b"]


def test_a_class_list_of_the_wrong_length_is_refused(tmp_path: Path) -> None:
    path = write_tiny_classifier(tmp_path / "classifier.onnx")
    with pytest.raises(classify.ClassifierError):
        Classifier(path, classes=CLASSES[:2], providers=["CPUExecutionProvider"]).scores(crop(96, 96))


def test_a_model_without_probabilities_is_softmaxed(tmp_path: Path) -> None:
    path = write_tiny_classifier(tmp_path / "classifier.onnx", with_probs=False)
    reader = Classifier(path, classes=CLASSES, providers=["CPUExecutionProvider"])
    scores = reader.scores(crop(96, 96))
    assert sum(scores.values()) == pytest.approx(1.0, abs=1e-5)
    assert reader.score_set(crop(96, 96), set(CLASSES)) == pytest.approx(1.0, abs=1e-5)


def test_a_missing_class_list_is_reported(tmp_path: Path) -> None:
    path = write_tiny_classifier(tmp_path / "classifier.onnx")
    with pytest.raises(classify.ClassifierError):
        Classifier(path, providers=["CPUExecutionProvider"])


def test_the_export_reader_accepts_what_the_training_loop_feeds_it(tmp_path: Path) -> None:
    """A grey numpy crop and a PIL crop of the same pixels give the same distribution."""
    reader = classifier(tmp_path)
    image = crop(80, 60)
    from_array = reader.scores(np.asarray(image.convert("L")))
    from_image = reader.scores(image)
    assert from_array == pytest.approx(from_image, abs=1e-6)
    with pytest.raises(ValueError):
        reader.scores(np.zeros((3, 96, 96), dtype=np.uint8))


def test_top_returns_the_likeliest_classes_first(tmp_path: Path) -> None:
    reader = classifier(tmp_path)
    scores = reader.scores(crop(96, 96))
    ranked = reader.top(crop(96, 96), 3)
    assert len(ranked) == 3
    assert ranked[0][0] == max(scores, key=lambda name: scores[name])
    assert ranked[0][1] == pytest.approx(max(scores.values()), abs=1e-9)
    assert [value for _, value in ranked] == sorted((value for _, value in ranked), reverse=True)


def test_preprocessing_refuses_an_empty_crop() -> None:
    with pytest.raises(ValueError):
        classify.preprocess(Image.new("L", (1, 1)).crop((0, 0, 0, 0)))
    with pytest.raises(ValueError):
        Classifier("stub.onnx", classes=CLASSES, session=object(), size=0)


def test_the_size_is_read_from_the_export(tmp_path: Path) -> None:
    """An export at 128 is fed 128-square crops; one whose input names no size gets the default."""
    from types import SimpleNamespace

    def session(shape):
        return SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name="pixel_values", shape=shape)])

    wide = Classifier(tmp_path / "x.onnx", classes=CLASSES, session=session(["batch", 3, 128, 128]))
    assert wide.size == 128
    assert wide._pixels(crop(40, 60)).shape == (1, 3, 128, 128)
    assert Classifier(tmp_path / "x.onnx", classes=CLASSES, session=session(["batch", 3, "h", "w"])).size == classify.SIZE


def test_a_run_refuses_a_classifier_it_was_not_measured_with(tmp_path: Path) -> None:
    import hashlib

    from glyph_atlas.align import Run, check_classifier

    export = tmp_path / "classifier.onnx"
    export.write_bytes(b"one model")
    run = Run(name="pinned", classifier=str(export), classifier_sha256=hashlib.sha256(b"one model").hexdigest())
    check_classifier(run)
    export.write_bytes(b"another model")
    with pytest.raises(ValueError, match="not the classifier"):
        check_classifier(run)
    assert run.fingerprint() == run.model_copy(update={"classifier_sha256": None}).fingerprint()


def test_a_classifier_handed_to_a_run_is_held_to_its_pin(tmp_path: Path) -> None:
    import hashlib
    from types import SimpleNamespace

    from glyph_atlas import align

    pinned, other = tmp_path / "pinned.onnx", tmp_path / "other.onnx"
    pinned.write_bytes(b"one model")
    other.write_bytes(b"another model")
    run = align.Run(name="pinned", classifier=str(pinned), classifier_sha256=hashlib.sha256(b"one model").hexdigest())
    with pytest.raises(ValueError, match="not the classifier"):
        align.run_directory(tmp_path, run, pages=[], detector=object(), classifier=SimpleNamespace(onnx_path=other))


def test_a_free_batch_runs_in_chunks(tmp_path: Path) -> None:
    """A line of a hundred detections never reaches the graph as one batch."""
    from types import SimpleNamespace

    runs = []

    def run(_names, feed):
        batch = feed["pixel_values"]
        runs.append(len(batch))
        return [np.full((len(batch), len(CLASSES)), 1 / len(CLASSES), dtype=np.float32)]

    session = SimpleNamespace(run=run, get_inputs=lambda: [SimpleNamespace(name="pixel_values", shape=["batch", 3, 96, 96])],
                              get_outputs=lambda: [SimpleNamespace(name="probs")])
    reader = Classifier(tmp_path / "x.onnx", classes=CLASSES, session=session)
    assert reader.probabilities_many([crop(30, 40)] * 100).shape == (100, len(CLASSES))
    assert max(runs) <= classify.CHUNK and sum(runs) == 100
