"""Việt Điển responses are synthetic and served locally."""

import json

import pytest
from conftest import Scripted
from typer.testing import CliRunner

from glyph_atlas import net, tables
from glyph_atlas.cli import app
from glyph_atlas.corpus import CorpusIndex, build_chars, discover
from glyph_atlas.corpus.api import CorpusAPI
from glyph_atlas.importers import viet_dien as vd
from glyph_atlas.schema import Document, Licence, Page, PageText


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(net, "CLOCK", lambda: now[0])
    monkeypatch.setattr(net, "SLEEP", lambda n: now.__setitem__(0, now[0] + n))
    net.reset_pauses()
    yield
    net.reset_pauses()


def detail(text_id=11, item="d8-example", numbers=None):
    numbers = [1, 3] if numbers is None else numbers
    return {"id": text_id, "item_id": item, "title": "Quốc âm", "title_han": "國音",
            "page_count": len(numbers), "page_numbers": numbers,
            "status": "complete", "manifest_url": "https://example.org/manifest",
            "source_url": "https://example.org/book"}


def page(number, text_id=11):
    return {"text_id": text_id, "page_number": number, "raw_text": "國𠊛\n 音\n",
            "corrected": number == 3,
            "image_url": f"https://example.org/iiif/{number}/full/!1280,1280/0/default.jpg",
            "viewer_url": f"{vd.BASE}/read/{text_id}/{number}", "source_url": "https://example.org/book"}


def put(server, path, obj):
    server.script[f"/api/v1/{path}"] = [Scripted(200, json.dumps(obj, ensure_ascii=False).encode())]


def populate(server):
    put(server, "texts", {"total": 1, "texts": [{"id": 11, "authors": ["An author"], "year": 1937}]})
    put(server, "texts/11", detail())
    for n in (1, 3):
        put(server, f"texts/11/pages/{n}", page(n))


def run(tmp_path, server, **kw):
    return vd.import_all(tmp_path / "corpora/viet-dien", cache=tmp_path / "cache",
                         base=server.base_url, **kw)


def test_import_replay_and_search(tmp_path, http_server):
    populate(http_server)
    assert run(tmp_path, http_server) == {"documents": 1, "pages": 2, "page_texts": 2}
    out = tmp_path / "corpora/viet-dien"
    doc, = tables.read(out / "documents.parquet", Document)
    pages = tables.read(out / "pages.parquet", Page)
    texts = tables.read(out / "page_texts.parquet", PageText)
    assert doc.id == "viet-dien:api:11"
    assert doc.source_refs["viewer"] == f"{vd.BASE}/read/11/1"
    assert doc.meta["viet_dien"]["year"] == 1937
    assert doc.origin == "unknown" and doc.production == "unknown" and not doc.dating
    assert doc.image_rights is None
    assert doc.text_rights.licence == Licence.RESTRICTED
    assert [p.seq for p in pages] == [0, 2]
    assert [p.meta["corrected"] for p in pages] == [False, True]
    assert pages[1].meta["viewer_url"] == f"{vd.BASE}/read/11/3"
    assert pages[0].image == page(1)["image_url"]
    assert all(t.text_raw == "國𠊛\n 音\n" for t in texts)
    assert len(texts[0].revision) == 64
    before = {p.name: p.read_bytes() for p in out.glob("*.parquet")}
    requests = len(http_server.requests)
    run(tmp_path, http_server)
    assert len(http_server.requests) == requests
    assert before == {p.name: p.read_bytes() for p in out.glob("*.parquet")}
    assert not tables.Dataset(out).validate()
    snapshot = json.loads((out / "UPSTREAM.json").read_text())
    assert len(snapshot["responses"]) == 4 and snapshot["expected_pages"] == 2
    assert snapshot["page_limited"] is False
    corpus, = discover(out.parent)
    assert corpus.searchable and not corpus.has_units
    index_dir = tmp_path / "index"
    build_chars(out.parent, index_dir)
    summary = CorpusIndex(index_dir, out.parent).summary("國")
    assert summary["n_page_hits"] == 2 and summary["n_units"] == 0
    idx = CorpusIndex(index_dir, out.parent)
    idx.build_occurrences("國")
    hits = idx.occurrences("國")
    assert len(hits) == 2
    assert {h.source.source_refs["viewer"] for h in hits} == {
        f"{vd.BASE}/read/11/1", f"{vd.BASE}/read/11/3",
    }
    assert {h.meta["page_meta"]["corrected"] for h in hits} == {False, True}
    api = CorpusAPI(out.parent, index_dir)
    payload = api._item(hits[0])
    assert payload["thumbnail"]["fallback"]["viewer_url"] == hits[0].source.source_refs["viewer"]
    assert payload["review"]["state"] == "machine"
    assert payload["source"]["text_rights"]["licence"] == "restricted"
    assert payload["source"]["text_rights"]["attribution"] == vd.ATTRIBUTION



