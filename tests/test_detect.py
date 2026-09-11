"""Tests for the ONNX character detector.

No trained checkpoint and no network are needed. Two stand-ins are used: `ThresholdSession`, a stub
session that boxes the dark pixels of a tile the way a trained detector would, and a tiny ONNX model
of constant outputs built with `onnx` in `tmp_path`, which exercises the real onnxruntime path.

Tiles are 1024 px square with 128 px of overlap, so a page has tile origins at multiples of 896 and
the tiles at the right and bottom edge are padded with white.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from PIL import Image, ImageDraw

from glyph_atlas import detect
from glyph_atlas.detect import Detector
from glyph_atlas.schema import Box

BLACK = 0
WHITE = 255
SIZE = 1024


class ThresholdSession:
    """A stub session that returns the bounding box of the dark pixels of a tile.

    The page is white and what should be found is drawn in black, so the stub produces the same
    clipped detection a trained detector produces in each tile it meets. It counts its calls, which
    the tests use to see which tiles were run.
    """

    def __init__(self, score: float = 0.9) -> None:
        self.score = score
        self.calls = 0
        self.tiles: list[tuple[float, float]] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="pixel_values")]

    def get_outputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="dets")]

    def get_providers(self) -> list[str]:
        return ["CPUExecutionProvider"]

    def run(self, output_names: object, feeds: dict[str, object]) -> list[object]:
        array = np.asarray(feeds["pixel_values"], dtype=np.float32)[0]
        self.calls += 1
        dark = (array < 0.0).any(axis=0)
        rows, columns = np.nonzero(dark)
        if rows.size == 0:
            return [np.zeros((1, 0, 5), dtype=np.float32)]
        self.tiles.append((float(columns.min()), float(rows.min())))
        dets = np.array(
            [
                [
                    columns.min(),
                    rows.min(),
                    columns.max() + 1,
                    rows.max() + 1,
                    self.score,
                ]
            ],
            dtype=np.float32,
        )
        return [dets[None]]


def page(width: int, height: int, squares: list[tuple[int, int, int, int]]) -> Image.Image:
    """A white page with black squares at `x, y, w, h`, the shapes the stub session finds."""
    image = Image.new("RGB", (width, height), (WHITE, WHITE, WHITE))
    draw = ImageDraw.Draw(image)
    for x, y, w, h in squares:
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(BLACK, BLACK, BLACK))
    return image


def write_tiny_detector(
    path: Path, queries: list[tuple[float, float, float, float, float]], size: int = SIZE
) -> Path:
    """Write an ONNX model that returns `x1, y1, x2, y2, logit` queries in tile pixels.

    The boxes and the logits are constants; the input is read and multiplied by zero so that the
    graph keeps the input the detector feeds, as an exported model does.
    """
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    boxes = np.zeros((1, len(queries), 4), dtype=np.float32)
    logits = np.zeros((1, len(queries), 1), dtype=np.float32)
    for index, (x1, y1, x2, y2, logit) in enumerate(queries):
        boxes[0, index] = [
            (x1 + x2) / 2 / size,
            (y1 + y2) / 2 / size,
            (x2 - x1) / size,
            (y2 - y1) / size,
        ]
        logits[0, index, 0] = logit
    nodes = [
        helper.make_node("ReduceMean", ["pixel_values"], ["mean"], axes=[1, 2, 3], keepdims=1),
        helper.make_node("ReduceSum", ["mean"], ["level"], keepdims=0),
        helper.make_node("Mul", ["level", "zero"], ["nothing"]),
        helper.make_node("Add", ["box_values", "nothing"], ["pred_boxes"]),
        helper.make_node("Add", ["logit_values", "nothing"], ["logits"]),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny_detector",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, [1, 3, size, size])],
        [
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, len(queries), 1]),
            helper.make_tensor_value_info("pred_boxes", TensorProto.FLOAT, [1, len(queries), 4]),
        ],
        [
            numpy_helper.from_array(boxes, "box_values"),
            numpy_helper.from_array(logits, "logit_values"),
            numpy_helper.from_array(np.zeros((), dtype=np.float32), "zero"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def detector(session: ThresholdSession, **fields: object) -> Detector:
    return Detector("stub.onnx", session=session, **fields)  # type: ignore[arg-type]


def boxes_of(found: list[tuple[Box, float]]) -> list[Box]:
    return [box for box, _ in found]


def scores_of(found: list[tuple[Box, float]]) -> list[float]:
    return [score for _, score in found]


def test_geometry_is_the_t20_one() -> None:
    assert (detect.TILE, detect.OVERLAP, detect.STRIDE) == (1024, 128, 896)
    assert detect.tile_origins(1024) == [0]
    assert detect.tile_origins(1025) == [0, 896]
    assert detect.tile_origins(1500) == [0, 896]
    assert detect.tile_origins(1920) == [0, 896]
    assert detect.tile_origins(1921) == [0, 896, 1792]


def test_tile_origins_cover_the_page_without_a_gap() -> None:
    for length in (1, 1023, 1024, 1400, 1920, 1921, 3000):
        origins = detect.tile_origins(length)
        assert origins[0] == 0
        assert origins[-1] + detect.TILE >= length
        for left, right in pairwise(origins):
            assert right - left == detect.STRIDE <= detect.TILE


def test_tiles_of_a_1500_by_1200_page() -> None:
    assert detect.tiles(1500, 1200) == [
        Box(x=0, y=0, w=1024, h=1024),
        Box(x=896, y=0, w=1024, h=1024),
        Box(x=0, y=896, w=1024, h=1024),
        Box(x=896, y=896, w=1024, h=1024),
    ]
    assert detect.tiles(800, 600) == [Box(x=0, y=0, w=1024, h=1024)]


def test_edge_tiles_are_padded_with_white() -> None:
    image = page(1500, 1200, [])
    array = detect.tile_array(image, Box(x=896, y=896, w=1024, h=1024))
    assert array.shape == (1, 3, SIZE, SIZE)
    white = (np.float32(WHITE) / 255 - np.asarray(detect.MEAN)) / np.asarray(detect.STD)
    # The page ends 604 px into the tile horizontally and 304 px vertically.
    assert np.allclose(array[0, :, 304:, 604:], white[:, None, None], atol=1e-6)
    assert np.allclose(array[0, :, :304, :604], white[:, None, None], atol=1e-6)
    # A page smaller than one tile is padded on both sides.
    small = detect.tile_array(page(800, 600, []), Box(x=0, y=0, w=1024, h=1024))
    assert np.allclose(small[0, :, 600:, :], white[:, None, None], atol=1e-6)
    assert np.allclose(small[0, :, :, 800:], white[:, None, None], atol=1e-6)


def test_boxes_are_returned_in_page_coordinates() -> None:
    image = page(1200, 1200, [(100, 200, 80, 60), (1050, 1050, 90, 70)])
    session = ThresholdSession()
    found = detector(session).boxes(image)
    assert boxes_of(found) == [Box(x=100, y=200, w=80, h=60), Box(x=1050, y=1050, w=90, h=70)]
    assert scores_of(found) == pytest.approx([0.9, 0.9])
    # The square at x 1050 lies in the second row and column of tiles only.
    assert session.tiles == [(100.0, 200.0), (154.0, 154.0)]


def test_a_box_on_a_tile_border_is_returned_once() -> None:
    image = page(1200, 1200, [(850, 300, 100, 60)])
    session = ThresholdSession()
    found = detector(session).boxes(image)
    # Both the tile at 0 and the tile at 896 see part of the square; the second is suppressed.
    assert session.calls == 4
    assert boxes_of(found) == [Box(x=850, y=300, w=100, h=60)]
    assert scores_of(found) == pytest.approx([0.9])


def test_a_box_on_a_horizontal_border_is_returned_once() -> None:
    image = page(1200, 1200, [(400, 860, 70, 100)])
    assert boxes_of(detector(ThresholdSession()).boxes(image)) == [Box(x=400, y=860, w=70, h=100)]


def test_a_box_in_a_corner_of_four_tiles_is_returned_once() -> None:
    image = page(1200, 1200, [(860, 860, 80, 80)])
    assert boxes_of(detector(ThresholdSession()).boxes(image)) == [Box(x=860, y=860, w=80, h=80)]


def test_boxes_in_returns_page_coordinates_of_one_region() -> None:
    image = page(1200, 1200, [(100, 200, 80, 60), (1050, 1050, 90, 70)])
    found = detector(ThresholdSession())
    whole = found.boxes(image)
    region = Box(x=1000, y=1000, w=200, h=200)
    inside = found.boxes_in(image, region)
    assert boxes_of(inside) == [Box(x=1050, y=1050, w=90, h=70)]
    assert scores_of(inside) == pytest.approx([0.9])
    assert inside == [pair for pair in whole if detect.overlap(pair[0], region)]


def test_boxes_in_runs_the_tiles_the_region_meets() -> None:
    image = page(1200, 1200, [(100, 200, 80, 60)])
    session = ThresholdSession()
    found = detector(session)
    found.boxes_in(image, Box(x=0, y=0, w=100, h=100))
    assert session.calls == 1
    session.calls = 0
    found.boxes(image)
    assert session.calls == 4


def test_boxes_in_a_region_the_model_finds_nothing_in() -> None:
    image = page(1200, 1200, [(100, 200, 80, 60)])
    assert detector(ThresholdSession()).boxes_in(image, Box(x=0, y=0, w=50, h=50)) == []


def test_a_region_outside_the_box_of_a_detection_is_excluded() -> None:
    image = page(1200, 1200, [(100, 200, 80, 60)])
    found = detector(ThresholdSession())
    assert found.boxes_in(image, Box(x=300, y=300, w=50, h=50)) == []
    overlapping = found.boxes_in(image, Box(x=170, y=250, w=20, h=20))
    assert boxes_of(overlapping) == [Box(x=100, y=200, w=80, h=60)]


def test_a_page_smaller_than_one_tile() -> None:
    image = page(700, 500, [(600, 400, 60, 50)])
    assert boxes_of(detector(ThresholdSession()).boxes(image)) == [Box(x=600, y=400, w=60, h=50)]


def test_a_page_may_be_a_path(tmp_path: Path) -> None:
    page(700, 500, [(600, 400, 60, 50)]).save(tmp_path / "page.png")
    found = detector(ThresholdSession()).boxes(tmp_path / "page.png")
    assert boxes_of(found) == [Box(x=600, y=400, w=60, h=50)]


def test_an_onnx_model_returns_boxes(tmp_path: Path) -> None:
    path = write_tiny_detector(
        tmp_path / "tiny.onnx",
        [(100.0, 200.0, 180.0, 260.0, 2.0), (400.0, 400.0, 500.0, 500.0, -4.0)],
    )
    found = Detector(path, score=0.3, providers=["CPUExecutionProvider"])
    assert found.providers() == ["CPUExecutionProvider"]
    image = page(1200, 1200, [])
    boxes = found.boxes(image)
    # Four tiles, one query above the threshold, in tile coordinates mapped to page coordinates.
    assert boxes == [
        (Box(x=100, y=200, w=80, h=60), pytest.approx(0.8808, abs=1e-4)),
        (Box(x=996, y=200, w=80, h=60), pytest.approx(0.8808, abs=1e-4)),
        (Box(x=100, y=1096, w=80, h=60), pytest.approx(0.8808, abs=1e-4)),
        (Box(x=996, y=1096, w=80, h=60), pytest.approx(0.8808, abs=1e-4)),
    ]
    assert found.boxes_in(image, Box(x=0, y=0, w=500, h=500)) == [
        (Box(x=100, y=200, w=80, h=60), pytest.approx(0.8808, abs=1e-4))
    ]


def test_max_per_tile_keeps_the_best_scores(tmp_path: Path) -> None:
    path = write_tiny_detector(
        tmp_path / "tiny.onnx",
        [(100.0, 100.0, 200.0, 200.0, 1.0), (400.0, 400.0, 500.0, 500.0, 3.0)],
    )
    image = page(1024, 1024, [])
    found = Detector(path, providers=["CPUExecutionProvider"], max_per_tile=1)
    assert found.boxes(image) == [(Box(x=400, y=400, w=100, h=100), pytest.approx(0.9526, abs=1e-4))]
    both = Detector(path, providers=["CPUExecutionProvider"]).boxes(image)
    assert len(both) == 2


def test_the_score_threshold_drops_weak_detections(tmp_path: Path) -> None:
    path = write_tiny_detector(tmp_path / "tiny.onnx", [(100.0, 100.0, 200.0, 200.0, -2.0)])
    assert Detector(path, score=0.3, providers=["CPUExecutionProvider"]).boxes(page(1024, 1024, [])) == []
    weak = Detector(path, score=0.05, providers=["CPUExecutionProvider"]).boxes(page(1024, 1024, []))
    assert weak == [(Box(x=100, y=100, w=100, h=100), pytest.approx(0.1192, abs=1e-4))]


def test_the_stub_can_speak_the_rtdetr_layout() -> None:
    class RtdetrSession(ThresholdSession):
        def get_outputs(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="logits"), SimpleNamespace(name="pred_boxes")]

        def run(self, output_names: object, feeds: dict[str, object]) -> list[object]:
            dets = super().run(output_names, feeds)[0]
            dets = np.asarray(dets)[0]
            if dets.size == 0:
                return [
                    np.zeros((1, 3, 1), dtype=np.float32),
                    np.zeros((1, 3, 4), dtype=np.float32),
                ]
            x1, y1, x2, y2 = (float(value) for value in dets[0, :4])
            logits = np.full((1, 3, 1), -10.0, dtype=np.float32)
            logits[0, 0, 0] = 2.0
            boxes = np.zeros((1, 3, 4), dtype=np.float32)
            boxes[0, 0] = [
                (x1 + x2) / 2 / SIZE,
                (y1 + y2) / 2 / SIZE,
                (x2 - x1) / SIZE,
                (y2 - y1) / SIZE,
            ]
            return [logits, boxes]

    found = detector(RtdetrSession()).boxes(page(700, 500, [(600, 400, 60, 50)]))
    assert found == [(Box(x=600, y=400, w=60, h=50), pytest.approx(0.8808, abs=1e-4))]


def test_arguments_are_checked() -> None:
    with pytest.raises(ValueError):
        Detector("stub.onnx", score=1.5, session=ThresholdSession())
    with pytest.raises(ValueError):
        Detector("stub.onnx", nms=-0.1, session=ThresholdSession())
    with pytest.raises(ValueError):
        Detector("stub.onnx", max_per_tile=0, session=ThresholdSession())
    with pytest.raises(ValueError):
        Detector("stub.onnx", format="yolo", session=ThresholdSession())
    with pytest.raises(ValueError):
        detect.tile_origins(0)
    with pytest.raises(ValueError):
        detect.tiles(0, 100)
