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
    connection.execute("CREATE TABLE events(event_id TEXT, sender TEXT, ts INTEGER, body TEXT)")
    connection.execute("INSERT INTO events(event_id, sender, ts, body) VALUES(?,?,?,?)", ("$row", sender, ts, body))
    return connection.execute("SELECT * FROM events").fetchone()


class FakeMatrix:
    def __init__(self, *, messages=None, sent=None):
        self._messages = messages or {"chunk": []}
        self.sent = sent if sent is not None else []
        self.events = []
        self.uploads = []
        self.typed = []
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

    def send(self, room_id, content, transaction_id, event_type="m.room.message"):
        if event_type != "m.room.message":
            self.events.append((room_id, event_type, content))
            return "$event"
        self.sent.append((room_id, content, transaction_id))
        self.transactions.append(transaction_id)
        return "$sent"

    def typing(self, room_id, user_id, *, timeout_ms=15_000):
        self.typed.append(room_id)

    def resolve(self, alias):
        return {"#ja:example": "!ja:example"}[alias]

    def upload(self, data, content_type, filename):
        self.uploads.append((len(data), content_type, filename))
        return f"mxc://example/{filename}"

    def joined_rooms(self):
        return set()

    def public_rooms(self):
        return []

    def join(self, room_id):
        return None


class FakeDeepSeek:
    def __init__(self, decision=None, error=None, act=None):
        self.decision = decision
        self.error = error
        self.act_value = act or fish_bot.Act(say=("嗨～本鯨在這裡！",), react_to=None, react="", issue=False)
        self.calls = []
        self.turns = []
        self.digests = []

    def digest(self, language, room_name, room_lines, changes, other_rooms):
        self.digests.append((language, room_name, changes, other_rooms))
        return f"digest in {language}"

    def act(self, room_name, room_topic, events, names, *, task, trigger, bot_user_id, stickers=(), recent=()):
        self.turns.append((task, trigger, [row["event_id"] for row in events]))
        self.sticker_offers = (tuple(stickers), tuple(recent))
        if self.error is not None:
            raise self.error
        return self.act_value

    def decide(self, room_name, room_topic, events, names, call, open_issues):
        self.calls.append((room_name, room_topic, events, names, call, open_issues))
        if self.error is not None:
            raise self.error
        return self.decision


class FakeGitHub:
    repo = "mkpoli/glyph-atlas"
    merged = ()

    def changes(self, repo, since):
        return {"repo": repo, "merged": list(self.merged), "released": []}

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


def make_bot(tmp_path, *, decision_value=None, github=None, config=None, matrix=None, act=None):
    config = config or fish_bot.Config(
        user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
        context_messages=20, min_confidence=0.7, max_issues_per_day=5,
    )
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    fake_matrix = matrix or FakeMatrix()
    bot = fish_bot.Bot(config, store, fake_matrix, FakeDeepSeek(decision_value, act=act), github,
                       fish_bot.State(tmp_path / "state.json"))
    return bot, fake_matrix, store


def test_reads_a_mention_by_mxid_and_display_name():
    call = fish_bot.call_of(event("Hey @bigfatfish:example, look"), USER, "Big Fat Fish")
    assert call is not None
    assert call.mode == "chat"
    assert fish_bot.call_of(event("Big Fat Fish, look"), USER, "Big Fat Fish") is not None
    assert fish_bot.call_of(event("@bigfatfish look"), USER, "Big Fat Fish") is not None


def test_reads_explicit_command_modes():
    review = fish_bot.call_of(event("@bigfatfish !fish review"), USER, "Big Fat Fish")
    issue = fish_bot.call_of(event("@bigfatfish !fish issue"), USER, "Big Fat Fish")
    assert review is not None and review.mode == "review"
    assert issue is not None and issue.mode == "issue"
    assert fish_bot.call_of(event("@bigfatfish please review this"), USER, "Big Fat Fish").mode == "chat"
    assert fish_bot.call_of(event("@bigfatfish open an issue"), USER, "Big Fat Fish").mode == "chat"
    assert fish_bot.call_of(event("!fish 大肥魚，你好呀！"), USER, "大肥魚").mode == "chat"
    assert fish_bot.call_of(event("/fish review"), USER, "大肥魚").mode == "review"
    assert fish_bot.call_of(event("the shell config is in a .fish file"), USER, "大肥魚") is None
    assert fish_bot.call_of(event("see ~/.config/fish file"), USER, "大肥魚") is None


