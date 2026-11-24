import importlib.util
import json
import unicodedata
from pathlib import Path

import pytest
import yaml

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
    assert len(labels) == 288


def test_the_pairs_are_the_jouyou_tables(labels):
    # 361 entries of the 文化庁 index (`joyo-2010`) print one older form and 弁 prints three.
    pairs = build.pairs()
    assert len(pairs) == 364
    assert [old for new, old in pairs if new == "弁"] == ["瓣", "辨", "辯"]
    # Each 新字体–旧字体 family is one, and so are those whose forms a 異体字 relation files together.
    families = yaml.safe_load((ROOT / "data" / "vocab" / "graphemes.yaml").read_text(encoding="utf-8"))["families"].values()
    listed = {family["members"][0]: family["members"][1] for family in families if family["relation"] == "shinjitai-kyujitai"}
    assert len(listed) == 271 and listed.items() <= labels.items()
    assert labels.keys() - listed.keys() == set("徴巣掲撃晩暦歩歴毎渉渇痩研縁録闘麺")


def test_a_pair_of_two_graphemes_labels_neither():
    apart = {new for new, old in build.pairs() if refs.grapheme(refs.to_code_point(new)) != refs.grapheme(refs.to_code_point(old))}
    assert apart == set("併即戻概涙状虚錬餅温黄弁")
    assert not apart & build.labels().keys()


def test_a_head_stands_beside_its_old_form(labels):
    assert {new: old + new for new, old in labels.items() if new in "仮国竜発"} == {"仮": "假仮", "国": "國国", "竜": "龍竜", "発": "發発"}
    assert all(unicodedata.normalize("NFC", old) == old != new for new, old in labels.items())


def test_both_are_characters_of_the_grapheme_the_new_form_heads(labels):
    for new, old in labels.items():
        assert refs.grapheme(refs.to_code_point(new)) == refs.grapheme(refs.to_code_point(old)) == refs.to_code_point(new)


def test_no_other_head_is_labelled(labels):
    # Heads that are no 新字体, whose older form is a compatibility ideograph, or which the table prints no
    # older form of (靑 青).
    assert not {"大", "遇", "神", "海", "は", "青", "値", "戸"} & labels.keys()
