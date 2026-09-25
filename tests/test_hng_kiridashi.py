"""Tests for the HNG 切り出しデータ importer. The clone is built in `tmp_path`; nothing is fetched."""

from __future__ import annotations

from pathlib import Path

import pytest

from glyph_atlas import rights, tables
from glyph_atlas.importers import hng_kiridashi as kd
from glyph_atlas.schema import Document, Licence, Line, Page, Production, ReviewState, Unit

BASE = "https://gallica.bnf.fr/iiif/ark:/12148/btv1b83019074"
HEADER = [
    f"# manifest\t= {BASE}/manifest.json",
    "# HNG code\t= H08",
    "# HNG source ID\t= myz",
    "# " + "\t".join(kd.COLUMNS),
]


def row(sid: int, page: str, ln: int, cn: int, box: tuple[int, int, int, int], char: str, gid: str = "", flg: int = 0) -> str:
    x, y, w, h = box
    return "\t".join(map(str, [sid, sid, page, ln, cn, x, y, w, h, char, gid, flg, "",
                               f"{BASE}/{page}/info.json", 3340, 2150, f"{BASE}/canvas/{page}"]))


def build(clone: Path) -> Path:
    """f2 column 0: 妙法 and a second entry of 法's box; column 1: 蓮 and 華 sharing one box, and a
    blank row; f3 column 0: 經 filed as the second 字体 of card 0504."""
    clone.mkdir(parents=True)
    rows = [
        row(1, "f2", 0, 0, (2959, 393, 113, 77), "妙", "0300"),
        row(2, "f2", 0, 1, (2976, 459, 91, 63), "法", "0435"),
        row(3, "f2", 0, 1, (2976, 459, 91, 63), "法", "0435"),
        row(4, "f2", 1, 0, (2837, 400, 122, 78), "蓮"),
        row(5, "f2", 1, 1, (2837, 400, 122, 78), "華", "0539"),
        row(6, "f2", 1, 2, (2840, 520, 90, 70), ""),
        row(7, "f3", 0, 0, (2974, 636, 91, 72), "經", "0504", 2),
    ]
    (clone / "H08_myz_P2334.tsv").write_text("\n".join(HEADER + rows) + "\n", encoding="utf-8")
    return clone


GLYPHS = {"myz": {"0300", "0435", "0539", "0504a", "0504b"}}


@pytest.fixture(autouse=True)
def basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kd, "basic_glyphs", lambda _basic, sources: {s: GLYPHS.get(s, set()) for s in sources})


@pytest.fixture
def imported(tmp_path: Path):
    counts = kd.import_all(tmp_path / "out", clone=build(tmp_path / "clone"))

    def read(name, model):
        return tables.read(tmp_path / "out" / f"{name}.parquet", model)

    return counts, read("documents", Document), read("pages", Page), read("lines", Line), {
        unit.id: unit for unit in read("units", Unit)}


def test_counts(imported) -> None:
    counts, documents, pages, lines, units = imported
    assert (counts["documents"], counts["pages"], counts["lines"], counts["units"]) == (1, 2, 3, 5)
    assert (counts["duplicates"], counts["blank"], counts["shared_boxes"], counts["linked"]) == (1, 1, 2, 4)
    assert counts["unlinked"] == 0
    assert len(documents) == 1 and len(pages) == 2 and len(lines) == 3 and len(units) == 5


def test_document_rights(imported) -> None:
    _, [document], _, _, _ = imported
    assert document.id == "hng-kiridashi:myz"
    assert (document.holder, document.shelfmark, document.production) == (kd.BNF, "P.2334", Production.MANUSCRIPT)
    assert document.image_rights.licence == Licence.RESTRICTED and not rights.eligible(document.image_rights)
    assert document.text_rights.licence == Licence.CC_BY_SA_4
    assert document.source_refs["iiif-manifest"] == f"{BASE}/manifest.json"


def test_pages_lines_units(imported) -> None:
    _, _, pages, lines, units = imported
    f2 = next(page for page in pages if page.id.endswith(":f2"))
    assert (f2.image, f2.canvas, f2.width, f2.height, f2.seq) == (BASE + "/f2", BASE + "/canvas/f2", 3340, 2150, 2)
    column = next(line for line in lines if line.id == "hng-kiridashi:myz:f2:l0")
    assert column.text == "妙法"
    assert (column.box.x, column.box.y, column.box.w, column.box.h) == (2959, 393, 113, 129)
    first = units["hng-kiridashi:myz:1"]
    assert (first.unicode, first.line_id, first.seq, first.review) == ("U+5999", column.id, 0, ReviewState.TRANSCRIBER)
    assert first.upstream["hng_unit"] == "hng:myz:0300"
    assert units["hng-kiridashi:myz:7"].upstream["hng_unit"] == "hng:myz:0504b"
    assert "hng_unit" not in units["hng-kiridashi:myz:4"].upstream


def test_shared_box_is_disputed(imported) -> None:
    *_, units = imported
    assert units["hng-kiridashi:myz:4"].review == ReviewState.DISPUTED
    assert units["hng-kiridashi:myz:4"].upstream["shares_box_with"] == "5"
    assert units["hng-kiridashi:myz:5"].upstream["shares_box_with"] == "4"


def test_revision_mismatch(tmp_path: Path) -> None:
    clone = build(tmp_path / "clone")
    (clone / ".git").mkdir()
    (clone / ".git" / "HEAD").write_text("0" * 40 + "\n")
    with pytest.raises(kd.RevisionError):
        kd.import_all(tmp_path / "out", clone=clone)


@pytest.mark.parametrize(
    ("gid", "flag", "found"),
    [
        ("0300", "0", ["hng:myz:0300"]),
        ("0504", "2", ["hng:myz:0504b"]),
        ("0504a", "1", ["hng:myz:0504a"]),
        ("0504a/0504b", "0", ["hng:myz:0504a", "hng:myz:0504b"]),
        ("0504", "0", []),
        ("0999", "0", []),
    ],
)
def test_targets(gid: str, flag: str, found: list[str]) -> None:
    assert kd.targets("myz", gid, flag, GLYPHS["myz"]) == found


def test_a_box_past_the_page_edge_is_clamped(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    rows = [row(1, "f2", 0, 0, (3300, 393, 60, 77), "妙", "0300")]
    (clone / "H08_myz_P2334.tsv").write_text("\n".join(HEADER + rows) + "\n", encoding="utf-8")
    counts = kd.import_all(tmp_path / "out", clone=clone)
    [unit] = tables.read(tmp_path / "out" / "units.parquet", Unit)
    assert counts["clamped"] == 1
    assert (unit.box.x, unit.box.w, unit.upstream["box_drawn"]) == (3300, 40, "3300,393,60,77")
    assert tables.Dataset(tmp_path / "out").validate() == []
