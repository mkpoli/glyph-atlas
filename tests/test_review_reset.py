"""The offline reset: history goes, corrections stay.

Every fixture is a synthetic dataset in ``tmp_path`` — a few units and lines, a real
review store built by the real store, and a synthetic corpus review database. Nothing
here touches a live dataset, a server or the user's own export.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from glyph_atlas import refs, tables
from glyph_atlas.review import reset as reset_module
from glyph_atlas.review.reset import (
    CORPUS_BASELINE_TABLE,
    REVISION_BUMP,
    REVISION_FLOOR_KEY,
    ResetError,
    UnsafeReset,
    in_progress,
    read_marker,
    reset_reviews,
)
from glyph_atlas.review.store import ReviewRequest, Store, apply
from glyph_atlas.schema import Box, Document, Line, Page, PageText, ReviewState, Unit


def build_dataset(root: Path) -> Path:
    """A small dataset with one document, one page and three units."""
    root.mkdir(parents=True, exist_ok=True)
    document = Document(
        id="d:1",
        title="Test book",
        holder="H",
        image_rights={"licence": "CC-BY-4.0", "holder": "H", "attribution": "H"},
    )
    page = Page(
        id="d:1:1", document_id="d:1", seq=1, image="https://example.invalid/1.jpg", width=1000, height=1000
    )
    line = Line(
        id="l:1",
        page_id="d:1:1",
        seq=1,
        box=Box(x=0, y=0, w=100, h=900),
        text_raw="あい",
        text="あい",
        meta={},
    )
    units = [
        Unit(
            id="u:1",
            document_id="d:1",
            page_id="d:1:1",
            line_id="l:1",
            seq=1,
            box=Box(x=1, y=2, w=30, h=40),
            text_source="あ",
            unicode="U+3042",
            kind="char",
            method="import",
            active=True,
        ),
        Unit(
            id="u:2",
            document_id="d:1",
            page_id="d:1:1",
            line_id="l:1",
            seq=2,
            box=Box(x=40, y=2, w=30, h=40),
            text_source="い",
            unicode="U+3044",
            kind="char",
            method="import",
            active=True,
        ),
        # A row the pipeline itself refused to trust.
        Unit(
            id="u:3",
            document_id="d:1",
            page_id="d:1:1",
            line_id="l:1",
            seq=3,
            box=Box(x=80, y=2, w=30, h=40),
            text_source="う",
            unicode="U+3046",
            kind="char",
            method="detect-align",
            active=True,
            meta={"alignment_repair": {"withheld": True, "status": "uncertain"}},
        ),
    ]
    tables.write(root / "documents.parquet", [document], Document, command="test")
    tables.write(root / "pages.parquet", [page], Page)
    tables.write(
        root / "page_texts.parquet",
        [PageText(page_id="d:1:1", source="test", revision="1", text_raw="あい")],
        PageText,
    )
    tables.write(root / "lines.parquet", [line], Line)
    tables.write(root / "units.parquet", units, Unit)
    return root


def review_the_dataset(root: Path) -> Store:
    """A store with a human correction, a confirmed row and a machine split."""
    store = Store(root)
    store.record_batch(
        [
            # A corrected label and box on u:1.
            ReviewRequest(
                target_type="unit",
                target_id="u:1",
                field="unicode",
                new="U+304B",
                base_revision=0,
                client_id="reviewer-1",
                idempotency_key="edit:1:unicode",
                evidence="correction",
            ),
            ReviewRequest(
                target_type="unit",
                target_id="u:1",
                field="box",
                new={"x": 5, "y": 6, "w": 31, "h": 41},
                base_revision=1,
                client_id="reviewer-1",
                idempotency_key="edit:1:box",
                evidence="correction",
            ),
            ReviewRequest(
                target_type="unit",
                target_id="u:1",
                field="review",
                new="reviewed",
                base_revision=2,
                client_id="reviewer-1",
                idempotency_key="edit:1:review",
                evidence="correction",
            ),
            # A plain confirmation.
            ReviewRequest(
                target_type="unit",
                target_id="u:2",
                field="review",
                new="reviewed",
                base_revision=0,
                client_id="reviewer-1",
                idempotency_key="edit:2:review",
                evidence="match",
            ),
        ]
    )
    return store


def split_a_unit(store: Store) -> None:
    """A machine split through the store: two children, one retired parent.

    The store asks `refs.ligature` while it records a split and that helper is still
    being added upstream; the test supplies the truthful answer for い and う (neither
    is a ligature) so the split itself is exercised rather than skipped.
    """
    if not hasattr(refs, "ligature"):
        refs.ligature = lambda code_point: None  # type: ignore[attr-defined]
    store.record_batch(
        [
            ReviewRequest(
                target_type="unit",
                target_id="u:2",
                field="segmentation",
                new={
                    "split": [
                        {
                            "box": {"x": 40, "y": 2, "w": 14, "h": 40},
                            "unicode": "U+3044",
                            "reading": "い",
                            "text_source": "い",
                            "script": "hiragana",
                            "kind": "char",
                            "granularity": "char",
                        },
                        {
                            "box": {"x": 55, "y": 2, "w": 15, "h": 40},
                            "unicode": "U+3046",
                            "reading": "う",
                            "text_source": "う",
                            "script": "hiragana",
                            "kind": "char",
                            "granularity": "char",
                        },
                    ]
                },
                base_revision=1,
                client_id="pipeline",
                idempotency_key="split:u:2",
                evidence="machine split",
            ),
        ],
        role="model",
    )


def corpus_db(path: Path, rows=None) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE IF NOT EXISTS glyph_reviews (
        revision INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        identity TEXT NOT NULL, client_id TEXT NOT NULL, request TEXT NOT NULL,
        source TEXT NOT NULL, decision TEXT NOT NULL, actor_kind TEXT NOT NULL,
        at TEXT NOT NULL)""")
    for row in rows or [
        (
            "corpus|d:1|p:1|-|U+4E00|3|literal_text",
            "rv1",
            {
                "verdict": "wrong",
                "issue": "reading",
                "character": "こ",
                "correction": None,
                "note": "",
                "suggestions": [],
            },
        )
    ]:
        connection.execute(
            "INSERT INTO glyph_reviews(id, identity, client_id, request, source,"
            " decision, actor_kind, at) VALUES (?,?,?,?,?,?,?,?)",
            (
                row[1],
                row[0],
                "reviewer-1",
                "{}",
                "{}",
                json.dumps(row[2], ensure_ascii=False),
                "human",
                "2026-01-01T00:00:00Z",
            ),
        )
    connection.commit()
    connection.close()
    return path


