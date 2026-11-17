"""Big Fat Fish: what it answers, what it files, and what it keeps in the log."""

from __future__ import annotations

import json
import sqlite3

import fish_bot
import pytest

USER = "@bigfatfish:example"
ROOM = "!room:example"


def event(body: str, *, sender: str = "@reader:example", event_id: str = "$event",
          ts: int | None = None, msgtype: str = "m.text", **content):
    return {
        "type": "m.room.message",
        "sender": sender,
        "event_id": event_id,
        "room_id": ROOM,
        "origin_server_ts": ts if ts is not None else fish_bot.now_ms(),
        "content": {"msgtype": msgtype, "body": body, **content},
    }


def message_row(body: str, *, sender: str = "@reader:example", ts: int = 1_700_000_000_000) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE events(sender TEXT, ts INTEGER, body TEXT)")
    connection.execute("INSERT INTO events(sender, ts, body) VALUES(?,?,?)", (sender, ts, body))
    return connection.execute("SELECT * FROM events").fetchone()


class FakeMatrix:
    def __init__(self, *, messages=None, sent=None):
        self._messages = messages or {"chunk": []}
        self.sent = sent if sent is not None else []
        self.transactions = []

    def whoami(self):
        return USER

    def sync(self, since, timeout_ms=0):
        return {"next_batch": "s1"}

    def messages(self, room_id, *, limit=100, from_token=None):
        return self._messages

    def room_name(self, room_id):
        return "Test room"

    def room_topic(self, room_id):
        return "A room for testing"

    def member_name(self, room_id, user_id):
        return "Alice" if user_id == "@reader:example" else ""

    def send(self, room_id, content, transaction_id):
        self.sent.append((room_id, content, transaction_id))
        self.transactions.append(transaction_id)
        return "$sent"

    def joined_rooms(self):
        return set()

    def public_rooms(self):
        return []

    def join(self, room_id):
        return None


class FakeDeepSeek:
    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error
        self.calls = []

    def decide(self, room_name, room_topic, events, names, call, open_issues):
        self.calls.append((room_name, room_topic, events, names, call, open_issues))
        if self.error is not None:
            raise self.error
        return self.decision


class FakeGitHub:
    repo = "mkpoli/glyph-atlas"

    def __init__(self, issues=None, error=None):
        self.issues = issues or []
        self.error = error
        self.created = []

    def open_issues(self):
        return self.issues

    def create_issue(self, title, body, labels):
        if self.error is not None:
            raise self.error
        self.created.append((title, body, tuple(labels)))
        return fish_bot.IssueRef(number=42, title=title, labels=tuple(labels),
                                 url="https://github.com/mkpoli/glyph-atlas/issues/42")


def decision(*, open=True, confidence=0.9, title="Add a test", reply="Test reply",
             body="## Context\nA test.", labels=("enhancement",), duplicate_of=None):
    return fish_bot.Decision(open=open, confidence=confidence, title=title, body=body,
                             labels=labels, reply=reply, reason="Because it is concrete.",
                             duplicate_of=duplicate_of)


def make_bot(tmp_path, *, decision_value=None, github=None, config=None, matrix=None):
    config = config or fish_bot.Config(
        user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
        context_messages=20, min_confidence=0.7, max_issues_per_day=5,
    )
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    fake_matrix = matrix or FakeMatrix()
    bot = fish_bot.Bot(config, store, fake_matrix, FakeDeepSeek(decision_value), github,
                       fish_bot.State(tmp_path / "state.json"))
    return bot, fake_matrix, store


def test_reads_a_mention_by_mxid_and_display_name():
    call = fish_bot.call_of(event("Hey @bigfatfish:example, look"), USER, "Big Fat Fish")
    assert call is not None
    assert call.mode == "auto"
    assert fish_bot.call_of(event("Big Fat Fish, look"), USER, "Big Fat Fish") is not None
    assert fish_bot.call_of(event("@bigfatfish look"), USER, "Big Fat Fish") is not None


def test_reads_explicit_command_modes():
    review = fish_bot.call_of(event("@bigfatfish !fish review"), USER, "Big Fat Fish")
    issue = fish_bot.call_of(event("@bigfatfish !fish issue"), USER, "Big Fat Fish")
    assert review is not None and review.mode == "review"
    assert issue is not None and issue.mode == "issue"
    assert fish_bot.call_of(event("@bigfatfish please review this"), USER, "Big Fat Fish").mode == "auto"
    assert fish_bot.call_of(event("@bigfatfish review only"), USER, "Big Fat Fish").mode == "review"
    assert fish_bot.call_of(event("@bigfatfish open an issue"), USER, "Big Fat Fish").mode == "issue"


