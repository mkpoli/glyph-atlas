"""The browser's preprocessing (apps/review/src/lib/preprocess.js) gives the classifier the same input
`classify.preprocess` and `classify.crop_array` give it, so an uploaded image is embedded as the
index's crops were."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
from PIL import Image

from glyph_atlas import classify

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { preprocess, tensor } from %s
const cases = JSON.parse(await Bun.file(process.argv[2]).text())
const out = cases.map(({ file, width, height }) => {
  const rgba = new Uint8Array(require('fs').readFileSync(file))
  const grey = preprocess(rgba, width, height, 128)
  return { grey: Buffer.from(grey).toString('base64'), tensor: Buffer.from(tensor(grey).buffer).toString('base64') }
})
await Bun.write(process.argv[3], JSON.stringify(out))
"""


def images():
    """Wide, tall, tiny, large, transparent and noisy crops: every branch of the resize and composite."""
    rng = np.random.default_rng(7)
    sizes = [(37, 61), (61, 37), (1, 1), (3, 50), (128, 128), (129, 90), (512, 300), (1200, 1700), (20, 30)]
    for n, (w, h) in enumerate(sizes):
        rgba = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
        # Every alpha from opaque to clear, and an opaque image, which is the common photo.
        rgba[..., 3] = 255 if n % 3 == 0 else rgba[..., 3]
        yield w, h, rgba
    gradient = np.zeros((80, 140, 4), dtype=np.uint8)
    gradient[..., 0] = np.linspace(0, 255, 140, dtype=np.uint8)
    gradient[..., 1] = np.linspace(255, 0, 80, dtype=np.uint8)[:, None]
    gradient[..., 2] = 128
    gradient[..., 3] = np.linspace(0, 255, 140, dtype=np.uint8)
    yield 140, 80, gradient


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun runs the browser module")
def test_the_browser_preprocessing_matches_pillow(tmp_path):
    cases, expected = [], []
    for n, (w, h, rgba) in enumerate(images()):
        file = tmp_path / f"{n}.rgba"
        file.write_bytes(rgba.tobytes())
        cases.append({"file": str(file), "width": w, "height": h})
        image = Image.fromarray(rgba, "RGBA")
        expected.append((np.asarray(classify.preprocess(image, size=128), dtype=np.uint8),
                         classify.crop_array(image, size=128)[0]))
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    module = json.dumps(str(ROOT / "apps/review/src/lib/preprocess.js"))
    (tmp_path / "run.mjs").write_text(SCRIPT % module)
    subprocess.run(["bun", str(tmp_path / "run.mjs"), str(tmp_path / "cases.json"), str(tmp_path / "out.json")],
                   check=True, cwd=ROOT)
    import base64
    for n, (result, (grey, tensor)) in enumerate(zip(json.loads((tmp_path / "out.json").read_text()), expected,
                                                     strict=True)):
        got = np.frombuffer(base64.b64decode(result["grey"]), dtype=np.uint8).reshape(128, 128)
        assert np.array_equal(got, grey), f"case {n}: {int(np.abs(got.astype(int) - grey).max())} grey levels apart"
        values = np.frombuffer(base64.b64decode(result["tensor"]), dtype=np.float32).reshape(3, 128, 128)
        # float32 arithmetic in the two runtimes may round the last bit differently.
        assert np.abs(values - tensor).max() <= 1e-6, f"case {n}: the tensor differs"
