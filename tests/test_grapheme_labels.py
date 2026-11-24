import hashlib
import importlib.util
import json
import unicodedata
import zipfile
from pathlib import Path

import pytest
import yaml

from glyph_atlas import refs, structures

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
            if label[-1] == char:
                assert char in listed and any((member, char) in simplified for member in members), label
            else:
                # The character its 简体字 simplifies, of another 構形 than the head.
                assert label[0] == char and label[-1] in listed and (char, label[-1]) in simplified, label
                assert head not in older and structures.for_character(char) != structures.for_character(head), label


def test_a_head_without_an_older_form_stands_after_what_its_simplified_form_simplifies(labels):
    read = [labels.get(head, head) for head in "壷楕聡頚涜溌蝿隣弯桧"]
    assert read == ["壺壷壶", "橢楕椭", "聰聡聪", "頸頚颈", "瀆涜渎", "潑溌泼", "蠅蝿蝇", "鄰隣邻", "彎弯", "檜桧"]
    # The same 構形 drawn otherwise, and a head that simplifies itself (線: 缐) beside 綫.
    assert [labels.get(head, head) for head in "説税嘘粤線語"] == ["説说", "税", "嘘", "粤", "線线", "語语"]


def test_the_older_forms_are_the_jouyou_tables(edges, older):
    # 361 entries of the 文化庁 index (`joyo-2010`) print one older form and 弁 prints three.
    pairs = edges[0]
    assert len(pairs) == 364
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
