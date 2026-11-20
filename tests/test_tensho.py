import csv
import hashlib
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from glyph_atlas import ngrams, tables
from glyph_atlas.cli import app
from glyph_atlas.importers import tensho
from glyph_atlas.schema import Document, Licence, Line, Page, Script, Unit

FIELDS = ["seq", "image", "canvas", "column", "x", "y", "w", "h", "parent", "parent_code_point", "char",
          "code_point", "ccz", "equivalent", "placed", "sha256"]
CANVAS = "http://kanji.zinbun.kyoto-u.ac.jp/db-machine/toho/L/A0280017.jpg"


def table(path: Path, fields: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# a table of the fixture\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)


@pytest.fixture
def fixture(tmp_path):
    """One page at half the width of CODH's image, with three boxes in two columns, two of them placed."""
    scans, data = tmp_path / "scans", tmp_path / "data" / "TE00039"
    scans.mkdir()
    data.mkdir(parents=True)
    scan = scans / "A0280017.jpg"
    Image.new("L", (400, 300), 200).save(scan)
    digest = hashlib.sha256(scan.read_bytes()).hexdigest()
    table(data / "pages.tsv", ["page", "canvas", "file", "sha256", "bytes", "width", "height"],
          [[17, CANVAS, scan.name, digest, scan.stat().st_size, 400, 300]])
    table(data / "characters.tsv", FIELDS, [
        [5, "TE00039_0001_1", CANVAS, 1, 700, 100, 41, 61, "吏", "U+540F", "\U0003D005", "U+3D005", "C-00006", "叓",
         "variant", "a" * 64],
        [6, "TE00039_0001_1", CANVAS, 1, 702, 300, 40, 60, "上", "U+4E0A", "\U0003D006", "U+3D006", "C-00007", "丄",
         "variant", "b" * 64],
        [9, "TE00039_0001_1", CANVAS, 2, 500, 100, 40, 60, "旁", "U+65C1", "", "", "", "", "", "c" * 64],
    ])
    record = yaml.safe_load(tensho.DEFAULT_SOURCE.read_text(encoding="utf-8"))
    record["books"][0].update(crops=3, codh_page_width=800)
    source = tmp_path / "codh-tensho.yaml"
    source.write_text(yaml.safe_dump(record, allow_unicode=True), encoding="utf-8")
    return scans, data.parent, source


def collect(fixture, tmp_path: Path) -> dict:
    scans, data, source = fixture
    return tensho.collect(scans, tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


def test_collect_puts_each_placed_form_on_its_page(fixture, tmp_path):
    summary = collect(fixture, tmp_path)

    assert (summary["pages"], summary["lines"], summary["units"], summary["left_out"]) == (1, 1, 2, 1)
    page = next(iter(tables.read(tmp_path / "out" / "pages.parquet", Page)))
    assert (page.id, page.image, page.width, page.height) == ("codh-tensho:TE00039:p0017", CANVAS, 400, 300)
    assert page.seq == 16
    line = next(iter(tables.read(tmp_path / "out" / "lines.parquet", Line)))
    assert (line.id, line.text, line.vertical) == ("codh-tensho:TE00039:p0017:c01", "\U0003D005\U0003D006", True)
    assert line.box.model_dump() == {"x": 350, "y": 50, "w": 21, "h": 130}
    first = next(iter(tables.read(tmp_path / "out" / "units.parquet", Unit)))
    assert (first.id, first.unicode, first.text_source) == ("codh-tensho:TE00039:00005", "U+3D005", "吏")
    assert (first.page_id, first.line_id, first.seq, first.crop) == (page.id, line.id, None, None)
    # CODH's box 700,100,41,61 on an image twice as wide as the page.
    assert first.box.model_dump() == {"x": 350, "y": 50, "w": 20, "h": 30}
    assert (first.script, first.style) == (Script.SEAL, "seal")
    assert first.upstream == {
        "source": "codh-tensho", "ref": "U+540F_TE00039_0001_1_X0700_Y0100", "parent": "吏", "equivalent": "叓",
        "ccz": "C-00006", "placed": "variant", "box": "700,100,41,61"}
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_the_document_states_whose_the_images_and_the_boxes_are(fixture, tmp_path):
    collect(fixture, tmp_path)

    document = next(iter(tables.read(tmp_path / "out" / "documents.parquet", Document)))
    assert (document.production, document.origin, document.genre) == ("printed/woodblock", "china", ["dictionary"])
    assert (document.dating[0].start, document.dating[0].kind) == (1873, "publication")
    assert (document.image_rights.licence, document.image_rights.holder_terms) == (
        Licence.PUBLIC_DOMAIN, Licence.UNKNOWN)
    assert "東方學デジタル圖書館（京都大学人文科学研究所）" in document.image_rights.attribution
    assert document.text_rights.licence is Licence.CC_BY_SA_4
    assert "doi:10.20676/00000390" in document.text_rights.attribution
    assert set(document.source_refs) == {"codh-tensho", "unicode-ucd"}


def test_marked_forms_of_a_column_make_no_run(fixture, tmp_path):
    collect(fixture, tmp_path)

    units = list(tables.read(tmp_path / "out" / "units.parquet", Unit))
    # The entries stand between the marked forms, so the two are no sequence of the text.
    assert ngrams.adjacent_ngrams(units) == []


def test_collect_refuses_another_page_image(fixture, tmp_path):
    scans, data, source = fixture
    Image.new("L", (400, 300), 100).save(scans / "A0280017.jpg")

    with pytest.raises(tensho.TenshoSourceError, match="not the image"):
        tensho.collect(scans, tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


def test_collect_refuses_a_code_point_that_is_not_the_character(fixture, tmp_path):
    scans, data, source = fixture
    held = data / "TE00039" / "characters.tsv"
    held.write_text(held.read_text(encoding="utf-8").replace("U+3D005", "U+0041"), encoding="utf-8")

    with pytest.raises(tensho.TenshoSourceError, match="not the code point"):
        tensho.collect(scans, tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


def test_the_table_places_each_seal_character_once_and_again_only_on_a_repeat():
    rows = tensho.read_tsv(tensho.DEFAULT_DATA / "TE00039" / "characters.tsv")
    placed = [row for row in rows if row["placed"]]
    own = [row for row in placed if row["placed"] != "repeat"]

    assert len(rows) == 10403
    assert {row["placed"] for row in placed} == {"label", "variant", "order", "repeat"}
    assert len({row["code_point"] for row in own}) == len(own)
    assert {row["code_point"] for row in placed if row["placed"] == "repeat"} <= {row["code_point"] for row in own}
    assert all(ord(row["char"]) in tensho.SEAL and row["code_point"] == f"U+{ord(row['char']):04X}" for row in placed)
    assert [int(row["ccz"][2:]) for row in placed] == sorted(int(row["ccz"][2:]) for row in placed)


def test_a_crop_takes_the_head_of_an_entry_over_its_other_forms():
    rows = {row["seq"]: row for row in tensho.read_tsv(tensho.DEFAULT_DATA / "TE00039" / "characters.tsv")}

    assert (rows["8785"]["parent"], rows["8785"]["code_point"]) == ("民", "U+3F54D")
    assert (rows["9"]["parent"], rows["9"]["code_point"], rows["9"]["placed"]) == ("旁", "U+3D00B", "variant")


def test_every_page_of_a_crop_is_pinned():
    folder = tensho.DEFAULT_DATA / "TE00039"
    pages = {row["canvas"] for row in tensho.read_tsv(folder / "pages.tsv")}

    assert len(pages) == 627
    assert {row["canvas"] for row in tensho.read_tsv(folder / "characters.tsv")} == pages


def test_the_command_reports_what_it_wrote(fixture, tmp_path, monkeypatch):
    scans, data, source = fixture
    monkeypatch.setattr(tensho, "DEFAULT_DATA", data)
    monkeypatch.setattr(tensho, "DEFAULT_SOURCE", source)

    result = CliRunner().invoke(app, ["import", "tensho", str(scans), "--out", str(tmp_path / "out"),
                                      "--cache", str(tmp_path / "cache")])

    assert result.exit_code == 0, result.output
    assert "units" in result.output and "2" in result.output


def test_a_box_over_the_edge_is_cut_back_to_the_page(fixture, tmp_path):
    scans, data, source = fixture
    held = data / "TE00039" / "characters.tsv"
    held.write_text(held.read_text(encoding="utf-8").replace("\t700\t100\t41\t61\t", "\t-10\t-4\t41\t61\t"),
                    encoding="utf-8")

    tensho.collect(scans, tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")

    first = next(iter(tables.read(tmp_path / "out" / "units.parquet", Unit)))
    assert first.box.model_dump() == {"x": 0, "y": 0, "w": 16, "h": 28}
    assert tables.Dataset(tmp_path / "out").validate() == []
