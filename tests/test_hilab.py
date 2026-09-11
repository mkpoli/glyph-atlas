"""Tests for the 東京大学史料編纂所 くずし字データセット importer.

Every archive is built in `tmp_path` and served by the `http_server` fixture, which honours `Range`;
no test reaches the network.
"""

from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from kuzushiji_atlas import tables
from kuzushiji_atlas.importers import hilab
from kuzushiji_atlas.schema import (
    Classification,
    Document,
    Licence,
    ReviewState,
    Script,
    Unit,
    UnitKind,
)

#: The code point folders of the fixture: あ out of U+3042 and 一 out of U+4E00.
HIRAGANA = "U+3042"
KANJI = "U+4E00"

#: What the three crops are called and how big they are, in member order.
CROPS = {"1001": (HIRAGANA, (11, 5)), "1002": (HIRAGANA, (13, 7)), "1003": (KANJI, (9, 9))}

EOCD = b"PK\x05\x06"
EOCD64 = b"PK\x06\x06"
LOCATOR = b"PK\x06\x07"
SATURATED = 0xFFFFFFFF


def crops(tmp_path: Path) -> dict[str, bytes]:
    """One JPEG per crop of `CROPS`, by member id, in distinct shades."""
    made = {}
    for index, (member, (_, size)) in enumerate(CROPS.items()):
        path = tmp_path / f"{member}.jpg"
        Image.new("RGB", size, (30 + 40 * index,) * 3).save(path, "JPEG")
        made[member] = path.read_bytes()
    return made