def test_leaves_own_messages_bots_and_edits_alone():
    assert fish_bot.call_of(event("@bigfatfish", sender=USER), USER, "Big Fat Fish") is None
    ignored = ("@discordbot:example",)
    assert fish_bot.call_of(event("@bigfatfish", sender="@discordbot:example"), USER, "Big Fat Fish", ignored) is None
    assert fish_bot.call_of(event("@bigfatfish", sender="@discord_123:example"), USER, "Big Fat Fish", ignored) is not None
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
    payload = json.loads(text)
    assert payload["room"] == {"name": "Dev", "topic": "Code talk"}
    assert payload["open_issues"] == [{"number": 7, "title": "Existing issue"}]
    assert payload["messages"][0]["time"] == "1970-01-01 09:00"
    assert payload["messages"][0]["sender"] == "Alice (@reader:example)"
    assert payload["messages"][0]["body"] == "first"
    assert payload["call"] == {"mode": "auto", "request": "should this be filed?"}


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
    payload = json.loads(text)
    assert len(text) < fish_bot.MAX_CONTEXT_CHARS + 3_000
    assert payload["messages"][-1]["body"].startswith("newest")


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
    assert "Participants: Alice (@a:example), Bob (@b:example)" in body
    assert "Big Fat Fish" not in body
    assert len(body) <= fish_bot.MAX_BODY


def test_issue_body_leaves_the_bot_out_of_the_participants():
    rows = [{"sender": "@a:example"}, {"sender": USER}]
    body = fish_bot.issue_body(decision(), ROOM, "Dev", rows,
                               {"@a:example": "Alice", USER: "Big Fat Fish"}, "2026-11-17",
                               bot_user_id=USER)
    assert "Participants: Alice (@a:example)." in body


