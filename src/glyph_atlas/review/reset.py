"""Permanently erase review history, keeping everything that was learned.

This is the maintenance path for a decided question: the reviewer has asked for the
history itself to go. What must not go with it is the *result*. A correction that was
made to a label, a box, a segmentation or a source reference is data now; the events
that produced it are history. This module writes the data down as a new baseline and
then destroys the history, in that order, so a crash at any point leaves the dataset
either untouched or repairable — never stripped of the corrections.

What it does, in order:

1. **Materialises the corrected state.** The current reviewed projection of ``lines``
   and ``units`` is read out of the store and written back as the dataset's baseline
   Parquet. Source tables — documents, pages, page texts — are never touched.
2. **Resets the review queue.** A unit a person checked goes back to ``machine`` so it
   can be reviewed again. A unit the pipeline marked unsafe — ``alignment_repair`` with
   ``withheld`` — stays withheld: that is not history, it is a standing refusal to
   trust the row.
3. **Preserves the corpus character overlay.** Decisions in the corpus review database
   are exported as a small baseline overlay before their history is deleted.
4. **Erases the history.** Review events, idempotency keys, stored results, the
   exported ``reviews.jsonl`` journal and the corpus decision log.
5. **Invalidates stale clients.** Every current target's revision is *raised*, never
   zeroed, into a base the store keeps when it rebuilds from changed tables. A browser
   holding the old revision is refused.
6. **Leaves no copy behind.** No backup of the deleted history, ``VACUUM`` and a WAL
   truncation on both databases, then a verification that the journals are empty.

Crash safety is a marker file. Each phase is recorded before it starts and is
idempotent, so re-running resumes rather than restarting, and a half-finished reset is
visible in the dataset directory rather than silent.

**Stop the viewer first.** SQLite is happy with concurrent readers but this rewrites
the files under them, and the erased history cannot be recovered from a running
server. There is no web route for any of this; it is an offline maintenance call.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import tables
from ..schema import Line, ReviewState, Unit
from .corpus_reviews import last_human_character

#: The store and the exported journal inside a dataset directory.
STORE_NAME = "review.sqlite"
LOG_NAME = "reviews.jsonl"
#: The corpus review database, when it does not sit beside the dataset.
CORPUS_NAME = "reviews.sqlite"
CORPUS_TABLE = "glyph_reviews"
GLYPH_TABLE = CORPUS_TABLE

#: The corpus baseline a reset leaves behind. The corrections are *consumed* data, not
#: a side file: `glyph_baseline` is the table the corpus layer reads, and
#: `review_meta.revision_floor` is what refuses a client that still holds revision 0.
CORPUS_BASELINE_TABLE = "glyph_baseline"
CORPUS_META_TABLE = "review_meta"
REVISION_FLOOR_KEY = "revision_floor"
#: Where the maintenance marker lives.
OVERLAY_NAME = CORPUS_BASELINE_TABLE
MARKER_NAME = "review-reset.json"
STAGING_NAME = ".review-reset-staging"

#: How far every target's revision moves. A browser cannot hold a revision this far
#: ahead, so every request it has in flight is refused rather than silently replaying
#: onto a state whose history no longer exists.
REVISION_BUMP = 1_000_000

#: Tables the reset rewrites. Everything else in the directory is source data.
REWRITTEN = ("lines", "units")

PHASES = ("started", "materialised", "erased", "sealed")

#: Review states a person authored. Any of them returns to machine on a reset.
HUMAN_STATES = frozenset(
    {
        ReviewState.TRANSCRIBER.value,
        ReviewState.REVIEWED.value,
        ReviewState.DOUBLE_REVIEWED.value,
        ReviewState.ADJUDICATED.value,
        ReviewState.DISPUTED.value,
        ReviewState.REJECTED.value,
    }
)


class ResetError(RuntimeError):
    """The reset cannot proceed."""


class UnsafeReset(ResetError):
    """Proceeding would lose corrections, so nothing was changed."""


@dataclass
class Report:
    """What the reset did, or what it would do."""

    dataset: str
    dry_run: bool = False
    phases: list[str] = field(default_factory=list)
    units: int = 0
    lines: int = 0
    units_reset: int = 0
    withheld_kept: int = 0
    revisions_bumped: int = 0
    events_erased: int = 0
    journal_erased: bool = False
    corpus_decisions_erased: int = 0
    corpus_corrections_preserved: int = 0
    overlay: str | None = None
    verified: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


# --------------------------------------------------------------------- paths
def store_path(dataset: Path) -> Path:
    return Path(dataset) / STORE_NAME


def log_path(dataset: Path) -> Path:
    return Path(dataset) / LOG_NAME


def marker_path(dataset: Path) -> Path:
    return Path(dataset) / MARKER_NAME


def corpus_path(dataset: Path, corpus_reviews_path: str | Path | None) -> Path | None:
    """The corpus review database, or None when there is not one."""
    if corpus_reviews_path is not None:
        path = Path(corpus_reviews_path)
        return path if path.is_file() else None
    for candidate in (Path(dataset) / CORPUS_NAME, Path(dataset).parent / CORPUS_NAME):
        if candidate.is_file():
            return candidate
    return None


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


# ---------------------------------------------------------------------- sqlite
@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success and always closes."""
    connection = sqlite3.connect(path, isolation_level=None, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def vacuum(path: Path) -> None:
    """Rewrite the file without the deleted pages, and truncate the WAL.

    ``VACUUM`` is what makes the erasure real on disk: until it runs, the deleted
    events are still in free pages. It needs its own connection and no transaction.
    """
    connection = sqlite3.connect(path, isolation_level=None, timeout=60.0)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


# ------------------------------------------------------------------- reading
def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(connection, table):
        return []
    return connection.execute(f"SELECT * FROM {table}").fetchall()


def _model(row: sqlite3.Row, model: type) -> Any:
    return model.model_validate(json.loads(row["data"]))


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _is_withheld(unit: Unit) -> bool:
    """Whether the pipeline itself refuses to trust this row.

    A withheld alignment is not a human decision that the reset undoes; it is a
    standing statement that the row is unsafe, and it survives.
    """
    repair = unit.meta.get("alignment_repair") if isinstance(unit.meta, Mapping) else None
    return bool(isinstance(repair, Mapping) and repair.get("withheld"))


def _reset_unit(unit: Unit) -> tuple[Unit, bool, bool]:
    """One unit as it should stand after the reset: (unit, reset?, withheld kept?)."""
    # The corrected fields and machine geometry are baseline data. A saved actor,
    # verdict, or event reference would retain the history the user asked to erase.
    if "feedback_repair" in unit.meta:
        unit = unit.model_copy(update={"meta": {k: v for k, v in unit.meta.items()
                                                if k != "feedback_repair"}})
    if _is_withheld(unit):
        # The withhold is the pipeline's and lives in `alignment_repair`, which the quiz and the
        # passes read. It is no review state: written as `disputed`, every withheld row read as a
        # person's flag, filled the flagged list, and was pinned against the passes meant to fix it.
        was_reviewed = unit.review.value in HUMAN_STATES
        return unit.model_copy(update={"review": ReviewState.MACHINE}), was_reviewed, True
    if unit.review.value in HUMAN_STATES:
        return unit.model_copy(update={"review": ReviewState.MACHINE}), True, False
    return unit, False, False


# ------------------------------------------------------- verification of safety
def _unmaterialised_events(dataset: Path, store: Path) -> list[str]:
    """Journal entries the store does not hold, which cannot be materialised.

    ``reviews.jsonl`` is the way out of a store that could not record an event; if it
    holds one the store never saw, deleting both would lose it. The reset refuses
    rather than guessing.
    """
    log = log_path(dataset)
    if not log.is_file():
        return []
    known: set[str] = set()
    if store.is_file():
        with closing(sqlite3.connect(store)) as connection:
            connection.row_factory = sqlite3.Row
            if table_exists(connection, "events"):
                known = {row["id"] for row in connection.execute("SELECT id FROM events").fetchall()}
    missing: list[str] = []
    for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError as error:
            # A line the reset cannot read is an event it cannot account for. Skipping
            # it would erase a decision nobody managed to materialise.
            raise UnsafeReset(
                f"{LOG_NAME} line {number} is not readable JSON ({error}); the journal"
                f" must be reconciled before its history is erased"
            ) from error
        if not isinstance(event, Mapping):
            raise UnsafeReset(f"{LOG_NAME} line {number} is not an object")
        ident = event.get("id")
        if ident and ident not in known:
            missing.append(str(ident))
    return missing


def _journal_ids(dataset: Path) -> set[str]:
    log = log_path(dataset)
    if not log.is_file():
        return set()
    ids: set[str] = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("id"):
            ids.add(str(event["id"]))
    return ids


def _unexported_events(dataset: Path, store: Path) -> list[str]:
    """Store events the exported journal has never seen.

    The store treats these as the reason it cannot rebuild; the reset treats them as
    the reason it must not either, because rows only the store knows would be lost.
    """
    if not store.is_file():
        return []
    with closing(sqlite3.connect(store)) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, "events"):
            return []
        ids = [row["id"] for row in connection.execute("SELECT id FROM events").fetchall()]
    exported = _journal_ids(dataset)
    return [ident for ident in ids if ident not in exported]


