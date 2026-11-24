"""The replay set's rule checker and case file, without calling any model."""

from pathlib import Path

import fish_bot
import pytest
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


def test_the_bots_own_key_is_refused(tmp_path):
    live, mine = tmp_path / "live", tmp_path / "mine"
    live.write_text("live-key\n")
    mine.write_text("test-key\n")
    assert replay.own_key(mine, live) == "test-key"
    copied = tmp_path / "copied"
    copied.write_text("live-key")
    with pytest.raises(SystemExit):
        replay.own_key(copied, live)


def test_a_run_stops_at_its_own_limit_and_the_day_at_another(tmp_path):
    purse = replay.RunPurse(fish_bot.Store(tmp_path / "ledger.sqlite3"), 2.4 * fish_bot.CALL_RESERVE)
    purse.reserve("chat")
    purse.reserve("chat")
    with pytest.raises(fish_bot.SpendError, match="this run"):
        purse.reserve("chat")
    runs = 0
    with pytest.raises(fish_bot.SpendError, match="today"):
        for _ in range(100):
            replay.RunPurse(fish_bot.Store(tmp_path / "ledger.sqlite3"), 2.4 * fish_bot.CALL_RESERVE).reserve("chat")
            runs += 1
    assert runs > 0 and purse.spent() <= replay.DAILY_USD + 1e-9


def test_a_run_keeps_its_own_count_over_midnight(tmp_path, monkeypatch):
    store = fish_bot.Store(tmp_path / "ledger.sqlite3")
    store.count_usage("chat", cost=0.40)
    purse = replay.RunPurse(store, 2.4 * fish_bot.CALL_RESERVE)
    day = purse.reserve("chat")
    purse.settle("chat", day, cost=0.01)
    purse.add("grounding", 0.014)
    later = fish_bot.now_ms() + 86_400_000
    monkeypatch.setattr(fish_bot, "now_ms", lambda: later)
    assert round(purse.taken, 6) == 0.024
    purse.reserve("chat")
    with pytest.raises(fish_bot.SpendError, match="this run"):
        purse.reserve("chat")
