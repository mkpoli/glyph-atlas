"""Tests of the multi-book importer of the 日本古典籍くずし字データセット.

The fixtures are built in `tmp_path`: a book list of three books, an NIJL manifest for one of them,
and per-book zips holding one or two page images and a coordinate CSV. The cache is pointed at
`tmp_path` through `GLYPH_ATLAS_CACHE`, the book list and the split file at fixtures, so nothing
is read from `cache/` and no request is made. The wait between the two size samples of a download is
set to zero, except in the test that watches a zip grow.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from glyph_atlas import images, refs, tables
from glyph_atlas.importers import codh_all
from glyph_atlas.schema import Classification, Production, ReviewState, UnitKind

PAGE_SIZE = (120, 200)
COLUMNS = ["Unicode", "Image", "X", "Y", "Block ID", "Char ID", "Width", "Height"]
#: The book list of the fixtures: one woodblock book, one manuscript and one that has no zip.
BOOKS = [
    ("900000001", "試しの本", "12", "2", "2019-01", "刊", "woodblock", "国文研", "天保５"),
    ("900000002", "例の写本", "3", "1", "2019-11", "写", "manuscript", "国文研貴重書", ""),
    ("900000003", "無い本", "1", "1", "2019-11", "刊", "woodblock", "", ""),
]
#: 漢 and か on one page of the first book, 々 on the one page of the second.
ROWS = {
    "900000001": [
        ["U+6F22", "900000001_00003_1", 10, 20, "B0001", "C0001", 30, 40],
        ["U+304B", "900000001_00003_1", 10, 120, "B0001", "C0002", 30, 40],
    ],
    "900000002": [["U+3005", "900000002_00001_2", 5, 5, "B0001", "C0001", 20, 20]],
}
#: A note the annotators left on a spot of the first page of the first book.
REPORTS = {"900000001": [["900000001_00003_1", 500, 600, "虫損"]]}


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the image cache at `tmp_path`, so no test reads or writes `cache/`."""
    root = tmp_path / "cache"
    monkeypatch.setenv(images.ENV_CACHE, str(root))
    return root


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the two size samples of a download back to back; the growing-zip test sets its own."""
    monkeypatch.setattr(codh_all, "SETTLE", 0.0)


@pytest.fixture
def books_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A book list of three books, in place of `data/sources/codh-books.tsv`."""
    path = tmp_path / "codh-books.tsv"
    path.write_text(book_list_text(BOOKS), encoding="utf-8")
    monkeypatch.setattr(codh_all, "BOOKS_FILE", path)
    return path


@pytest.fixture
def splits_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An evaluation split of the three fixture books, in place of `data/splits/codh.tsv`."""
    path = tmp_path / "codh.tsv"
    path.write_text(
        "# bid\tproduction\tsplit\n"
        "900000001\twoodblock\ttrain\n"
        "900000002\tmanuscript\ttest\n"
        "900000003\twoodblock\tval\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codh_all, "SPLITS_FILE", path)
    return path


def book_list_text(books: list[tuple[str, ...]]) -> str:
    header = "# " + "\t".join(codh_all.COLUMNS) + "\n# source: fixture\n"
    return header + "".join("\t".join(book) + "\n" for book in books)


def jpeg(size: tuple[int, int] = PAGE_SIZE) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def csv_text(rows: list[list], columns: list[str] = COLUMNS) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue()


def make_zip(
    folder: Path,
    bid: str,
    *,
    images: tuple[str, ...] | None = None,
    rows: list[list] | None = None,
    reports: list[list] | None = None,
) -> Path:
    """One fixture zip under `folder`: page images, the two CSVs and a crop directory."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{bid}.zip"
    coordinates = ROWS[bid] if rows is None else rows
    names = tuple(sorted({row[1] for row in coordinates})) if images is None else images
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(f"{bid}/images/{name}.jpg", jpeg())
        archive.writestr(f"{bid}/{bid}_coordinate.csv", csv_text(coordinates))
        if reports is not None:
            archive.writestr(f"{bid}/{bid}_report.csv", csv_text(reports, ["Image", "X", "Y", "Report"]))
        archive.writestr(f"{bid}/characters/U+6F22/{bid}_crop.jpg", jpeg((8, 8)))
    return path


