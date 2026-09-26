"""The chữ Hán-Nôm catalogue is the Vietnamese one respelled through `data/vocab/vi-hani.tsv`."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_vi_hani", ROOT / "scripts/build_vi_hani.py")
build_vi_hani = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_vi_hani)


def test_every_word_has_a_spelling():
    _, missing = build_vi_hani.build()
    assert not missing


def test_catalogue_is_up_to_date():
    catalogue, _ = build_vi_hani.build()
    written = json.loads((ROOT / "apps/review/src/locales/vi-Hani.json").read_text(encoding="utf-8"))
    assert written == catalogue


def test_spelling_joins_han_and_keeps_names():
    table = {"tải": "載", "lại": "吏", "từ": "自", "và": "吧", "để": "底"}
    missing = set()
    assert build_vi_hani.spell("Tải lại JSON.", table, missing) == "載吏JSON。"
    assert build_vi_hani.spell("Từ CODH, HI Lab và {name}", table, missing) == "自CODH，HI Lab吧{name}"
    assert build_vi_hani.spell("qwerty… để lại · ⌫ tải", table, missing) == "qwerty…底吏 · ⌫載"
    assert not missing


def test_webfont_draws_every_supplementary_character():
    """Few installed fonts reach past the BMP, so each such Nôm character must be in the subset."""
    from fontTools.ttLib import TTFont

    catalogue, _ = build_vi_hani.build()
    text = "".join(v for v in catalogue.values() if isinstance(v, str)) + build_vi_hani.LOCALE["name"]
    cmap = TTFont(build_vi_hani.FONT_OUT).getBestCmap()
    assert {c for c in text if ord(c) >= 0x20000 and ord(c) not in cmap} == set()
