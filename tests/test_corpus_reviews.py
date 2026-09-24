"""Corpus review persistence, provenance, and corrected search over a synthetic corpus."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glyph_atlas import tables
from glyph_atlas.corpus import details
from glyph_atlas.corpus.api import CorpusAPI
from glyph_atlas.review.corpus_reviews import CorpusReviews
from glyph_atlas.review.corpus_reviews import router as review_router
from glyph_atlas.schema import Box, Document, Licence, Line, Page, Rights, Unit

DOC = "codh:200057428"          # a CODH-shaped document id, as the real corpus registers it
PAGE = f"{DOC}:0001"
LINE = f"{PAGE}:1"
UNIT = f"{LINE}:ア"
ALT_UNIT = f"{LINE}:イ"
NE, NU, TOMO = "ネ", "ヌ", "𪜈"
NE_POINT, TOMO_POINT = "U+30CD", "U+2A708"


def chars_table(path: Path, rows: list[dict]) -> None:
    """A minimal cached character summary: what the counts route reads and the overlay adjusts."""
    columns = ["char", "codepoint", "name", "block", "known", "renderable", "n_occurrences",
               "n_literal", "n_annotated", "n_located", "n_line_hits", "n_page_hits", "n_units",
               "n_glyph_rects", "n_glyphs", "n_documents", "n_pages_approx", "corpora"]
    filled = [{key: row.get(key) for key in columns} for row in rows]
    pq.write_table(pa.Table.from_pylist(filled), path)


def write_units(directory: Path, rows: list[Unit]) -> None:
    """Rewrite the unit table, which is what a later import or repair does."""
    tables.write(directory / "units.parquet", rows, Unit)


def units_of(box: Box | None = None, encoded: str = NE_POINT) -> list[Unit]:
    """The corpus's own rows. The written form is the code point, so a repair changes that."""
    return [
        # `text_source` is the corpus's own transcription (here the reading), the code point is what
        # was printed: the two differ on purpose, as they do on 340 units of the real corpus.
        Unit(id=UNIT, document_id=DOC, page_id=PAGE, line_id=LINE, seq=0,
             box=box or Box(x=40, y=30, w=24, h=30), unicode=encoded, text_source="ね",
             reading="ね", script="katakana", method="import"),
        Unit(id=ALT_UNIT, document_id=DOC, page_id=PAGE, line_id=LINE, seq=1,
             box=Box(x=80, y=30, w=24, h=30), unicode=TOMO_POINT, text_source=TOMO,
             reading="とも", script="han", method="import"),
    ]


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch):
    """A synthetic corpus root with an index, and the API over it."""
    root = tmp_path / "work"
    index = root / "corpus-index"
    source = root / "codh-full"
    index.mkdir(parents=True)
    source.mkdir(parents=True)
    rights = Rights(licence=Licence.PDM, attribution="Synthetic fixture", holder="Fixture Holder")
    tables.write(source / "documents.parquet", [Document(
        id=DOC, title="仮名資料", holder="Fixture Holder", shelfmark="F-1",
        image_rights=rights, text_rights=rights)], Document)
    tables.write(source / "pages.parquet", [Page(
        id=PAGE, document_id=DOC, seq=0, image="https://example.org/iiif/bk1/0001",
        canvas="https://example.org/iiif/canvas/1", width=1000, height=1000, sha256="e" * 64)], Page)
    tables.write(source / "lines.parquet", [Line(
        id=LINE, page_id=PAGE, seq=1, text_raw="ネ𪜈", text="ネ𪜈", box=Box(x=10, y=20, w=200, h=60))], Line)
    write_units(source, units_of())
    chars_table(index / "chars.parquet", [
        {"char": NE, "codepoint": NE_POINT, "name": "KATAKANA LETTER NE", "block": "Katakana",
         "known": True, "renderable": True, "n_occurrences": 1, "n_literal": 1, "n_annotated": 0,
         "n_located": 1, "n_line_hits": 0, "n_page_hits": 0, "n_units": 1, "n_glyph_rects": 0,
         "n_glyphs": 1, "n_documents": 1, "n_pages_approx": 1, "corpora": "codh-full"},
        {"char": TOMO, "codepoint": TOMO_POINT, "name": "CJK UNIFIED IDEOGRAPH-2A708",
         "block": "CJK Unified Ideographs Extension C", "known": True, "renderable": True,
         "n_occurrences": 1, "n_literal": 1, "n_annotated": 0, "n_located": 1, "n_line_hits": 0,
         "n_page_hits": 0, "n_units": 1, "n_glyph_rects": 0, "n_glyphs": 1, "n_documents": 1,
         "n_pages_approx": 1, "corpora": "codh-full"},
    ])
    (index / "index.json").write_text('{"corpora": [{"name": "codh-full"}]}', encoding="utf-8")
    (index / "glyphs.json").write_text("{}", encoding="utf-8")
    api = CorpusAPI(str(root), str(index))
    reviews = CorpusReviews(api)
    api._atlas_reviews = reviews
    app = FastAPI()
    from glyph_atlas.corpus.fastapi_router import corpus_router

    app.include_router(corpus_router(api=api))
    app.include_router(review_router(reviews))
    return {"api": api, "reviews": reviews, "client": TestClient(app), "root": root, "index": index,
            "source": source}


