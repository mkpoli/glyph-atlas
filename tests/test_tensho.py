import csv
import hashlib
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from glyph_atlas import ngrams, registry, tables
from glyph_atlas.cli import app
from glyph_atlas.importers import tensho
from glyph_atlas.schema import Document, Licence, Line, Page, ReviewState, Script, Unit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_tensho_equivalents

FIELDS = ["seq", "image", "canvas", "column", "x", "y", "w", "h", "parent", "parent_code_point", "char",
          "code_point", "ccz", "equivalent", "placed", "sha256"]
CANVAS = "http://kanji.zinbun.kyoto-u.ac.jp/db-machine/toho/L/A0280017.jpg"
NDL_CANVAS = "https://dl.ndl.go.jp/api/iiif/1181893/canvas/3"
NDL_SERVICE = "https://dl.ndl.go.jp/api/iiif/1181893/R0000003"
VOLUMES = {"TE00026": 3561, "TE00025": 4018, "TE00024": 3795}


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
    # A volume on IIIF pages: a form on the one seal character of its 親字, one that stays on its 親字,
    # and one whose 親字 has no code point.
    volume = tmp_path / "data" / "TE00024"
    volume.mkdir()
    table(volume / "pages.tsv", ["page", "canvas", "image", "width", "height"],
          [[3, NDL_CANVAS, NDL_SERVICE, 5199, 3979]])
    table(volume / "characters.tsv", FIELDS, [
        [1, "TE00024_0001_1", NDL_CANVAS, 1, 2161, 1330, 148, 245, "一", "U+4E00", "\U0003D000", "U+3D000", "", "一",
         "equivalent", "d" * 64],
        [2, "TE00024_0001_1", NDL_CANVAS, 1, 2165, 1700, 150, 240, "茱", "U+8331", "", "", "", "", "parent",
         "e" * 64],
        [3, "TE00024_0001_1", NDL_CANVAS, 2, 1900, 1330, 150, 240, "〓", "U+3013", "", "", "", "", "", "f" * 64],
    ])
    record = yaml.safe_load(tensho.DEFAULT_SOURCE.read_text(encoding="utf-8"))
    books = {book["id"]: book for book in record["books"]}
    books["TE00039"].update(crops=3, codh_page_width=800)
    books["TE00024"].update(crops=3, replaced_pages=[])
    source = tmp_path / "codh-tensho.yaml"
    source.write_text(yaml.safe_dump(record, allow_unicode=True), encoding="utf-8")
    return scans, data.parent, source


def collect(fixture, tmp_path: Path) -> dict:
    scans, data, source = fixture
    return tensho.collect(["TE00039"], tmp_path / "out", scans=scans, data=data, source=source,
                          cache=tmp_path / "cache")


