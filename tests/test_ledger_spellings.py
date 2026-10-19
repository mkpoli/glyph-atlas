"""Current crop claims become counted spellings; glosses and superseded evidence stay on the crop."""
import sqlite3
from uuid import uuid4

import pytest
from test_review_atlas import LINE, dataset  # noqa: F401
from typer.testing import CliRunner

from glyph_atlas import evidence
from glyph_atlas.cli import app
from glyph_atlas.review import characters, ledger, ledger_spellings
from glyph_atlas.review.store import Store

WORD = "ja/ばかり/副助詞"
OTHER = "ja/のみ/副助詞"


@pytest.fixture
def crops(dataset):  # noqa: F811
    store = Store(dataset)
    with store._connection() as conn:
        template = store._unit_row(conn, LINE + ':u0')

    def add(identity, spelling="斗", *, document="d", line=None, seq=None, active=True):
        unit = template.model_copy(update={"id": identity, "unicode": " ".join(f"U+{ord(c):04X}" for c in spelling),
                                           "document_id": document, "line_id": line, "seq": seq, "active": active})
        with store._connection() as conn:
            store._insert_unit(conn, unit)
        return unit

    def claim(unit, *, confidence=1, word=WORD, gloss=None, actor="ann", method="context", version=None):
        version = version or evidence.crop_version(unit.id, characters._source_digest(store, unit), unit.box)
        member = ledger.Claim(object=word if gloss is None else None, value=gloss,
                              confidence=confidence, confidence_scheme="form")
        with store._connection() as conn:
            return ledger.write_claims(conn, key=str(uuid4()), actor=actor, request={}, subject=unit.id,
                                       predicate="writes_word", claims=[member], crop_version=version,
                                       method=method, objects={WORD: "word", OTHER: "word"})

    return store, add, claim


def test_certain_probable_accepted_and_two_documents(crops):
    store, add, claim = crops
    certain = add("a")
    made = claim(certain)
    version = evidence.crop_version(certain.id, characters._source_digest(store, certain), certain.box)
    with store._connection() as conn:
        ledger.act(conn, key=str(uuid4()), actor="bob", request={}, target=made["assertions"][0],
                   action="accept", version_of=lambda _: version)
    claim(add("b", document="second"), confidence=0.5, method="ruby")
    assert ledger_spellings.count([store.directory]) == [{
        "word": WORD, "spelling": "斗", "code_points": "U+6597", "documents": 2, "occurrences": 2,
        "crops": 2, "probable": 1, "methods": "ruby context", "examples": "a b"}]


@pytest.mark.parametrize("excluded", ["gloss", "rejected", "disputed", "old", "inactive"])
def test_claims_that_do_not_count(crops, excluded):
    store, add, claim = crops
    unit = add("a", active=excluded != "inactive")
    made = claim(unit, gloss={"gloss": "a reading"} if excluded == "gloss" else None,
                 version="a@old@1,2,3,4" if excluded == "old" else None)
    if excluded == "disputed":
        claim(unit, word=OTHER, actor="bob")
    if excluded == "rejected":
        version = evidence.crop_version(unit.id, characters._source_digest(store, unit), unit.box)
        with store._connection() as conn:
            ledger.act(conn, key=str(uuid4()), actor="bob", request={}, target=made["assertions"][0],
                       action="reject", version_of=lambda _: version)
    assert ledger_spellings.count([store.directory]) == []


def test_consecutive_crops_write_one_word_and_any_probable_crop_makes_it_probable(crops):
    store, add, claim = crops
    claim(add("a", "而", line=LINE, seq=0))
    claim(add("b", "已", line=LINE, seq=1), confidence=0.5)
    assert ledger_spellings.count([store.directory]) == [{
        "word": WORD, "spelling": "而已", "code_points": "U+800C U+5DF2", "documents": 1, "occurrences": 1,
        "crops": 2, "probable": 1, "methods": "context", "examples": "a b"}]


def test_support_is_read_by_id_and_any_certain_support_keeps_a_crop_certain(crops):
    store, add, claim = crops
    unit = add("a", "𠮷\ufe00")
    claim(unit, confidence=0.5, method="context")
    claim(unit, actor="bob", method="ruby")
    row, = ledger_spellings.count([store.directory])
    assert (row["spelling"], row["code_points"], row["probable"], row["methods"]) == (
        "𠮷\ufe00", "U+20BB7 U+FE00", 0, "ruby context")


def test_gaps_lines_words_and_missing_positions_do_not_join(crops):
    store, add, claim = crops
    claim(add("a", line=LINE, seq=0))
    claim(add("b", line=LINE, seq=2))
    claim(add("c", line=LINE, seq=3), word=OTHER)
    claim(add("d", line="other", seq=4))
    claim(add("e", line="other"))
    rows = {row["word"]: row for row in ledger_spellings.count([store.directory])}
    assert rows[WORD]["occurrences"] == 4
    assert rows[OTHER]["occurrences"] == 1
    assert all(row["spelling"] == "斗" for row in rows.values())


def test_examples_are_sorted_limited_and_cli_is_readonly(crops, tmp_path):
    store, add, claim = crops
    for identity in reversed("abcdef"):
        claim(add(identity))
    before = store.path.read_bytes()
    out = tmp_path / "spellings.tsv"
    result = CliRunner().invoke(app, ["review", "ledger-spellings", str(store.directory), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert store.path.read_bytes() == before
    rows = ledger_spellings.count([store.directory, store.directory])
    assert rows[0]["examples"] == "a b c d e"
    assert rows[0]["occurrences"] == 6
    content = "# written: 2000-01-01\n" + out.read_text().split("\n", 1)[1]
    out.write_text(content)
    ledger_spellings.write(rows, out)
    assert out.read_text() == content
    with (Store(store.directory, readonly=True)._connection() as conn,
          pytest.raises(sqlite3.OperationalError, match="readonly")):
        conn.execute("DELETE FROM current_claims")


def test_missing_store_is_not_created(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        ledger_spellings.count([tmp_path])
    assert not (tmp_path / "review.sqlite").exists()


def test_a_repeated_word_is_two_occurrences_and_a_partial_spelling_stays_one_crop(crops):
    store, add, claim = crops
    claim(add("a", line=LINE, seq=0))
    claim(add("b", line=LINE, seq=1))
    claim(add("c", "而", line="other", seq=0))
    rows = {row["spelling"]: row for row in ledger_spellings.count([store.directory])}
    assert (rows["斗"]["occurrences"], rows["斗"]["crops"]) == (2, 2)
    assert (rows["而"]["occurrences"], rows["而"]["crops"]) == (1, 1)
    assert "斗斗" not in rows


@pytest.mark.parametrize("disputed", [False, True])
def test_an_adjudicated_slot_counts_its_word(crops, disputed):
    store, add, claim = crops
    unit = add("a")
    made = claim(unit)
    if disputed:
        claim(unit, word=OTHER, actor="bob")
    version = evidence.crop_version(unit.id, characters._source_digest(store, unit), unit.box)
    with store._connection() as conn:
        ledger.act(conn, key=str(uuid4()), actor="dan", request={}, target=made["assertions"][0],
                   action="adjudicate", adjudicator=True, version_of=lambda _: version)
    row, = ledger_spellings.count([store.directory])
    assert (row["word"], row["occurrences"]) == (WORD, 1)
