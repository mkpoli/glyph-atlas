import hashlib
import json
from datetime import UTC, datetime

import pytest
import yaml

from glyph_atlas.images import ImageRecord
from glyph_atlas.importers import kyujanggak
from glyph_atlas.importers.kyujanggak import build, image_list
from glyph_atlas.tables import Dataset


def fixture():
    book = {"book_cd": "GK18324_00", "item_cd": "FND", "vol_no": "0001", "pages": 2,
            "title": "Test", "shelfmark": "奎18324", "date": 1722, "date_literal": "景宗2年(1722)",
            "catalogue": "https://example.org"}
    rows = [{"BOOK_CD": "GK18324_00", "ITEM_CD": "FND", "FILE_NM": name, "DISP_PAGE": "004a",
             "ADD_PAGE_YN": inserted} for name, inserted in
            [("GK18324_00_IL_0001_004a.jpg", "N"), ("GK18324_00_IL_0001_004a_01.jpg", "Y")]]
    payload = json.dumps({"list": rows}).encode()
    book["image_list_sha256"] = hashlib.sha256(payload).hexdigest()
    book["image_list_bytes"] = len(payload)
    return book, rows, payload


def test_inserted_scans_have_distinct_ids_and_follow_the_list():
    book, rows, payload = fixture()
    assert image_list(payload, book) == rows
    held = [ImageRecord(url=f"https://example.org/{i}", sha256=str(i) * 64,
                        width=100, height=200, bytes=10, fetched_at=datetime.now(UTC)) for i in (1, 2)]
    document, pages = build(book, rows, held)
    assert document.dating[0].start == 1722
    assert [page.seq for page in pages] == [0, 1]
    assert len({page.id for page in pages}) == 2
    assert [page.meta["inserted"] for page in pages] == [False, True]
    assert [page.sha256 for page in pages] == [held[0].sha256, held[1].sha256]


def test_list_rejects_changed_snapshot_and_duplicate_image():
    book, rows, payload = fixture()
    with pytest.raises(ValueError, match="snapshot mismatch"):
        image_list(payload + b" ", book)
    rows[1]["FILE_NM"] = rows[0]["FILE_NM"]
    payload = json.dumps({"list": rows}).encode()
    book["image_list_sha256"] = hashlib.sha256(payload).hexdigest()
    book["image_list_bytes"] = len(payload)
    with pytest.raises(ValueError, match="duplicate"):
        image_list(payload, book)


def test_collection_preserves_order_and_wont_replace_review_data(tmp_path, monkeypatch):
    book, _, payload = fixture()
    book["image_path"] = "/data01/stream/FND/IMG/GK18324_00/GK18324_00_0001/"
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump({"collections": [book]}))
    pinned = tmp_path / "list.json"
    pinned.write_bytes(payload)
    monkeypatch.setattr(kyujanggak.net, "download", lambda *args, **kwargs: pinned)

    def fetch(url, **kwargs):
        return ImageRecord(url=url, sha256="a" * 64, width=100, height=200, bytes=10,
                           fetched_at=datetime.now(UTC))

    monkeypatch.setattr(kyujanggak.images, "fetch", fetch)
    out = tmp_path / "dataset"
    summary = kyujanggak.collect(source, out, tmp_path / "cache")
    assert summary == {"documents": 1, "pages": 2, "inserted_pages": 1,
                       "page_texts": 0, "character_crops": 0}
    assert Dataset(out).validate() == []
    pages = Dataset(out).read("pages")
    assert [p.meta["inserted"] for p in sorted(pages, key=lambda p: p.seq)] == [False, True]
    assert "004a_01.jpg" in pages[1].image
    (out / "reviews.jsonl").write_text("existing review\n")
    with pytest.raises(ValueError, match="review data"):
        kyujanggak.collect(source, out, tmp_path / "cache")
    assert (out / "reviews.jsonl").read_text() == "existing review\n"


