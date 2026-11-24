import importlib.util
import json
import unicodedata
from pathlib import Path

import pytest

from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_grapheme_labels", ROOT / "scripts/build_grapheme_labels.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture(scope="module")
def labels():
    return build.labels()


def test_the_shipped_labels_are_the_tables(labels):
    assert json.loads(build.LABELS.read_text(encoding="utf-8")) == labels
    assert len(labels) == 271


def test_a_head_stands_beside_its_old_form(labels):
    assert {new: old + new for new, old in labels.items() if new in "仮国竜発"} == {"仮": "假仮", "国": "國国", "竜": "龍竜", "発": "發発"}
    assert all(unicodedata.normalize("NFC", old) == old != new for new, old in labels.items())


def test_both_are_characters_of_the_grapheme_the_new_form_heads(labels):
    for new, old in labels.items():
        assert refs.grapheme(refs.to_code_point(new)) == refs.grapheme(refs.to_code_point(old)) == refs.to_code_point(new)


def test_no_other_head_is_labelled(labels):
    # Heads that are no 新字体, whose older form is a compatibility ideograph, or whose older form
    # differs too little to be listed (靑 青).
    assert not {"大", "遇", "神", "海", "は", "青", "値", "戸", "歩"} & labels.keys()