def edit(identity: str, **overrides) -> dict:
    payload = {"id": str(uuid4()), "identity": identity, "client_id": "reviewer-1", "revision": 0,
               "source_revision": details.detail(CORPUS["api"], identity)["source_revision"],
               "verdict": "wrong", "issue": "character", "character": NU, "correction": None, "note": ""}
    payload.update(overrides)
    return payload


def fingerprint(source: Path) -> dict[str, str]:
    """Every source table by checksum: what a journal write must not touch."""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.glob("*.parquet"))}


CORPUS: dict = {}


@pytest.fixture(autouse=True)
def bound(corpus):
    CORPUS.clear()
    CORPUS.update(corpus)
    return corpus


def save(client, identity: str, **overrides):
    payload = edit(identity, **overrides)
    return client.post("/atlas/corpus/reviews", json=payload), payload


# The journal over a real source ------------------------------------------------------------------

def test_a_review_round_trips_and_the_source_is_never_written(corpus):
    before = fingerprint(corpus["source"])
    response, payload = save(corpus["client"], UNIT)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["label"] == NU and saved["code_point"] == "U+30CC"
    assert saved["source_label"] == "ね", "the original transcription survives the correction"
    assert saved["source_code_point"] == NE_POINT
    assert saved["state"] == "checked" and saved["revision"] == 1
    assert saved["review_event"] and saved["origin"] == "corpus"

    detail = corpus["client"].get("/atlas/corpus/character", params={"id": UNIT}).json()
    assert detail["label"] == NU and detail["reading"] == "ね"
    assert detail["source"]["holder"] == "Fixture Holder"
    assert saved["accepted_event"] == payload["id"], "the answer acknowledges the id that was sent"
    assert fingerprint(corpus["source"]) == before, "the source tables are not written by a review"
    assert (corpus["index"] / "reviews.sqlite").is_file(), "the journal is the one file written"


def test_the_encoded_identity_the_transcription_and_the_reading_are_three_layers(corpus):
    # What the resolver reports: the encoded character, the source's own transcription, the reading.
    resolved = details.detail(corpus["api"], UNIT)
    assert resolved["label"] == NE and resolved["code_point"] == NE_POINT
    assert resolved["source_label"] == "ね", "the source's own transcription is kept as it was imported"
    assert resolved["reading"] == "ね"
    assert resolved["label_is_verified"] is False and resolved["source_label_is_verified"] is False

    # The overlay preserves the transcription and encoded source class separately.
    detail = corpus["client"].get("/atlas/corpus/character", params={"id": UNIT}).json()
    assert detail["label"] == NE and detail["char"] == NE and detail["code_point"] == NE_POINT
    assert detail["source_label"] == "ね" and detail["source_code_point"] == NE_POINT
    assert detail["written_character"] is None
    assert detail["reading"] == "ね"

    reading_only, _ = save(corpus["client"], ALT_UNIT, issue="reading", character=None, correction="とも")
    assert reading_only.status_code == 200, reading_only.text
    assert reading_only.json()["label"] == TOMO and reading_only.json()["code_point"] == TOMO_POINT
    assert reading_only.json()["state"] == "flagged", "a reading edit does not confirm the identity"

    identity, _ = save(corpus["client"], ALT_UNIT, revision=1, issue="character", character=TOMO)
    assert identity.status_code == 200, identity.text
    assert identity.json()["label"] == TOMO and identity.json()["code_point"] == TOMO_POINT
    assert identity.json()["state"] == "checked"
    # Correcting the identity leaves the reading the source was imported with.
    assert identity.json()["reading"] == "とも" and identity.json()["source_label"] == TOMO


