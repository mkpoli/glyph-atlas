import json
from datetime import date
from pathlib import Path

import pytest

from glyph_atlas import kokusho
from glyph_atlas.schema import Document, Licence, Rights

DAY = date(2026, 9, 27)


def record(bid="200004107", **fields):
    base = {
        "bid": bid, "top_shomeih": "二人比丘尼", "smeishoh": "国文学研究資料館", "cmeishoh": "一般",
        "w_seikyu": "ナ4-189", "kansha": "刊", "bpublish": ["　〈京〉堤／六左衛門"], "chuki": ["〈般〉絵入本。"],
        "work": [{"wid": "8924", "name": "二人比丘尼"}], "doi": f"https://doi.org/10.20730/{bid}",
        "licenselink": "https://creativecommons.org/publicdomain/mark/1.0/deed.ja",
        "manifest": f"https://kokusho.nijl.ac.jp/biblio/{bid}/manifest",
    }
    return {**base, **fields}


def fetcher_for(records):
    calls = []

    def fetch(url, dest, expected=None):
        calls.append(url)
        bid = url.rsplit("/", 1)[1]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(json.dumps(records[bid], ensure_ascii=False), encoding="utf-8")
        return Path(dest)

    fetch.calls = calls
    return fetch


@pytest.mark.parametrize("document,bid", [
    (Document(id="codh:1", title="t", source_refs={"nijl-bid": "100241706"}), "100241706"),
    (Document(id="hk:1", title="t", source_refs={"iiif-manifest": "https://kotenseki.nijl.ac.jp/biblio/100249502/manifest"}), "100249502"),
    (Document(id="hk:2", title="t", meta={"canvas": "https://kokusho.nijl.ac.jp/api/iiif/200004107/v4/NIIP/1"}), "200004107"),
    (Document(id="hk:3", title="t", source_refs={"iiif-manifest": "https://example.org/biblio/100249502/manifest"}), None),
])
def test_a_document_names_its_record_by_bid(document, bid):
    assert kokusho.bid_of(document) == bid


@pytest.mark.parametrize("link,licence", [
    ("https://creativecommons.org/publicdomain/mark/1.0/deed.ja", Licence.PDM),
    ("https://creativecommons.org/licenses/by-sa/4.0/deed.ja", Licence.CC_BY_SA_4),
    ("https://creativecommons.org/licenses/by/4.0/deed.ja", Licence.CC_BY_4),
    ("https://creativecommons.org/licenses/by-nc-nd/4.0/", Licence.CC_BY_NC_ND_4),
    ("https://rightsstatements.org/page/NoC-CR/1.0/?language=en", Licence.RS_NOC_CR),
    ("https://kokusho.nijl.ac.jp/page/usage.html", Licence.RESTRICTED),
    (None, Licence.RESTRICTED),
])
def test_the_licence_link_is_read_into_the_vocabulary(link, licence):
    assert kokusho.licence_of(link) == licence


def test_empty_fields_are_filled_and_the_record_is_kept_with_its_source():
    document = Document(id="codh:200004107", title="二人比丘尼", source_refs={"nijl-bid": "200004107"})
    enriched = kokusho.enrich(document, record(), DAY)
    assert (enriched.holder, enriched.shelfmark, enriched.production) == ("国文学研究資料館", "ナ4-189", "printed")
    kept = enriched.meta["kokusho"]
    assert kept["record_url"] == "https://kokusho.nijl.ac.jp/biblio/200004107"
    assert kept["doi"] == "https://doi.org/10.20730/200004107"
    assert kept["licence"] == "PDM-1.0" and kept["source"] == kokusho.SOURCE and kept["retrieved"] == "2026-09-27"
    assert kept["publication"] == ["〈京〉堤／六左衛門"]


def test_what_a_document_already_states_is_kept():
    rights = Rights(licence=Licence.CC_BY_SA_4, holder="味の素食文化セ", attribution="CODH")
    document = Document(id="codh:1", title="t", source_refs={"nijl-bid": "200004107"}, holder="味の素食文化セ",
                        shelfmark="X-1", production="handwritten", image_rights=rights)
    enriched = kokusho.enrich(document, record(kansha="刊"), DAY)
    assert (enriched.holder, enriched.shelfmark, enriched.production) == ("味の素食文化セ", "X-1", "handwritten")
    assert enriched.image_rights == document.image_rights


def test_a_manuscript_is_handwritten_and_an_unstated_making_stays_unknown():
    document = Document(id="hk:1", title="t", source_refs={"nijl-bid": "200004107"})
    assert kokusho.enrich(document, record(kansha="写"), DAY).production == "handwritten"
    assert kokusho.enrich(document, record(kansha=""), DAY).production == "unknown"


def test_enrich_all_reads_each_record_once_and_counts_what_it_filled(tmp_path):
    documents = [
        Document(id="codh:200004107", title="a", source_refs={"nijl-bid": "200004107"}),
        Document(id="hl:x", title="b", source_refs={"iiif-manifest": "https://kotenseki.nijl.ac.jp/biblio/100249502/manifest"},
                 holder="味の素食の文化センター"),
        Document(id="ndl:1", title="c"),
    ]
    fetch = fetcher_for({"200004107": record(), "100249502": record("100249502", kansha="写")})
    out, counts = kokusho.enrich_all(documents, tmp_path, DAY, fetcher=fetch)
    assert counts == {"documents": 3, "matched": 2, "failed": 0, "holder": 1, "shelfmark": 2, "production": 2, "doi": 2}
    assert out[2] == documents[2]
    assert out[1].holder == "味の素食の文化センター" and out[1].source_refs["nijl-bid"] == "100249502"
    assert len(fetch.calls) == 2


def test_a_record_for_another_bid_is_not_used(tmp_path):
    documents = [Document(id="codh:1", title="a", source_refs={"nijl-bid": "200004107"})]
    out, counts = kokusho.enrich_all(documents, tmp_path, DAY, fetcher=fetcher_for({"200004107": record("100000001")}))
    assert counts["failed"] == 1 and out == documents
