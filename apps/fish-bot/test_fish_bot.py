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

    def canonical_alias(self, room_id):
        return {"!welcome:example": "#welcome:example"}.get(room_id, "")

    def resolve(self, alias):
        return {"#ja:example": "!ja:example", "#welcome:example": "!welcome:example"}[alias]

    def upload(self, data, content_type, filename):
        self.uploads.append((len(data), content_type, filename))
        return f"mxc://example/{filename}"

    def joined_rooms(self):
        return set()

    room_state = ()
    room_directory = ()
    memberships = ()

    def state(self, room_id):
        return list(self.room_state)

    def directory(self):
        return list(self.room_directory)

    def first_memberships(self, room_id, limit=100):
        return list(self.memberships)

    pictures: dict = {}  # noqa: RUF012 - a fake; tests replace it per instance

    def thumbnail(self, mxc, size=1024, limit=4_000_000):
        if mxc not in self.pictures:
            raise fish_bot.MatrixError(404, "not found")
        return self.pictures[mxc]

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
        self.memories = []
        self.memory_value = fish_bot.Memory(summary="They talked about 西夏文.", keywords=("Tangut",), edits=())
        self.memory = None

    def recap(self, language, episodes, notes, request=""):
        self.recaps = getattr(self, "recaps", []) + [(language, episodes, notes, request)]
        return "Welcome! Here is what we talked about."

    def digest(self, language, room_name, room_lines, changes, other_rooms):
        self.digests.append((language, room_name, changes, other_rooms))
        return f"digest in {language}"

    def remember(self, room_name, rows, names, known, *, bot_user_id, left_out=()):
        self.memories.append(([row["event_id"] for row in rows], [int(fact["id"]) for fact in known], set(left_out)))
        return self.memory_value

    def act(self, room_name, room_topic, events, names, *, task, trigger, bot_user_id, stickers=(), recent=(),
            rooms="", memory=None, lookup=None, on_step=None, room_facts=None, images=()):
        self.rooms = rooms
        self.room_facts = room_facts
        self.images = list(images)
        self.memory = memory
        self.lookup = lookup
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
    assert fish_bot.call_of(event("大肥魚餓嗎？"), USER, "大肥魚", (), aliases) is not None
    assert fish_bot.call_of(event("大肥魚喫🍚"), USER, "大肥魚", (), aliases) is not None
    for prose in ("今天吃鯨魚", "今晚吃了个大肥鱼头", "减肥鱼油有用吗", "大肥鱼V4发布了", "清蒸大肥魚片"):
        assert fish_bot.call_of(event(prose), USER, "大肥魚", (), aliases) is None, prose
    assert fish_bot.call_of(event("bigfatfishy"), USER, "Big Fat Fish") is None


def test_answers_a_call_as_a_reply(tmp_path):
    bot, matrix, store = make_bot(tmp_path, act=act("嗨嗨～", "本鯨在這裡！"))
    trigger = event("大肥魚，你好呀！", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.turns[0][0] == "called"
    assert [content["body"] for _, content, _ in matrix.sent] == ["嗨嗨～", "本鯨在這裡！"]
    assert matrix.sent[0][1]["m.relates_to"] == {"m.in_reply_to": {"event_id": "$call"}}
    assert "m.relates_to" not in matrix.sent[1][1]
    assert store.outcome("$call")[0] == "chatted"


def test_reads_the_introduction_command():
    for body in ("!fish 自我介紹", "!fish intro", "大肥魚，自我介紹一下", "大肥魚さん、自己紹介して", "@bigfatfish introduce yourself"):
        assert fish_bot.call_of(event(body), USER, "大肥魚").mode == "intro", body
    assert fish_bot.call_of(event("大肥鱼自我介绍一下"), USER, "大肥魚", (), ("大肥鱼",)).mode == "intro"
    for body in ("大肥魚，介紹一下這個字", "大肥魚 introduction to IDS", "Big Fat Fish, intro to radicals?",
                 "!fish introspection", "自我介紹一下，我是新來的，大肥魚你好",
                 "大肥魚 introduce yourself and file an issue"):
        assert fish_bot.call_of(event(body), USER, "大肥魚", (), ("Big Fat Fish",)).mode == "chat", body


def intro_bot(tmp_path):
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20,
                             intro_rooms=("#welcome:example",))
    return make_bot(tmp_path, config=config, act=act("我是大肥魚！"))


def test_introduces_herself_on_request_in_the_welcome_room(tmp_path):
    bot, matrix, store = intro_bot(tmp_path)
    trigger = event("!fish 自我介紹", event_id="$intro")
    bot.ingest("!welcome:example", trigger, backfilled=False)
    bot.answer("!welcome:example", trigger)
    assert bot.deepseek.turns[0][0] == "intro"
    assert matrix.sent[0][0] == "!welcome:example"
    assert store.outcome("$intro")[0] == "introduced"


def test_points_to_the_welcome_room_from_a_project_room(tmp_path):
    bot, _matrix, _ = intro_bot(tmp_path)
    trigger = event("大肥魚 自我介紹", event_id="$intro")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.turns[0][0] == "intro-elsewhere"
    assert bot.deepseek.rooms == "Test room"


def test_never_introduces_herself_unasked(tmp_path):
    bot, matrix, _ = intro_bot(tmp_path)
    message = event("hello everyone", event_id="$m")
    bot.ingest(ROOM, message, backfilled=False)
    bot.notice(ROOM, message)
    assert bot.deepseek.turns == [] and matrix.sent == []


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
    bot, _matrix, _store = make_bot(tmp_path, decision_value=decision(), github=github, config=config,
                                   act=act("好～", issue=True))
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


