import json
from pathlib import Path

import pytest

from glyph_atlas import date_claims, dates

#: Era spans as HuTime gives them (first and last day), for the eras these tests use.
ERAS = {
    "寛政": ("1789-02-19", "1801-03-18"), "天明": ("1781-04-25", "1789-02-19"), "文化": ("1804-02-11", "1818-05-26"),
    "慶長": ("1596-12-16", "1615-09-05"), "元和": ("1615-09-05", "1624-04-17"), "嘉元": ("1303-09-16", "1307-01-18"),
    "明治": ("1868-10-23", "1912-07-30"), "享和": ("1801-03-19", "1804-03-22"), "元禄": ("1688-10-23", "1704-04-16"),
    "天平勝宝": ("0749-08-19", "0757-09-06"), "康応": ("1389-03-07", "1390-04-12"),
}
#: Era years as HuTime gives them; 寛政3年 began on 1791-02-03.
YEARS = {"寛政3年": ("1791-02-03", "1792-01-23"), "天明7年": ("1787-01-19", "1788-02-06"),
         "寛文8年": ("1668-02-13", "1669-01-31"), "嘉元1年": ("1303-02-17", "1304-02-05"),
         "享和1年": ("1801-02-13", "1802-02-02"), "享和2年": ("1802-02-03", "1803-01-22"),
         "文化1年": ("1804-01-12", "1805-01-30"), "明治17年": ("1884-01-01", "1884-12-31"),
         "元禄15年": ("1702-01-28", "1703-02-16"), "天平勝宝2年": ("0750-02-17", "0751-02-06")}
DAYS = {"元禄15年閏8月1日": "1702-09-22", "元禄15年8月1日": "1702-08-23"}
MONTHS = {"明治17年6月": ("1884-06-01", "1884-06-30"), "康応1年12月": ("1389-12-18", "1390-01-16")}


class Calendar:
    """HuTime's answers for the dates the tests ask about, through the `HuTime` class itself."""

    def __new__(cls, tmp_path: Path) -> dates.HuTime:
        answers = {f"era|{k}": list(v) for k, v in ERAS.items()} | {f"year|{k}": list(v) for k, v in YEARS.items()} \
            | {f"date|{k}": v for k, v in DAYS.items()} | {f"month|{k}": list(v) for k, v in MONTHS.items()}
        (tmp_path / "hutime").mkdir(exist_ok=True)
        (tmp_path / "hutime" / "answers.json").write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        return dates.HuTime(tmp_path / "hutime", offline=True)


@pytest.fixture
def calendar(tmp_path):
    return Calendar(tmp_path)


def reading(text, calendar):
    found = dates.interval(dates.read(text, calendar), calendar)
    return found and (found.start, found.end, found.precision, found.qualifier)


@pytest.mark.parametrize(("text", "expected"), [
    ("寛政三年", (1791, 1791, "year", None)),
    ("寛政3", (1791, 1791, "year", None)),
    ("[天明7 (1787)]", (1787, 1787, "year", None)),
    ("寛文八刊（1668）", (1668, 1668, "year", None)),
    ("慶長・元和年間", (1596, 1624, "years", None)),
    ("文化年間", (1804, 1818, "years", None)),
    ("嘉元元以後", (1303, None, "year", "after")),
    ("明17.6", (1884, 1884, "month", None)),
    ("元禄15年閏8月1日", (1702, 1702, "day", None)),
    ("天平勝宝2年", (750, 750, "year", None)),
    ("初・二編享和元、三編同二、四編文化元序", (1801, 1804, "years", None)),
])
def test_japanese_dates_convert_through_the_calendar(text, expected, calendar):
    assert reading(text, calendar) == expected


def test_a_leap_month_is_its_own_day(calendar):
    leap = dates.claim("d", "元禄15年閏8月1日", kind="copied", scope="witness", tier="attested", source="s",
                       locator="l", calendar=calendar)
    plain = dates.claim("d", "元禄15年8月1日", kind="copied", scope="witness", tier="attested", source="s",
                        locator="l", calendar=calendar)
    assert (leap.day, plain.day) == ("1702-09-22", "1702-08-23")
    assert leap.conversion["query"] == dates.hutime_url("date", "元禄15年閏8月1日")
    assert leap.calendar == "japanese"


def test_a_stated_year_stands_when_the_conversion_differs(calendar):
    found = dates.interval(dates.read("寛政三年（1790）", calendar), calendar)
    assert (found.start, found.end) == (1790, 1790)
    assert "HuTime places 寛政3年 in 1791" in found.note