def _corpus_overlay(path: Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    """The accepted character correction per identity — and nothing else.

    A review is history. What survives is only what was *applied*: the character the
    corpus now stands by, the identity it belongs to, and the source revision the
    decision was made against so a later baseline change can be detected. Verdicts,
    notes, actors, event ids and timestamps are the record of how the decision was
    reached and do not survive; a proposal that was never accepted as a character does
    not either.

    Per identity, the last decision's own character wins when it has one. An issue
    report, a bare match or an unapplied proposal carries none and does not erase an
    earlier correction: the walk back follows ``CorpusReviews.overlay``, so a reset
    agrees with the live review view. An identity with no human character carries
    nothing forward.
    """
    if path is None or not path.is_file():
        return [], []
    per_identity: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, CORPUS_TABLE):
            return [], []
        rows = connection.execute(
            f"SELECT identity, source, decision, actor_kind FROM {CORPUS_TABLE} ORDER BY revision"
        ).fetchall()
    for row in rows:
        identity = row["identity"]
        try:
            source = json.loads(row["source"] or "{}")
            decision = json.loads(row["decision"] or "{}")
        except ValueError:
            problems.append(f"{identity}: source or decision is not readable JSON")
            continue
        if not isinstance(source, Mapping) or not isinstance(decision, Mapping):
            problems.append(f"{identity}: source or decision is not an object")
            continue
        per_identity.setdefault(identity, []).append(
            {"source": source, "decision": decision, "actor_kind": row["actor_kind"]}
        )
    latest: dict[str, dict[str, Any] | None] = {}
    for identity in sorted(per_identity):
        events = per_identity[identity]
        last = events[-1]
        revision = last["source"].get("source_revision")
        character = last["decision"].get("character")
        if character is None:
            character = last_human_character(reversed(events), revision)
        if character is None:
            # No decision ever landed an explicit human character for this identity:
            # nothing to carry forward.
            latest[identity] = None
            continue
        if not isinstance(character, str) or len(character) != 1:
            problems.append(f"{identity}: accepted character is not one character")
            continue
        latest[identity] = {
            "identity": identity,
            "character": character,
            "source_revision": revision if isinstance(revision, str) else None,
            "state": "corpus-correction-baseline",
        }
    return [latest[key] for key in sorted(latest) if latest[key] is not None], problems


