import csv
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from glyph_atlas import tables
from glyph_atlas.cli import app
from glyph_atlas.importers import tensho
from glyph_atlas.schema import Document, Licence, Script, Unit

FIELDS = ["seq", "image", "canvas", "x", "y", "w", "h", "parent", "parent_code_point", "char", "code_point", "ccz",
          "equivalent", "placed", "sha256"]
CANVAS = "http://kanji.zinbun.kyoto-u.ac.jp/db-machine/toho/L/A0280017.jpg"


def crop(shade: int) -> bytes:
    held = io.BytesIO()
    Image.new("L", (20, 30), shade).save(held, "JPEG")
    return held.getvalue()


@pytest.fixture
def fixture(tmp_path):
    """A zip of three crops, a table that places two of them, and a source record that pins the zip."""
    files = {"U+540F_TE00039_0001_1_X3435_Y0806.jpg": crop(40), "U+4E0A_TE00039_0001_1_X3101_Y0810.jpg": crop(90),
             "U+65C1_TE00039_0001_1_X2314_Y0807.jpg": crop(140)}
    archive = tmp_path / "TE00039.zip"
    with zipfile.ZipFile(archive, "w") as held:
        for name, content in files.items():
            held.writestr(f"TE00039/characters/{name.split('_')[0]}/{name}", content)
    digests = [hashlib.sha256(content).hexdigest() for content in files.values()]
    data = tmp_path / "data"
    (data / "TE00039").mkdir(parents=True)
    with (data / "TE00039" / "characters.tsv").open("w", encoding="utf-8", newline="") as handle:
        handle.write("# a table of the fixture\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        writer.writerow([5, "TE00039_0001_1", CANVAS, 3435, 806, 109, 195, "吏", "U+540F", "\U0003D005", "U+3D005",
                         "C-00006", "叓", "order", digests[0]])
        writer.writerow([6, "TE00039_0001_1", CANVAS, 3101, 810, 116, 169, "上", "U+4E0A", "\U0003D006", "U+3D006",
                         "C-00007", "丄", "variant", digests[1]])
        writer.writerow([9, "TE00039_0001_1", CANVAS, 2314, 807, 108, 185, "旁", "U+65C1", "", "", "", "", "",
                         digests[2]])
    record = yaml.safe_load(tensho.DEFAULT_SOURCE.read_text(encoding="utf-8"))
    record["books"][0]["zip_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = tmp_path / "codh-tensho.yaml"
    source.write_text(yaml.safe_dump(record, allow_unicode=True), encoding="utf-8")
    return archive, data, source


def collect(fixture, tmp_path: Path) -> dict:
    archive, data, source = fixture
    return tensho.collect(archive, tmp_path / "out", data=data, source=source, crops=tmp_path / "crops")


def test_collect_writes_a_unit_for_each_placed_crop(fixture, tmp_path):
    summary = collect(fixture, tmp_path)

    assert (summary["units"], summary["left_out"], summary["documents"]) == (2, 1, ["codh-tensho:TE00039"])
    document = next(iter(tables.read(tmp_path / "out" / "documents.parquet", Document)))
    assert (document.production, document.origin, document.genre) == ("printed/woodblock", "china", ["dictionary"])
    assert (document.dating[0].start, document.dating[0].kind) == (1873, "publication")
    assert document.image_rights.licence is Licence.CC_BY_SA_4
    assert "doi:10.20676/00000390" in document.image_rights.attribution
    units = list(tables.read(tmp_path / "out" / "units.parquet", Unit))
    first = units[0]
    assert (first.id, first.unicode, first.text_source) == ("codh-tensho:TE00039:00005", "U+3D005", "\U0003D005")
    assert (first.script, first.style, first.page_id, first.box) == (Script.SEAL, "seal", None, None)
    assert first.crop == "TE00039.zip!TE00039/characters/U+540F/U+540F_TE00039_0001_1_X3435_Y0806.jpg"
    assert (first.upstream["parent"], first.upstream["equivalent"], first.upstream["ccz"]) == ("吏", "叓", "C-00006")
    assert (first.upstream["placed"], first.upstream["page"], first.upstream["box"]) == (
        "order", CANVAS, "3435,806,109,195")
    assert tables.Dataset(tmp_path / "out").validate() == []


def test_collect_extracts_each_crop_where_the_exports_look(fixture, tmp_path):
    collect(fixture, tmp_path)

    held = tmp_path / "crops" / "TE00039/characters/U+540F/U+540F_TE00039_0001_1_X3435_Y0806.jpg"
    unit = next(iter(tables.read(tmp_path / "out" / "units.parquet", Unit)))
    assert hashlib.sha256(held.read_bytes()).hexdigest() == unit.crop_sha256
    assert not (tmp_path / "crops" / "TE00039/characters/U+65C1").exists()


def test_collect_refuses_another_zip(fixture, tmp_path):
    archive, data, source = fixture
    with zipfile.ZipFile(archive, "a") as held:
        held.writestr("TE00039/extra.txt", "x")

    with pytest.raises(tensho.TenshoSourceError, match="not the zip"):
        tensho.collect(archive, tmp_path / "out", data=data, source=source, crops=tmp_path / "crops")


def test_collect_refuses_a_crop_the_table_does_not_pin(fixture, tmp_path):
    archive, data, source = fixture
    table = data / "TE00039" / "characters.tsv"
    table.write_text(table.read_text(encoding="utf-8").replace("\torder\t", "\torder\t0"), encoding="utf-8")

    with pytest.raises(tensho.TenshoSourceError, match="not the crop"):
        tensho.collect(archive, tmp_path / "out", data=data, source=source, crops=tmp_path / "crops")


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


def test_the_command_reports_what_it_wrote(fixture, tmp_path, monkeypatch):
    archive, data, source = fixture
    monkeypatch.setattr(tensho, "DEFAULT_DATA", data)
    monkeypatch.setattr(tensho, "DEFAULT_SOURCE", source)

    result = CliRunner().invoke(app, ["import", "tensho", str(archive), "--out", str(tmp_path / "out"),
                                      "--crops", str(tmp_path / "crops")])

    assert result.exit_code == 0, result.output
    assert "units" in result.output and "2" in result.output
