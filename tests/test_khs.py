"""Tests for the 국가유산청 Open API collector. No test reaches the network."""

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
from glyph_atlas.importers import khs
from glyph_atlas.schema import Document, Licence, Page, PageText

CHECKED = date(2026, 9, 25)
IID = "12-0008750000400-24"


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


def record_xml(kind: str = "12", number: str = "0008750000400", province: str = "24") -> str:
    """The shape `SearchKindOpenapiDt.do` answers with, CDATA and blank lines included."""
    return f"""<?xml version="1.0" encoding="utf-8"?>


<result>
<ccbaKdcd>{kind}</ccbaKdcd>
<ccbaAsno>{number}</ccbaAsno>
<ccbaCtcd>{province}</ccbaCtcd>
<ccbaCpno>1122408750400</ccbaCpno>
<item>
<ccmaName><![CDATA[보물]]></ccmaName>
<ccbaMnm1><![CDATA[상교정본자비도량참법 권6]]></ccbaMnm1>
<ccbaMnm2><![CDATA[詳校正本慈悲道場懺法 卷六]]></ccbaMnm2>
<ccbaAdmin>
               <![CDATA[전남대학교 도서관]]>
		</ccbaAdmin>
<ccbaPoss><![CDATA[국유]]></ccbaPoss>
<ccbaAsdt>20221026</ccbaAsdt>
<ccbaQuan>1권 1첩</ccbaQuan>
<ccceName>1352년(고려 공민왕 1)</ccceName>
<gcodeName>기록유산</gcodeName>
<bcodeName>전적류</bcodeName>
<mcodeName>전적류</mcodeName>
<scodeName>전적류</scodeName>
<content><![CDATA[본문 가운데는 음독구결도 묵서되어 있다.]]></content>
</item>
</result>
"""


def image_list_xml(photos: list[tuple[str, str]], captions: dict[str, str] | None = None) -> str:
    """The shape `SearchImageOpenapi.do` answers with: every photograph in one `<item>`."""
    fields = "".join(
        f"<sn>{sn}</sn><imageNuri>{nuri}</imageNuri>"
        f"<imageUrl>http://www.khs.go.kr/unisearch/images/treasure/{name}.jpg</imageUrl>"
        f"<ccimDesc><![CDATA[{(captions or {}).get(name, f'사진 {sn}')}]]></ccimDesc>"
        for sn, (name, nuri) in enumerate(photos, 1)
    )
    return f'<?xml version="1.0" encoding="utf-8"?>\n<result><totalCnt>{len(photos)}</totalCnt><item>{fields}</item></result>'