# ------------------------------------------------------------------ baseline
def _write_table(dataset: Path, name: str, records: Sequence[Any], model: type, staging: Path) -> Path:
    """Write one rewritten table into staging, ready to be moved into place."""
    if (dataset / name).is_dir():
        target = staging / name
        target.mkdir(parents=True, exist_ok=True)
        tables.write(target, records, model, shard=True, command="atlas review reset")
        return target
    target = (staging / name).with_suffix(".parquet")
    tables.write(target, records, model, command="atlas review reset")
    return target


def _move_into_place(dataset: Path, name: str, staged: Path) -> None:
    if staged.is_dir():
        destination = dataset / name
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staged, destination)
    else:
        destination = (dataset / name).with_suffix(".parquet")
        os.replace(staged, destination)


def _stamp(dataset: Path) -> str:
    """The table fingerprint the store compares, computed the same way it does."""
    parts: list[str] = []
    for name in REWRITTEN:
        single = (dataset / name).with_suffix(".parquet")
        directory = dataset / name
        if directory.is_dir():
            files = sorted(directory.glob("*.parquet"))
            detail = ";".join(f"{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}" for f in files)
        elif single.is_file():
            detail = f"{single.stat().st_size}:{int(single.stat().st_mtime)}"
        else:
            detail = "-"
        parts.append(f"{name}:{detail}")
    return "|".join(parts)


