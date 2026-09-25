"""Merging ainu-records' character occurrences into the atlas, occurrence by occurrence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from glyph_atlas import ainu_characters, refs, tables
from glyph_atlas.review.store import ReviewRequest, Store
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
        {"id": "1-ocr0-7", "label": "ロ", "reading": "confirmed", "boundary": "confirmed"},  # a person relabelled it
    ]}), encoding="utf-8")
    (folder / "empty-exclusions.json").write_text(json.dumps({"records": [{"id": "1-l1-6"}]}), encoding="utf-8")
    return atlas, records


def uid(n: int) -> str:
    return f"{PAGE}:L0:r:{n}"


def units_of(directory: Path) -> dict[str, Unit]:
    return {u.id: u for u in tables.read(directory / "units.parquet", Unit)}


def event(n: int, target: str, name: str, old, new, role: str = "reviewer") -> dict:
    return {"id": f"rv{n:08d}", "target_type": "unit", "target_id": target, "field": name, "old": json.dumps(old),
            "new": json.dumps(new), "role": role, "actor": "reviewer-1", "evidence": None,
            "at": "2026-09-24T00:00:00+00:00"}


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
    assert ainu_characters.plan(atlas, records).agree == {"decided, same": 1}


def test_the_merged_dataset_keeps_confirms_replaces_and_imports(world, tmp_path):
    atlas, records = world
    out = tmp_path / "merged"
    counts = ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    assert counts == {"atlas kept": 2, "atlas confirmed": 1, "atlas replaced": 2, "imported": 1, "units": 9,
                      "active": 7}
    assert (out / "review.sqlite").exists(), "the store is rebuilt from the merged log"
    units = units_of(out)
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


def record(atlas: Path, target: str, name: str, new, role: str = "reviewer") -> None:
    Store(atlas).record_batch([ReviewRequest(target_id=target, field=name, new=new, client_id="reviewer-1")], role=role)


def test_a_unit_a_person_acted_on_keeps_its_reading_and_replay_agrees(world, tmp_path):
    atlas, records = world
    record(atlas, uid(1), "unicode", "U+30C4")  # a person names the ink ツ, where ainu-records reads ト
    result = ainu_characters.plan(atlas, records)
    assert uid(1) in [u for u, _ in result.keep_atlas] and uid(1) not in [u for u, _ in result.replace]
    out = tmp_path / "merged"
    counts = ainu_characters.build(result, atlas, records, out)
    assert "merge events" not in counts, "a kept unit the log names gets no event for a note"
    kept = units_of(out)[uid(1)]
    assert (kept.unicode, "ainu_records" in kept.meta) == ("U+30C4", False)


def test_a_machine_event_is_no_decision_and_replay_keeps_the_merge(world, tmp_path):
    atlas, records = world
    record(atlas, uid(1), "meta", {"alignment_repair": {"withheld": True, "quiz": False}}, role="model")
    result = ainu_characters.plan(atlas, records)
    assert [u for u, _ in result.confirm] == [uid(1)]
    ainu_characters.build(result, atlas, records, tmp_path / "merged")
    assert units_of(tmp_path / "merged")[uid(1)].meta["alignment_repair"]["quiz"] is True


def test_a_split_a_person_made_survives_the_merge(world, tmp_path):
    atlas, records = world
    record(atlas, uid(4), "segmentation", {"split": [
        {"box": {"x": 900, "y": 100, "w": 20, "h": 50}, "unicode": "U+30AB", "reading": "カ", "text_source": "カ"},
        {"box": {"x": 920, "y": 100, "w": 20, "h": 50}, "unicode": "U+30AB", "reading": "カ", "text_source": "カ"}]})
    children = [u.id for u in ainu_characters.read_log(atlas).units.values() if u.active and u.id.startswith(f"{PAGE}:L0:m")]
    assert len(children) == 2
    out = tmp_path / "merged"
    ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    merged = units_of(out)
    assert not merged[uid(4)].active and all(merged[c].active for c in children)


def test_a_flagged_unit_ainu_records_reads_the_same_stays_flagged(world):
    atlas, records = world
    units = tables.read(atlas / "units.parquet", Unit)
    units[5] = units[5].model_copy(update={"text_source": "ロ", "unicode": " ".join(refs.to_code_points("ロ"))})
    tables.write(atlas / "units.parquet", units, Unit)
    result = ainu_characters.plan(atlas, records)
    assert uid(5) in [u for u, _ in result.keep_atlas] and uid(5) not in [u for u, _ in result.confirm]


def test_ink_ainu_records_rejected_withholds_the_machine_unit_on_it(world, tmp_path):
    atlas, records = world
    units = tables.read(atlas / "units.parquet", Unit)
    tables.write(atlas / "units.parquet", [*units, unit(6, 700, "イ")], Unit)
    result = ainu_characters.plan(atlas, records)
    assert [u for u, _ in result.withhold] == [uid(6)]
    ainu_characters.build(result, atlas, records, tmp_path / "merged")
    assert units_of(tmp_path / "merged")[uid(6)].meta["alignment_repair"]["quiz"] is False


def test_a_reading_a_person_doubted_does_not_replace_a_label(world):
    atlas, records = world
    folder = records / "data/characters/moshiogusa--ninjal-1"
    reviews = json.loads((folder / "reviews.json").read_text(encoding="utf-8"))
    reviews["edits"].append({"id": "1-l1-2", "label": "ユ", "reading": "uncertain", "boundary": "confirmed"})
    (folder / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    assert uid(2) in [u for u, _ in ainu_characters.plan(atlas, records).keep_withheld]


def test_a_blank_reading_never_replaces_a_label(world):
    atlas, records = world
    folder = records / "data/characters/moshiogusa--ninjal-1"
    samples = json.loads((folder / "samples.json").read_text(encoding="utf-8"))
    samples["samples"][2]["proposed"] = ""  # ainu-records writes 〓 as a blank
    (folder / "samples.json").write_text(json.dumps(samples), encoding="utf-8")
    result = ainu_characters.plan(atlas, records)
    assert uid(2) in [u for u, _ in result.keep_withheld]


def test_merging_its_own_output_again_imports_nothing_twice(world, tmp_path):
    atlas, records = world
    first = tmp_path / "first"
    ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, first)
    again = ainu_characters.plan(first, records)
    assert again.import_new == [] and again.replace == []
    assert sorted(again.imported_before) == sorted(u for u in units_of(first) if u.startswith("ar:"))
    ainu_characters.build(again, first, records, tmp_path / "second")


def test_an_import_retired_or_rejected_since_is_handled_on_the_next_merge(world, tmp_path):
    atlas, records = world
    first = tmp_path / "first"
    ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, first)
    record(first, "ar:moshiogusa--ninjal-1:1-l1-4", "segmentation", {"merge": [
        "ar:moshiogusa--ninjal-1:1-l1-4", "ar:moshiogusa--ninjal-1:1-l1-2"]})
    folder = records / "data/characters/moshiogusa--ninjal-1"
    reviews = json.loads((folder / "reviews.json").read_text(encoding="utf-8"))
    reviews["edits"].append({"id": "1-ocr0-3", "label": "ス", "reading": "rejected", "boundary": "rejected"})
    (folder / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    again = ainu_characters.plan(first, records)
    assert again.import_new == [], "a merged-away import is not imported again"
    ainu_characters.build(again, first, records, tmp_path / "second")


def test_the_merge_carries_the_datasets_other_files(world, tmp_path):
    atlas, records = world
    (atlas / "quiz-shapes.json").write_text('{"orders": {}}', encoding="utf-8")
    (atlas / "repairs.jsonl").write_text("", encoding="utf-8")
    out = tmp_path / "merged"
    ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    assert (out / "quiz-shapes.json").exists() and (out / "repairs.jsonl").exists()


def test_parts_are_numbered_as_ainu_records_numbers_them(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/sources.yaml").write_text(
        "sources:\n  - slug: s\n    witnesses:\n      - slug: w\n        parts:\n"
        "          - wikisource: {index: a.pdf}\n          - entry: e2\n", encoding="utf-8")
    assert ainu_characters.entries(tmp_path) == {"s/w-2": "e2"}


def test_a_store_loaded_from_other_tables_is_refused(world):
    atlas, _ = world
    record(atlas, uid(1), "unicode", "U+30C4")
    units = tables.read(atlas / "units.parquet", Unit)
    tables.write(atlas / "units.parquet", units[:-1], Unit)  # an alignment rewrites the tables
    with pytest.raises(RuntimeError, match="other tables"):
        ainu_characters.read_log(atlas)


def test_merge_events_never_reuse_an_event_number(world, tmp_path):
    atlas, records = world
    record(atlas, uid(1), "meta", {"note": "x"}, role="model")
    record(atlas, uid(2), "meta", {"note": "y"}, role="model")
    log = ainu_characters.read_log(atlas)
    log.events = log.events[1:]  # as a reset leaves it: the first number was handed out and erased
    out = tmp_path / "merged"
    ainu_characters.build(ainu_characters.plan(atlas, records, log=log), atlas, records, out, log=log)
    ids = [json.loads(line)["id"] for line in (out / "reviews.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ids) == len(set(ids)) and "rv00000001" not in ids[1:]
    assert Store(out)  # the rebuilt store opens over the merged tables without a stale-table refusal


def test_ocr_settles_a_unit_the_atlas_does_not_stand_behind(world):
    atlas, records = world
    units = tables.read(atlas / "units.parquet", Unit)
    withheld = {"alignment_repair": {"status": "uncertain", "withheld": True, "quiz": False}}
    units[3] = units[3].model_copy(update={"meta": withheld})  # OCR reads ス where the atlas says ヌ
    tables.write(atlas / "units.parquet", units, Unit)
    result = ainu_characters.plan(atlas, records)
    assert uid(3) in [u for u, _ in result.replace], "an unverified pairing gives way to a reading of the ink"
    units[3] = units[3].model_copy(update={"text_source": "ス", "unicode": " ".join(refs.to_code_points("ス"))})
    tables.write(atlas / "units.parquet", units, Unit)
    assert uid(3) in [u for u, _ in ainu_characters.plan(atlas, records).confirm], "the OCR agrees, so it is released"


def test_the_merged_dataset_leaves_its_store_current(world, tmp_path, monkeypatch):
    import os

    atlas, records = world
    export = Store.export

    def later(self):  # an export that finishes in a later second than the store was loaded in
        counts = export(self)
        units = self.directory / "units.parquet"
        os.utime(units, (units.stat().st_atime, units.stat().st_mtime + 5))
        return counts

    monkeypatch.setattr(Store, "export", later)
    out = tmp_path / "merged"
    ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    assert ainu_characters.read_log(out).units, "a merge can read the dataset it wrote"


def test_the_merged_store_keeps_the_ledgers_no_event_rebuilds(world, tmp_path):
    import sqlite3

    atlas, records = world
    record(atlas, uid(1), "unicode", "U+30C4")
    with sqlite3.connect(atlas / "review.sqlite") as db:
        db.execute("INSERT INTO revision_bases VALUES (?, ?)", (uid(1), 2000000))
        db.execute("CREATE TABLE cloudflare_imports(remote_id TEXT PRIMARY KEY, target_id TEXT NOT NULL)")
        db.execute("INSERT INTO cloudflare_imports VALUES ('remote-1', ?)", (uid(1),))
    out = tmp_path / "merged"
    counts = ainu_characters.build(ainu_characters.plan(atlas, records), atlas, records, out)
    assert "merge events" not in counts, "a note alone is no reason for an event"
    with sqlite3.connect(out / "review.sqlite") as db:
        assert db.execute("SELECT base FROM revision_bases").fetchall() == [(2000000,)]
        assert db.execute("SELECT remote_id FROM cloudflare_imports").fetchall() == [("remote-1",)]


def test_one_ink_is_imported_once_and_two_readings_of_it_are_withheld(world, tmp_path):
    atlas, records = world
    folder = records / "data/characters/moshiogusa--ninjal-1"
    samples = json.loads((folder / "samples.json").read_text(encoding="utf-8"))
    samples["samples"] += [sample(8, 601, "ア", "ocr", None),  # the same ink as 1-l1-4, read the same
                           sample(9, 1301, "キ", "transcription", 2), sample(10, 1300, "ケ", "transcription", 3)]
    samples["samples"][-2]["id"], samples["samples"][-1]["id"] = "1-l2-9", "1-l3-10"
    (folder / "samples.json").write_text(json.dumps(samples), encoding="utf-8")
    result = ainu_characters.plan(atlas, records)
    assert "1-ocr0-8" not in [o.id for o in result.import_new]
    assert result.skipped["same ink as another occurrence"] == 1
    assert result.contested == {"moshiogusa/ninjal-1#1-l2-9", "moshiogusa/ninjal-1#1-l3-10"}
    out = tmp_path / "merged"
    ainu_characters.build(result, atlas, records, out)
    units = units_of(out)
    assert all(units[f"ar:moshiogusa--ninjal-1:{i}"].meta["alignment_repair"]["withheld"] for i in ("1-l2-9", "1-l3-10"))
    assert not ainu_characters.trusted(units["ar:moshiogusa--ninjal-1:1-l2-9"]), "rec.aynu.org leaves them out"
