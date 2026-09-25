"""Round trip, ordering, validation and merge of the dataset tables."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import tables
from glyph_atlas.schema import (
    Box,
    Candidate,
    Classification,
    Confidence,
    Dating,
    Document,
    Group,
    Licence,
    Line,
    LineRole,
    Page,
    PageText,
    Register,
    Review,
    ReviewState,
    Rights,
    Script,
    Source,
    Unit,
    UnitKind,
    VariantRef,
)

MODELS = {
    "documents": Document,
    "pages": Page,
    "page_texts": PageText,
    "lines": Line,
    "units": Unit,
    "groups": Group,
}


def rights() -> Rights:
    return Rights(licence=Licence.CC_BY_SA_4, holder="国文学研究資料館", attribution="出典",
                  evidence="https://example.org/licence", checked=date(2024, 5, 1))


def document(ident: str = "d1", **overrides) -> Document:
    fields = {"id": ident, "title": "源氏物語", "source_refs": {"codh-char-shape": "200006663"}, "holder": "国文研",
              "shelfmark": "甲-1", "production": "handwritten", "genre": ["monogatari"],
              "text_register": Register.WABUN,
              "dating": [Dating(literal="文政3", start=1820, end=1820, kind="copying", evidence="奥書")],
              "hands": ["筆者"], "image_rights": rights(), "text_rights": rights(), "meta": {"note": "写本"}}
    return Document(**{**fields, **overrides})


def page(ident: str = "p1", **overrides) -> Page:
    fields = {"id": ident, "document_id": "d1", "seq": 1, "canvas": "https://example.org/canvas/1",
              "image": "https://example.org/iiif/1/full", "width": 1000, "height": 1500, "sha256": "a" * 64,
              "transcription": {"source": "codh-char-shape", "entry": "200006663"}, "meta": {"half": 1}}
    return Page(**{**fields, **overrides})


def page_text(**overrides) -> PageText:
    fields = {"page_id": "p1", "source": "codh-char-shape", "revision": "r1", "text_raw": "か"}
    return PageText(**{**fields, **overrides})


def line(ident: str = "l1", **overrides) -> Line:
    fields = {"id": ident, "page_id": "p1", "seq": 0, "box": Box(x=10, y=20, w=30, h=40), "vertical": True,
              "role": LineRole.MAIN, "text_raw": "か", "text": "か", "match_method": None,
              "match_confidence": None, "meta": {}}
    return Line(**{**fields, **overrides})


def unit(ident: str = "u1", **overrides) -> Unit:
    fields = {"id": ident, "document_id": "d1", "page_id": "p1", "line_id": None, "seq": 0,
              "box": Box(x=10, y=20, w=30, h=40), "crop": None, "kind": UnitKind.CHAR, "granularity": "char",
              "text_source": "か", "reading": "か", "unicode": "U+304B",
              "classification": Classification.IDENTIFIED, "script": Script.HIRAGANA}
    return Unit(**{**fields, **overrides})


def group(ident: str = "g1", **overrides) -> Group:
    fields = {"id": ident, "page_id": "p1", "box": Box(x=10, y=20, w=100, h=40), "unit_ids": ["u1"]}
    return Group(**{**fields, **overrides})


def write_dataset(directory: Path, **records: list) -> Path:
    """Write the named tables as plain files; a table that is not named is absent from the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in records.items():
        tables.write(directory / f"{name}.parquet", rows, MODELS[name])
    return directory