# -------------------------------------------------------------------- marker
def baseline_digest(dataset: Path) -> str:
    """A content digest of the tables a reset rewrites.

    The store's own fingerprint is size and mtime, which cannot see a rewrite that
    lands in the same second at the same size. A completed reset is only skipped when
    the *content* is the content it wrote.
    """
    hasher = hashlib.sha256()
    for name in REWRITTEN:
        directory = dataset / name
        single = dataset / f"{name}.parquet"
        files = (
            sorted(directory.glob("*.parquet"))
            if directory.is_dir()
            else ([single] if single.is_file() else [])
        )
        for path in files:
            hasher.update(path.name.encode("utf-8"))
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    hasher.update(block)
    return hasher.hexdigest()


def read_marker(dataset: Path) -> dict[str, Any] | None:
    path = marker_path(dataset)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"phase": "unknown", "damaged": True}


def _write_marker(dataset: Path, payload: Mapping[str, Any]) -> None:
    path = marker_path(dataset)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _clear_marker(dataset: Path) -> None:
    marker_path(dataset).unlink(missing_ok=True)


def in_progress(dataset: str | Path) -> bool:
    """Whether a reset was interrupted and needs to be resumed."""
    return read_marker(Path(dataset)) is not None


# ---------------------------------------------------------------------- reset
def reset_reviews(
    dataset_directory: str | Path, corpus_reviews_path: str | Path | None = None, *, dry_run: bool = False
) -> Report:
    """Erase review history, keeping every correction that was learned.

    ``dataset_directory`` holds the review store, the exported journal and the
    dataset tables. ``corpus_reviews_path`` names the corpus review database; when it
    is omitted, ``reviews.sqlite`` beside the dataset is used if it is there.

    Stop the viewer before calling this. Re-running is safe: each phase is recorded in
    a marker and is idempotent, so an interrupted reset resumes where it stopped.

    Raises :class:`UnsafeReset` — changing nothing — when a correction could not be
    materialised, because losing one silently is worse than not running.
    """
    dataset = Path(dataset_directory)
    if not dataset.is_dir():
        raise ResetError(f"{dataset.name} is not a directory")
    store = store_path(dataset)
    corpus = corpus_path(dataset, corpus_reviews_path)
    report = Report(dataset=dataset.name, dry_run=dry_run)

    # A store whose cached tables no longer match the dataset is a cache, not a
    # source: rebuilding the baseline from it would put pre-review rows back over
    # newer ones. With events outstanding it is worse than stale, it is unresolved.
    if store.is_file():
        with closing(sqlite3.connect(store)) as connection:
            connection.row_factory = sqlite3.Row
            loaded = (
                connection.execute("SELECT value FROM meta WHERE key='source_stamp'").fetchone()
                if table_exists(connection, "meta")
                else None
            )
            pending = (
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if table_exists(connection, "events")
                else 0
            )
        # Any mismatch this reset did not itself make is refused, whether or not the
        # journal can account for the events: an exported journal proves the events
        # were written somewhere, not that the store's cached rows are the dataset's
        # current rows. Rebuilding a baseline from a cache is how a newer correction
        # gets overwritten by an older one. Only a reset already under way, recorded in
        # the store's own meta, is allowed to have moved the tables.
        if loaded is not None and loaded["value"] != _stamp(dataset) and not _underway(store):
            raise UnsafeReset(
                f"the store's cached tables do not match the dataset ({pending} event(s)"
                f" held), so its rows may be older than the tables': reconcile first by"
                f" opening the Store (or running `atlas review apply`), then run the"
                f" reset"
            )

    # The journal's events are gone by design once the erase has committed, so the
    # check only makes sense before it does. This is the crash gap the store's own
    # phase closes: after the commit it says `erased`, and the resume skips the check
    # instead of refusing over events it deliberately erased.
    missing = [] if reset_phase(store) == "erased" else _unmaterialised_events(dataset, store)
    if missing:
        raise UnsafeReset(
            f"{LOG_NAME} holds {len(missing)} event(s) the store never recorded, so"
            f" their corrections are not in the baseline and deleting both would lose"
            f" them; run `atlas review apply` first (first: {missing[0]})"
        )

    raw_units: list[sqlite3.Row] = []
    raw_lines: list[sqlite3.Row] = []
    if store.is_file():
        with closing(sqlite3.connect(store)) as connection:
            connection.row_factory = sqlite3.Row
            raw_units = _rows(connection, "units")
            raw_lines = _rows(connection, "lines")
    units = [_model(row, Unit) for row in raw_units]
    lines = [_model(row, Line) for row in raw_lines]
    if not units and not lines:
        raise UnsafeReset(
            "the store holds no lines or units to materialise, so a baseline cannot be"
            " rebuilt from it; nothing was changed"
        )

    reset_units = []
    for unit in units:
        replacement, was_reset, withheld = _reset_unit(unit)
        reset_units.append(replacement)
        report.units_reset += int(was_reset)
        report.withheld_kept += int(withheld)
    report.units = len(reset_units)
    report.lines = len(lines)

    overlay, problems = _corpus_overlay(corpus)
    if problems:
        raise UnsafeReset("the corpus review log holds unreadable decisions: " + "; ".join(problems[:3]))
    report.corpus_corrections_preserved = len(overlay)
    report.overlay = CORPUS_BASELINE_TABLE if overlay else None

    if dry_run:
        report.phases.append("started")
        report.verified = _verify(dataset, store, corpus, dry_run=True)
        return report

    # A reset that already ran and left nothing behind is not run again: a second
    # revision bump would be a change nobody asked for, and there is no history left to
    # erase. The marker phase drives the rest, so an interrupted run resumes at the step
    # it stopped on and never repeats the bump.
    marker = read_marker(dataset) or {}
    # The store's committed phase wins over the file marker, which is written after its
    # work and can therefore be missing exactly when it matters most.
    phase = _furthest(reset_phase(store), marker.get("phase"))
    if phase is None and _already_reset(dataset, store, corpus):
        report.phases.append("skipped")
        report.verified = _verify(dataset, store, corpus)
        return report
    if phase is None:
        _write_marker(
            dataset, {"phase": "started", "at": _now(), "dataset": dataset.name, "stop_the_viewer": True}
        )
        phase = "started"
        _set_reset_phase(store, "started")
    report.phases.append(phase)

    # ---- materialise: the corrections become the baseline before anything is deleted
    if phase == "started":
        staging = dataset / STAGING_NAME
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            if reset_units:
                staged = _write_table(dataset, "units", reset_units, Unit, staging)
                _move_into_place(dataset, "units", staged)
            if lines:
                staged = _write_table(dataset, "lines", lines, Line, staging)
                _move_into_place(dataset, "lines", staged)

        finally:
            shutil.rmtree(staging, ignore_errors=True)
        stamp = _stamp(dataset)
        _set_reset_phase(store, "materialised")
        _write_marker(
            dataset,
            {
                "phase": "materialised",
                "at": _now(),
                "dataset": dataset.name,
                "stamp": stamp,
                "units": report.units,
                "lines": report.lines,
            },
        )
        report.phases.append("materialised")
        phase = "materialised"
    stamp = _stamp(dataset)

    # ---- erase: the history, and only the history
    if phase == "materialised":
        report.events_erased = _erase_store(store, raw_units, reset_units, raw_lines, lines, stamp, report)
        _write_marker(
            dataset,
            {"phase": "erased", "at": _now(), "dataset": dataset.name, "stamp": stamp, "bumped": True},
        )
        report.phases.append("erased")
    report.journal_erased = log_path(dataset).is_file()
    log_path(dataset).unlink(missing_ok=True)
    if (dataset / "feedback-backups").exists():
        shutil.rmtree(dataset / "feedback-backups")
    if corpus is not None:
        report.corpus_decisions_erased = _erase_corpus(corpus, overlay)

    # ---- seal: make the erasure real, then check it
    if store.is_file():
        vacuum(store)
    if corpus is not None:
        vacuum(corpus)
    report.verified = _verify(dataset, store, corpus)
    _set_reset_phase(store, None)  # the reset is complete
    _clear_marker(dataset)
    report.phases.append("sealed")
    return report


