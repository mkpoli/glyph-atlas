"""Tests for the みんなで翻刻データ v3 importer.

The clone, the manifest cache and the datasets live in `tmp_path`, and every request goes to the
`http_server` fixture; the pauses run on a fake clock, so no test waits or reaches the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import Scripted
from conftest import TestServer as Server

from kuzushiji_atlas import net, rights, tables
from kuzushiji_atlas.importers import honkoku_data
from kuzushiji_atlas.schema import Document, Licence, Page, PageText

COMMIT = "be63dc209b65ed81d1f51ea5cd10bc09be261640"
ENTRY = "0A678AA21E602F6A3FFF3329B090920C"
ENTRY_MISSING = "1B678AA21E602F6A3FFF3329B090920D"
ENTRY_P3 = "2C678AA21E602F6A3FFF3329B090920E"
ENTRY_EXTRA = "3D678AA21E602F6A3FFF3329B090920F"
UNLISTED = "4E678AA21E602F6A3FFF3329B0909210"
HOLDER = "国文学研究資料館"
ELSEWHERE = "架空の文庫"
HEADERS = "id\tlabel\tmanifestUrl\tprojectId\tsize\tprogress\tattribution\tthumbnail\n"


class Clock:
    """A monotonic clock that moves only when the code under test sleeps."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Keep every pause in a fake clock, so that a run does not wait for the hosts."""
    fake = Clock()
    monkeypatch.setattr(net, "CLOCK", fake)
    monkeypatch.setattr(net, "SLEEP", fake.sleep)
    net.reset_pauses()
    yield fake
    net.reset_pauses()


def canvas(server: Server, number: int, service: str = "iiif", width: int = 4064, height: int = 2888) -> dict:
    """One Presentation 2 canvas whose image service is `{base}/{service}/{number}`."""
    base = server.base_url
    return {
        "@id": f"{base}/canvas/{number}",
        "@type": "sc:Canvas",
        "label": str(number),
        "width": width,
        "height": height,
        "images": [
            {
                "@id": f"{base}/annotation/{number}",
                "@type": "oa:Annotation",
                "motivation": "sc:painting",
                "resource": {
                    "@id": f"{base}/{service}/{number}/full/full/0/default.jpg",
                    "@type": "dctypes:Image",
                    "format": "image/jpeg",
                    "width": width,
                    "height": height,
                    "service": {
                        "@context": "http://iiif.io/api/image/2/context.json",
                        "@id": f"{base}/{service}/{number}",
                        "profile": "http://iiif.io/api/image/2/level1.json",
                    },
                },
                "on": f"{base}/canvas/{number}",
            }
        ],
    }


def p2_manifest(server: Server, pages: int = 2) -> dict:
    return {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "@id": server.url("manifests/p2.json"),
        "@type": "sc:Manifest",
        "label": "仮名文書",
        "license": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": HOLDER,
        "sequences": [
            {"@type": "sc:Sequence", "canvases": [canvas(server, number) for number in range(1, pages + 1)]}
        ],
    }


def p3_manifest(server: Server, pages: int = 2) -> dict:
    """A Presentation 3 manifest, with a thumbnail that is not the page image."""
    base = server.base_url
    canvases = []
    for number in range(1, pages + 1):
        body = {
            "id": f"{base}/iiif/3/{number}/full/max/0/default.jpg",
            "type": "Image",
            "format": "image/jpeg",
            "service": [
                {
                    "id": f"{base}/iiif/3/{number}",
                    "type": "ImageService3",
                    "profile": "level2",
                }
            ],
        }
        canvases.append(
            {
                "id": f"{base}/canvas3/{number}",
                "type": "Canvas",
                "label": {"none": [str(number)]},
                "width": 6048,
                "height": 4034,
                "items": [
                    {
                        "id": f"{base}/page/{number}",
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": f"{base}/annotation3/{number}",
                                "type": "Annotation",
                                "motivation": "painting",
                                "body": body,
                                "target": f"{base}/canvas3/{number}",
                            }
                        ],
                    }
                ],
                "thumbnail": [{"id": f"{base}/thumbnail/{number}", "type": "Image"}],
            }
        )
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": server.url("manifests/p3.json"),
        "type": "Manifest",
        "label": {"none": ["仮名文書 四"]},
        "rights": "http://creativecommons.org/publicdomain/mark/1.0/",
        "requiredStatement": {"label": {"none": ["Credit"]}, "value": {"none": ["架空の文庫"]}},
        "items": canvases,
    }