@pytest.fixture
def zips(cache: Path) -> Path:
    """The two fixture zips that are in the cache; the third book has none."""
    folder = cache / codh_all.ZIP_DIR
    make_zip(folder, "900000001", images=("900000001_00003_1", "900000001_00004_1"), reports=REPORTS["900000001"])
    make_zip(folder, "900000002")
    return folder


@pytest.fixture
def manifest(cache: Path) -> Path:
    """The NIJL manifest of the first book; the other two carry none."""
    folder = cache / codh_all.MANIFEST_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "900000001.json"
    metadata = [
        {"label": "DC.title", "value": "試しの本"},
        {"label": "DCTERMS.relation", "value": "国文研"},
    ]
    path.write_text(json.dumps({"metadata": metadata}, ensure_ascii=False), encoding="utf-8")
    return path


def fixture_import(tmp_path: Path, **options) -> dict[str, int]:
    """Import the fixture books into `tmp_path/out`."""
    return codh_all.import_all(tmp_path / "out", **options)


def test_two_books_merge_into_one_directory(tmp_path, cache, books_file, zips, manifest):
    counts = fixture_import(tmp_path, books=["900000001", "900000002"])
    assert counts == {
        "documents": 2,
        "pages": 3,
        "units": 3,
        "reports": 1,
        "kana_unassessed": 1,
        "books": 2,
    }
    dataset = tables.Dataset(tmp_path / "out")
    assert dataset.validate() == []

    documents = {document.id: document for document in dataset.read("documents")}
    assert sorted(documents) == ["codh:900000001", "codh:900000002"]
    first = documents["codh:900000001"]
    assert (first.title, first.production) == ("試しの本", Production.WOODBLOCK)
    assert first.source_refs == {"codh-char-shape": "900000001", "nijl-bid": "900000001"}
    assert first.holder == "国文研"
    assert first.image_rights.licence.value == "CC-BY-SA-4.0"
    assert first.meta["released"] == "2019-01" and first.meta["issued"] == "天保５"
    second = documents["codh:900000002"]
    assert (second.title, second.production) == ("例の写本", Production.MANUSCRIPT)
    assert second.source_refs == {"codh-char-shape": "900000002"}
    assert second.holder is None

    pages = {page.id: page for page in dataset.read("pages")}
    assert sorted(pages) == [
        "codh:900000001:900000001_00003_1",
        "codh:900000001:900000001_00004_1",
        "codh:900000002:900000002_00001_2",
    ]
    # the second page of the first book is blank: no row of its coordinate CSV names it
    blank = pages["codh:900000001:900000001_00004_1"]
    assert (blank.width, blank.height) == PAGE_SIZE and blank.seq == 7
    assert pages["codh:900000001:900000001_00003_1"].seq == 5
    assert pages["codh:900000002:900000002_00001_2"].seq == 2
    page = pages["codh:900000001:900000001_00003_1"]
    assert page.image == "https://codh.rois.ac.jp/char-shape/iiif/900000001/900000001_00003_1.tif"
    assert page.document_id == "codh:900000001" and page.transcription["entry"] == "900000001"
    # the page images are written out under `out/images`, and only the page images
    written = sorted(entry.name for entry in (tmp_path / "out" / "images").iterdir())
    assert written == ["900000001_00003_1.jpg", "900000001_00004_1.jpg", "900000002_00001_2.jpg"]

    units = {unit.id: unit for unit in dataset.read("units")}
    assert sorted(units) == [
        "codh:900000001:900000001_00003_1:B0001:C0001",
        "codh:900000001:900000001_00003_1:B0001:C0002",
        "codh:900000001:900000001_00003_1:report:1",
        "codh:900000002:900000002_00001_2:B0001:C0001",
    ]
    kanji = units["codh:900000001:900000001_00003_1:B0001:C0001"]
    assert kanji.box.iiif_region() == "10,20,30,40"
    assert kanji.unicode == "U+6F22" and kanji.text_source == "漢" and kanji.reading == "漢"
    assert kanji.page_id == "codh:900000001:900000001_00003_1" and kanji.document_id == "codh:900000001"
    assert kanji.method == "import" and kanji.review is ReviewState.TRANSCRIBER and kanji.active
    assert kanji.upstream == {
        "source": "codh-char-shape",
        "ref": "900000001/900000001_00003_1/B0001/C0001",
        "block": "B0001",
        "identity_basis": "normalized_transcription",
        "source_code_point": "U+6F22",
        "normalization_evidence": "https://codh.rois.ac.jp/char-shape/#version",
    }


