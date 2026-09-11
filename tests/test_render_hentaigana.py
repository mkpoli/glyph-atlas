"""Tests for `scripts/render_hentaigana.py`.

Rendering runs against a font already downloaded into `cache/fonts/`. When no font is there the font
tests skip with the command that fetches one, so the suite never reaches the network. A rendering
test draws a handful of images into `tmp_path`, not the 200 of a full run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = ROOT / "cache" / "fonts"
FETCH = "uv run python scripts/render_hentaigana.py --seed 0 --count 1"

pytest.importorskip("cv2")
_spec = importlib.util.spec_from_file_location("render_hentaigana", ROOT / "scripts" / "render_hentaigana.py")
assert _spec is not None and _spec.loader is not None
render_hentaigana = importlib.util.module_from_spec(_spec)
sys.modules["render_hentaigana"] = render_hentaigana
_spec.loader.exec_module(render_hentaigana)


@pytest.fixture(scope="module")
def font():
    """A font from `cache/fonts/`, or a skip when none has been downloaded."""
    for spec in render_hentaigana.FONTS:
        path = FONTS_DIR / spec.filename
        if path.exists():
            return render_hentaigana.open_font(spec, path)
    pytest.skip(f"no hentaigana font under {FONTS_DIR}; fetch one with: {FETCH}")


def covered(font, count: int = 3) -> list[str]:
    """The first `count` code points of the vocabulary that the font covers."""
    points = [code_point for code_point in render_hentaigana.code_points() if font.covers(code_point)]
    assert len(points) >= count
    return points[:count]


def test_renders_three_code_points_into_tmp_path(tmp_path: Path, font) -> None:
    wanted = covered(font, 3)
    out = tmp_path / "kana"
    rows = render_hentaigana.render_dataset([font], wanted, out, seed=0, count=3, progress=False)
    assert len(rows) == 9

    for code_point in wanted:
        for index in range(3):
            path = out / code_point / f"{font.spec.name}-{index}.png"
            assert path.exists(), path
            with Image.open(path) as image:
                assert image.size == (128, 128)
                assert image.mode == "RGB"

    table = pq.read_table(out / "manifest.parquet")
    assert table.column_names == ["path", "code_point", "font", "parameters", "seed", "synthetic"]
    assert table.num_rows == 9
    records = table.to_pylist()
    assert {record["code_point"] for record in records} == set(wanted)
    assert {record["font"] for record in records} == {font.spec.name}
    assert all(record["synthetic"] is True for record in records)
    assert all(record["seed"] == 0 for record in records)

    for record in records:
        assert (out / record["path"]).exists()
        params = json.loads(record["parameters"])
        assert set(params) == render_hentaigana.PARAMETER_KEYS
        assert 0.62 <= params["fit"] <= 0.82
        assert abs(params["shear"]) <= 0.2
        assert abs(params["rotation_deg"]) <= 8.0
        assert abs(params["stroke_px"]) <= 2
        assert 0.0 <= params["blur_sigma"] <= 1.5
        assert 0 <= len(params["ink_blots"]) <= 3


def test_code_point_absent_from_the_font_raises(tmp_path: Path, font) -> None:
    missing = [code_point for code_point in render_hentaigana.code_points() if not font.covers(code_point)]
    if not missing:
        pytest.skip(f"{font.spec.name} covers every code point in hentaigana.tsv")
    with pytest.raises(render_hentaigana.MissingGlyphError):
        font.glyph(missing[0])

    out = tmp_path / "kana"
    rows = render_hentaigana.render_dataset([font], missing, out, seed=0, count=1, progress=False)
    assert rows == []
    assert not (out / missing[0]).exists()
    assert pq.read_table(out / "manifest.parquet").num_rows == 0


def test_renderings_repeat_from_the_seed(tmp_path: Path, font) -> None:
    code_point = covered(font, 1)[0]
    first = render_hentaigana.render_dataset(
        [font], [code_point], tmp_path / "first", seed=0, count=2, progress=False
    )
    second = render_hentaigana.render_dataset(
        [font], [code_point], tmp_path / "second", seed=0, count=2, progress=False
    )
    name = Path(code_point) / f"{font.spec.name}-0.png"
    assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    assert first[0]["parameters"] == second[0]["parameters"]


def test_two_code_points_do_not_share_a_glyph(font) -> None:
    """A `.notdef` box for every missing character would give two code points the same ink."""
    first, second = covered(font, 2)
    a = font.glyph(first).mask
    b = font.glyph(second).mask
    assert float(a.max()) > 0.5
    assert float(b.max()) > 0.5
    assert a.shape != b.shape or not np.array_equal(a, b)


def test_coverage_table_counts_what_the_font_has(font) -> None:
    wanted = render_hentaigana.code_points()
    lines = render_hentaigana.coverage_table([font], wanted)
    has = sum(1 for code_point in wanted if font.covers(code_point))
    assert lines[0].startswith("font")
    assert lines[1].startswith(font.spec.name)
    assert f"{has}/{len(wanted)}" in lines[1]
    assert "any font" in lines[2]


def test_sample_sheet_holds_one_tile_per_code_point(tmp_path: Path, font) -> None:
    wanted = covered(font, 2)
    out = tmp_path / "kana"
    render_hentaigana.render_dataset([font], wanted, out, seed=0, count=1, progress=False)
    sheet_path = render_hentaigana.write_sample_sheet(out, wanted, [font], tile=32, columns=2)
    with Image.open(sheet_path) as sheet:
        assert sheet.size == (64, 42)


def test_sampled_parameters_stay_within_the_documented_ranges() -> None:
    rng = np.random.default_rng(0)
    for _ in range(200):
        params = render_hentaigana.sample_parameters(rng)
        assert set(params) == render_hentaigana.PARAMETER_KEYS
        assert 0.62 <= params["fit"] <= 0.82
        assert 0.95 <= params["aspect_x"] <= 1.05
        assert 0.95 <= params["aspect_y"] <= 1.05
        assert abs(params["shear"]) <= 0.2
        assert abs(params["rotation_deg"]) <= 8.0
        assert abs(params["stroke_px"]) <= 2
        assert 1.5 <= params["elastic_alpha_px"] <= 4.0
        assert 4.0 <= params["elastic_sigma_px"] <= 9.0
        assert 0.0 <= params["blur_sigma"] <= 1.5
        for blot in params["ink_blots"]:
            assert 1.0 <= blot["rx_px"] <= 3.5
            assert 0.0 <= blot["u"] <= 1.0 and 0.0 <= blot["v"] <= 1.0


def test_code_points_are_the_vocabulary() -> None:
    points = render_hentaigana.code_points()
    assert len(points) == 287
    assert len(set(points)) == 287
    assert points[0] == "U+1B001"
    assert render_hentaigana.char_of("U+1B001") == "\U0001b001"