def build_zip64(path: Path, members: dict[str, bytes]) -> Path:
    """A zip64 archive of the members, with the zip64 end record and its locator.

    `zipfile` writes a zip64 end record once an archive outgrows its 32-bit fields, which three
    members never do, so the record and the locator are appended and the fields of the end record
    are saturated, the way an archive that needs them writes them.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    payload = path.read_bytes()
    at = len(payload) - 22
    assert payload[at : at + 4] == EOCD and payload[at + 20 :] == b"\x00\x00", "no end record comment"
    count, size, offset = struct.unpack_from("<HII", payload, at + 10)
    record = struct.pack("<4sQHHIIQQQQ", EOCD64, 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sIQI", LOCATOR, 0, at, 1)
    end = bytearray(payload[at:])
    struct.pack_into("<HII", end, 10, 0xFFFF, SATURATED, SATURATED)
    path.write_bytes(payload[:at] + record + locator + bytes(end))
    with zipfile.ZipFile(path) as again:
        assert again.namelist() == list(members), "zipfile reads the archive as zip64"
    return path


def archive(tmp_path: Path, members: dict[str, bytes], name: str = "all.zip") -> Path:
    """Three crops in two folders, beside the members the importer skips or ignores."""
    stored = {
        f"all/characters/{folder}/{member}.jpg": members[member]
        for member, (folder, _) in CROPS.items()
    }
    stored["all/characters/.DS_Store"] = b"\x00\x01\x02"
    stored["all/characters/notes/readme.txt"] = b"a file in a folder that is not U+XXXX\n"
    stored[f"characters/{KANJI}/1003.jpg"] = members["1003"]
    return build_zip64(tmp_path / name, stored)


def serve(http_server, path: Path, name: str = "all.zip") -> str:
    http_server.put(name, path.read_bytes())
    return http_server.url(name)


def units_of(out: Path) -> dict[str, Unit]:
    return {unit.id: unit for unit in tables.read(out / "units.parquet", Unit)}


def test_import_reads_the_listing_and_leaves_the_crops_alone(tmp_path, http_server):
    members = crops(tmp_path)
    url = serve(http_server, archive(tmp_path, members))
    out, cached = tmp_path / "work", tmp_path / "cache" / "hilab"

    counts = hilab.import_all(out, url=url, crops=cached, listing=tmp_path / "listing")

    assert counts["units"] == 3 and counts["documents"] == 1
    assert counts["code_points"] == 2 and counts["kana"] == 2
    assert counts["skipped"] == 2, "the .DS_Store and the member of the folder that is not U+XXXX"
    assert counts["non_unicode"] == 2
    assert counts["requests"] == 2, "the listing is a HEAD and one range request"
    assert not cached.exists(), "the crops are read only when download asks for them"
    assert tables.Dataset(out).validate() == []

    document = tables.read(out / "documents.parquet", Document)[0]
    assert document.id == "hi:kuzushiji-2023"
    assert document.title == "くずし字データセット（東京大学史料編纂所）"
    assert document.holder == "東京大学史料編纂所"
    assert document.meta["kind"] == "dataset"
    assert document.image_rights is not None and document.text_rights is not None
    assert document.image_rights.licence is Licence.CC_BY_4
    assert document.image_rights.attribution == hilab.source_file()["attribution"]
    assert document.image_rights.checked is not None
    assert not (out / "pages.parquet").exists(), "a crop has no page"

    units = units_of(out)
    assert list(units) == ["hi:1001", "hi:1002", "hi:1003"]
    hiragana = units["hi:1001"]
    assert hiragana.document_id == "hi:kuzushiji-2023"
    assert hiragana.page_id is None and hiragana.line_id is None and hiragana.box is None
    assert hiragana.crop == f"all.zip!all/characters/{HIRAGANA}/1001.jpg"
    assert hiragana.crop_sha256 is None and "width" not in hiragana.upstream
    assert hiragana.unicode == HIRAGANA and hiragana.text_source == "あ" and hiragana.reading == "あ"
    assert hiragana.script is Script.HIRAGANA
    assert hiragana.classification is Classification.UNASSESSED
    assert hiragana.granularity == "char" and hiragana.kind is UnitKind.CHAR
    assert hiragana.method == "import" and hiragana.review is ReviewState.TRANSCRIBER
    assert hiragana.upstream == {
        "source": hilab.SOURCE,
        "ref": "1001",
        "url": "https://wwwap.hi.u-tokyo.ac.jp/ships/w34/detail/1001",
    }
    kanji = units["hi:1003"]
    assert kanji.crop == f"all.zip!all/characters/{KANJI}/1003.jpg"
    assert kanji.unicode == KANJI and kanji.text_source == "一" and kanji.reading == "一"
    assert kanji.script is Script.KANJI and kanji.classification is Classification.IDENTIFIED


def test_download_extracts_and_measures_the_crops_in_batches(tmp_path, http_server):
    members = crops(tmp_path)
    url = serve(http_server, archive(tmp_path, members))
    out, cached = tmp_path / "work", tmp_path / "cache" / "hilab"

    counts = hilab.import_all(out, download=True, batch=2, url=url, crops=cached, listing=tmp_path / "listing")

    assert counts["units"] == 3
    units = units_of(out)
    for member, (folder, size) in CROPS.items():
        unit = units[f"hi:{member}"]
        path = cached / "all" / "characters" / folder / f"{member}.jpg"
        assert path.read_bytes() == members[member]
        assert unit.crop_sha256 == hashlib.sha256(members[member]).hexdigest()
        assert (unit.upstream["width"], unit.upstream["height"]) == (str(size[0]), str(size[1]))

    before = len(http_server.requests)
    again = hilab.import_all(out, download=True, batch=2, url=url, crops=cached, listing=tmp_path / "listing")
    assert again["units"] == 3 and again["requests"] == 1, "the crops on disk are not fetched again"
    assert len(http_server.requests) == before + 1, "the second run makes the HEAD of the listing only"


def test_two_folders_that_hold_the_same_id_stop_the_import(tmp_path, http_server):
    members = crops(tmp_path)
    stored = {
        f"all/characters/{HIRAGANA}/1001.jpg": members["1001"],
        f"all/characters/{KANJI}/1001.jpg": members["1003"],
    }
    url = serve(http_server, build_zip64(tmp_path / "clash.zip", stored))
    out = tmp_path / "work"

    with pytest.raises(hilab.DuplicateIdError, match="hi:1001 is in two folders"):
        hilab.import_all(out, url=url, crops=tmp_path / "cache", listing=tmp_path / "listing")

    assert not out.exists(), "the import stops before a table is written"


def test_limit_keeps_the_first_crops_of_the_listing(tmp_path, http_server):
    members = crops(tmp_path)
    url = serve(http_server, archive(tmp_path, members))
    out = tmp_path / "work"

    counts = hilab.import_all(out, limit=2, url=url, crops=tmp_path / "cache", listing=tmp_path / "listing")

    assert counts["units"] == 2 and counts["code_points"] == 1 and counts["kana"] == 2
    assert sorted(units_of(out)) == ["hi:1001", "hi:1002"]
    assert tables.Dataset(out).validate() == []
