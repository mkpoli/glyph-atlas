import hashlib
import json

from glyph_atlas import date_catalogues as catalogues
from glyph_atlas import date_claims


def statements(entries, url="https://ourarchives.amane-project.jp/iiif/manifest/ina/x.json"):
    return list(date_claims.metadata_statements(entries, source="iiif-manifests", locator=url))


def test_ina_reads_written_date_not_sort_year_or_exemplar():
    found = statements([("ラベル", "岩崎家 iwasaki03 唐松苗木代金請取 元治二年三月十日 1862 状１ 原資料年代：寛保二年")])
    assert [s.text for s in found] == ["元治二年三月十日"]
    assert found[0].tier == "derived" and found[0].fixed
    assert statements([("ラベル", "内藤家 179-01 禁裏ヘ初菱喰進献ニ付 （近世）八月十八日 1867 状１")]) == []
    assert statements([("ラベル", "大正五年五月八日 1916 竪帳１")], "https://other.example/") == []


def test_kadomi_reads_explicit_fields_under_misplaced_label():
    found = statements([("manifest URI", "[年（年号）]:安政6 [年（西暦）]:1859 [月]:8 [日]: [備考]:1860年の写")])
    assert [s.text for s in found] == ["安政6"]
    assert statements([("manifest URI", "[年（年号）]:夘 [年（西暦）]: [月]:2 [日]:7")]) == []


def test_multilingual_labels_preserve_recognized_date_field():
    manifest = {"metadata": [{"label": {"ja": ["日付"], "en": ["Date"]}, "value": {"none": ["1859〜"]}}]}
    found = statements(date_claims.manifest_entries(manifest))
    assert [s.text for s in found] == ["1859〜"]


def test_expanded_labels_exclude_digitization_and_content_dates():
    found = statements([("製作年", "嘉永3年"), ("年代", "1850"), ("刊年", "明治2年"),
                        ("撮影年", "2023"), ("作成年度", "2017"), ("災害年", "1855"), ("年月日(Date)", "00000000")])
    assert [s.text for s in found] == ["嘉永3年", "1850", "明治2年"]


def test_repeated_metadata_labels_retain_distinct_dates():
    found = statements([("Date", "1850"), ("Date", "1860"), ("Date", "1850")])
    assert [s.text for s in found] == ["1850", "1860"]


def test_catalogue_html_ignores_digitization_year_and_descriptions():
    parser = catalogues.DateFields({"field--name-field-time-series"})
    parser.feed('''<div class="field--name-field-description"><div class="field--item">Originally 1600</div></div>
      <div class="field--name-field-time-series"><div class="field--label">年代<br>Date</div>
      <div class="field--items"><div class="field--item"><p>1876</p></div></div></div>
      <div class="field--name-field-created-fiscal-year"><div class="field--item">2017</div></div>''')
    assert parser.values == ["1876"]


def test_catalogue_urls_are_limited_to_known_item_routes():
    assert catalogues.catalogue_url("https://rmda.kulib.kyoto-u.ac.jp/iiif/metadata_manifest/RB00005351/manifest.json") == (
        "kyoto", "https://rmda.kulib.kyoto-u.ac.jp/item/RB00005351")
    assert catalogues.catalogue_url("https://unrelated.example/iiif/metadata_manifest/RB00005351/manifest.json") is None


def test_tokyo_linked_data_reads_date_not_digitized_date(tmp_path):
    manifest = "https://iiif.dl.itc.u-tokyo.ac.jp/repo/iiif/2570bbba-b639-4469-b1a0-dbe76da8dd8d/manifest"
    _, url = catalogues.catalogue_url(manifest)
    path = tmp_path / "date-catalogues" / (hashlib.sha256(url.encode()).hexdigest() + ".txt")
    path.parent.mkdir()
    path.write_text(json.dumps({"dcterms:date": [{"@value": "弘化4年"}], "dcterms:issued": [{"@value": "1847"}],
                                "dcterms:description": ["刊年: 弘化4年", "災害年：1855"],
                                "dcndl:dateDigitized": "2023-01-01"}))
    [(locator, entries)] = list(catalogues.catalogue_entries(manifest, tmp_path))
    found = list(date_claims.metadata_statements(entries, source="holder-catalogues", locator=locator))
    assert [s.text for s in found] == ["弘化4年", "弘化4年"]


