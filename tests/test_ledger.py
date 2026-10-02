"""The assertion ledger: competing claims, retraction, adjudication, alternatives, evidence versions, replay."""
import sqlite3
import uuid

import pytest

from glyph_atlas.review import ledger
from glyph_atlas.review.ledger import Claim, LedgerError

CROP, V1, V2 = "hk:1", "hk:1@" + "a" * 64 + "@1,2,3,4", "hk:1@" + "a" * 64 + "@1,2,3,5"


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:", isolation_level=None)
    ledger.schema(db)
    return db


class Crop:
    """A crop whose current version a test can recut."""

    def __init__(self):
        self.version = V1

    def __call__(self, unit):
        return self.version if unit == CROP else None


def claim(conn, actor, *values, version=V1, key=None, request=None, at=None, **extra):
    members = [v if isinstance(v, Claim) else Claim(value=v) for v in values]
    key = key or f"{actor}:{uuid.uuid4()}"
    return ledger.write_claims(conn, key=key, actor=actor, request=request or {"values": [repr(m) for m in members], **extra},
                               subject=CROP, predicate="has_form", claims=members, crop_version=version, at=at)


def act(conn, actor, target, action, crop=None, **extra):
    return ledger.act(conn, key=f"{actor}:{uuid.uuid4()}", actor=actor, request={"target": target, "action": action},
                      target=target, action=action, version_of=crop or (lambda unit: V1 if unit == CROP else None), **extra)


def now(conn, version=V1):
    rows = ledger.current(conn, CROP, version)
    return rows[0] if rows else None


def test_a_claim_stands_on_its_own_and_an_acceptance_marks_it_accepted(conn):
    made = claim(conn, "ann", "unresolved")
    row = now(conn)
    assert (row["status"], row["value"], row["object"], row["supporting"]) == ("asserted", "unresolved", None, made["assertions"])
    assert row["members"] == [{"assertion": made["assertions"][0], "object": None, "value": "unresolved",
                               "confidence": None, "scheme": None}]
    assert row["resolver"] == ledger.RESOLVER and row["crop_version"] == V1
    act(conn, "bob", made["assertions"][0], "accept")
    assert now(conn)["status"] == "accepted"


def test_two_people_who_disagree_both_stay_and_the_slot_is_disputed(conn):
    first = claim(conn, "ann", "unresolved")["assertions"][0]
    second = claim(conn, "bob", "unreadable")["assertions"][0]
    row = now(conn)
    assert (row["status"], row["value"], row["members"], row["supporting"]) == ("disputed", None, [], [])
    assert [c["claim"] for c in row["claims"]] == [first, second]
    # A third person agreeing with one does not settle it: only an adjudication does.
    act(conn, "cat", first, "accept")
    assert now(conn)["status"] == "disputed"
    with pytest.raises(LedgerError) as refused:
        act(conn, "cat", second, "adjudicate")
    assert refused.value.status == 403
    act(conn, "dan", second, "adjudicate", adjudicator=True)
    row = now(conn)
    assert (row["status"], row["value"], row["supporting"]) == ("adjudicated", "unreadable", [second])
    assert len(row["claims"]) == 2, "the losing claim stays in the slot's record"
    # A later adjudication decides again; the latest one counts.
    act(conn, "dan", first, "adjudicate", adjudicator=True)
    assert now(conn)["value"] == "unresolved"
    # An adjudicated claim its asserter retracts decides nothing; the latest adjudication of a live
    # claim is the earlier one again.
    act(conn, "ann", first, "retract")
    assert (now(conn)["status"], now(conn)["value"], now(conn)["supporting"]) == ("adjudicated", "unreadable", [second])


def test_agreeing_people_support_one_value(conn):
    a = claim(conn, "ann", "unreadable")["assertions"][0]
    b = claim(conn, "bob", "unreadable")["assertions"][0]
    row = now(conn)
    assert (row["status"], row["value"], row["supporting"]) == ("asserted", "unreadable", [a, b])


def test_a_new_claim_retracts_the_asserters_own_earlier_one(conn):
    first = claim(conn, "ann", "unresolved")["assertions"][0]
    second = claim(conn, "ann", "unreadable")
    assert second["retracted"] == [first]
    row = now(conn)
    assert (row["status"], row["value"], row["supporting"]) == ("asserted", "unreadable", second["assertions"])
    retraction = ledger.history(conn, CROP)[0]["actions"]
    assert [(a["action"], a["actor"], a["reason"]) for a in retraction] == [("retract", "ann", "superseded by " + second["assertions"][0])]
    # Someone else's claim is theirs: ann's new claim does not touch it.
    other = claim(conn, "bob", "unreadable")["assertions"][0]
    claim(conn, "ann", "unreadable")
    assert other in now(conn)["supporting"]


def test_a_retraction_is_the_asserters_and_leaves_the_crop_unsorted(conn):
    made = claim(conn, "ann", "unresolved")["assertions"][0]
    with pytest.raises(LedgerError) as refused:
        act(conn, "bob", made, "retract")
    assert refused.value.status == 403
    with pytest.raises(LedgerError):
        act(conn, "ann", made, "accept")
    act(conn, "ann", made, "retract")
    assert now(conn) is None, "no live claim: the slot has no row"
    with pytest.raises(LedgerError) as again:
        act(conn, "ann", made, "retract")
    assert again.value.status == 409
    assert len(ledger.history(conn, CROP)) == 1, "the claim itself stays in the ledger"


