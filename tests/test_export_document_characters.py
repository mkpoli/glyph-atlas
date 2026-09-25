"""The rows of `document_characters`: a document's characters in the order its pages give them."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from glyph_atlas import refs, tables
from glyph_atlas.schema import Box, Line, ReviewState, Unit

SCRIPT = Path(__file__).parent.parent / "scripts" / "export_document_characters.py"
DOC = "hk:3daea514503efa7c8ec5ccc61c9be9d8"


def load():
    spec = importlib.util.spec_from_file_location("export_document_characters", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aligned(uid: str, page: int, line: int, seq: int, text: str, **extra) -> Unit:
    return Unit(**{"id": uid, "document_id": DOC, "page_id": f"{DOC}:{page}", "line_id": f"{DOC}:{page}:L{line}",
                   "seq": seq, "box": Box(x=seq * 10, y=0, w=10, h=10), "text_source": text,
                   "unicode": " ".join(refs.to_code_points(text)), **extra})


def imported(sample: str, page: int, block: str, position: int, label: str, origin: str) -> Unit:
    return Unit(id=f"ar:moshiogusa--ninjal-1:{sample}", document_id=DOC, page_id=f"{DOC}:{page - 1}",
                box=Box(x=1, y=2, w=3, h=4), text_source=label,
                meta={"ainu_records": {"id": sample, "page": page, "line": None, "block": block, "position": position,
                                       "context": "ナニ", "origin": origin}})


def dataset(tmp_path: Path) -> Path:
    units = [
        imported("1-ocr0-1", 1, "ocr0", 1, "ニ", "ocr"),
        imported("1-ocr0-0", 1, "ocr0", 0, "ナ", "ocr"),
        aligned(f"{DOC}:0:L1:r:0", 0, 1, 0, "イ"),
        aligned(f"{DOC}:0:L0:r:1", 0, 0, 1, "し", review=ReviewState.REVIEWED,
                meta={"ainu_records": {"id": "1-l1-1"}}),
        aligned(f"{DOC}:0:L0:r:0", 0, 0, 0, "ア"),
        aligned(f"{DOC}:0:L0:r:9", 0, 0, 9, "ウ", active=False),
        aligned(f"{DOC}:0:L0:r:7", 0, 0, 7, "ヲ", review=ReviewState.DISPUTED),
        aligned(f"{DOC}:0:L0:r:8", 0, 0, 8, "ヰ", meta={"alignment_repair": {"withheld": True}}),
        imported("2-l1-0", 2, "l1", 0, "エ", "transcription"),
        aligned("hk:other:0:L0:r:0", 0, 0, 0, "オ", document_id="hk:other"),
    ]
    units[3] = units[3].model_copy(update={"unicode": " ".join(refs.to_code_points("シ"))})
    tables.write(tmp_path / "units.parquet", units, Unit)
    tables.write(tmp_path / "lines.parquet", [
        Line(id=f"{DOC}:0:L0", page_id=f"{DOC}:0", seq=0, text_raw="", text="アシ"),
        Line(id=f"{DOC}:0:L1", page_id=f"{DOC}:0", seq=1, text_raw="", text="イ")], Line)
    return tmp_path


def rows(tmp_path: Path, **options) -> list[tuple[str, dict]]:
    out = tmp_path / "out.sql"
    load().export(dataset(tmp_path), out, **options)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE document_characters (document TEXT NOT NULL, ord INTEGER NOT NULL, unit TEXT NOT NULL, "
               "data TEXT NOT NULL, PRIMARY KEY(document, ord)) WITHOUT ROWID")
    db.executescript(out.read_text(encoding="utf-8"))
    return [(unit, json.loads(data)) for unit, data in
            db.execute("SELECT unit, data FROM document_characters WHERE document=? ORDER BY ord", (DOC,))]


def test_a_page_lists_its_lines_then_its_ocr_blocks_each_by_position(tmp_path):
    listed = rows(tmp_path)
    assert [d.get("sample") or unit for unit, d in listed] == [
        f"{DOC}:0:L0:r:0", "1-l1-1", f"{DOC}:0:L1:r:0", "1-ocr0-0", "1-ocr0-1", "2-l1-0"]


def test_an_aligned_unit_is_placed_by_its_own_line_and_named_by_its_review(tmp_path):
    reviewed = dict(rows(tmp_path))[f"{DOC}:0:L0:r:1"]
    assert reviewed == {"sample": "1-l1-1", "page": 1, "line": 1, "block": "l1", "position": 1, "context": "アシ",
                        "source": "review", "label": "シ", "box": [10, 0, 10, 10]}


def test_an_imported_occurrence_keeps_the_block_and_label_source_ainu_records_gave_it(tmp_path):
    ocr = dict(rows(tmp_path))["ar:moshiogusa--ninjal-1:1-ocr0-0"]
    assert (ocr["block"], ocr["position"], ocr["context"], ocr["source"], ocr["label"]) == ("ocr0", 0, "ナニ", "ocr", "ナ")


def test_only_the_documents_ainu_records_covers_are_replaced(tmp_path):
    out = tmp_path / "out.sql"
    counts = load().export(dataset(tmp_path), out)
    assert counts == {DOC: 6}, "retired, flagged and withheld units, and documents ainu-records has no say on, stay out"
    assert out.read_text(encoding="utf-8").startswith(f"DELETE FROM document_characters WHERE document IN ('{DOC}');")


def test_a_unit_a_person_corrected_in_the_review_store_is_listed(tmp_path):
    from glyph_atlas.review.store import ReviewRequest, Store
    from glyph_atlas.schema import Page

    directory = dataset(tmp_path)
    units = tables.read(directory / "units.parquet", Unit)
    rejected = aligned(f"{DOC}:0:L1:r:1", 0, 1, 1, "ロ", review=ReviewState.REJECTED)
    tables.write(directory / "units.parquet", [*units, rejected], Unit)
    tables.write(directory / "pages.parquet", [Page(id=f"{DOC}:{n}", document_id=DOC, seq=n, image="i", width=100,
                                                    height=100) for n in (0, 1)], Page)
    Store(directory).record_batch([ReviewRequest(target_id=rejected.id, field="text_source", new="ロ",
                                                 client_id="reviewer-1")])
    out = tmp_path / "out.sql"
    load().export(directory, out)
    assert rejected.id in out.read_text(encoding="utf-8"), "the aligner rejected it, and a person has since settled it"