def _event_seqs(path: Path) -> list[tuple[str, int]]:
    """(table, sequence) rows sqlite keeps for AUTOINCREMENT columns."""
    with sqlite3.connect(path) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            return []
        return [tuple(row) for row in connection.execute("SELECT name, seq FROM sqlite_sequence").fetchall()]


def corpus_baseline(path: Path) -> list[dict]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (CORPUS_BASELINE_TABLE,)
        ).fetchone():
            return []
        return [
            dict(row)
            for row in connection.execute(f"SELECT * FROM {CORPUS_BASELINE_TABLE} ORDER BY identity")
        ]


def corpus_floor(path: Path) -> int | None:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='review_meta'").fetchone():
            return None
        row = connection.execute(
            "SELECT value FROM review_meta WHERE key=?", (REVISION_FLOOR_KEY,)
        ).fetchone()
        return int(row["value"]) if row else None


def counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("events", "revisions", "revision_bases", "units", "lines")
        }


class TestCorrectionsSurvive:
    def test_a_corrected_label_and_box_become_the_baseline(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        report = reset_reviews(root)
        assert report.units_reset >= 1

        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        assert units["u:1"].unicode == "U+304B"  # the learned label
        assert units["u:1"].box == Box(x=5, y=6, w=31, h=41)  # the learned box

    def test_split_children_survive_and_the_parent_stays_retired(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        store = review_the_dataset(root)
        split_a_unit(store)  # a machine split, recorded properly
        apply(root)  # then the reviewed state is the tables
        Store(root)  # reconcile, so the store's cache matches the tables
        reset_reviews(root)

        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        parent = units["u:2"]
        assert parent.active is False  # still retired
        assert parent.split_into, "the parent forgot its children"
        children = [units[child] for child in parent.split_into if child in units]
        assert len(children) == len(parent.split_into), "a child is missing from the baseline"
        assert all(child.active for child in children)
        assert [child.text_source for child in children] == ["い", "う"]

    def test_the_source_tables_are_never_touched(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        before = {
            name: (root / f"{name}.parquet").read_bytes() for name in ("documents", "pages", "page_texts")
        }
        review_the_dataset(root)
        reset_reviews(root)
        for name, payload in before.items():
            assert (root / f"{name}.parquet").read_bytes() == payload, name

    def test_source_text_is_not_overwritten_by_pre_review_state(self, tmp_path):
        """The page text the store never held is exactly as it was."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        texts = tables.read(root / "page_texts.parquet", PageText)
        assert [t.text_raw for t in texts] == ["あい"]

    def test_source_provenance_survives_on_the_units(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        assert units["u:1"].document_id == "d:1"
        assert units["u:1"].page_id == "d:1:1"
        assert units["u:1"].line_id == "l:1"
        assert units["u:1"].method == "import"


class TestReviewQueueIsFresh:
    def test_a_human_review_returns_to_machine(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        assert units["u:1"].review == ReviewState.MACHINE
        assert units["u:2"].review == ReviewState.MACHINE

    def test_a_withheld_alignment_stays_withheld(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        report = reset_reviews(root)
        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        # Withheld is the pipeline's refusal, carried by `alignment_repair`; it is not a person's flag.
        assert units["u:3"].review == ReviewState.MACHINE
        assert units["u:3"].meta["alignment_repair"]["withheld"] is True
        assert report.withheld_kept == 1

    def test_the_store_reopens_on_the_fresh_queue(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        reopened = Store(root)
        assert reopened.events() == []
        assert reopened.unit_snapshot("u:1")[0][0].review == ReviewState.MACHINE


class TestHistoryIsGone:
    def test_events_idempotency_and_results_are_erased(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        report = reset_reviews(root)
        assert report.verified["events"] == 0
        assert report.verified["idempotency_keys"] == 0
        assert report.verified["results"] == 0
        assert counts(root / "review.sqlite")["events"] == 0

    def test_the_exported_journal_is_removed(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        apply(root)  # writes the ordinary exported journal
        assert (root / "reviews.jsonl").is_file()
        Store(root)  # reconcile before the offline reset
        report = reset_reviews(root)
        assert not (root / "reviews.jsonl").exists()
        assert report.journal_erased is True

    def test_no_backup_of_the_deleted_history_is_left(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        leftovers = [p.name for p in root.iterdir() if p.suffix in (".bak", ".backup") or "backup" in p.name]
        assert leftovers == []

    def test_the_erased_bytes_are_not_recoverable_from_the_file(self, tmp_path):
        """VACUUM rewrites the file, so a deleted event is not lying in free pages."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        witness = "edit:1:unicode"
        assert witness.encode() in (root / "review.sqlite").read_bytes()
        reset_reviews(root)
        assert witness.encode() not in (root / "review.sqlite").read_bytes()

    def test_corpus_decision_history_is_erased(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        db = corpus_db(root / "reviews.sqlite")
        report = reset_reviews(root)
        assert report.corpus_decisions_erased == 1
        with sqlite3.connect(db) as connection:
            assert connection.execute("SELECT COUNT(*) FROM glyph_reviews").fetchone()[0] == 0


class TestCorpusCorrectionsArePreserved:
    def test_the_latest_correction_becomes_baseline_data(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        baseline = corpus_baseline(root / "reviews.sqlite")
        assert len(baseline) == 1
        entry = baseline[0]
        assert entry["character"] == "こ"  # the applied correction
        # Nothing that describes how the decision was reached survives.
        assert set(entry) == {"identity", "character", "source_revision"}, entry

    def test_the_baseline_is_a_table_with_no_history_columns(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        with sqlite3.connect(root / "reviews.sqlite") as connection:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({CORPUS_BASELINE_TABLE})")]
        assert columns == ["identity", "character", "source_revision"]

    def test_an_explicit_corpus_path_is_used(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        elsewhere = corpus_db(tmp_path / "elsewhere.sqlite")
        report = reset_reviews(root, elsewhere)
        assert report.corpus_decisions_erased == 1

    def test_an_unreadable_decision_refuses_the_whole_reset(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        db = corpus_db(root / "reviews.sqlite")
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE glyph_reviews SET decision='{not json'")
        with pytest.raises(UnsafeReset):
            reset_reviews(root)
        # Nothing was changed.
        assert counts(root / "review.sqlite")["events"] > 0


class TestStaleClientsAreRefused:
    def test_every_revision_is_raised_never_zeroed(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        report = reset_reviews(root)
        assert report.revisions_bumped >= 3
        assert report.verified["lowest_revision"] >= REVISION_BUMP

    def test_a_client_holding_the_old_revision_is_refused(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        store = review_the_dataset(root)
        stale = store.revision("u:1")
        reset_reviews(root)
        reopened = Store(root)
        from glyph_atlas.review.store import Conflict

        with pytest.raises(Conflict):
            reopened.record_batch(
                [
                    ReviewRequest(
                        target_type="unit",
                        target_id="u:1",
                        field="unicode",
                        new="U+304B",
                        base_revision=stale,
                        client_id="browser",
                        idempotency_key="stale:1",
                        evidence="stale",
                    )
                ]
            )

    def test_the_raised_revision_survives_reopening_the_store(self, tmp_path):
        """Rewriting the tables must not make the next open discard the bump."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        Store(root)  # the guard would wipe revisions here
        assert counts(root / "review.sqlite")["revision_bases"] > 0
        with sqlite3.connect(root / "review.sqlite") as connection:
            lowest = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
        assert lowest >= REVISION_BUMP

    def test_the_raised_revision_survives_a_table_change(self, tmp_path):
        """A store rebuilt from changed tables keeps the revisions the reset raised."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        raised = Store(root).revision("u:2")
        units = tables.read(root / "units.parquet", Unit)
        units[0] = units[0].model_copy(update={"text_source": "か"})
        tables.write(root / "units.parquet", units, Unit)
        # The store notices a table by its size and mtime; move the mtime past this second.
        later = (root / "units.parquet").stat().st_mtime + 10
        os.utime(root / "units.parquet", (later, later))
        assert raised >= REVISION_BUMP
        assert Store(root).revision("u:2") == raised

    def test_a_target_outside_the_tables_is_raised_too(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        with sqlite3.connect(root / "review.sqlite") as connection:
            connection.execute("INSERT INTO revisions (target_id, revision) VALUES ('d:1:1', 3)")
        reset_reviews(root)
        assert Store(root).revision("d:1:1") == 3 + REVISION_BUMP

    def test_a_reset_unit_is_unreviewed_again(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        store = Store(root)
        assert store.queue("unreviewed")
        assert store.page_counts("d:1:1")["reviewed"] == 0

    def test_event_sequence_is_not_recycled(self, tmp_path):
        """`sqlite_sequence` keeps the high-water mark, so `seq` never repeats.

        The store numbers its human-readable ids from `MAX(seq)`, which an empty
        events table cannot supply; the database-level sequence is what this module
        can and does preserve, and the mark is recorded beside it.
        """
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        highest = max(seq for _name, seq in _event_seqs(root / "review.sqlite"))
        assert highest > 0
        reset_reviews(root)
        after = dict(_event_seqs(root / "review.sqlite"))
        assert after.get("events", 0) >= highest  # the AUTOINCREMENT mark survives
        with sqlite3.connect(root / "review.sqlite") as connection:
            recorded = connection.execute("SELECT value FROM meta WHERE key='reset_seq'").fetchone()
        assert recorded and int(recorded[0]) == highest

    def test_a_later_event_gets_a_fresh_sequence(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        reopened = Store(root)
        results = reopened.record_batch(
            [
                ReviewRequest(
                    target_type="unit",
                    target_id="u:1",
                    field="review",
                    new="reviewed",
                    base_revision=reopened.revision("u:1"),
                    client_id="later",
                    idempotency_key="later:1",
                    evidence="later",
                )
            ]
        )
        assert results[0]["id"]  # an id was issued, not refused
        with sqlite3.connect(root / "review.sqlite") as connection:
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


class TestRoundUndoState:
    def test_undo_history_is_gone_with_the_events(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        store = review_the_dataset(root)
        assert store.submission_results("reviewer-1", "edit:")
        reset_reviews(root)
        assert Store(root).submission_results("reviewer-1", "edit:") == []


class TestEmptyHistoryStaysEmpty:
    def test_an_empty_export_does_not_reintroduce_history(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        (root / "reviews.jsonl").write_text("", encoding="utf-8")
        store = Store(root)
        assert store.events() == []
        assert counts(root / "review.sqlite")["events"] == 0


class TestIdempotence:
    def test_running_the_reset_twice_is_safe(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        first = reset_reviews(root)
        second = reset_reviews(root)
        assert second.verified["events"] == 0
        assert second.corpus_decisions_erased in (0, first.corpus_decisions_erased)

    def test_the_second_run_keeps_the_corrected_baseline(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        reset_reviews(root)
        units = {u.id: u for u in tables.read(root / "units.parquet", Unit)}
        assert units["u:1"].unicode == "U+304B"


class TestCrashSafety:
    def test_a_marker_records_the_phase_and_is_cleared_on_success(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        assert in_progress(root) is False
        reset_reviews(root)
        assert in_progress(root) is False
        assert read_marker(root) is None

    def test_an_interrupted_reset_is_resumable(self, tmp_path, monkeypatch):
        """A crash before the erase leaves a marker and a dataset still intact."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real = reset_module._erase_store

        def crash(*args, **kwargs):
            raise KeyboardInterrupt("simulated crash")

        monkeypatch.setattr(reset_module, "_erase_store", crash)
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        assert in_progress(root) is True
        marker = read_marker(root)
        assert marker["phase"] == "materialised"
        assert counts(root / "review.sqlite")["events"] > 0  # nothing erased yet

        monkeypatch.setattr(reset_module, "_erase_store", real)
        report = reset_reviews(root)  # resumes
        assert report.verified["events"] == 0
        assert in_progress(root) is False

    def test_a_crash_after_the_erase_still_verifies_on_resume(self, tmp_path, monkeypatch):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real = reset_module._verify

        def crash(*args, **kwargs):
            raise KeyboardInterrupt("simulated crash")

        monkeypatch.setattr(reset_module, "_verify", crash)
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        assert in_progress(root) is True
        monkeypatch.setattr(reset_module, "_verify", real)
        assert reset_reviews(root).verified["events"] == 0

    def test_the_baseline_and_marker_are_fsynced_before_the_erase_commits(self, tmp_path, monkeypatch):
        """A power loss must never find durable history-erasure beside a baseline or
        marker that never made it to disk. Every ``os.fsync`` this reset issues for the
        baseline files and the phase marker has to happen strictly before the erase
        transaction commits.
        """
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        calls: list[str] = []
        real_fsync = os.fsync
        real_erase = reset_module._erase_store

        def recording_fsync(fd, *args, **kwargs):
            calls.append("fsync")
            return real_fsync(fd, *args, **kwargs)

        def recording_erase(*args, **kwargs):
            calls.append("erase")
            return real_erase(*args, **kwargs)

        monkeypatch.setattr(reset_module.os, "fsync", recording_fsync)
        monkeypatch.setattr(reset_module, "_erase_store", recording_erase)
        reset_reviews(root)

        assert "fsync" in calls, "no fsync was issued at all"
        assert "erase" in calls
        assert calls.index("fsync") < calls.index("erase"), (
            "the erase committed before any baseline or marker file was fsynced"
        )

    def test_a_resume_at_erased_with_a_tampered_baseline_refuses(self, tmp_path, monkeypatch):
        """Once the erase has committed, the digest recorded at ``materialised`` is the
        only proof the baseline on disk is what this reset actually wrote — there is no
        history left to fall back on if it is not.
        """
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real_verify = reset_module._verify

        def crash(*args, **kwargs):
            raise KeyboardInterrupt("simulated crash")

        monkeypatch.setattr(reset_module, "_verify", crash)
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        monkeypatch.setattr(reset_module, "_verify", real_verify)

        marker = read_marker(root)
        assert marker["phase"] == "erased"
        assert marker.get("baseline_digest")
        assert reset_module.reset_phase(root / "review.sqlite") == "erased"

        # Corrupt the baseline that was materialised and fsynced before the erase, as a
        # power loss with a half-written disk sector could.
        units_path = root / "units.parquet"
        original = units_path.read_bytes()
        tampered = bytearray(original)
        tampered[len(tampered) // 2] ^= 0xFF
        units_path.write_bytes(bytes(tampered))

        with pytest.raises(UnsafeReset) as error:
            reset_reviews(root)
        assert "does not match" in str(error.value)
        # Refusing must not clear the marker or touch the store further.
        assert in_progress(root) is True
        assert reset_module.reset_phase(root / "review.sqlite") == "erased"


class TestRefusals:
    def test_a_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(ResetError):
            reset_reviews(tmp_path / "nope")

    def test_a_store_with_nothing_to_materialise_is_refused(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        (root / "units.parquet").unlink()
        (root / "units").mkdir()
        with pytest.raises(UnsafeReset):
            reset_reviews(root)

    def test_a_journal_the_store_never_saw_is_refused(self, tmp_path):
        """The correction is only in the log, so deleting both would lose it."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        (root / "reviews.jsonl").write_text(
            json.dumps({"id": "rv99999999", "target_id": "u:1", "field": "unicode", "new": "U+304B"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(UnsafeReset) as error:
            reset_reviews(root)
        assert "rv99999999" in str(error.value)
        assert counts(root / "review.sqlite")["events"] > 0

    def test_a_dry_run_changes_nothing(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        before = counts(root / "review.sqlite")
        report = reset_reviews(root, dry_run=True)
        assert report.dry_run is True and report.units_reset >= 1
        assert counts(root / "review.sqlite") == before
        assert in_progress(root) is False

    def test_no_web_route_is_registered(self):
        """The reset is offline maintenance; nothing exposes it over HTTP."""
        import inspect

        source = inspect.getsource(reset_module)
        assert "APIRouter" not in source and "@api." not in source


class TestOverlayIsCorrectionNotHistory:
    """The corpus overlay carries the applied value, and only that."""

    def test_the_actual_review_shapes_leave_an_empty_overlay(self, tmp_path):
        """A flagged report with no accepted character, and a match: nothing to keep."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(
            root / "reviews.sqlite",
            rows=[
                (
                    "corpus|d:1|p:1|-|U+4E00|3|literal_text",
                    "rv1",
                    {
                        "verdict": "wrong",
                        "issue": "reading",
                        "character": None,
                        "correction": None,
                        "note": "User report: looks like 有",
                        "suggestions": [{"engine": "Reported correction", "text": "有"}],
                    },
                ),
                (
                    "corpus|d:1|p:1|-|U+4E8C|4|literal_text",
                    "rv2",
                    {"verdict": "match", "issue": None, "character": None, "correction": None, "note": ""},
                ),
            ],
        )
        report = reset_reviews(root)
        assert report.corpus_corrections_preserved == 0
        assert report.overlay is None
        assert corpus_baseline(root / "reviews.sqlite") == []

    def test_a_later_decision_that_clears_the_value_removes_the_earlier_one(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        identity = "corpus|d:1|p:1|-|U+4E00|3|literal_text"
        corpus_db(
            root / "reviews.sqlite",
            rows=[
                (
                    identity,
                    "rv1",
                    {
                        "verdict": "wrong",
                        "issue": "reading",
                        "character": "こ",
                        "correction": None,
                        "note": "",
                    },
                ),
                (
                    identity,
                    "rv2",
                    {"verdict": "match", "issue": None, "character": None, "correction": None, "note": ""},
                ),
            ],
        )
        report = reset_reviews(root)
        assert report.corpus_corrections_preserved == 0
        assert corpus_baseline(root / "reviews.sqlite") == []

    def test_the_latest_accepted_character_wins(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        identity = "corpus|d:1|p:1|-|U+4E00|3|literal_text"
        corpus_db(
            root / "reviews.sqlite",
            rows=[
                (
                    identity,
                    "rv1",
                    {
                        "verdict": "wrong",
                        "issue": "reading",
                        "character": "こ",
                        "correction": None,
                        "note": "",
                    },
                ),
                (
                    identity,
                    "rv2",
                    {
                        "verdict": "wrong",
                        "issue": "reading",
                        "character": "さ",
                        "correction": None,
                        "note": "",
                    },
                ),
            ],
        )
        reset_reviews(root)
        baseline = corpus_baseline(root / "reviews.sqlite")
        assert len(baseline) == 1
        assert baseline[0]["character"] == "さ"

    def test_a_multi_character_proposal_is_not_an_accepted_identity(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(
            root / "reviews.sqlite",
            rows=[
                (
                    "corpus|d:1|p:1|-|U+4E00|3|literal_text",
                    "rv1",
                    {
                        "verdict": "wrong",
                        "issue": "merged",
                        "character": None,
                        "correction": "シヨロ",
                        "note": "",
                    },
                ),
            ],
        )
        report = reset_reviews(root)
        assert report.corpus_corrections_preserved == 0

    def test_a_malformed_source_refuses_the_reset(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        db = corpus_db(root / "reviews.sqlite")
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE glyph_reviews SET source='not json'")
        with pytest.raises(UnsafeReset):
            reset_reviews(root)
        assert counts(root / "review.sqlite")["events"] > 0


class TestCompletedResetIsIdempotent:
    def test_a_second_run_does_not_bump_revisions_again(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        first = reset_reviews(root)
        assert first.revisions_bumped > 0
        with sqlite3.connect(root / "review.sqlite") as connection:
            before = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]

        second = reset_reviews(root)
        assert "skipped" in second.phases
        assert second.revisions_bumped == 0
        with sqlite3.connect(root / "review.sqlite") as connection:
            after = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
        assert after == before

    def test_a_changed_baseline_is_reset_again(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        # A new correction lands in the tables, so the recorded baseline is stale. The store is
        # reconciled first, as the reset requires: without it the outcome turned on whether the
        # rewrite fell in the same second as the first reset, which the table stamp cannot see.
        units = tables.read(root / "units.parquet", Unit)
        units[0] = units[0].model_copy(update={"text_source": "か"})
        time.sleep(1.1)
        tables.write(root / "units.parquet", units, Unit)
        Store(root)
        again = reset_reviews(root)
        assert "skipped" not in again.phases

    def test_the_last_reset_mark_is_recorded(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        reset_reviews(root)
        with sqlite3.connect(root / "review.sqlite") as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='last_reset'").fetchone()
        recorded = json.loads(row[0])
        assert recorded["baseline_stamp"] and recorded["reset_seq"] > 0

    def test_a_resumed_reset_does_not_bump_twice(self, tmp_path, monkeypatch):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real = reset_module._verify

        monkeypatch.setattr(
            reset_module,
            "_verify",
            lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("crash after erase")),
        )
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        marker = read_marker(root)
        assert marker["phase"] == "erased" and marker.get("bumped") is True
        with sqlite3.connect(root / "review.sqlite") as connection:
            bumped = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]

        monkeypatch.setattr(reset_module, "_verify", real)
        resumed = reset_reviews(root)
        assert resumed.verified["events"] == 0
        with sqlite3.connect(root / "review.sqlite") as connection:
            after = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
        assert after == bumped  # the bump happened exactly once
        assert in_progress(root) is False


class TestCorpusRevisionFloor:
    """Deleting the decisions must not make every old client valid again."""

    def test_a_revision_zero_client_is_refused_after_the_erase(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        report = reset_reviews(root)
        floor = corpus_floor(root / "reviews.sqlite")
        assert floor == REVISION_BUMP
        assert report.verified["corpus_revision_floor"] == REVISION_BUMP

    def test_the_autoincrement_sequence_is_lifted_to_the_floor(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        with sqlite3.connect(root / "reviews.sqlite") as connection:
            seq = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='glyph_reviews'").fetchone()
        assert seq is not None and seq[0] >= REVISION_BUMP

    def test_a_new_decision_starts_above_the_floor(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        with sqlite3.connect(root / "reviews.sqlite") as connection:
            connection.execute(
                "INSERT INTO glyph_reviews(id, identity, client_id, request, source,"
                " decision, actor_kind, at) VALUES ('new','i','c','{}','{}','{}','human','')"
            )
            revision = connection.execute("SELECT revision FROM glyph_reviews WHERE id='new'").fetchone()[0]
        assert revision > REVISION_BUMP

    def test_a_second_erase_does_not_bump_again(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        floor = corpus_floor(root / "reviews.sqlite")
        # Force a second pass over the same corpus database.
        reset_module._erase_corpus(root / "reviews.sqlite", [])
        assert corpus_floor(root / "reviews.sqlite") == floor

    def test_the_baseline_survives_a_second_erase(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        corpus_db(root / "reviews.sqlite")
        reset_reviews(root)
        before = corpus_baseline(root / "reviews.sqlite")
        reset_module._erase_corpus(root / "reviews.sqlite", [])
        assert corpus_baseline(root / "reviews.sqlite") == before
        assert before[0]["character"] == "こ"


class TestEraseCrashGap:
    """The gap between the erase committing and the marker being written."""

    def test_a_crash_at_the_marker_write_resumes_without_a_second_bump(self, tmp_path, monkeypatch):
        """The store's own meta says the erase committed, so the resume skips it."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real = reset_module._write_marker

        def crash(dataset, payload):
            if payload.get("phase") == "erased":
                raise KeyboardInterrupt("crash exactly at the marker write")
            return real(dataset, payload)

        monkeypatch.setattr(reset_module, "_write_marker", crash)
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        # The erase committed; only the file marker is missing.
        assert counts(root / "review.sqlite")["events"] == 0
        assert reset_module.reset_phase(root / "review.sqlite") == "erased"
        with sqlite3.connect(root / "review.sqlite") as connection:
            bumped = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]

        monkeypatch.setattr(reset_module, "_write_marker", real)
        resumed = reset_reviews(root)
        assert resumed.verified["events"] == 0
        with sqlite3.connect(root / "review.sqlite") as connection:
            after = connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
        assert after == bumped, "the resume bumped revisions a second time"
        assert in_progress(root) is False

    def test_the_resume_does_not_refuse_over_the_emptied_journal(self, tmp_path, monkeypatch):
        """An emptied store beside a live journal is the erased state, not a loss."""
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        real = reset_module._write_marker
        monkeypatch.setattr(
            reset_module,
            "_write_marker",
            lambda dataset, payload: (
                (_ for _ in ()).throw(KeyboardInterrupt())
                if payload.get("phase") == "erased"
                else real(dataset, payload)
            ),
        )
        with pytest.raises(KeyboardInterrupt):
            reset_reviews(root)
        monkeypatch.setattr(reset_module, "_write_marker", real)
        # The journal is still there from the run that crashed; the store is empty.
        assert reset_reviews(root).verified["events"] == 0


class TestMalformedJournalIsRefused:
    def test_an_unreadable_line_stops_the_reset(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        (root / "reviews.jsonl").write_text('{"id": "rv1"}\n{not json at all\n', encoding="utf-8")
        with pytest.raises(UnsafeReset) as error:
            reset_reviews(root)
        assert "line 2" in str(error.value)
        assert counts(root / "review.sqlite")["events"] > 0  # nothing was erased

    def test_a_non_object_line_stops_the_reset(self, tmp_path):
        root = build_dataset(tmp_path / "work" / "honkoku-lines")
        review_the_dataset(root)
        (root / "reviews.jsonl").write_text("[1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(UnsafeReset):
            reset_reviews(root)


def test_erase_phase_is_committed_before_return(tmp_path, monkeypatch):
    root = build_dataset(tmp_path / "work" / "honkoku-lines")
    review_the_dataset(root)
    real = reset_module._erase_store
    def crash(*args, **kwargs):
        real(*args, **kwargs)
        raise KeyboardInterrupt("after transaction, before return")
    monkeypatch.setattr(reset_module, "_erase_store", crash)
    with pytest.raises(KeyboardInterrupt):
        reset_reviews(root)
    with sqlite3.connect(root / "review.sqlite") as db:
        before = db.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
    assert reset_module.reset_phase(root / "review.sqlite") == "erased"
    monkeypatch.setattr(reset_module, "_erase_store", real)
    reset_reviews(root)
    with sqlite3.connect(root / "review.sqlite") as db:
        assert db.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0] == before


def test_reset_removes_embedded_feedback_history():
    from glyph_atlas.schema import Unit
    unit = Unit(id="x", text_source="を", reading="を", unicode="U+3092",
                meta={"feedback_repair": {"source_actor": "person", "source_event_id": "rv1"},
                      "alignment_repair": {"withheld": True}})
    result, _, _ = reset_module._reset_unit(unit)
    assert result.reading == "を" and "feedback_repair" not in result.meta
    assert result.meta["alignment_repair"]["withheld"]


def test_a_withheld_row_is_neither_flagged_nor_dealt_after_a_reset():
    """The reset keeps the withhold, and the withhold alone keeps the row out of review rounds."""
    from glyph_atlas.review import atlas, status
    from glyph_atlas.schema import Unit
    unit = Unit(id="w", text_source="を", reading="を", unicode="U+3092", review=ReviewState.DISPUTED,
                meta={"alignment_repair": {"withheld": True, "quiz": False, "status": "uncertain"}})
    result, was_reset, kept = reset_module._reset_unit(unit)
    assert result.review == ReviewState.MACHINE and was_reset and kept
    standing = status.unit_reviews([result], [])[result.id]
    assert atlas.review_state(standing.human_review) == "pending", "not in the flagged list"
    assert atlas.repair_withheld(result), "still out of every round"
