"""Tests for the 국립한글박물관 아카이브 collector. No test reaches the network."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from PIL import Image

from glyph_atlas import images, net, tables
from glyph_atlas.corpus import sources
from glyph_atlas.corpus.api import PROXYABLE
from glyph_atlas.importers import hangeul_museum as hm
from glyph_atlas.schema import Document, Licence, Page, PageText, Production

CHECKED = date(2026, 9, 25)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Clock:
    monkeypatch.setenv(images.ENV_CACHE, str(tmp_path / "cache"))
    fake = Clock()
    monkeypatch.setattr(net, "CLOCK", fake)
    monkeypatch.setattr(net, "SLEEP", fake.sleep)
    net.reset_pauses()
    yield fake
    net.reset_pauses()


def jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (width % 255, height % 255, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def record(rid: int, *, kogl: str = "CD00167", text: str = "부아 답셔\n너ᄒᆡ 소식은", files=(11, 12)) -> dict:
    return {
        "rcrdId": rid,
        "rcrdNm": f"한글편지 {rid}",
        "rcrdNum": f"OT-{rid}",
        "orLoc": "국립한글박물관",
        "histClCdNm": "17세기~19세기",
        "histInfo": "",
        "tags": "편지,한글",
        "koglCdId": kogl,
        "orgItptr": text,
        "orgTxt": "해제",
        "relicVO": {"relicMngNum": f"한구 {rid}", "editionCdNm": "필사본", "chctCdNm": "순한글",
                    "author": "미상", "nameCn": "", "size": "", "relicDesc": ""},
        "imgList": [{"atchFileSn": sn, "orgnlFileNm": f"{sn}.jpg"} for sn in files],
    }


def transport(records: dict[int, dict], sizes: dict[int, tuple[int, int]], seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["User-Agent"] == net.USER_AGENT
        if "/api/archv/ot/view" in request.url.path:
            rid = int(request.url.params["rcrdId"])
            return httpx.Response(200, json=records[rid])
        sn = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, content=jpeg(*sizes[sn]), headers={"Content-Type": "image/jpeg"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_kogl_type_1_record_becomes_a_document_with_pages_text_and_licence(tmp_path: Path) -> None:
    seen: list[str] = []
    text = "부아 답셔\n너ᄒᆡ 소식은\n\n판독자: 이래호"
    client = transport({40712: record(40712, text=text)}, {11: (30, 20), 12: (40, 25)}, seen)
    out = tmp_path / "hangeul-museum"
    summary = hm.collect([40712], out, client=client, checked=CHECKED)

    assert summary["collected"] == [{"id": "40712", "title": "한글편지 40712", "koglCdId": "CD00167",
                                     "pages": 2, "has_text": True}]
    (document,) = tables.read(out / "documents.parquet", Document)
    assert document.id == "hangeul-museum:40712"
    assert document.holder == "국립한글박물관"
    assert document.shelfmark == "한구 40712"
    assert document.production is Production.MANUSCRIPT
    assert document.source_refs["catalogue"] == "https://archives.hangeul.go.kr/ko/M000000591/archv/ot/view?rcrdId=40712"
    for rights in (document.image_rights, document.text_rights):
        assert rights.licence is Licence.KOGL_1
        assert rights.evidence == "https://archives.hangeul.go.kr/ko/M000000614/html/view"
        assert rights.checked == CHECKED
    assert document.image_rights.attribution == "국립한글박물관, 공공누리 제1유형(출처표시)"
    assert document.text_rights.attribution == "판독: 이래호; 국립한글박물관, 공공누리 제1유형(출처표시)"
    assert document.meta["language"] == "ko" and document.meta["scripts"] == ["Hang"]
    assert document.meta["transcriber"] == "이래호"
    assert "page 1" in document.meta["text_placement"]
    assert document.dating[0].start == 1601 and document.dating[0].end == 1900

    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.seq, p.width, p.height) for p in pages] == [(1, 30, 20), (2, 40, 25)]
    assert all(images.path_for(p.image) is not None and p.sha256 for p in pages)
    (page_text,) = tables.read(out / "page_texts.parquet", PageText)
    assert page_text.page_id == "hangeul-museum:40712:1"
    assert page_text.text_raw == "부아 답셔\n너ᄒᆡ 소식은"
    assert "KOGL-1" in PROXYABLE

    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tables"] == {"documents": 1, "pages": 2, "page_texts": 1}
    (corpus,) = sources.discover(tmp_path)
    assert corpus.name == "hangeul-museum" and corpus.searchable


def test_a_record_under_another_licence_is_skipped_without_fetching_images(tmp_path: Path) -> None:
    seen: list[str] = []
    client = transport({1: record(1, kogl="CD00168")}, {}, seen)
    summary = hm.collect([1], tmp_path / "out", client=client, checked=CHECKED)
    assert summary["skipped"] == [{"id": "1", "title": "한글편지 1", "koglCdId": "CD00168"}]
    assert summary["collected"] == []
    assert not any("/image/" in url for url in seen)
    assert tables.read(tmp_path / "out" / "documents.parquet", Document) == []


def test_pages_follow_the_image_list_order(tmp_path: Path) -> None:
    seen: list[str] = []
    files = (905, 3, 77)
    client = transport({7: record(7, text="", files=files)}, {905: (10, 10), 3: (11, 10), 77: (12, 10)}, seen)
    hm.collect([7], tmp_path / "out", client=client, checked=CHECKED)
    pages = sorted(tables.read(tmp_path / "out" / "pages.parquet", Page), key=lambda p: p.seq)
    assert [p.meta["atchFileSn"] for p in pages] == list(files)
    assert [p.width for p in pages] == [10, 11, 12]
    assert tables.read(tmp_path / "out" / "page_texts.parquet", PageText) == []
    (document,) = tables.read(tmp_path / "out" / "documents.parquet", Document)
    assert document.text_rights is None
    assert "no 판독" in document.meta["text_placement"]


def test_a_network_error_is_retried_then_fails_cleanly(tmp_path: Path, clock: Clock) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = tmp_path / "out"
    summary = hm.collect([40712], out, client=client, retries=3, checked=CHECKED)
    assert len(attempts) == 3
    assert clock.slept  # backed off between attempts
    assert summary["collected"] == [] and summary["unavailable"][0]["id"] == "40712"
    assert "gave up after 3 attempts" in summary["unavailable"][0]["error"]
    assert not (out / "upstream" / "40712.json").exists()
    assert tables.read(out / "documents.parquet", Document) == []


def test_a_record_id_must_be_a_number() -> None:
    with pytest.raises(ValueError):
        hm.record_id("12a")
