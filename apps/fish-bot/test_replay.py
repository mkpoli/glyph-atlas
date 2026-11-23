"""The replay set's rule checker and case file, without calling any model."""

from pathlib import Path

import replay


def test_every_case_in_the_set_loads_and_has_rules():
    cases = replay.load_cases(Path(replay.__file__).with_name("replay.json"))
    assert cases and all(case["messages"] and case["expect"] for case in cases)


def test_the_checker_names_each_broken_rule():
    case = {"expect": {"tools_any": ["characters"], "must_match": ["U\\+17F07"], "must_not_match": ["mji"],
                       "max_chars": 20}}
    assert replay.check(case, "U+17F07", ["characters"]) == []
    broken = replay.check(case, "It reads mji, a long made-up gloss.", [])
    assert len(broken) == 4


def test_small_talk_must_not_look_anything_up():
    case = {"expect": {"tools_none": True}}
    assert replay.check(case, "晚安～", []) == []
    assert replay.check(case, "晚安～", ["web_search"]) == ["looked something up for small talk (['web_search'])"]


def test_a_case_can_ask_for_silence():
    case = {"expect": {"quiet": True}}
    assert replay.check(case, "", []) == []
    assert replay.check(case, "嗨～", []) == ["spoke where she should stay quiet"]