PHASE_ORDER = {"started": 0, "materialised": 1, "erased": 2}


def reset_phase(store: Path) -> str | None:
    """The phase of a reset in progress, read from the store's own meta.

    The file marker is written *after* its work; this one is written *with* it, inside
    the same transaction, so a crash between the commit and the marker write cannot
    lose the fact that the erase already happened.
    """
    if not store.is_file():
        return None
    with closing(sqlite3.connect(store)) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, "meta"):
            return None
        row = connection.execute("SELECT value FROM meta WHERE key='reset_phase'").fetchone()
    return row["value"] if row else None


def _underway(store: Path) -> bool:
    """Whether a reset this module started is still in progress."""
    return reset_phase(store) in PHASE_ORDER


def _furthest(store_phase: str | None, marker_phase: str | None) -> str | None:
    """The later of the two records: the store's commit beats the file's intention."""
    if store_phase is None:
        return marker_phase
    if marker_phase is None:
        return store_phase
    return max((store_phase, marker_phase), key=lambda name: PHASE_ORDER.get(name, 0))


def _set_reset_phase(store: Path, phase: str | None) -> None:
    """Record the phase in the store, or clear it when the reset is complete."""
    if not store.is_file():
        return
    with connect(store) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, "meta"):
            return
        if phase is None:
            connection.execute("DELETE FROM meta WHERE key='reset_phase'")
        else:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES ('reset_phase', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (phase,),
            )


