"""The Wikisource scan collector against a mocked Wikisource, Commons and thumbnail server."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image

from glyph_atlas import net, tables
from glyph_atlas.corpus.sources import discover
from glyph_atlas.importers import wikisource_scans as scans
from glyph_atlas.schema import Document, Page, PageText

FILE = "조선어학회 훈민정음.pdf"
INDEX = f"색인:{FILE}"
PAGE = f"페이지:{FILE}"

PAGE_1 = ('<noinclude><pagequality level="3" user="Aspere" />머리글</noinclude>'
          "ㄱ。牙音。如君字初彂聲\n{{nop}}<noinclude>꼬리글</noinclude>")
PAGE_3 = '<noinclude><pagequality level="0" user="Aspere" /></noinclude>\n<noinclude></noinclude>'
COMMONS_TEXT = """=={{int:filedesc}}==
{{Information
|description={{ko|1=1946년 조선어학회에서 출판한 《훈민정음》 판본.}}
|date=1946
|source=[https://www.nl.go.kr/NL/contents/search.do?#viewKey=CNTS-00047969667&viewType=C National Library of Korea] {{Books from National Library of Korea}}
|author=世宗[세종] / Sejong the Great
}}

=={{int:license-header}}==

{{PD-scan|PD-old-100-expired}}

[[Category:Hunminjeongeum Haerye]]"""


def jpeg(shade: int, size=(96, 136)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def revision(content: str, revid: int) -> list[dict]:
    return [{"revid": revid, "timestamp": "2024-10-03T12:43:30Z", "slots": {"main": {"content": content}}}]


def answer(params: dict[str, str]) -> dict:
    if params.get("meta") == "siteinfo":
        return {"query": {"namespaces": {"250": {"name": "페이지"}, "252": {"name": "색인"}},
                          "rightsinfo": {"url": "https://creativecommons.org/licenses/by-sa/4.0/deed.ko",
                                         "text": "Creative Commons Attribution-Share Alike 4.0"}}}
    if params.get("list") == "proofreadpagesinindex":
        assert params["prppiititle"] == INDEX
        return {"query": {"proofreadpagesinindex": [
            {"pageoffset": 3, "pageid": 13, "title": f"{PAGE}/3", "formattedPageNumber": "3"},
            {"pageoffset": 1, "pageid": 11, "title": f"{PAGE}/1", "formattedPageNumber": "표지"},
            {"pageoffset": 2, "pageid": 0, "title": f"{PAGE}/2", "formattedPageNumber": "2"},
        ]}}
    titles = params.get("titles", "").split("|")
    if "imageinfo" in params.get("prop", ""):
        return {"query": {"pages": [{"title": f"File:{FILE}", "revisions": revision(COMMONS_TEXT, 1109019813),
                                     "imageinfo": [{
                                         "url": "https://upload.wikimedia.org/wikipedia/commons/b/be/x.pdf?utm_source=a",
                                         "descriptionurl": "https://commons.wikimedia.org/wiki/File:x.pdf",
                                         "width": 1239, "height": 1754, "pagecount": 3, "size": 10,
                                         "sha1": "ab", "mime": "application/pdf",
                                         "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]}]}}
    if "proofread" in params.get("prop", ""):
        ids = params["pageids"].split("|")
        assert ids == ["11", "13"]  # a gap in the index is never asked for
        titles = [f"{PAGE}/{int(pageid) - 10}" for pageid in ids]
        pages = {f"{PAGE}/1": {"pageid": 11, "title": f"{PAGE}/1", "revisions": revision(PAGE_1, 101),
                               "proofread": {"quality": 3, "quality_text": "교정됨"},
                               "fullurl": "https://ko.wikisource.org/wiki/p1",
                               "transcludedin": [{"title": "훈민정음", "ns": 0}]},
                 f"{PAGE}/3": {"pageid": 13, "title": f"{PAGE}/3", "revisions": revision(PAGE_3, 103),
                               "proofread": {"quality": 0, "quality_text": "내용 없음"},
                               "fullurl": "https://ko.wikisource.org/wiki/p3"}}
        return {"query": {"pages": [pages[title] for title in titles]}}
    if titles == [INDEX]:
        return {"query": {"pages": [{"title": INDEX, "fullurl": "https://ko.wikisource.org/wiki/index",
                                     "revisions": revision("{{:MediaWiki:Proofreadpage_index_template\n"
                                                           "|제목=[[훈민정음]](訓民正音)\n|언어=ko\n"
                                                           "|출판사=조선어학회\n|연도=1946\n}}", 390571)}]}}
    if titles == ["훈민정음"]:
        return {"query": {"pages": [{"title": "훈민정음", "fullurl": "https://ko.wikisource.org/wiki/hunmin",
                                     "revisions": revision("{{머리말\n|제목 = 훈민정음\n|연도 = 1446\n}}", 460082)}]}}
    raise AssertionError(f"unexpected request {params}")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


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


def read(out):
    return (tables.read(out / "documents.parquet", Document), tables.read(out / "pages.parquet", Page),
            tables.read(out / "page_texts.parquet", PageText))


def run(tmp_path, served):
    _, client, _ = served
    clock = Clock()
    http = scans.Http(client=client, clock=clock, sleeper=clock.sleep)
    out = tmp_path / "work" / "hunminjeongeum"
    result = scans.collect(out, "ko", [INDEX], http=http, image_root=tmp_path / "images", scripts=["Hani", "Hang"])
    return out, result, clock


def test_page_text_is_the_body_and_the_level_is_kept(tmp_path, served):
    out, result, _ = run(tmp_path, served)
    _, pages, texts = read(out)
    assert [text.text_raw for text in texts] == ["ㄱ。牙音。如君字初彂聲\n{{nop}}"]
    assert texts[0].revision == "101"
    first = next(page for page in pages if page.seq == 1)
    assert first.meta["quality"] == 3 and first.meta["quality_user"] == "Aspere" and first.meta["label"] == "표지"
    assert result["documents"][0]["quality_levels"] == {"0": 1, "3": 1, "missing": 1}


def test_page_images_are_in_file_order_with_their_urls_sizes_and_checksums(tmp_path, served):
    seen, _, shades = served
    out, _, _ = run(tmp_path, served)
    pages = sorted(read(out)[1], key=lambda page: page.seq)
    assert [page.seq for page in pages] == [1, 2, 3]
    assert [page.image for page in pages] == [scans.thumbnail_url(FILE, n, 960) for n in (1, 2, 3)]
    for page in pages:
        assert page.sha256 == hashlib.sha256(jpeg(shades[page.seq])).hexdigest()
        assert (page.width, page.height) == (96, 136)
    assert {request.headers["user-agent"] for request in seen} == {scans.USER_AGENT}


def test_thumbnail_url_and_width_follow_commons():
    assert scans.thumbnail_url(FILE, 5, 960) == (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/"
        "%EC%A1%B0%EC%84%A0%EC%96%B4%ED%95%99%ED%9A%8C_%ED%9B%88%EB%AF%BC%EC%A0%95%EC%9D%8C.pdf/page5-960px-"
        "%EC%A1%B0%EC%84%A0%EC%96%B4%ED%95%99%ED%9A%8C_%ED%9B%88%EB%AF%BC%EC%A0%95%EC%9D%8C.pdf.jpg")
    assert scans.thumbnail_width(1239) == 960
    assert scans.thumbnail_width(1500) == 1280
    assert scans.thumbnail_width(1280) == 1280


def test_a_gap_in_the_index_is_a_page_without_text(tmp_path, served):
    out, _, _ = run(tmp_path, served)
    _, pages, texts = read(out)
    by_seq = {page.seq: page for page in pages}
    with_text = {text.page_id for text in texts}
    assert by_seq[2].meta["text_status"] == "missing" and by_seq[2].meta["quality"] is None
    assert by_seq[3].meta["text_status"] == "empty" and by_seq[3].meta["quality"] == 0
    assert by_seq[2].id not in with_text and by_seq[3].id not in with_text
    assert by_seq[2].image and by_seq[2].sha256  # the scan page is still there


def test_document_rights_dating_and_discovery(tmp_path, served):
    out, _, _ = run(tmp_path, served)
    document = read(out)[0][0]
    assert document.title == "조선어학회 훈민정음"
    assert document.holder == "National Library of Korea"
    assert document.image_rights.licence == "PD"
    assert document.text_rights.licence == "CC-BY-SA-4.0"
    assert document.text_rights.holder == "Korean Wikisource contributors"
    assert "https://ko.wikisource.org/wiki/index" in document.text_rights.attribution
    assert document.source_refs["nlk"] == "CNTS-00047969667"
    assert document.meta["commons"]["licence_tags"] == ["{{PD-scan|PD-old-100-expired}}"]
    assert document.meta["language"] == "ko" and document.meta["scripts"] == ["Hani", "Hang"]
    assert {(d.kind, d.start) for d in document.dating} == {("publication", 1946), ("composition", 1446)}
    found = {corpus.name: corpus for corpus in discover(tmp_path / "work")}
    assert found["hunminjeongeum"].searchable and found["hunminjeongeum"].has_page_texts


def test_a_429_is_retried_after_retry_after():
    net.reset_pauses()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={"query": {"namespaces": {}}})

    clock = Clock()
    http = scans.Http(client=httpx.Client(transport=httpx.MockTransport(handler)), clock=clock,
                      sleeper=clock.sleep)
    assert http.api("ko.wikisource.org", {"action": "query"}) == {"query": {"namespaces": {}}}
    assert len(calls) == 2
    assert 7.0 in clock.slept
    net.reset_pauses()


def test_split_page_keeps_only_the_body():
    header, body, footer = scans.split_page('<noinclude><pagequality level="4" user="X" />H</noinclude>B'
                                            "<noinclude>x</noinclude>b<noinclude>F</noinclude>")
    assert body == "B<noinclude>x</noinclude>b"
    assert footer == "F"
    assert scans.quality_of(header) == (4, "X")
    assert scans.split_page("plain") == ("", "plain", "")
