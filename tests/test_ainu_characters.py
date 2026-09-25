"""Merging ainu-records' character occurrences into the atlas, occurrence by occurrence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from glyph_atlas import ainu_characters, refs, tables
from glyph_atlas.schema import Box, Line, Page, ReviewState, Unit

ENTRY = "3daea514503efa7c8ec5ccc61c9be9d8"
PAGE = f"hk:{ENTRY}:0"


def unit(n: int, x: int, text: str, review: ReviewState = ReviewState.MACHINE, **extra) -> Unit:
    return Unit(id=f"{PAGE}:L0:r:{n}", document_id=f"hk:{ENTRY}", page_id=PAGE, line_id=f"{PAGE}:L0", seq=n,
                box=Box(x=x, y=100, w=40, h=50), text_source=text, reading=text,
                unicode=" ".join(refs.to_code_points(text)), review=review, **extra)


def sample(n: int, x: int, proposed: str, origin: str = "transcription", line: int | None = 1) -> dict:
    return {"id": f"1-l{line}-{n}" if line else f"1-ocr0-{n}", "page": 1, "line": line, "block": None,
            "position": n, "proposed": proposed, "origin": origin, "context": "シトコ", "box": [x, 100, 40, 50],
            "revision": 1}


@pytest.fixture
def world(tmp_path: Path):
    atlas = tmp_path / "atlas"
    atlas.mkdir()
    units = [
        unit(0, 100, "シ", ReviewState.REVIEWED),  # a person's decision
        unit(1, 200, "ト"),  # ainu-records agrees
        unit(2, 300, "コ"),  # ainu-records reads another character
        unit(3, 400, "ヌ"),  # ainu-records has only OCR here
        unit(4, 900, "カ"),  # only the atlas has it
        unit(5, 500, "ル", ReviewState.DISPUTED),  # flagged: a machine label a person doubted
    ]
    tables.write(atlas / "units.parquet", units, Unit)
    tables.write(atlas / "lines.parquet", [Line(id=f"{PAGE}:L0", page_id=PAGE, seq=0, text_raw="", text="")], Line)
    tables.write(atlas / "pages.parquet", [Page(id=PAGE, document_id=f"hk:{ENTRY}", seq=0, image="i", width=1000,
                                                height=800)], Page)
    (atlas / "reviews.jsonl").write_text("", encoding="utf-8")

    records = tmp_path / "ainu-records"
    folder = records / "data/characters/moshiogusa--ninjal-1"
    folder.mkdir(parents=True)
    (records / "data/sources.yaml").write_text(
        "sources:\n  - slug: moshiogusa\n    witnesses:\n      - slug: ninjal\n        parts:\n"
        f"          - entry: {ENTRY}\n          - entry: 62c6743982041882d0aefd6582ac6a84\n", encoding="utf-8")
    samples = [sample(0, 101, "ツ"), sample(1, 201, "ト"), sample(2, 301, "ユ"), sample(3, 401, "ス", "ocr", None),
               sample(4, 600, "ア"), sample(5, 700, "イ"), sample(6, 800, "ウ"), sample(7, 501, "ル", "ocr", None)]
    (folder / "samples.json").write_text(json.dumps({"key": "moshiogusa/ninjal-1", "samples": samples}),
                                         encoding="utf-8")
    (folder / "reviews.json").write_text(json.dumps({"edits": [
        {"id": "1-l1-5", "label": "イ", "reading": "rejected"},
        {"id": "1-ocr0-7", "label": "ロ", "reading": "confirmed"},  # a person relabelled the OCR reading
    ]}), encoding="utf-8")
    (folder / "empty-exclusions.json").write_text(json.dumps({"records": [{"id": "1-l1-6"}]}), encoding="utf-8")
    return atlas, records


def uid(n: int) -> str:
    return f"{PAGE}:L0:r:{n}"


def test_every_occurrence_and_unit_gets_one_decision(world):
    atlas, records = world
    result = ainu_characters.plan(atlas, records)
    assert [u for u, _ in result.keep_atlas] == [uid(0)], "a person's decision stands"
    assert [u for u, _ in result.confirm] == [uid(1)]
    assert sorted(u for u, _ in result.replace) == [uid(2), uid(5)], "a flagged unit is no decision"
    assert [u for u, _ in result.keep_withheld] == [uid(3)], "an OCR reading does not override a transcription"
    assert [o.id for o in result.import_new] == ["1-l1-4"]
    assert result.atlas_only == [uid(4)]
    assert result.skipped == {"rejected or empty in ainu-records": 2}


def test_a_review_in_ainu_records_makes_its_label_trusted(world):
    atlas, records = world
    replaced = dict(ainu_characters.plan(atlas, records).replace)
    assert replaced[uid(5)].origin == "review"
    assert replaced[uid(5)].label == "ロ"


def test_a_reviewed_unit_is_compared_by_the_identity_its_review_gave(world):
    atlas, records = world
    units = tables.read(atlas / "units.parquet", Unit)
    units[0] = units[0].model_copy(update={"text_source": "し", "unicode": " ".join(refs.to_code_points("ツ"))})
    tables.write(atlas / "units.parquet", units, Unit)
    assert ainu_characters.plan(atlas, records).agree == {"reviewed, same": 1}


def test_the_merged_dataset_keeps_confirms_replaces_and_imports(world, tmp_path):
    atlas, records = world
    out = tmp_path / "merged"
    counts = ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    assert counts == {"atlas kept": 2, "atlas confirmed": 1, "atlas replaced": 2, "imported": 1, "units": 9,
                      "active": 7}
    units = {u.id: u for u in tables.read(out / "units.parquet", Unit)}
    assert units[uid(1)].meta["alignment_repair"]["quiz"] is True
    assert units[uid(1)].meta["ainu_records"]["id"] == "1-l1-1"
    assert not units[uid(2)].active
    new = units[units[uid(2)].meta["ainu_records"]["replaced_by"]]
    assert (new.id, new.text_source, new.line_id, new.method) == ("ar:moshiogusa--ninjal-1:1-l1-2", "ユ",
                                                                  f"{PAGE}:L0", "import")
    assert new.upstream == {"source": "ainu-records", "id": "moshiogusa/ninjal-1#1-l1-2"}
    assert units["ar:moshiogusa--ninjal-1:1-ocr0-7"].review == ReviewState.REVIEWED
    assert units[uid(4)].active and "ainu_records" not in units[uid(4)].meta
    assert (out / "reviews.jsonl").exists() and (out / "lines.parquet").exists()


def test_the_merge_never_writes_over_a_dataset(world, tmp_path):
    atlas, records = world
    with pytest.raises(FileExistsError):
        ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, atlas)