def test_explicit_ids_and_page_limit(tmp_path, http_server):
    populate(http_server)
    run(tmp_path, http_server, text_ids=[11, 11], page_limit=1)
    assert http_server.requests == ["GET /api/v1/texts", "GET /api/v1/texts/11", "GET /api/v1/texts/11/pages/1"]
    snapshot = json.loads((tmp_path / "corpora/viet-dien/UPSTREAM.json").read_text())
    assert snapshot["page_limited"] and snapshot["imported_pages"] == 1


def test_catalogue_pagination(tmp_path, http_server):
    for payload in ({"total": 2, "texts": [{"id": 11}]}, {"total": 2, "texts": [{"id": 12}]}):
        http_server.script.setdefault("/api/v1/texts", []).append(Scripted(200, json.dumps(payload).encode()))
    api = vd.API(tmp_path, base=http_server.base_url)
    assert [t["id"] for t in api.texts(0)] == [11, 12]
    assert list(api.responses)[1].endswith("limit=100&offset=1")


@pytest.mark.parametrize("second", [
    {"total": 3, "texts": [{"id": 12}]},
    {"total": 2, "texts": [{"id": 11}]},
    {"total": 2, "texts": []},
])
def test_inconsistent_catalogue(tmp_path, http_server, second):
    first = {"total": 2, "texts": [{"id": 11}]}
    http_server.script["/api/v1/texts"] = [Scripted(200, json.dumps(p).encode()) for p in (first, second)]
    with pytest.raises(ValueError):
        vd.API(tmp_path, base=http_server.base_url).texts(0)


@pytest.mark.parametrize("change", [
    {"page_count": 3}, {"page_numbers": [1, 1]}, {"page_numbers": [0, 3]}, {"id": 12},
])
def test_bad_detail(tmp_path, http_server, change):
    populate(http_server)
    put(http_server, "texts/11", {**detail(), **change})
    with pytest.raises(ValueError):
        run(tmp_path, http_server)
    assert not (tmp_path / "corpora/viet-dien/documents.parquet").exists()


@pytest.mark.parametrize("change", [{"text_id": 12}, {"page_number": 3}, {"corrected": "false"}])
def test_bad_page_leaves_existing_output(tmp_path, http_server, change):
    populate(http_server)
    out = tmp_path / "corpora/viet-dien"
    out.mkdir(parents=True)
    (out / "documents.parquet").write_bytes(b"previous snapshot")
    put(http_server, "texts/11/pages/1", {**page(1), **change})
    with pytest.raises(ValueError):
        run(tmp_path, http_server)
    assert (out / "documents.parquet").read_bytes() == b"previous snapshot"


def test_volumes_sharing_a_library_item_keep_distinct_identities():
    old = vd.records(detail(), {}, [(page(1), "hash")], "revision")
    new = vd.records(detail(text_id=999), {}, [(page(1, text_id=999), "hash")], "revision")
    assert old[0].id != new[0].id
    assert old[0].source_refs["viet-dien"] == new[0].source_refs["viet-dien"]
    assert old[0].source_refs["viewer"] != new[0].source_refs["viewer"]
    assert old[1][0].id != new[1][0].id
    assert old[1][0].meta["viewer_url"] != new[1][0].meta["viewer_url"]


