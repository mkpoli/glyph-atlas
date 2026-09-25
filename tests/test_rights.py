"""Tests for the rights vocabulary and resolver. No test reaches the network."""

from __future__ import annotations

from datetime import date

import pytest

from glyph_atlas import rights
from glyph_atlas.schema import Licence, Rights

# Upstream spellings that must resolve to each licence value the schema knows.
LICENCE_INPUTS = [
    ("CC0-1.0", Licence.CC0),
    ("CC-BY-4.0", Licence.CC_BY_4),
    ("CC-BY-SA-3.0", Licence.CC_BY_SA_3),
    ("CC-BY-SA-4.0", Licence.CC_BY_SA_4),
    ("CC BY-SA 2.1 JP", Licence.CC_BY_SA_2_1_JP),
    ("CC-BY-NC-4.0", Licence.CC_BY_NC_4),
    ("CC-BY-ND-4.0", Licence.CC_BY_ND_4),
    ("CC-BY-NC-SA-4.0", Licence.CC_BY_NC_SA_4),
    ("CC-BY-NC-ND-4.0", Licence.CC_BY_NC_ND_4),
    ("공공누리 제1유형(출처표시)", Licence.KOGL_1),
    ("Unicode-3.0", Licence.UNICODE),
    ("Public Domain", Licence.PUBLIC_DOMAIN),
    ("PDM-1.0", Licence.PDM),
    ("RS-NOC-CR", Licence.RS_NOC_CR),
    ("bespoke-free", Licence.BESPOKE_FREE),
    ("restricted", Licence.RESTRICTED),
    ("unknown", Licence.UNKNOWN),
]

ELIGIBILITY = {
    Licence.PUBLIC_DOMAIN: True,
    Licence.PDM: True,
    Licence.CC0: True,
    Licence.CC_BY_4: True,
    Licence.CC_BY_SA_3: True,
    Licence.CC_BY_SA_4: True,
    Licence.CC_BY_SA_2_1_JP: True,
    Licence.KOGL_1: True,
    Licence.UNICODE: True,
    Licence.BESPOKE_FREE: True,
    Licence.CC_BY_NC_4: False,
    Licence.CC_BY_ND_4: False,
    Licence.CC_BY_NC_SA_4: False,
    Licence.CC_BY_NC_ND_4: False,
    Licence.RS_NOC_CR: False,
    Licence.RESTRICTED: False,
    Licence.UNKNOWN: False,
}

# The 27 distinct (image_license, image_license_url) pairs of Honkoku-Lines' items.tsv, with the
# licence each resolves to. Only the pair whose licence field holds no statement and which points at
# no page stays unknown: that is the upstream `(unspecified)`.
HONKOKU_PAIRS = [
    ("", "", Licence.UNKNOWN),
    (
        "",
        (
            '<a href="https://dcollections.lib.keio.ac.jp/ja/about" target="_blank">'
            "https://dcollections.lib.keio.ac.jp/ja/about</a>"
        ),
        Licence.RESTRICTED,
    ),
    ("", "http://kokusho.nijl.ac.jp/page/usage.html", Licence.RESTRICTED),
    ("", "https://collectie.wereldculturen.nl", Licence.RESTRICTED),
    ("", "https://da.library.ryukoku.ac.jp/support2.html", Licence.RESTRICTED),
    ("", "https://kokusho.nijl.ac.jp/page/list-tshb.html", Licence.RESTRICTED),
    ("", "https://kokusho.nijl.ac.jp/page/list-tsukuba.html", Licence.RESTRICTED),
    ("", "https://kokusho.nijl.ac.jp/page/list-ympl.html", Licence.RESTRICTED),
    ("", "https://kokusho.nijl.ac.jp/page/usage.html", Licence.RESTRICTED),
    ("", "https://library.u-gakugei.ac.jp/digitalarchive/riyotop.html", Licence.RESTRICTED),
    ("", "https://www.arc.ritsumei.ac.jp/j/database/guide.html", Licence.RESTRICTED),
    ("", "https://www.lib.u-tokyo.ac.jp/ja/library/contents/archives-top/reuse", Licence.RESTRICTED),
    ("CC-BY-4.0", "http://creativecommons.org/licenses/by/4.0/deed.ja", Licence.CC_BY_4),
    ("CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/deed.ja", Licence.CC_BY_4),
    ("CC-BY-NC-4.0", "https://creativecommons.org/licenses/by-nc/4.0/", Licence.CC_BY_NC_4),
    ("CC-BY-NC-4.0", "https://creativecommons.org/licenses/by-nc/4.0/deed.ja", Licence.CC_BY_NC_4),
    ("CC-BY-NC-ND-4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/deed.ja", Licence.CC_BY_NC_ND_4),
    ("CC-BY-NC-SA-4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/", Licence.CC_BY_NC_SA_4),
    ("CC-BY-NC-SA-4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/deed.ja", Licence.CC_BY_NC_SA_4),
    ("CC-BY-ND-4.0", "https://creativecommons.org/licenses/by-nd/4.0/deed.ja", Licence.CC_BY_ND_4),
    ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/", Licence.CC_BY_SA_4),
    ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/deed.ja", Licence.CC_BY_SA_4),
    ("FREE LICENSE with Attribution", "https://rmda.kulib.kyoto-u.ac.jp/reuse", Licence.BESPOKE_FREE),
    ("PDM-1.0", "https://creativecommons.org/publicdomain/mark/1.0/", Licence.PDM),
    ("PDM-1.0", "https://creativecommons.org/publicdomain/mark/1.0/deed.ja", Licence.PDM),
    ("RS-NOC-CR", "http://rightsstatements.org/vocab/NoC-CR/1.0/", Licence.RS_NOC_CR),
    ("RS-NOC-CR", "https://rightsstatements.org/page/NoC-CR/1.0/", Licence.RS_NOC_CR),
]