def test_store_keeps_events_in_order_and_decisions_once(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    assert store.add_event("$1", ROOM, "@a:example", 1000, "first", "m.text", backfilled=True)
    assert not store.add_event("$1", ROOM, "@a:example", 1000, "first", "m.text", backfilled=True)
    store.add_event("$1", ROOM, "@a:example", 1000, "first", "m.text", backfilled=False)
    assert store.db.execute("SELECT backfilled FROM events WHERE event_id='$1'").fetchone()["backfilled"] == 0
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


def test_process_ignores_a_room_outside_the_active_set(tmp_path):
    bot, matrix, store = make_bot(tmp_path)
    bot.active_rooms = set()
    response = {"rooms": {"join": {ROOM: {"timeline": {"events": [event("@bigfatfish")]}}}}}
    bot.process(response)
    assert store.event_count(ROOM) == 0
    assert matrix.sent == []


def test_redaction_removes_the_stored_text(tmp_path):
    bot, _, store = make_bot(tmp_path)
    bot.ingest(ROOM, event("remove me", event_id="$gone"), backfilled=False)
    assert store.event_count(ROOM) == 1
    bot.redact({"type": "m.room.redaction", "redacts": "$gone"})
    assert store.event_count(ROOM) == 0


def test_initial_sync_does_not_answer_old_mentions(tmp_path):
    matrix = FakeMatrix()
    response = {"next_batch": "s1", "rooms": {"join": {ROOM: {"timeline": {"events": [
        event("@bigfatfish old question", event_id="$old"),
    ]}}}}}
    matrix.sync = lambda since, timeout_ms=0: response
    bot, _, store = make_bot(tmp_path, matrix=matrix)
    bot.active_rooms = {ROOM}
    assert bot.initial_sync() == "s1"
    assert matrix.sent == []
    assert store.event_count(ROOM) == 1


def test_declines_a_discussion_that_is_not_an_issue(tmp_path):
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(open=False, reply="That is just chat."))
    trigger = event("!fish review should this be an issue?", event_id="$call")
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
    trigger = event("!fish issue this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert len(github.created) == 1
    title, body, labels = github.created[0]
    assert title == "Add a test"
    assert "From the Matrix discussion" in body
    assert labels == ("enhancement",)
    assert "#42: https://github.com/mkpoli/glyph-atlas/issues/42" in matrix.sent[0][1]["body"]
    assert store.outcome("$call")[0] == "opened"


def test_issue_command_does_not_bypass_the_confidence_floor(tmp_path):
    github = FakeGitHub()
    bot, _matrix, store = make_bot(tmp_path, decision_value=decision(confidence=0.2), github=github)
    trigger = event("@bigfatfish !fish issue", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert github.created == []
    assert store.pending(ROOM) is not None
    assert store.outcome("$call")[0] == "low-confidence"


def test_file_rechecks_duplicates_before_filing(tmp_path):
    github = FakeGitHub(issues=[fish_bot.IssueRef(7, "Add a test", (), "u7")])
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), github=github)
    trigger = event("@bigfatfish !fish issue", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    store.set_pending(ROOM, decision(), "$old")
    bot.answer(ROOM, trigger)
    assert github.created == []
    assert store.outcome("$call")[0] == "duplicate"
    assert "already open as #7" in matrix.sent[0][1]["body"]


def test_reply_keeps_the_issue_link_when_the_text_is_long():
    long = "x" * 10_000
    reply = fish_bot.reply_for(decision(reply=long), fish_bot.IssueRef(7, "T", (), "https://example/7"),
                               dry_run=False)
    assert "https://example/7" in reply
    assert len(reply) <= fish_bot.MAX_REPLY


def test_ingest_skips_an_edit(tmp_path):
    bot, _, store = make_bot(tmp_path)
    edit = event("new", **{"m.new_content": {"msgtype": "m.text", "body": "new"},
                          "m.relates_to": {"rel_type": "m.replace", "event_id": "$old"}})
    bot.ingest(ROOM, edit, backfilled=False)
    assert store.event_count(ROOM) == 0


def test_dry_run_never_calls_github_and_needs_no_token(tmp_path):
    config = fish_bot.Config(user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
                             context_messages=20, dry_run=True)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), config=config)
    store.set_pending(ROOM, decision(), "$old")
    trigger = event("!fish issue this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.sent[0][1]["body"].startswith("[dry run]")
    assert store.outcome("$call")[0] == "dry-run"
    assert store.pending(ROOM) is None


def test_daily_limit_stops_a_file(tmp_path):
    github = FakeGitHub()
    config = fish_bot.Config(user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
                             context_messages=20, max_issues_per_day=0)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), github=github, config=config)
    trigger = event("!fish issue this should be fixed", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert github.created == []
    assert store.outcome("$call")[0] == "daily-limit"
    assert "issue limit" in matrix.sent[0][1]["body"]


def test_call_budget_stops_a_second_call(tmp_path):
    config = fish_bot.Config(user_id=USER, display_name="Big Fat Fish", room_cooldown_seconds=0,
                             context_messages=20, max_calls_per_hour=1)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision(), config=config)
    first = event("!fish review one", event_id="$one")
    second = event("!fish review two", event_id="$two")
    bot.ingest(ROOM, first, backfilled=False)
    bot.ingest(ROOM, second, backfilled=False)
    bot.answer(ROOM, first)
    bot.answer(ROOM, second)
    assert len(matrix.sent) == 1
    assert store.outcome("$two")[0] == "call-limit"


def test_cooldown_marks_without_answering(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_bot.time, "monotonic", lambda: 10.0)
    bot, matrix, store = make_bot(tmp_path, decision_value=decision())
    bot.config = fish_bot.dataclasses.replace(bot.config, room_cooldown_seconds=3600)
    first = event("!fish review one", event_id="$one")
    second = event("!fish review two", event_id="$two")
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