def test_cli_validation_and_help():
    runner = CliRunner()
    assert runner.invoke(app, ["import", "viet-dien", "--page-limit", "-1"]).exit_code == 2
    assert runner.invoke(app, ["import", "viet-dien", "--text-id", "0"]).exit_code == 2
    assert runner.invoke(app, ["import", "viet-dien", "--help"]).exit_code == 0


def test_local_upload_without_library_id(tmp_path, http_server):
    populate(http_server)
    put(http_server, "texts/11", {**detail(), "item_id": None, "manifest_url": None})
    put(http_server, "texts/11/pages/1", {**page(1), "image_url": "/img/book/page_001.webp"})
    run(tmp_path, http_server, page_limit=1)
    out = tmp_path / "corpora/viet-dien"
    doc, = tables.read(out / "documents.parquet", Document)
    pg, = tables.read(out / "pages.parquet", Page)
    assert doc.id == "viet-dien:api:11" and doc.meta["identity_basis"] == "api_id"
    assert doc.source_refs["viewer"] == f"{vd.BASE}/read/11/1"
    assert pg.meta["viewer_url"] == f"{vd.BASE}/read/11/1"
    assert pg.image == f"{vd.BASE}/img/book/page_001.webp"


def test_selection_method_keeps_catalogue_metadata(tmp_path, http_server):
    populate(http_server)
    run(tmp_path, http_server)
    path = tmp_path / "corpora/viet-dien/documents.parquet"
    before = path.read_bytes()
    # A distinct catalogue URL (limit=100) uses the same upstream row.
    put(http_server, "texts", {"total": 1, "texts": [{"id": 11, "authors": ["An author"], "year": 1937}]})
    run(tmp_path, http_server, text_ids=[11])
    assert path.read_bytes() == before


def test_table_failure_preserves_snapshot(tmp_path, http_server, monkeypatch):
    populate(http_server)
    run(tmp_path, http_server)
    out = tmp_path / "corpora/viet-dien"
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    write = tables.write

    def fail_pages(path, *args, **kwargs):
        if path.name == "pages.parquet":
            raise OSError("simulated write failure")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(tables, "write", fail_pages)
    with pytest.raises(OSError, match="simulated"):
        run(tmp_path, http_server)
    assert before == {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}


def test_directory_swap_failure_restores_snapshot(tmp_path, http_server, monkeypatch):
    from pathlib import Path

    populate(http_server)
    run(tmp_path, http_server)
    out = tmp_path / "corpora/viet-dien"
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    replace = Path.replace

    def fail_publish(self, target):
        if target == out and self.name.startswith(".viet-dien-") and not self.name.endswith("-previous"):
            raise OSError("simulated publication failure")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(OSError, match="publication"):
        run(tmp_path, http_server)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_import_preserves_derived_tables(tmp_path, http_server):
    populate(http_server)
    run(tmp_path, http_server)
    out = tmp_path / "corpora/viet-dien"
    (out / "units.parquet").write_bytes(b"derived glyphs")
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(ValueError, match="derived tables"):
        run(tmp_path, http_server)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_older_occurrence_file_can_still_be_read(tmp_path, http_server):
    import pyarrow.parquet as pq

    populate(http_server)
    run(tmp_path, http_server)
    root, directory = tmp_path / "corpora", tmp_path / "index"
    idx = CorpusIndex(directory, root)
    idx.build_occurrences("國")
    path = idx.occ_path("國")
    old = pq.read_table(path).drop(["source_refs"])
    pq.write_table(old, path)
    hits = CorpusIndex(directory, root).occurrences("國")
    assert len(hits) == 2 and all(h.source.source_refs == {} for h in hits)


def export_body(numbers=(1, 3), raw=('國𠊛\n 音\n', '')):
    return ('# Quốc âm\n# 國音\n# Việt Điển text 11 — 2 pages\n'
            '# https://viet-dien.com/read/11/1\n\n' +
            '\n'.join(f'--- page {n} ---\n{text}' for n, text in zip(numbers, raw, strict=True))).encode()