SAMPLES = {
    Box: Box(x=1, y=2, w=3, h=4),
    Rights: rights(),
    Source: Source(id="codh-char-shape", name="日本古典籍くずし字データセット", publisher="CODH",
                   kind="image-collection", url="https://codh.rois.ac.jp/char-shape/", licence=Licence.CC_BY_SA_4,
                   attribution="出典", version="2023-01", doi="10.20676/00000340", released=date(2023, 1, 1)),
    Dating: Dating(literal="文政3", start=1820, end=1820, kind="copying", evidence="奥書"),
    Document: document(),
    Page: page(),
    PageText: page_text(),
    Line: line(box=Box(x=1, y=2, w=3, h=4), vertical=False, role=LineRole.RUBY, text_raw="か<ruby>き</ruby>",
               text="かき", match_method="detector", match_confidence=0.875, meta={"split": "right"}),
    VariantRef: VariantRef(scheme="mj", id="MJ090024", version="2019"),
    Candidate: Candidate(unicode="U+304B", p=0.75, jibo="可"),
    Confidence: Confidence(detection=0.9, segmentation=0.8, text=0.7, jibo=0.6, model="detector-1"),
    Unit: Unit(
        id="u1", document_id="d1", page_id="p1", line_id="l1", seq=2, box=Box(x=1, y=2, w=3, h=4),
        crop="crops/u1.jpg", crop_sha256="b" * 64, kind=UnitKind.ITERATION_MARK, granularity="sequence",
        text_source="ゝ", reading="か", unicode="U+304B", classification=Classification.AMBIGUOUS,
        script=Script.HENTAIGANA, variants=[VariantRef(scheme="mj", id="MJ090024"),
                                                       VariantRef(scheme="local", id="ka-3")],
        candidates=[Candidate(unicode="U+304B", p=0.6, jibo="可"), Candidate(unicode="U+1B019", p=0.3)],
        antecedent_ids=["u0"], group_id="g1", voicing="dakuten", method="detect-align",
        confidence=Confidence(detection=0.9, segmentation=0.8, text=0.7, jibo=0.6, model="detector-1"),
        review=ReviewState.DOUBLE_REVIEWED, upstream={"source": "codh-char-shape", "ref": "200006663/1/B1/C1"},
        active=False, split_into=["u5", "u6"], merged_into=None,
    ),
    Group: group(unit_ids=["u1", "u2"]),
    Review: Review(id="r1", target_type="unit", target_id="u1", field="unicode", old="U+304A",
                   new={"unicode": "U+304B"}, role="reviewer", actor="reviewer-1",
                   evidence="https://example.org/review/1", at=datetime(2024, 5, 1, 12, 0, tzinfo=UTC)),
}


def test_schema_for_maps_the_pydantic_types_to_arrow():
    document_schema = tables.schema_for(Document)
    assert document_schema.field("id").type == pa.string()
    assert document_schema.field("production").type == pa.string()
    assert document_schema.field("source_refs").type == pa.string() and document_schema.field("meta").type == pa.string()
    assert document_schema.field("dating").type == pa.list_(
        pa.struct([("literal", pa.string()), ("start", pa.int64()), ("end", pa.int64()), ("kind", pa.string()),
                   ("evidence", pa.string())])
    )
    assert document_schema.field("image_rights").type.field("checked").type == pa.date32()
    assert tables.schema_for(Unit).field("box").type == pa.struct(
        [("x", pa.int64()), ("y", pa.int64()), ("w", pa.int64()), ("h", pa.int64())]
    )
    assert tables.schema_for(Line).field("vertical").type == pa.bool_()
    assert tables.schema_for(Review).field("at").type == pa.timestamp("us", tz="UTC")


@pytest.mark.parametrize("model, record", [pytest.param(model, record, id=model.__name__) for model, record in SAMPLES.items()])
def test_every_model_round_trips(tmp_path, model, record):
    path = tmp_path / f"{model.__name__}.parquet"
    assert tables.write(path, [record], model) == 1
    assert tables.read(path, model) == [record]
    assert pq.read_schema(path) == tables.schema_for(model)


@pytest.mark.parametrize("model", [pytest.param(model, id=model.__name__) for model in SAMPLES])
def test_an_empty_table_keeps_every_column(tmp_path, model):
    path = tmp_path / f"{model.__name__}.parquet"
    assert tables.write(path, [], model) == 0
    assert tables.read(path, model) == []
    assert pq.read_schema(path) == tables.schema_for(model)


def test_write_takes_dumped_dicts(tmp_path):
    path = tmp_path / "units.parquet"
    assert tables.write(path, [unit().model_dump(mode="json")], Unit) == 1
    assert tables.read(path, Unit) == [unit()]


def test_rows_are_sorted_by_document_page_seq_and_id_with_nulls_first(tmp_path):
    rows = [unit("u3", document_id="d1", page_id="p2", seq=1),
            unit("u1", document_id="d1", page_id=None, seq=2),
            unit("u2", document_id="d1", page_id="p1", seq=1),
            unit("u0", document_id=None, page_id="p1", seq=0),
            unit("u4", document_id="d1", page_id="p1", seq=1)]
    path = tmp_path / "units.parquet"
    tables.write(path, rows, Unit)
    assert [record.id for record in tables.read(path, Unit)] == ["u0", "u1", "u2", "u4", "u3"]