@pytest.mark.parametrize(("raw", "expected"), LICENCE_INPUTS)
def test_every_licence_value_has_an_input_that_resolves_to_it(raw, expected):
    assert rights.resolve(licence=raw).licence is expected


@pytest.mark.parametrize(("raw", "expected"), LICENCE_INPUTS)
def test_every_resolved_licence_carries_evidence_and_a_checked_date(raw, expected):
    resolved = rights.resolve(licence=raw)
    if resolved.licence is Licence.UNKNOWN:
        assert raw in resolved.attribution
    else:
        assert resolved.checked is not None and resolved.attribution


@pytest.mark.parametrize("licence", list(Licence))
def test_the_vocabulary_covers_every_licence_value(licence):
    entries = rights.vocabulary()
    assert licence.value in {entry["licence"] for entry in entries}
    entry = next(entry for entry in entries if entry["licence"] == licence.value)
    assert entry["match"] and entry["obligations"].strip() and entry["checked"] is not None


def test_the_vocabulary_has_one_entry_per_statement():
    owner: dict[str, str] = {}
    for entry in rights.vocabulary():
        for match in entry["match"]:
            folded = rights.key(match)
            assert folded
            assert owner.setdefault(folded, entry["label"]) == entry["label"]


@pytest.mark.parametrize("licence", list(Licence))
def test_eligible_for_every_licence_value(licence):
    resolved = Rights(licence=licence, attribution="test")
    assert rights.eligible(resolved) is ELIGIBILITY[licence]


def test_eligible_is_false_without_rights_or_for_a_target_that_grants_nothing():
    assert rights.eligible(None) is False
    assert rights.eligible(Rights(licence=Licence.CC_BY_4, attribution="t"), Licence.UNKNOWN) is False
    assert rights.eligible(Rights(licence=Licence.PUBLIC_DOMAIN, attribution="t"), "restricted") is False


def test_a_share_alike_source_needs_a_share_alike_target():
    share_alike = Rights(licence=Licence.CC_BY_SA_4, attribution="t")
    permissive = Rights(licence=Licence.CC_BY_4, attribution="t")
    assert rights.eligible(share_alike, Licence.CC_BY_SA_4) is True
    assert rights.eligible(share_alike, "CC-BY-4.0") is False
    assert rights.eligible(permissive, Licence.CC_BY_SA_4) is True
    assert rights.eligible(permissive, Licence.CC_BY_4) is True


@pytest.mark.parametrize(("licence", "url", "expected"), HONKOKU_PAIRS)
def test_every_honkoku_lines_pair_resolves(licence, url, expected):
    assert rights.resolve(licence=licence, url=url).licence is expected


@pytest.mark.parametrize(("licence", "url", "expected"), HONKOKU_PAIRS)
def test_only_the_unspecified_pair_stays_unknown(licence, url, expected):
    resolved = rights.resolve(licence=licence, url=url)
    if not licence and not url:
        assert resolved.licence is Licence.UNKNOWN
        assert "no licence statement" in resolved.attribution
    else:
        assert resolved.licence is not Licence.UNKNOWN


