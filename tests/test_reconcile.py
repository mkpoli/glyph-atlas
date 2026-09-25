"""Tests for rights reconciliation, the report and the attribution file.

No test reaches the network: a document whose manifest is on a remote host gets it from a seeded
cache file, and the one manifest that is fetched comes from the `http_server` fixture. No test reads
the repository's `cache/`; every case passes its own cache directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import Scripted

from glyph_atlas import reconcile, rights, tables
from glyph_atlas.schema import Box, Document, Licence, Line, Page, Rights, Unit

P2 = "http://iiif.io/api/presentation/2/context.json"
PDM_DEED = "https://creativecommons.org/publicdomain/mark/1.0/deed.ja"
BY_DEED = "https://creativecommons.org/licenses/by/4.0/deed.ja"
SA_DEED = "https://creativecommons.org/licenses/by-sa/4.0/deed.ja"
NDL = "国立国会図書館"
NIJL = "国文学研究資料館"

HOLDERS_YAML = f"""\
- id: nijl
  ja: {NIJL}
  licence: per-item
  terms: https://kokusho.nijl.ac.jp/page/terms.html
- id: ndl
  ja: {NDL}
  terms: https://www.ndl.go.jp/jp/use/reproduction/index.html
"""


def manifest(*, licence: str | None = None, attribution: str | None = None) -> dict:
    """A IIIF Presentation 2 manifest with the rights fields given."""
    found: dict = {"@context": P2, "label": "Test"}
    if licence is not None:
        found["license"] = licence
    if attribution is not None:
        found["attribution"] = attribution
    return found


def document(
    ident: str,
    *,
    holder: str | None = None,
    licence: str | None = None,
    url: str | None = None,
    manifest_url: str | None = None,
    refs: dict[str, str] | None = None,
    image_rights: Rights | None = None,
    title: str | None = None,
) -> Document:
    """A document as an importer would leave it: the upstream fields in `meta`, a resolved licence."""
    meta: dict = {}
    if licence is not None:
        meta["image_license"] = licence
    if url is not None:
        meta["image_license_url"] = url
    source_refs = dict(refs or {})
    if manifest_url is not None:
        source_refs["iiif-manifest"] = manifest_url
    return Document(
        id=ident,
        title=title or ident,
        holder=holder,
        source_refs=source_refs,
        image_rights=image_rights,
        meta=meta,
    )


def dataset(directory: Path, documents: list[Document], pages: list[Page] | None = None,
            lines: list[Line] | None = None, units: list[Unit] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tables.write_table(directory / "documents.parquet", documents, Document)
    if pages:
        tables.write_table(directory / "pages.parquet", pages, Page)
    if lines:
        tables.write_table(directory / "lines.parquet", lines, Line)
    if units:
        tables.write_table(directory / "units.parquet", units, Unit)
    return directory


def seed_manifest(cache: Path, url: str, found: dict) -> Path:
    """Put a manifest in the cache of a test, so that no request is made for it."""
    path = reconcile.manifest_path(url, cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return path


def read_documents(directory: Path) -> dict[str, Document]:
    return {record.id: record for record in tables.read(directory / "documents.parquet", Document)}


def page(ident: str, document_id: str, seq: int = 0) -> Page:
    return Page(id=ident, document_id=document_id, seq=seq, image=f"https://example.invalid/{ident}", width=100, height=100)


def line(ident: str, page_id: str, seq: int = 0, text: str = "あ") -> Line:
    return Line(id=ident, page_id=page_id, seq=seq, box=Box(x=1, y=1, w=10, h=10), text_raw=text, text=text)


def test_manifest_path_is_the_sha256_of_the_url(tmp_path):
    url = "https://example.invalid/iiif/manifest.json"
    expected = tmp_path / "manifests" / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
    assert reconcile.manifest_path(url, tmp_path) == expected


def test_evidence_agrees_disagrees_and_is_missing(tmp_path, http_server):
    cached_url = "https://example.invalid/a/manifest.json"
    served_url = http_server.url("b/manifest.json")
    cache = tmp_path / "cache"
    seed_manifest(cache, cached_url, manifest(licence="https://creativecommons.org/publicdomain/mark/1.0/"))
    http_server.put(
        "b/manifest.json",
        json.dumps(manifest(licence=SA_DEED, attribution="会津若松市立会津図書館")).encode("utf-8"),
    )
    unresolved = Rights(licence=Licence.UNKNOWN, attribution="unresolved for review: no licence statement")
    directory = dataset(
        tmp_path / "honkoku-lines",
        [
            document(
                "hl:a",
                holder=NDL,
                licence="PDM-1.0",
                url=PDM_DEED,
                manifest_url=cached_url,
                image_rights=Rights(licence=Licence.PDM, attribution=NDL, evidence=PDM_DEED),
            ),
            document(
                "hl:b",
                holder="会津若松市立会津図書館",
                licence="CC-BY-4.0",
                url=BY_DEED,
                manifest_url=served_url,
            ),
            document("hl:c", holder="未登録館", image_rights=unresolved),
        ],
    )

    counts = reconcile.resolve_directory(directory, cache=cache, pause=0.0)
    documents = read_documents(directory)

    assert counts == {
        "documents": 3,
        "evidence": 5,
        "missing": 1,
        "resolved": 2,
        "changed": 1,
        "agreements": 1,
        "disagreements": 1,
        "unstated": 0,
        "holders": 3,
        "manifests_read": 1,
        "manifests_fetched": 1,
        "manifests_skipped": 0,
        "manifest_errors": 0,
        "rechecked": 0,
        "terms_changed": 0,
        "terms_failed": 0,
        "recheck_skipped": 0,
    }

    # The document every source agrees on: the manifest, the upstream field and the holder table.
    agreeing = documents["hl:a"]
    assert agreeing.image_rights.licence is Licence.PDM
    rows = agreeing.meta[reconcile.EVIDENCE]
    assert [row["source"] for row in rows] == ["manifest", "upstream", "holder"]
    assert {row["licence"] for row in rows} == {"PDM-1.0"}
    assert rows[0]["url"] == cached_url and rows[1]["url"] == PDM_DEED
    assert all(set(row) == {"source", "licence", "url", "fetched"} for row in rows)
    assert all(row["fetched"] for row in rows)

    # The document whose sources disagree: the manifest decides and every row is kept.
    disagreeing = documents["hl:b"]
    assert disagreeing.image_rights.licence is Licence.CC_BY_SA_4
    assert disagreeing.image_rights.attribution == "会津若松市立会津図書館"
    rows = disagreeing.meta[reconcile.EVIDENCE]
    assert [(row["source"], row["licence"]) for row in rows] == [
        ("manifest", "CC-BY-SA-4.0"),
        ("upstream", "CC-BY-4.0"),
    ]
    assert rows[0]["url"] == served_url and rows[1]["url"] == BY_DEED

    # The document no source states anything about keeps what its importer wrote.
    missing = documents["hl:c"]
    # An undated document is pre-modern: the unresolved statement is kept beside a PD licence.
    assert missing.image_rights == unresolved.model_copy(
        update={"licence": Licence.PUBLIC_DOMAIN, "holder_terms": Licence.UNKNOWN}
    )
    assert reconcile.EVIDENCE not in missing.meta

    # The manifest was fetched into the cache through the server, once, and nothing else was fetched.
    assert reconcile.manifest_path(served_url, cache).is_file()
    assert http_server.requests == ["GET /b/manifest.json"]
    assert sorted(path.name for path in reconcile.manifests_dir(cache).glob("*.json")) == sorted(
        [
            reconcile.manifest_path(cached_url, cache).name,
            reconcile.manifest_path(served_url, cache).name,
        ]
    )


def test_precedence_per_item_then_manifest_then_upstream_then_holder(tmp_path, monkeypatch):
    holders = tmp_path / "holders.yaml"
    holders.write_text(HOLDERS_YAML, encoding="utf-8")
    monkeypatch.setattr(rights, "HOLDERS", holders)
    cache = tmp_path / "cache"
    seed_manifest(cache, "https://example.invalid/per-item/manifest.json", manifest(licence=SA_DEED))
    seed_manifest(cache, "https://example.invalid/manifest/manifest.json", manifest(licence="CC0-1.0"))
    seed_manifest(cache, "https://example.invalid/blank/manifest.json", manifest(attribution="国文学研究資料館"))
    directory = dataset(
        tmp_path / "ds",
        [
            document(
                "hl:per-item",
                holder=NIJL,
                licence="CC-BY-NC-4.0",
                manifest_url="https://example.invalid/per-item/manifest.json",
            ),
            document(
                "hl:manifest",
                holder="会津若松市立会津図書館",
                licence="CC-BY-NC-4.0",
                manifest_url="https://example.invalid/manifest/manifest.json",
            ),
            document("hl:upstream", holder=NDL, licence="CC-BY-4.0", url=BY_DEED),
            document("hl:holder", holder=NDL),
            # A per-item holder whose item states no licence: the holder row is the only statement
            # of the page, and the upstream field stands beside it as a disagreement.
            document("hl:per-item-blank", holder=NIJL, manifest_url="https://example.invalid/blank/manifest.json"),
            document("hl:per-item-unread", holder=NIJL, licence="CC-BY-SA-4.0", url=SA_DEED),
        ],
    )

    reconcile.resolve_directory(directory, cache=cache, pause=0.0)
    documents = read_documents(directory)

    def chosen(ident: str) -> tuple[str, str]:
        found = documents[ident]
        licence = (found.image_rights.holder_terms or found.image_rights.licence).value
        row = next(row for row in found.meta[reconcile.EVIDENCE] if row["licence"] == licence)
        return row["source"], licence

    # 国文学研究資料館 states the licence of each item, so its manifest is the per-item statement,
    # and the holder row, which is the page that states the per-item licence, is not kept beside it.
    assert chosen("hl:per-item") == ("per-item", "CC-BY-SA-4.0")
    assert [row["source"] for row in documents["hl:per-item"].meta[reconcile.EVIDENCE]] == [
        "per-item",
        "upstream",
    ]
    # A manifest that states a licence wins over the upstream field.
    assert chosen("hl:manifest") == ("manifest", "CC0-1.0")
    # The upstream field wins over the holder table, which states PDM for 国立国会図書館.
    assert chosen("hl:upstream") == ("upstream", "CC-BY-4.0")
    # Without a manifest or an upstream field the holder table decides.
    assert chosen("hl:holder") == ("holder", "PDM-1.0")
    # A per-item holder whose manifest states no licence keeps the holder row, which is its terms
    # page; the upstream claim of a licence stands beside it and is reported as a disagreement.
    assert chosen("hl:per-item-blank") == ("holder", "restricted")
    assert documents["hl:per-item-blank"].image_rights.licence is Licence.PUBLIC_DOMAIN
    assert chosen("hl:per-item-unread") == ("upstream", "CC-BY-SA-4.0")
    assert [(row["source"], row["licence"]) for row in documents["hl:per-item-unread"].meta[reconcile.EVIDENCE]] == [
        ("upstream", "CC-BY-SA-4.0"),
        ("holder", "restricted"),
    ]


def test_a_statement_the_vocabulary_does_not_know_does_not_beat_a_known_one(tmp_path):
    url = "https://example.invalid/site/rules"
    cache = tmp_path / "cache"
    seed_manifest(cache, "https://example.invalid/m/manifest.json", manifest(licence=url, attribution="例機関"))
    directory = dataset(
        tmp_path / "ds",
        [
            document(
                "hl:x",
                holder="例機関",
                licence="CC-BY-4.0",
                url=BY_DEED,
                manifest_url="https://example.invalid/m/manifest.json",
            )
        ],
    )

    counts = reconcile.resolve_directory(directory, cache=cache, pause=0.0)
    found = read_documents(directory)["hl:x"]

    assert counts["disagreements"] == 1
    assert counts["unstated"] == 0
    assert found.image_rights.licence is Licence.CC_BY_4
    rows = found.meta[reconcile.EVIDENCE]
    assert [(row["source"], row["licence"]) for row in rows] == [
        ("manifest", "unknown"),
        ("upstream", "CC-BY-4.0"),
    ]
    assert rows[0]["url"] == "https://example.invalid/m/manifest.json"


def test_the_recorded_statement_is_the_upstream_field_of_last_resort(tmp_path):
    """An importer that kept no `meta` licence still contributes the statement it resolved."""
    recorded = Rights(
        licence=Licence.PDM,
        holder=NDL,
        attribution=NDL,
        evidence="https://dl.ndl.go.jp/ja/iiif_license.html",
    )
    directory = dataset(tmp_path / "ndl-minhon", [document("ndl-minhon:v1:L1", holder=NDL, image_rights=recorded)])

    counts = reconcile.resolve_directory(directory, cache=tmp_path / "cache", pause=0.0)
    found = read_documents(directory)["ndl-minhon:v1:L1"]

    assert counts["changed"] == 0 and counts["agreements"] == 1
    assert found.image_rights.licence is Licence.PDM
    assert [row["source"] for row in found.meta[reconcile.EVIDENCE]] == ["upstream", "holder"]


def test_a_cached_manifest_is_not_fetched_again(tmp_path, http_server):
    url = http_server.url("m/manifest.json")
    http_server.put("m/manifest.json", json.dumps(manifest(licence=PDM_DEED)).encode("utf-8"))
    cache = tmp_path / "cache"
    directory = dataset(tmp_path / "ds", [document("hl:x", holder="例機関", licence="CC-BY-4.0", url=BY_DEED,
                                                    manifest_url=url)])

    first = reconcile.resolve_directory(directory, cache=cache, pause=0.0)
    requests = list(http_server.requests)
    second = reconcile.resolve_directory(directory, cache=cache, pause=0.0)

    assert first["manifests_fetched"] == 1 and first["manifests_read"] == 0
    assert second["manifests_fetched"] == 0 and second["manifests_read"] == 1
    assert second["changed"] == 0
    assert http_server.requests == requests


def test_limit_bounds_the_manifests_fetched_and_the_rest_fall_back(tmp_path, http_server):
    documents = []
    for index in range(3):
        http_server.put(f"m/{index}.json", json.dumps(manifest(licence=PDM_DEED)).encode("utf-8"))
        documents.append(
            document(
                f"hl:{index}",
                holder="例機関",
                licence="CC-BY-4.0",
                url=BY_DEED,
                manifest_url=http_server.url(f"m/{index}.json"),
            )
        )
    directory = dataset(tmp_path / "ds", documents)

    counts = reconcile.resolve_directory(directory, cache=tmp_path / "cache", pause=0.0, limit=1)

    assert counts["manifests_fetched"] == 1
    assert counts["manifests_skipped"] == 2
    assert len([request for request in http_server.requests if request.startswith("GET /m/")]) == 1
    assert counts["disagreements"] == 1
    assert sorted(found.image_rights.licence.value for found in read_documents(directory).values()) == [
        "CC-BY-4.0",
        "CC-BY-4.0",
        "PDM-1.0",
    ]


def test_nothing_is_downloaded_without_a_manifest_url(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no download is expected")

    monkeypatch.setattr(reconcile.net, "download", refuse)
    cache = tmp_path / "cache"
    directory = dataset(tmp_path / "ds", [document("hl:x", holder=NDL, licence="PDM-1.0", url=PDM_DEED)])

    counts = reconcile.resolve_directory(directory, cache=cache, pause=0.0)

    assert counts["manifests_fetched"] == 0
    assert not reconcile.manifests_dir(cache).exists()


def test_resolve_rewrites_the_documents_table_and_its_manifest(tmp_path):
    directory = dataset(tmp_path / "ds", [document("hl:x", holder=NDL, licence="PDM-1.0", url=PDM_DEED)])
    (directory / "MANIFEST.json").write_text(
        json.dumps({"schema_version": 1, "tables": {"documents": 1}, "files": {"documents.parquet": "old"},
                    "writer": "glyph-atlas 0.0.1", "command": "atlas import honkoku-lines",
                    "written_at": "2026-09-11T00:00:00Z"},
                   ),
        encoding="utf-8",
    )

    reconcile.resolve_directory(directory, cache=tmp_path / "cache", pause=0.0, command="atlas rights resolve ds")

    found = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((directory / "documents.parquet").read_bytes()).hexdigest()
    assert found["files"]["documents.parquet"] == digest
    assert found["tables"]["documents"] == 1
    assert found["command"] == "atlas rights resolve ds"
    assert found["written_at"] != "2026-09-11T00:00:00Z"


def test_report_counts_documents_pages_lines_and_units_by_licence(tmp_path):
    documents = [
        document("hl:a", holder="会津若松市立会津図書館", title="A",
                 image_rights=Rights(licence=Licence.PDM, attribution="会津若松市立会津図書館")),
        document("hl:b", holder="例書館", title="B",
                 image_rights=Rights(licence=Licence.CC_BY_SA_4, attribution="例書館")),
        document("hl:c", holder="未登録館", title="C",
                 image_rights=Rights(licence=Licence.RESTRICTED, attribution="未登録館")),
    ]
    directory = dataset(
        tmp_path / "ds",
        documents,
        pages=[page("p1", "hl:a"), page("p2", "hl:b"), page("p3", "hl:c")],
        lines=[line("l1", "p1"), line("l2", "p1", seq=1), line("l3", "p2"), line("l4", "p3")],
        units=[
            Unit(id="u1", document_id="hl:a", page_id="p1"),
            Unit(id="u2", document_id="hl:b", page_id="p2"),
        ],
    )

    text = reconcile.report(directory)

    assert "`" + str(directory) + "`: 3 documents, 3 pages, 4 lines, 2 units." in text
    assert "| PDM-1.0 | 1 | 1 | 2 | 1 | yes |" in text
    assert "| CC-BY-SA-4.0 | 1 | 1 | 1 | 1 | yes |" in text
    # The restricted holder's book is pre-modern, so its images are recorded and counted as PD.
    assert "| PD | 1 | 1 | 1 | 0 | yes |" in text
    assert "| **Total** | 3 | 3 | 4 | 2 | |" in text
    assert "| yes | 3 | 3 | 3 | 4 | 2 |" in text
    assert "| no | 0 | 0 | 0 | 0 | 0 |" in text
    assert "No document carries a statement that disagrees" in text


def test_report_lists_every_disagreeing_statement(tmp_path, http_server):
    served_url = http_server.url("b/manifest.json")
    http_server.put("b/manifest.json", json.dumps(manifest(licence=SA_DEED)).encode("utf-8"))
    directory = dataset(
        tmp_path / "ds",
        [
            document("hl:a", holder=NDL, licence="PDM-1.0", url=PDM_DEED,
                     image_rights=Rights(licence=Licence.PDM, attribution=NDL)),
            document("hl:b", holder="会津若松市立会津図書館", licence="CC-BY-4.0", url=BY_DEED,
                     manifest_url=served_url,
                     image_rights=Rights(licence=Licence.CC_BY_4, attribution="会津若松市立会津図書館")),
        ],
    )
    reconcile.resolve_directory(directory, cache=tmp_path / "cache", pause=0.0)

    text = reconcile.report(directory)

    assert "1 documents carry 1 statements that disagree" in text
    assert "| hl:b | hl:b | 会津若松市立会津図書館 | upstream | CC-BY-4.0 | " + BY_DEED + " | CC-BY-SA-4.0 |" in text
    # The limit caps the listing, not the counts.
    capped = reconcile.report(directory, limit=0)
    assert "1 statements that disagree" in capped
    assert "1 further rows are not listed" in capped
    assert "| hl:a |" not in capped


def test_report_counts_a_dataset_without_pages_lines_or_units(tmp_path):
    directory = dataset(
        tmp_path / "ds",
        [
            document(
                "hl:a",
                holder=NDL,
                licence="PDM-1.0",
                url=PDM_DEED,
                image_rights=Rights(licence=Licence.PDM, attribution=NDL, evidence=PDM_DEED),
            )
        ],
    )
    text = reconcile.report(directory)
    assert "1 documents, 0 pages, 0 lines, 0 units." in text
    assert "| PDM-1.0 | 1 | 0 | 0 | 0 | yes |" in text
    assert "## Holders to follow up" not in text


def test_attribution_names_every_source_and_holder(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "honkoku-lines.yaml").write_text(
        "id: honkoku-lines\n"
        "name: Honkoku-Lines\n"
        "publisher: 橋本雄太\n"
        "kind: line-dataset\n"
        "url: https://huggingface.co/datasets/yuta1984/honkoku-lines\n"
        "licence: CC-BY-SA-4.0\n"
        "attribution: 橋本雄太, Honkoku-Lines v2.0\n",
        encoding="utf-8",
    )
    (sources / "honkoku-data.yaml").write_text(
        "id: honkoku-data\n"
        "name: みんなで翻刻データ\n"
        "publisher: 橋本雄太（みんなで翻刻）\n"
        "kind: transcription-corpus\n"
        "url: https://github.com/yuta1984/honkoku-data\n"
        "licence: CC-BY-SA-4.0\n"
        "attribution: みんなで翻刻翻刻データ, CC BY-SA 4.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reconcile, "SOURCES", sources)
    directory = dataset(
        tmp_path / "ds",
        [
            document("hl:a", holder="会津若松市立会津図書館", refs={"honkoku-data": "a"},
                     image_rights=Rights(licence=Licence.CC_BY_4, attribution="会津若松市立会津図書館",
                                         evidence=BY_DEED)),
            document("hl:b", holder=NDL, refs={"honkoku-data": "b"},
                     image_rights=Rights(licence=Licence.PDM, attribution=NDL, evidence=PDM_DEED)),
        ],
    )
    (directory / "MANIFEST.json").write_text(
        json.dumps({"command": "atlas import honkoku-lines --limit 10"}), encoding="utf-8"
    )

    text = reconcile.attribution(directory)

    # One entry per source present: the imported source and the source the records name.
    assert "### Honkoku-Lines (honkoku-lines)" in text
    assert "### みんなで翻刻データ (honkoku-data)" in text
    assert "- Credit: 橋本雄太, Honkoku-Lines v2.0" in text
    assert "- URL: https://huggingface.co/datasets/yuta1984/honkoku-lines" in text
    # The obligations come from licences.yaml.
    assert "Credit, link to the licence, state changes, and keep the share-alike terms on adaptations." in text
    assert "- Documents: 2" in text
    # One entry per holder present, with its credit line, licence, URL and obligations.
    assert "### 会津若松市立会津図書館" in text
    assert "### " + NDL in text
    assert f"- Credit: {NDL}" in text
    assert f"- Licence: PDM-1.0 — {PDM_DEED} (1 documents)" in text
    assert "No conditions. The record keeps the public-domain mark" in text
    assert text.count("Material unchanged:") == 4
    assert text.count("Material modified:") == 4


def test_attribution_marks_a_holder_with_no_statement(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "SOURCES", tmp_path / "missing")
    directory = dataset(tmp_path / "ds", [document("hl:a", holder="未登録館")])

    text = reconcile.attribution(directory)

    assert "No source of `data/sources/` is named by these records." in text
    assert "### 未登録館" in text
    assert "- Credit: 未登録館" in text
    assert "(none)" in text
    assert "No licence statement on the record; ask the holder before reuse." in text


def test_recheck_diffs_a_holder_terms_page_against_the_vocabulary(tmp_path, http_server, monkeypatch):
    terms_url = http_server.url("terms.html")
    licences = tmp_path / "licences.yaml"
    licences.write_text(
        "- match:\n"
        f"    - {terms_url}\n"
        "    - CC-BY-4.0\n"
        "  licence: CC-BY-4.0\n"
        "  label: CC BY 4.0\n"
        f"  evidence: {terms_url}\n"
        "  eligible: true\n"
        '  obligations: "Credit the holder."\n'
        "  checked: 2026-09-01\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rights, "LICENCES", licences)
    holders = tmp_path / "holders.yaml"
    holders.write_text(f"- id: example\n  ja: 例書館\n  terms: {terms_url}\n", encoding="utf-8")
    monkeypatch.setattr(rights, "HOLDERS", holders)
    http_server.put("terms.html", f"<html><body>CC-BY-4.0 <a href='{terms_url}'>{terms_url}</a></body></html>".encode())
    directory = dataset(tmp_path / "ds", [document("hl:x", holder="例書館")])
    cache = tmp_path / "cache"

    first = reconcile.resolve_directory(
        directory, cache=cache, pause=0.0, recheck=True, out=tmp_path / "first.md"
    )
    assert first["rechecked"] == 1 and first["terms_changed"] == 0 and first["terms_failed"] == 0
    assert read_documents(directory)["hl:x"].image_rights.licence is Licence.CC_BY_4
    assert "Changed: no" in (tmp_path / "first.md").read_text(encoding="utf-8")

    http_server.put("terms.html", b"<html><body>All rights reserved.</body></html>")
    second = reconcile.resolve_directory(
        directory, cache=cache, pause=0.0, recheck=True, out=tmp_path / "second.md"
    )
    marked = (tmp_path / "second.md").read_text(encoding="utf-8")

    assert second["rechecked"] == 1 and second["terms_changed"] == 1
    assert f"### {terms_url}" in marked
    assert "Changed: yes" in marked
    assert "Still printed: (nothing)" in marked
    assert f"No longer printed: {terms_url}, CC-BY-4.0" in marked


def test_recheck_can_be_bounded_and_reports_a_page_that_cannot_be_read(tmp_path, http_server, monkeypatch):
    terms_url = http_server.url("gone.html")
    holders = tmp_path / "holders.yaml"
    holders.write_text(f"- id: example\n  ja: 例書館\n  terms: {terms_url}\n", encoding="utf-8")
    monkeypatch.setattr(rights, "HOLDERS", holders)
    directory = dataset(tmp_path / "ds", [document("hl:x", holder="例書館")])

    counts = reconcile.resolve_directory(
        directory, cache=tmp_path / "cache", pause=0.0, recheck=True, out=tmp_path / "recheck.md"
    )
    marked = (tmp_path / "recheck.md").read_text(encoding="utf-8")

    assert counts["rechecked"] == 0 and counts["terms_failed"] == 1
    assert "Not read: " in marked and "HTTP 404" in marked

    skipped = reconcile.resolve_directory(
        directory, cache=tmp_path / "cache", pause=0.0, recheck=True, recheck_limit=0
    )
    assert skipped["recheck_skipped"] == 1
    assert skipped["rechecked"] == 0 and skipped["terms_failed"] == 0


def test_a_throttled_terms_page_is_asked_again(tmp_path, http_server, monkeypatch):
    monkeypatch.setattr(reconcile, "SLEEP", lambda seconds: None)
    terms_url = http_server.url("terms.html")
    http_server.put("terms.html", b"<html><body>CC-BY-4.0</body></html>")
    http_server.script["/terms.html"] = [Scripted(429, headers={"Retry-After": "0"}), Scripted(503)]

    assert "CC-BY-4.0" in reconcile.fetch_terms(terms_url, pause=0.0)
    assert http_server.requests == ["GET /terms.html"] * 3


def test_holder_terms_and_per_item_detection_read_the_holder_table(tmp_path, monkeypatch):
    holders = tmp_path / "holders.yaml"
    holders.write_text(HOLDERS_YAML, encoding="utf-8")
    monkeypatch.setattr(rights, "HOLDERS", holders)

    assert reconcile.per_item_terms(NIJL) == "https://kokusho.nijl.ac.jp/page/terms.html"
    assert reconcile.per_item_terms(NDL) is None
    assert reconcile.per_item_terms(None) is None
    assert reconcile.per_item_terms("未登録館") is None
    found = reconcile.holder_terms([document("hl:a", holder=NDL), document("hl:b", holder=NIJL)])
    assert found == {
        "https://www.ndl.go.jp/jp/use/reproduction/index.html": [NDL],
        "https://kokusho.nijl.ac.jp/page/terms.html": [NIJL],
    }


def test_terms_diff_reads_a_page():
    entry = {"match": ["https://example.invalid/x/terms.html", "CC-BY-4.0"], "licence": "CC-BY-4.0"}

    unchanged = reconcile.terms_diff("see https://example.invalid/x/terms.html", entry)
    assert unchanged["changed"] is False and unchanged["names"] is False
    assert unchanged["still"] == ["https://example.invalid/x/terms.html"]
    assert unchanged["missing"] == ["CC-BY-4.0"]

    # A relative link to the statement counts, so a page that does not repeat its own address whole
    # is not reported as changed.
    linked = reconcile.terms_diff('<a href="/x/terms.html">terms</a> CC-BY-4.0', entry, "https://example.invalid/")
    assert linked["still"] == ["https://example.invalid/x/terms.html", "CC-BY-4.0"]
    assert linked["changed"] is False and linked["names"] is True

    changed = reconcile.terms_diff("<p>All rights reserved.</p>", entry)
    assert changed["changed"] is True and changed["still"] == [] and changed["names"] is False

    # The page is still the recorded address, but it names licences and not the recorded one.
    renamed = reconcile.terms_diff('<a href="x/terms.html">terms</a> now CC0-1.0', entry, "https://example.invalid/")
    assert renamed["still"] == ["https://example.invalid/x/terms.html"]
    assert renamed["found"] == ["CC0-1.0"] and renamed["names"] is False
    assert renamed["changed"] is True

    assert reconcile.terms_diff("anything", None) == {
        "still": [],
        "missing": [],
        "found": [],
        "names": False,
        "changed": False,
    }


def test_attribution_cites_pd_and_the_holder_terms_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "SOURCES", tmp_path / "missing")
    nc_deed = "https://creativecommons.org/licenses/by-nc/4.0/"
    directory = dataset(
        tmp_path / "ds",
        [document("hl:a", holder="例書館",
                  image_rights=Rights(licence=Licence.CC_BY_NC_4, attribution="例書館", evidence=nc_deed))],
    )

    text = reconcile.attribution(directory)

    assert f"- Licence: PD — {nc_deed}" not in text
    assert "- Licence: PD — " in text
    assert f"- Holder's own terms, kept beside PD: CC-BY-NC-4.0 — {nc_deed} (1 documents)" in text


def test_a_converted_document_whose_evidence_agrees_is_no_disagreement(tmp_path):
    nc = Rights(licence=Licence.CC_BY_NC_4, attribution="例書館")
    doc = document("hl:a", holder="例書館", image_rights=nc)
    doc.meta[reconcile.EVIDENCE] = [
        {"source": "upstream", "licence": "CC-BY-NC-4.0", "url": None, "fetched": "2026-09-25"}
    ]
    directory = dataset(tmp_path / "ds", [doc], pages=[page("p1", "hl:a")])

    text = reconcile.report(directory)

    assert "No document carries a statement that disagrees" in text