def test_config_without_a_bot_table_uses_the_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[matrix]\nhomeserver = \"http://127.0.0.1:6167\"\n", encoding="utf-8")
    assert fish_bot.Config.load(path).room_ids == ()


def act(*lines, react_to=None, react="", issue=False, sticker=""):
    return fish_bot.Act(say=tuple(lines), react_to=react_to, react=react, issue=issue, sticker=sticker)


def test_reads_a_call_by_a_han_alias_inside_a_sentence():
    aliases = ("大肥魚", "大肥鱼")
    assert fish_bot.call_of(event("大肥魚你好呀"), USER, "大肥魚", (), aliases).mode == "chat"
    assert fish_bot.call_of(event("大肥鱼来看看"), USER, "大肥魚", (), aliases) is not None
    assert fish_bot.call_of(event("這個字怎麼讀，大肥魚？"), USER, "大肥魚", (), aliases) is not None
    assert fish_bot.call_of(event("大肥魚さん、これ見て"), USER, "大肥魚", (), aliases) is not None
    for prose in ("今天吃鯨魚", "今晚吃了个大肥鱼头", "减肥鱼油有用吗", "大肥鱼V4发布了", "大肥鱼发布了新模型"):
        assert fish_bot.call_of(event(prose), USER, "大肥魚", (), aliases) is None, prose
    assert fish_bot.call_of(event("bigfatfishy"), USER, "Big Fat Fish") is None