@pytest.fixture
def clone(tmp_path: Path, http_server: Server) -> Path:
    """A one-project clone: two pages on a Presentation 2 manifest, a Presentation 3 manifest, a
    manifest that 404s, an entry with more text files than canvases, and the clone's commit."""
    http_server.put("manifests/p2.json", json.dumps(p2_manifest(http_server)).encode("utf-8"))
    http_server.put("manifests/p3.json", json.dumps(p3_manifest(http_server)).encode("utf-8"))
    root = tmp_path / "clone"
    project = root / "v3" / "demo"
    project.mkdir(parents=True)
    (project / "info.tsv").write_text(
        HEADERS
        + "\t".join([ENTRY_EXTRA, "仮名文書 三", http_server.url("manifests/p2.json"), "demo", "3", "2", HOLDER, ""])
        + "\n"
        + "\t".join([ENTRY, "仮名文書", http_server.url("manifests/p2.json"), "demo", "2", "2", HOLDER, ""])
        + "\n"
        + "\t".join(
            [ENTRY_MISSING, "仮名文書 二", http_server.url("manifests/missing.json"), "demo", "1", "1", ELSEWHERE, ""]
        )
        + "\n"
        + "\t".join([ENTRY_P3, "仮名文書 四", http_server.url("manifests/p3.json"), "demo", "2", "1", HOLDER, ""])
        + "\n"
        + "Keio University Libraries\t\thttps://example.org/keio.json\n",
        encoding="utf-8",
    )
    pages = {
        ENTRY: ["【右丁】\n一\n", "二\n"],
        ENTRY_MISSING: ["三\n"],
        ENTRY_P3: ["四\n", "五\n"],
        ENTRY_EXTRA: ["六\n", "七\n", "八\n"],
        UNLISTED: ["九\n"],
    }
    for entry, texts in pages.items():
        entry_dir = project / entry
        entry_dir.mkdir()
        for number, text in enumerate(texts, start=1):
            (entry_dir / f"{number:03d}.txt").write_text(text, encoding="utf-8")
        (entry_dir / "info.tsv").write_text("filename\tstatus\timage\n", encoding="utf-8")
        translations = entry_dir / "translations"
        translations.mkdir()
        (translations / "001_en.txt").write_text("first\n", encoding="utf-8")
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (git / "refs" / "heads" / "master").write_text(COMMIT + "\n", encoding="utf-8")
    return root


@pytest.fixture
def imported(tmp_path: Path, clone: Path) -> tuple[Path, dict[str, int]]:
    """`work/honkoku-data` and the counts of a full import of the fixture clone."""
    out = tmp_path / "work" / "honkoku-data"
    counts = honkoku_data.import_all(out, clone=clone, cache=tmp_path / "cache")
    return out, counts


def test_import_counts_every_page_of_the_clone(imported: tuple[Path, dict[str, int]]) -> None:
    _, counts = imported
    assert counts["projects"] == 1
    assert counts["documents"] == 4
    assert counts["pages"] == 8
    assert counts["page_texts"] == 8
    assert counts["manifest_errors"] == 1
    assert counts["pages_without_canvas"] == 1
    assert counts["entries_skipped"] == 1
    assert counts["entries_unlisted"] == 1


def test_document_carries_the_label_holder_and_rights(imported: tuple[Path, dict[str, int]]) -> None:
    out, _ = imported
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    assert set(documents) == {f"hk:{ENTRY}", f"hk:{ENTRY_MISSING}", f"hk:{ENTRY_P3}", f"hk:{ENTRY_EXTRA}"}
    document = documents[f"hk:{ENTRY}"]
    assert document.title == "仮名文書"
    assert document.holder == HOLDER
    assert document.source_refs["honkoku-data"] == ENTRY
    assert document.source_refs["iiif-manifest"].endswith("manifests/p2.json")
    assert document.source_refs["honkoku-project"] == "demo"
    assert document.image_rights.licence is Licence.CC_BY_SA_4
    assert document.image_rights.holder == HOLDER
    assert document.text_rights.licence is Licence.CC_BY_SA_4
    assert document.meta == {"size": "2", "progress": "2"}


