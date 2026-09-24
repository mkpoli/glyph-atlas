"""Tests of the audit sample and the precision report."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from glyph_atlas import audit, tables
from glyph_atlas.schema import (
    Box,
    Classification,
    Confidence,
    Document,
    Review,
    ReviewState,
    Script,
    Unit,
)


def unit(
    index: int,
    *,
    box: Box | None = None,
    review: ReviewState = ReviewState.MACHINE,
    script: Script = Script.HAN,
    unicode: str | None = "U+4E00",
    document: str = "d1",
    page: str = "p1",
    run: str = "run-a",
    active: bool = True,
) -> Unit:
    return Unit(
        id=f"{document}:{page}:u{index}",
        document_id=document,
        page_id=page,
        box=box or Box(x=index * 10, y=0, w=10, h=10),
        text_source="一",
        reading="いち",
        unicode=unicode,
        classification=Classification.IDENTIFIED,
        script=script,
        method="detect-align",
        review=review,
        active=active,
        confidence=Confidence(detection=0.9, text=0.8, model="test"),
        upstream={"source": "detect-align", "run": run},
    )


def dataset(directory: Path, units: list[Unit]) -> Path:
    tables.write(directory / "documents.parquet", [Document(id="d1", title="t")], Document)
    tables.write(directory / "units.parquet", units, Unit)
    return directory


def test_the_sample_is_reproducible_from_its_seed(tmp_path):
    directory = dataset(tmp_path, [unit(i) for i in range(50)])
    first = audit.sample(directory, n=10, seed=7)
    assert first["drawn"] == 10 and first["population"] == 50
    stored = audit.load(directory, "all-s7-n10")
    ids = [row.id for row in stored.units]
    assert ids == sorted(ids)
    # The same seed over the same population selects the same units.
    second = audit.sample(directory, n=10, seed=7, replace=True)
    assert second["drawn"] == 10
    assert [row.id for row in audit.load(directory, "all-s7-n10").units] == ids
    audit.sample(directory, n=10, seed=8, sample_id="other")
    assert [row.id for row in audit.load(directory, "other").units] != ids


def test_the_inclusion_probabilities_sum_to_the_expected_sample_size(tmp_path):
    units = [unit(i, document="d1") for i in range(30)] + [unit(i, document="d2") for i in range(10)]
    directory = dataset(tmp_path, units)
    audit.sample(directory, n=8, strata=("document",), seed=1)
    record = audit.load(directory, "all-s1-n8")
    assert {row.stratum for row in record.units} == {"d1", "d2"}
    # Each stratum keeps the share the population gives it: 30 of 40 units, so 6 of 8.
    assert sum(1 for row in record.units if row.stratum == "d1") == 6
    assert sum(1 for row in record.units if row.stratum == "d2") == 2
    # The sum of the inclusion probabilities over the whole frame is the expected sample size, and
    # it is the sample size itself only when the design is unstratified: 30 units at 0.2 plus 10 at
    # 0.2 is an expected 8 of 40.
    first = record.units[0]
    assert first.frame * first.p + (10 * 0.2 if first.frame == 30 else 30 * 0.2) == pytest.approx(8.0)
    for row in record.units:
        assert row.p == pytest.approx(row.n_sample / row.frame)
        assert row.n_stratum <= row.frame


def test_only_accepted_units_are_in_the_population(tmp_path):
    units = [unit(i) for i in range(6)]
    units[0].review = ReviewState.TRANSCRIBER
    units[1].review = ReviewState.REVIEWED
    units[2].review = ReviewState.REJECTED
    units[3].active = False
    units[4].upstream = {"source": "detect-align", "run": "other-run"}
    directory = dataset(tmp_path, units)
    counts = audit.sample(directory, n=10, run="run-a", seed=0)
    # u5 keeps every condition; u0 to u4 fail one each: transcribed, reviewed, rejected, retired,
    # and belonging to another run.
    assert counts["population"] == 1
    assert {row.id for row in audit.load(directory, "run-a-s0-n10").units} == {"d1:p1:u5"}
    assert counts["drawn"] == 1


def test_a_unit_already_audited_is_left_out_of_a_second_sample(tmp_path):
    directory = dataset(tmp_path, [unit(i) for i in range(20)])
    audit.sample(directory, n=5, seed=0)
    first = {row.id for row in audit.load(directory, "all-s0-n5").units}
    audit.sample(directory, n=5, seed=0, sample_id="second")
    second = {row.id for row in audit.load(directory, "second").units}
    assert not (first & second)
    assert audit.samples(directory) == ["all-s0-n5", "second"]


def test_an_empty_population_draws_nothing(tmp_path):
    directory = dataset(tmp_path, [unit(i, review=ReviewState.TRANSCRIBER) for i in range(4)])
    counts = audit.sample(directory, n=5, seed=0)
    assert counts == {"population": 0, "population_pages": 0, "strata": 0, "drawn": 0}
    record = audit.load(directory, "all-s0-n5")
    assert record.units == []


def test_sampling_refuses_an_unknown_stratum_and_a_bad_run(tmp_path):
    directory = dataset(tmp_path, [unit(0)])
    with pytest.raises(audit.AuditError):
        audit.sample(directory, n=1, strata=("page",))
    with pytest.raises(audit.AuditError):
        audit.sample(directory, n=1, run="a:b")
    with pytest.raises(audit.AuditError):
        audit.sample(directory, n=0)


def test_the_sample_tags_the_units_and_records_what_is_hidden(tmp_path):
    directory = dataset(tmp_path, [unit(i) for i in range(6)])
    audit.sample(directory, n=3, seed=0)
    record = audit.load(directory, "all-s0-n5".replace("n5", "n3"))
    stored = tables.read(directory / "units.parquet", Unit)
    tagged = [row for row in stored if row.meta.get("audit")]
    assert len(tagged) == 3
    for row in tagged:
        assert row.meta["audit"]["sample"] == "all-s0-n3"
        assert row.meta["audit"]["p"] > 0
        assert row.meta["audit"]["hidden"] == list(audit.HIDDEN_FIELDS)
    assert record.population == 6
    # A dataset directory that carries MANIFEST.json records the sample in it; this fixture is a
    # bare pair of tables, so the sample is only the file under audit/.
    assert not (directory / tables.MANIFEST_NAME).exists()


def test_wilson_intervals_match_known_values():
    assert audit.intervals([0], [0]) == (0.0, 1.0)
    low, high = audit.intervals([50], [100])
    assert low == pytest.approx(0.4038, abs=1e-4)
    assert high == pytest.approx(0.5962, abs=1e-4)
    low, high = audit.intervals([10], [10])
    assert low == pytest.approx(0.7225, abs=1e-4) and high == 1.0


def test_the_report_scores_box_label_and_joint(tmp_path):
    units = [unit(i) for i in range(4)]
    directory = dataset(tmp_path, units)
    audit.sample(directory, n=4, seed=0)
    record = audit.load(directory, "all-s0-n4")
    reviewed_at = datetime(2026, 9, 11, tzinfo=UTC)
    events = [
        # u0 keeps its box and label: right on both.
        Review(id="r1", target_id="d1:p1:u0", field="reading", old="いち", new="いち",
               role="reviewer", at=reviewed_at),
        # u1 is moved a little: the box still overlaps at IoU 0.5 or more, the label stands.
        Review(id="r2", target_id="d1:p1:u1", field="box", old=None,
               new={"x": 12, "y": 0, "w": 10, "h": 10}, role="reviewer", at=reviewed_at),
        # u2 is moved far away and relabelled: wrong on both.
        Review(id="r3", target_id="d1:p1:u2", field="box", old=None,
               new={"x": 900, "y": 0, "w": 10, "h": 10}, role="reviewer", at=reviewed_at),
        Review(id="r4", target_id="d1:p1:u2", field="unicode", old="U+4E00", new="U+4E8C",
               role="reviewer", at=reviewed_at),
        # u3 keeps its box and is relabelled: box right, label wrong.
        Review(id="r5", target_id="d1:p1:u3", field="unicode", old="U+4E00", new="U+4E8C",
               role="reviewer", at=reviewed_at),
    ]
    (directory / "reviews.jsonl").write_text(
        "".join(event.model_dump_json() + "\n" for event in events), encoding="utf-8"
    )
    markdown = audit.report(directory, sample=record.id)
    # u0 and u1 keep their box and label, u2 is moved away and relabelled, u3 keeps its box and is
    # relabelled: three boxes of four hold, two labels of four agree, two units are right on both.
    assert "| box precision | 0.7500 |" in markdown
    assert "| label precision | 0.5000 |" in markdown
    assert "| joint precision | 0.5000 |" in markdown
    assert "Reviewed 4 of 4 sampled units" in markdown
    assert "cluster bootstrap" in markdown
    assert "not a training set" in markdown


def test_a_retired_unit_counts_as_wrong_in_the_report(tmp_path):
    units = [unit(i) for i in range(3)]
    directory = dataset(tmp_path, units)
    audit.sample(directory, n=3, seed=0)
    record = audit.load(directory, "all-s0-n3")
    stored = tables.read(directory / "units.parquet", Unit)
    for row in stored:
        if row.id.endswith("u0"):
            row.active = False
    tables.write(directory / "units.parquet", stored, Unit)
    markdown = audit.report(directory, sample=record.id)
    assert "1 retired" in markdown
    assert "| box precision | 0.6667 |" in markdown


def test_the_report_names_a_sample_that_does_not_exist(tmp_path):
    directory = dataset(tmp_path, [unit(0)])
    with pytest.raises(audit.AuditError) as error:
        audit.report(directory, sample="nope")
    assert "no sample nope" in str(error.value)


def test_the_report_can_carry_a_notice_that_says_it_is_not_a_measurement(tmp_path):
    directory = dataset(tmp_path, [unit(i) for i in range(3)])
    audit.sample(directory, n=3, seed=0)
    notice = "**Not a measurement.** The boxes came from a stub detector."
    markdown = audit.report(directory, sample="all-s0-n3", notice=notice)
    assert markdown.splitlines()[2].startswith("> **Not a measurement.**")
    # A notice may also be a file, which is how a long caveat travels with the command.
    path = tmp_path / "notice.md"
    path.write_text("Nothing here is a measurement.\n", encoding="utf-8")
    markdown = audit.report(directory, sample="all-s0-n3", notice=str(path))
    assert "Nothing here is a measurement." in markdown