def test_leaves_own_messages_bots_and_edits_alone():
    assert fish_bot.call_of(event("@bigfatfish", sender=USER), USER, "Big Fat Fish") is None
    assert fish_bot.call_of(event("@bigfatfish", sender="@discordbot:example"), USER, "Big Fat Fish") is None
    assert fish_bot.call_of(event("@bigfatfish", sender="@bridge:example"), USER, "Big Fat Fish") is None
    assert fish_bot.call_of(event("@bigfatfish", sender="@discord_123:example"), USER, "Big Fat Fish") is not None
    edit = event("@bigfatfish", **{"m.new_content": {"msgtype": "m.text", "body": "@bigfatfish"}})
    assert fish_bot.call_of(edit, USER, "Big Fat Fish") is None


def test_ignores_empty_and_malformed_messages():
    assert fish_bot.call_of({"type": "m.room.message", "sender": "@a:example", "content": []},
                            USER, "Big Fat Fish") is None
    assert fish_bot.call_of(event("@bigfatfish", msgtype="m.notice"), USER, "Big Fat Fish") is None
    assert fish_bot.call_of(event("@bigfatfish", msgtype="m.image"), USER, "Big Fat Fish") is None


def test_prompt_removes_the_call_and_keeps_the_request():
    call = fish_bot.call_of(event("@bigfatfish:example !fish issue support PUA fonts"), USER, "Big Fat Fish")
    assert call == fish_bot.Call(mode="issue", prompt="support PUA fonts")


def test_context_names_the_room_the_issues_and_the_call():
    rows = [message_row("first", ts=1), message_row("second", ts=2)]
    text = fish_bot.format_context("Dev", "Code talk", rows, {"@reader:example": "Alice"},
                                   fish_bot.Call("auto", "should this be filed?"),
                                   [fish_bot.IssueRef(7, "Existing issue", (), "u")])
    assert "Room: Dev" in text
    assert "#7 Existing issue" in text
    assert "[1970-01-01 09:00] Alice: first" in text
    assert "Call: should this be filed?" in text
    assert "Mode: decide whether to file" in text


def test_model_prompt_carries_the_repository_without_formatting_its_json():
    model = fish_bot.DeepSeek("key", repo="owner/repo")
    sent = []

    def post(body):
        sent.append(body)
        return {"choices": [{"message": {"content": json.dumps({
            "open": False, "reply": "No.", "confidence": 0.1, "title": "", "body": "", "labels": [],
        })}}]}

    model._post = post
    model.decide("Room", "Topic", [], {}, fish_bot.Call("auto", "look"), [])
    system = sent[0]["messages"][0]["content"]
    assert "owner/repo" in system
    assert '"open": true or false' in system


def test_context_keeps_the_newest_messages_when_the_log_is_long():
    rows = [message_row("old " + "x" * 20_000, ts=1),
            message_row("new " + "y" * 20_000, ts=2),
            message_row("newest " + "z" * 20_000, ts=3)]
    text = fish_bot.format_context("Dev", "", rows, {"@reader:example": "Alice"},
                                   fish_bot.Call("auto", "look"), [])
    assert len(text) < fish_bot.MAX_CONTEXT_CHARS + 2_000
    assert "newest" in text
    assert "[older messages omitted]" in text


def test_parses_a_complete_decision():
    raw = json.dumps({
        "open": True,
        "confidence": 0.82,
        "title": "Support PUA fonts",
        "body": "## Context\nA font is missing.",
        "labels": ["enhancement", "not-a-label"],
        "reply": "Filed it.",
        "reason": "Concrete request.",
        "duplicate_of": None,
    })
    parsed = fish_bot.parse_decision(raw, [])
    assert parsed.open is True
    assert parsed.confidence == 0.82
    assert parsed.labels == ("enhancement",)
    assert parsed.title == "Support PUA fonts"


def test_refuses_a_decision_without_the_required_shape():
    with pytest.raises(TypeError):
        fish_bot.parse_decision(json.dumps({"open": "yes", "reply": "x"}), [])
    with pytest.raises(ValueError):
        fish_bot.parse_decision(json.dumps({"open": True, "reply": "x"}), [])
    with pytest.raises(ValueError):
        fish_bot.parse_decision(json.dumps({"open": False, "reply": ""}), [])


def test_keeps_only_known_open_issue_numbers_as_duplicates():
    issues = [fish_bot.IssueRef(7, "Existing", (), "u")]
    raw = json.dumps({"open": True, "title": "T", "body": "B", "reply": "R", "duplicate_of": 7})
    assert fish_bot.parse_decision(raw, issues).duplicate_of == 7
    raw = json.dumps({"open": True, "title": "T", "body": "B", "reply": "R", "duplicate_of": 99})
    assert fish_bot.parse_decision(raw, issues).duplicate_of is None