def test_image_rights_come_from_the_manifest_and_fall_back_to_the_holder(
    imported: tuple[Path, dict[str, int]],
) -> None:
    out, _ = imported
    documents = {document.id: document for document in tables.read(out / "documents.parquet", Document)}
    p3 = documents[f"hk:{ENTRY_P3}"]
    assert p3.image_rights.licence is Licence.PDM
    assert p3.image_rights.attribution == ELSEWHERE
    failed = documents[f"hk:{ENTRY_MISSING}"]
    assert failed.image_rights.licence is Licence.UNKNOWN
    assert failed.image_rights.holder == ELSEWHERE
    assert failed.image_rights == rights.resolve(holder=ELSEWHERE)


def test_page_maps_to_the_canvas_of_its_number(imported: tuple[Path, dict[str, int]], http_server: Server) -> None:
    out, _ = imported
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    first, second = pages[f"hk:{ENTRY}:1"], pages[f"hk:{ENTRY}:2"]
    assert first.canvas == http_server.url("canvas/1")
    assert first.image == http_server.url("iiif/1")
    assert (first.width, first.height) == (4064, 2888)
    assert first.seq == 1 and first.transcription == {"source": "honkoku-data", "entry": ENTRY, "revision": COMMIT}
    assert second.canvas == http_server.url("canvas/2") and second.image == http_server.url("iiif/2")
    assert first.meta == {} and second.meta == {}


def test_presentation_three_canvases_are_read_too(imported: tuple[Path, dict[str, int]]) -> None:
    out, _ = imported
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    first = pages[f"hk:{ENTRY_P3}:1"]
    assert first.canvas.endswith("/canvas3/1")
    assert first.image.endswith("/iiif/3/1")
    assert not first.image.endswith("/thumbnail/1")
    assert (first.width, first.height) == (6048, 4034)


def test_a_manifest_that_fails_keeps_the_pages_without_an_image(imported: tuple[Path, dict[str, int]]) -> None:
    out, _ = imported
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    page = pages[f"hk:{ENTRY_MISSING}:1"]
    assert page.canvas is None and page.image == "" and (page.width, page.height) == (0, 0)
    assert "HTTP 404" in page.meta["manifest_error"]
    assert page.seq == 1


def test_more_text_files_than_canvases_warns(clone: Path, tmp_path: Path) -> None:
    out = tmp_path / "work"
    with pytest.warns(RuntimeWarning, match="has 2 canvases"):
        counts = honkoku_data.import_all(out, clone=clone, cache=tmp_path / "cache")
    assert counts["pages_without_canvas"] == 1
    pages = {page.id: page for page in tables.read(out / "pages.parquet", Page)}
    extra = pages[f"hk:{ENTRY_EXTRA}:3"]
    assert extra.canvas is None and extra.image == "" and extra.meta == {"canvas_missing": True}
    assert f"hk:{ENTRY_EXTRA}:2" in pages


def test_page_texts_hold_the_file_and_the_clone_commit(imported: tuple[Path, dict[str, int]]) -> None:
    out, _ = imported
    rows = tables.read(out / "page_texts.parquet", PageText)
    assert len(rows) == 8
    assert {row.source for row in rows} == {"honkoku-data"}
    assert {row.revision for row in rows} == {COMMIT}
    first = next(row for row in rows if row.page_id == f"hk:{ENTRY}:1")
    assert first.text_raw == "【右丁】\n一\n"
    assert not any(row.page_id.endswith(":0") for row in rows)


def test_a_clone_without_git_has_no_revision(clone: Path, tmp_path: Path) -> None:
    assert honkoku_data.clone_revision(clone) == COMMIT
    assert honkoku_data.clone_revision(tmp_path / "nowhere") is None
    bare = tmp_path / "bare"
    bare.mkdir()
    assert honkoku_data.clone_revision(bare) is None


def test_projects_and_limit_choose_the_entries(clone: Path, tmp_path: Path) -> None:
    out = tmp_path / "limited"
    counts = honkoku_data.import_all(out, clone=clone, projects=["demo"], limit=1, cache=tmp_path / "cache")
    assert counts["documents"] == 1 and counts["pages"] == 2
    documents = tables.read(out / "documents.parquet", Document)
    assert [document.id for document in documents] == [f"hk:{ENTRY}"]
    with pytest.warns(RuntimeWarning, match="not a project"):
        empty = honkoku_data.import_all(
            tmp_path / "none", clone=clone, projects=["nope"], cache=tmp_path / "cache"
        )
    assert empty["documents"] == 0 and empty["projects"] == 0


