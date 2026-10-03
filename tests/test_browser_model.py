"""The float16 export the browser downloads keeps the float32 answers, and its class list names the
characters each output stands for."""
import importlib
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
ort = pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")
pytest.importorskip("timm")
np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path("models/classifier").resolve()))
export_onnx = importlib.import_module("export_onnx")
browser_model = importlib.import_module("browser_model")


class Tiny(torch.nn.Module):
    """A model with timm's `forward_features` and `forward_head`, small enough for a test."""

    def __init__(self, classes=5, width=8):
        super().__init__()
        torch.manual_seed(0)
        self.stem = torch.nn.Conv2d(3, width, 3, stride=4)
        self.pre = torch.nn.Linear(width, width)
        self.fc = torch.nn.Linear(width, classes)

    def forward_features(self, x):
        return torch.relu(self.stem(x))

    def forward_head(self, x, pre_logits=False):
        x = torch.tanh(self.pre(x.mean((2, 3))))
        return x if pre_logits else self.fc(x)


def run(path, pixels):
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return session.run(None, {"pixel_values": pixels})


def test_the_half_export_keeps_float32_inputs_and_outputs_and_the_answers(tmp_path):
    if not torch.cuda.is_available():
        pytest.skip("float16 convolutions are traced on the GPU")
    model = Tiny().cuda().eval()
    full = export_onnx.export(model, tmp_path / "full.onnx", 0.8, 32, 17, False)
    half = export_onnx.export(model, tmp_path / "half.onnx", 0.8, 32, 17, False, half=True)
    assert (full["precision"], half["precision"]) == ("float32", "float16")
    # The model the half export was given is still the float32 reference.
    assert next(model.parameters()).dtype == torch.float32
    pixels = np.random.default_rng(0).standard_normal((2, 3, 32, 32)).astype(np.float32)
    (_, p32, f32), (_, p16, f16) = run(tmp_path / "full.onnx", pixels), run(tmp_path / "half.onnx", pixels)
    assert p16.dtype == np.float32 and f16.dtype == np.float32
    assert np.abs(p16 - p32).max() < export_onnx.HALF_TOLERANCE
    cosine = (f16 * f32).sum(1) / np.linalg.norm(f16, axis=1) / np.linalg.norm(f32, axis=1)
    assert cosine.min() > .999
    assert browser_model.feature_width(tmp_path / "half.onnx") == 8


def test_a_merged_family_lists_its_members_and_other_none():
    entries = browser_model.class_entries(["U+56FD", "U+304B", "other"])
    country, ka, other = entries
    assert country["family"] == "U+56FD" and {"国", "國"} <= set(country["chars"])
    assert ka == {"class": "U+304B", "chars": ["か"], "family": None}
    assert other == {"class": "other", "chars": [], "family": None}