@pytest.mark.parametrize(("text", "expected"), [
    ("1787", (1787, 1787, "year", None)),
    ("Ca. 1932", (1932, 1932, "year", "circa")),
    ("c. 827-835", (827, 835, "years", "circa")),
    ("1694~1762", (1694, 1762, "years", None)),
    ("[18--]", (1800, 1899, "century", None)),
    ("[191-]", (1910, 1919, "decade", None)),
    ("9-10C", (801, 1000, "century", None)),
    ("17세기~19세기", (1601, 1900, "century", None)),
    ("平安時代・12世紀 / Heian period/12th century", (1101, 1200, "century", None)),
    ("1895-04-17", (1895, 1895, "day", None)),
    ("1872-07", (1872, 1872, "month", None)),
    ("[江戸中期-末期] [写]", (None, None, "period", None)),
])
def test_gregorian_and_vague_dates(text, expected, calendar):
    assert reading(text, calendar) == expected


def test_text_with_no_date_is_no_claim(calendar):
    assert dates.claim("d", "2巻", kind="printed", scope="witness", tier="attested", source="s", locator="l",
                       calendar=calendar) is None
    assert dates.claim("d", "[刊寫地未詳] : [刊寫者未詳]", kind="printed", scope="witness", tier="attested",
                       source="s", locator="l", calendar=calendar) is None


def test_without_a_calendar_no_era_is_read():
    assert dates.interval(dates.read("寛政三年")) is None


@pytest.mark.parametrize(("start", "end", "precision", "qualifier", "uncertain", "expected"), [
    (1791, 1791, "year", None, False, "1791"),
    (1800, 1800, "year", "circa", False, "c. 1800"),
    (1789, 1801, "years", None, False, "1789–1801"),
    (1701, 1800, "century", None, False, "18th c."),
    (801, 1000, "century", None, False, "9th–10th c."),
    (1150, 1199, "century", None, False, "1150–1199"),
    (1800, 1899, "century", None, False, "1800s"),
    (1910, 1919, "decade", None, False, "1910s"),
    (1855, None, "year", "after", False, "after 1855"),
    (None, 1600, "century", "before", False, "before 1600"),
    (1792, 1792, "year", None, True, "1792?"),
    (1021, 1021, "year", None, False, "1021"),
    (1111, 1111, "year", None, False, "1111"),
])
def test_labels(start, end, precision, qualifier, uncertain, expected):
    found = dates.Resolved("witness", "copied", start, end, precision, qualifier, uncertain, "x", "single", ())
    assert dates.label(found) == expected


def test_a_period_without_years_is_labelled_as_written():
    found = dates.Resolved("witness", "produced", None, None, "period", None, False, "江戸後期", "single", ())
    assert dates.label(found) == "江戸後期"


def make(kind, start, end=None, *, tier="attested", source="kokusho", scope="witness", text=None, precision="year"):
    from glyph_atlas.schema import DateClaim

    end = start if end is None else end
    value = {"scope": scope, "text": text or str(start), "start": start, "end": end, "precision": precision, "tier": tier}
    return DateClaim(id=dates.claim_id("d", source, f"{source}#{kind}", kind, value), document="d", kind=kind,
                     source=source, locator=f"{source}#{kind}", **value)


def test_a_manuscript_is_dated_by_its_copying_and_a_printed_book_by_its_printing():
    claims = [make("copied", 1271), make("printed", 1650), make("composed", 1000, scope="work")]
    assert dates.resolve(claims, "handwritten")["witness"].start == 1271
    assert dates.resolve(claims, "printed/woodblock")["witness"].start == 1650
    assert dates.resolve(claims, "handwritten")["composed"].start == 1000


def test_a_composition_date_never_dates_the_witness():
    found = dates.resolve([make("composed", 1000, scope="work")], "handwritten")
    assert "witness" not in found
    assert found["composed"].start == 1000


def test_the_works_printing_does_not_date_this_copy():
    found = dates.resolve([make("printed", 1792, scope="work")], "printed")
    assert "witness" not in found


def test_when_sources_disagree_the_holder_wins_and_the_date_is_disputed():
    holder = make("produced", 1787, source="iiif-manifests")
    aggregator = make("produced", 1790, source="honkoku-data")
    found = dates.resolve([aggregator, holder], "unknown")["witness"]
    assert (found.start, found.status, found.source) == (1787, "disputed", "iiif-manifests")
    assert set(found.claims) == {holder.id, aggregator.id}


def test_an_attested_claim_wins_over_a_derived_one_and_overlap_is_agreement():
    attested = make("colophon", 1641, source="kokusho")
    derived = make("colophon", 1630, 1650, tier="derived", source="kokusho", text="寛永年間", precision="years")
    found = dates.resolve([derived, attested], "printed")["witness"]
    assert (found.start, found.status) == (1641, "agreed")