def test_answers_a_call_as_a_reply_and_introduces_itself_the_first_time(tmp_path):
    bot, matrix, store = make_bot(tmp_path, act=act("嗨嗨～", "本鯨是大肥魚！"))
    trigger = event("大肥魚，你好呀！", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.turns[0][0] == "called-intro"
    assert [content["body"] for _, content, _ in matrix.sent] == ["嗨嗨～", "本鯨是大肥魚！"]
    assert matrix.sent[0][1]["m.relates_to"] == {"m.in_reply_to": {"event_id": "$call"}}
    assert "m.relates_to" not in matrix.sent[1][1]
    assert store.outcome("$call")[0] == "chatted"
    assert store.introduced(ROOM)
    again = event("大肥魚，再見", event_id="$again")
    bot.ingest(ROOM, again, backfilled=False)
    bot.answer(ROOM, again)
    assert bot.deepseek.turns[1][0] == "called"


def test_says_hello_once_on_the_first_message_in_a_room(tmp_path):
    bot, matrix, store = make_bot(tmp_path, act=act("大家好，我是大肥魚！"))
    for number in range(2):
        message = event(f"hello {number}", event_id=f"$m{number}")
        bot.ingest(ROOM, message, backfilled=False)
        bot.notice(ROOM, message)
    assert [turn[0] for turn in bot.deepseek.turns] == ["intro"]
    assert len(matrix.sent) == 1
    assert "m.relates_to" not in matrix.sent[0][1]
    assert store.introduced(ROOM)


def test_a_chat_that_asks_for_an_issue_reaches_the_issue_desk(tmp_path):
    github = FakeGitHub()
    bot, _matrix, store = make_bot(tmp_path, decision_value=decision(), github=github,
                                  act=act("好，本鯨去記下來～", issue=True))
    trigger = event("大肥魚 can you open an issue for this?", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert len(github.created) == 1
    assert store.outcome("$call")[0] == "opened"


def test_reacts_to_the_message_the_model_picks(tmp_path):
    bot, matrix, _ = make_bot(tmp_path, act=act("好可愛", react_to=0, react="🐳"))
    trigger = event("@bigfatfish look", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.events == [(ROOM, "m.reaction", {"m.relates_to": {
        "rel_type": "m.annotation", "event_id": "$call", "key": "🐳"}})]


def test_chat_budget_stops_a_flood_without_answering(tmp_path):
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20,
                             max_chats_per_user_per_hour=1)
    bot, matrix, store = make_bot(tmp_path, config=config)
    store.mark_introduced(ROOM)
    for number in range(2):
        message = event(f"@bigfatfish {number}", event_id=f"$c{number}")
        bot.ingest(ROOM, message, backfilled=False)
        bot.answer(ROOM, message)
    assert len(matrix.sent) == 1
    assert store.outcome("$c1")[0] == "chat-limit"


def test_an_issue_asked_in_chat_obeys_the_issue_limits(tmp_path):
    github = FakeGitHub()
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20,
                             max_calls_per_user_per_hour=1)
    bot, _matrix, store = make_bot(tmp_path, decision_value=decision(), github=github, config=config,
                                   act=act("好～", issue=True))
    store.mark_introduced(ROOM)
    for number in range(2):
        message = event(f"大肥魚 please file this {number}", event_id=f"$i{number}")
        bot.ingest(ROOM, message, backfilled=False)
        bot.answer(ROOM, message)
    assert len(bot.deepseek.calls) == 1


def test_parse_act_keeps_a_few_bubbles_and_a_valid_reaction():
    raw = json.dumps({"say": "one\ntwo\n\nthree\nfour", "react": {"to": 1, "emoji": "🐳"}, "issue": False})
    parsed = fish_bot.parse_act(raw, 2)
    assert parsed.say == ("one two", "three", "four")
    assert (parsed.react_to, parsed.react, parsed.issue) == (1, "🐳", False)
    assert fish_bot.parse_act(json.dumps({"say": "", "react": {"to": 5, "emoji": "🐳"}}), 2).react_to is None
    assert fish_bot.parse_act(json.dumps({"say": "x", "react": {"to": 0, "emoji": "two words"}}), 2).react == ""
    assert fish_bot.parse_act(json.dumps({"say": "x", "issue": "yes"}), 1).issue is False
    with pytest.raises(TypeError):
        fish_bot.parse_act(json.dumps({"say": ["x"]}), 1)


def test_chat_turn_turns_thinking_off_and_names_the_bot_in_the_log():
    model = fish_bot.DeepSeek("key")
    seen = {}

    def post(body):
        seen.update(body)
        return {"choices": [{"message": {"content": json.dumps({"say": "hi", "react": None, "issue": False})}}]}

    model._post = post
    rows = [message_row("hello", sender="@reader:example"), message_row("hi!", sender=USER)]
    result = model.act("Room", "Topic", rows, {"@reader:example": "Alice"}, task="called", trigger=0,
                       bot_user_id=USER)
    assert result.say == ("hi",)
    assert seen["thinking"] == {"type": "disabled"}
    assert seen["temperature"] == fish_bot.CHAT_TEMPERATURE
    payload = json.loads(seen["messages"][1]["content"])
    assert [m["sender"] for m in payload["messages"]] == ["Alice", "大肥魚 (you)"]
    assert payload["task"].startswith("Message 0 talks to you")


def test_uploads_each_sticker_once_and_only_when_its_hash_matches(tmp_path, monkeypatch):
    first, second = fish_bot.STICKERS[:2]
    monkeypatch.setattr(fish_bot, "STICKERS", (first, second))
    good = b"good image"
    first = fish_bot.dataclasses.replace(first, sha256=fish_bot.hashlib.sha256(good).hexdigest())
    monkeypatch.setattr(fish_bot, "STICKERS", (first, second))
    bot, matrix, store = make_bot(tmp_path)
    bot.ensure_stickers(fetch=lambda url: good)
    assert [name for _, _, name in matrix.uploads] == [f"dafeiyu-{first.name}.png"]
    assert set(store.stickers()) == {first.name}
    bot.ensure_stickers(fetch=lambda url: good)
    assert len(matrix.uploads) == 1


def test_sends_the_sticker_the_model_picks_after_its_line(tmp_path):
    bot, matrix, store = make_bot(tmp_path, act=act("好耶", sticker="yay"))
    store.set_sticker("yay", next(s.sha256 for s in fish_bot.STICKERS if s.name == "yay"), "mxc://example/yay", 123)
    store.mark_introduced(ROOM)
    trigger = event("大肥魚 we shipped it!", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.sent[0][1]["body"] == "好耶"
    assert matrix.events[-1][1] == "m.sticker"
    assert matrix.events[-1][2]["url"] == "mxc://example/yay"
    assert bot.deepseek.sticker_offers[0] == ("yay",)
    trigger = event("大肥魚 again!", event_id="$again")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.sticker_offers[1] == ("yay",)


def test_parse_act_drops_a_sticker_it_does_not_have():
    raw = json.dumps({"say": "x", "sticker": "dragon"})
    assert fish_bot.parse_act(raw, 1, ("yay",)).sticker == ""
    assert fish_bot.parse_act(json.dumps({"say": "", "sticker": "yay"}), 1, ("yay",)).sticker == "yay"


def test_a_cut_download_leaves_the_sticker_out_instead_of_stopping(tmp_path, monkeypatch):
    def cut(request, timeout):
        raise fish_bot.http.client.IncompleteRead(b"half")

    monkeypatch.setattr(fish_bot.urllib.request, "urlopen", cut)
    with pytest.raises(OSError):
        fish_bot._download("https://example/x.png")
    bot, matrix, store = make_bot(tmp_path)
    bot.ensure_stickers()
    assert matrix.uploads == [] and store.stickers() == {}


def ambient_bot(tmp_path, monkeypatch, *, chance=1.0, act_value=None, **overrides):
    clock = {"now": 1000.0}
    monkeypatch.setattr(fish_bot.time, "monotonic", lambda: clock["now"])
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20,
                             ambient_chance=0.5, ambient_quiet_seconds=20, ambient_min_messages=2,
                             ambient_room_cooldown_seconds=900, ambient_per_hour=10, **overrides)
    bot, matrix, store = make_bot(tmp_path, config=config, act=act_value or act("哈哈哈好可愛"))
    bot._random = lambda: chance - 0.01 if chance > 0 else 1.0
    store.mark_introduced(ROOM)
    return bot, matrix, store, clock


def say_in_room(bot, body, event_id):
    message = event(body, event_id=event_id)
    bot.ingest(ROOM, message, backfilled=False)
    bot.notice(ROOM, message)


def test_joins_in_only_after_the_room_goes_quiet(tmp_path, monkeypatch):
    bot, matrix, _, clock = ambient_bot(tmp_path, monkeypatch, chance=0.5)
    say_in_room(bot, "this glyph is wild", "$a")
    say_in_room(bot, "right", "$b")
    bot.tick()
    assert matrix.sent == []
    clock["now"] += 21
    bot.tick()
    assert [turn[0] for turn in bot.deepseek.turns] == ["ambient"]
    assert matrix.sent[0][1]["body"] == "哈哈哈好可愛"
    assert matrix.typed == []
    assert "m.relates_to" not in matrix.sent[0][1]


def test_one_plain_message_or_an_unlucky_draw_stays_quiet(tmp_path, monkeypatch):
    bot, _matrix, _, clock = ambient_bot(tmp_path, monkeypatch, chance=0.5)
    say_in_room(bot, "ok", "$a")
    clock["now"] += 21
    bot.tick()
    assert bot.deepseek.turns == []
    bot._random = lambda: 0.99
    say_in_room(bot, "ok", "$b")
    say_in_room(bot, "sure", "$c")
    clock["now"] += 21
    bot.tick()
    assert bot.deepseek.turns == []


def test_a_lively_message_counts_alone(tmp_path, monkeypatch):
    bot, _, _, clock = ambient_bot(tmp_path, monkeypatch, chance=0.5)
    say_in_room(bot, "哈哈哈哈", "$a")
    clock["now"] += 21
    bot._random = lambda: 0.9  # under the doubled chance of 1.0
    bot.tick()
    assert [turn[0] for turn in bot.deepseek.turns] == ["ambient"]


def test_waits_out_the_room_cooldown_after_speaking(tmp_path, monkeypatch):
    bot, _matrix, _, clock = ambient_bot(tmp_path, monkeypatch, chance=0.5)
    for round_number in range(2):
        say_in_room(bot, "one", f"$a{round_number}")
        say_in_room(bot, "two", f"$b{round_number}")
        clock["now"] += 21
        bot.tick()
    assert len(bot.deepseek.turns) == 1


def test_never_files_from_an_ambient_turn(tmp_path, monkeypatch):
    github = FakeGitHub()
    bot, _, _, clock = ambient_bot(tmp_path, monkeypatch, act_value=act("x", issue=True))
    bot.github = github
    say_in_room(bot, "one", "$a")
    say_in_room(bot, "two", "$b")
    clock["now"] += 21
    bot.tick()
    assert github.created == []


def test_config_reads_the_chat_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[chat]\nambient_chance = 0.05\nambient_per_hour = 3\n", encoding="utf-8")
    config = fish_bot.Config.load(path)
    assert (config.ambient_chance, config.ambient_per_hour) == (0.05, 3)


def digest_bot(tmp_path, *, hour=21, merged=()):
    github = FakeGitHub()
    github.merged = merged
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20, digest_hour=hour,
                             digest_rooms=(("#ja:example", "Japanese"),), digest_repos=("mkpoli/glyph-atlas",))
    bot, matrix, store = make_bot(tmp_path, config=config, github=github)
    bot.active_rooms = {ROOM, "!ja:example"}
    return bot, matrix, store