def test_two_urls_for_one_licence_share_one_entry():
    deed_ja = rights.resolve(url="http://creativecommons.org/licenses/by/4.0/deed.ja")
    deed_en = rights.resolve(url="https://creativecommons.org/licenses/by/4.0/deed.en")
    assert deed_ja.licence is Licence.CC_BY_4 and deed_en.licence is Licence.CC_BY_4
    assert deed_ja.evidence == deed_en.evidence == "https://creativecommons.org/licenses/by/4.0/"
    spellings = {rights.key("http://creativecommons.org/licenses/by/4.0/deed.ja"),
                 rights.key("https://creativecommons.org/licenses/by/4.0/deed.en")}
    owners = [
        entry["label"]
        for entry in rights.vocabulary()
        if spellings <= {rights.key(match) for match in entry["match"]}
    ]
    assert len(owners) == 1


def test_rights_statements_page_and_vocab_spellings_agree():
    label = rights.resolve(licence="No Copyright - Contractual Restrictions")
    page = rights.resolve(url="https://rightsstatements.org/page/NoC-CR/1.0/")
    vocabulary = rights.resolve(url="https://rightsstatements.org/vocab/NoC-CR/1.0/")
    assert label.licence is page.licence is vocabulary.licence is Licence.RS_NOC_CR
    assert rights.eligible(page) is False


def test_unspecified_from_honkoku_lines_stays_unknown_with_the_raw_string():
    resolved = rights.resolve(licence="(unspecified)")
    assert resolved.licence is Licence.UNKNOWN
    assert "(unspecified)" in resolved.attribution
    assert rights.eligible(resolved) is False


def test_unknown_input_keeps_its_raw_strings_and_never_raises():
    resolved = rights.resolve(licence="CC-BY-9.9", url="https://example.invalid/terms", holder="Example Library")
    assert resolved.licence is Licence.UNKNOWN
    assert "CC-BY-9.9" in resolved.attribution
    assert "example.invalid" in resolved.attribution
    assert "Example Library" in resolved.attribution
    assert resolved.holder == "Example Library"
    assert resolved.evidence == "https://example.invalid/terms"
    assert rights.resolve(licence="", url="not a url", holder=" ").licence is Licence.UNKNOWN


def test_a_known_licence_keeps_the_checked_date_of_its_entry():
    entry = next(entry for entry in rights.vocabulary() if entry["licence"] == Licence.CC_BY_4.value)
    assert rights.resolve(licence="CC-BY-4.0").checked == entry["checked"]
    assert rights.resolve(licence="CC-BY-4.0", checked=date(2001, 2, 3)).checked == date(2001, 2, 3)


def test_the_licence_field_decides_before_the_url():
    resolved = rights.resolve(licence="CC-BY-SA-4.0", url="https://creativecommons.org/licenses/by/4.0/")
    assert resolved.licence is Licence.CC_BY_SA_4
    assert resolved.evidence == "https://creativecommons.org/licenses/by-sa/4.0/"


def test_holder_entry_matches_a_name_or_a_compound_holder_string():
    ndl = rights.holder_entry("国立国会図書館")
    assert ndl is not None and ndl["id"] == "ndl"
    assert rights.holder_entry("国立国会図書館 National Diet Library, JAPAN") == ndl
    assert rights.holder_entry("東北大学附属図書館 国文学研究資料館")["id"] == "nijl"
    assert rights.holder_entry("A Library That Is Not Listed") is None
    assert rights.holder_entry(None) is None


def test_a_holder_resolves_through_its_terms_page():
    resolved = rights.resolve(holder="国立国会図書館 National Diet Library, JAPAN")
    assert resolved.licence is Licence.PDM
    assert resolved.evidence == "https://www.ndl.go.jp/jp/use/reproduction/index.html"
    assert resolved.holder == "国立国会図書館 National Diet Library, JAPAN"
    assert rights.eligible(resolved) is True