def _already_reset(dataset: Path, store: Path, corpus: Path | None) -> bool:
    """Whether a completed reset is already in effect.

    True only when every trace is gone *and* the baseline is the one the last reset
    wrote. An unchanged dataset with no history is not reset a second time: bumping
    every revision again would be a change nobody asked for.
    """
    if log_path(dataset).is_file() or not store.is_file():
        return False
    with closing(sqlite3.connect(store)) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, "meta"):
            return False
        row = connection.execute("SELECT value FROM meta WHERE key='last_reset'").fetchone()
        if not row:
            return False
        if (
            table_exists(connection, "events")
            and connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        ):
            return False
    if corpus is not None and corpus.is_file():
        with closing(sqlite3.connect(corpus)) as connection:
            if (
                table_exists(connection, GLYPH_TABLE)
                and connection.execute(f"SELECT COUNT(*) FROM {GLYPH_TABLE}").fetchone()[0]
            ):
                return False
    try:
        recorded = json.loads(row["value"])
    except ValueError:
        return False
    if not recorded.get("baseline_stamp") or recorded["baseline_stamp"] != _stamp(dataset):
        return False
    return recorded.get("baseline_digest") == baseline_digest(dataset)


def _erase_store(
    store: Path,
    raw_units: Sequence[sqlite3.Row],
    units: Sequence[Unit],
    raw_lines: Sequence[sqlite3.Row],
    lines: Sequence[Line],
    stamp: str,
    report: Report,
) -> int:
    """Clear the events, keep the ids monotonic, raise every revision.

    ``sqlite_sequence`` is left alone: the next event id continues from where the
    erased ones stopped, so an id is never reused for a different decision. The store's
    own copies of the tables are rewritten to the reset state and its table fingerprint
    updated, so the next open keeps the raised revisions instead of discarding them as
    a table change.
    """
    if not store.is_file():
        return 0
    highest = 0
    with connect(store) as connection:
        connection.row_factory = sqlite3.Row
        erased = 0
        if table_exists(connection, "events"):
            erased = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            # The high-water mark of the AUTOINCREMENT sequence is kept: deleting the
            # rows must not let `seq` start handing out numbers it already used, so
            # `sqlite_sequence` is left exactly as it is and the mark is recorded
            # beside it for anyone numbering ids from the events table.
            highest = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
            connection.execute("DELETE FROM events")
            connection.execute(
                "INSERT INTO meta (key, value) VALUES ('reset_seq', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(highest),),
            )
            # The completed-reset mark: a re-run sees it, sees no history and an
            # unchanged baseline, and does nothing rather than bumping again.
            connection.execute(
                "INSERT INTO meta (key, value) VALUES ('last_reset', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (
                    json.dumps(
                        {
                            "baseline_stamp": stamp,
                            "reset_seq": highest,
                            "baseline_digest": baseline_digest(store.parent),
                            "at": _now(),
                        },
                        sort_keys=True,
                    ),
                ),
            )
        # The store's own copies are rewritten to the reset state. Their join columns
        # come from the rows that were just read, so nothing about where a unit lives
        # is recomputed from a table that has already been emptied.
        if table_exists(connection, "units"):
            connection.execute("DELETE FROM units")
            for raw, record in zip(raw_units, units, strict=True):
                connection.execute(
                    "INSERT INTO units (id, document_id, page_id, line_id, seq, active,"
                    " data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        raw["document_id"],
                        raw["page_id"],
                        raw["line_id"],
                        raw["seq"],
                        1 if record.active else 0,
                        _dump(record),
                    ),
                )
        if table_exists(connection, "lines"):
            connection.execute("DELETE FROM lines")
            for raw, record in zip(raw_lines, lines, strict=True):
                connection.execute(
                    "INSERT INTO lines (id, document_id, page_id, seq, data) VALUES (?, ?, ?, ?, ?)",
                    (record.id, raw["document_id"], raw["page_id"], raw["seq"], _dump(record)),
                )
        report.revisions_bumped = _bump_revisions(connection, units, lines)
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('source_stamp', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (stamp,),
        )
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('reset_at', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_now(),),
        )
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('reset_phase', 'erased')"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
    return erased


