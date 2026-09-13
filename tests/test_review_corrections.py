"""Tests for `kuzushiji_atlas.review.corrections`, the page-transcription correction layer.

The store's `ReviewRequest` accepts `target_type: page`, and before this module that was the whole
story: an event was written, acknowledged, and changed nothing, because the store's change dispatcher
returns a no-op for any target that is not a unit or a line and `page_texts` is not part of its mutable
projection. A page editor wired to `POST /reviews` would therefore have looked like it worked.

So the questions these tests answer are the ones that decide whether a correction is real:

* does it survive the store being reopened, applied to the tables and rebuilt from the log?
* does a reimport of the transcription leave it intact, and does text that moved underneath it become
  a visible conflict rather than a silent mis-application?
* are the source's placement rules enforced here, so a correction that this project accepts is one the
  publishing project's build will accept too?
* does an undo actually undo, in the journal and in the effective text?
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kuzushiji_atlas import ainu_source, tables
from kuzushiji_atlas.review import corrections as corrections_module
from kuzushiji_atlas.review.corrections import Correction, CorrectionError
from kuzushiji_atlas.review.store import Store
from kuzushiji_atlas.schema import Document, Line, Page, PageText

#: A page whose transcription has the source's shape: a 右丁 marker that its parser skips, a blank
#: line, and four transcription lines, the third of which holds the text to correct.
TEXT = (
    "【右丁】\n\n蝦夷紀行巻之上\n"
    "文化五辰年の秋再ひ間宮林蔵をして北蝦夷の奥地に\n"
    "至らしむるに其年の七月十三日本蝦夷ソウヤを出\n"
    "帆して其日シラヌシに至る此所土着の住夷多\n"
)
#: What the reviewer reads on the scan at line 3.
ORIGINAL = "ソウヤ"
CORRECTED = "ソウヤ湾"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    directory = tmp_path / "ainu"
    directory.mkdir()
    document = Document(id="hk:d1", title="蝦夷紀行")
    page = Page(id="hk:d1:0", document_id="hk:d1", seq=0, canvas="c", image="i",
                width=1000, height=800)
    lines = [
        Line(id="hk:d1:0:L0", page_id="hk:d1:0", seq=0, box=None, text_raw="蝦夷紀行巻之上",
             text="蝦夷紀行巻之上"),
        Line(id="hk:d1:0:L1", page_id="hk:d1:0", seq=1, box=None, text_raw="至らしむるに其年の七月",
             text="至らしむるに其年の七月"),
    ]
    tables.write(directory / "documents.parquet", [document], Document)
    tables.write(directory / "pages.parquet", [page], Page)
    tables.write(directory / "lines.parquet", lines, Line)
    tables.write(directory / "page_texts.parquet",
                 [PageText(page_id=page.id, source="ainu-records", text_raw=TEXT)], PageText)
    return directory


def correction(**overrides: object) -> Correction:
    fields: dict[str, object] = {
        "id": "ezo-kiko-ryukoku-souya", "page_id": "hk:d1:0", "line": 3,
        "original": ORIGINAL, "corrected": CORRECTED, "note": "原画像の左丁3行目を確認。",
    }
    fields.update(overrides)
    return Correction(**fields)  # type: ignore[arg-type]


def test_a_correction_is_recorded_and_read_back(dataset: Path) -> None:
    """Save, then read: the record is in the journal and the effective text changes."""
    store = Store(dataset)
    assert store.page_text("hk:d1:0", ) == TEXT, "the imported text is what the store serves"
    corrections_module.record(store, correction(), client_id="r1")

    page = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    assert [item.correction.id for item in page.judgements] == ["ezo-kiko-ryukoku-souya"]
    assert page.applied and not page.conflicted
    assert CORRECTED in page.text()
    assert store.page_text("hk:d1:0") == TEXT, "the source text itself is never rewritten"


def test_a_correction_survives_reopening_applying_and_replaying(dataset: Path) -> None:
    """Reopen, `apply`, `replay`: the correction is still there and still means the same thing.

    This is the whole point of recording it as a journal event. `apply` writes the reviewed state into
    the tables and `replay` rebuilds the store from those tables plus the log, so a correction that
    lived only in a Python object would be gone at the first reopen.
    """
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    store.close() if hasattr(store, "close") else None

    reopened = Store(dataset)
    page = corrections_module.page_corrections(reopened, "hk:d1:0", reopened.page_text("hk:d1:0") or "")
    assert [item.correction.id for item in page.judgements] == ["ezo-kiko-ryukoku-souya"]
    assert CORRECTED in page.text()

    from kuzushiji_atlas.review import store as store_module

    store_module.apply(dataset)
    store_module.replay(dataset)
    rebuilt = Store(dataset)
    page = corrections_module.page_corrections(rebuilt, "hk:d1:0", rebuilt.page_text("hk:d1:0") or "")
    assert [item.correction.id for item in page.judgements] == ["ezo-kiko-ryukoku-souya"]
    assert rebuilt.page_text("hk:d1:0") == TEXT, "apply does not rewrite the transcription"
    assert CORRECTED in page.text()


def test_a_reimport_that_moves_the_text_makes_the_correction_conflicted(dataset: Path) -> None:
    """A transcription that changed underneath a correction is reported, not silently re-aimed."""
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")

    moved = TEXT.replace(ORIGINAL, "別の湊")
    tables.write(dataset / "page_texts.parquet",
                 [PageText(page_id="hk:d1:0", source="ainu-records", text_raw=moved)], PageText)
    after = Store(dataset, rebuilding=True)
    page = corrections_module.page_corrections(after, "hk:d1:0", moved)
    assert page.conflicted and not page.applied
    assert "no longer contains" in (page.conflicted[0].reason or "")
    assert page.text() == "\n".join(ainu_source.transcription_lines(moved)), "nothing is applied"


def test_the_text_is_left_alone_when_the_correction_cannot_be_placed(dataset: Path) -> None:
    """A correction against a line that does not exist is refused at record time."""
    store = Store(dataset)
    with pytest.raises(CorrectionError, match="does not exist"):
        corrections_module.record(store, correction(line=99), client_id="r1")
    with pytest.raises(CorrectionError, match="contains"):
        corrections_module.record(store, correction(original="ナイ"), client_id="r1")
    duplicate = TEXT.replace("七月十三日", f"七月十三日{ORIGINAL}")
    tables.write(dataset / "page_texts.parquet",
                 [PageText(page_id="hk:d1:0", source="ainu-records", text_raw=duplicate)], PageText)
    with pytest.raises(CorrectionError, match="appears 2 times"):
        corrections_module.record(Store(dataset), correction(), client_id="r1")


def test_a_correction_cannot_add_or_remove_lines(dataset: Path) -> None:
    """The source places corrections by line, so a newline would move every later one."""
    store = Store(dataset)
    with pytest.raises(CorrectionError, match="cannot add or remove lines"):
        corrections_module.record(store, correction(corrected="ソウヤ\n湾"), client_id="r1")


def test_a_duplicate_id_is_kept_once_and_a_retraction_removes_it(dataset: Path) -> None:
    """An undo is a second journal event, and the effective text follows it."""
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    corrections_module.record(store, correction(note="second thought"), client_id="r1")
    page = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    assert len(page.judgements) == 1, "the id identifies the correction, so the later record replaces"

    corrections_module.retract(store, "hk:d1:0", "ezo-kiko-ryukoku-souya", reason="読めなかった")
    after = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    assert after.judgements == [], "a retracted correction is not part of the page"
    assert after.text() == "\n".join(ainu_source.transcription_lines(TEXT))
    events = [event.field for event in store.events() if event.target_type == "page"]
    assert events == ["correction", "correction", "correction-retracted"], "the journal keeps both"


def test_a_stale_revision_is_refused(dataset: Path) -> None:
    """A client that edited from an older revision is told, rather than overwriting."""
    from kuzushiji_atlas.review.store import Conflict

    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    with pytest.raises(Conflict):
        corrections_module.record(store, correction(note="from a stale tab"), client_id="r2",
                                  base_revision=0)


def test_the_diff_shows_one_line_changing(dataset: Path) -> None:
    """A reviewer approves a change, so the change has to be readable as one."""
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    page = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    lines = page.diffs()[0]
    assert any(line.startswith("-") and ORIGINAL in line for line in lines)
    assert any(line.startswith("+") and CORRECTED in line for line in lines)
    assert lines[0].startswith("---") and lines[1].startswith("+++")


def test_proposals_render_the_sources_format(dataset: Path) -> None:
    """The bridge turns a correction into the record the publishing project reads."""
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    page = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    proposals = corrections_module.to_proposals(page, unit="ezo-kiko/ryukoku", page_number=16)
    assert len(proposals) == 1
    record = proposals[0].record()
    assert record["id"] == "ezo-kiko-ryukoku-souya" and record["line"] == 3
    assert "unit" not in record and "page" not in record, "the path supplies both"
    assert proposals[0].path() == Path("data/editorial/corrections/ezo-kiko/ryukoku/p16.json")


def test_a_conflicted_correction_becomes_an_unmappable_proposal(dataset: Path) -> None:
    """Unplaceable feedback is surfaced for a person, not dropped and not guessed at."""
    store = Store(dataset)
    corrections_module.record(store, correction(), client_id="r1")
    moved = TEXT.replace(ORIGINAL, "別の語")
    page = corrections_module.page_corrections(store, "hk:d1:0", moved)
    proposals = corrections_module.to_proposals(page, unit="ezo-kiko/ryukoku", page_number=16)
    assert proposals[0].unmappable and "no longer contains" in proposals[0].unmappable
    with pytest.raises(ainu_source.AinuSourceError):
        proposals[0].record()


def test_the_json_is_the_sources_array_shape(dataset: Path) -> None:
    """The file the source holds is a JSON array of records, so that is what is rendered."""
    rendered = corrections_module.as_json([correction()])
    loaded = json.loads(rendered)
    assert isinstance(loaded, list) and len(loaded) == 1
    assert loaded[0]["id"] == "ezo-kiko-ryukoku-souya" and loaded[0]["line"] == 3
    assert set(loaded[0]) == {"id", "line", "original", "corrected", "note"}


def test_a_note_and_a_timing_event_do_not_touch_the_corrections(dataset: Path) -> None:
    """Context events stay out of the editorial layer, which the status module also insists on."""
    from kuzushiji_atlas.review.store import ReviewRequest

    store = Store(dataset)
    store.record(ReviewRequest(target_type="page", target_id="hk:d1:0", field="note",
                              new="read the whole page", client_id="r1"))
    store.record(ReviewRequest(target_type="page", target_id="hk:d1:0", field="timing",
                              new={"ms": 42000}, client_id="r1"))
    page = corrections_module.page_corrections(store, "hk:d1:0", store.page_text("hk:d1:0") or "")
    assert page.judgements == [] and page.text() == "\n".join(ainu_source.transcription_lines(TEXT))
