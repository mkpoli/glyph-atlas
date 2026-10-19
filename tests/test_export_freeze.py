"""The export reads a frozen copy of the dataset, which holds everything the crop records are built from."""
import importlib.util
import json
import sys
from pathlib import Path

from glyph_atlas import ainu_geometry

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "export_cloudflare.py"
sys.path.insert(0, str(SCRIPTS))


def load():
    spec = importlib.util.spec_from_file_location("export_cloudflare", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_frozen_copy_carries_the_crop_geometry_flags(tmp_path):
    dataset, frozen = tmp_path / "dataset", tmp_path / "frozen"
    dataset.mkdir(); frozen.mkdir()
    (dataset / "units.parquet").write_bytes(b"units")
    mark = {"flags": ["sparse-ink"], "box": {"x": 1, "y": 2, "w": 3, "h": 4}}
    (dataset / ainu_geometry.FILE).write_text(json.dumps({"method": ainu_geometry.METHOD, "units": {"ar:a:1": mark}}))
    load().freeze(dataset, frozen)
    assert (frozen / "units.parquet").read_bytes() == b"units"
    assert ainu_geometry.load(frozen) == {"ar:a:1": mark}