def test_a_kana_unit_is_unassessed_and_a_kanji_identified(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001"])
    units = {unit.id: unit for unit in tables.Dataset(tmp_path / "out").read("units")}
    kana = units["codh:900000001:900000001_00003_1:B0001:C0002"]
    assert kana.unicode == "U+304B" and kana.script.value == "hiragana"
    # か carries no 字母: the layer states one for the kana forms of a source, not for the modern
    # kana, whose derivation every reader of Japanese knows and no record of it needs.
    assert kana.classification is Classification.UNASSESSED and refs.jibo_of_unit(kana.unicode) is None
    assert units["codh:900000001:900000001_00003_1:B0001:C0001"].classification is Classification.IDENTIFIED


def test_a_report_row_becomes_an_unreadable_unit(tmp_path, cache, books_file, zips):
    counts = fixture_import(tmp_path, books=["900000001"])
    assert counts["reports"] == 1 and counts["units"] == 2
    units = {unit.id: unit for unit in tables.Dataset(tmp_path / "out").read("units")}
    report = units["codh:900000001:900000001_00003_1:report:1"]
    assert report.kind is UnitKind.UNREADABLE and report.review is ReviewState.REJECTED
    assert report.text_source is None and report.reading is None and report.unicode is None
    assert report.box is None and report.page_id == "codh:900000001:900000001_00003_1"
    assert report.document_id == "codh:900000001" and report.method == "import"
    assert report.upstream["report"] == "虫損"
    assert (report.upstream["x"], report.upstream["y"]) == ("500", "600")


def test_a_page_the_csv_names_without_an_image_is_kept_with_no_size(tmp_path, cache, books_file):
    rows = [*ROWS["900000001"], ["U+3005", "900000001_00009_2", 1, 2, "B0002", "C0001", 10, 10]]
    make_zip(cache / codh_all.ZIP_DIR, "900000001", images=("900000001_00003_1",), rows=rows)
    with pytest.warns(UserWarning, match="no image in the archive"):
        counts = fixture_import(tmp_path, books=["900000001"])
    assert counts["pages"] == 2
    pages = {page.id: page for page in tables.Dataset(tmp_path / "out").read("pages")}
    absent = pages["codh:900000001:900000001_00009_2"]
    assert (absent.width, absent.height) == (0, 0)
    assert absent.image.endswith("900000001_00009_2.tif")
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_a_box_that_reaches_past_its_page_is_cut_at_the_edge(tmp_path, cache, books_file):
    rows = [["U+6F22", "900000001_00003_1", 100, 10, "B0001", "C0001", 40, 40]]
    make_zip(cache / codh_all.ZIP_DIR, "900000001", rows=rows)
    with pytest.warns(UserWarning, match="cut at its edge"):
        fixture_import(tmp_path, books=["900000001"])
    unit = tables.Dataset(tmp_path / "out").read("units")[0]
    assert unit.box.iiif_region() == "100,10,20,40"
    assert unit.upstream["box"] == "100,10,40,40"
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_a_book_without_a_zip_is_skipped(tmp_path, cache, books_file, zips):
    with pytest.warns(UserWarning, match="not in the cache yet; 900000003 skipped"):
        counts = fixture_import(tmp_path)
    assert counts["books"] == 2 and counts["documents"] == 2
    documents = tables.Dataset(tmp_path / "out").read("documents")
    assert [document.id for document in documents] == ["codh:900000001", "codh:900000002"]


def test_a_growing_zip_is_left_for_the_next_run(tmp_path, cache, books_file, zips, monkeypatch):
    path = codh_all.zip_path("900000001")

    def grow(seconds: float) -> None:
        with path.open("ab") as handle:
            handle.write(b"0" * 4096)

    monkeypatch.setattr(codh_all, "SETTLE", 1.0)
    monkeypatch.setattr(codh_all, "_sleep", grow)
    with pytest.warns(UserWarning, match="left for the next run"):
        counts = fixture_import(tmp_path, books=["900000001", "900000002"])
    assert counts["books"] == 1 and counts["documents"] == 1
    documents = tables.Dataset(tmp_path / "out").read("documents")
    assert [document.id for document in documents] == ["codh:900000002"]


def test_limit_stops_after_that_many_books(tmp_path, cache, books_file, zips):
    counts = fixture_import(tmp_path, limit=1)
    assert counts["books"] == 1 and counts["documents"] == 1


def test_a_book_the_list_does_not_carry_is_imported_under_its_identifier(tmp_path, cache, books_file, zips):
    rows = [["U+4E00", "900000004_00001_1", 1, 1, "B0001", "C0001", 10, 10]]
    make_zip(cache / codh_all.ZIP_DIR, "900000004", rows=rows)
    counts = fixture_import(tmp_path, books=["900000004"])
    assert counts["books"] == 1
    document = tables.Dataset(tmp_path / "out").read("documents")[0]
    assert (document.id, document.title) == ("codh:900000004", "900000004")
    assert document.production is Production.UNKNOWN
    assert document.source_refs == {"codh-char-shape": "900000004"}


def test_a_second_import_writes_the_same_tables(tmp_path, cache, books_file, zips, manifest):
    first = fixture_import(tmp_path, books=["900000001", "900000002"])
    written = {name: (tmp_path / "out" / f"{name}.parquet").read_bytes() for name in ("documents", "pages", "units")}
    second = fixture_import(tmp_path, books=["900000001", "900000002"])
    assert first == second
    assert {name: (tmp_path / "out" / f"{name}.parquet").read_bytes() for name in written} == written


def test_register_images_registers_the_pages_asked_for(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001", "900000002"])
    out = tmp_path / "out"
    page_id = "codh:900000001:900000001_00003_1"
    file = out / "images" / "900000001_00003_1.jpg"
    size = file.stat().st_size

    counts = codh_all.register_images(out, pages=[page_id])
    assert (counts["pages"], counts["selected"]) == (3, 1)
    assert (counts["registered"], counts["skipped"], counts["missing"]) == (1, 0, 0)
    assert counts["bytes"] == size
    # the staged copy goes once the cache holds it; the pages that are not cached stay
    assert (counts["deleted"], counts["freed"]) == (1, size)
    assert counts["kept_files"] == 2 and counts["kept"] > 0
    assert not file.exists()

    pages = {page.id: page for page in tables.Dataset(out).read("pages")}
    page = pages[page_id]
    assert page.sha256 == hashlib.sha256(images.path_for(page.image).read_bytes()).hexdigest()
    assert (page.width, page.height) == PAGE_SIZE and images.path_for(page.image) is not None
    assert len(images.index()) == 1
    assert pages["codh:900000002:900000002_00001_2"].sha256 is None

    again = codh_all.register_images(out, pages=[page_id])
    assert (again["registered"], again["skipped"], again["missing"]) == (0, 1, 0)
    assert (again["bytes"], again["deleted"], again["freed"]) == (0, 0, 0)
    assert again["kept_files"] == 2
    assert len(images.index()) == 1


def test_register_images_takes_an_image_name_a_page_id_or_a_document_id(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001", "900000002"])
    out = tmp_path / "out"
    by_document = codh_all.register_images(out, pages=["codh:900000001"])
    assert (by_document["selected"], by_document["registered"]) == (2, 2)
    by_image = codh_all.register_images(out, pages=["900000002_00001_2"])
    assert (by_image["selected"], by_image["registered"]) == (1, 1)
    assert len(images.index()) == 3


def test_register_images_warns_about_a_page_that_is_not_in_the_table(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001"])
    with pytest.warns(UserWarning, match="not in the pages table"):
        counts = codh_all.register_images(tmp_path / "out", pages=["codh:900000009:900000009_00001_1"])
    assert (counts["pages"], counts["selected"], counts["registered"]) == (2, 0, 0)


def test_register_images_without_pages_needs_the_split_file(tmp_path, cache, books_file, zips, monkeypatch):
    fixture_import(tmp_path, books=["900000001", "900000002"])
    # the split file is not written yet, and the test must not read the one in the repository
    monkeypatch.setattr(codh_all, "SPLITS_FILE", tmp_path / "no-split.tsv")
    with pytest.warns(UserWarning, match="split file is not there"):
        counts = codh_all.register_images(tmp_path / "out")
    assert (counts["selected"], counts["registered"]) == (0, 0)


def test_register_images_defaults_to_the_split(tmp_path, cache, books_file, zips, splits_file):
    fixture_import(tmp_path, books=["900000001", "900000002"])
    names = ("900000001_00003_1.jpg", "900000001_00004_1.jpg", "900000002_00001_2.jpg")
    staged = sum((tmp_path / "out" / "images" / name).stat().st_size for name in names)
    counts = codh_all.register_images(tmp_path / "out")
    # every page of the test and validation books and of the first train book
    assert (counts["selected"], counts["registered"], counts["skipped"]) == (3, 3, 0)
    assert counts["bytes"] == staged == counts["freed"]
    assert (counts["deleted"], counts["kept_files"], counts["kept"]) == (3, 0, 0)
    assert len(images.index()) == 3


def test_a_reread_does_not_stage_an_image_the_cache_holds(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001"])
    out = tmp_path / "out"
    page_id = "codh:900000001:900000001_00003_1"
    codh_all.register_images(out, pages=[page_id])
    assert not (out / "images" / "900000001_00003_1.jpg").exists()

    counts = fixture_import(tmp_path, books=["900000001"])
    assert counts["books"] == 1
    assert not (out / "images" / "900000001_00003_1.jpg").exists()
    page = {page.id: page for page in tables.Dataset(out).read("pages")}[page_id]
    assert (page.width, page.height) == PAGE_SIZE
    assert page.sha256 is not None


def test_register_images_counts_a_missing_image(tmp_path, cache, books_file):
    rows = [*ROWS["900000001"], ["U+3005", "900000001_00009_2", 1, 2, "B0002", "C0001", 10, 10]]
    make_zip(cache / codh_all.ZIP_DIR, "900000001", images=("900000001_00003_1",), rows=rows)
    with pytest.warns(UserWarning, match="no image in the archive"):
        fixture_import(tmp_path, books=["900000001"])
    counts = codh_all.register_images(tmp_path / "out", pages=["codh:900000001"])
    assert (counts["selected"], counts["registered"], counts["missing"]) == (2, 1, 1)


def test_register_images_stops_at_the_limit(tmp_path, cache, books_file, zips):
    fixture_import(tmp_path, books=["900000001"])
    counts = codh_all.register_images(tmp_path / "out", pages=["codh:900000001"], limit=1)
    assert (counts["registered"], len(images.index())) == (1, 1)


def test_import_all_with_register_runs_the_image_pass(tmp_path, cache, books_file, zips, splits_file):
    counts = fixture_import(tmp_path, books=["900000001"], register=True)
    assert (counts["registered"], counts["selected"]) == (2, 2)
    assert counts["bytes"] > 0 and len(images.index()) == 2


def test_register_images_needs_the_pages_table(tmp_path, cache, books_file):
    with pytest.raises(FileNotFoundError, match="no pages table"):
        codh_all.register_images(tmp_path / "out")


def test_splits_file_is_read_by_its_header(tmp_path, monkeypatch):
    path = tmp_path / "codh.tsv"
    path.write_text(
        "# CODH split by book.\n"
        "bid\ttitle\tproduction\tsplit\n"
        "900000001\t試しの本\twoodblock\ttrain\n"
        "900000002\t例の写本\tmanuscript\ttest\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codh_all, "SPLITS_FILE", path)
    assert codh_all.split_bids() == [("900000001", "train"), ("900000002", "test")]


def test_book_list_reads_the_columns_of_the_tsv(tmp_path, books_file):
    books = codh_all.book_list()
    assert [book.bid for book in books] == [row[0] for row in BOOKS]
    first = books[0]
    assert (first.title, first.production, first.expected) == ("試しの本", Production.WOODBLOCK, 2)
    assert (first.collection, first.issued, first.kind) == ("国文研", "天保５", "刊")
    assert books[2].expected == 1
    assert codh_all.unlisted("900000009").production is Production.UNKNOWN