def test_the_link_is_the_registered_canvas(corpus):
    """The viewer link carries the canvas the source registered, and never a page position."""
    detail = details.detail(corpus["api"], UNIT)
    link = detail["record_url"]
    assert link
    query = parse_qs(urlparse(link).query)
    assert query["canvas"] == ["https://example.org/iiif/canvas/1"], "the registered canvas selects the page"
    assert query["xywh"] == ["40,30,24,30"], "the box is the character's own"
    assert "pos" not in query, "no page number is derived: the canvas is what the source registered"
    assert "200057428" in query["manifest"][0]

    # A page with no registered canvas: the character box is still linkable, and if no page can be
    # named the link is dropped rather than invented.
    tables.write(corpus["source"] / "pages.parquet", [Page(
        id=PAGE, document_id=DOC, seq=0, image="", canvas=None, width=1000, height=1000)], Page)
    bare = details.detail(corpus["api"], UNIT)
    if bare["record_url"]:
        bare_query = parse_qs(urlparse(bare["record_url"]).query)
        assert bare_query.get("canvas") != ["https://example.org/iiif/canvas/1"]
        assert "pos" not in bare_query


def test_a_source_that_was_deleted_still_acknowledges_the_save(corpus):
    """A glyph the source dropped answers `missing` on a retry: the journal keeps the record."""
    response, payload = save(corpus["client"], ALT_UNIT)
    assert response.status_code == 200, response.text
    write_units(corpus["source"], [unit for unit in units_of() if unit.id != ALT_UNIT])

    retry = corpus["client"].post("/atlas/corpus/reviews", json=payload)
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["state"] == "missing", "a deleted source is acknowledged, not hidden"
    assert body["accepted_event"] == payload["id"]
    assert body["review_event"], "the journal still holds the event"
    assert len(corpus["reviews"].exports()) == 1, "a retry writes no second event"


def test_a_codepoint_python_does_not_know_is_still_a_character(corpus):
    """U+1B127 is unassigned in this interpreter's unicodedata (Cn) and encoded in Unicode 18."""
    import unicodedata

    assert unicodedata.category("𛄧") == "Cn", "the interpreter does not know this codepoint"
    accepted, _ = save(corpus["client"], ALT_UNIT, character="𛄧")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["label"] == "𛄧" and accepted.json()["code_point"] == "U+1B127"


def test_the_glyph_and_the_counts_move_to_the_corrected_character(corpus):
    client = corpus["client"]
    before = client.get("/api/corpus/glyphs", params={"char": NE, "scope": "grapheme"}).json()
    assert before["total"] == 1 and before["items"][0]["char"] == NE
    assert client.get("/api/corpus/glyphs", params={"char": NU}).json()["total"] == 0

    counts_before = {row["char"]: row for row in
                     client.get("/api/corpus/counts", params={"chars": f"{NE},{NU}"}).json()["chars"]}
    assert counts_before[NE]["n_glyphs"] == 1 and counts_before[NU]["n_glyphs"] == 0

    response, _ = save(client, UNIT)
    assert response.status_code == 200, response.text

    moved = client.get("/api/corpus/glyphs", params={"char": NU}).json()
    assert moved["total"] == 1, "the corrected glyph is found under the character it was corrected to"
    assert moved["items"][0]["char"] == NU
    assert client.get("/api/corpus/glyphs", params={"char": NE}).json()["total"] == 0

    counts_after = {row["char"]: row for row in
                    client.get("/api/corpus/counts", params={"chars": f"{NE},{NU}"}).json()["chars"]}
    assert counts_after[NU]["n_glyphs"] == 1, "the counts follow the correction"
    assert counts_after[NE]["n_glyphs"] == 0
    assert counts_after[NE]["n_occurrences"] == 1, "a text occurrence is not a glyph and does not move"


# Provenance, staleness and the export ------------------------------------------------------------

def test_the_fingerprint_follows_provenance(corpus):
    first = details.detail(corpus["api"], UNIT)["source_revision"]
    assert len(first) == 64
    write_units(corpus["source"], units_of())          # an identical rewrite
    assert details.detail(corpus["api"], UNIT)["source_revision"] == first
    write_units(corpus["source"], units_of(box=Box(x=41, y=30, w=24, h=30)))
    assert details.detail(corpus["api"], UNIT)["source_revision"] != first, "a moved box is a change"


def test_a_repaired_source_makes_the_correction_stale(corpus):
    response, _ = save(corpus["client"], UNIT)
    assert response.status_code == 200
    assert corpus["reviews"].corrections() == {UNIT: NU}

    # A repair changes the row the glyph is: the encoded character and the box it sits in.
    write_units(corpus["source"], units_of(encoded="U+30E1", box=Box(x=44, y=30, w=24, h=30)))
    stale = corpus["client"].get("/atlas/corpus/character", params={"id": UNIT}).json()
    assert stale["state"] == "stale" and stale["label"] == "メ", "the repaired source wins"
    assert corpus["reviews"].corrections() == {}, "a stale correction is not in force"
    # The glyph is where the repaired source puts it, and nowhere the stale correction said.
    assert corpus["client"].get("/api/corpus/glyphs", params={"char": "メ", "scope": "grapheme"}).json()["total"] == 1
    assert corpus["client"].get("/api/corpus/glyphs", params={"char": "メ"}).json()["total"] == 0
    assert corpus["client"].get("/api/corpus/glyphs", params={"char": NU}).json()["total"] == 0
    exported = next(row for row in corpus["reviews"].exports() if row["event"]["target_id"] == UNIT)
    assert exported["current"] is False