def test_duplicate_titles_are_close_enough_only_when_they_are():
    assert fish_bot.similar_title("Seed crop boxes from OCR", "Seed crop boxes from OCR detections")
    assert not fish_bot.similar_title("Seed crop boxes from OCR", "Add Tangut fonts")
    existing = fish_bot.IssueRef(9, "Seed crop boxes from OCR", (), "u")
    assert fish_bot.find_duplicate("Seed crop boxes from OCR detections", [existing]) is existing


def test_issue_body_adds_a_room_and_participant_footer():
    rows = [{"sender": "@a:example"}, {"sender": "@b:example"}, {"sender": "@a:example"}]
    body = fish_bot.issue_body(decision(), ROOM, "Dev", rows,
                               {"@a:example": "Alice", "@b:example": "Bob"}, "2026-11-17",
                               bot_user_id=USER)
    assert "From the Matrix discussion in [#Dev](https://matrix.to/#/!room:example)" in body
    assert "Participants: Alice, Bob" in body
    assert "Big Fat Fish" not in body
    assert len(body) <= fish_bot.MAX_BODY


def test_issue_body_leaves_the_bot_out_of_the_participants():
    rows = [{"sender": "@a:example"}, {"sender": USER}]
    body = fish_bot.issue_body(decision(), ROOM, "Dev", rows,
                               {"@a:example": "Alice", USER: "Big Fat Fish"}, "2026-11-17",
                               bot_user_id=USER)
    assert "Participants: Alice." in body