def test_a_holder_listed_as_per_item_lends_only_its_terms_page(tmp_path, monkeypatch):
    table = tmp_path / "holders.yaml"
    table.write_text(
        "- id: nijl\n"
        "  ja: 国文学研究資料館\n"
        "  licence: per-item\n"
        "  terms: https://kokusho.nijl.ac.jp/page/terms.html\n"
        "- id: mystery\n"
        "  ja: 未登録館\n"
        "  licence: per-item\n"
        "  terms: https://example.invalid/terms\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rights, "HOLDERS", table)
    row = rights.holder_entry("国文学研究資料館")
    assert row is not None and row["licence"] == "per-item"
    resolved = rights.resolve(holder="国文学研究資料館")
    assert resolved.licence is Licence.RESTRICTED
    assert resolved.evidence == "https://kokusho.nijl.ac.jp/page/terms.html"
    assert rights.eligible(resolved) is False
    unknown = rights.resolve(holder="未登録館")
    assert unknown.licence is Licence.UNKNOWN
    assert "未登録館" in unknown.attribution


def test_a_per_item_statement_is_part_of_the_vocabulary():
    entries = [entry for entry in rights.vocabulary() if entry["eligible"] == "per-item"]
    assert entries and all(entry["licence"] == Licence.RESTRICTED.value for entry in entries)
    kokusho = next(
        entry
        for entry in entries
        if any("kokusho.nijl.ac.jp/page/terms.html" in match for match in entry["match"])
    )
    assert kokusho["evidence"] == "https://kokusho.nijl.ac.jp/page/terms.html"
    assert rights.resolve(url=kokusho["evidence"]).licence is Licence.RESTRICTED


def test_manifest_rights_reads_presentation_3():
    manifest = {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": "https://example.org/iiif/manifest",
        "type": "Manifest",
        "rights": "https://creativecommons.org/licenses/by-sa/4.0/",
        "requiredStatement": {
            "label": {"en": ["Attribution"]},
            "value": {"en": ["Example Library, CC BY-SA 4.0"], "ja": ["例機関"]},
        },
    }
    resolved = rights.manifest_rights(manifest)
    assert resolved is not None
    assert resolved.licence is Licence.CC_BY_SA_4
    assert resolved.attribution == "Example Library, CC BY-SA 4.0"
    assert resolved.evidence == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert rights.eligible(resolved) is True


def test_manifest_rights_reads_a_string_required_statement_and_presentation_2():
    string_value = {
        "type": "Manifest",
        "rights": "https://creativecommons.org/publicdomain/mark/1.0/deed.ja",
        "requiredStatement": {"label": "Attribution", "value": "国立国会図書館デジタルコレクション"},
    }
    resolved = rights.manifest_rights(string_value)
    assert resolved is not None and resolved.licence is Licence.PDM
    assert resolved.attribution == "国立国会図書館デジタルコレクション"
    presentation_2 = {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "<span>会津若松市立会津図書館</span>",
    }
    older = rights.manifest_rights(presentation_2)
    assert older is not None and older.licence is Licence.CC_BY_4
    assert older.attribution == "会津若松市立会津図書館"
    assert rights.manifest_rights({}) is None
    assert rights.manifest_rights({"license": None, "attribution": None}) is None
    assert rights.manifest_rights("not a manifest") is None


def test_manifest_rights_keeps_an_unmatched_rights_uri_for_review():
    manifest = {
        "rights": "https://example.invalid/terms",
        "requiredStatement": {"value": {"ja": ["例機関"]}},
    }
    resolved = rights.manifest_rights(manifest)
    assert resolved is not None and resolved.licence is Licence.UNKNOWN
    assert resolved.attribution == "例機関"
    assert resolved.evidence == "https://example.invalid/terms"
    assert rights.eligible(resolved) is False


def test_manifest_rights_reads_a_credit_line_without_a_rights_uri():
    resolved = rights.manifest_rights({"requiredStatement": {"value": {"en": ["Credit: Example Library"]}}})
    assert resolved is not None and resolved.licence is Licence.UNKNOWN
    assert resolved.attribution == "Credit: Example Library"
    assert resolved.evidence is None


def test_markdown_table_lists_every_entry_and_match():
    table = rights.markdown_table()
    entries = rights.vocabulary()
    lines = table.splitlines()
    rows = [line for line in lines if line.startswith("| ") and not line.startswith("| ---")]
    matches = sum(len(entry["match"]) for entry in entries)
    assert len(rows) == len(entries) + matches + 2
    assert "| Statement | Licence | Eligible | Evidence | Obligations | Checked |" in lines
    assert "| Match | Licence |" in lines
    assert "CC-BY-4.0" in table and "per-item" in table


def test_gallica_terms_are_not_eligible() -> None:
    for url in (
        "https://gallica.bnf.fr/html/und/conditions-dutilisation-des-contenus-de-gallica",
        "https://gallica.bnf.fr/edit/und/conditions-dutilisation-des-contenus-de-gallica",
    ):
        resolved = rights.resolve(url=url)
        assert resolved.licence == Licence.RESTRICTED
        assert not rights.eligible(resolved)
