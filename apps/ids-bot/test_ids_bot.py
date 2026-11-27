"""The IDS bot: which descriptions it reads, what it answers, and how it keeps its place in the stream."""

from __future__ import annotations

import json
import logging

import ids_bot

USER = "@idsbot:example"
ROOM = "!room:example"


def message(body: str, sender: str = "@reader:example", msgtype: str = "m.text", **content):
    return {"type": "m.room.message", "sender": sender, "event_id": "$event", "room_id": ROOM,
            "content": {"msgtype": msgtype, "body": body, **content}}


class Fake:
    """A Matrix that answers from a queue and records every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def call(self, method, path, *, params=None, body=None, timeout=None):
        self.calls.append((method, path, params, body))
        if not self.answers:
            raise AssertionError(f"no answer left for {method} {path}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_reads_each_description_in_a_message():
    assert ids_bot.sequences("⿱⿻韦丷吋 ⿳⿻韦丷一吋") == ["⿱⿻韦丷吋", "⿳⿻韦丷一吋"]


def test_leaves_the_prose_around_a_description_alone():
    assert ids_bot.sequences("how about ⿰木木?") == ["⿰木木"]
    assert ids_bot.sequences("⿰木木、是林") == ["⿰木木"]
    assert ids_bot.sequences("（⿰木木）") == ["⿰木木"]
    assert ids_bot.sequences("⿰木木 ") == ["⿰木木"]


def test_needs_a_description_to_stand_as_a_word_of_its_own():
    assert ids_bot.sequences("用⿰表示左右结构") == []
    assert ids_bot.sequences("⿱は上下構造を表す") == []
    assert ids_bot.sequences("⿰木木是林") == []


def test_reads_each_description_once():
    assert ids_bot.sequences("⿰木木 and ⿰木木") == ["⿰木木"]


def test_reads_a_variation_selector_with_its_component():
    assert ids_bot.sequences("⿰木\uFE0F木") == ["⿰木\uFE0F木"]


def test_reads_the_zi_tools_modifier_after_an_operator():
    assert ids_bot.sequences("⿻[1:]丨日") == ["⿻[1:]丨日"]
    assert ids_bot.sequences("⿻[1.]丨日") == []


def test_reads_adjacent_descriptions_without_a_break_between_them():
    assert ids_bot.sequences("⿰木木⿰日曰") == ["⿰木木", "⿰日曰"]


def test_needs_a_complete_description():
    assert ids_bot.sequences("⿰") == []
    assert ids_bot.sequences("⿰木") == []
    assert ids_bot.sequences("⿱⿻韦") == []
    assert ids_bot.sequences("⿰ 木 木") == []


def test_leaves_fragments_of_an_incomplete_operator_run_alone():
    for text in ("按⿰⿱⿴排字", "⿰⿱⿴排字", "（⿰⿱⿴排字）", "⿰⿾木"):
        assert ids_bot.sequences(text) == []
        assert ids_bot.wanted(message(text), USER) == []


def test_reads_separate_descriptions_around_an_incomplete_operator_run():
    for separator in (" ", "?", "\n"):
        text = "⿰木木" + separator + "⿰⿱⿴排字" + separator + "⿱日月"
        assert ids_bot.sequences(text) == ["⿰木木", "⿱日月"]
    assert ids_bot.sequences("⿰⿱⿴排字木木⿱日月") == ["⿰⿱⿴排字木木", "⿱日月"]


def test_leaves_descriptions_whose_components_would_cut_their_url_alone():
    assert ids_bot.sequences("⿰木.木") == []
    assert ids_bot.sequences("⿰木/木") == []
    assert ids_bot.sequences("⿰木?木") == []
    assert ids_bot.sequences("⿰木#木") == []
    assert ids_bot.sequences("⿰木%木") == []


def test_reads_hangul_jamo_clusters_and_korean_tone_marks():
    assert ids_bot.sequences("⿰\u1100\u1161木") == ["⿰\u1100\u1161木"]
    assert ids_bot.sequences("⿰\ua960\ud7b0\ud7cb木") == ["⿰\ua960\ud7b0\ud7cb木"]
    assert ids_bot.sequences("⿰가\u302e木") == ["⿰가\u302e木"]
    assert ids_bot.sequences("⿰\u1100\u1161\u302f木") == ["⿰\u1100\u1161\u302f木"]
    assert ids_bot.sequences("⿰木{1}\u302e") == ["⿰木{1}\u302e"]


def test_reads_trailing_loose_marks():
    assert ids_bot.sequences("⿰亻木ˊ") == ["⿰亻木ˊ"]
    assert ids_bot.sequences("（⿰ㄅㄚˇ）") == ["⿰ㄅㄚˇ"]
    assert ids_bot.sequences("⿻[1:]丨日ˊ") == ["⿻[1:]丨日ˊ"]
    assert ids_bot.sequences("⿰木木゛") == ["⿰木木゛"]
    assert ids_bot.sequences("⿰木木\u3099") == ["⿰木木\u3099"]
    assert ids_bot.sequences("⿰木木\u0897") == ["⿰木木\u0897"]
    assert ids_bot.sequences("⿰木木²") == ["⿰木木²"]
    assert ids_bot.sequences("⿰木木ˊ是林") == []


def test_reads_multi_codepoint_emoji_components():
    assert ids_bot.sequences("⿰👩🏽木") == ["⿰👩🏽木"]
    assert ids_bot.sequences("⿰👩\u200d👧木") == ["⿰👩\u200d👧木"]
    assert ids_bot.sequences("⿰😀\u0301木") == ["⿰😀\u0301木"]
    assert ids_bot.sequences("⿰🇯🇵🇺🇸") == ["⿰🇯🇵🇺🇸"]
    assert ids_bot.sequences("⿰1️⃣木") == ["⿰1️⃣木"]
    assert ids_bot.sequences("⿰1\u20e3\u0301木") == ["⿰1\u20e3\u0301木"]
    assert ids_bot.sequences("⿰☺\ufe0f\u3099木") == ["⿰☺\ufe0f\u3099木"]
    assert ids_bot.sequences("⿰👩\u200d👧\u0301木") == ["⿰👩\u200d👧\u0301木"]
    assert ids_bot.sequences("⿰🇯\u200d木") == ["⿰🇯\u200d木"]
    assert ids_bot.sequences("⿰😀\u200c木") == ["⿰😀\u200c木"]
    assert ids_bot.sequences("⿰😀\u0e33木") == ["⿰😀\u0e33木"]
    assert ids_bot.sequences("⿰❤\uff9e木") == ["⿰❤\uff9e木"]


def test_reads_an_emoji_newer_than_python_as_a_break():
    assert ids_bot.sequences("⿰木木\U0001fae9") == ["⿰木木"]
    assert ids_bot.sequences("\U0001fae9⿰木木") == ["⿰木木"]


def test_reads_no_hangul_filler_or_ignorable_as_a_component():
    for filler in "\u115f\u1160\u3164\uffa0\u034f":
        assert ids_bot.sequences("⿰木" + filler) == []


def test_reads_a_long_run_after_many_operators_quickly():
    import time

    for run in ("😀" + "\u0301" * 32000, "1" + "\u20e3" * 32000, "木" + "\u302e" * 32000):
        started = time.monotonic()
        ids_bot.sequences("⿰" * 150 + run)
        assert time.monotonic() - started < 0.2
    assert ids_bot.sequences("⿰🏴󠁧󠁢󠁥󠁮󠁧󠁿木") == ["⿰🏴󠁧󠁢󠁥󠁮󠁧󠁿木"]


def test_reads_custom_components_and_literal_braces_fallback():
    max_entity = "⿾&" + "a" * 47 + ";"
    assert ids_bot.sequences("⿰木{53}") == ["⿰木{53}"]
    assert ids_bot.sequences("⿾{1}") == ["⿾{1}"]
    assert ids_bot.sequences("⿰木&CDP-8BD0;") == ["⿰木&CDP-8BD0;"]
    assert ids_bot.sequences(max_entity) == [max_entity]
    assert ids_bot.sequences("⿾&1;") == []
    assert ids_bot.sequences("how about ⿳{1}?") == ["⿳{1}"]
    assert ids_bot.sequences("⿳{1}abc") == []
    assert ids_bot.sequences("⿰#(-𠃌㇉)木") == []


def test_leaves_unassigned_ideographs_joined_to_prose_and_incomplete_slots_alone():
    assert ids_bot.sequences("用⿰𽀀木") == []
    assert ids_bot.sequences("⿰木𽀀𽀁") == []
    assert ids_bot.sequences("⿰木木\U0002ebf0") == []
    assert ids_bot.sequences("\U00018e00⿰木木") == []
    assert ids_bot.sequences("⿰㇀🏽") == ["⿰㇀🏽"]
    assert ids_bot.sequences("⿱艹⬚") == []
    assert ids_bot.sequences("⿰木⬚\ufe0f") == []
    assert ids_bot.sequences("⿰❤\u20e3") == []
    assert ids_bot.sequences("⿰木⿱\ufe0f木") == []
    assert ids_bot.sequences("⿱左右") == ["⿱左右"]
    assert ids_bot.sequences("小隹") == []


def test_reads_no_description_longer_than_the_workshop_does():
    longest = "⿾" * (ids_bot.LONGEST - 1) + "木"
    assert len(longest) == ids_bot.LONGEST
    assert ids_bot.sequences(longest) == [longest]
    assert ids_bot.sequences("⿾" * ids_bot.LONGEST) == []
    assert ids_bot.sequences("⿾" * (ids_bot.LONGEST - 1) + "木゛") == []
    assert ids_bot.sequences("⿾" * (ids_bot.LONGEST - 3) + "{53}") == []
    assert ids_bot.sequences("⿾" * (ids_bot.LONGEST - 3) + "⿰{1}木") == []


def test_skips_a_long_description_whole_and_links_no_fragment_of_it():
    chain = "木"
    for _ in range(100):
        chain = "⿰木" + chain
    assert len(chain) > ids_bot.LONGEST
    assert ids_bot.sequences(chain) == []
    pyramid = "⿰" + "⿰木木" + "⿱" * 255 + "木" * 256
    assert len(pyramid) == 515
    assert ids_bot.sequences(pyramid) == []


def test_leaves_its_own_messages_and_edits_alone():
    assert ids_bot.wanted(message("⿰木木"), USER) == ["⿰木木"]
    assert ids_bot.wanted(message("⿰木木", sender=USER), USER) == []
    edit = message("⿰木木", **{"m.new_content": {"msgtype": "m.text", "body": "⿰木木"},
                                "m.relates_to": {"rel_type": "m.replace", "event_id": "$old"}})
    assert ids_bot.wanted(edit, USER) == []


def test_leaves_messages_that_already_link_the_workshop_alone():
    assert ids_bot.wanted(message("see https://ids.mkpo.li/⿰木木"), USER) == []


def test_answers_below_a_quoted_reply():
    assert ids_bot.wanted(message("> <@reader:example> ⿰木木\n\n⿱日曰"), USER) == ["⿱日曰"]
    assert ids_bot.wanted(message("> <@idsbot:example> https://ids.mkpo.li/⿰木木\n\n⿱日曰"), USER) == ["⿱日曰"]


def test_leaves_malformed_content_alone():
    assert ids_bot.wanted({"type": "m.room.message", "sender": "@reader:example", "content": []}, USER) == []
    assert ids_bot.wanted({"type": "m.room.message", "sender": "@reader:example",
                           "content": {"msgtype": "m.text", "body": "⿰木木", "m.relates_to": "x"}}, USER) == ["⿰木木"]


def test_answers_at_most_ten_descriptions_of_one_message():
    body = " ".join("⿰" + chr(0x4E00 + i) + "木" for i in range(11))
    assert len(ids_bot.wanted(message(body), USER)) == 10


def test_reads_text_and_emotes_but_not_notices():
    assert ids_bot.wanted(message("⿰木木", msgtype="m.emote"), USER) == ["⿰木木"]
    assert ids_bot.wanted(message("⿰木木", msgtype="m.notice"), USER) == []
    assert ids_bot.wanted({"type": "m.room.member", "sender": "@reader:example"}, USER) == []
    assert ids_bot.wanted({"type": "m.room.message", "sender": "@reader:example",
                           "content": {"msgtype": "m.text", "body": 3}}, USER) == []


def test_answers_with_the_page_of_every_description():
    content = ids_bot.reply_content(["⿰木木", "⿱日曰"], "$event")
    assert content["body"] == "https://ids.mkpo.li/⿰木木\nhttps://ids.mkpo.li/⿱日曰"
    assert content["formatted_body"] == (
        '<a href="https://ids.mkpo.li/%E2%BF%B0%E6%9C%A8%E6%9C%A8">https://ids.mkpo.li/⿰木木</a>'
        '<br /><a href="https://ids.mkpo.li/%E2%BF%B1%E6%97%A5%E6%9B%B0">https://ids.mkpo.li/⿱日曰</a>'
    )
    assert content["m.relates_to"] == {"rel_type": "m.in_reply_to", "event_id": "$event"}


def test_names_the_same_transaction_for_the_same_message():
    assert ids_bot.transaction_id("$event") == ids_bot.transaction_id("$event")
    assert ids_bot.transaction_id("$event") != ids_bot.transaction_id("$other")


def test_waits_out_a_rate_limit_and_sends_once():
    matrix = Fake(ids_bot.MatrixError(429, "slow down", 0), {"event_id": "$answer"})
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    bot.answer(ROOM, message("⿰木木"))
    assert [call[0] for call in matrix.calls] == ["PUT", "PUT"]


def test_sends_one_reply_to_the_message_it_reads():
    matrix = Fake({"event_id": "$answer"})
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    bot.answer(ROOM, message("⿱⿻韦丷吋 ⿳⿻韦丷一吋"))
    method, path, _, body = matrix.calls[0]
    assert method == "PUT"
    assert path == (
        f"/_matrix/client/v3/rooms/%21room%3Aexample/send/m.room.message/{ids_bot.transaction_id('$event')}"
    )
    assert body["body"] == "https://ids.mkpo.li/⿱⿻韦丷吋\nhttps://ids.mkpo.li/⿳⿻韦丷一吋"


def test_answers_nothing_to_a_message_without_a_description():
    matrix = Fake()
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    bot.answer(ROOM, message("nothing here"))
    assert matrix.calls == []


def test_one_malformed_event_does_not_stop_the_batch():
    matrix = Fake({"event_id": "$answer"})
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    events = [{"type": "m.room.message", "sender": "@reader:example", "content": []}, message("⿰木木")]
    bot.process({"rooms": {"join": {ROOM: {"timeline": {"events": events}}}}})
    assert [call[0] for call in matrix.calls] == ["PUT"]


def test_warns_when_a_timeline_is_cut(caplog):
    matrix = Fake()
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    with caplog.at_level(logging.WARNING, logger="ids-bot"):
        bot.process({"rooms": {"join": {ROOM: {"timeline": {"limited": True, "events": []}}}}})
    assert "cut" in caplog.text


def test_cycle_answers_the_batch_and_keeps_its_token(tmp_path):
    state = ids_bot.State(tmp_path / "state.json")
    matrix = Fake({"next_batch": "s1", "rooms": {"join": {ROOM: {"timeline": {"events": [message("⿰木木")]}}}}},
                  {"event_id": "$answer"})
    bot = ids_bot.Bot(matrix, state)
    bot.user_id = USER
    assert bot.cycle("s0") == "s1"
    method, path, params, _ = matrix.calls[0]
    assert (method, path) == ("GET", "/_matrix/client/v3/sync")
    assert params["since"] == "s0"
    assert params["timeout"] == str(ids_bot.SYNC_TIMEOUT_MS)
    assert json.loads(state.path.read_text(encoding="utf-8")) == {"since": "s1"}


def test_the_first_sync_is_read_without_answering_it(tmp_path):
    state = ids_bot.State(tmp_path / "state.json")
    matrix = Fake({"next_batch": "s1", "rooms": {"join": {ROOM: {"timeline": {"events": [message("⿰木木")]}}}}})
    bot = ids_bot.Bot(matrix, state)
    bot.user_id = USER
    assert bot.initial_sync() == "s1"
    assert [call[0] for call in matrix.calls] == ["GET"]
    assert json.loads(state.path.read_text(encoding="utf-8")) == {"since": "s1"}


def test_a_sync_answer_without_a_token_is_an_error():
    matrix = Fake({})
    bot = ids_bot.Bot(matrix, ids_bot.State())
    try:
        bot._token({})
    except ids_bot.MatrixError as error:
        assert "next_batch" in str(error)
    else:
        raise AssertionError("a sync answer without next_batch was accepted")


def test_joins_every_public_room_of_its_server():
    matrix = Fake(
        {"joined_rooms": ["!joined:example"]},
        {"chunk": [{"room_id": "!public:example"}, {"room_id": "!joined:example"}, {"room_id": "!private:example"},
                   {"room_id": "!broken:example"}], "next_batch": "p2"},
        {"room_id": "!public:example"},
        ids_bot.MatrixError(403, "forbidden"),
        ids_bot.MatrixError(500, "busy"),
        {"chunk": [{"room_id": "!later:example"}]},
        {"room_id": "!later:example"},
    )
    bot = ids_bot.Bot(matrix, ids_bot.State())
    bot.user_id = USER
    bot.join_public_rooms()
    joins = [path for method, path, _, _ in matrix.calls if method == "POST"]
    assert joins == [
        "/_matrix/client/v3/join/%21public%3Aexample",
        "/_matrix/client/v3/join/%21private%3Aexample",
        "/_matrix/client/v3/join/%21broken%3Aexample",
        "/_matrix/client/v3/join/%21later%3Aexample",
    ]
    pages = [params for method, path, params, _ in matrix.calls if path == "/_matrix/client/v3/publicRooms"]
    assert [page.get("since") for page in pages] == [None, "p2"]


def test_keeps_the_sync_token_in_a_file(tmp_path):
    state = ids_bot.State(tmp_path / "nested" / "state.json")
    assert state.load() is None
    state.save("s1")
    assert state.load() == "s1"
    state.path.write_text("not json", encoding="utf-8")
    assert state.load() is None
    state.forget()
    assert not state.path.exists()


def test_reads_the_token_from_its_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret\n", encoding="utf-8")
    assert ids_bot.read_token(path) == "secret"
