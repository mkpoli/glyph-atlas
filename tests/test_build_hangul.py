import importlib
import json
import sys
import unicodedata
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from PIL import Image

from glyph_atlas import refs, tables
from glyph_atlas.clusters import COMPATIBILITY_SHAPE_KEY_OVERRIDES, shape_key

sys.path.insert(0, str(Path("models/classifier").resolve()))
build_hangul = importlib.import_module("build_hangul")
build_combined = importlib.import_module("build_combined")
build_manifests = importlib.import_module("build_manifests")


def crop(unit_id, split, code_point):
    return build_manifests.Crop(
        unit_id=unit_id,
        crop=f"crops/{split}/{unit_id}.jpg",
        label=code_point,
        code_point=code_point,
        document_id="doc",
        page_id="",
        split=split,
        production="unknown",
        kind="char",
        script="hangul",
    )


def write_manifest(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    return tables.write(path, records, build_manifests.Crop, command="test")


def test_inventory_strips_wiki_markup_and_keeps_hangul_keys():
    text = (
        "가 {{outer {{inner 나}} 다}}<ref>x</ref> [[target|셰ᇰ]] [[ㄱ]] [[나]] "
        "Latin 123"
    )

    counts = build_hangul.inventory_from_texts([text])

    assert counts == {
        "U+AC00": 1,
        "U+C170 U+11F0": 1,
        "U+1100": 1,
        "U+B098": 1,
    }


def test_class_key_uses_nfc_and_atlas_format():
    assert build_hangul.class_key("\u1109\u1168\u11f0") == "U+C170 U+11F0"
    assert build_hangul.class_key("가") == "U+AC00"
    assert build_hangul.class_key("\u1112\u119e") == "U+1112 U+119E"


def test_shape_key_groups_printed_jamo_shapes():
    assert shape_key("\u3163") == "\u1175"
    assert shape_key("\u1175") == "\u1175"
    assert shape_key("\u115f\u1175") == "\u1175"
    assert shape_key("\u3145") == "\u1109"
    assert shape_key("\u1109") == "\u1109"
    assert shape_key("\u318d") == "\u119e"
    assert shape_key("\u119e") == "\u119e"
    assert shape_key("가") == "가"
    assert shape_key("\u1112\u119e") == "\u1112\u119e"


@pytest.mark.parametrize(("compatibility", "expected"), sorted(COMPATIBILITY_SHAPE_KEY_OVERRIDES.items()))
def test_shape_key_explicit_compatibility_table_entries(compatibility, expected):
    assert shape_key(compatibility) == expected


def test_min_uses_filter_drops_key_used_twice():
    counts = {"U+1100": 3, "U+1175": 2}

    kept, dropped = build_hangul.filter_min_uses(counts, 3)

    assert kept == {"U+1100": 3}
    assert dropped == {"classes": 1, "uses": 2}


def test_render_smoke_outputs_a_tight_greyscale_jpeg(tmp_path):
    faces = build_hangul.resolve_fonts(required=False)
    if not faces:
        pytest.skip("Hangul test fonts are not installed")
    cluster = "\u1109\u1168\u11f0"
    key = build_hangul.class_key(cluster)
    text = refs.to_char(key)
    face = next((candidate for candidate in faces if build_hangul.can_render(candidate, text)), None)
    if face is None:
        pytest.skip("No installed Hangul test font renders the old jamo cluster")

    target = tmp_path / "crop.jpg"
    assert build_hangul.render_crop(face, text, key, 0, target)

    with Image.open(target) as image:
        assert image.format == "JPEG"
        assert image.mode == "L"
        assert 4 <= image.width <= 250
        assert 4 <= image.height <= 250
        data = list(image.get_flattened_data())
        assert min(data) < 100
        assert max(data) > 180
        paper = max(data)
        bbox = image.point(lambda value: 255 if abs(value - paper) >= 40 else 0).getbbox()
        assert bbox is not None
        assert bbox[0] <= 12
        assert bbox[1] <= 12
        assert image.width - bbox[2] <= 12
        assert image.height - bbox[3] <= 12

    font = build_hangul.load_font(str(face.path), face.index, 96)
    assert abs(font.getlength(cluster) - font.getlength(unicodedata.normalize("NFC", cluster))) <= 2
    assert font.getlength(text) <= 0.9 * sum(font.getlength(char) for char in text)


def test_render_crop_is_deterministic_for_fixed_seed(tmp_path):
    faces = build_hangul.resolve_fonts(required=False)
    if not faces:
        pytest.skip("Hangul test fonts are not installed")
    text = "\u1109\u1168\u11f0"
    key = build_hangul.class_key(text)
    face = next((candidate for candidate in faces if build_hangul.can_render(candidate, text)), None)
    if face is None:
        pytest.skip("No installed Hangul test font renders the old jamo cluster")

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    assert build_hangul.render_crop(face, text, key, 2, first)
    assert build_hangul.render_crop(face, text, key, 2, second)

    assert first.read_bytes() == second.read_bytes()


def test_build_combined_extra_merges_spaced_jamo_classes(tmp_path, monkeypatch, capsys):
    base = tmp_path / "base"
    extra = tmp_path / "extra"
    for split in ("train", "val", "test"):
        write_manifest(base / f"{split}.parquet", [])
    write_manifest(
        base / "train.parquet",
        [
            crop("base-1", "train", "U+AC00"),
            crop("base-2", "train", "U+AC00"),
        ],
    )
    write_manifest(
        extra / "train.parquet",
        [
            crop("extra-1", "train", "U+1112 U+119E"),
            crop("extra-2", "train", "U+1112 U+119E"),
            crop("extra-3", "train", "U+3131"),
        ],
    )

    monkeypatch.setattr(build_combined, "CODH", base)
    monkeypatch.setattr(build_combined, "hilab_rows", list)
    out = tmp_path / "combined"
    classes = tmp_path / "classes.json"

    build_combined.main(
        ["--min-crops", "2", "--out", str(out), "--classes", str(classes), "--extra", str(extra)]
    )
    summary = json.loads(capsys.readouterr().out)

    assert summary["extra"] == 3
    class_list = json.loads(classes.read_text())["classes"]
    assert "U+1112 U+119E" in class_list
    assert "U+3131" not in class_list
    rows = pq.read_table(out / "train.parquet").to_pylist()
    assert next(row for row in rows if row["code_point"] == "U+3131")["label"] == "other"
    assert pq.read_table(out / "train.parquet").schema.names == pq.read_table(base / "train.parquet").schema.names

    no_extra_out = tmp_path / "combined-no-extra"
    no_extra_classes = tmp_path / "classes-no-extra.json"
    build_combined.main(["--min-crops", "2", "--out", str(no_extra_out), "--classes", str(no_extra_classes)])
    no_extra_summary = json.loads(capsys.readouterr().out)

    assert "extra" not in no_extra_summary
    assert json.loads(no_extra_classes.read_text())["classes"] == ["U+AC00", "other"]
