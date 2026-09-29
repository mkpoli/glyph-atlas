"""Where NDL's sequence model runs: CUDA when available, the CPU when asked."""

from pathlib import Path

import pytest

onnx = pytest.importorskip("onnx")
ort = pytest.importorskip("onnxruntime")


def tiny_model(path: Path) -> None:
    from onnx import TensorProto, helper
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 16, 64])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3, 16, 64])
    graph = helper.make_graph([helper.make_node("Identity", ["x"], ["y"])], "g", [x], [y])
    onnx.save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)]), path)


def test_the_sequence_model_runs_on_the_cpu_when_asked(tmp_path, monkeypatch):
    from glyph_atlas.review.suggestions import Recognizer

    tiny_model(tmp_path / "parseq.onnx")
    (tmp_path / "characters.yaml").write_text("model:\n  charset_train: \"あい\"\n")
    monkeypatch.setenv("ATLAS_OCR_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_CLASSIFIER_MODEL", str(tmp_path / "missing.onnx"))
    reader = Recognizer(sequence_on_cpu=True, sequence_threads=3)
    assert reader.sequence.get_providers() == ["CPUExecutionProvider"]
    assert reader.engines == [{"name": "NDLkotenOCR", "sha256": reader.engines[0]["sha256"],
                               "provider": "CPUExecutionProvider"}]
    assert reader.sequence.get_session_options().intra_op_num_threads == 3