def at(hour):
    return lambda: fish_bot.datetime.datetime(2026, 11, 19, hour, 0, tzinfo=fish_bot.JST)


def test_posts_the_digest_once_a_day_from_its_hour(tmp_path):
    bot, matrix, store = digest_bot(tmp_path, merged=[{"number": 1, "title": "Add X", "url": "u"}])
    bot.ingest(ROOM, event("we talked about 𠀀", event_id="$talk"), backfilled=False)
    bot._clock = at(20)
    bot.digest()
    assert matrix.sent == []
    bot._clock = at(21)
    bot.digest()
    bot.digest()
    assert [(room, content["body"]) for room, content, _ in matrix.sent] == [("!ja:example", "digest in Japanese")]
    _, _, changes, others = bot.deepseek.digests[0]
    assert changes[0]["merged"][0]["title"] == "Add X"
    assert [row["body"] for row in others["Test room"]] == ["we talked about 𠀀"]
    assert store.digested("2026-11-19", "!ja:example")


def test_skips_a_quiet_day_without_posting(tmp_path):
    bot, matrix, store = digest_bot(tmp_path)
    bot._clock = at(22)
    bot.digest()
    assert matrix.sent == [] and bot.deepseek.digests == []
    assert store.digested("2026-11-19", "!ja:example")


