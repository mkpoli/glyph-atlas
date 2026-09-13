"""Tests of what an alignment may drop when it writes, and of the lock that guards the write.

The bug these cover: two alignments ran at once over `work/honkoku-lines`, each read the units table,
each wrote it back with its own units added, and the later write removed the other run's units — 2,920
units of the calibration pages that had been measured and packaged. The merge rule keeps this run's
units off the pages it is not writing, and the lock makes the read and the write one step, so a second
writer queues behind the first instead of overwriting it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kuzushiji_atlas import align, tables
from kuzushiji_atlas.schema import Box, Line, Page, Unit, UnitKind

ROOT = Path(__file__).resolve().parents[1]


def unit(page_id: str, seq: int, *, run: str, review: str = "rejected") -> Unit:
    """A unit as an alignment writes it: the run's fingerprint sits inside the id."""
    return Unit(
        id=f"{page_id}:L0:{run}:{seq}",
        page_id=page_id,
        document_id="doc",
        box=Box(x=seq * 10, y=0, w=8, h=8),
        text=chr(0x4E00 + seq),
        kind=UnitKind.CHAR,
        method="detect-align",
        review=review,
    )


def write(directory: Path, units: list[Unit], run: str, *, pages: set[str] | None = None) -> None:
    align._write_units(
        directory, units, [], align.Run(name=run), run, replace=True,
        pages=pages or {item.page_id for item in units if item.page_id},
    )


def test_a_second_run_over_other_pages_keeps_the_units_already_there(tmp_path: Path) -> None:
    """A run over one group of pages must not clear the units of the pages outside the group."""
    first = "aaaa11112222"
    second = "bbbb33334444"
    write(tmp_path, [unit("hl:one:0", 0, run=first), unit("hl:one:0", 1, run=first)], first)
    write(tmp_path, [unit("hl:two:0", 0, run=second)], second)

    stored = {item.id for item in tables.read(tmp_path / "units.parquet", Unit)}
    assert stored == {"hl:one:0:L0:aaaa11112222:0", "hl:one:0:L0:aaaa11112222:1",
                      "hl:two:0:L0:bbbb33334444:0"}


def test_a_rerun_over_the_same_pages_replaces_only_its_own_units(tmp_path: Path) -> None:
    """Rerunning one page twice must not double its units, and must not touch another run's."""
    run = "aaaa11112222"
    write(tmp_path, [unit("hl:one:0", 0, run=run)], run)
    write(tmp_path, [unit("hl:one:0", 0, run=run), unit("hl:one:0", 1, run=run)], run)
    write(tmp_path, [unit("hl:two:0", 0, run="cccc55556666")], "cccc55556666")

    stored = [item.id for item in tables.read(tmp_path / "units.parquet", Unit)]
    assert sorted(stored) == ["hl:one:0:L0:aaaa11112222:0", "hl:one:0:L0:aaaa11112222:1",
                              "hl:two:0:L0:cccc55556666:0"]


def test_a_reviewed_unit_survives_the_run_that_wrote_it(tmp_path: Path) -> None:
    """An alignment is not allowed to throw away a person's work on its own pages."""
    run = "aaaa11112222"
    write(tmp_path, [unit("hl:one:0", 0, run=run, review="reviewed")], run)
    write(tmp_path, [unit("hl:one:0", 1, run=run)], run)

    stored = {item.id for item in tables.read(tmp_path / "units.parquet", Unit)}
    assert stored == {"hl:one:0:L0:aaaa11112222:0", "hl:one:0:L0:aaaa11112222:1"}


def test_the_lock_is_exclusive_and_is_released(tmp_path: Path) -> None:
    """One holder at a time, and a released lock can be taken again."""
    target = tmp_path / "units.parquet"
    with tables.locked(target), pytest.raises(TimeoutError), tables.locked(
        target, poll=0.01, timeout=0.2
    ):
        pass
    with tables.locked(target, poll=0.01, timeout=1.0):
        pass


