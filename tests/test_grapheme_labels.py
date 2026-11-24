import hashlib
import importlib.util
import json
import unicodedata
import zipfile
from collections import Counter
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


@pytest.fixture(scope="module")
def edges():
    return build.edges()


@pytest.fixture(scope="module")
def older(edges):
    return build.older_forms(edges[0])


def test_the_shipped_labels_are_the_tables(labels):
    assert json.loads(build.LABELS.read_text(encoding="utf-8")) == labels
    assert Counter(len(label) for label in labels.values()) == {2: 2147, 3: 86}


def test_the_labels_are_the_older_form_the_head_and_the_simplified_form(labels):
    read = {head: labels.get(head, head) for head in "図竜国語馬線鉄説仮来参弯歩青後"}
    assert read == {
        "図": "圖図图", "竜": "龍竜龙", "国": "國国", "語": "語语", "馬": "馬马", "線": "線线", "鉄": "鐵鉄铁", "説": "説说",
        "仮": "假仮", "来": "來来", "参": "參参", "弯": "彎弯", "歩": "步歩", "青": "青", "後": "後",
    }  # fmt: skip


def test_a_label_holds_its_head_and_characters_of_its_grapheme_once(labels):
    for head, label in labels.items():
        assert head in label and len(set(label)) == len(label)
        assert {build.grapheme(char) for char in label} == {head}
        assert unicodedata.normalize("NFC", label) == label


def test_every_character_of_a_label_is_one_table_says_so(labels, edges, older):
    listed, simplified = build.tongyong(), set(edges[1])
    for head, label in labels.items():
        for char in set(label) - {head, older.get(head)}:
            members = {refs.to_char(point) for point in refs.graphemes()[refs.to_code_point(head)]}
            if char in listed:
                assert label[-1] == char and any((member, char) in simplified for member in members), label
            else:
                assert label == char + head and head in listed and (char, head) in simplified, label


def test_the_older_forms_are_the_jouyou_tables(edges, older):
    # 361 entries of the 文化庁 index (`joyo-2010`) print one older form and 弁 prints three.
    pairs = edges[0]
    assert len(pairs) == 364 and len(older) == 288
    assert [old for new, old in pairs if new == "弁"] == ["瓣", "辨", "辯"]
    # Each 新字体–旧字体 family is one, and so are those whose forms a 異体字 relation files together.
    families = yaml.safe_load((ROOT / "data" / "vocab" / "graphemes.yaml").read_text(encoding="utf-8"))["families"].values()
    listed = {family["members"][0]: family["members"][1] for family in families if family["relation"] == "shinjitai-kyujitai"}
    assert len(listed) == 271 and listed.items() <= older.items()
    assert older.keys() - listed.keys() == set("徴巣掲撃晩暦歩歴毎渉渇痩研縁録闘麺")


def test_a_pair_of_two_graphemes_gives_no_older_form(edges, older):
    apart = {new for new, old in edges[0] if build.grapheme(new) != build.grapheme(old)}
    assert apart == set("併即戻概涙状虚錬餅温黄弁")
    assert not apart & older.keys()


def test_no_simplified_form_of_another_member_stands_for_the_grapheme(labels):
    # 豐 and 閣 simplify to 丰 and 阁, of other graphemes, and 滷 to 卤; 确, 黹, 砷, 簧 and 晅 are listed as they are.
    assert labels["豊"] == "豐豊"
    assert not set("閣滷确黹砷簧晅") & labels.keys()
    # 发, 历 and 艺 are other graphemes' too, and 寇 and 冦 are forms no table calls a simplification.
    assert [labels[head] for head in "発歴芸"] == ["發発", "歷歴", "藝芸"]
    assert not set("寇宼") & labels.keys()


def test_the_general_table_is_whole():
    listed = build.tongyong()
    assert len(listed) == build.TONGYONG_SIZE and sorted(listed.values()) == list(range(1, build.TONGYONG_SIZE + 1))
    assert [listed[char] for char in "一线缐铁"] == [1, 1336, 7459, 1932]
    digest = hashlib.sha256(build.TONGYONG.read_bytes()).hexdigest()
    assert digest == "f2878b332c6b360689268793507354c698f4d86298bcf54640562ade2febeedb"


def test_another_archive_than_the_recorded_one_is_refused(tmp_path):
    archive = tmp_path / "Unihan.zip"
    with zipfile.ZipFile(archive, "w") as files:
        files.writestr("Unihan_OtherMappings.txt", "U+4E00\tkTGH\t2013:1\n")
    with pytest.raises(SystemExit, match="Unihan 18.0.0"):
        build.tongyong_table(archive)
