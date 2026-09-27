"""Tests for `glyph_atlas.line_match`: OCR line matching without real models or network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from glyph_atlas import images, line_match, tables
from glyph_atlas.schema import Box, Line, Page


def page(page_id: str = "doc:1", width: int = 100, height: int = 80) -> Page:
    return Page(id=page_id, document_id="doc", seq=0, canvas="c", image=f"cache://{page_id}", width=width, height=height)


def line(seq: int, text: str, *, box: Box | None = None, method: str | None = None) -> Line:
    return Line(id=f"doc:1:L{seq}", page_id="doc:1", seq=seq, text_raw=text, text=text, box=box, match_method=method)


def cached_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item: Page, *, size: tuple[int, int] | None = None) -> None:
    monkeypatch.setenv(images.ENV_CACHE, str(tmp_path / "cache"))
    path = tmp_path / f"{item.id.replace(':', '_')}.png"
    Image.new("RGB", size or (item.width, item.height), (240, 240, 240)).save(path)
    images.register(path, item.image)


def write_dataset(directory: Path, item: Page, lines: list[Line]) -> None:
    directory.mkdir()
    tables.write(directory / "pages.parquet", [item], Page)
    tables.write(directory / "lines.parquet", lines, Line)


class FakeDetector:
    def __init__(self, detections: list[line_match.Detection]) -> None:
        self.detections = detections

    def boxes(self, path: Path) -> list[line_match.Detection]:
        return self.detections


class FakeRecognizer:
    def __init__(self, texts: dict[int, str]) -> None:
        self.texts = texts

    def __call__(self, image: Image.Image, box: Box) -> str:
        return self.texts[box.x]


def detection(x: int, text_class: str = "line_main") -> line_match.Detection:
    return line_match.Detection(box=Box(x=x, y=10, w=20, h=30), score=0.9, class_id=1, class_name=text_class)


def test_levenshtein_and_normalisation() -> None:
    assert line_match.levenshtein("abc", "abc") == 0
    assert line_match.normalised_distance("abc", "abc") == 0
    assert line_match.levenshtein("abc", "xyz") == 3
    assert line_match.normalised_distance("abc", "xyz") == 1
    assert line_match.normalised_distance("", "abcd") == 1


def test_assignment_is_globally_cheapest() -> None:
    costs = np.asarray([[1, 2, 100], [2, 100, 3], [100, 3, 4]], dtype=float)

    assert sorted(line_match.assign(costs)) == [(0, 0), (1, 2), (2, 1)]


def test_acceptance_gate_checks_distance_and_length_ratio() -> None:
    good = line_match.Match(line(0, "abcd"), line_match.ReadLine(detection(0), "abce"), 0.25, 0.75)
    assert not good.accepted
    too_far = line_match.Match(line(0, "abcd"), line_match.ReadLine(detection(0), "wxyz"), 1.0, 1.0)
    assert not too_far.accepted
    accepted = line_match.Match(line(0, "abcd"), line_match.ReadLine(detection(0), "abce"), 0.25, 1.0)
    assert accepted.accepted


def test_existing_box_is_never_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page()
    cached_page(tmp_path, monkeypatch, item)
    original = Box(x=1, y=2, w=3, h=4)
    directory = tmp_path / "data"
    write_dataset(directory, item, [line(0, "perfect", box=original, method="ainu-ink-columns-v2")])

    counts = line_match.match_dataset(
        directory,
        detector=FakeDetector([detection(10)]),
        recognizer=FakeRecognizer({10: "perfect"}),
        out=tmp_path / "report.tsv",
    )

    after = tables.read(directory / "lines.parquet", Line)[0]
    assert counts["matched"] == 0
    assert after.box == original
    assert after.match_method == "ainu-ink-columns-v2"


def test_rerun_withdraws_only_this_methods_boxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page()
    cached_page(tmp_path, monkeypatch, item)
    own = line(0, "abc", box=Box(x=10, y=10, w=20, h=30), method=line_match.METHOD)
    own.match_confidence = 1.0
    own.meta = {"line_match": {"ocr_text": "abc"}, "keep": "yes"}
    other = line(1, "def", box=Box(x=40, y=10, w=20, h=30), method="manual")
    directory = tmp_path / "data"
    write_dataset(directory, item, [own, other])

    counts = line_match.match_dataset(
        directory,
        detector=FakeDetector([]),
        recognizer=FakeRecognizer({}),
        out=tmp_path / "report.tsv",
    )

    first, second = tables.read(directory / "lines.parquet", Line)
    assert counts["withdrawn"] == 1
    assert first.box is None and first.match_method is None and first.match_confidence is None
    assert first.meta == {"keep": "yes"}
    assert second.box == Box(x=40, y=10, w=20, h=30)
    assert second.match_method == "manual"


def test_image_size_mismatch_skips_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page(width=100, height=80)
    cached_page(tmp_path, monkeypatch, item, size=(50, 80))
    directory = tmp_path / "data"
    write_dataset(directory, item, [line(0, "abc")])

    counts = line_match.match_dataset(
        directory,
        detector=FakeDetector([detection(10)]),
        recognizer=FakeRecognizer({10: "abc"}),
        out=tmp_path / "report.tsv",
    )

    after = tables.read(directory / "lines.parquet", Line)[0]
    report = (tmp_path / "report.tsv").read_text(encoding="utf-8")
    assert counts["failed"] == 1
    assert after.box is None
    assert "image size mismatch" in report


def test_detector_uses_session_shape_and_preserves_all_raw_labels() -> None:
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="input", shape=[1, 3, 100, 200])]

        def run(self, outputs, feed):
            pixels = feed["input"]
            assert pixels.shape == (1, 3, 100, 200)
            assert pixels.dtype == np.float32
            np.testing.assert_allclose(pixels[0, :, 0, 0], ([30, 20, 10] - line_match.MEAN_BGR) / line_match.STD_BGR)
            np.testing.assert_allclose(pixels[0, :, -1, -1], -line_match.MEAN_BGR / line_match.STD_BGR)
            return [
                np.asarray([[[10, 10, 30, 60, .9], [40, 10, 60, 60, .8],
                             [70, 10, 90, 60, .7], [100, 10, 120, 60, .3]]]),
                np.asarray([[0, 5, 99, 0]]),
            ]

    detector = line_match.LineDetector(session=Session())
    detections = detector.boxes(Image.new("RGB", (400, 300), (10, 20, 30)))
    assert [d.class_id for d in detections] == [0, 5, 99]
    assert {d.class_name for d in detections} == {"line_main"}
    assert detections[0].box == Box(x=20, y=36, w=40, h=208)


def test_rerun_keeps_matches_and_preserves_raw_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page()
    cached_page(tmp_path, monkeypatch, item)
    directory = tmp_path / "data"
    write_dataset(directory, item, [line(0, "perfect")])
    found = line_match.Detection(box=Box(x=10, y=10, w=20, h=30), score=.9, class_id=0)
    args = {"detector": FakeDetector([found]), "recognizer": FakeRecognizer({10: "perfect"})}
    for _ in range(2):
        assert line_match.match_dataset(directory, **args)["matched"] == 1
        after = tables.read(directory / "lines.parquet", Line)[0]
        assert after.box == found.box
        assert after.meta["line_match"]["class_id"] == 0

    after.box = Box(x=1, y=2, w=3, h=4)
    tables.write(directory / "lines.parquet", [after], Line)
    assert line_match.match_dataset(directory, **args)["withdrawn"] == 0
    assert tables.read(directory / "lines.parquet", Line)[0].box == after.box


def test_evaluation_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page()
    cached_page(tmp_path, monkeypatch, item)
    found = detection(10)
    directory = tmp_path / "data"
    write_dataset(directory, item, [line(0, "perfect", box=found.box)])
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = line_match.evaluate_dataset(
        directory, detector=FakeDetector([found]), recognizer=FakeRecognizer({10: "perfect"}),
    )
    assert result["matched_share"] == result["iou_ge_0.5_share"] == result["median_iou"] == 1
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_commit_drops_stale_proposals(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    original = line(0, "perfect")
    edited = original.model_copy(update={"box": Box(x=1, y=2, w=3, h=4), "match_method": "manual"})
    write_dataset(directory, page(), [edited])
    result = line_match._commit(directory, {original.id: {
        "was": (None, None), "box": detection(10).box,
        "line_match": {}, "match_confidence": 1,
    }})
    assert result["stale"] == 1
    assert tables.read(directory / "lines.parquet", Line)[0] == edited


def test_old_label_filtered_cache_is_invalidated(tmp_path: Path) -> None:
    path = tmp_path / "detections.jsonl"
    settings = line_match.detector_settings(tmp_path / "fake.onnx")
    line_match._write_cache(path, {"doc:1": []}, settings={**settings, "classes": [1, 2, 3, 4]})
    assert line_match._read_cache(path, settings=settings) == {}
    line_match._write_cache(path, {"doc:1": [line_match.Detection(detection(10).box, .9, 0)]}, settings=settings)
    assert line_match._read_cache(path, settings=settings)["doc:1"][0].class_id == 0


def test_owned_detector_is_released_before_recognition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import weakref

    item = page()
    cached_page(tmp_path, monkeypatch, item)
    directory = tmp_path / "data"
    write_dataset(directory, item, [line(0, "perfect")])
    references = []

    def make_detector(*args, **kwargs):
        detector = FakeDetector([detection(10)])
        references.append(weakref.ref(detector))
        return detector

    def make_recognizer():
        assert references and references[0]() is None
        return FakeRecognizer({10: "perfect"})

    monkeypatch.setattr(line_match, "detector_for", make_detector)
    monkeypatch.setattr(line_match, "recognizer_for", make_recognizer)
    assert line_match.match_dataset(directory)["matched"] == 1


def test_withdrawing_a_box_retires_the_machine_units_on_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from glyph_atlas.schema import Unit

    item = page()
    cached_page(tmp_path, monkeypatch, item)
    own = line(0, "abc", box=Box(x=10, y=10, w=20, h=30), method=line_match.METHOD)
    own.meta = {"line_match": {"ocr_text": "abc"}}
    directory = tmp_path / "data"
    write_dataset(directory, item, [own])
    placed = Unit(id="u1", line_id=own.id, page_id=item.id, text_source="a", box=Box(x=10, y=10, w=20, h=10),
                  method="detect-align")
    tables.write(directory / "units.parquet", [placed], Unit)

    counts = line_match.match_dataset(directory, detector=FakeDetector([]), recognizer=FakeRecognizer({}),
                                      out=tmp_path / "report.tsv")

    assert counts["units-retired"] == 1
    assert not tables.read(directory / "units.parquet", Unit)[0].active


def test_a_boxed_lines_ink_is_not_offered_to_another_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = page()
    cached_page(tmp_path, monkeypatch, item)
    boxed = line(0, "abc", box=Box(x=10, y=10, w=20, h=30), method="manual")
    unboxed = line(1, "abd")
    directory = tmp_path / "data"
    write_dataset(directory, item, [boxed, unboxed])

    counts = line_match.match_dataset(directory, detector=FakeDetector([detection(10)]),
                                      recognizer=FakeRecognizer({10: "abc"}), out=tmp_path / "report.tsv")

    assert counts["matched"] == 0
    assert tables.read(directory / "lines.parquet", Line)[1].box is None


def test_a_page_scoped_line_is_never_a_candidate() -> None:
    scoped = line(0, "abc")
    scoped.meta = {"scope": "page"}
    assert line_match.candidate_lines([scoped, line(1, "def")]) == [line(1, "def")]


def test_a_whole_line_is_decoded_past_the_review_cap() -> None:
    from glyph_atlas.review.suggestions import decode

    alphabet = "ab"
    logits = np.zeros((1, 40, 3))
    logits[0, :, 1] = 5.0  # "a" at every position, no end-of-sequence token
    assert len(decode(logits, alphabet)[0]["text"]) == 32
    assert len(decode(logits, alphabet, limit=None)[0]["text"]) == 40


def test_inside_share_measures_the_inner_boxs_area() -> None:
    assert line_match.inside_share(Box(x=0, y=0, w=10, h=10), Box(x=0, y=0, w=100, h=100)) == 1.0
    assert line_match.inside_share(Box(x=0, y=0, w=10, h=10), Box(x=5, y=0, w=100, h=100)) == 0.5