def test_shards_are_named_after_sha1_of_the_document_and_scan_walks_them(tmp_path):
    directory = tmp_path / "units"
    rows = [unit("u0", document_id="d1"), unit("u1", document_id="d2"), unit("u2", document_id="d1"),
            unit("u3", document_id=None, page_id=None, crop="crop.jpg", box=None)]
    assert tables.write(directory, rows, Unit, shard=True) == 4
    buckets = {hashlib.sha1(value.encode()).hexdigest()[:2] for value in ("d1", "d2")} | {"00"}
    assert {path.stem for path in directory.glob("*.parquet")} == buckets

    batches = list(tables.scan(directory, Unit, columns=["id"], batch_size=1))
    assert [len(batch) for batch in batches] == [1, 1, 1, 1]
    assert sorted(record.id for batch in batches for record in batch) == ["u0", "u1", "u2", "u3"]
    assert all(record.document_id is None and record.box is None for batch in batches for record in batch)
    assert sorted(record.id for record in tables.read(directory, Unit)) == ["u0", "u1", "u2", "u3"]


def test_a_sharded_write_replaces_the_shards_it_finds(tmp_path):
    directory = tmp_path / "units"
    tables.write(directory, [unit("u0", document_id="d1")], Unit, shard=True)
    stale = directory / "aa.parquet"
    stale.write_bytes(b"")
    tables.write(directory, [unit("u1", document_id="d2")], Unit, shard=True)
    assert not stale.exists()
    assert [record.id for record in tables.read(directory, Unit)] == ["u1"]


def test_a_directory_needs_shard_true_and_only_units_and_lines_shard(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    with pytest.raises(ValueError, match="needs shard=True"):
        tables.write(directory, [document()], Document)
    with pytest.raises(ValueError, match="not sharded"):
        tables.write(directory, [document()], Document, shard=True)
    with pytest.raises(ValueError, match="directory of shards"):
        tables.write(tmp_path / "units.parquet", [unit()], Unit, shard=True)


def test_the_manifest_counts_the_rows_written_and_checksums_the_files(tmp_path):
    directory = tmp_path / "units"
    rows = [unit(f"u{i}", document_id=f"d{i % 2}") for i in range(3)]
    assert tables.write(directory, rows, Unit, shard=True, command="atlas import codh") == 3
    manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == tables.SCHEMA_VERSION
    assert manifest["tables"] == {"units": 3}
    assert manifest["command"] == "atlas import codh"
    assert manifest["writer"].startswith("glyph-atlas ")
    assert datetime.fromisoformat(manifest["written_at"]).utcoffset() == timedelta(0)
    files = sorted(directory.glob("*.parquet"))
    assert set(manifest["files"]) == {file.name for file in files}
    for file in files:
        assert manifest["files"][file.name] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert sum(len(tables.read(file, Unit)) for file in files) == manifest["tables"]["units"]


def test_a_complete_dataset_validates_without_errors(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[page()], page_texts=[page_text()],
                              lines=[line()], units=[unit()], groups=[group()])
    dataset = tables.Dataset(directory)
    assert dataset.validate() == []
    assert dataset.read("groups") == [group()]
    assert {name for name, path in dataset.tables.items() if path is not None} == set(MODELS)


def test_a_dangling_page_id_is_reported(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[], units=[unit(page_id="p9")])
    assert tables.Dataset(directory).validate() == ["units: u1: page_id 'p9' not in pages"]


def test_other_dangling_references_are_reported(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[page()], lines=[],
                              units=[unit(line_id="l9")], groups=[group(unit_ids=["u9"])])
    assert tables.Dataset(directory).validate() == ["units: u1: line_id 'l9' not in lines",
                                                    "groups: g1: unit_ids 'u9' not in units"]


def test_a_unit_without_a_page_and_without_a_crop_is_reported(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()],
                              units=[unit(page_id=None, crop=None, box=None)])
    assert tables.Dataset(directory).validate() == ["units: u1: page_id is null and crop is not set"]