def test_store_keeps_events_in_order_and_decisions_once(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    assert store.add_event("$1", ROOM, "@a:example", 1000, "first", "m.text", backfilled=True)
    assert not store.add_event("$1", ROOM, "@a:example", 1000, "first", "m.text", backfilled=True)
    store.add_event("$2", ROOM, "@b:example", 2000, "second", "m.text", backfilled=False)
    rows = store.context(ROOM, 2000, 10)
    assert [row["body"] for row in rows] == ["first", "second"]
    assert store.handled("$2") is False
    store.mark_handled("$2", "opened")
    assert store.handled("$2") is True
    store.add_issue("mkpoli/glyph-atlas", 1, "T", "u", ROOM, "$2")
    assert store.issues_today(fish_bot.local_day(fish_bot.now_ms())) == 1
    store.close()


def test_store_prunes_old_text(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    now = fish_bot.now_ms()
    store.add_event("$old", ROOM, "@a:example", now - 10 * 86_400_000, "old", "m.text", backfilled=True)
    store.add_event("$new", ROOM, "@a:example", now - 86_400_000, "new", "m.text", backfilled=True)
    store.prune(now - 5 * 86_400_000)
    assert [row["body"] for row in store.context(ROOM, now, 10)] == ["new"]
    store.close()


def test_store_keeps_a_pending_proposal(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    store.set_pending(ROOM, decision(), "$event")
    pending = store.pending(ROOM)
    assert pending is not None
    assert pending[0].title == "Add a test"
    store.clear_pending(ROOM)
    assert store.pending(ROOM) is None
    store.close()


def test_sends_a_reply_with_one_transaction_per_trigger():
    matrix = fish_bot.Matrix("http://example", "token")
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"event_id": "$sent"}

    matrix.call = call
    matrix.send(ROOM, {"msgtype": "m.text", "body": "hello"}, "fish-abc")
    assert calls[0][0] == "PUT"
    assert calls[0][1] == "/_matrix/client/v3/rooms/%21room%3Aexample/send/m.room.message/fish-abc"


def test_github_lists_open_issues_without_pull_requests():
    github = fish_bot.GitHub("token", "mkpoli/glyph-atlas")
    github._call = lambda method, path, body=None: [
        {"number": 7, "title": "Seven", "html_url": "u7", "labels": [{"name": "bug"}]},
        {"number": 8, "title": "A pull request", "html_url": "u8", "pull_request": {}},
    ]
    issues = github.open_issues()
    assert [issue.number for issue in issues] == [7]
    assert issues[0].labels == ("bug",)


def test_github_creates_an_issue_with_known_labels_only():
    github = fish_bot.GitHub("token", "mkpoli/glyph-atlas")
    seen = {}

    def call(method, path, body=None):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        return {"number": 42, "html_url": "https://example/42", "title": body["title"]}

    github._call = call
    issue = github.create_issue("A title", "A body", ("enhancement", "not-a-label"))
    assert issue.number == 42
    assert seen["method"] == "POST"
    assert seen["path"] == "/repos/mkpoli/glyph-atlas/issues"
    assert seen["body"]["labels"] == ["enhancement"]


def test_initial_sync_does_not_answer_old_mentions(tmp_path):
    matrix = FakeMatrix()
    response = {"next_batch": "s1", "rooms": {"join": {ROOM: {"timeline": {"events": [
        event("@bigfatfish old question", event_id="$old"),
    ]}}}}}
    matrix.sync = lambda since, timeout_ms=0: response
    bot, _, store = make_bot(tmp_path, matrix=matrix)
    assert bot.initial_sync() == "s1"
    assert matrix.sent == []
    assert store.event_count(ROOM) == 1


def test_declines_a_discussion_that_is_not_an_issue(tmp_path):
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(open=False, reply="That is just chat."))
    trigger = event("@bigfatfish should this be an issue?", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.sent
    assert matrix.sent[0][1]["body"] == "That is just chat."
    assert store.outcome("$call")[0] == "declined"


def test_review_stores_a_proposal_without_filing_it(tmp_path):
    github = FakeGitHub()
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), github=github)
    trigger = event("@bigfatfish !fish review", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert github.created == []
    assert store.pending(ROOM) is not None
    assert "Reply `!fish issue`" in matrix.sent[0][1]["body"]


def test_review_does_not_store_a_declined_discussion(tmp_path):
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(open=False, reply="No."))
    trigger = event("@bigfatfish !fish review", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert store.pending(ROOM) is None
    assert matrix.sent[0][1]["body"] == "No."


def test_files_an_issue_and_replies_with_its_number(tmp_path):
    github = FakeGitHub()
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), github=github)
    trigger = event("@bigfatfish this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert len(github.created) == 1
    title, body, labels = github.created[0]
    assert title == "Add a test"
    assert "From the Matrix discussion" in body
    assert labels == ("enhancement",)
    assert "#42: https://github.com/mkpoli/glyph-atlas/issues/42" in matrix.sent[0][1]["body"]
    assert store.outcome("$call")[0] == "opened"


def test_dry_run_never_calls_github_and_needs_no_token(tmp_path):
    config = fish_bot.Config(user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
                             context_messages=20, dry_run=True)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), config=config)
    trigger = event("@bigfatfish this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.sent[0][1]["body"].startswith("[dry run]")
    assert store.outcome("$call")[0] == "dry-run"


def test_daily_limit_stops_a_file(tmp_path):
    github = FakeGitHub()
    config = fish_bot.Config(user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
                             context_messages=20, max_issues_per_day=0)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), github=github, config=config)
    trigger = event("@bigfatfish this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert github.created == []
    assert store.outcome("$call")[0] == "daily-limit"
    assert "issue limit" in matrix.sent[0][1]["body"]


def test_cooldown_marks_without_answering(tmp_path):
    bot, matrix, store = make_bot(tmp_path, decision_value=decision())
    bot.config = fish_bot.dataclasses.replace(bot.config, room_cooldown_seconds=3600)
    first = event("@bigfatfish one", event_id="$one")
    second = event("@bigfatfish two", event_id="$two")
    bot.ingest(ROOM, first, backfilled=False)
    bot.ingest(ROOM, second, backfilled=False)
    bot.answer(ROOM, first)
    bot.answer(ROOM, second)
    assert len(matrix.sent) == 1
    assert store.outcome("$two")[0] == "cooldown"


def test_config_reads_toml_and_resolves_relative_paths(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[matrix]
homeserver = "http://127.0.0.1:6167"
token_file = "matrix-token"

[deepseek]
api_key_file = "deepseek-key"
model = "deepseek-flash"

[github]
repo = "mkpoli/glyph-atlas"

[bot]
user_id = "@bigfatfish:glyphatlas.org"
display_name = "Big Fat Fish"
rooms = ["!one:example"]

[limits]
context_messages = 80
min_confidence = 0.8
""", encoding="utf-8")
    config = fish_bot.Config.load(config_path)
    assert config.token_file == tmp_path / "matrix-token"
    assert config.deepseek_key_file == tmp_path / "deepseek-key"
    assert config.github_repo == "mkpoli/glyph-atlas"
    assert config.room_ids == ("!one:example",)
    assert config.context_messages == 80
    assert config.min_confidence == 0.8
    assert config.retention_days == 180


def test_finite_refuses_nan_and_infinity():
    assert fish_bot._finite("0.5", 0.3) == 0.5
    assert fish_bot._finite("nan", 0.3) == 0.3
    assert fish_bot._finite("inf", 0.3) == 0.3
    assert fish_bot._finite(None, 0.3) == 0.3
