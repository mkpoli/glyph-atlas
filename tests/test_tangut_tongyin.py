import csv
import hashlib
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from glyph_atlas import tables
from glyph_atlas.cli import app
from glyph_atlas.importers import tangut_tongyin
from glyph_atlas.schema import Document, Licence, Line, Page, Unit

DATA = tangut_tongyin.DEFAULT_DATA
HEADER = "# a table of the fixture\n"


def table(path: Path, fields: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(HEADER)
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)


@pytest.fixture
def fixture(tmp_path):
    """One scanned page with one plate, one column and three placed characters."""
    scans, data = tmp_path / "scans", tmp_path / "data"
    scans.mkdir()
    data.mkdir()
    scan = scans / "00000040.jpg"
    Image.new("L", (400, 600), 200).save(scan)
    digest = hashlib.sha256(scan.read_bytes()).hexdigest()
    table(data / "pages.tsv", ["page", "file", "sha256", "bytes", "width", "height", "folios", "plates"],
          [[1, scan.name, digest, scan.stat().st_size, 400, 600, "2", "10,10,380,280"]])
    table(data / "columns.tsv", ["folio", "side", "column", "page", "x", "y", "w", "h", "text"],
          [[2, "A", 5, 1, 300, 20, 40, 260, "𗩤𗠜𗠪"]])
    table(data / "characters.tsv",
          ["folio", "side", "column", "position", "char", "codepoint", "page", "x", "y", "w", "h", "score", "ink",
           "quality"],
          [[2, "A", 5, 1, "𗩤", "U+17A64", 1, 302, 24, 30, 32, 0.41, 70.2, "ok"],
           [2, "A", 5, 2, "𗠜", "U+1781C", 1, 303, 60, 30, 31, 0.19, 64.0, "low-score"],
           [2, "A", 5, 3, "𗠪", "U+1782A", 1, 301, 96, 12, 30, 0.33, 61.5, "size"]])
    return scans, data


def test_collect_writes_a_unit_for_each_sound_place(fixture, tmp_path):
    scans, data = fixture
    out = tmp_path / "out"
    summary = tangut_tongyin.collect(scans, out, data=data, cache=tmp_path / "cache")

    assert (summary["pages"], summary["lines"], summary["units"], summary["characters"]) == (1, 1, 1, 3)
    assert summary["left_out"] == {"low-score": 1, "size": 1}
    document = next(iter(tables.read(out / "documents.parquet", Document)))
    assert document.id == "ecang-heishuicheng:inv-207"
    assert (document.title, document.shelfmark) == ("音同（甲種本）", "Инв. No. 207")
    assert (document.production, document.origin, document.genre) == ("printed/woodblock", "china", ["dictionary"])
    # A plate reproduces a Western Xia print: public domain, with the publisher's terms kept beside it.
    assert (document.image_rights.licence, document.image_rights.holder_terms) == (
        Licence.PUBLIC_DOMAIN, Licence.RESTRICTED)
    assert "Maxim Persikov" in document.image_rights.attribution
    assert document.text_rights.licence == Licence.PUBLIC_DOMAIN

    page = next(iter(tables.read(out / "pages.parquet", Page)))
    assert page.id == "ecang-heishuicheng:7:p001"
    assert (page.width, page.height, page.meta["folios"], page.meta["plates"]) == (400, 600, [2], [[10, 10, 380, 280]])
    assert page.image.startswith("file:") and page.sha256
    line = next(iter(tables.read(out / "lines.parquet", Line)))
    assert (line.id, line.text, line.page_id) == ("ecang-heishuicheng:inv-207:02A5", "𗩤𗠜𗠪", page.id)
    unit = next(iter(tables.read(out / "units.parquet", Unit)))
    assert unit.id == "ecang-heishuicheng:inv-207:02A51"
    assert (unit.text_source, unit.unicode, unit.line_id, unit.seq) == ("𗩤", "U+17A64", line.id, 0)
    assert (unit.box.x, unit.box.y, unit.box.w, unit.box.h) == (302, 24, 30, 32)
    assert unit.confidence.text == pytest.approx(0.41)
    assert unit.upstream == {"nkay0-tangut-rhyme": "旧版同音 02A51"}


def test_collect_refuses_a_scan_that_is_not_the_pinned_one(fixture, tmp_path):
    scans, data = fixture
    Image.new("L", (400, 600), 90).save(scans / "00000040.jpg")
    with pytest.raises(tangut_tongyin.TongyinSourceError, match="not the scan"):
        tangut_tongyin.collect(scans, tmp_path / "out", data=data, cache=tmp_path / "cache")
    assert not (tmp_path / "out" / "units.parquet").exists()


def test_command_reports_a_missing_scan(tmp_path):
    result = CliRunner().invoke(app, ["import", "tangut-tongyin", str(tmp_path), "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "is not there" in result.output


def test_the_placement_tables_agree_with_each_other():
    pages = {row["page"]: row for row in tangut_tongyin.read_tsv(DATA / "pages.tsv")}
    columns = tangut_tongyin.read_tsv(DATA / "columns.tsv")
    characters = tangut_tongyin.read_tsv(DATA / "characters.tsv")

    assert len(pages) == 28
    places = [tangut_tongyin.place_id(row) for row in characters]
    assert len(set(places)) == len(places) == 5673
    # 同音 enters each character once.
    assert len({row["char"] for row in characters}) == len(characters)
    assert Counter(row["quality"] for row in characters).keys() == {"ok", "low-score", "size", "faint"}
    text = {tangut_tongyin.place_id(row): row["text"] for row in columns}
    held: dict[str, list[tuple[int, str]]] = {}
    for row in characters:
        assert tangut_tongyin.is_ideograph(row["char"])
        assert row["codepoint"] == f"U+{ord(row['char']):04X}"
        # A column holds eight head characters; the table gives 19B6 a ninth.
        assert 1 <= int(row["column"]) <= 7 and 1 <= int(row["position"]) <= 9 and row["side"] in "AB"
        page = pages[row["page"]]
        assert str(int(row["folio"])) in page["folios"].split()
        assert 0 <= int(row["x"]) and int(row["x"]) + int(row["w"]) <= int(page["width"])
        assert 0 <= int(row["y"]) and int(row["y"]) + int(row["h"]) <= int(page["height"])
        held.setdefault(tangut_tongyin.place_id({**row, "position": ""}), []).append((int(row["position"]), row["char"]))
    assert {column: "".join(char for _, char in sorted(found)) for column, found in held.items()} == text