def test_hutime_batches_align_answers_and_unknown_dates(tmp_path):
    asked = []

    def fetcher(url, dest, **kwargs):
        asked.append(kwargs["data"])
        lines = kwargs["data"]["ival"].split("\r\n")
        answers = {"寛政": [{"text": "1789-02-19"}, {"text": "1801-03-18"}]}
        body = [item for name in lines for item in answers.get(name, [None, None])]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(body), encoding="utf-8")
        return dest

    calendar = dates.HuTime(tmp_path, fetcher=fetcher)
    assert calendar.prefetch("era", ["寛政", "山田"]) == 2
    assert calendar.era("寛政") == ("1789-02-19", "1801-03-18")
    assert calendar.era("山田") is None
    assert asked[0]["itype"] == "era" and asked[0]["ep"] == "be"
    again = dates.HuTime(tmp_path, offline=True)
    assert again.era("寛政") == ("1789-02-19", "1801-03-18")
    assert calendar.prefetch("era", ["寛政"]) == 0


def kokusho_record():
    return {
        "bid": "100038834", "kansha": "刊", "bpublish": ["万治２", "〈京〉金屋／長兵衛"],
        "chuki": ["〈奥〉（元奥書）「元禄元年／土州一宮」。", "〈序〉寛永１８［跋］。", "〈形〉外題は書き題簽"],
        "work": [{"name": "某書", "year": "嘉元元以後"}, {"name": "別書", "year": "寛政四刊（1792）"}],
    }


def test_kokusho_separates_the_copy_its_notes_and_its_works():
    found = list(date_claims.kokusho_statements(kokusho_record()))
    by_locator = {s.locator.rsplit("#", 1)[1]: s for s in found}
    assert by_locator["bpublish.0"].text == "万治２" and by_locator["bpublish.0"].kind == "printed"
    assert by_locator["bpublish.0"].scope == "witness"
    assert by_locator["chuki.0"].kind == "exemplar" and by_locator["chuki.0"].tier == "derived"
    assert by_locator["chuki.1"].kind == "colophon"
    assert by_locator["work.0.year"].scope == "work" and by_locator["work.0.year"].kind == "composed"
    assert by_locator["work.1.year"].kind == "printed" and by_locator["work.1.year"].scope == "work"
    assert "chuki.2" not in by_locator


def test_manifest_labels_prefer_the_written_date_over_its_machine_form():
    entries = [("Publication Date", "[天明7 (1787)]"), ("Publication Date (W3CDTF fortmat)", "1787"),
               ("Date", "西村 源六〈東都〉,柏原屋 清右衞門〈大坂〉"), ("年月日始", "1"), ("Title", "某")]
    found = list(date_claims.metadata_statements(entries, source="iiif-manifests", locator="https://m"))
    assert [s.text for s in found] == ["[天明7 (1787)]"]
    assert found[0].locator == "https://m#metadata=Publication Date"


def test_commons_dates_and_uncited_dates_are_left_out():
    row = {"id": "ws:1", "source_refs": "{}", "dating": [
        {"literal": "2017-05-01", "start": None, "end": None, "kind": "unknown",
         "evidence": "https://commons.wikimedia.org/wiki/File:X.djvu"},
        {"literal": "慶長・元和年間", "start": 1596, "end": 1624, "kind": "publication", "evidence": None},
        {"literal": "1871", "start": 1871, "end": 1871, "kind": "composition",
         "evidence": "https://zh.wikisource.org/wiki/X"}]}
    found = list(date_claims.importer_statements(row, "wikisource"))
    assert [(s.text, s.kind, s.scope) for s in found] == [("1871", "composed", "work")]


def test_build_reads_a_corpus_and_resolves(tmp_path, calendar):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root, cache = tmp_path / "work", tmp_path
    (root / "codh-full").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{
        "id": "codh:100038834", "origin": "japan", "production": "printed",
        "source_refs": json.dumps({"nijl-bid": "100038834"}), "meta": "{}", "dating": []}]),
        root / "codh-full" / "documents.parquet")
    (cache / "kokusho").mkdir()
    (cache / "kokusho" / "100038834.json").write_text(json.dumps(kokusho_record(), ensure_ascii=False))
    answers = json.loads((cache / "hutime" / "answers.json").read_text())
    answers |= {"era|万治": ["1658-08-21", "1661-05-23"], "year|万治2年": ["1659-01-24", "1660-02-11"],
                "era|元禄": list(ERAS["元禄"]), "year|元禄1年": ["1688-02-02", "1689-01-20"],
                "era|寛永": ["1624-04-17", "1645-01-13"], "year|寛永18年": ["1641-01-11", "1642-01-30"]}
    (cache / "hutime" / "answers.json").write_text(json.dumps(answers, ensure_ascii=False))
    claims, resolved, counts = date_claims.build(root, cache, corpora=["codh-full"])
    witness = resolved["codh:100038834"]["witness"]
    assert (witness["kind"], witness["start"], witness["label"]) == ("printed", 1659, "1659")
    assert resolved["codh:100038834"]["composed"]["label"] == "after 1303"
    assert counts["documents with a witness date"] == 1
    assert all(c.source == "kokusho" for c in claims)