def test_the_export_snapshots_the_source(corpus):
    response, _ = save(corpus["client"], UNIT)
    assert response.status_code == 200
    exported = corpus["reviews"].exports()[0]
    assert exported["reviewed"]["source_revision"] == response.json()["source_revision"]
    assert exported["reviewed"]["box"] == {"x": 40, "y": 30, "w": 24, "h": 30}
    assert exported["reviewed"]["source"]["title"] == "仮名資料"
    assert exported["source_update"]["applied_upstream"] is False
    assert exported["source_update"]["original_character"] == NE
    assert exported["source_update"]["proposed_character"] == NU
    assert exported["current"] is True


def test_a_retry_is_one_event_and_a_different_body_conflicts(corpus):
    client = corpus["client"]
    payload = edit(UNIT)
    first = client.post("/atlas/corpus/reviews", json=payload)
    assert first.status_code == 200, first.text
    events = len(corpus["reviews"].exports())

    retry = client.post("/atlas/corpus/reviews", json={**payload, "revision": 0})
    assert retry.status_code == 200, retry.text
    assert retry.json()["review_event"] == first.json()["review_event"]
    assert retry.json()["accepted_event"] == payload["id"], "the retried id is acknowledged"
    assert retry.json()["label"] == first.json()["label"]
    assert len(corpus["reviews"].exports()) == events, "a retry writes no second event"

    # A later review moves the record on; the retry answers the state that is in force now, and still
    # acknowledges the id the client is asking about.
    later = client.post("/atlas/corpus/reviews", json=edit(UNIT, revision=1, character="メ"))
    assert later.status_code == 200, later.text
    again = client.post("/atlas/corpus/reviews", json=payload)
    assert again.status_code == 200, again.text
    assert again.json()["accepted_event"] == payload["id"]
    assert again.json()["review_event"] == later.json()["review_event"], "the state in force"

    conflict = client.post("/atlas/corpus/reviews", json={**payload, "character": "メ"})
    assert conflict.status_code == 409, conflict.text


def test_a_stale_revision_or_source_is_refused(corpus):
    client = corpus["client"]
    payload = edit(UNIT)
    assert client.post("/atlas/corpus/reviews", json=payload).status_code == 200
    assert client.post("/atlas/corpus/reviews",
                       json={**payload, "id": str(uuid4()), "revision": 0}).status_code == 409
    assert client.post("/atlas/corpus/reviews", json={**payload, "id": str(uuid4()), "revision": 1,
                       "source_revision": "b" * 64}).status_code == 409
    assert len(corpus["reviews"].exports()) == 1


def test_erased_history_baseline_is_searchable_and_rejects_stale_clients(corpus):
    reviews = corpus["reviews"]
    source = reviews.source(UNIT)
    with reviews.connect() as db:
        db.execute("INSERT INTO glyph_baseline VALUES (?, ?, ?)", (UNIT, NU, source["source_revision"]))
        db.execute("INSERT INTO review_meta VALUES ('revision_floor', '1000000')")
        db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('glyph_reviews',1000000)")
    detail = reviews.detail(UNIT)
    assert detail["label"] == NU and detail["state"] == "pending"
    assert detail["revision"] == 1000000 and detail["review_event"] is None
    assert reviews.exports() == [] and reviews.corrections() == {UNIT: NU}
    rejected, _ = save(corpus["client"], UNIT)
    assert rejected.status_code == 409
    accepted, _ = save(corpus["client"], UNIT, revision=1000000, verdict="match", issue=None, character=None)
    assert accepted.status_code == 200
    assert accepted.json()["label"] == NU and accepted.json()["revision"] > 1000000


def test_stale_baseline_never_overrides_changed_source(corpus):
    reviews = corpus["reviews"]
    with reviews.connect() as db:
        db.execute("INSERT INTO glyph_baseline VALUES (?, ?, ?)", (UNIT, NU, "0" * 64))
    assert reviews.detail(UNIT)["label"] == NE
    assert reviews.corrections() == {}


@pytest.mark.parametrize(("character", "code_point"), [
    ("ツ゚", "U+30C4 U+309A"),  # a base and its mark are one character
    ("欄", "U+F91D"),  # a compatibility ideograph is not rewritten as its unified twin
])
def test_a_reviewer_s_character_is_kept_as_written(corpus, character, code_point):
    accepted, _ = save(corpus["client"], ALT_UNIT, character=character)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["code_point"] == code_point