def test_digest_message_names_no_one(tmp_path):
    bot, _, store = digest_bot(tmp_path)
    bot.ingest(ROOM, event("secret plan", sender="@alice:example", event_id="$a"), backfilled=False)
    rows = store.since(0)
    payload = json.loads(fish_bot.format_digest("日本語", [], [{"repo": "r", "merged": [], "released": []}],
                                                {"Test room": rows[ROOM]}))
    assert payload["changes"] == []
    assert payload["other_rooms"] == [{"room": "Test room", "messages": ["secret plan"]}]
    assert "@alice" not in json.dumps(payload)


def test_config_reads_the_digest_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[digest]\nhour = 20\nrepos = ["mkpoli/han-compose"]\n'
                    '[digest.rooms]\n"#ja:glyphatlas.org" = "Japanese"\n', encoding="utf-8")
    config = fish_bot.Config.load(path)
    assert config.digest_hour == 20
    assert config.digest_rooms == (("#ja:glyphatlas.org", "Japanese"),)
    assert config.digest_repos == ("mkpoli/han-compose",)


def test_an_unknown_room_alias_is_looked_up_once_an_hour(tmp_path):
    bot, matrix, _ = digest_bot(tmp_path)
    lookups = []

    def resolve(alias):
        lookups.append(alias)
        raise fish_bot.MatrixError(404, "no such room")

    matrix.resolve = resolve
    bot._clock = at(22)
    bot.digest()
    bot.digest()
    assert lookups == ["#ja:example"]