def test_bulk_export_preserves_text_and_unknown_metadata(tmp_path, http_server):
    populate(http_server)
    http_server.script['/api/v1/texts/11/full.txt'] = [Scripted(200, export_body())]
    assert run(tmp_path, http_server, bulk_text=True, page_limit=0) == {
        'documents': 1, 'pages': 2, 'page_texts': 2,
    }
    out = tmp_path / 'corpora/viet-dien'
    pages = tables.read(out / 'pages.parquet', Page)
    texts = tables.read(out / 'page_texts.parquet', PageText)
    assert [t.text_raw for t in texts] == ['國𠊛\n 音\n', '']
    assert all(p.image == '' and p.meta['corrected'] is None and p.meta['text_status'] == 'unknown' for p in pages)
    assert [p.meta['viewer_url'] for p in pages] == [f'{vd.BASE}/read/11/{n}' for n in (1, 3)]
    assert all(p.meta['text_transport'] == 'full.txt' for p in pages)
    assert http_server.requests[-1] == 'GET /api/v1/texts/11/full.txt'
    assert len(http_server.requests) == 3
    before = {p.name: p.read_bytes() for p in out.glob('*.parquet')}
    run(tmp_path, http_server, bulk_text=True, page_limit=0)
    assert len(http_server.requests) == 3
    assert before == {p.name: p.read_bytes() for p in out.glob('*.parquet')}
    snapshot = json.loads((out / 'UPSTREAM.json').read_text())
    assert snapshot['text_transport'] == 'full.txt' and not snapshot['page_limited']
    idx = CorpusIndex(tmp_path / 'index', out.parent)
    idx.build_occurrences('國')
    hit, = idx.occurrences('國')
    assert hit.meta['page_meta']['corrected'] is None
    assert hit.meta['page_meta']['text_status'] == 'unknown'


@pytest.mark.parametrize('body', [
    export_body(numbers=(1, 2)), export_body(numbers=(1, 1)),
    export_body().replace(b'text 11', b'text 12'),
    export_body().replace(b'2 pages', b'3 pages'),
    export_body().replace(b'--- page 3 ---\n', b''),
    b'<html>upstream error</html>',
])
def test_bulk_export_rejects_mismatched_snapshot(body):
    with pytest.raises(ValueError):
        vd.export_pages(body, detail())


def test_bulk_export_checks_unselected_pages(tmp_path, http_server):
    populate(http_server)
    http_server.script['/api/v1/texts/11/full.txt'] = [Scripted(200, export_body(numbers=(1, 2)))]
    with pytest.raises(ValueError, match='markers'):
        run(tmp_path, http_server, bulk_text=True, page_limit=1)
    assert not (tmp_path / 'corpora/viet-dien/documents.parquet').exists()


def test_full_import_retains_volumes_sharing_one_library_item(tmp_path, http_server):
    populate(http_server)
    put(http_server, 'texts', {'total': 2, 'texts': [{'id': 11}, {'id': 12}]})
    # As upstream's 道教源流: one title_han, the volume only in the romanised title.
    put(http_server, 'texts/11', {**detail(), 'title': 'Đạo giáo nguyên lưu (quyển thượng)', 'title_han': '道教源流'})
    put(http_server, 'texts/12', {**detail(text_id=12), 'title': 'Đạo giáo nguyên lưu (quyển hạ)', 'title_han': '道教源流'})
    for n in (1, 3):
        put(http_server, f'texts/12/pages/{n}', page(n, text_id=12))
    assert run(tmp_path, http_server, limit=0) == {'documents': 2, 'pages': 4, 'page_texts': 4}
    out = tmp_path / 'corpora/viet-dien'
    docs = tables.read(out / 'documents.parquet', Document)
    pages = tables.read(out / 'pages.parquet', Page)
    assert {d.id: d.title for d in docs} == {'viet-dien:api:11': '道教源流 · Đạo giáo nguyên lưu (quyển thượng)',
                                             'viet-dien:api:12': '道教源流 · Đạo giáo nguyên lưu (quyển hạ)'}
    assert {d.source_refs['viet-dien'] for d in docs} == {'d8-example'}
    assert {p.meta['viewer_url'] for p in pages} == {
        f'{vd.BASE}/read/{i}/{n}' for i in (11, 12) for n in (1, 3)
    }
    assert len({p.transcription['entry'] for p in pages}) == 2
