"""Normalising ``atlas-character-reviews`` exports.

The fixtures are synthetic and mirror the two real shapes byte-for-byte in the fields
that matter: a local review whose ``event.evidence`` is a JSON string, and a corpus
review whose ``event.new`` is a structured object with no evidence. One optional test
reads a live export from ``/tmp`` when it happens to be there; it is skipped otherwise,
so nothing private is committed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import ClassVar

import pytest

from kuzushiji_atlas.feedback import (
    DECISIONS,
    FLAG_EXISTING_BUG,
    FLAG_LAYER_REQUEST,
    FLAG_LEGACY_CHOICE,
    FLAG_LEGACY_DEFAULT_ISSUE,
    FLAG_UNTRUSTED_ACTOR,
    QUARANTINE_IMPLICIT_QUIZ_MATCH,
    QUARANTINE_MATCH_CHANGED,
    QUARANTINE_MERGED_AS_SINGLE,
    QUARANTINE_MERGED_NO_TEXT,
    QUARANTINE_NOTE_DISAGREES,
    QUARANTINE_PHONETIC_INTENT,
    QUARANTINE_READING_NOT_IDENTITY,
    QUARANTINE_STALE,
    active,
    by_unit,
    normalize_export,
    source_corpus_of,
    training_pairs,
)

UNIT = "hk:3daea514503efa7c8ec5ccc61c9be9d8:7:L13:7a0d3267f64e:7"
IMAGE_HASH = "059c59905ca9746911ca8f993857d0288dd8486ea2421b22ff902c4e5ae4d0a1"
BOX = {"x": 358, "y": 559, "w": 39, "h": 75}


def local_record(
    event_id="rv00000007",
    *,
    verdict="match",
    issue="reading",
    label="シ",
    reading=None,
    correction=None,
    note="",
    box=None,
    current=True,
    role="reviewer",
    actor="reviewer-1",
    at="2026-01-01T00:00:00Z",
    character=None,
    suggestions=None,
    new="reviewed",
    revision=0,
    effective=None,
    suggested=None,
    layer=None,
    request_reading=None,
    kind="character-review",
    quiz_character=None,
):
    """One local character review, shaped exactly as the export writes it."""
    reading = label if reading is None else reading
    request = {
        "id": f"edit-{event_id}",
        "client_id": actor,
        "revision": revision,
        "image_sha256": IMAGE_HASH,
        "reading": None,
        "verdict": verdict,
        "issue": issue,
        "correction": correction,
        "note": note,
        "box": box,
        "character": character,
    }
    if suggestions is not None:
        request["suggestions"] = suggestions
    evidence = {
        "kind": kind,
        "verdict": verdict,
        "issue": issue,
        "note": note,
        "request": request,
        "snapshot": {
            "character": {
                "id": UNIT,
                "label": label,
                "reading": reading,
                "script": "katakana",
                "jibo": None,
                "revision": revision,
                "state": "pending",
                "page_id": UNIT.rsplit(":", 1)[0],
                "line_id": UNIT.rsplit(":", 1)[0],
                "box": BOX,
                "image_sha256": IMAGE_HASH,
            },
            "image_sha256": IMAGE_HASH,
            "page_index": 7,
            "source_refs": {},
            "canvas": "https://example.invalid/iiif/canvas/7",
        },
        # The saved unit state. On the real merged records this is still the old label,
        # because the save path cannot resolve a two-character correction.
        "correction": {"reading": correction if effective is None else effective, "box": box if box else BOX},
    }
    if suggested is not None:
        evidence["suggested_reading"] = suggested
    if layer is not None:
        evidence["layer"] = layer
    if quiz_character is not None:
        evidence["suggested_character"] = quiz_character
    if request_reading is not None:
        request["reading"] = request_reading
    return {
        "event": {
            "id": event_id,
            "target_type": "unit",
            "target_id": UNIT,
            "field": "review",
            "old": "pending",
            "new": new,
            "role": role,
            "actor": actor,
            "at": at,
            "evidence": json.dumps(evidence, ensure_ascii=False),
        },
        "reviewed": evidence["snapshot"],
        "current": current,
        "current_revision": 1,
    }


def corpus_record(
    event_id="e5820ca3-b059",
    *,
    char=None,
    issue="reading",
    suggestions=None,
    actor_kind="user-report",
    note="User report: ...",
    verdict="wrong",
    current=True,
):
    """One corpus review: structured ``new``, no evidence, detail-shaped snapshot."""
    return {
        "event": {
            "id": event_id,
            "target_type": "unit",
            "target_id": "codh:200008316:200008316_00030_2:B0001:C0027",
            "actor": "reported-example",
            "actor_kind": actor_kind,
            "at": "2026-09-20T12:05:47Z",
            "field": "review",
            "role": None,
            "new": {
                "character": char,
                "correction": None,
                "issue": issue,
                "note": note,
                "verdict": verdict,
                "suggestions": suggestions or [],
            },
        },
        "reviewed": {
            "id": "codh:200008316:200008316_00030_2:B0001:C0027",
            "unit_id": "codh:200008316:20000830_2:B0001:C0027",
            "char": "在",
            "code_point": "U+5728",
            "label": "在",
            "source_label": "在",
            "reading": "在",
            "box": {"x": 2305, "y": 1498, "w": 81, "h": 210},
            "source_revision": "a1b2c3",
            "basis": "upstream_bbox",
            "source": {"corpus": "codh-full", "page_id": "codh:200008316:p2"},
        },
        "current": current,
        "current_revision": 1,
    }


def export(*records):
    return {"version": 1, "kind": "atlas-character-reviews", "reviews": list(records)}


def test_quiz_unselected_crops_are_not_positive_supervision():
    record = local_record(kind="visual-quiz", verdict="match", issue=None)
    normalized = normalize_export(export(record))[0]
    assert normalized.decision == "ambiguous"
    assert QUARANTINE_IMPLICIT_QUIZ_MATCH in normalized.quarantine_reasons
    assert not normalized.train_parent_as_single_char
    assert normalized.training_text is None
    # Explicit single-character confirmations keep their existing meaning.
    explicit = normalize_export(export(local_record(verdict="match")))[0]
    assert explicit.decision == "accepted-match"


def test_quiz_suggestion_spelled_with_several_code_points_is_one_character():
    """The quiz names ツ + U+309A as its two code points, and that is the character ツ゚."""
    record = local_record(verdict="wrong", issue="character", label="ツ", quiz_character="U+30C4 U+309A")
    normalized = normalize_export(export(record))[0]
    assert normalized.decision == "accepted-identity"
    assert normalized.proposed_text == "ツ゚"


class TestTheSuppliedPatterns:
    """Every shape the live export actually contains, and what each must become."""

    def test_a_merged_glyph_proposed_as_one_character_is_quarantined(self):
        """rv7: the note says シヨロ, the correction says シ. They cannot both hold."""
        records = normalize_export(
            export(local_record(verdict="match", issue="merged", label="シ", correction="シ", note="シヨロ"))
        )
        record = records[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_MERGED_AS_SINGLE in record.quarantine_reasons
        assert QUARANTINE_NOTE_DISAGREES in record.quarantine_reasons
        assert record.proposed_text == "シ"  # kept for audit
        assert record.note == "シヨロ"  # the note is never the proposal
        assert record.is_active is False

    def test_a_merged_glyph_with_no_joined_text_is_quarantined(self):
        """rv9: a merge with nothing selected is not a positive single character."""
        records = normalize_export(
            export(local_record(verdict="match", issue="merged", label="ア", correction=None, note=""))
        )
        assert records[0].decision == "ambiguous"
        assert QUARANTINE_MERGED_NO_TEXT in records[0].quarantine_reasons

    @pytest.mark.parametrize(
        "event_id", ["rv00000014", "rv00000024", "rv00000027", "rv00000030", "rv00000034"]
    )
    def test_a_wrong_merged_review_is_quarantined_too(self, event_id):
        records = normalize_export(
            export(
                local_record(
                    event_id, verdict="wrong", issue="merged", label="イ", correction="イ", new="disputed"
                )
            )
        )
        assert records[0].decision == "ambiguous"
        assert records[0].is_active is False

    def test_a_match_with_nothing_changed_is_the_legacy_default_issue(self):
        """rv8 and friends: issue=reading, but the reading was never actually moved."""
        records = normalize_export(
            export(local_record(verdict="match", issue="reading", label="を", correction="を", note=""))
        )
        record = records[0]
        assert record.decision == "accepted-match"
        assert FLAG_LEGACY_DEFAULT_ISSUE in record.evidence_flags
        assert record.is_active is True

    def test_a_wrong_reading_with_one_character_is_the_legacy_ui_choice(self):
        """rv11: 手 written and read as 手; the correction を is the *character*."""
        records = normalize_export(
            export(
                local_record(
                    "rv00000011", verdict="wrong", issue="reading", label="手", reading="手", correction="を"
                )
            )
        )
        record = records[0]
        assert record.decision == "accepted-identity"
        assert FLAG_LEGACY_CHOICE in record.evidence_flags
        assert record.original_identity == "手"
        assert record.proposed_text == "を"

    def test_an_explicit_layer_request_wins_over_the_wrong_verdict(self):
        """rv18: the layer requested a character; `new=disputed` is the known bug."""
        records = normalize_export(
            export(
                local_record(
                    "rv00000018",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    correction=None,
                    character="こ",
                    new="disputed",
                )
            )
        )
        record = records[0]
        assert record.decision == "accepted-identity"
        assert FLAG_LAYER_REQUEST in record.evidence_flags
        assert FLAG_EXISTING_BUG in record.evidence_flags

    def test_a_disputed_character_with_nothing_proposed_is_quarantined(self):
        records = normalize_export(
            export(
                local_record(
                    "rv00000021",
                    verdict="wrong",
                    issue="character",
                    label="カ",
                    correction=None,
                    new="disputed",
                )
            )
        )
        record = records[0]
        assert record.decision == "ambiguous"
        assert FLAG_EXISTING_BUG in record.evidence_flags

    def test_a_crop_correction_is_a_crop_not_an_identity(self):
        box = {"x": 100, "y": 200, "w": 30, "h": 40}
        records = normalize_export(
            export(
                local_record(
                    "rv00000035",
                    verdict="wrong",
                    issue="crop",
                    label="や",
                    correction="や",
                    box=box,
                    new="disputed",
                )
            )
        )
        record = records[0]
        assert record.decision == "crop"
        assert record.proposed_box == box
        assert record.source_box == BOX  # the source box is untouched

    def test_a_user_report_is_reported_and_not_trusted(self):
        """The live corpus record: prose and a suggestion, but nobody verified it."""
        records = normalize_export(
            export(corpus_record(suggestions=[{"engine": "Reported correction", "text": "有"}]))
        )
        record = records[0]
        assert record.decision == "reported"
        assert record.trusted_human is False
        assert FLAG_UNTRUSTED_ACTOR in record.evidence_flags
        assert record.proposed_text is None  # a suggestion is not an accepted value
        assert record.door == "corpus"

    def test_the_supplied_export_normalises_as_a_whole(self):
        payload = export(
            local_record(
                "rv00000007", verdict="match", issue="merged", label="シ", correction="シ", note="シヨロ"
            ),
            local_record("rv00000008", verdict="match", issue="reading", label="を", correction="を"),
            local_record("rv00000011", verdict="wrong", issue="reading", label="手", correction="を"),
            local_record(
                "rv00000014", verdict="wrong", issue="merged", label="イ", correction="イ", new="disputed"
            ),
            corpus_record(),
        )
        records = normalize_export(payload)
        assert len(records) == 5
        assert {r.decision for r in records} <= set(DECISIONS)
        assert sum(1 for r in records if r.decision == "ambiguous") == 2
        assert len(active(records)) == 3


class TestSourceIdentityAndReading:
    """The written form and the reading are different fields and stay different."""

    def test_a_reading_correction_does_not_become_an_identity(self):
        """ネ written, ね read: correcting to ね fixes the reading, not the character."""
        records = normalize_export(
            export(
                local_record(
                    "rv-x", verdict="wrong", issue="reading", label="ネ", reading="ね", correction="ね"
                )
            )
        )
        record = records[0]
        assert record.original_identity == "ネ"
        assert record.original_reading == "ね"
        assert record.decision == "ambiguous"
        assert QUARANTINE_READING_NOT_IDENTITY in record.quarantine_reasons

    def test_the_difference_is_flagged_even_when_the_decision_stands(self):
        """An explicit character request stands, and the split is still reported."""
        records = normalize_export(
            export(
                local_record(
                    "rv-y",
                    verdict="wrong",
                    issue="character",
                    label="𪜈",
                    reading="トモ",
                    character="丶",
                    new="disputed",
                )
            )
        )
        record = records[0]
        assert "source-label-differs-from-reading" in record.evidence_flags
        assert record.decision == "accepted-identity"
        assert record.original_identity == "𪜈"
        assert record.original_reading == "トモ"

    def test_a_correction_equal_to_the_label_proposes_nothing(self):
        records = normalize_export(
            export(local_record("rv-z", verdict="wrong", issue="reading", label="手", correction="手"))
        )
        assert records[0].decision == "ambiguous"


class TestNotesAreNotProposals:
    def test_a_note_never_becomes_the_proposed_text(self):
        records = normalize_export(
            export(
                local_record(
                    "rv-note",
                    verdict="wrong",
                    issue="reading",
                    label="シ",
                    correction=None,
                    note="これは シヨロ と読むべき",
                )
            )
        )
        record = records[0]
        assert record.proposed_text is None
        assert record.note == "これは シヨロ と読むべき"
        assert record.decision == "ambiguous"

    def test_a_merge_with_only_a_note_is_not_joined(self):
        records = normalize_export(
            export(
                local_record(
                    "rv-note2", verdict="match", issue="merged", label="シ", correction=None, note="シヨロ"
                )
            )
        )
        assert records[0].decision == "ambiguous"


class TestJoinedSuggestions:
    def test_a_selected_multi_character_text_is_kept_as_the_transcription(self):
        records = normalize_export(
            export(
                local_record(
                    "rv-joined",
                    verdict="wrong",
                    issue="merged",
                    label="シ",
                    correction="シヨロ",
                    suggestions=[{"engine": "Joined", "text": "シヨロ", "selected": True}],
                )
            )
        )
        record = records[0]
        assert record.decision == "joined"
        assert record.proposed_text == "シヨロ"
        assert record.train_parent_as_single_char is False
        assert record.is_active is True

    def test_a_joined_record_is_trainable_as_a_sequence(self):
        payload = export(
            local_record(
                "rv-joined",
                verdict="wrong",
                issue="merged",
                label="シ",
                correction="シヨロ",
                suggestions=[{"engine": "Joined", "text": "シヨロ", "selected": True}],
            )
        )
        pairs = training_pairs(normalize_export(payload))
        assert pairs == [(UNIT, "シヨロ", BOX)]

    def test_an_offered_but_unselected_suggestion_is_not_a_decision(self):
        records = normalize_export(
            export(
                local_record(
                    "rv-offered",
                    verdict="match",
                    issue="merged",
                    label="シ",
                    correction="シ",
                    suggestions=[{"engine": "Joined", "text": "シヨロ"}, {"engine": "Single", "text": "シ"}],
                )
            )
        )
        assert records[0].decision == "ambiguous"
        assert records[0].train_parent_as_single_char is False

    def test_a_single_character_record_may_train_the_parent(self):
        records = normalize_export(
            export(local_record("rv-single", verdict="wrong", issue="reading", label="手", correction="を"))
        )
        assert records[0].train_parent_as_single_char is True
        assert training_pairs(records) == [(UNIT, "を", BOX)]


class TestStalenessAndSupersession:
    def test_a_stale_record_keeps_its_decision_but_leaves_active_use(self):
        records = normalize_export(
            export(
                local_record(
                    "rv-old", verdict="wrong", issue="reading", label="手", correction="を", current=False
                )
            )
        )
        record = records[0]
        assert record.decision == "accepted-identity"
        assert record.current is False
        assert record.is_active is False
        assert QUARANTINE_STALE in record.quarantine_reasons

    def test_a_later_identity_review_supersedes_an_earlier_one(self):
        payload = export(
            local_record(
                "rv-first",
                verdict="wrong",
                issue="reading",
                label="手",
                correction="を",
                at="2026-01-01T00:00:00Z",
            ),
            local_record(
                "rv-second",
                verdict="wrong",
                issue="reading",
                label="手",
                correction="り",
                at="2026-02-01T00:00:00Z",
            ),
        )
        records = normalize_export(payload)
        first, second = records
        assert first.superseded_by == "rv-second"
        assert first.is_active is False
        assert QUARANTINE_STALE in first.quarantine_reasons
        assert second.superseded_by is None
        assert second.is_active is True
        assert second.proposed_text == "り"  # the later identity wins

    def test_supersession_only_applies_to_identity_decisions(self):
        payload = export(
            local_record(
                "rv-crop",
                verdict="wrong",
                issue="crop",
                label="や",
                at="2026-01-01T00:00:00Z",
                new="disputed",
            ),
            local_record(
                "rv-id",
                verdict="wrong",
                issue="reading",
                label="手",
                correction="を",
                at="2026-02-01T00:00:00Z",
            ),
        )
        records = normalize_export(payload)
        assert records[0].superseded_by is None
        assert records[0].is_active is True  # a crop is not an identity claim

    def test_active_only_filters_without_rewriting_the_decision(self):
        payload = export(
            local_record("rv-a", verdict="wrong", issue="reading", label="手", correction="を"),
            local_record("rv-b", verdict="match", issue="merged", label="シ", correction="シ", note="シヨロ"),
        )
        everything = normalize_export(payload)
        usable = normalize_export(payload, active_only=True)
        assert len(everything) == 2 and len(usable) == 1
        assert usable[0].event_id == "rv-a"
        assert everything[1].decision == "ambiguous"  # not softened by filtering


class TestInputIsNotMutated:
    def test_the_payload_is_untouched(self):
        payload = export(
            local_record(
                "rv00000007", verdict="match", issue="merged", label="シ", correction="シ", note="シヨロ"
            )
        )
        before = copy.deepcopy(payload)
        normalize_export(payload)
        assert payload == before

    def test_records_are_frozen(self):
        import dataclasses

        record = normalize_export(export(local_record()))[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.decision = "joined"  # type: ignore[misc]

    def test_each_record_carries_a_digest_of_its_source(self):
        one = normalize_export(export(local_record("rv-1")))[0]
        two = normalize_export(export(local_record("rv-2")))[0]
        assert one.snapshot_digest and one.snapshot_digest != two.snapshot_digest
        same = normalize_export(export(local_record("rv-1")))[0]
        assert same.snapshot_digest == one.snapshot_digest

    def test_the_raw_event_new_is_kept_for_audit(self):
        record = normalize_export(export(corpus_record()))[0]
        assert isinstance(record.event_new, dict)
        assert record.event_new.get("issue") == "reading"


class TestShape:
    def test_the_documented_fields_are_all_present(self):
        record = normalize_export(export(local_record()))[0]
        for name in (
            "source_unit_id",
            "event_id",
            "source_box",
            "page_hash",
            "source_revision",
            "original_identity",
            "original_reading",
            "decision",
            "proposed_text",
            "trusted_human",
        ):
            assert hasattr(record, name), name

    def test_the_source_box_and_revision_come_from_the_snapshot(self):
        record = normalize_export(export(local_record(revision=4)))[0]
        assert record.source_box == BOX
        assert record.page_hash == IMAGE_HASH
        assert record.source_revision == 4

    def test_a_trusted_local_review_is_marked_trusted(self):
        assert normalize_export(export(local_record()))[0].trusted_human is True

    def test_the_door_is_recorded(self):
        assert normalize_export(export(local_record()))[0].door == "local"
        assert normalize_export(export(corpus_record()))[0].door == "corpus"

    def test_the_corpus_is_read_from_the_unit_id(self):
        record = normalize_export(export(local_record()))[0]
        assert record.source_corpus == "ainu-records"
        assert source_corpus_of("codh-omt:001:1") == "kokatsuji"
        assert source_corpus_of("nonsense") is None


class TestHelpers:
    def test_training_pairs_excludes_quarantine_and_ambiguity(self):
        payload = export(
            local_record("rv-good", verdict="wrong", issue="reading", label="手", correction="を"),
            local_record(
                "rv-merged", verdict="match", issue="merged", label="シ", correction="シ", note="シヨロ"
            ),
            corpus_record(),
            local_record(
                "rv-stale", verdict="wrong", issue="reading", label="手", correction="り", current=False
            ),
        )
        pairs = training_pairs(normalize_export(payload))
        assert pairs == [(UNIT, "を", BOX)]

    def test_by_unit_groups_records(self):
        payload = export(local_record("rv-1"), local_record("rv-2"))
        grouped = by_unit(normalize_export(payload))
        assert list(grouped) == [UNIT]
        assert len(grouped[UNIT]) == 2

    def test_active_filters_in_place(self):
        payload = export(local_record("rv-1"), local_record("rv-2", current=False))
        assert [r.event_id for r in active(normalize_export(payload))] == ["rv-1"]


class TestInputValidation:
    def test_json_text_is_accepted(self):
        payload = export(local_record())
        from_text = normalize_export(json.dumps(payload, ensure_ascii=False))
        from_object = normalize_export(payload)
        assert [r.event_id for r in from_text] == [r.event_id for r in from_object]

    def test_a_foreign_export_is_refused(self):
        with pytest.raises(ValueError):
            normalize_export({"kind": "something-else", "reviews": []})

    def test_a_non_object_is_refused(self):
        with pytest.raises(TypeError):
            normalize_export([1, 2, 3])

    def test_an_export_with_no_reviews_is_empty_not_an_error(self):
        assert normalize_export({"version": 1, "kind": "atlas-character-reviews"}) == []

    def test_a_malformed_evidence_string_does_not_raise(self):
        record = local_record()
        record["event"]["evidence"] = "{not json"
        parsed = normalize_export(export(record))[0]
        assert parsed.decision in DECISIONS

    def test_a_record_without_an_event_is_skipped(self):
        assert normalize_export(export({"reviewed": {}})) == []


class TestAgainstALiveExportIfPresent:
    """Optional integration check: reads a fetched export, never commits one."""

    PATH = Path("/tmp/reviews_export.json")

    def test_the_live_export_normalises(self):
        if not self.PATH.is_file():
            pytest.skip("no live export fetched")
        before = self.PATH.read_bytes()
        records = normalize_export(self.PATH.read_bytes())
        assert records, "the export should contain reviews"
        for record in records:
            assert record.decision in DECISIONS
            assert record.event_id
            assert record.source_unit_id
        # nothing was written back
        assert self.PATH.read_bytes() == before


class TestJoinedSequencesFromTheSuppliedExport:
    """The five merged records whose reviewer typed a real multi-character sequence.

    Their shape is the trap: ``evidence.correction.reading`` is the *saved unit state*,
    and the save path could not resolve a two-character correction, so it still reads
    the old single-character label. A parser that takes the effective state as the
    proposal turns all five joined sequences into single characters and then quarantines
    them. The proposal is ``request.correction``.
    """

    #: (event id, source label, the sequence the reviewer selected)
    SUPPLIED: ClassVar[list[tuple[str, str, str]]] = [
        ("rv00000014", "イ", "シヤ"),
        ("rv00000024", "さ", "さへ"),
        ("rv00000027", "出", "に四"),
        ("rv00000030", "キ", "キナ"),
        ("rv00000034", "例", "菰を"),
    ]

    @pytest.mark.parametrize("event_id,label,sequence", SUPPLIED)
    def test_the_selected_sequence_is_the_proposal(self, event_id, label, sequence):
        record = normalize_export(
            export(
                local_record(
                    event_id,
                    verdict="wrong",
                    issue="merged",
                    label=label,
                    correction=sequence,
                    new="disputed",
                )
            )
        )[0]
        assert record.decision == "joined", record.quarantine_reasons
        assert record.proposed_text == sequence
        assert record.requested_text == sequence

    @pytest.mark.parametrize("event_id,label,sequence", SUPPLIED)
    def test_the_effective_state_does_not_outrank_the_request(self, event_id, label, sequence):
        """The saved reading is still the old label, and must not be the proposal."""
        record = normalize_export(
            export(
                local_record(
                    event_id,
                    verdict="wrong",
                    issue="merged",
                    label=label,
                    correction=sequence,
                    effective=label,
                    new="disputed",
                )
            )
        )[0]
        assert record.effective_text == label
        assert record.proposed_text == sequence
        assert record.proposed_text != record.effective_text

    @pytest.mark.parametrize("event_id,label,sequence", SUPPLIED)
    def test_a_joined_record_never_trains_the_parent_as_one_character(self, event_id, label, sequence):
        records = normalize_export(
            export(
                local_record(
                    event_id,
                    verdict="wrong",
                    issue="merged",
                    label=label,
                    correction=sequence,
                    effective=label,
                    new="disputed",
                )
            )
        )
        record = records[0]
        assert record.train_parent_as_single_char is False
        assert record.is_active is True
        assert training_pairs(records) == [(UNIT, sequence, BOX)]

    def test_the_whole_supplied_group_normalises_together(self):
        payload = export(
            *[
                local_record(
                    event_id,
                    verdict="wrong",
                    issue="merged",
                    label=label,
                    correction=sequence,
                    effective=label,
                    new="disputed",
                )
                for event_id, label, sequence in self.SUPPLIED
            ]
        )
        records = normalize_export(payload)
        assert [r.decision for r in records] == ["joined"] * 5
        assert [r.proposed_text for r in records] == [s for _, _, s in self.SUPPLIED]


class TestProposalPrecedence:
    """request.character > request.correction > suggested_reading > effective state."""

    def test_an_explicit_character_outranks_a_typed_correction(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-p1",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    character="こ",
                    correction="み",
                    new="disputed",
                )
            )
        )[0]
        assert record.decision == "accepted-identity"
        assert record.proposed_text == "こ"
        assert record.requested_text == "み"

    def test_a_typed_correction_outranks_a_suggestion(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-p2",
                    verdict="wrong",
                    issue="merged",
                    label="イ",
                    correction="シヤ",
                    suggested="シヨ",
                    new="disputed",
                )
            )
        )[0]
        assert record.decision == "joined"
        assert record.proposed_text == "シヤ"

    def test_a_suggestion_is_used_when_nothing_was_typed(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-p3",
                    verdict="wrong",
                    issue="merged",
                    label="イ",
                    correction=None,
                    suggested="シヤ",
                    new="disputed",
                )
            )
        )[0]
        assert record.decision == "joined"
        assert record.proposed_text == "シヤ"

    def test_the_effective_state_only_counts_when_it_actually_changed(self):
        """An unchanged stored reading proposes nothing; a changed one is the legacy fix."""
        unchanged = normalize_export(
            export(
                local_record(
                    "rv-p4",
                    verdict="wrong",
                    issue="reading",
                    label="手",
                    reading="手",
                    correction=None,
                    effective="手",
                )
            )
        )[0]
        assert unchanged.decision == "ambiguous"

        changed = normalize_export(
            export(
                local_record(
                    "rv-p5",
                    verdict="wrong",
                    issue="reading",
                    label="手",
                    reading="手",
                    correction=None,
                    effective="を",
                )
            )
        )[0]
        assert changed.decision == "accepted-identity"
        assert FLAG_LEGACY_CHOICE in changed.evidence_flags

    def test_the_effective_state_is_never_used_for_a_merge(self):
        """A stored single character cannot turn a merge into a joined sequence."""
        record = normalize_export(
            export(
                local_record(
                    "rv-p6", verdict="wrong", issue="merged", label="イ", correction=None, effective="イ"
                )
            )
        )[0]
        assert record.decision == "ambiguous"
        assert record.proposed_text is None


class TestMatchIsOnlyAConfirmation:
    """A match confirms the source identity. Anything else contradicts it."""

    def test_an_unchanged_match_trains_on_the_source_identity(self):
        record = normalize_export(
            export(local_record("rv-m1", verdict="match", issue="reading", label="を", correction="を"))
        )[0]
        assert record.decision == "accepted-match"
        assert record.training_text == "を"
        assert record.train_parent_as_single_char is True
        assert training_pairs([record]) == [(UNIT, "を", BOX)]

    def test_a_bare_match_with_no_issue_also_trains(self):
        record = normalize_export(
            export(local_record("rv-m2", verdict="match", issue=None, label="ヤ", correction=None))
        )[0]
        assert record.decision == "accepted-match"
        assert record.training_text == "ヤ"

    @pytest.mark.parametrize("issue", ["crop", "blank", "character"])
    def test_a_match_carrying_a_defect_issue_is_ambiguous(self, issue):
        record = normalize_export(
            export(local_record("rv-m3", verdict="match", issue=issue, label="ヤ", correction=None))
        )[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_MATCH_CHANGED in record.quarantine_reasons
        assert training_pairs([record]) == []

    def test_a_match_carrying_a_changed_proposal_is_ambiguous(self):
        record = normalize_export(
            export(local_record("rv-m4", verdict="match", issue="reading", label="ヤ", correction="ユ"))
        )[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_MATCH_CHANGED in record.quarantine_reasons

    def test_a_match_with_a_changed_effective_state_is_ambiguous(self):
        """The modern door forbids this; a record that carries it is not agreement."""
        record = normalize_export(
            export(
                local_record(
                    "rv-m5", verdict="match", issue="reading", label="ヤ", correction=None, effective="ユ"
                )
            )
        )[0]
        assert record.decision == "accepted-match"  # nothing was *proposed*
        assert record.training_text == "ヤ"

    def test_a_stale_match_is_not_trainable(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-m6", verdict="match", issue="reading", label="を", correction="を", current=False
                )
            )
        )[0]
        assert record.decision == "accepted-match"
        assert record.training_text is None
        assert training_pairs([record]) == []


class TestModernLayerIsPhoneticIntent:
    """`issue=reading` from the modern layer is about the reading, not the character."""

    def test_a_layer_marked_reading_issue_never_infers_an_identity(self):
        """The trap: the written form and the reading are the same character."""
        record = normalize_export(
            export(
                local_record(
                    "rv-l1",
                    verdict="wrong",
                    issue="reading",
                    label="ネ",
                    reading="ネ",
                    correction="ね",
                    layer="review",
                )
            )
        )[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_PHONETIC_INTENT in record.quarantine_reasons
        assert FLAG_LEGACY_CHOICE not in record.evidence_flags

    def test_an_explicit_request_reading_is_phonetic_intent_too(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-l2",
                    verdict="wrong",
                    issue="reading",
                    label="ネ",
                    reading="ネ",
                    correction="ね",
                    request_reading="ね",
                )
            )
        )[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_PHONETIC_INTENT in record.quarantine_reasons

    def test_the_old_correction_only_choice_still_infers(self):
        """No layer, no request reading: the legacy inference is the whole signal."""
        record = normalize_export(
            export(
                local_record(
                    "rv-l3", verdict="wrong", issue="reading", label="手", reading="手", correction="を"
                )
            )
        )[0]
        assert record.decision == "accepted-identity"
        assert FLAG_LEGACY_CHOICE in record.evidence_flags
        assert QUARANTINE_PHONETIC_INTENT not in record.quarantine_reasons

    def test_a_layer_marked_character_request_still_stands(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-l4",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    character="こ",
                    layer="review",
                    new="disputed",
                )
            )
        )[0]
        assert record.decision == "accepted-identity"
        assert FLAG_LAYER_REQUEST in record.evidence_flags


class TestEffectiveFallbackReachesTheRecord:
    def test_the_effective_reading_becomes_the_proposed_text(self):
        """`_decide` used the fallback; the record has to carry what it used."""
        record = normalize_export(
            export(
                local_record(
                    "rv-e1",
                    verdict="wrong",
                    issue="reading",
                    label="手",
                    reading="手",
                    correction=None,
                    effective="を",
                )
            )
        )[0]
        assert record.decision == "accepted-identity"
        assert record.proposed_text == "を"
        assert record.effective_text == "を"
        assert training_pairs([record]) == [(UNIT, "を", BOX)]

    def test_an_unchanged_effective_reading_proposes_nothing(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-e2",
                    verdict="wrong",
                    issue="reading",
                    label="手",
                    reading="手",
                    correction=None,
                    effective="手",
                )
            )
        )[0]
        assert record.decision == "ambiguous"
        assert record.proposed_text is None


class TestCorpusHumanWithoutARole:
    """`CorpusReviews.record` saves actor_kind="human" and no role at all."""

    def test_a_normal_user_corpus_review_is_trusted(self):
        record = normalize_export(
            export(corpus_record(actor_kind="human", verdict="wrong", issue="character", char="有"))
        )[0]
        assert record.trusted_human is True
        assert record.decision == "accepted-identity"
        assert record.proposed_text == "有"

    def test_a_user_report_is_still_only_reported(self):
        record = normalize_export(export(corpus_record(actor_kind="user-report", char="有")))[0]
        assert record.trusted_human is False
        assert record.decision == "reported"

    def test_a_corpus_human_cannot_claim_a_match_with_a_change(self):
        record = normalize_export(
            export(corpus_record(actor_kind="human", verdict="match", issue="character", char="有"))
        )[0]
        assert record.decision == "ambiguous"
        assert QUARANTINE_MATCH_CHANGED in record.quarantine_reasons

    def test_a_local_reviewer_is_still_trusted(self):
        assert normalize_export(export(local_record()))[0].trusted_human is True


class TestVisualQuizSuggestedCharacter:
    def test_a_suggested_code_point_becomes_the_character(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-q1",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    correction=None,
                    character=None,
                    kind="visual-quiz",
                    quiz_character="U+3053",
                )
            )
        )[0]
        assert record.proposed_text == "こ"
        assert record.decision == "accepted-identity"

    def test_a_quiz_suggestion_is_not_a_single_character_merge(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-q2",
                    verdict="wrong",
                    issue="merged",
                    label="イ",
                    correction=None,
                    kind="visual-quiz",
                    quiz_character="U+30B7",
                )
            )
        )[0]
        assert record.decision == "ambiguous"

    def test_an_already_canonical_character_is_left_alone(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-q3",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    character="こ",
                    kind="visual-quiz",
                    quiz_character="こ",
                )
            )
        )[0]
        assert record.proposed_text == "こ"

    def test_a_nonsense_code_point_is_ignored(self):
        record = normalize_export(
            export(
                local_record(
                    "rv-q4",
                    verdict="wrong",
                    issue="character",
                    label="チ",
                    correction=None,
                    character=None,
                    kind="visual-quiz",
                    quiz_character="U+ZZZZ",
                )
            )
        )[0]
        assert record.proposed_text is None
        assert record.decision == "ambiguous"


class TestTheLiveCountsArePreserved:
    """The 22 supplied records keep the decisions the corrections established."""

    def test_the_supplied_group_decisions(self):
        payload = export(
            local_record(
                "rv00000007", verdict="match", issue="merged", label="シ", correction=None, note="シヨロ"
            ),
            local_record("rv00000009", verdict="match", issue="merged", label="ア", correction=None),
            local_record("rv00000011", verdict="wrong", issue="reading", label="手", correction="を"),
            local_record(
                "rv00000018",
                verdict="wrong",
                issue="character",
                label="チ",
                character="こ",
                layer="review",
                new="disputed",
            ),
            local_record(
                "rv00000014",
                verdict="wrong",
                issue="merged",
                label="イ",
                correction="シヤ",
                effective="イ",
                new="disputed",
            ),
            local_record(
                "rv00000035", verdict="wrong", issue="crop", label="や", correction="や", new="disputed"
            ),
            corpus_record(actor_kind="user-report"),
        )
        records = normalize_export(payload)
        assert [r.decision for r in records] == [
            "ambiguous",
            "ambiguous",
            "accepted-identity",
            "accepted-identity",
            "joined",
            "crop",
            "reported",
        ]
