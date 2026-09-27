"""Tests for the 국립중앙도서관 collector. No test reaches the network."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import pymupdf
import pytest
from PIL import Image

from glyph_atlas import images, net, tables
from glyph_atlas.corpus import sources
from glyph_atlas.corpus.api import PROXYABLE
from glyph_atlas.importers import nlk
from glyph_atlas.schema import Document, Licence, Page

CHECKED = date(2026, 9, 27)


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


def pdf_bytes(sizes: list[tuple[int, int]], *, blank_page: int | None = None) -> bytes:
    """A tiny PDF with one embedded JPEG page per size; `blank_page` (1-based) gets no image."""
    doc = pymupdf.open()
    for seq, (width, height) in enumerate(sizes, 1):
        page = doc.new_page(width=width, height=height)
        if seq != blank_page:
            page.insert_image(pymupdf.Rect(0, 0, width, height), stream=jpeg(width, height))
        else:
            page.insert_text((5, 5), "no image here")
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def bookinfo_html(*, title: str, author: str = "", publisher: str = "", year: str = "",
                   collation: str = "", standard_number: str = "", copyright_note: str = "무료") -> str:
    rows = [
        ("표제", title), ("저자", author), ("발행처", publisher), ("발행년도", year),
        ("형태사항", collation), ("표준번호", standard_number), ("저작권", copyright_note),
    ]
    items = "".join(
        f'<li><div class="context"><span class="label">{label}</span>'
        f'<span class="text">{value}</span></div></li>'
        for label, value in rows
    )
    return f'<ul class="bookInfo">{items}</ul>'


def detail_html(*, title: str, production: str = "", publication: str = "", collation: str = "",
                notes: str = "", standard_number: str = "", kol: str | None = None) -> str:
    if kol:
        notes = (notes + " " if notes else "") + (
            f'다른 형태자료: <a href="/NL/search/makeDetailUrl.do?controlNo={kol}" '
            f'target="_blank" class="txt_blue">상세보기</a>'
        )
    fields = [
        ("표제/저자사항", title), ("판사항", production), ("발행사항", publication),
        ("형태사항", collation), ("주기사항", notes), ("표준번호/부호", standard_number),
    ]
    body = "".join(
        f'<p><span class="mark">{label}</span>{value}</p>' for label, value in fields if value
    )
    return (
        '<div class="popup_contents"><div class="detail_top_wrap grid_wrap">'
        f'<h3 class="detail_tit"><span class="txt_blue tit_top">[고문헌]</span>{title}</h3>'
        f'<div class="grid grid_r info_wrap" id="divSeoji"><div class="more_info_wrap">{body}'
        "</div></div></div></div>"
    )


def png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("1", (width, height), 1).save(buffer, format="PNG")
    return buffer.getvalue()


def viewer_page(cno: str, record: dict) -> str:
    """The viewer page the form answers: a PDF record names its file, an image record its pages."""
    if "images" in record:
        count = len(record["images"])
        return (f'<script>var srcpath = "/data2/imagedb/NCL_DB_7/X"; var ext = "TIF";\n'
                f'var vol_maxpage = "{count}";</script>'
                f"<a onClick=\"loadVol('{cno}',0,'/data2/imagedb/NCL_DB_7/X',{count},'1'); false;\">")
    return f"<script>var DEFAULT_URL = '/conv/{record['pdf_name']}.pdf';</script>"


def transport(records: dict[str, dict], seen: list[str]):
    """A mock of the library's hosts for one or more CNTS records.

    Each `records[cno]` gives `bookinfo`, `detail`, and either `pdf_name` and `pdf` (the file the
    viewer page names, and its bytes) or `images` (the served page images, in order). As on the real
    host, the viewer form answers 404 without the viewer page as Referer; a PDF answers 404 until the
    form has been posted for its record, and for good when `built` is False; a page image is served
    only to the session the form opened, and outside it the host answers a short text body.
    """
    opened: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["User-Agent"] == net.USER_AGENT
        url = str(request.url)
        if "getBookInfo.jsp" in url:
            cno = url.rsplit("=", 1)[1]
            return httpx.Response(200, text=records[cno]["bookinfo"],
                                  headers={"Content-Type": "text/html;charset=UTF-8"})
        if "nl_detail_online_view.ajax" in url:
            form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            cno = form["viewKey"]
            return httpx.Response(200, text=records[cno]["detail"],
                                  headers={"Content-Type": "text/html;charset=UTF-8"})
        if url == nlk.VIEWER_OPEN:
            if not request.headers.get("Referer", "").startswith(nlk.VIEWER_OPEN):
                return httpx.Response(404, text="not found")
            form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            cno = form["cno"]
            opened.add(cno)
            return httpx.Response(200, text=viewer_page(cno, records[cno]),
                                  headers={"Content-Type": "text/html;charset=UTF-8",
                                           "Set-Cookie": f"JSESSIONID={cno}; Path=/"})
        if "view_image.jsp" in url:
            query = dict(request.url.params)
            record = records[query["cno"]]
            if request.headers.get("Cookie") != f"JSESSIONID={query['cno']}":
                return httpx.Response(200, text="error\r\n", headers={"Content-Type": "text/html"})
            return httpx.Response(200, content=record["images"][int(query["page"]) - 1],
                                  headers={"Content-Type": "image/png"})
        if url.endswith(".pdf"):
            name = url.rsplit("/", 1)[1].removesuffix(".pdf")
            for cno, record in records.items():
                if name == record.get("pdf_name") and cno in opened and record.get("built", True):
                    return httpx.Response(200, content=record["pdf"],
                                          headers={"Content-Type": "application/pdf",
                                                   "Accept-Ranges": "bytes"})
            return httpx.Response(404, text="not found")
        raise AssertionError(f"unexpected request: {url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_an_undated_record_becomes_a_pd_document_from_the_pdf_its_viewer_names(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00092710493": {
            "bookinfo": bookinfo_html(title=" 倭語類解. 1-2/ 洪舜明 著", author="홍순명",
                                      publisher="[刊寫者未詳]", year="---------",
                                      standard_number="UCI G701:B-00092710493"),
            "detail": detail_html(title="倭語類解. 1-2/ 洪舜明 著 / 홍순명", production="木板本",
                                  publication="[刊寫地未詳] : [刊寫者未詳], ---------",
                                  collation="2卷2冊: 四周雙邊 半郭 23.2 x 17.0 cm",
                                  standard_number="UCI G701:B-00092710493", kol="KOL000032963"),
            "pdf_name": "KOL000032963",
            "pdf": pdf_bytes([(30, 20), (40, 25)]),
        }
    }
    client = transport(records, seen)
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00092710493"], out, client=client, checked=CHECKED, build_retries=1)

    assert summary["collected"] == [{
        "id": "CNTS-00092710493", "title": "倭語類解. 1-2", "pages": 2, "held_as": "pdf",
        "pdf": "https://viewer.nl.go.kr/conv/KOL000032963.pdf", "kol": "KOL000032963",
    }]
    assert summary["unavailable"] == []
    (document,) = tables.read(out / "documents.parquet", Document)
    assert document.id == "nlk:CNTS-00092710493"
    assert document.holder == "국립중앙도서관"
    assert document.origin == "korea"
    assert document.production == "printed/woodblock"
    assert document.source_refs["kol"] == "KOL000032963"
    assert document.source_refs["pdf"] == "https://viewer.nl.go.kr/conv/KOL000032963.pdf"
    assert document.source_refs["viewer"] == nlk.VIEWER_PAGE.format(cno="CNTS-00092710493")
    # The statement names no year, but is kept as an unknown-kind dating for provenance.
    assert document.dating[0].start is None and document.dating[0].end is None
    assert document.dating[0].kind == "unknown"
    assert "scripts" not in document.meta

    # No 공공누리 mark: the holder statement is `restricted`, and an undated pre-modern record is
    # recorded as PD with that statement kept as `holder_terms`.
    assert document.image_rights.licence is Licence.PUBLIC_DOMAIN
    assert document.image_rights.holder_terms is Licence.RESTRICTED
    assert document.image_rights.attribution == "국립중앙도서관, [관외이용-무료]"
    assert document.image_rights.evidence == nlk.POLICY
    assert Licence.PUBLIC_DOMAIN.value in PROXYABLE

    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.seq, p.width, p.height) for p in pages] == [(1, 30, 20), (2, 40, 25)]
    assert all(images.path_for(p.image) is not None and p.sha256 for p in pages)
    assert pages[0].image == "https://viewer.nl.go.kr/conv/KOL000032963.pdf#page=1"

    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tables"] == {"documents": 1, "pages": 2, "page_texts": 0}
    (corpus,) = sources.discover(tmp_path)
    assert corpus.name == "nlk"

    # Only the file the viewer page names is ever asked for.
    assert [url for url in seen if url.endswith(".pdf")] == [
        "https://viewer.nl.go.kr/conv/KOL000032963.pdf"
    ]


def test_a_dated_record_with_no_kol_link(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00132358209": {
            "bookinfo": bookinfo_html(title="老乞大諺解. 上", author="司譯院", publisher="司譯院",
                                      year="1675----", collation="PDF | 1 p."),
            "detail": detail_html(title="老乞大諺解. 上 / 司譯院", production="活字本",
                                  publication="漢陽 : 司譯院, 1675", collation="PDF134 p."),
            "pdf_name": "CNTS-00132358209",
            "pdf": pdf_bytes([(50, 60)]),
        }
    }
    client = transport(records, seen)
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00132358209"], out, client=client, checked=CHECKED)

    assert summary["collected"][0]["pdf"] == "https://viewer.nl.go.kr/conv/CNTS-00132358209.pdf"
    assert summary["collected"][0]["kol"] is None
    (document,) = tables.read(out / "documents.parquet", Document)
    assert document.production == "printed/type"
    assert document.shelfmark is None
    assert document.dating[0].literal == "漢陽 : 司譯院, 1675"
    assert document.dating[0].start == 1675 and document.dating[0].end == 1675
    assert document.dating[0].kind == "publication"
    # 1675 is well before 1900, so the record still becomes PD with the holder statement kept.
    assert document.image_rights.licence is Licence.PUBLIC_DOMAIN
    assert document.image_rights.holder_terms is Licence.RESTRICTED
    assert "kol" not in document.source_refs


def test_a_record_dated_after_1900_keeps_the_holder_statement(tmp_path: Path) -> None:
    records = {
        "CNTS-00047976255": {
            "bookinfo": bookinfo_html(title="朝鮮司譯院日滿蒙語學書斷簡"),
            "detail": detail_html(title="朝鮮司譯院日滿蒙語學書斷簡", production="木板本(日本)",
                                  publication="[刊寫地未詳] : [朝鮮司譯院], 1918", kol="KOL000012253"),
            "pdf_name": "CNTS-00047976255",
            "pdf": pdf_bytes([(80, 90)]),
        }
    }
    client = transport(records, [])
    out = tmp_path / "nlk"
    nlk.collect(["CNTS-00047976255"], out, client=client, checked=CHECKED)
    (document,) = tables.read(out / "documents.parquet", Document)
    assert document.dating[0].start == 1918
    assert document.image_rights.licence is Licence.RESTRICTED
    assert document.image_rights.holder_terms is None


def test_a_pdf_page_with_no_embedded_image_leaves_the_record_out(tmp_path: Path) -> None:
    records = {
        "CNTS-00132358210": {
            "bookinfo": bookinfo_html(title="老乞大諺解. 下"),
            "detail": detail_html(title="老乞大諺解. 下 / 司譯院", publication="1675"),
            "pdf_name": "CNTS-00132358210",
            "pdf": pdf_bytes([(50, 60), (10, 10)], blank_page=2),
        }
    }
    client = transport(records, [])
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00132358210"], out, client=client, checked=CHECKED)
    assert summary["collected"] == []
    assert summary["unavailable"][0]["id"] == "CNTS-00132358210"
    assert "draws no image" in summary["unavailable"][0]["error"]
    assert tables.read(out / "documents.parquet", Document) == []


def test_a_pdf_still_building_is_retried_before_giving_up(tmp_path: Path, clock: Clock) -> None:
    """A PDF that never finishes converting is asked for again, with a growing pause, then given up on."""
    records = {
        "CNTS-00132136027": {
            "bookinfo": bookinfo_html(title="詩傳正音. 中"),
            "detail": detail_html(title="詩傳正音. 中"),
            "pdf_name": "CNTS-00132136027",
            "pdf": pdf_bytes([(10, 10)]),
            "built": False,
        }
    }
    client = transport(records, [])
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00132136027"], out, client=client, checked=CHECKED, build_retries=3)
    assert summary["collected"] == []
    assert "no whole PDF" in summary["unavailable"][0]["error"]
    assert clock.slept.count(nlk.BUILD_BACKOFF * 1) == 1
    assert clock.slept.count(nlk.BUILD_BACKOFF * 2) == 1


def tiled_pdf() -> bytes:
    """One 40×60 pt page drawn as two 80×60 px JPEG tiles, top and bottom: 2 px per point."""
    doc = pymupdf.open()
    page = doc.new_page(width=40, height=60)
    page.insert_image(pymupdf.Rect(0, 0, 40, 30), stream=jpeg(80, 60))
    page.insert_image(pymupdf.Rect(0, 30, 40, 60), stream=jpeg(80, 60))
    part = doc.new_page(width=40, height=60)
    part.insert_image(pymupdf.Rect(0, 0, 40, 30), stream=jpeg(80, 60))  # covers half the page
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def test_a_composite_page_is_rendered_at_its_densest_layer(tmp_path: Path) -> None:
    records = {
        "CNTS-00092710493": {
            "bookinfo": bookinfo_html(title="倭語類解", collation="PDF | 2 p."),
            "detail": detail_html(title="倭語類解"),
            "pdf_name": "KOL000032963",
            "pdf": tiled_pdf(),
        }
    }
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00092710493"], out, client=transport(records, []), checked=CHECKED)
    assert summary["collected"][0]["pages"] == 2
    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.width, p.height) for p in pages] == [(80, 120), (80, 120)]
    assert pages[0].meta["rendered"] == {"zoom": 2.0, "layers": 2}
    assert pages[1].meta["rendered"] == {"zoom": 2.0, "layers": 1}
    assert images.path_for(pages[0].image).suffix == ".png"


def test_a_whole_page_scan_keeps_its_own_bytes(tmp_path: Path) -> None:
    scan = pdf_bytes([(30, 20)])
    records = {
        "CNTS-00132358209": {
            "bookinfo": bookinfo_html(title="老乞大諺解. 上", collation="PDF | 1 p."),
            "detail": detail_html(title="老乞大諺解. 上"),
            "pdf_name": "CNTS-00132358209",
            "pdf": scan,
        }
    }
    out = tmp_path / "nlk"
    nlk.collect(["CNTS-00132358209"], out, client=transport(records, []), checked=CHECKED)
    (page,) = tables.read(out / "pages.parquet", Page)
    assert "rendered" not in page.meta
    assert images.path_for(page.image).read_bytes() == jpeg(30, 20)


def test_a_rerun_reuses_the_cached_pdf(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00092710493": {
            "bookinfo": bookinfo_html(title="倭語類解"),
            "detail": detail_html(title="倭語類解", kol="KOL000032963"),
            "pdf_name": "KOL000032963",
            "pdf": pdf_bytes([(30, 20)]),
        }
    }
    client = transport(records, seen)
    out = tmp_path / "nlk"
    nlk.collect(["CNTS-00092710493"], out, client=client, checked=CHECKED, build_retries=1)
    seen.clear()
    summary = nlk.collect(["CNTS-00092710493"], out, client=client, checked=CHECKED, build_retries=1)
    assert summary["collected"][0]["pages"] == 1
    assert not any(url.endswith(".pdf") for url in seen)


def test_a_record_held_as_page_images_is_fetched_in_the_viewer_session(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00047976255": {
            "bookinfo": bookinfo_html(title="朝鮮司譯院日滿蒙語學書斷簡", collation="JPG | 1권 1책"),
            "detail": detail_html(title="朝鮮司譯院日滿蒙語學書斷簡", publication="[刊寫地未詳]"),
            "images": [png(40, 60), png(42, 61)],
        }
    }
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00047976255"], out, client=transport(records, seen), checked=CHECKED)
    assert summary["collected"][0]["pages"] == 2
    assert summary["collected"][0]["held_as"] == "images"
    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.seq, p.width, p.height) for p in pages] == [(1, 40, 60), (2, 42, 61)]
    assert pages[0].image == nlk.PAGE_IMAGE.format(cno="CNTS-00047976255", vol="0", page=1)
    assert images.path_for(pages[0].image).suffix == ".png"
    (document,) = tables.read(out / "documents.parquet", Document)
    assert "pdf" not in document.source_refs
    assert not any(url.endswith(".pdf") for url in seen)
    assert seen.index(nlk.VIEWER_OPEN) < min(i for i, url in enumerate(seen) if "view_image" in url)


def test_a_page_image_outside_the_viewer_session_fails_the_record(tmp_path: Path) -> None:
    records = {
        "CNTS-00047976255": {
            "bookinfo": bookinfo_html(title="朝鮮司譯院日滿蒙語學書斷簡"),
            "detail": detail_html(title="朝鮮司譯院日滿蒙語學書斷簡"),
            "images": [png(40, 60)],
        }
    }
    client = transport(records, [])
    # A client that drops the session cookie before each request.
    client.event_hooks["request"] = [lambda request: request.headers.pop("Cookie", None)]
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00047976255"], out, client=client, checked=CHECKED, retries=1)
    assert summary["collected"] == []
    assert "view_image.jsp" in summary["unavailable"][0]["error"]


def test_the_pdf_is_fetched_only_after_the_viewer_opens_the_record(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00132358211": {
            "bookinfo": bookinfo_html(title="朴通事諺解. 上", collation="PDF | 1 p."),
            "detail": detail_html(title="朴通事諺解. 上"),
            "pdf_name": "CNTS-00132358211",
            "pdf": pdf_bytes([(10, 10)]),
        }
    }
    summary = nlk.collect(["CNTS-00132358211"], tmp_path / "nlk", client=transport(records, seen),
                          checked=CHECKED, build_retries=1)
    assert summary["collected"][0]["pages"] == 1
    assert seen.index(nlk.VIEWER_OPEN) < seen.index("https://viewer.nl.go.kr/conv/CNTS-00132358211.pdf")


def test_a_pdf_short_of_the_stated_page_count_leaves_the_record_out(tmp_path: Path) -> None:
    records = {
        "CNTS-00132358211": {
            "bookinfo": bookinfo_html(title="朴通事諺解. 上", collation="PDF | 144 p."),
            "detail": detail_html(title="朴通事諺解. 上"),
            "pdf_name": "CNTS-00132358211",
            "pdf": pdf_bytes([(10, 10), (10, 10)]),
        }
    }
    summary = nlk.collect(["CNTS-00132358211"], tmp_path / "nlk", client=transport(records, []),
                          checked=CHECKED, build_retries=1)
    assert summary["collected"] == []
    assert "the PDF has 2 pages, the record states 144" in summary["unavailable"][0]["error"]


def test_a_corrupt_pdf_body_is_retried_and_never_cached(tmp_path: Path) -> None:
    records = {
        "CNTS-00132358211": {
            "bookinfo": bookinfo_html(title="朴通事諺解. 上", collation="PDF | 1 p."),
            "detail": detail_html(title="朴通事諺解. 上"),
            "pdf_name": "CNTS-00132358211",
            "pdf": b"%PDF-1.4 truncated",
        }
    }
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00132358211"], out, client=transport(records, []),
                          checked=CHECKED, build_retries=2)
    assert summary["collected"] == []
    assert "not a readable PDF" in summary["unavailable"][0]["error"]
    assert not (out / "upstream" / "CNTS-00132358211.pdf").exists()

    records["CNTS-00132358211"]["pdf"] = pdf_bytes([(10, 10)])
    summary = nlk.collect(["CNTS-00132358211"], out, client=transport(records, []),
                          checked=CHECKED, build_retries=1)
    assert summary["collected"][0]["pages"] == 1


def dangan(images_: list[bytes]) -> dict[str, dict]:
    return {
        "CNTS-00047976255": {
            "bookinfo": bookinfo_html(title="朝鮮司譯院日滿蒙語學書斷簡"),
            "detail": detail_html(title="朝鮮司譯院日滿蒙語學書斷簡"),
            "images": images_,
        }
    }


def test_a_rerun_reuses_whole_page_images_and_refetches_broken_ones(tmp_path: Path) -> None:
    seen: list[str] = []
    records = dangan([png(40, 60), png(42, 61)])
    out = tmp_path / "nlk"
    nlk.collect(["CNTS-00047976255"], out, client=transport(records, seen), checked=CHECKED)
    folder = out / "upstream" / "CNTS-00047976255"
    (folder / "0002.png").write_bytes(png(42, 61)[:20])  # truncated on disk
    (folder / "0001.download.part").write_bytes(b"left from an interrupted run")
    seen.clear()
    summary = nlk.collect(["CNTS-00047976255"], out, client=transport(records, seen), checked=CHECKED)
    assert summary["collected"][0]["pages"] == 2
    fetched = [url for url in seen if "view_image" in url]
    assert fetched == [nlk.PAGE_IMAGE.format(cno="CNTS-00047976255", vol="0", page=2)]
    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.width, p.height) for p in pages] == [(40, 60), (42, 61)]


def test_a_corrupt_image_body_fails_only_its_record(tmp_path: Path) -> None:
    records = dangan([b"\x89PNG\r\n\x1a\n broken"])
    records.update({
        "CNTS-00132358211": {
            "bookinfo": bookinfo_html(title="朴通事諺解. 上", collation="PDF | 1 p."),
            "detail": detail_html(title="朴通事諺解. 上"),
            "pdf_name": "CNTS-00132358211",
            "pdf": pdf_bytes([(10, 10)]),
        }
    })
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00047976255", "CNTS-00132358211"], out,
                          client=transport(records, []), checked=CHECKED, retries=1)
    assert [entry["id"] for entry in summary["collected"]] == ["CNTS-00132358211"]
    assert "not a readable image" in summary["unavailable"][0]["error"]
    assert not list((out / "upstream" / "CNTS-00047976255").glob("0001*"))


def test_a_viewer_page_listing_several_volumes_fails_the_record() -> None:
    page = b"var vol_maxpage = \"5\"; loadVol('C',1,'/x',5,'1'); loadVol('C',2,'/x',5,'1');"
    with pytest.raises(nlk.RecordError, match="lists volumes"):
        nlk._volume(page, "C")
    assert nlk._volume(b"loadVol('C',3,'/x',5,'1'); loadVol('C',3,'/x',5,'9');", "C") == "3"


def test_a_partial_pdf_is_never_resumed(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00123492041": {
            "bookinfo": bookinfo_html(title="老乞大", collation="PDF | 1 p."),
            "detail": detail_html(title="老乞大"),
            "pdf_name": "CNTS-00123492041_pdf",
            "pdf": pdf_bytes([(10, 10)]),
        }
    }
    out = tmp_path / "nlk"
    (out / "upstream").mkdir(parents=True)
    (out / "upstream" / "CNTS-00123492041.pdf.part").write_bytes(b"%PDF-1.4 from another URL")
    client = transport(records, seen)
    ranged: list[str] = []
    client.event_hooks["request"] = [
        lambda request: ranged.append(str(request.url)) if "Range" in request.headers else None
    ]
    summary = nlk.collect(["CNTS-00123492041"], out, client=client, checked=CHECKED, build_retries=1)
    assert summary["collected"][0]["pdf"] == "https://viewer.nl.go.kr/conv/CNTS-00123492041_pdf.pdf"
    assert ranged == []


def test_a_cached_pdf_without_its_sidecar_is_fetched_again(tmp_path: Path) -> None:
    seen: list[str] = []
    records = {
        "CNTS-00092710493": {
            "bookinfo": bookinfo_html(title="倭語類解"),
            "detail": detail_html(title="倭語類解", kol="KOL000032963"),
            "pdf_name": "KOL000032963",
            "pdf": pdf_bytes([(30, 20)]),
        }
    }
    out = tmp_path / "nlk"
    (out / "upstream").mkdir(parents=True)
    (out / "upstream" / "CNTS-00092710493.pdf").write_bytes(pdf_bytes([(30, 20)]))
    summary = nlk.collect(["CNTS-00092710493"], out, client=transport(records, seen),
                          checked=CHECKED, build_retries=1)
    assert summary["collected"][0]["pages"] == 1
    assert "https://viewer.nl.go.kr/conv/KOL000032963.pdf" in seen


def test_a_viewer_page_naming_no_file_leaves_the_record_out(tmp_path: Path) -> None:
    with pytest.raises(nlk.RecordError, match="neither a PDF nor page images"):
        nlk._viewer(b"<html>nothing here</html>", "CNTS-1")


def test_a_bookinfo_error_page_is_not_kept(tmp_path: Path) -> None:
    records = {
        "CNTS-00132358211": {
            "bookinfo": "<html>error</html>",
            "detail": detail_html(title="朴通事諺解. 上"),
            "pdf_name": "CNTS-00132358211",
            "pdf": pdf_bytes([(10, 10)]),
        }
    }
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00132358211"], out, client=transport(records, []), checked=CHECKED)
    assert summary["unavailable"][0]["id"] == "CNTS-00132358211"
    assert not (out / "upstream" / "CNTS-00132358211.bookinfo.html").exists()


def test_a_network_error_is_retried_then_fails_cleanly(tmp_path: Path, clock: Clock) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = tmp_path / "nlk"
    summary = nlk.collect(["CNTS-00092710493"], out, client=client, retries=3, checked=CHECKED)
    assert len(attempts) == 3
    assert clock.slept
    assert summary["collected"] == [] and summary["unavailable"][0]["id"] == "CNTS-00092710493"
    assert "gave up after 3 attempts" in summary["unavailable"][0]["error"]
    assert tables.read(out / "documents.parquet", Document) == []


def test_a_content_id_must_match_cnts_dash_digits() -> None:
    with pytest.raises(ValueError):
        nlk.content_id("KOL000032963")
    assert nlk.content_id("CNTS-00092710493") == "CNTS-00092710493"