def test_the_lock_crosses_processes(tmp_path: Path) -> None:
    """A lock held here stops a second process, which is how the lost units happened."""
    target = tmp_path / "units.parquet"
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "from kuzushiji_atlas import tables\n"
        "with tables.locked(Path(sys.argv[1]), poll=0.01, timeout=0.2):\n"
        "    pass\n"
    )
    with tables.locked(target):
        result = subprocess.run(
            [sys.executable, "-c", program, str(target)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    assert result.returncode != 0
    assert "TimeoutError" in result.stderr


def test_a_write_takes_the_lock_and_writes(tmp_path: Path) -> None:
    """`tables.write` remains the plain way to write, lock and all."""
    rows = [unit("hl:one:0", 0, run="aaaa11112222")]
    assert tables.write(tmp_path / "units.parquet", rows, Unit) == 1
    assert tables.locked(tmp_path / "units.parquet").lock_path.name == ".units.parquet.lock"
    assert len(tables.read(tmp_path / "units.parquet", Unit)) == 1


def test_an_empty_page_selection_aligns_nothing(tmp_path: Path) -> None:
    """`pages=[]` selects no page: the `--limit 0` case must not run the whole dataset."""
    page = Page(id="hl:one:0", document_id="doc", seq=0, canvas="c", image="i", width=10, height=10)
    lines = [
        Line(id="hl:one:0:L0", page_id="hl:one:0", seq=0, box=Box(x=0, y=0, w=10, h=10),
             text_raw="あ", text="あ"),
    ]
    tables.write(tmp_path / "pages.parquet", [page], Page)
    tables.write(tmp_path / "lines.parquet", lines, Line)

    counts = align.run_directory(tmp_path, align.Run(name="empty"), pages=[])
    assert counts["pages"] == 0 and counts["lines"] == 0 and counts["units"] == 0
    assert not (tmp_path / "units.parquet").exists(), "nothing is detected and nothing is written"


def test_the_run_carries_the_detectors_operating_point(tmp_path: Path, monkeypatch) -> None:
    """A run detects at its own score, not at whatever the Detector's default happens to be.

    The aligner built its detector with no score, so it used 0.3 while the derivation had found the
    boxes at 0.02: on the Ainu records' cursive columns every detection sits near 0.02, so the aligner
    found nothing and wrote boxless units. The score is part of the run now, and the fingerprint
    leaves it out because the pilot's units were measured at 0.02 before it was written down.
    """
    seen: dict[str, float] = {}
    real = align_module_detector()

    def spy(onnx, **options):
        seen.update(options)
        return real(onnx, **options)

    monkeypatch.setattr("kuzushiji_atlas.detect.Detector", spy)
    page = Page(id="hl:one:0", document_id="doc", seq=0, canvas="c", image="i", width=10, height=10)
    tables.write(tmp_path / "pages.parquet", [page], Page)
    tables.write(tmp_path / "lines.parquet", [line(0)], Line)

    run = align.Run(name="scored", score=0.02)
    align.run_directory(tmp_path, run)
    assert seen.get("score") == 0.02, "the aligner's detector takes the run's operating point"
    assert align.Run(name="scored").fingerprint() == align.Run(name="other", score=0.02).fingerprint(), (
        "the name and the operating point are not part of the id"
    )


def line(seq: int) -> Line:
    """One transcribed line with a box, which is what the runner needs to align anything."""
    return Line(id=f"hl:one:0:L{seq}", page_id="hl:one:0", seq=seq, box=Box(x=0, y=0, w=10, h=10),
                text_raw="あ", text="あ")


def align_module_detector():
    """The real `Detector` class, which the spy replaces inside the module."""
    from kuzushiji_atlas.detect import Detector

    return Detector


def test_a_run_carries_the_detectors_suppression(tmp_path: Path, monkeypatch) -> None:
    """A run states the detector's `nms`, so the boxes it aligns are the boxes it names.

    The suppression decides which boxes survive a tile border, and the alignment only ever sees those.
    A run that did not state it took the constructor's 0.5 whatever the measured value was, which is
    how the pilot's units were produced under one suppression while `models/detector/config.yaml`
    recorded another.
    """
    seen: dict[str, float] = {}
    from kuzushiji_atlas.detect import Detector as RealDetector

    def spy(onnx, **options):
        seen.update(options)
        return RealDetector(onnx, **options)

    monkeypatch.setattr("kuzushiji_atlas.detect.Detector", spy)
    page = Page(id="hl:one:0", document_id="doc", seq=0, canvas="c", image="i", width=10, height=10)
    tables.write(tmp_path / "pages.parquet", [page], Page)
    tables.write(tmp_path / "lines.parquet", [line(0)], Line)

    align.run_directory(tmp_path, align.Run(name="suppressed", score=0.02, nms=0.2))
    assert seen.get("score") == 0.02 and seen.get("nms") == 0.2, (
        "both the operating point and the suppression reach the detector"
    )


def test_the_pilot_configuration_states_both_numbers() -> None:
    """The run the pilot is measured with names the operating point and the suppression."""
    run = align.load_run(ROOT / "models" / "align" / "runs" / "pilot-v1.yaml")
    assert run.score == 0.02, "the measured operating point of the detector"
    assert run.nms == 0.2, "the val-chosen suppression from models/detector/nms-sweep.json"