def transport(photos: list[tuple[str, str]], sizes: dict[str, tuple[int, int]], seen: list[str], *, record=None,
              captions=None, record_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("SearchKindOpenapiDt.do"):
            body = record_body.pop(0) if record_body else (record or record_xml())
            return httpx.Response(200, text=body, headers={"Content-Type": "application/xml"})
        if request.url.path.endswith("SearchImageOpenapi.do"):
            return httpx.Response(200, text=image_list_xml(photos, captions), headers={"Content-Type": "text/xml"})
        assert request.url.scheme == "https"
        name = request.url.path.rsplit("/", 1)[1].removesuffix(".jpg")
        return httpx.Response(200, content=jpeg(*sizes[name]), headers={"Content-Type": "image/jpeg"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_kogl_type_1_photographs_of_an_item_become_pages(tmp_path: Path) -> None:
    seen: list[str] = []
    photos = [("a1", "A"), ("b2", "D"), ("c3", "A")]
    client = transport(photos, {"a1": (30, 20), "c3": (40, 25)}, seen)
    out = tmp_path / "khs"
    summary = khs.collect([IID], out, client=client, checked=CHECKED)

    assert summary["collected"] == [{"id": IID, "title": "상교정본자비도량참법 권6", "pages": 2, "left_out": 1,
                                     "credited_elsewhere": 0}]
    assert not any("b2" in url for url in seen)
    (document,) = tables.read(out / "documents.parquet", Document)
    assert document.id == f"khs:{IID}"
    assert document.holder == "전남대학교 도서관"
    assert document.source_refs["catalogue"] == (
        "https://www.heritage.go.kr/heri/cul/culSelectDetail.do?ccbaCpno=1122408750400")
    rights = document.image_rights
    assert rights.licence is Licence.KOGL_1 and rights.holder == "국가유산청"
    assert rights.attribution == "국가유산청, 공공누리 제1유형(출처표시)"
    assert rights.evidence == khs.API_TERMS and rights.checked == CHECKED
    assert document.text_rights is None
    assert document.meta["title_hanja"] == "詳校正本慈悲道場懺法 卷六"
    assert document.meta["category"] == ["기록유산", "전적류"]
    assert "음독구결" in document.meta["description"]
    assert document.dating[0].literal == "1352년(고려 공민왕 1)" and document.dating[0].start is None

    pages = tables.read(out / "pages.parquet", Page)
    assert [(p.seq, p.width, p.meta["sn"], p.meta["caption"]) for p in pages] == [
        (1, 30, "1", "사진 1"), (2, 40, "3", "사진 3")]
    assert all(p.image.startswith("https://") and images.path_for(p.image) is not None for p in pages)
    assert tables.read(out / "page_texts.parquet", PageText) == []

    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tables"] == {"documents": 1, "pages": 2, "page_texts": 0}
    (corpus,) = sources.discover(tmp_path)
    assert corpus.name == "khs"


def test_an_item_with_no_kogl_type_1_photograph_is_skipped_without_fetching_images(tmp_path: Path) -> None:
    seen: list[str] = []
    client = transport([("a1", "D"), ("b2", "B")], {}, seen)
    summary = khs.collect([IID], tmp_path / "out", client=client, checked=CHECKED)
    assert summary["skipped"] == [{"id": IID, "title": "상교정본자비도량참법 권6", "imageNuri": ["B", "D"],
                                   "credited_elsewhere": 0}]
    assert not any("/treasure/" in url for url in seen)
    assert tables.read(tmp_path / "out" / "documents.parquet", Document) == []


def test_an_answer_for_another_item_is_unavailable(tmp_path: Path) -> None:
    seen: list[str] = []
    client = transport([("a1", "A")], {"a1": (10, 10)}, seen, record=record_xml(number="0000010000000"))
    summary = khs.collect([IID], tmp_path / "out", client=client, checked=CHECKED)
    assert summary["collected"] == []
    assert "different item" in summary["unavailable"][0]["error"]


def test_a_network_error_is_retried_then_fails_cleanly(tmp_path: Path, clock: Clock) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = khs.collect([IID], tmp_path / "out", client=client, retries=3, checked=CHECKED)
    assert len(attempts) == 3 and clock.slept
    assert summary["unavailable"][0]["id"] == IID
    assert "gave up after 3 attempts" in summary["unavailable"][0]["error"]


@pytest.mark.parametrize("value", ["12-875-24", "0008750000400", "12_0008750000400_24"])
def test_an_item_id_names_all_three_keys(value: str) -> None:
    with pytest.raises(ValueError):
        khs.item_id(value)


def test_an_error_page_behind_an_xml_head_is_not_kept_for_the_next_run(tmp_path: Path) -> None:
    broken = '<?xml version="1.0" encoding="utf-8"?>\n<result>\n<!DOCTYPE html><html><body>오류</body></html>'
    seen: list[str] = []
    out = tmp_path / "out"
    client = transport([("a1", "A")], {"a1": (10, 10)}, seen, record_body=[broken])
    summary = khs.collect([IID], out, client=client, checked=CHECKED)
    assert "not XML" in summary["unavailable"][0]["error"]
    assert not (out / "upstream" / f"{IID}.xml").exists()
    summary = khs.collect([IID], out, client=client, checked=CHECKED)
    assert summary["collected"][0]["pages"] == 1


def test_a_masked_custodian_is_not_a_holder(tmp_path: Path) -> None:
    masked = record_xml().replace("전남대학교 도서관", "범＊＊＊").replace("국유", "김＊＊＊")
    client = transport([("a1", "A")], {"a1": (10, 10)}, [], record=masked)
    khs.collect([IID], tmp_path / "out", client=client, checked=CHECKED)
    (document,) = tables.read(tmp_path / "out" / "documents.parquet", Document)
    assert document.holder is None
    assert "owner" not in document.meta and document.meta["masked"] == ["ccbaAdmin", "ccbaPoss"]


def test_a_photograph_credited_to_a_private_publisher_is_left_out(tmp_path: Path) -> None:
    seen: list[str] = []
    client = transport([("a1", "A"), ("b2", "A")], {"a1": (10, 10)}, seen, captions={"b2": "경남신문"})
    summary = khs.collect([IID], tmp_path / "out", client=client, checked=CHECKED)
    assert summary["collected"][0] == {"id": IID, "title": "상교정본자비도량참법 권6", "pages": 1,
                                            "left_out": 1, "credited_elsewhere": 1}
    assert not any("b2" in url for url in seen)