def test_rejected_publication_leaves_no_generation(tmp_path, monkeypatch):
    book, _, payload = fixture()
    book["image_path"] = "/data01/stream/FND/IMG/GK18324_00/GK18324_00_0001/"
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump({"collections": [book]}))
    pinned = tmp_path / "list.json"
    pinned.write_bytes(payload)
    monkeypatch.setattr(kyujanggak.net, "download", lambda *args, **kwargs: pinned)
    out = tmp_path / "dataset"

    def fetch(url, **kwargs):
        # A review lands while the scans are being fetched, after the first check.
        out.mkdir(exist_ok=True)
        (out / "reviews.jsonl").write_text("concurrent review\n")
        return ImageRecord(url=url, sha256="a" * 64, width=100, height=200, bytes=10,
                           fetched_at=datetime.now(UTC))

    monkeypatch.setattr(kyujanggak.images, "fetch", fetch)
    with pytest.raises(ValueError, match="review data"):
        kyujanggak.collect(source, out, tmp_path / "cache")
    assert out.is_dir() and not out.is_symlink()
    assert list((tmp_path / ".dataset-versions").iterdir()) == []


def test_failed_switch_leaves_no_generation(tmp_path, monkeypatch):
    book, _, payload = fixture()
    book["image_path"] = "/data01/stream/FND/IMG/GK18324_00/GK18324_00_0001/"
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump({"collections": [book]}))
    pinned = tmp_path / "list.json"
    pinned.write_bytes(payload)
    monkeypatch.setattr(kyujanggak.net, "download", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(kyujanggak.images, "fetch", lambda url, **kwargs: ImageRecord(
        url=url, sha256="a" * 64, width=100, height=200, bytes=10, fetched_at=datetime.now(UTC)))

    def refuse(*args, **kwargs):
        raise OSError("no symlink today")

    monkeypatch.setattr(kyujanggak.os, "symlink", refuse)
    out = tmp_path / "dataset"
    with pytest.raises(OSError, match="no symlink"):
        kyujanggak.collect(source, out, tmp_path / "cache")
    assert list((tmp_path / ".dataset-versions").iterdir()) == []
    assert not out.exists()


def second_volume(book, rows):
    other = dict(book, book_cd="GK17689_00", title="Second volume", shelfmark="奎17689-v.1",
                 date=1904, date_literal="光武8年(1904)")
    moved = [dict(row, BOOK_CD="GK17689_00", FILE_NM=row["FILE_NM"].replace("GK18324_00", "GK17689_00"))
             for row in rows]
    payload = json.dumps({"list": moved}).encode()
    other.update(image_list_sha256=hashlib.sha256(payload).hexdigest(), image_list_bytes=len(payload))
    return other, payload


def test_book_selection_collects_one_volume(tmp_path, monkeypatch):
    first, rows, payload = fixture()
    first["image_path"] = "/data01/stream/FND/IMG/GK18324_00/GK18324_00_0001/"
    second, payload2 = second_volume(first, rows)
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump({"collections": [first, second]}))
    payloads = {"GK18324_00": payload, "GK17689_00": payload2}

    def download(url, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[kwargs["data"]["book_cd"]])
        return path

    monkeypatch.setattr(kyujanggak.net, "download", download)
    monkeypatch.setattr(kyujanggak.images, "fetch", lambda url, **kwargs: ImageRecord(
        url=url, sha256="a" * 64, width=100, height=200, bytes=10, fetched_at=datetime.now(UTC)))
    out = tmp_path / "dataset"
    summary = kyujanggak.collect(source, out, tmp_path / "cache", books=["GK17689_00:0001"])
    assert summary["documents"] == 1 and summary["pages"] == 2
    assert Dataset(out).read("documents")[0].id.startswith("kyujanggak:GK17689_00")


def test_unlisted_volume_is_rejected_before_download(tmp_path, monkeypatch):
    book, _, _payload = fixture()
    book["image_path"] = "/data01/stream/FND/IMG/GK18324_00/GK18324_00_0001/"
    source = tmp_path / "source.yaml"
    source.write_text(yaml.safe_dump({"collections": [book]}))
    calls = []
    monkeypatch.setattr(kyujanggak.net, "download", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="not in the source record"):
        kyujanggak.collect(source, tmp_path / "dataset", tmp_path / "cache", books=["GK00000_00:0001"])
    assert calls == []
