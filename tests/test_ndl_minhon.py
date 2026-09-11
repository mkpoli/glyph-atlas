"""Tests of the NDL古典籍OCR学習用データセット importer.

A fixture of one page per version with one metadata row each, built in `tmp_path`: the v1 page is a
bare list of rows, the v2 page an object with `words`, and the v1 page carries a row that is not a
textline. No test reaches the network or reads `cache/`.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from glyph_atlas import tables
from glyph_atlas.importers import ndl_minhon
from glyph_atlas.schema import Box, Document, Licence, Line, Page

V1_BOOK = "L000001"
V1_IMAGE_ID = "000005725"
V1_IMAGE = "http://example.test/G0000002erilib/000/005/000005725.jpg"
V2_PROJECT = "kusazoushi"
V2_BOOK = "9D61092EC482751E687C188D44347857"
V2_PAGE = "001"
V2_IMAGE = "https://www.dl.ndl.go.jp/api/iiif/10301810/R0000001/full/full/0/default.jpg"
V2_SERVICE = "https://www.dl.ndl.go.jp/api/iiif/10301810/R0000001"
NDL_LICENCE = "https://dl.ndl.go.jp/ja/iiif_license.html"

V1_ID = f"ndl-minhon:v1:{V1_BOOK}"
V1_PAGE_ID = f"{V1_ID}:{V1_IMAGE_ID}"
V2_ID = f"ndl-minhon:v2:{V2_PROJECT}:{V2_BOOK}"
V2_PAGE_ID = f"{V2_ID}:{V2_PAGE}"


def box(x: int, y: int, w: int, h: int) -> list[list[int]]:
    """The four corners of a rectangle, in the order the archive states them."""
    return [[x, y], [x, y + h], [x + w, y], [x + w, y + h]]


def row(ident: int, text: str, points: list[list[int]], *, textline: str = "true", vertical: str = "true") -> dict:
    """One page JSON row, with the flags as the strings the archive uses."""
    return {
        "boundingBox": points,
        "id": ident,
        "isVertical": vertical,
        "text": text,
        "isTextline": textline,
        "confidence": 1,
    }


# The v1 page in file order: the left column, the right one, and a row that is not a textline whose
# box stands furthest right, so that keeping it would change the reading order of the other two.
V1_ROWS = [
    row(1, "左の列", box(500, 100, 80, 400)),
    row(9, "表紙", box(2000, 100, 60, 200), textline="false"),
    row(2, "右の列", box(1000, 100, 80, 400)),
]

# The v2 page: two vertical columns, the right one read first, and 振り仮名 that `koji` removes.
V2_ROWS = [
    row(8, "はなしで（わ）聞ました。", box(3805, 631, 125, 2260)),
    row(2, "入道は。", box(3653, 629, 157, 2258)),
]

V1_CSV = (
    "\ufeffBook ID,Book Name,Attribution,File ID(Minna De Honkoku),File ID(NDL),Image URL,GitHub URL\r\n"
    f"{V1_BOOK},地震年代記,東京大学地震研究所図書室,001,{V1_IMAGE_ID},{V1_IMAGE},"
    f"https://github.com/yuta1984/honkoku-data/tree/master/v1/{V1_BOOK}\r\n"
)

# v2 names its columns with tabs, spells one of them in lower case and pads two headers with a space.
V2_CSV = (
    "Project ID \tBook ID\tBook Name \tattribution\tFile ID(Minna De Honkoku)\tImage URL\tGitHub URL\r\n"
    f"{V2_PROJECT}\t{V2_BOOK}\t化物世帯氣質\t国立国会図書館 National Diet Library\t{V2_PAGE}\t{V2_IMAGE}\t"
    f"https://github.com/yuta1984/honkoku-data/tree/master/v2/{V2_PROJECT}/{V2_BOOK}\r\n"
)


def fixture(
    tmp_path: Path, *, v1_rows: list[dict] | None = None, v2_rows: list[dict] | None = None
) -> Path:
    """Write the unpacked tree and the two metadata CSVs beside it; return the tree's root."""
    root = tmp_path / ndl_minhon.ROOT
    (root / "v1" / V1_BOOK).mkdir(parents=True)
    (root / "v2" / V2_PROJECT / V2_BOOK).mkdir(parents=True)
    (root / "v1" / V1_BOOK / f"{V1_IMAGE_ID}.json").write_text(
        json.dumps(V1_ROWS if v1_rows is None else v1_rows, ensure_ascii=False), encoding="utf-8"
    )
    (root / "v2" / V2_PROJECT / V2_BOOK / f"{V2_PAGE}.json").write_text(
        json.dumps({"words": V2_ROWS if v2_rows is None else v2_rows}, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / ndl_minhon.V1_METADATA).write_text(V1_CSV, encoding="utf-8")
    (tmp_path / ndl_minhon.V2_METADATA).write_text(V2_CSV, encoding="utf-8")
    return root


def archive(tmp_path: Path) -> Path:
    """Zip the fixture's tree under the release's top directory, as the upstream archive does."""
    root = tmp_path / ndl_minhon.ROOT
    path = tmp_path / ndl_minhon.ARCHIVE
    with zipfile.ZipFile(path, "w") as bundle:
        for file in sorted(root.rglob("*.json")):
            bundle.write(file, f"{root.name}/{file.relative_to(root).as_posix()}")
    return path


def test_import_writes_both_versions_with_reading_order_and_rights(tmp_path):
    counts = ndl_minhon.import_all(tmp_path / "out", unpacked=fixture(tmp_path))

    assert counts == {
        "documents": 2,
        "pages": 2,
        "lines": 4,
        "v1_documents": 1,
        "v2_documents": 1,
        "v1_pages": 1,
        "v2_pages": 1,
        "v1_lines": 2,
        "v2_lines": 2,
        "skipped": 1,
        "unknown_url": 0,
    }

    documents = {record.id: record for record in tables.read(tmp_path / "out" / "documents.parquet", Document)}
    assert set(documents) == {V1_ID, V2_ID}
    assert documents[V1_ID].title == "地震年代記"
    assert documents[V1_ID].holder == "東京大学地震研究所図書室"
    assert documents[V1_ID].source_refs == {ndl_minhon.SOURCE: f"v1/{V1_BOOK}", "honkoku-data": V1_BOOK}
    assert documents[V1_ID].meta["github"].endswith(f"/v1/{V1_BOOK}")
    assert documents[V2_ID].title == "化物世帯氣質"
    assert documents[V2_ID].holder == "国立国会図書館 National Diet Library"
    assert documents[V2_ID].source_refs == {
        ndl_minhon.SOURCE: f"v2/{V2_PROJECT}/{V2_BOOK}",
        "honkoku-data": f"{V2_PROJECT}/{V2_BOOK}",
    }

    pages = {record.id: record for record in tables.read(tmp_path / "out" / "pages.parquet", Page)}
    assert set(pages) == {V1_PAGE_ID, V2_PAGE_ID}
    assert pages[V1_PAGE_ID].seq == int(V1_IMAGE_ID)
    assert pages[V1_PAGE_ID].image == V1_IMAGE
    assert pages[V1_PAGE_ID].width == 0 and pages[V1_PAGE_ID].height == 0
    assert pages[V1_PAGE_ID].transcription["source"] == ndl_minhon.SOURCE
    assert pages[V2_PAGE_ID].seq == int(V2_PAGE)
    assert pages[V2_PAGE_ID].image == V2_SERVICE

    lines = {record.id: record for record in tables.read(tmp_path / "out" / "lines.parquet", Line)}
    assert set(lines) == {f"{V1_PAGE_ID}:1", f"{V1_PAGE_ID}:2", f"{V2_PAGE_ID}:2", f"{V2_PAGE_ID}:8"}
    right, left = lines[f"{V1_PAGE_ID}:2"], lines[f"{V1_PAGE_ID}:1"]
    assert (right.seq, left.seq) == (0, 1)
    assert right.box == Box(x=1000, y=100, w=80, h=400)
    assert right.meta["points"] == box(1000, 100, 80, 400)
    assert right.meta["upstream_id"] == "2"
    assert right.meta["confidence"] == 1
    assert right.vertical is True and right.match_method == ndl_minhon.MATCH_METHOD
    assert right.text_raw == "右の列" and right.text == "右の列"
    assert (lines[f"{V2_PAGE_ID}:8"].seq, lines[f"{V2_PAGE_ID}:2"].seq) == (0, 1)
    assert lines[f"{V2_PAGE_ID}:8"].text_raw == "はなしで（わ）聞ました。"
    assert lines[f"{V2_PAGE_ID}:8"].text == "はなしで聞ました。"

    assert documents[V2_ID].image_rights.licence is Licence.PDM
    assert documents[V2_ID].image_rights.evidence == NDL_LICENCE
    assert documents[V1_ID].image_rights.licence is Licence.UNKNOWN
    assert documents[V1_ID].image_rights.evidence is None
    for document in documents.values():
        assert document.text_rights.licence is Licence.CC_BY_SA_4


def test_repeated_upstream_id_keeps_every_row_and_a_unique_line_id(tmp_path):
    rows = [
        row(4, "同九百七十関東大ぢしん", box(100, 100, 80, 400)),
        row(7, "地震年代記", box(300, 100, 80, 400)),
        row(4, "同五百六十三人多く死ス", box(100, 100, 80, 400)),
    ]
    counts = ndl_minhon.import_all(tmp_path / "out", unpacked=fixture(tmp_path, v1_rows=rows))

    assert counts["v1_lines"] == 3
    lines = tables.read(tmp_path / "out" / "lines.parquet", Line)
    ordered = sorted((line for line in lines if line.page_id == V1_PAGE_ID), key=lambda line: line.seq)
    assert [line.id for line in ordered] == [f"{V1_PAGE_ID}:7", f"{V1_PAGE_ID}:4", f"{V1_PAGE_ID}:4:2"]
    assert [line.meta["upstream_id"] for line in ordered] == ["7", "4", "4"]
    assert [line.text for line in ordered] == ["地震年代記", "同九百七十関東大ぢしん", "同五百六十三人多く死ス"]


def test_import_reads_the_same_records_from_the_zip(tmp_path):
    fixture(tmp_path)
    counts = ndl_minhon.import_all(tmp_path / "out", zip_path=archive(tmp_path))

    assert counts["documents"] == 2 and counts["pages"] == 2 and counts["lines"] == 4
    assert counts["v1_lines"] == 2 and counts["v2_lines"] == 2 and counts["skipped"] == 1
    pages = tables.read(tmp_path / "out" / "pages.parquet", Page)
    assert sorted(page.id for page in pages) == [V1_PAGE_ID, V2_PAGE_ID]
    lines = tables.read(tmp_path / "out" / "lines.parquet", Line)
    assert lines[0].box == Box(x=1000, y=100, w=80, h=400)


def test_rectangle_without_area_keeps_the_points_and_carries_no_box(tmp_path):
    rows = [
        row(1, "左の列", box(500, 100, 80, 400)),
        row(4, "吐利不已", [[0, 1206], [0, 2000], [0, 1206], [0, 2000]]),
    ]
    ndl_minhon.import_all(tmp_path / "out", unpacked=fixture(tmp_path, v1_rows=rows))

    lines = {line.id: line for line in tables.read(tmp_path / "out" / "lines.parquet", Line)}
    flat = lines[f"{V1_PAGE_ID}:4"]
    assert flat.box is None and flat.text_raw == "吐利不已" and flat.text == "吐利不已"
    assert flat.meta["points"] == [[0, 1206], [0, 2000], [0, 1206], [0, 2000]]
    assert (lines[f"{V1_PAGE_ID}:1"].seq, flat.seq) == (0, 1)


def test_page_file_without_a_metadata_row_keeps_an_unknown_url(tmp_path):
    root = fixture(tmp_path)
    header = "Project ID \tBook ID\tBook Name \tattribution\tFile ID(Minna De Honkoku)\tImage URL\tGitHub URL\r\n"
    (tmp_path / ndl_minhon.V2_METADATA).write_text(header, encoding="utf-8")

    with pytest.warns(ndl_minhon.MissingMetadataWarning):
        counts = ndl_minhon.import_all(tmp_path / "out", unpacked=root)

    assert counts["unknown_url"] == 1 and counts["v2_pages"] == 1 and counts["v2_lines"] == 2
    pages = {page.id: page for page in tables.read(tmp_path / "out" / "pages.parquet", Page)}
    assert pages[V2_PAGE_ID].image == "unknown" and pages[V2_PAGE_ID].meta["url_unknown"] is True
    documents = {record.id: record for record in tables.read(tmp_path / "out" / "documents.parquet", Document)}
    assert documents[V2_ID].title == V2_BOOK
    assert documents[V2_ID].holder is None and documents[V2_ID].image_rights.licence is Licence.UNKNOWN