@pytest.mark.parametrize("box", [Box(x=0, y=0, w=0, h=10), Box(x=0, y=0, w=10, h=-1)])
def test_a_box_without_a_positive_size_is_reported(tmp_path, box):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[page()], lines=[line(box=box)])
    assert tables.Dataset(directory).validate() == [f"lines: l1: box {box.w}x{box.h}; w and h must be positive"]


def test_a_box_outside_a_page_of_known_size_is_reported(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[page(width=100, height=100)],
                              units=[unit(box=Box(x=90, y=10, w=20, h=20))])
    assert tables.Dataset(directory).validate() == ["units: u1: box 90,10,20,20 outside page p1 100x100"]


def test_a_box_is_not_checked_against_a_page_of_unknown_size(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()], pages=[page(width=0, height=0)],
                              units=[unit(box=Box(x=9090, y=10, w=20, h=20))])
    assert tables.Dataset(directory).validate() == []


def test_a_row_that_does_not_parse_is_reported_with_its_id(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    row = pa.Table.from_pylist([{"id": "u1", "document_id": "d1", "page_id": None, "crop": "crop.jpg", "seq": "x"}])
    pq.write_table(row, directory / "units.parquet")
    errors = tables.Dataset(directory).validate()
    assert errors[0] == "documents: table documents.parquet is missing"
    assert errors[1].startswith("units: u1: does not parse: seq: ")


def test_a_duplicated_id_is_reported(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    duplicate = pa.Table.from_pylist([{"id": "d1", "title": "a"}, {"id": "d1", "title": "b"}])
    pq.write_table(duplicate, directory / "documents.parquet")
    assert tables.Dataset(directory).validate() == ["documents: d1: duplicate id"]


def test_a_file_that_is_not_parquet_is_reported(tmp_path):
    directory = write_dataset(tmp_path / "work", documents=[document()])
    (directory / "units.parquet").write_text("not parquet", encoding="utf-8")
    errors = tables.Dataset(directory).validate()
    assert len(errors) == 1 and errors[0].startswith("units: cannot read the table: ")


def test_merge_collapses_an_identical_duplicate(tmp_path):
    first = write_dataset(tmp_path / "first", documents=[document()], units=[unit("u1"), unit("u2", seq=1)])
    second = write_dataset(tmp_path / "second", documents=[document()], units=[unit("u1"), unit("u3", seq=2)])
    counts = tables.Dataset(first).merge([tables.Dataset(second)], tmp_path / "merged",
                                         command="atlas tables merge first second --out merged")
    assert counts == {"documents": 1, "units": 3}
    assert [record.id for record in tables.Dataset(tmp_path / "merged").read("units")] == ["u1", "u2", "u3"]
    manifest = json.loads((tmp_path / "merged" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tables"] == {"documents": 1, "units": 3}
    assert set(manifest["files"]) == {"documents.parquet", "units.parquet"}
    assert manifest["command"] == "atlas tables merge first second --out merged"


def test_merge_prints_both_rows_of_a_conflicting_duplicate(tmp_path):
    first = write_dataset(tmp_path / "first", documents=[document()], units=[unit("u1")])
    second = write_dataset(tmp_path / "second", documents=[document()],
                           units=[unit("u1", unicode="U+1B003", reading="か", script=Script.HENTAIGANA)])
    with pytest.raises(tables.DuplicateIdError) as excinfo:
        tables.Dataset(first).merge([tables.Dataset(second)], tmp_path / "merged")
    message = str(excinfo.value)
    assert "units: duplicate id 'u1' with different content" in message
    assert '"unicode": "U+304B"' in message and '"unicode": "U+1B003"' in message
    assert excinfo.value.id == "u1" and len(excinfo.value.rows) == 2


def test_merge_keeps_a_sharded_table_sharded(tmp_path):
    first = tmp_path / "first"
    tables.write(first / "units", [unit("u0", document_id="d1"), unit("u1", document_id="d2")], Unit, shard=True)
    counts = tables.Dataset(first).merge([], tmp_path / "merged")
    assert counts == {"units": 2}
    merged = tmp_path / "merged" / "units"
    assert merged.is_dir()
    assert {path.stem for path in merged.glob("*.parquet")} == {
        hashlib.sha1(value.encode()).hexdigest()[:2] for value in ("d1", "d2")
    }
    assert sorted(record.id for record in tables.Dataset(tmp_path / "merged").read("units")) == ["u0", "u1"]