def test_the_same_clone_imports_to_the_same_bytes(tmp_path: Path, clone: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    honkoku_data.import_all(first, clone=clone, cache=tmp_path / "cache")
    honkoku_data.import_all(second, clone=clone, cache=tmp_path / "cache")
    for name in ("documents.parquet", "pages.parquet", "page_texts.parquet"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_manifests_are_cached_by_the_sha256_of_the_url(
    clone: Path, tmp_path: Path, http_server: Server
) -> None:
    out = tmp_path / "work"
    honkoku_data.import_all(out, clone=clone, cache=tmp_path / "cache")
    cached = {path.name for path in (tmp_path / "cache" / "manifests").glob("*.json")}
    assert cached == {
        hashlib.sha256(http_server.url(name).encode("utf-8")).hexdigest() + ".json"
        for name in ("manifests/p2.json", "manifests/p3.json")
    }


def test_refresh_writes_the_platform_text_beside_the_clone_text(
    imported: tuple[Path, dict[str, int]], tmp_path: Path, http_server: Server
) -> None:
    out, _ = imported
    http_server.put(
        f"api/entries/{ENTRY}",
        json.dumps(
            {
                "id": ENTRY,
                "label": "仮名文書",
                "transcriptions": [
                    {
                        "index": "0",
                        "text": "【右丁】\n一\n",
                        "updatedAt": "{'_seconds': 1754597011, '_nanoseconds': 920000000}",
                    },
                    {"index": 1, "text": "二\n", "updatedAt": {"_seconds": 1754597042}},
                    {"index": "2", "text": "三\n", "updatedAt": None},
                ],
            }
        ).encode("utf-8"),
    )
    base = http_server.url("api/entries/{entry}")
    with pytest.warns(RuntimeWarning, match="1 transcriptions name no page"):
        written = honkoku_data.refresh(out, f"hk:{ENTRY}", base=base)
    assert written == 2
    rows = tables.read(out / "page_texts.parquet", PageText)
    first = next(row for row in rows if row.page_id == f"hk:{ENTRY}:1")
    assert first.source == "honkoku-data" and first.revision == COMMIT
    api_rows = {row.page_id: row for row in rows if row.source == "honkoku-api"}
    assert set(api_rows) == {f"hk:{ENTRY}:1", f"hk:{ENTRY}:2"}
    assert api_rows[f"hk:{ENTRY}:1"].revision == "2025-08-07T20:03:31Z"
    assert api_rows[f"hk:{ENTRY}:1"].text_raw == "【右丁】\n一\n"
    assert api_rows[f"hk:{ENTRY}:2"].revision == "2025-08-07T20:04:02Z"
    assert len(tables.read(out / "page_texts.parquet", PageText)) == 10
    assert honkoku_data.refresh(out, ENTRY, base=base) == 2
    assert len(tables.read(out / "page_texts.parquet", PageText)) == 10
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["page_texts"] == 10


def test_refresh_needs_an_imported_entry(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no pages table"):
        honkoku_data.refresh(tmp_path / "nowhere", ENTRY)

def test_a_host_that_keeps_failing_is_left_alone(tmp_path: Path, http_server: Server) -> None:
    """Five manifests of one host in a row are enough; the rest record why they have no image."""
    project = tmp_path / "clone" / "v3" / "demo"
    project.mkdir(parents=True)
    rows = [HEADERS.rstrip("\n")]
    for number in range(7):
        http_server.script[f"/manifests/broken-{number}.json"] = [Scripted(500, b"upstream error") for _ in range(4)]
        entry = f"{number:032X}"
        rows.append(
            "\t".join(
                [entry, f"資料{number}", http_server.url(f"manifests/broken-{number}.json"), "demo", "1", "0", HOLDER, ""]
            )
        )
        entry_dir = project / entry
        entry_dir.mkdir()
        (entry_dir / "001.txt").write_text("一\n", encoding="utf-8")
    (project / "info.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    counts = honkoku_data.import_all(tmp_path / "work", clone=tmp_path / "clone", cache=tmp_path / "cache")
    assert counts["manifest_errors"] == 7
    assert counts["hosts_abandoned"] == 1
    asked = [request for request in http_server.requests if "broken-" in request]
    assert len(asked) == honkoku_data.HOST_FAILURES * honkoku_data.MANIFEST_RETRIES
    pages = {page.id: page for page in tables.read(tmp_path / "work" / "pages.parquet", Page)}
    assert "left alone" in pages[f"hk:{6:032X}:1"].meta["manifest_error"]
    assert "HTTP 500" in pages[f"hk:{0:032X}:1"].meta["manifest_error"]