def test_rejections_outnumbering_support_leave_the_candidate_rejected(conn):
    made = claim(conn, "ann", "unresolved")["assertions"][0]
    act(conn, "bob", made, "reject")
    assert now(conn)["status"] == "rejected", "one rejection against the asserter's own word"
    act(conn, "cat", made, "accept")
    assert now(conn)["status"] == "accepted"
    # A person's latest word counts once: bob accepting after rejecting is one acceptance.
    act(conn, "dan", made, "reject")
    act(conn, "eve", made, "reject")
    assert now(conn)["status"] == "rejected"
    act(conn, "eve", made, "accept")
    assert now(conn)["status"] == "accepted"
    row = now(conn)
    assert (row["claims"][0]["accepts"], row["claims"][0]["rejects"]) == (2, 2)


def test_an_alternative_set_is_one_claim_with_each_alternative_and_its_confidence(conn):
    made = claim(conn, "ann", Claim(value="unresolved", confidence=0.7, confidence_scheme="reviewer-weight"),
                 Claim(value="unreadable", confidence=0.3, confidence_scheme="reviewer-weight"))
    row = now(conn)
    assert (row["status"], row["object"], row["value"]) == ("asserted", None, None)
    assert [(m["value"], m["confidence"], m["scheme"]) for m in row["members"]] == [
        ("unreadable", 0.3, "reviewer-weight"), ("unresolved", 0.7, "reviewer-weight")]
    assert sorted(row["supporting"]) == sorted(made["assertions"])
    # Someone accepting one alternative accepts the set; retracting one member retracts the set.
    act(conn, "bob", made["assertions"][1], "accept")
    assert now(conn)["status"] == "accepted"
    retracted = act(conn, "ann", made["assertions"][0], "retract")
    assert len(retracted["actions"]) == 2 and now(conn) is None


def test_a_claim_on_an_earlier_evidence_version_no_longer_stands(conn):
    crop = Crop()
    old = claim(conn, "ann", "unreadable")["assertions"][0]
    crop.version = V2
    ledger.resolve(conn, ledger.slots(conn, [CROP]), crop)
    assert now(conn, V2) is None, "the recut crop is unsorted until someone looks at it again"
    assert ledger.history(conn, CROP)[0]["evidence"] == [{"kind": "crop", "ref": V1, "locator": None}]
    renewed = claim(conn, "bob", "unreadable", version=V2)["assertions"][0]
    row = now(conn, V2)
    assert (row["value"], row["supporting"], row["crop_version"]) == ("unreadable", [renewed], V2)
    # Back on the first cut, the first claim would stand again and the second would not.
    crop.version = V1
    ledger.resolve(conn, ledger.slots(conn, [CROP]), crop)
    assert now(conn, V1)["supporting"] == [old]


def test_a_retry_answers_with_the_first_response_and_a_reused_id_is_refused(conn):
    first = claim(conn, "ann", "unresolved", key="ann:1", request={"value": "unresolved"})
    again = claim(conn, "ann", "unresolved", key="ann:1", request={"value": "unresolved"})
    assert again == first
    assert conn.execute("SELECT count(*) FROM assertions").fetchone()[0] == 1
    with pytest.raises(LedgerError) as refused:
        claim(conn, "ann", "unreadable", key="ann:1", request={"value": "unreadable"})
    assert refused.value.status == 409


def test_a_rebuild_replays_the_ledger_to_the_same_rows(conn):
    crop = Crop()
    a = claim(conn, "ann", "unresolved", at="2026-10-01T00:00:00.000Z")["assertions"][0]
    b = claim(conn, "bob", "unreadable", at="2026-10-01T00:01:00.000Z")["assertions"][0]
    act(conn, "cat", a, "reject", at="2026-10-01T00:02:00.000Z")
    act(conn, "dan", b, "adjudicate", adjudicator=True, at="2026-10-01T00:03:00.000Z")
    before = conn.execute("SELECT * FROM current_claims").fetchall()
    assert before[0][-1] == "2026-10-01T00:03:00.000Z", "a row is dated by the latest row it was resolved from"
    assert ledger.rebuild(conn, crop) == 1
    assert conn.execute("SELECT * FROM current_claims").fetchall() == before


def test_the_catalogue_refuses_what_a_predicate_does_not_take(conn):
    for members, status in [([Claim(value="legible")], 422), ([Claim(object="fm:x")], 422),
                            ([Claim(value="unresolved", confidence=0.5)], 422),
                            ([Claim(value="unresolved"), Claim(value="unresolved")], 422)]:
        with pytest.raises(LedgerError) as refused:
            ledger.write_claims(conn, key=f"ann:{uuid.uuid4()}", actor="ann", request={}, subject=CROP,
                                predicate="has_form", claims=members, crop_version=V1)
        assert refused.value.status == status
    with pytest.raises(LedgerError):
        ledger.write_claims(conn, key="ann:x", actor="ann", request={}, subject=CROP, predicate="reads_as",
                            claims=[Claim(value="x")], crop_version=V1)
    with pytest.raises(LedgerError) as no_image:
        ledger.write_claims(conn, key="ann:y", actor="ann", request={}, subject=CROP, predicate="has_form",
                            claims=[Claim(value="unresolved")], crop_version=None)
    assert no_image.value.status == 409
    assert conn.execute("SELECT count(*) FROM assertions").fetchone()[0] == 0


def test_ledger_rows_are_never_changed_or_removed(conn):
    made = claim(conn, "ann", "unresolved")["assertions"][0]
    act(conn, "bob", made, "accept")
    for sql in ("UPDATE assertions SET value='\"unreadable\"'", "DELETE FROM assertions",
                "UPDATE assertion_actions SET action='reject'", "DELETE FROM assertion_actions",
                "UPDATE assertion_evidence SET ref='x'", "DELETE FROM assertion_evidence"):
        with pytest.raises(sqlite3.IntegrityError, match="ledger_immutable"):
            conn.execute(sql)


def test_the_resolver_names_its_version():
    assert f"'{ledger.RESOLVER}' AS resolver" in ledger.RESOLVE_BODY

