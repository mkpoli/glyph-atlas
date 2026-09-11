"""Tests of the アイヌ関連資料 importer: the platform's entry records into the tables.

Every test builds the platform's responses in `tmp_path` and points the importer's cache there, so
the module never reaches the network. The fixture is three entries of two canvases each, one licence
that the rights vocabulary resolves and one that it records as restricted, one page with text and one
without.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from kuzushiji_atlas import tables
from kuzushiji_atlas.importers import ainu_records as ainu
from kuzushiji_atlas.schema import Document, Line, Page, PageText

FIRST = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
SECOND = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
THIRD = "2b3c4d5e6f708192a3b4c5d6e7f80910"
CC_BY = "https://creativecommons.org/licenses/by/4.0/deed.ja"
RESTRICTED = "https://da.library.ryukoku.ac.jp/support2.html"


def entry_record(
    entry: str,
    *,
    label: str,
    licence: str,
    holder: str,
    texts: list[str],
    host: str = "https://example.test",
) -> dict[str, Any]:
    """One entry as the platform serves it, with a canvas and a transcription per page."""
    canvases = []
    transcriptions = []
    for index, text in enumerate(texts):
        canvas_id = f"{host}/iiif/{entry}/canvas/{index}"
        canvases.append(
            {
                "id": canvas_id,
                "width": 1000 + index,
                "height": 800 + index,
                "imageUrl": f"{host}/iiif/{entry}/full/1000,/0/default.jpg",
                "infoJsonUrl": f"{host}/iiif/{entry}/info.json",
                "thumbnailUrl": f"{host}/iiif/{entry}/full/200,/0/default.jpg",
            }
        )
        transcriptions.append(
            {
                "id": f"{entry}_{index}",
                "entryId": entry,
                "index": index,
                "canvasId": canvas_id,
                "text": text,
                "updatedAt": {"_seconds": 1_760_000_000 + index, "_nanoseconds": 0},
                "status": "completed",
            }
        )
    return {
        "id": entry,
        "projectId": "ainu",
        "index": 1,
        "label": label,
        "size": len(canvases),
        "license": licence,
        "attribution": holder,
        "manifestUrl": f"{host}/iiif/{entry}/manifest.json",
        "metadata": [{"label": "請求記号", "value": "W57-2/U36"}],
        "canvases": canvases,
        "transcriptions": transcriptions,
    }


@pytest.fixture
def platform(tmp_path: Path) -> dict[str, Any]:
    """Three entries in a cache directory, and a source file that lists them."""
    cache = tmp_path / "cache"
    records = {
        FIRST: entry_record(
            FIRST, label="蝦夷方言藻汐草　乾巻（国立国語研究所）", licence=CC_BY, holder="国立国語研究所",
            texts=["蝦夷\n方言\n藻汐", "天地\n人物"], host="https://dglb01.ninjal.ac.jp",
        ),
        SECOND: entry_record(
            SECOND, label="蝦夷記行　第1冊（龍谷大学図書館）", licence=RESTRICTED, holder="龍谷大学図書館",
            texts=["【右丁】\n一　松前の城下\n\n", ""],
        ),
        THIRD: entry_record(
            THIRD, label="蝦夷方言（Wereldmuseum Leiden）", licence="https://collectie.wereldculturen.nl",
            holder="Nationaal Museum van Wereldculturen", texts=["テㇰ\n", ""], host="https://www.dh-jac.net",
        ),
    }
    entries_dir = ainu.records_dir(cache) / "entries"
    entries_dir.mkdir(parents=True)
    for entry, record in records.items():
        (entries_dir / f"{entry}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "ainu-records.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "id": "ainu-records",
                "entries": [
                    {"id": FIRST, "title": "蝦夷方言藻汐草", "holder": "国立国語研究所",
                     "image_licence": CC_BY, "work": "moshiogusa", "witness": "ninjal",
                     "catalogue": "1792-uehara-moshiogusa", "shelfmark": "W57-2/U36"},
                    {"id": SECOND, "title": "蝦夷紀行", "holder": "龍谷大学図書館",
                     "image_licence": RESTRICTED, "work": "ezo-kiko", "witness": "ryukoku",
                     "shelfmark": "491.7-26-W-3"},
                    {"id": THIRD, "title": "蝦夷方言", "holder": "Wereldmuseum Leiden",
                     "image_licence": "https://collectie.wereldculturen.nl", "witness": "leiden"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return {"cache": cache, "source": source, "records": records}


def test_entries_become_documents_pages_texts_and_lines(tmp_path: Path, platform: dict[str, Any]) -> None:
    out = tmp_path / "out"
    counts = ainu.import_all(out, cache=platform["cache"], source_path=platform["source"])

    assert counts["entries"] == 3 and counts["documents"] == 3
    assert counts["pages"] == 6 and counts["page_texts"] == 6
    # 5 lines in the first entry, 2 in the second (【右丁】 and the text line), 1 in the third
    assert counts["lines"] == 5 + 2 + 1
    assert counts["pages_without_text"] == 2, "the second page of the second and of the third entry"

    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    assert set(documents) == {f"hk:{FIRST}", f"hk:{SECOND}", f"hk:{THIRD}"}
    first = documents[f"hk:{FIRST}"]
    assert first.title == "蝦夷方言藻汐草　乾巻（国立国語研究所）", "the platform's label is the title"
    assert first.holder == "国立国語研究所"
    assert first.shelfmark == "W57-2/U36"
    assert first.source_refs["honkoku-data"] == FIRST
    assert first.source_refs["iiif-manifest"].endswith(f"{FIRST}/manifest.json")
    assert first.source_refs["ainu-witness"] == "ninjal"
    assert first.source_refs["aynu-catalogue"] == "1792-uehara-moshiogusa"
    assert first.meta["title_curated"] == "蝦夷方言藻汐草"
    assert first.meta["pages"] == 2
    assert first.image_rights is not None and first.image_rights.licence.value == "CC-BY-4.0"
    assert first.text_rights is not None and first.text_rights.licence.value == "CC-BY-SA-4.0"

    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    assert sorted(pages) == [f"hk:{FIRST}:0", f"hk:{FIRST}:1", f"hk:{SECOND}:0", f"hk:{SECOND}:1",
                             f"hk:{THIRD}:0", f"hk:{THIRD}:1"]
    page = pages[f"hk:{FIRST}:0"]
    assert page.seq == 0 and page.document_id == f"hk:{FIRST}"
    assert page.canvas.endswith(f"{FIRST}/canvas/0")
    assert page.image.endswith(f"{FIRST}/full/1000,/0/default.jpg")
    assert (page.width, page.height) == (1000, 800)
    assert page.meta["status"] == "completed" and page.meta["updated_at"].startswith("2025-")
    assert pages[f"hk:{SECOND}:1"].meta["untranscribed"] is True

    texts = tables.read(out / "page_texts.parquet", PageText)
    assert {row.source for row in texts} == {"ainu-records"}
    assert all(row.revision for row in texts), "every page text carries the platform's stamp"
    body = next(row for row in texts if row.page_id == f"hk:{SECOND}:0")
    assert body.text_raw.startswith("【右丁】"), "the markup is kept raw"


def test_a_licence_that_does_not_resolve_is_recorded_as_restricted(tmp_path: Path, platform: dict[str, Any]) -> None:
    out = tmp_path / "out"
    ainu.import_all(out, cache=platform["cache"], source_path=platform["source"])
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    rights = documents[f"hk:{SECOND}"].image_rights
    assert rights is not None and rights.licence.value == "restricted"
    assert rights.holder == "龍谷大学図書館"
    assert rights.evidence is None or "ryukoku" in (rights.evidence or "") or rights.evidence == RESTRICTED
    leiden = documents[f"hk:{THIRD}"].image_rights
    assert leiden is not None and leiden.licence.value == "restricted"
    assert "Wereldmuseum" in (leiden.holder or "") or "Leiden" in (leiden.attribution or "")


def test_lines_keep_the_transcriber_breaks_and_carry_no_box(tmp_path: Path, platform: dict[str, Any]) -> None:
    out = tmp_path / "out"
    ainu.import_all(out, cache=platform["cache"], source_path=platform["source"])
    lines = tables.read(out / "lines.parquet", Line)
    first_page = [line for line in lines if line.page_id == f"hk:{FIRST}:0"]
    assert [line.text_raw for line in first_page] == ["蝦夷", "方言", "藻汐"]
    assert [line.seq for line in first_page] == [0, 1, 2]
    second_page = [line for line in lines if line.page_id == f"hk:{FIRST}:1"]
    assert [line.text_raw for line in second_page] == ["天地", "人物"]
    assert [line.seq for line in second_page] == [0, 1]
    assert all(line.box is None for line in lines), "no box is invented for a page that has none"
    assert {line.match_method for line in lines} == {ainu.MATCH_METHOD}
    assert first_page[0].id == f"hk:{FIRST}:0:L0"
    # An empty page writes no lines at all rather than an empty one.
    assert not [line for line in lines if line.page_id == f"hk:{SECOND}:1"]
    assert not [line for line in lines if line.page_id == f"hk:{THIRD}:1"]
    # Markup is stripped in `text` and kept in `text_raw`.
    marked = [line for line in lines if line.text_raw.startswith("【右丁】")]
    assert marked and marked[0].text == "", "a line that is only markup becomes empty plain text"


def test_a_rerun_makes_no_request(tmp_path: Path, platform: dict[str, Any], http_server) -> None:
    """With the cache warm the importer reads disk only, which the server's request log shows.

    The fixture server is where a request would go if the importer made one: the cache holds every
    response, and the second import is asked for the same entries as the first.
    """
    out = tmp_path / "out"
    counts = ainu.import_all(out, cache=platform["cache"], source_path=platform["source"])
    first = [document.id for document in tables.read(out / "documents.parquet", Document)]
    assert counts["entries"] == 3

    again = tmp_path / "out2"
    ainu.import_all(again, cache=platform["cache"], source_path=platform["source"])
    assert http_server.requests == [], "the importer reached the network with a warm cache"
    assert first == [document.id for document in tables.read(again / "documents.parquet", Document)]


def test_the_cache_write_is_reused_and_refresh_replaces_it(
    tmp_path: Path, platform: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ainu.entry_path(FIRST, platform["cache"])
    before = path.stat().st_mtime_ns
    ainu.fetch_entry(FIRST, cache=platform["cache"])
    assert path.stat().st_mtime_ns == before, "a cached response is read, not rewritten"
    assert ainu.records_dir(platform["cache"]).name == "ainu-records"


def test_a_transcription_is_placed_by_its_canvas(tmp_path: Path, platform: dict[str, Any]) -> None:
    """The order of the transcriptions array is not the canvas order, and the canvas wins.

    The platform's array arrives in whatever order the database returns it, so a transcription is
    placed by the canvas it names. Here the array is reversed and the `index` field is moved off the
    canvas position, so only the `canvasId` can put the pages in the right order.
    """
    record = dict(platform["records"][FIRST])
    reversed_rows = []
    for offset, row in enumerate(reversed(record["transcriptions"])):
        moved = dict(row)
        moved["index"] = 10 + offset
        reversed_rows.append(moved)
    record["transcriptions"] = reversed_rows
    found = ainu.transcriptions(record)
    assert sorted(found) == [0, 1]
    assert ainu.text_of(found[0]).startswith("蝦夷"), "placed by canvasId, not by array position or index"
    assert ainu.text_of(found[1]).startswith("天地")

    # A transcription whose canvas is not in the canvas list falls back to its own index.
    orphan = dict(record["transcriptions"][0])
    orphan["canvasId"] = "https://example.test/iiisrv/nowhere/canvas/0"
    orphan["index"] = 1
    assert sorted(ainu.transcriptions({"canvases": record["canvases"], "transcriptions": [orphan]})) == [1]


def test_only_narrows_the_run_to_listed_entries(tmp_path: Path, platform: dict[str, Any]) -> None:
    out = tmp_path / "out"
    counts = ainu.import_all(out, cache=platform["cache"], source_path=platform["source"], only=[SECOND])
    assert counts["entries"] == 1 and counts["documents"] == 1
    documents = tables.read(out / "documents.parquet", Document)
    assert [document.id for document in documents] == [f"hk:{SECOND}"]
    with pytest.raises(ValueError):
        ainu.import_all(out, cache=platform["cache"], source_path=platform["source"], only=["nope"])


def test_the_source_file_lists_nine_entries_with_their_rights() -> None:
    """The committed curation: nine entries, thirty-two character ids, a licence for each."""
    document = ainu.load_source()
    ids = ainu.entry_ids(document)
    assert len(ids) == 9
    assert len(set(ids)) == 9
    assert all(len(entry) == 32 and entry.isalnum() for entry in ids)
    rows = ainu.curated(document)
    assert len(rows) == 9
    for entry in ids:
        row = rows[entry]
        assert row.get("holder"), f"{entry}: no holder"
        assert str(row.get("image_licence", "")).startswith("http"), f"{entry}: no licence URL"
        assert row.get("work") and row.get("witness"), f"{entry}: no work or witness slug"
    works = {row["work"] for row in rows.values()}
    assert {"moshiogusa", "ezo-soshi"} <= works
    assert works == {"moshiogusa", "ezo-soshi", "ezo-kiko", "hokkai-zuihitsu", "ezo-yabubanashi",
                     "ezoto-kikan"}, "six works; 藻汐草 has three witnesses and 蝦夷草紙 two"