def test_an_era_span_inside_a_note(calendar):
    assert reading("巻末文化中刊", calendar) == (1804, 1818, "years", None)
    assert reading("寛政年間", calendar) == (1789, 1801, "years", None)


@pytest.mark.parametrize(("text", "kind"), [
    ("享保３年刊の再版", "exemplar"), ("寛政１年後印", "edition"), ("天明５年版の補刻", "exemplar"),
    ("寛政四刊（1792）", "printed"), ("文化一四跋（1817）", "colophon"), ("近世初期写", "copied"), ("文明一七（1485）", "composed"),
])
def test_kind_from_wording(text, kind):
    assert dates.kind_of(text, "composed" if kind == "composed" else "produced") == kind


def test_a_word_ending_in_before_does_not_qualify_the_date(calendar):
    assert dates.read("明治検定以前の教科書（明治6年-13年）").qualifier is None
    assert dates.read("1855年(安政二年)以後").qualifier == "after"


def test_an_era_year_range(calendar):
    calendar.answers |= {"year|明治6年": ["1873-01-01", "1873-12-31"], "year|明治13年": ["1880-01-01", "1880-12-31"]}
    assert reading("（明治6年-13年）", calendar) == (1873, 1880, "years", None)


def test_an_era_after_other_words_or_starting_with_its_own_year_word(calendar):
    calendar.answers |= {"year|元禄3年": ["1690-02-08", "1691-01-28"]}
    assert reading("書写元禄三年", calendar) == (1690, 1690, "year", None)
    assert reading("文化三巻", calendar) is None


def test_a_month_is_placed_by_its_own_first_day(calendar):
    assert reading("康応元年12月", calendar) == (1389, 1389, "month", None)


@pytest.mark.parametrize(("text", "expected"), [
    ("1777-01-01", (1777, 1777, "year", None)),
    ("18th c.", (1701, 1800, "century", None)),
    ("vol. 3 c. 1800", (1800, 1800, "year", "circa")),
    ("827- 835", (827, 835, "years", None)),
    ("200丁", None),
])
def test_counts_and_padding_are_not_dates(text, expected, calendar):
    assert reading(text, calendar) == expected


def test_a_bracketed_number_that_is_not_the_eras_year_does_not_replace_it(calendar):
    found = dates.interval(dates.read("寛政三年 [123]", calendar), calendar)
    assert found.start == 1791 and "is not 寛政3年" in found.note


def test_a_copy_of_a_printing_is_dated_by_its_exemplar():
    assert dates.kind_of("寛文７年板写", "copied") == "exemplar"


def test_a_work_date_of_several_events_is_split():
    assert date_claims.events("寛政四成、同五序、同八刊") == ["寛政四成", "寛政五序", "寛政八刊"]
    assert [dates.kind_of(e, "composed") for e in date_claims.events("寛政四成、同五序、同八刊")] == ["composed", "colophon", "printed"]
    assert date_claims.events("寛政四刊（1792）") == ["寛政四刊（1792）"]


def test_a_later_impression_dates_a_printed_copy_before_its_first_printing():
    assert dates.resolve([make("printed", 1755), make("edition", 1764)], "printed")["witness"].start == 1764


def test_the_holders_own_date_disputes_another_kind():
    found = dates.resolve([make("printed", 1834), make("produced", 1900, source="iiif-manifests")], "printed")["witness"]
    assert (found.start, found.status) == (1834, "disputed")


def test_importer_years_carry_no_conversion_of_ours(calendar):
    found = dates.claim("d", "寛政三年以後", kind="printed", scope="witness", tier="attested", source="s", locator="l",
                        calendar=calendar, years=(1791, 1795))
    assert (found.start, found.end, found.qualifier, found.conversion) == (1791, 1795, None, None)


def test_a_copy_dated_only_by_a_named_period_shows_it_without_years():
    from glyph_atlas.schema import DateClaim

    value = {"scope": "witness", "text": "[江戸後期]", "start": None, "end": None, "precision": "period", "tier": "attested"}
    period = DateClaim(id=dates.claim_id("d", "iiif-manifests", "m", "produced", value), document="d", kind="produced",
                       source="iiif-manifests", locator="m", **value)
    found = dates.resolve([period], "unknown")["witness"]
    assert (found.start, found.end, found.text, dates.label(found)) == (None, None, "[江戸後期]", "[江戸後期]")
    assert dates.resolve([period, make("produced", 1820, source="iiif-manifests")], "unknown")["witness"].start == 1820