def _bump_revisions(connection: sqlite3.Connection, units: Sequence[Unit], lines: Sequence[Line]) -> int:
    """Raise every current target's revision so an in-flight client is refused.

    The raised value is the target's base in ``revision_bases``, and its event count in
    ``revisions`` starts again from none. The store rebuilds ``revisions`` from the
    events whenever its tables change, so a revision held there would fall back to the
    event count; the base is never rebuilt, and an event row keeps meaning that somebody
    touched the target since the reset.
    """
    targets = {record.id for record in units} | {record.id for record in lines}
    connection.execute(
        "CREATE TABLE IF NOT EXISTS revision_bases (target_id TEXT PRIMARY KEY, base INTEGER NOT NULL)"
    )
    current = {row["target_id"]: row["base"] for row in connection.execute("SELECT * FROM revision_bases")}
    if table_exists(connection, "revisions"):
        for row in connection.execute("SELECT target_id, revision FROM revisions"):
            current[row["target_id"]] = current.get(row["target_id"], 0) + row["revision"]
        connection.execute("DELETE FROM revisions")
    # A target with events outside the reset tables, a page or a unit a split made, is raised too.
    for target in targets | current.keys():
        connection.execute(
            "INSERT INTO revision_bases (target_id, base) VALUES (?, ?)"
            " ON CONFLICT(target_id) DO UPDATE SET base = excluded.base",
            (target, current.get(target, 0) + REVISION_BUMP),
        )
    return len(targets | current.keys())