def test_cached_copy_survives_manifest_metadata_removal(tmp_path):
    url = "https://example.org/manifest"
    path = date_claims._manifest_path(tmp_path, url)
    path.parent.mkdir()
    path.write_text('{"metadata": []}')
    row = {"id": "hk:one", "source_refs": {"iiif-manifest": url, "honkoku-entry": "https://app.honkoku.org/api/entries/one"},
           "meta": {"metadata": {"{'ja': ['年代']}": "1850"}}}
    found = date_claims.statements(row, "honkoku-collection/current", tmp_path)
    assert [s.text for s in found] == ["1850"]
    assert found[0].source == "honkoku-data"


def test_same_honkoku_entry_shares_dates_without_title_matching(tmp_path, monkeypatch):
    rows = [("honkoku-lines", {"id": "hl:ABC", "title": "X", "source_refs": {"honkoku-data": "ABC"}}),
            ("honkoku-collection/current", {"id": "hk:abc", "source_refs": {"honkoku-data": "abc"},
              "dating": [{"literal": "1850", "kind": "copying", "evidence": "https://holder.example/abc"}]}),
            ("honkoku-lines", {"id": "hl:other", "title": "X", "source_refs": {"honkoku-data": "other"}})]
    monkeypatch.setattr(date_claims, "documents", lambda *args: iter(rows))
    _, resolved, _ = date_claims.build(tmp_path, tmp_path)
    assert resolved["hl:ABC"]["witness"]["start"] == 1850
    assert "hl:other" not in resolved


def test_ndl_honkoku_training_data_uses_publication_field(tmp_path, monkeypatch):
    source = tmp_path / "honkoku-data/v1/entries.csv"
    source.parent.mkdir(parents=True)
    source.write_text("暫定ID,出版年（西暦年月日）,災害年（西暦年月日）\nL000001,1855,1703\n")
    rows = [("ndl-minhon", {"id": "ndl:one", "source_refs": {"ndl-minhon-ocr": "v1/L000001"}})]
    monkeypatch.setattr(date_claims, "documents", lambda *args: iter(rows))
    claims, resolved, _ = date_claims.build(tmp_path, tmp_path)
    assert resolved["ndl:one"]["witness"]["start"] == 1855
    assert "L000001" in claims[0].locator


def test_v1_catalogue_fetch_obeys_site_scope(tmp_path, monkeypatch):
    rows = [("ndl-minhon", {"id": "ndl:one", "source_refs": {"ndl-minhon-ocr": "v1/L000001"}})]
    monkeypatch.setattr(date_claims, "documents", lambda *args: iter(rows))
    fetched = []

    def download(url, path, **kwargs):
        fetched.append(url)
        path.parent.mkdir(parents=True)
        path.write_text("暫定ID,出版年（西暦年月日）\nL000001,1855\n")

    monkeypatch.setattr(date_claims.net, "download", download)
    date_claims.build(tmp_path, tmp_path, fetch=True, site=set())
    assert fetched == []
    _, resolved, _ = date_claims.build(tmp_path, tmp_path, fetch=True, site={"ndl:one"})
    assert fetched == ["https://raw.githubusercontent.com/yuta1984/honkoku-data/master/v1/entries.csv"]
    assert resolved["ndl:one"]["witness"]["start"] == 1855


def test_ryukyu_separates_content_and_supplied_copying_dates(tmp_path, monkeypatch):
    url = "https://shimuchi.lib.u-ryukyu.ac.jp/collection/sakamaki/hw49401"
    found = statements([("Date", "[1610-1654][1874][写]")], url)
    monkeypatch.setattr(date_claims, "documents", lambda *args: iter([("honkoku-lines", {"id": "d"})]))
    monkeypatch.setattr(date_claims, "statements", lambda *args, **kwargs: found)
    claims, resolved, _ = date_claims.build(tmp_path, tmp_path)
    assert resolved["d"]["witness"]["start"] == 1874
    assert resolved["d"]["witness"]["uncertain"]
    assert any(c.kind == "other" and c.start == 1610 for c in claims)


def test_ryukyu_printed_edition_tags():
    found = statements([("Date", "1766[版刷][写]")], "https://shimuchi.lib.u-ryukyu.ac.jp/collection/sakamaki/hw78406")
    assert found[0].kind == "printed" and found[0].fixed


def test_wikisource_index_year_is_distinct_from_commons_scan_date(tmp_path):
    row = {"id": "ws:one", "source_refs": {"wikisource-index": "https://zh.wikisource.org/wiki/Index:Book"},
           "meta": {"index": {"year": "1880"}, "commons": {"date": "2017-05-01"}}}
    found = date_claims.statements(row, "wikisource-zh-scans", tmp_path)
    assert [s.text for s in found] == ["1880"]
    assert found[0].locator.endswith("#year")