def test_introduces_herself_in_the_welcome_room_even_when_lookup_fails(tmp_path):
    bot, matrix, _ = intro_bot(tmp_path)

    def broken(alias):
        raise fish_bot.MatrixError(502, "bad gateway")

    matrix.resolve = broken
    trigger = event("!fish 自我介紹", event_id="$intro")
    bot.ingest("!welcome:example", trigger, backfilled=False)
    bot.answer("!welcome:example", trigger)
    assert bot.deepseek.turns[0][0] == "intro"


HOUR = 3_600_000


def memory_bot(tmp_path):
    bot, matrix, store = make_bot(tmp_path)
    bot.active_rooms = {ROOM, "!other:example"}
    return bot, matrix, store


def say(bot, body, *, sender="@reader:example", event_id, ts, room=ROOM):
    bot.ingest(room, event(body, sender=sender, event_id=event_id, ts=ts), backfilled=True)


def test_memory_terms_pair_spaceless_scripts_and_keep_words():
    assert fish_bot.memory_terms("西夏文 Tangut IDS") == ["西夏", "夏文", "tangut", "ids"]
    assert fish_bot.memory_terms("字 a 한국어") == ["字", "한국", "국어"]


def test_remembers_a_finished_stretch_and_moves_on(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    start = fish_bot.now_ms() - 10 * HOUR
    for number in range(4):
        say(bot, f"西夏文 {number}", event_id=f"$a{number}", ts=start + number * 60_000)
    say(bot, "a new day", event_id="$late", ts=start + 9 * HOUR)
    bot.consolidate()
    assert bot.deepseek.memories[0][0] == ["$a0", "$a1", "$a2", "$a3"]
    episode = store.db.execute("SELECT * FROM episodes").fetchone()
    assert episode["summary"] == "They talked about 西夏文." and json.loads(episode["people"]) == ["@reader:example"]
    bot._memory_due = 0
    bot.consolidate()
    assert len(bot.deepseek.memories) == 1, "the open stretch waits until the room goes quiet"


def test_a_stretch_with_nothing_to_keep_leaves_no_episode(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    bot.deepseek.memory_value = fish_bot.Memory(summary="", keywords=(), edits=())
    say(bot, "hi", event_id="$hi", ts=fish_bot.now_ms() - 30 * HOUR)
    bot.consolidate()
    assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert store.unremembered() == {}


def test_memory_leaves_bridges_and_forgotten_people_out(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    start = fish_bot.now_ms() - 2 * HOUR
    say(bot, "hello", event_id="$a", ts=start)
    say(bot, "relayed", sender="@idsbot:glyphatlas.org", event_id="$b", ts=start + 1000)
    say(bot, "secret", sender="@quiet:example", event_id="$c", ts=start + 2000)
    say(bot, "see you", event_id="$d", ts=start + 3000)
    say(bot, "bye", event_id="$e", ts=start + 4000)
    for number in range(200):
        say(bot, "⿰木木", sender="@idsbot:glyphatlas.org", event_id=f"$ids{number}", ts=start + 1001 + number * 4)
    store.forget_person("@quiet:example")
    store.remember_person("@quiet:example")
    say(bot, "大肥魚 hi", sender=USER, event_id="$echo", ts=start + 2500)
    bot.consolidate()
    assert bot.deepseek.memories[0][0] == ["$a", "$d", "$e"], "nothing from the forgotten time, nor her answer to it"


def test_fact_edits_reach_only_known_facts_and_present_people():
    keys = {"Alice": "@reader:example"}
    raw = json.dumps({"summary": "s", "keywords": ["k"], "facts": [
        {"op": "add", "about": "Alice", "text": "Alice studies Tangut.", "importance": 9},
        {"op": "add", "about": "Mallory", "text": "Nobody."},
        {"op": "add", "about": "self", "text": "大肥魚 promised a sticker."},
        {"op": "update", "id": 3, "text": "Updated."},
        {"op": "update", "id": 99, "text": "Unknown."},
        {"op": "delete", "id": 3},
    ]})
    memory = fish_bot.parse_memory(raw, keys, [3])
    assert [(edit.op, edit.subject, edit.fact, edit.importance) for edit in memory.edits] == [
        ("add", "@reader:example", None, 5), ("add", "self", None, 3), ("update", "", 3, 3)]
    blocked = fish_bot.parse_memory(raw, keys, [3], left_out={"@reader:example"})
    assert all(edit.subject != "@reader:example" for edit in blocked.edits)
    assert fish_bot.parse_memory('{"summary": "", "facts": [{"op": "add", "about": "self", "text": "x"}]}', keys, []).edits == ()


def save(store, room, *, ts, summary, facts=(), people=("@reader:example",), event_ids=None):
    rows = [{"ts": ts, "sender": people[0], "event_id": event_id, "body": ""} for event_id in event_ids or [f"$x{ts}"]]
    edits = tuple(fish_bot.Edit(op="add", subject=subject, text=text, importance=4) for subject, text in facts)
    return store.save_memory(room, rows, people, fish_bot.Memory(summary=summary, keywords=(), edits=edits))


def test_a_turn_recalls_the_speakers_and_a_related_talk_elsewhere(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    old = fish_bot.now_ms() - 48 * HOUR
    save(store, "!other:example", ts=old, summary="Bob showed a 西夏文 dictionary.",
         facts=[("@reader:example", "Alice writes traditional Chinese."), ("@bob:example", "Bob collects rubbings.")],
         people=("@bob:example",))
    save(store, "!other:example", ts=old - HOUR, summary="Talk about soup recipes.")
    trigger = event("大肥魚，西夏文怎麼讀？", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    memory = bot.deepseek.memory
    assert [note["note"] for note in memory["notes"]] == ["Alice writes traditional Chinese."]
    assert memory["earlier"][-1]["summary"] == "Bob showed a 西夏文 dictionary."


def test_forget_me_deletes_their_notes_and_talks_and_says_so(tmp_path):
    bot, matrix, store = memory_bot(tmp_path)
    save(store, ROOM, ts=fish_bot.now_ms() - 48 * HOUR, summary="Alice and Bob talked.",
         facts=[("@reader:example", "Alice likes rice."), ("community", "The atlas has a review page.")])
    assert fish_bot.call_of(event("!fish forget me"), USER, "大肥魚").mode == "forget"
    assert fish_bot.call_of(event("大肥魚，忘記我吧"), USER, "大肥魚").mode == "forget"
    assert fish_bot.call_of(event("大肥魚 forget me not is a flower"), USER, "大肥魚").mode == "chat"
    trigger = event("!fish forget me", event_id="$forget")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.turns[-1][0] == "forgot" and matrix.sent
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert store.forgotten() == {"@reader:example"}
    back = event("!fish remember me", event_id="$back")
    bot.ingest(ROOM, back, backfilled=False)
    bot.answer(ROOM, back)
    assert bot.deepseek.turns[-1][0] == "remember-me" and store.forgotten() == set()


def test_a_redacted_message_takes_its_episode_and_facts_along(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    start = fish_bot.now_ms() - 2 * HOUR
    for number in range(3):
        say(bot, f"line {number}", event_id=f"$r{number}", ts=start + number * 1000)
    bot.deepseek.memory_value = fish_bot.Memory(summary="s", keywords=(), edits=(
        fish_bot.Edit(op="add", subject="@reader:example", text="A fact."),))
    bot.consolidate()
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    bot.redact({"type": "m.room.redaction", "redacts": "$r1"})
    assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM memory_terms").fetchone()[0] == 0


def test_memory_outlives_the_log_but_not_its_own_horizon(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    now = fish_bot.now_ms()
    save(store, ROOM, ts=now - 400 * 24 * HOUR, summary="Long ago.")
    save(store, ROOM, ts=now - 200 * 24 * HOUR, summary="Last spring.")
    store.prune(now - 180 * 24 * HOUR, now - 365 * 24 * HOUR)
    assert [row["summary"] for row in store.db.execute("SELECT summary FROM episodes")] == ["Last spring."]


def test_memory_prompt_formats_people_under_keys_and_lists_known_facts(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    save(store, ROOM, ts=fish_bot.now_ms(), summary="s", facts=[("@bob:example", "Bob collects rubbings.")])
    known = store.known_facts(["@bob:example"], [])
    rows = [message_row("hi", sender="@reader:example"), message_row("yo", sender=USER),
            message_row("hey", sender="@alice2:example")]
    names = {"@reader:example": "Alice", "@alice2:example": "alice"}
    user, keys = fish_bot.format_memory("Room", rows, names, known, bot_user_id=USER)
    payload = json.loads(user)
    assert payload["people"] == ["Alice", "alice (2)", "bob"]
    assert [message["from"] for message in payload["messages"]] == ["Alice", "大肥魚", "alice (2)"]
    assert payload["known"][0]["about"] == "bob"
    assert keys == {"Alice": "@reader:example", "alice (2)": "@alice2:example", "bob": "@bob:example"}


def test_config_reads_the_memory_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[memory]\nenabled = false\ndays = 90\n", encoding="utf-8")
    config = fish_bot.Config.load(path)
    assert (config.memory_enabled, config.memory_days) == (False, 90)


def test_a_stretch_that_keeps_failing_backs_off_and_is_skipped(tmp_path, monkeypatch):
    bot, _, store = memory_bot(tmp_path)
    old = fish_bot.now_ms() - 30 * HOUR
    for number in range(3):
        say(bot, f"bad {number}", event_id=f"$p{number}", ts=old + number)
        say(bot, f"good {number}", event_id=f"$g{number}", ts=old + 3600_000 + number, room="!other:example")
    calls = []

    def remember(room_name, rows, names, known, *, bot_user_id, left_out=()):
        calls.append(rows[0]["event_id"])
        if rows[0]["event_id"] == "$p0":
            raise fish_bot.DeepSeekError("400 Content Exists Risk")
        return fish_bot.Memory(summary="fine", keywords=(), edits=())

    monkeypatch.setattr(bot.deepseek, "remember", remember)
    bot.consolidate()
    bot._memory_due = 0
    bot.consolidate()
    assert calls == ["$p0", "$g0"], "the other room goes on while the failing one waits"
    for _ in range(fish_bot.MEMORY_TRIES - 1):
        bot._memory_failures[ROOM] = (*bot._memory_failures[ROOM][:2], 0.0)
        bot._memory_due = 0
        bot.consolidate()
    assert calls.count("$p0") == fish_bot.MEMORY_TRIES and ROOM not in store.unremembered()


def test_a_note_goes_with_every_talk_it_came_from(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    now = fish_bot.now_ms()
    first = save(store, ROOM, ts=now - 2 * HOUR, summary="Bob said he reads Tangut.", people=("@bob:example",),
                 facts=[("community", "Someone here reads Tangut.")], event_ids=["$bob"])
    fact = store.db.execute("SELECT id FROM facts").fetchone()["id"]
    rows = [{"ts": now - HOUR, "sender": "@carol:example", "event_id": "$carol", "body": ""}]
    store.save_memory(ROOM, rows, ["@carol:example"], fish_bot.Memory(summary="Carol too.", keywords=(), edits=(
        fish_bot.Edit(op="update", fact=fact, text="Bob and Carol read Tangut."),)))
    assert first
    store.forget_person("@bob:example")
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert [row["summary"] for row in store.db.execute("SELECT summary FROM episodes")] == ["Carol too."]


def test_a_redaction_after_the_log_is_pruned_still_reaches_memory(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    old = fish_bot.now_ms() - 200 * 24 * HOUR
    save(store, ROOM, ts=old, summary="Long ago.", facts=[("@reader:example", "Alice wrote it.")], event_ids=["$old"])
    store.prune(fish_bot.now_ms() - 180 * 24 * HOUR, fish_bot.now_ms() - 365 * 24 * HOUR)
    bot.redact({"type": "m.room.redaction", "redacts": "$old"})
    assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_a_redaction_outside_an_episode_leaves_it(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    start = fish_bot.now_ms() - 2 * HOUR
    say(bot, "one", event_id="$1", ts=start)
    say(bot, "relay", sender="@telegrambot:glyphatlas.org", event_id="$relay", ts=start + 1)
    say(bot, "two", event_id="$2", ts=start + 2)
    say(bot, "three", event_id="$3", ts=start + 3)
    bot.consolidate()
    bot.redact({"type": "m.room.redaction", "redacts": "$relay"})
    assert store.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


def test_forget_me_takes_effect_over_the_chat_budget(tmp_path):
    config = fish_bot.Config(user_id=USER, room_cooldown_seconds=0, context_messages=20, max_chats_per_hour=1)
    bot, matrix, store = make_bot(tmp_path, config=config)
    save(store, ROOM, ts=fish_bot.now_ms() - HOUR, summary="Alice talked.", facts=[("@reader:example", "Alice likes rice.")])
    bot._chats = [fish_bot.time.monotonic()]
    trigger = event("!fish forget me", event_id="$forget")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert matrix.sent == [] and store.forgotten() == {"@reader:example"}
    assert store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_a_room_no_longer_read_is_not_recalled(tmp_path):
    bot, _, store = memory_bot(tmp_path)
    save(store, "!gone:example", ts=fish_bot.now_ms() - HOUR, summary="西夏文 in a closed room.")
    rows = [message_row("西夏文")]
    assert bot.recall(ROOM, rows, {}) == {}


def test_a_question_about_being_remembered_is_a_chat():
    assert fish_bot.call_of(event("大肥魚 remember me?"), USER, "大肥魚").mode == "chat"
    assert fish_bot.call_of(event("大肥魚，記住我"), USER, "大肥魚").mode == "remember"


def test_memory_waits_for_the_chat_to_go_still(tmp_path):
    bot, _, _ = memory_bot(tmp_path)
    start = fish_bot.now_ms() - 30 * HOUR
    for number in range(3):
        say(bot, f"line {number}", event_id=f"$s{number}", ts=start + number)
    bot._heard_at = fish_bot.time.monotonic()
    bot.consolidate()
    assert bot.deepseek.memories == []
    bot._heard_at = 0.0
    bot.consolidate()
    assert len(bot.deepseek.memories) == 1
class FakeWeb:
    def __init__(self):
        self.queries = []

    def search(self, query, count=5):
        self.queries.append(query)
        return [{"title": "Tangut", "url": "https://example.org/tangut", "date": "2026-11-01", "summary": "A script."}]

    def fetch(self, url):
        return {"title": "Page", "content": "x" * 20_000}


class SearchingGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.searches = []

    def search(self, kind, query, scope):
        self.searches.append((kind, query, scope))
        return [{"repo": "mkpoli/han-compose", "path": "js/src/tree.js"}]


def answer(content=None, calls=None):
    message = {"role": "assistant", "content": content if content is not None else " "}
    if calls:
        message["tool_calls"] = [{"id": f"call_{number}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for number, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message}]}


def scripted(monkeypatch, answers):
    model = fish_bot.DeepSeek("key")
    bodies = []

    def post(body, url=None):
        bodies.append(json.loads(json.dumps(body)))
        return answers.pop(0)

    monkeypatch.setattr(model, "_post", post)
    return model, bodies


def test_a_called_turn_looks_something_up_before_it_answers(tmp_path, monkeypatch):
    web = FakeWeb()
    lookup = fish_bot.Lookup(web, None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli")
    model, bodies = scripted(monkeypatch, [answer(calls=[("web_search", {"query": "Tangut script"})]),
                                           answer('{"say": "It is a script."}')])
    steps = []
    act = model.act("Room", "", [message_row("what is Tangut?")], {}, task="called", trigger=0,
                    bot_user_id=USER, lookup=lookup, on_step=lambda: steps.append(1))
    assert act.say == ("It is a script.",) and steps == [1] and web.queries == ["Tangut script"]
    assert [tool["function"]["name"] for tool in bodies[0]["tools"]] == ["web_search", "read_page", "characters", "recall"]
    assert [message["role"] for message in bodies[1]["messages"][-2:]] == ["tool", "system"]
    assert json.loads(bodies[1]["messages"][-2]["content"])["results"][0]["url"] == "https://example.org/tangut"
    assert bodies[1]["response_format"] == {"type": "json_object"}


def test_lookups_stop_after_a_few_rounds(tmp_path, monkeypatch):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli")
    loops = [answer(calls=[("web_search", {"query": "again"})]) for _ in range(fish_bot.LOOKUP_ROUNDS)]
    model, bodies = scripted(monkeypatch, [*loops, answer('{"say": "ok"}')])
    act = model.act("Room", "", [message_row("hi")], {}, task="called", trigger=0, bot_user_id=USER, lookup=lookup)
    assert act.say == ("ok",)
    assert bodies[-1]["tool_choice"] == "none" and "tool_choice" not in bodies[-2]


def test_a_lookup_refuses_bad_arguments_and_keeps_to_its_budget(tmp_path):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli", per_hour=2)
    assert "error" in lookup.run("read_page", '{"url": "file:///etc/passwd"}')
    assert "error" in lookup.run("web_search", "not json")
    assert "error" in lookup.run("github_read", '{"repo": "mkpoli/glyph-atlas"}')
    assert "budget" in lookup.run("web_search", '{"query": "x"}')["error"]
    page = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "other.sqlite3"), owner="mkpoli").run(
        "read_page", '{"url": "https://example.org"}')
    assert page["cut"] and len(page["text"]) < fish_bot.MAX_TOOL_RESULT


def test_github_search_covers_the_owner_unless_a_repo_is_named(tmp_path):
    github = SearchingGitHub()
    lookup = fish_bot.Lookup(FakeWeb(), github, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli")
    assert [tool["function"]["name"] for tool in lookup.specs()][-3:] == ["github_search", "github_read", "github_item"]
    lookup.run("github_search", {"kind": "code", "query": "descriptionEnd"})
    lookup.run("github_search", {"kind": "issues", "query": "sticker", "repo": "mkpoli/glyph-atlas"})
    assert github.searches == [("code", "descriptionEnd", "user:mkpoli"), ("issues", "sticker", "repo:mkpoli/glyph-atlas")]


def test_github_reads_a_file_and_lists_a_directory(monkeypatch):
    github = fish_bot.GitHub("token", "mkpoli/glyph-atlas")
    paths = []
    replies = {
        "/repos/mkpoli/han-compose/contents/js/src/tree.js?ref=main": {
            "type": "file", "encoding": "base64", "content": "ZXhwb3J0IGZ1bmN0aW9u\n", "size": 15, "html_url": "https://github.com/x"},
        "/repos/mkpoli/han-compose/contents/": [{"name": "js", "type": "dir", "size": 0}],
    }

    def call(method, path, body=None, accept="", timeout=None):
        paths.append(path)
        return replies[path]

    monkeypatch.setattr(github, "_call", call)
    assert github.read("mkpoli/han-compose", "/js/src/tree.js", "main")["text"] == "export function"
    assert github.read("mkpoli/han-compose")["entries"] == [{"name": "js", "type": "dir", "size": 0}]


def test_only_an_answer_to_a_call_may_look_things_up(tmp_path, monkeypatch):
    bot, _, _ = make_bot(tmp_path)
    trigger = event("大肥魚，IDS工房是什麼？", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.lookup is bot.lookup is not None
    monkeypatch.setattr(bot, "_random", lambda: 0.0)
    bot.turn(ROOM, "$call", task="ambient")
    assert bot.deepseek.lookup is None


def test_the_recall_tool_searches_every_room(tmp_path):
    store = fish_bot.Store(tmp_path / "chat.sqlite3")
    store.set_room("!other:example", "開發 · Dev", "")
    save(store, "!other:example", ts=fish_bot.now_ms() - 24 * HOUR, summary="IDS工房上線了。",
         facts=[("community", "IDS工房 is an open-source editor.")])
    found = fish_bot.Lookup(FakeWeb(), None, store, owner="mkpoli",
                            rooms=lambda: {"!other:example"}).run("recall", {"query": "IDS工房"})
    assert found["earlier"][0]["room"] == "開發 · Dev"
    assert found["notes"][0]["about"] == "the community"
    closed = fish_bot.Lookup(FakeWeb(), None, store, owner="mkpoli").run("recall", {"query": "IDS工房"})
    assert closed["earlier"] == []


def test_config_reads_the_lookup_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[lookup]\nenabled = false\nper_hour = 30\n", encoding="utf-8")
    config = fish_bot.Config.load(path)
    assert (config.lookup_enabled, config.lookup_per_hour) == (False, 30)


def test_characters_are_described_from_the_database_with_derived_names():
    described = fish_bot.describe_characters("𗼇字\U0003FFFD")
    assert described[0] == {"char": "𗼇", "code_point": "U+17F07", "name": "TANGUT IDEOGRAPH-17F07"}
    assert described[1]["name"] == "CJK UNIFIED IDEOGRAPH-5B57"
    assert described[2]["name"].startswith("unassigned")


def test_the_characters_tool_costs_no_budget(tmp_path):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli", per_hour=0)
    assert lookup.run("characters", {"text": "字"})["characters"][0]["code_point"] == "U+5B57"
    assert "budget" in lookup.run("web_search", {"query": "x"})["error"]


def test_no_recall_tool_while_memory_is_off(tmp_path):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli", memory=False)
    assert "recall" not in [tool["function"]["name"] for tool in lookup.specs()]
    assert "error" in lookup.run("recall", {"query": "x"})


def test_github_reads_stay_inside_the_named_repository(tmp_path):
    github = SearchingGitHub()
    lookup = fish_bot.Lookup(FakeWeb(), github, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli")
    for repo in ("../..", "mkpoli/..", "./x"):
        assert "error" in lookup.run("github_item", {"repo": repo, "number": 1}), repo
    with pytest.raises(ValueError):
        fish_bot.GitHub("token", "mkpoli/glyph-atlas").read("mkpoli/glyph-atlas", "../../../../user/repos")


def test_characters_keep_whitespace():
    lookup_text = fish_bot.describe_characters("　 a ")
    assert [item["code_point"] for item in lookup_text] == ["U+3000", "U+0020", "U+0061", "U+00A0"]


def test_github_item_shows_the_newest_comments(monkeypatch):
    github = fish_bot.GitHub("token", "mkpoli/glyph-atlas")
    pages = {page: [{"user": {"login": "x"}, "body": f"c{n}"} for n in range(page * 5 - 4, min(page * 5, 102) + 1)]
             for page in range(1, 22)}

    def call(method, path, body=None, accept="", timeout=None):
        if path.endswith("/issues/7"):
            return {"title": "t", "comments": 102}
        if "/comments" in path:
            return pages[int(path.rsplit("page=", 1)[1])]
        raise AssertionError(path)

    monkeypatch.setattr(github, "_call", call)
    assert [comment["body"] for comment in github.item("mkpoli/glyph-atlas", 7)["comments"]] == [
        "c98", "c99", "c100", "c101", "c102"]


def test_a_turn_stops_looking_things_up_when_time_runs_out(tmp_path, monkeypatch):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli")
    model, bodies = scripted(monkeypatch, [answer(calls=[("web_search", {"query": "slow"})]), answer('{"say": "ok"}')])
    clock = iter([0.0, 1000.0, 1000.0, 1000.0])
    monkeypatch.setattr(fish_bot.time, "monotonic", lambda: next(clock))
    act = model.act("Room", "", [message_row("hi")], {}, task="called", trigger=0, bot_user_id=USER, lookup=lookup)
    assert act.say == ("ok",) and lookup.deepseek.queries == []
    assert bodies[-1]["tool_choice"] == "none"


def member(user, name, *, ts=1_792_852_938_000, membership="join"):
    return {"type": "m.room.member", "state_key": user, "origin_server_ts": ts,
            "content": {"membership": membership, "displayname": name}}


ROOM_STATE = [
    {"type": "m.room.create", "sender": "@ops:example", "state_key": "", "origin_server_ts": 1_792_852_938_380, "content": {}},
    {"type": "m.room.canonical_alias", "state_key": "", "content": {"alias": "#zh:example"}},
    {"type": "m.room.topic", "state_key": "", "content": {"topic": "中文"}},
    {"type": "m.bridge", "state_key": "discord", "content": {
        "protocol": {"displayname": "Discord"},
        "channel": {"displayname": "#中文", "external_url": "https://discord.com/channels/1/2"}}},
    member("@mkpoli:example", "mkpoli"), member("@qq_ab:example", "胡妹"), member("@discord_1:example", "まくぽり"),
    member("@qqbot:example", "QQ"), member("@bigfatfish:example", "大肥魚"), member("@left:example", "x", membership="leave"),
]


def community(matrix=None):
    matrix = matrix or FakeMatrix()
    matrix.room_state = ROOM_STATE
    matrix.room_directory = [{"room_id": ROOM, "name": "中文", "canonical_alias": "#zh:example", "num_joined_members": 6},
                             {"room_id": "!dev:example", "name": "開發 · Dev", "canonical_alias": "#dev:example"},
                             {"room_id": "!devops:example", "name": "開發 · Ops", "canonical_alias": "#ops:example"}]
    matrix.memberships = [member("@ops:example", "ops", ts=1), member("@telegrambot:glyphatlas.org", "bot", ts=2),
                          member("@mkpoli:example", "mkpoli", ts=3, membership="invite"),
                          member("@mkpoli:example", "mkpoli", ts=4), member("@mkpoli:example", "mkpoli", ts=5)]
    return fish_bot.Community(matrix, {"@qqbot:example", "@bigfatfish:example", "@telegrambot:glyphatlas.org"},
                              rooms=lambda: {ROOM, "!dev:example", "!devops:example"})


def test_a_room_summary_counts_people_by_platform_and_names_its_bridges():
    facts = community().summary(ROOM)
    assert facts["members"] == 3 and facts["by_platform"] == {"Matrix": 1, "QQ": 1, "Discord": 1}
    assert facts["alias"] == "#zh:example" and facts["created"] == fish_bot.local_day(1_792_852_938_380)
    assert facts["bridged_to"] == ["Discord #中文 (https://discord.com/channels/1/2)", "QQ group"]


def test_room_info_lists_who_joined_first_without_bots_or_repeats():
    info = community().info(ROOM)
    assert info["created_by"] == "@ops:example" and info["room"] == "中文"
    assert [person["name"] for person in info["first_to_join"]] == ["ops", "mkpoli"]
    assert "QQ group members who never spoke are missing" in info["coverage"]


def test_rooms_are_found_by_alias_name_part_or_here():
    rooms = community()
    assert rooms.find("", ROOM) == ROOM and rooms.find("this group", ROOM) == ROOM
    assert rooms.find("#dev:example", ROOM) == "!dev:example"
    assert rooms.find("dev", ROOM) == "!dev:example"
    with pytest.raises(ValueError):
        rooms.find("開發", ROOM)
    with pytest.raises(ValueError):
        rooms.find("!private:example", ROOM)


def test_members_are_filtered_by_name():
    found = community().members(ROOM, "胡")
    assert found["members"] == [{"name": "胡妹", "platform": "QQ"}] and found["total"] == 1


def test_room_tools_are_local_and_free(tmp_path):
    lookup = fish_bot.Lookup(FakeWeb(), None, fish_bot.Store(tmp_path / "chat.sqlite3"), owner="mkpoli",
                             per_hour=0, community=community())
    lookup.here = ROOM
    assert "rooms" in [tool["function"]["name"] for tool in lookup.specs()]
    assert lookup.run("room_info", {})["first_to_join"][0]["name"] == "ops"
    listed = lookup.run("rooms", {})["rooms"]
    assert len(listed) == 3 and listed[0]["members"] == 3, "counted as room_info counts, without bots"
    assert lookup.run("members", {"room": "中文", "query": "mk"})["total"] == 1


def test_every_turn_knows_its_room(tmp_path):
    matrix = FakeMatrix()
    community(matrix)
    bot, _, _ = make_bot(tmp_path, matrix=matrix)
    trigger = event("大肥魚 這個羣多少人？", event_id="$call")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    assert bot.deepseek.room_facts["alias"] == "#zh:example"
    assert bot.lookup.here == ROOM


def test_the_chat_prompt_carries_the_room_facts():
    payload = json.loads(fish_bot.format_chat("中文", "", [message_row("hi")], {}, task="called", trigger=0,
                                              bot_user_id=USER, room_facts={"members": 3}))
    assert payload["room"] == {"name": "中文", "topic": "", "members": 3}


def test_a_long_member_list_fits_and_says_how_many_are_left_out():
    matrix = FakeMatrix()
    rooms = community(matrix)
    matrix.room_state = [*ROOM_STATE, *(member(f"@discord_{n}:example", f"使用者{n:03d}長長的名字") for n in range(400)),
                         {"type": "m.room.member", "state_key": ["odd"], "content": {"membership": "join"}}]
    found = rooms.members(ROOM)
    assert len(json.dumps(found, ensure_ascii=False)) <= fish_bot.MAX_TOOL_RESULT
    assert found["total"] == 403 and "more" in found["left_out"]
    assert rooms.summary(ROOM)["members"] == 403


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def picture(event_id, *, sender="@reader:example", url="mxc://example/abc", sticker=False, ts=None):
    if sticker:
        return {"type": "m.sticker", "sender": sender, "event_id": event_id, "room_id": ROOM,
                "origin_server_ts": ts or fish_bot.now_ms(), "content": {"body": "cute", "url": url}}
    return event("photo.png", sender=sender, event_id=event_id, ts=ts, msgtype="m.image", url=url)


def test_keeps_the_link_to_a_picture_or_sticker(tmp_path):
    bot, _, store = make_bot(tmp_path)
    bot.ingest(ROOM, picture("$p"), backfilled=False)
    bot.ingest(ROOM, picture("$s", url="mxc://example/st", sticker=True), backfilled=False)
    rows = store.context(ROOM, fish_bot.now_ms() + 1000, 10)
    assert [(row["body"], row["media"]) for row in rows] == [
        ("[image] photo.png", "mxc://example/abc"), ("[sticker] cute", "mxc://example/st")]


def test_an_old_log_gains_the_media_column(tmp_path):
    path = tmp_path / "chat.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, sender TEXT NOT NULL, "
                       "ts INTEGER NOT NULL, body TEXT NOT NULL, msgtype TEXT NOT NULL DEFAULT '', "
                       "backfilled INTEGER NOT NULL DEFAULT 0)")
    connection.execute("INSERT INTO events VALUES('$old', ?, '@a:example', 1, 'hi', 'm.text', 0)", (ROOM,))
    connection.commit()
    connection.close()
    store = fish_bot.Store(path)
    assert store.context(ROOM, 10, 5)[0]["media"] == ""


def test_a_turn_sees_the_newest_pictures_people_posted(tmp_path):
    matrix = FakeMatrix()
    matrix.pictures = {"mxc://example/1": PNG, "mxc://example/2": b"GIF89a....", "mxc://example/own": PNG,
                       "mxc://example/odd": b"not an image"}
    bot, _, _ = make_bot(tmp_path, matrix=matrix)
    start = fish_bot.now_ms() - 60_000
    bot.ingest(ROOM, picture("$1", url="mxc://example/1", ts=start), backfilled=False)
    bot.ingest(ROOM, picture("$own", sender=USER, url="mxc://example/own", ts=start + 1), backfilled=False)
    bot.ingest(ROOM, picture("$odd", url="mxc://example/odd", ts=start + 2), backfilled=False)
    bot.ingest(ROOM, picture("$gone", url="mxc://example/gone", ts=start + 3), backfilled=False)
    bot.ingest(ROOM, picture("$2", url="mxc://example/2", sticker=True, ts=start + 4), backfilled=False)
    trigger = event("大肥魚，這是你嗎？", event_id="$call", ts=start + 5)
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    images = bot.deepseek.images
    assert [index for index, _ in images] == [0, 4]
    assert images[0][1].startswith("data:image/png;base64,") and images[1][1].startswith("data:image/gif;base64,")


def test_the_model_gets_pictures_after_the_json(monkeypatch):
    model, bodies = scripted(monkeypatch, [answer('{"say": "是我！"}')])
    rows = [message_row("[image] me.png")]
    model.act("Room", "", rows, {}, task="called", trigger=0, bot_user_id=USER, images=[(0, "data:image/png;base64,AA")])
    content = bodies[0]["messages"][1]["content"]
    assert json.loads(content[0]["text"])["messages"][0]["image"] == "attached below"
    assert content[1] == {"type": "text", "text": "The picture in message 0:"}
    assert content[2] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}


def test_a_posted_picture_counts_as_something_to_look_at(tmp_path, monkeypatch):
    bot, _, _ = make_bot(tmp_path)
    bot.notice(ROOM, picture("$p"))
    assert bot._ambient[ROOM]["lively"] and bot._ambient[ROOM]["count"] == 1


def test_a_refused_picture_costs_the_picture_not_the_answer(monkeypatch):
    model = fish_bot.DeepSeek("key")
    bodies = []

    def post(body, url=None, **_):
        bodies.append(json.loads(json.dumps(body)))
        if isinstance(body["messages"][1]["content"], list):
            raise fish_bot.DeepSeekError("DeepSeek answered 400: invalid image")
        return answer('{"say": "看不到圖，但我在！"}')

    monkeypatch.setattr(model, "_post", post)
    act = model.act("Room", "", [message_row("[image] x.png")], {}, task="called", trigger=0, bot_user_id=USER,
                    images=[(0, "data:image/png;base64,AA")])
    assert act.say == ("看不到圖，但我在！",) and isinstance(bodies[1]["messages"][1]["content"], str)


def test_a_picture_that_fails_is_fetched_once(tmp_path):
    matrix = FakeMatrix()
    calls = []
    matrix.thumbnail = lambda mxc, size=1024, limit=0: calls.append(mxc) or (_ for _ in ()).throw(fish_bot.MatrixError(404, "gone"))
    bot, _, store = make_bot(tmp_path, matrix=matrix)
    bot.ingest(ROOM, picture("$gone", url="mxc://example/gone"), backfilled=False)
    rows = store.context(ROOM, fish_bot.now_ms() + 1000, 10)
    assert bot.pictures(rows, 4) == [] and bot.pictures(rows, 4) == []
    assert calls == ["mxc://example/gone"]


def test_her_own_sticker_is_not_a_picture_she_cannot_see(tmp_path):
    bot, _, store = make_bot(tmp_path)
    bot.ingest(ROOM, picture("$mine", sender=USER, url="mxc://example/mine", sticker=True), backfilled=False)
    rows = store.context(ROOM, fish_bot.now_ms() + 1000, 10)
    payload = json.loads(fish_bot.format_chat("Room", "", rows, {}, task="ambient", trigger=None, bot_user_id=USER))
    assert "image" not in payload["messages"][0]


def test_only_a_real_new_picture_counts_for_reading_along():
    edited = picture("$e")
    edited["content"]["m.relates_to"] = {"rel_type": "m.replace", "event_id": "$x"}
    empty = {"type": "m.sticker", "sender": "@reader:example", "event_id": "$s", "content": {}}
    linkless = event("photo.png", event_id="$l", msgtype="m.image")
    assert [fish_bot._picture(item, USER) for item in (edited, empty, linkless, picture("$ok"))] == [None, None, None, "!"]


def test_a_picture_link_cannot_climb_out_of_the_media_api():
    with pytest.raises(fish_bot.MatrixError):
        fish_bot.Matrix("http://127.0.0.1:1", "token").thumbnail("mxc://../config")


def reply(body, target, *, event_id="$reply", sender="@discord_1:example"):
    return event(body, event_id=event_id, sender=sender, **{"m.relates_to": {"m.in_reply_to": {"event_id": target}}})


def test_a_reply_to_her_message_is_a_call(tmp_path):
    bot, matrix, store = make_bot(tmp_path, act=act("不客氣～"))
    bot.ingest(ROOM, event("오늘의 소식", sender=USER, event_id="$digest"), backfilled=False)
    thanks = reply("고마워요 딥식이!", "$digest")
    bot.ingest(ROOM, thanks, backfilled=False)
    bot.answer(ROOM, thanks)
    assert bot.deepseek.turns[0][0] == "called" and matrix.sent[0][1]["body"] == "不客氣～"
    assert store.outcome("$reply")[0] == "chatted"


def test_a_reply_to_someone_else_is_not_a_call(tmp_path):
    bot, matrix, _ = make_bot(tmp_path)
    bot.ingest(ROOM, event("hello", sender="@other:example", event_id="$other"), backfilled=False)
    answer_to_other = reply("thanks!", "$other")
    bot.answer(ROOM, answer_to_other)
    assert bot.deepseek.turns == [] and matrix.sent == []


def test_a_reply_to_her_message_outside_the_log_is_checked_with_the_homeserver(tmp_path):
    matrix = FakeMatrix()
    asked = []
    matrix.event = lambda room_id, event_id: asked.append(event_id) or {"sender": USER}
    bot, _, _ = make_bot(tmp_path, matrix=matrix)
    late = reply("還記得這條嗎", "$old")
    bot.ingest(ROOM, late, backfilled=False)
    bot.answer(ROOM, late)
    bot.notice(ROOM, late)
    bot.answer(ROOM, reply("還有這條", "$old", event_id="$again"))
    assert asked == ["$old"] and bot.deepseek.turns[0][0] == "called"
    bot.answer(ROOM, reply("relay", "$other-old", sender="@discordbot:glyphatlas.org"))
    assert asked == ["$old"], "no lookup for a bot's reply"


def test_the_first_message_of_a_thread_on_her_post_is_not_a_reply_to_her(tmp_path):
    bot, matrix, _ = make_bot(tmp_path)
    bot.ingest(ROOM, event("今日のまとめ", sender=USER, event_id="$digest"), backfilled=False)
    threaded = event("@alice what do you think?", event_id="$t", **{"m.relates_to": {
        "rel_type": "m.thread", "event_id": "$digest", "is_falling_back": True,
        "m.in_reply_to": {"event_id": "$digest"}}})
    bot.answer(ROOM, threaded)
    assert bot.deepseek.turns == [] and matrix.sent == []




def test_reads_the_catch_up_command():
    for body in ("!fish recap", "大肥魚，總結一下", "大肥魚 前情提要", "大肥鱼，之前都聊了些什么？", "大肥魚さん、まとめて",
                 "@bigfatfish catch me up please"):
        assert fish_bot.call_of(event(body), USER, "大肥魚", (), ("大肥鱼",)).mode == "recap", body
    for body in ("大肥魚，總結一下這個 PR", "大肥魚 recap of the meeting tomorrow?"):
        assert fish_bot.call_of(event(body), USER, "大肥魚").mode == "chat", body


def test_a_catch_up_covers_every_room_still_read(tmp_path):
    bot, matrix, store = memory_bot(tmp_path)
    store.set_room("!other:example", "開發 · Dev", "")
    save(store, "!other:example", ts=fish_bot.now_ms() - 48 * HOUR, summary="IDS工房上線了。",
         facts=[("community", "IDS工房 is an open-source editor.")])
    save(store, ROOM, ts=fish_bot.now_ms() - 24 * HOUR, summary="Talked about 西夏文.")
    save(store, "!gone:example", ts=fish_bot.now_ms() - HOUR, summary="A closed room.")
    trigger = event("!fish recap", event_id="$recap")
    bot.ingest(ROOM, trigger, backfilled=False)
    bot.answer(ROOM, trigger)
    _, episodes, notes, request = bot.deepseek.recaps[0]
    assert [episode["summary"] for episode in episodes] == ["IDS工房上線了。", "Talked about 西夏文."]
    assert episodes[0]["room"] == "開發 · Dev" and notes == ["IDS工房 is an open-source editor."]
    assert request == "!fish recap"
    assert matrix.sent[-1][1]["m.relates_to"] == {"m.in_reply_to": {"event_id": "$recap"}}
    assert store.outcome("$recap")[0] == "recapped"


def test_the_catch_up_prompt_carries_the_episodes_and_request():
    payload = json.loads(fish_bot.format_recap([{"room": "中文", "date": "2026-11-01", "summary": "s"}], ["n"], "!fish recap"))
    assert payload == {"request": "!fish recap", "episodes": [{"room": "中文", "date": "2026-11-01", "summary": "s"}],
                       "notes": ["n"]}
