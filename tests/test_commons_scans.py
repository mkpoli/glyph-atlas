"""The Commons scan collector against a mocked Commons and thumbnail server."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image

from glyph_atlas import net, tables
from glyph_atlas.corpus.sources import discover
from glyph_atlas.importers import commons_scans as scans
from glyph_atlas.importers import wikisource_scans
from glyph_atlas.schema import Document, Page

FILE = "NLC403-312001067176-19028 新釋地理備考全書 卷三.pdf"
OTHER = "SSID-12345 新釋地理備考全書 卷三.pdf"
NLC_TEXT = """=={{int:filedesc}}==
{{NLCBookNaviBar|nth=3|total=10|bookid=312001067176|volumeid=19028}}
{{Digitized Local Records in the National Library of China
  |byline=玛吉士
  |title=新釋地理備考全書
  |volume=卷三
  |abstract=
  |bookid=312001067176
  |volumenth=3
  |volumetotal=10
  |volumeid=19028
  |出版时间=清道光27年[1847]
  |索取号=地900/993
  |版本=刻本
}}

[[Category:新釋地理備考全書]]"""


def jpeg(shade: int, size=(96, 68)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def answer(params: dict[str, str]) -> dict:
    if params.get("list") == "categorymembers":
        assert params["cmtitle"] == "Category:新釋地理備考全書" and params["cmtype"] == "file"
        return {"query": {"categorymembers": [{"title": f"File:{OTHER}"}, {"title": f"File:{FILE}"}]}}
    assert params["titles"] == f"File:{FILE}"
    return {"query": {"pages": [{
        "title": f"File:{FILE}",
        "revisions": [{"revid": 7, "slots": {"main": {"content": NLC_TEXT}}}],
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/wikipedia/commons/b/be/x.pdf?utm_source=a",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:x.pdf",
            "width": 1506, "height": 1112, "pagecount": 3, "size": 6332075, "sha1": "ab",
            "mime": "application/pdf",
            "extmetadata": {"LicenseShortName": {"value": "Public domain"},
                            "Credit": {"value": '<bdi><a href="u"><span>National Library of China</span></a>\n</bdi>'}}}]}]}}


@pytest.fixture
def served():
    net.reset_pauses()
    seen: list[httpx.Request] = []
    shades = {1: 40, 2: 120, 3: 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "upload.wikimedia.org":
            page = int(request.url.path.rsplit("/", 1)[1].split("-", 1)[0].removeprefix("page"))
            return httpx.Response(200, content=jpeg(shades[page]), headers={"content-type": "image/jpeg"})
        params = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        return httpx.Response(200, json=answer(params))

    yield seen, httpx.Client(transport=httpx.MockTransport(handler)), shades
    net.reset_pauses()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def http_for(client) -> wikisource_scans.Http:
    clock = Clock()
    return wikisource_scans.Http(client=client, clock=clock, sleeper=clock.sleep)


def run(tmp_path, served, title=FILE):
    _, client, _ = served
    out = tmp_path / "work" / "dili"
    result = scans.collect(out, [title], http=http_for(client), image_root=tmp_path / "images",
                           scripts=["Hani"], language="zh")
    return out, result


def test_one_file_is_one_document_with_a_page_per_scan_page_and_nothing_transcribed(tmp_path, served):
    seen, _, shades = served
    out, result = run(tmp_path, served)
    documents = tables.read(out / "documents.parquet", Document)
    pages = sorted(tables.read(out / "pages.parquet", Page), key=lambda page: page.seq)
    assert result["tables"] == {"documents": 1, "pages": 3, "page_texts": 0, "lines": 0}
    assert [page.seq for page in pages] == [0, 1, 2]
    assert [page.id for page in pages] == [f"{documents[0].id}:{n}" for n in (1, 2, 3)]
    assert [page.image for page in pages] == [wikisource_scans.thumbnail_url(FILE, n, 1280) for n in (1, 2, 3)]
    for page in pages:
        assert page.sha256 == hashlib.sha256(jpeg(shades[page.meta["pdf_page"]])).hexdigest()
        assert (page.width, page.height) == (96, 68) and page.transcription == {}
    assert {request.headers["user-agent"] for request in seen} == {wikisource_scans.USER_AGENT}
    assert tables.read(out / "page_texts.parquet", wikisource_scans.PageText) == []
    assert tables.read(out / "lines.parquet", wikisource_scans.Line) == []


def test_the_id_follows_the_file_title_not_the_spelling_of_it(tmp_path, served):
    first = run(tmp_path / "a", served)[1]["documents"][0]["document"]
    second = scans.collect(tmp_path / "b", [FILE.replace(" ", "_")], http=http_for(served[1]),
                           image_root=tmp_path / "images")["documents"][0]["document"]
    assert first == second and first.startswith("commons:scan:")


def test_the_file_page_states_the_bibliography_rights_and_dating(tmp_path, served):
    out, _ = run(tmp_path, served)
    [document] = tables.read(out / "documents.parquet", Document)
    assert document.title == "新釋地理備考全書 卷三"
    assert document.shelfmark == "地900/993" and document.holder == "National Library of China"
    assert document.image_rights.licence == "PD" and document.text_rights is None
    assert [(d.kind, d.start, d.end, d.literal) for d in document.dating] == [
        ("publication", 1847, 1847, "清道光27年[1847]")]
    assert document.source_refs["commons-file"] == "https://commons.wikimedia.org/wiki/File:x.pdf"
    commons = document.meta["commons"]
    assert (commons["author"], commons["edition"], commons["publisher"]) == ("玛吉士", "刻本", None)
    assert commons["nlc"]["volumeid"] == "19028" and commons["licence_tags"] == []
    assert document.meta["language"] == "zh" and document.origin == "unknown"
    assert {corpus.name for corpus in discover(tmp_path / "work")} == {"dili"}


def test_a_category_is_filtered_by_the_regular_expression(served):
    titles = scans.category_files(http_for(served[1]), "Category:新釋地理備考全書", r"^File:NLC403-")
    assert titles == [f"File:{FILE}"]


def test_a_file_without_pages_is_an_error(tmp_path):
    class Flat:
        def query(self, host, params):
            return {"pages": [{"title": "File:a.png", "imageinfo": [{
                "url": "u", "descriptionurl": "d", "width": 1, "height": 1, "size": 1}], "revisions": []}]}

    with pytest.raises(wikisource_scans.WikiError):
        scans.collect_file(Flat(), "a.png", image_root=tmp_path)


def test_dates_without_a_bracketed_year():
    assert scans.published_year("清道光27年[1847]") == 1847
    assert scans.published_year("1901") == 1901
    assert scans.published_year("清道光27年") is None
