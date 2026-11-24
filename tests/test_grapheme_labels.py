import importlib.util
import json
import unicodedata
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_grapheme_labels", ROOT / "scripts/build_grapheme_labels.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture(scope="module")
def labels():
    return build.labels()


def test_the_shipped_labels_are_the_tables(labels):
    assert json.loads(build.LABELS.read_text(encoding="utf-8")) == labels
    assert len(labels) == 354


def test_a_head_stands_beside_its_old_form(labels):
    assert {new: old + new for new, old in labels.items() if new in "仮国竜発"} == {"仮": "假仮", "国": "國国", "竜": "龍竜", "発": "發発"}
    # Every pair of the Jōyō table's families is labelled, by the same old form.
    families = yaml.safe_load((ROOT / "data/vocab/graphemes.yaml").read_text(encoding="utf-8"))["families"]
    pairs = {row["members"][0]: row["members"][1] for row in families.values() if row["relation"] == "shinjitai-kyujitai"}
    assert len(pairs) == 271
    assert pairs.items() <= labels.items()


def test_of_several_old_forms_the_one_most_sources_state_is_shown(labels):
    assert [labels[new] for new in "郷鶏麺"] == ["鄕", "鷄", "麵"]


def test_no_other_head_is_labelled(labels):
    # No 新字体, the head of an old-form family, and one whose only other code point is for compatibility.
    assert not {"大", "遇", "神", "海", "は"} & labels.keys()
    assert all(unicodedata.normalize("NFC", old) == old for old in labels.values())