def collect_volume(fixture, tmp_path: Path) -> dict:
    _, data, source = fixture
    return tensho.collect(["TE00024"], tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


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
        tensho.collect(["TE00039"], tmp_path / "out", scans=scans, data=data, source=source, cache=tmp_path / "cache")


def test_collect_refuses_a_code_point_that_is_not_the_character(fixture, tmp_path):
    scans, data, source = fixture
    held = data / "TE00039" / "characters.tsv"
    held.write_text(held.read_text(encoding="utf-8").replace("U+3D005", "U+0041"), encoding="utf-8")

    with pytest.raises(tensho.TenshoSourceError, match="not the code point"):
        tensho.collect(["TE00039"], tmp_path / "out", scans=scans, data=data, source=source, cache=tmp_path / "cache")


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

    result = CliRunner().invoke(app, ["import", "tensho", "TE00039", "TE00024", "--scans", str(scans),
                                      "--out", str(tmp_path / "out"), "--cache", str(tmp_path / "cache")])

    assert result.exit_code == 0, result.output
    assert "units" in result.output and "4" in result.output
    documents = {document.id for document in tables.read(tmp_path / "out" / "documents.parquet", Document)}
    assert documents == {"codh-tensho:TE00039", "codh-tensho:TE00024"}
    # Each book numbers its own lines, whatever was imported beside it.
    assert sorted(line.seq for line in tables.read(tmp_path / "out" / "lines.parquet", Line)) == [0, 0]
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_a_box_over_the_edge_is_cut_back_to_the_page(fixture, tmp_path):
    scans, data, source = fixture
    held = data / "TE00039" / "characters.tsv"
    held.write_text(held.read_text(encoding="utf-8").replace("\t700\t100\t41\t61\t", "\t-10\t-4\t41\t61\t"),
                    encoding="utf-8")

    tensho.collect(["TE00039"], tmp_path / "out", scans=scans, data=data, source=source, cache=tmp_path / "cache")

    first = next(iter(tables.read(tmp_path / "out" / "units.parquet", Unit)))
    assert first.box.model_dump() == {"x": 0, "y": 0, "w": 16, "h": 28}
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_a_volume_on_iiif_pages_names_the_service_and_reads_no_image(fixture, tmp_path):
    summary = collect_volume(fixture, tmp_path)

    assert (summary["pages"], summary["units"], summary["seal"], summary["han"], summary["left_out"]) == (1, 2, 1, 1, 1)
    page = next(iter(tables.read(tmp_path / "out" / "pages.parquet", Page)))
    assert (page.id, page.image, page.width, page.height, page.sha256) == (
        "codh-tensho:TE00024:p0003", NDL_SERVICE, 5199, 3979, None)
    assert (page.seq, page.canvas) == (2, NDL_CANVAS)
    assert not (tmp_path / "cache").exists()
    line = next(iter(tables.read(tmp_path / "out" / "lines.parquet", Line)))
    assert (line.id, line.text) == ("codh-tensho:TE00024:p0003:c01", "\U0003D000茱")
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_a_form_takes_the_one_seal_character_of_its_parent_or_stays_on_it(fixture, tmp_path):
    collect_volume(fixture, tmp_path)

    placed, kept = tables.read(tmp_path / "out" / "units.parquet", Unit)
    assert (placed.unicode, placed.text_source, placed.script, placed.review) == (
        "U+3D000", "一", Script.SEAL, ReviewState.MACHINE)
    # CODH's box stands as it is: its pixels are the page's.
    assert placed.box.model_dump() == {"x": 2161, "y": 1330, "w": 148, "h": 245}
    assert placed.upstream == {
        "source": "codh-tensho", "ref": "U+4E00_TE00024_0001_1_X2161_Y1330", "parent": "一", "equivalent": "一",
        "placed": "equivalent", "box": "2161,1330,148,245"}
    assert (kept.unicode, kept.text_source, kept.script, kept.style, kept.review) == (
        "U+8331", "茱", Script.HAN, "seal", ReviewState.TRANSCRIBER)
    assert kept.upstream == {
        "source": "codh-tensho", "ref": "U+8331_TE00024_0001_1_X2165_Y1700", "parent": "茱", "placed": "parent",
        "box": "2165,1700,150,240"}
    assert ngrams.adjacent_ngrams([placed, kept]) == []


def test_a_volume_states_the_library_and_its_public_domain_mark(fixture, tmp_path):
    collect_volume(fixture, tmp_path)

    document = next(iter(tables.read(tmp_path / "out" / "documents.parquet", Document)))
    assert (document.id, document.title) == ("codh-tensho:TE00024", "新撰篆書字典 巻3")
    assert (document.production, document.origin) == ("printed", "japan")
    assert (document.dating[0].start, document.dating[0].literal) == (1924, "春湖書屋 大正13")
    assert document.dating[0].evidence == "https://dl.ndl.go.jp/api/iiif/1181893/manifest.json"
    assert (document.image_rights.licence, document.image_rights.holder_terms) == (Licence.PDM, None)
    # The line of CODH's list of providers, with the URLs of all three volumes.
    assert document.image_rights.attribution.endswith(
        "『新撰篆書字典』 国立国会図書館デジタルコレクション（国立国会図書館） https://dl.ndl.go.jp/info:ndljp/pid/1181876 "
        "https://dl.ndl.go.jp/info:ndljp/pid/1181885 https://dl.ndl.go.jp/info:ndljp/pid/1181893")
    assert document.meta["publisher"] == "春湖書屋"
    assert document.source_refs["iiif-manifest"] == "https://dl.ndl.go.jp/api/iiif/1181893/manifest.json"


def test_a_page_the_library_replaced_is_left_out_with_its_forms(fixture, tmp_path):
    source = fixture[2]
    record = yaml.safe_load(source.read_text(encoding="utf-8"))
    next(book for book in record["books"] if book["id"] == "TE00024")["replaced_pages"] = [3]
    source.write_text(yaml.safe_dump(record, allow_unicode=True), encoding="utf-8")

    summary = collect_volume(fixture, tmp_path)

    assert (summary["pages"], summary["units"], summary["left_out"], summary["replaced"]) == (0, 0, 0, 3)


def test_a_replaced_page_must_be_a_page_of_the_book(fixture, tmp_path):
    source = fixture[2]
    record = yaml.safe_load(source.read_text(encoding="utf-8"))
    next(book for book in record["books"] if book["id"] == "TE00024")["replaced_pages"] = [4]
    source.write_text(yaml.safe_dump(record, allow_unicode=True), encoding="utf-8")

    with pytest.raises(tensho.TenshoSourceError, match="replaced page"):
        collect_volume(fixture, tmp_path)


def test_a_book_of_page_files_needs_their_directory(fixture, tmp_path):
    _, data, source = fixture

    with pytest.raises(tensho.TenshoSourceError, match="name their directory"):
        tensho.collect(["TE00039"], tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


def test_collect_refuses_a_form_on_its_parent_and_on_a_seal_character(fixture, tmp_path):
    _, data, source = fixture
    held = data / "TE00024" / "characters.tsv"
    held.write_text(held.read_text(encoding="utf-8").replace("\tequivalent\t", "\tparent\t"), encoding="utf-8")

    with pytest.raises(tensho.TenshoSourceError, match="at once"):
        tensho.collect(["TE00024"], tmp_path / "out", data=data, source=source, cache=tmp_path / "cache")


def test_only_the_parent_of_one_seal_character_places_a_form(tmp_path):
    sources = tmp_path / "SealSources.txt"
    sources.write_text(
        "# SealSources\nU+3D000\tkSEAL_MCJK\t4E00\nU+3D000\tkSEAL_CCZSrc\tC-00001\n"
        "U+3D010\tkSEAL_MCJK\t4E0A\nU+3D011\tkSEAL_MCJK\t4E0A\nU+3D65F\tkSEAL_MCJK\t5668\n", encoding="utf-8")
    crops = [{"parent": parent} for parent in ("4E00", "4E0A", "8331", "3013", "FA38")]

    build_tensho_equivalents.place(crops, build_tensho_equivalents.equivalents(sources))

    assert [(crop["placed"], crop.get("seal")) for crop in crops] == [
        ("equivalent", "U+3D000"), ("parent", None), ("parent", None), ("", None), ("equivalent", "U+3D65F")]
    # 器 as a compatibility ideograph is read as the unified ideograph it is equivalent to.
    assert crops[-1]["equivalent"] == "5668"


@pytest.mark.parametrize(("book", "crops"), VOLUMES.items())
def test_the_tables_of_a_volume_keep_every_crop_on_a_page_of_the_library(book, crops):
    folder = tensho.DEFAULT_DATA / book
    rows = tensho.read_tsv(folder / "characters.tsv")
    pages = {row["canvas"]: row for row in tensho.read_tsv(folder / "pages.tsv")}

    assert len(rows) == crops == len({row["sha256"] for row in rows})
    assert {row["canvas"] for row in rows} == set(pages)
    assert {row["placed"] for row in rows} <= {"equivalent", "parent", ""}
    for row in rows:
        page = pages[row["canvas"]]
        assert int(row["x"]) + int(row["w"]) <= int(page["width"])
        assert int(row["y"]) + int(row["h"]) <= int(page["height"])
        if row["placed"] == "equivalent":
            assert ord(row["char"]) in tensho.SEAL
            assert row["equivalent"] == unicodedata.normalize("NFC", row["parent"])
        else:
            assert not row["char"] and (row["placed"] == "parent") == (row["parent"] != "〓")
            # No form stays on a compatibility ideograph, which is no character of its own.
            assert row["parent"] == unicodedata.normalize("NFC", row["parent"])
    assert all(page["image"].startswith("https://dl.ndl.go.jp/api/iiif/") for page in pages.values())


def test_one_seal_character_never_answers_two_parents():
    held: dict[str, set[str]] = {}
    for book in VOLUMES:
        for row in tensho.read_tsv(tensho.DEFAULT_DATA / book / "characters.tsv"):
            if row["placed"] == "equivalent":
                held.setdefault(row["code_point"], set()).add(row["parent"])

    assert all(len(parents) == 1 for parents in held.values())


def test_pages_a_library_serves_are_not_hosted():
    hosted = registry.hosted_documents()

    assert not any(f"codh-tensho:{book}" in hosted for book in VOLUMES)
