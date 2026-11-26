"""Parts no code point names: the table built from ids-input, and how a description names them."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from glyph_atlas import custom_parts

ROOT = Path(__file__).resolve().parents[1]


def test_every_spelling_names_one_identifier():
    assert custom_parts.identifier("{88}") == "X-00087"
    assert custom_parts.identifier("&X-00087;") == "X-00087"
    assert custom_parts.identifier("{73}") == custom_parts.identifier("⿱⿵冂三一") == "X-00072"
    assert custom_parts.identifier("&CDP-8BD0;") == custom_parts.identifier("{7}") == "X-00007"
    assert custom_parts.identifier("{999}") is None and custom_parts.identifier("&X-99999;") is None


def test_a_retired_identifier_answers_for_the_one_it_went_into():
    assert custom_parts.identifier("&X-01653;") == "X-00026"
    assert custom_parts.identifier("&X-01612;") is None
    assert "X-01653" not in custom_parts.parts()


def test_spelt_writes_every_such_part_as_its_identifier():
    assert custom_parts.spelt("⿱{88}龴") == "⿱&X-00087;龴"
    assert custom_parts.spelt("⿰⿱{73}小系") == "⿰⿱&X-00072;小系"
    assert custom_parts.spelt("⿰&CDP-8BD0;寸") == "⿰&X-00007;寸"
    assert custom_parts.spelt("⿱&X-01653;一") == "⿱&X-00026;一"
    # An unknown part stays as it was typed.
    assert custom_parts.spelt("⿱{999}一") == "⿱{999}一"
    assert custom_parts.unknown("⿱{999}{88}") == ["{999}"]


def test_a_part_of_a_description_that_draws_one_named_part_is_that_part():
    assert custom_parts.spelt("⿱⿵𠆢一龴") == "⿱&X-00087;龴"
    # The whole description is never replaced, so a form is never one bare part.
    assert custom_parts.spelt("⿵𠆢一") == "⿵𠆢一"
    # ⿴𠀃三 draws both {73} and &CDP-8BA5;, and ⿻日乚 is 电, so neither is a part's spelling.
    assert custom_parts.spelt("⿱⿴𠀃三八") == "⿱⿴𠀃三八"
    assert custom_parts.spelt("⿱⿻日乚一") == "⿱⿻日乚一"
    assert custom_parts.spelt("⿱⿵𠆢一") == "⿱⿵𠆢一"


def test_a_part_known_only_as_a_description_is_written_as_it():
    assert custom_parts.spelt("⿱&X-01788;一") == custom_parts.spelt("⿱⿹⿺㇉一灬一") == "⿱⿹&X-00022;灬一"


def test_the_one_spelling_is_settled():
    for part in custom_parts.parts().values():
        for text in (*part.draws, *part.names, part.spelling, *part.folds):
            once = custom_parts.spelt(f"⿰{text}木")
            assert custom_parts.spelt(once) == once


def test_drawn_writes_a_description_in_encoded_characters():
    assert custom_parts.drawn("⿱&X-00087;龴") == "⿱⿵𠆢一龴"
    assert custom_parts.drawn("⿱{88}龴") == "⿱⿵𠆢一龴"
    assert custom_parts.drawn("令") == "令"
    # {5}, the left of 叚, has no drawing in encoded characters.
    assert custom_parts.drawn("⿰{5}又") is None
    # A form no source names as a component draws as its expression; one drawn first by a private-use
    # glyph takes its next drawing.
    assert custom_parts.drawn("&X-01548;") == "⿰⺀⺀"
    assert custom_parts.drawn("&CDP-8C78;") == "コ"
    assert custom_parts.parts()["X-00081"].editorial and not custom_parts.parts()["X-00087"].editorial


def test_the_grammar_is_ids_inputs():
    assert custom_parts.unknown("{１２}") == [] and custom_parts.spelt("{１２}") == "{１２}"
    found = custom_parts.TOKEN.findall("&A-U-i005+81FC;#(-丨𠃍-一z){12345}&1bad;")
    assert found == ["&A-U-i005+81FC;", "#(-丨𠃍-一z)"]


def test_the_table_matches_its_build(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_custom_parts", ROOT / "scripts" / "build_custom_parts.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if not build.CACHE.exists():
        pytest.skip("the upstream file is not in cache/; run scripts/build_custom_parts.py")
    data = build.fetched()
    assert (ROOT / "data" / "vocab" / "custom-parts.tsv").read_text(encoding="utf-8") == build.table(data)
    assert (ROOT / "apps" / "cloudflare" / "src" / "customPartsData.ts").read_text(encoding="utf-8") == build.module(data)


def test_the_structure_layer_reads_a_part_as_one_component():
    from glyph_atlas import han_component_variants, structures

    assert han_component_variants.parse("⿱&X-00087;龴") == ("⿱", ("&X-00087;", "龴"))
    assert structures.for_ids("⿱&X-00087;龴").label == "⿱&X-00087;龴"