def _erase_corpus(path: Path, corrections: Sequence[Mapping[str, Any]]) -> int:
    """Consume the corrections into a baseline table, then erase the decisions.

    Two things have to survive the erasure and one of them is not data:

    * an accepted character becomes a row of ``glyph_baseline`` — the correction the
      corpus layer reads instead of replaying a decision;
    * the decision *history* has to stop being reachable, which for a client means its
      revision no longer existing. Deleting the rows alone would leave every stored
      revision of 0 valid again, so a ``revision_floor`` a million above the old range
      is recorded in ``review_meta`` and the AUTOINCREMENT sequence is lifted to match.
      A client holding an old revision is then refused, and the next real decision
      starts above the floor.

    All of it commits together: a crash leaves either the history intact or the
    baseline written, never the corrections consumed and the decisions still live.
    """
    with connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {CORPUS_BASELINE_TABLE} ("
            " identity TEXT PRIMARY KEY, character TEXT NOT NULL,"
            " source_revision TEXT)"
        )
        for correction in corrections:
            connection.execute(
                f"INSERT INTO {CORPUS_BASELINE_TABLE} (identity, character, source_revision)"
                " VALUES (?, ?, ?) ON CONFLICT(identity) DO UPDATE SET"
                " character = excluded.character, source_revision = excluded.source_revision",
                (correction["identity"], correction["character"], correction.get("source_revision")),
            )
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {CORPUS_META_TABLE} ( key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        floor_row = connection.execute(
            f"SELECT value FROM {CORPUS_META_TABLE} WHERE key=?", (REVISION_FLOOR_KEY,)
        ).fetchone()
        erased = 0
        if table_exists(connection, CORPUS_TABLE):
            erased = connection.execute(f"SELECT COUNT(*) FROM {CORPUS_TABLE}").fetchone()[0]
            connection.execute(f"DELETE FROM {CORPUS_TABLE}")
        # One bump per real erasure. A repeat sees an empty table and an existing floor
        # and leaves both alone; the first run records a floor even with no history, so
        # a revision-0 client is never valid again.
        if erased or floor_row is None:
            floor = int(floor_row["value"] if floor_row else 0) + REVISION_BUMP
            connection.execute(
                f"INSERT INTO {CORPUS_META_TABLE} (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (REVISION_FLOOR_KEY, str(floor)),
            )
            # SQLite creates `sqlite_sequence` itself for an AUTOINCREMENT table and
            # reserves the name, so it is updated only when it is already there. The
            # floor in `review_meta` is what the corpus layer reads; this keeps the
            # next AUTOINCREMENT id from landing below it.
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
                # `sqlite_sequence` has no unique index of its own, so it is updated
                # rather than upserted.
                changed = connection.execute(
                    "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (floor, CORPUS_TABLE)
                ).rowcount
                if not changed:
                    connection.execute(
                        "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (CORPUS_TABLE, floor)
                    )
    return erased


def _write_overlay(dataset: Path, overlay: Sequence[Mapping[str, Any]]) -> None:
    path = dataset / OVERLAY_NAME
    temporary = path.with_suffix(".json.part")
    temporary.write_text(
        json.dumps(
            {
                "kind": "corpus-character-corrections",
                "note": "Baseline corpus corrections preserved from review history before the"
                " history was erased. This is data, not a journal.",
                "count": len(overlay),
                "corrections": list(overlay),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


# ------------------------------------------------------------------ verifying
def _verify(dataset: Path, store: Path, corpus: Path | None, *, dry_run: bool = False) -> dict[str, Any]:
    """Check that the history is actually gone and the baseline is readable."""
    verified: dict[str, Any] = {"dry_run": dry_run}
    if store.is_file():
        with closing(sqlite3.connect(store)) as connection:
            connection.row_factory = sqlite3.Row
            verified["events"] = (
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if table_exists(connection, "events")
                else 0
            )
            verified["idempotency_keys"] = (
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE idempotency_key IS NOT NULL"
                ).fetchone()[0]
                if table_exists(connection, "events")
                else 0
            )
            verified["results"] = (
                connection.execute("SELECT COUNT(*) FROM events WHERE result IS NOT NULL").fetchone()[0]
                if table_exists(connection, "events")
                else 0
            )
            verified["revisions"] = (
                connection.execute("SELECT COUNT(*) FROM revision_bases").fetchone()[0]
                if table_exists(connection, "revision_bases")
                else 0
            )
            verified["lowest_revision"] = (
                connection.execute("SELECT MIN(base) FROM revision_bases").fetchone()[0]
                if table_exists(connection, "revision_bases")
                else None
            )
    verified["journal"] = log_path(dataset).is_file()
    if corpus is not None and corpus.is_file():
        with closing(sqlite3.connect(corpus)) as connection:
            verified["corpus_decisions"] = (
                connection.execute(f"SELECT COUNT(*) FROM {CORPUS_TABLE}").fetchone()[0]
                if table_exists(connection, CORPUS_TABLE)
                else 0
            )
    verified["units"] = _count_table(dataset, "units")
    verified["lines"] = _count_table(dataset, "lines")
    if corpus is not None and corpus.is_file():
        with closing(sqlite3.connect(corpus)) as connection:
            connection.row_factory = sqlite3.Row
            verified["corpus_baseline"] = (
                connection.execute(f"SELECT COUNT(*) FROM {CORPUS_BASELINE_TABLE}").fetchone()[0]
                if table_exists(connection, CORPUS_BASELINE_TABLE)
                else 0
            )
            row = (
                connection.execute(
                    f"SELECT value FROM {CORPUS_META_TABLE} WHERE key=?", (REVISION_FLOOR_KEY,)
                ).fetchone()
                if table_exists(connection, CORPUS_META_TABLE)
                else None
            )
            verified["corpus_revision_floor"] = int(row["value"]) if row else None
    return verified


def _count_table(dataset: Path, name: str) -> int:
    import pyarrow.parquet as pq

    directory = dataset / name
    single = dataset / f"{name}.parquet"
    files = (
        sorted(directory.glob("*.parquet")) if directory.is_dir() else ([single] if single.is_file() else [])
    )
    return sum(pq.ParquetFile(path).metadata.num_rows for path in files)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
