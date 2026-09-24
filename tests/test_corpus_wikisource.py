"""Wikisource discovery and import.

Behaviour is tested against a fake client and tiny fixtures, so the suite never
depends on the network and never asserts on an implementation file's text.
"""

from __future__ import annotations

import pytest

from glyph_atlas.corpus import wikisource as ws
from glyph_atlas.corpus.occurrence import TOMO
from glyph_atlas.corpus.wikisource import (
    EXCLUDED_NAMESPACES,
    TEXT_NAMESPACES,
    Wikisource,
    WikisourcePage,
    _first_image,
    _index_of,
    import_corpus,
    is_text_namespace,
    namespace_of,
    occurrences_in_pages,
    select_text_pages,
)


def make_page(title, *, ns, wikitext="", revision=1, pageid=1):
    return WikisourcePage(
        title=title,
        pageid=pageid,
        revision=revision,
        timestamp="2026-01-01T00:00:00Z",
        url=f"https://ja.wikisource.org/wiki/{title}",
        namespace=ns,
        wikitext=wikitext,
        index_title=_index_of(title),
        file_page=_first_image(wikitext),
    )


class FakeClient:
    """Stands in for the live API: returns canned pages, no network."""

    def __init__(self, pages):
        self._pages = pages
        self.searched: list[str] = []

    def search(self, char, limit=20):
        self.searched.append(char)
        return [p.title for p in self._pages[:limit]]

    def pages(self, titles):
        wanted = set(titles)
        return [p for p in self._pages if p.title in wanted]


class TestNamespacePolicy:
    def test_only_transcribed_namespaces_count(self):
        assert is_text_namespace("Page:Futsu Ezogo Shokei.pdf/10") is True
        assert is_text_namespace("蕉窓雑話") is True
        assert is_text_namespace("Index:Futsu Ezogo Shokei.pdf") is False

    @pytest.mark.parametrize(
        "title",
        [
            "Index:NAJDA-195-0324 蕉窓雑話1.pdf",  # transcription conventions
            "Template:Header",
            "Category:Texts",
            "Help:Contents",
            "Wikisource:About",
            "Portal:Japan",
            "MediaWiki:Common.js",
        ],
    )
    def test_conventions_and_navigation_are_excluded(self, title):
        assert is_text_namespace(title) is False

    def test_the_index_namespace_is_named_explicitly(self):
        assert 252 in EXCLUDED_NAMESPACES
        assert 0 in TEXT_NAMESPACES and 250 in TEXT_NAMESPACES

    def test_namespace_numbers_are_read_from_prefixes(self):
        assert namespace_of("Page:X/1") == 250
        assert namespace_of("Index:X") == 252
        assert namespace_of("蕉窓雑話") == 0

    def test_select_splits_text_from_convention(self):
        pages = [
            make_page("Page:Scan.pdf/1", ns=250, wikitext="山" + TOMO + "云"),
            make_page("Index:Scan.pdf", ns=252, wikitext="「" + TOMO + "」は「トモ」"),
            make_page("蕉窓雑話", ns=0, wikitext="然" + TOMO),
        ]
        keep, drop = select_text_pages(pages)
        assert [p.title for p in keep] == ["Page:Scan.pdf/1", "蕉窓雑話"]
        assert [p.title for p in drop] == ["Index:Scan.pdf"]


class TestTagReading:
    def test_a_page_belongs_to_its_index(self):
        assert _index_of("Page:Futsu Ezogo Shokei.pdf/10") == "Index:Futsu Ezogo Shokei.pdf"

    def test_a_main_namespace_title_has_no_index(self):
        assert _index_of("蕉窓雑話") is None

    @pytest.mark.parametrize(
        "wikitext,expected",
        [
            ("[[File:Scan.pdf|page=3]]", "Scan.pdf"),
            ("[[ファイル:走査.pdf|300px]]", "走査.pdf"),
            ("no image here", None),
        ],
    )
    def test_the_scan_behind_a_page_is_found(self, wikitext, expected):
        assert _first_image(wikitext) == expected


class TestOccurrences:
    def test_only_transcribed_text_yields_occurrences(self):
        found = occurrences_in_pages(
            [
                make_page("Page:Scan.pdf/1", ns=250, wikitext="山" + TOMO + "云"),
                make_page("Index:Scan.pdf", ns=252, wikitext="「" + TOMO + "」は「トモ」"),
            ],
            TOMO,
        )
        assert {r["title"] for r in found} == {"Page:Scan.pdf/1"}
        record = found[0]
        assert record["tier"] == "page_text"
        assert record["geometry"] == "none"
        assert record["revision"]
        assert record["url"].startswith("https://ja.wikisource.org/")
        assert record["licence"] == ws.SITE_LICENCE
        assert record["note"].startswith("Wikisource transcription occurrence")

    def test_a_convention_note_is_not_an_occurrence(self):
        assert (
            occurrences_in_pages(
                [
                    make_page("Index:Scan.pdf", ns=252, wikitext="「" + TOMO + "」は「トモ」"),
                ],
                TOMO,
            )
            == []
        )


class TestRights:
    def test_the_transcription_is_cc_by_sa(self):
        assert ws.document_of().text_rights.licence.value == "CC-BY-SA-4.0"

    def test_the_scan_is_not_assumed_public_domain(self):
        rights = ws.document_of().image_rights
        assert rights.licence.value == "unknown"
        assert "does not imply PD" in (rights.evidence or "")

    def test_an_unknown_scan_licence_is_not_proxyable(self):
        from glyph_atlas.corpus.api import PROXYABLE

        assert "unknown" not in PROXYABLE


class TestImport:
    def test_import_writes_a_corpus_and_drops_conventions(self, tmp_path):
        client = FakeClient(
            [
                make_page("Page:Scan.pdf/1", ns=250, pageid=11, wikitext="山" + TOMO + "云", revision=42),
                make_page(
                    "Index:Scan.pdf", ns=252, pageid=12, wikitext="「" + TOMO + "」は「トモ」", revision=43
                ),
            ]
        )
        out = tmp_path / "wikisource"
        counts = import_corpus(out, char=TOMO, client=client, cache=tmp_path / "cache")
        assert counts["pages"] == 1
        assert counts["excluded_pages"] == 1
        assert counts["documents"] == 1
        for name in ("documents.parquet", "pages.parquet", "page_texts.parquet", "MANIFEST.json"):
            assert (out / name).exists()

    def test_the_written_corpus_is_discoverable_and_indexable(self, tmp_path):
        from glyph_atlas.corpus import CorpusIndex, build_chars, discover

        client = FakeClient(
            [
                make_page("Page:Scan.pdf/1", ns=250, pageid=11, wikitext="山" + TOMO + "雲", revision=42),
            ]
        )
        root = tmp_path / "primary-work"
        root.mkdir()
        import_corpus(root / "wikisource", char=TOMO, client=client, cache=tmp_path / "cache")

        found = discover(root)
        assert [c.name for c in found] == ["wikisource"]
        assert found[0].has_page_texts and found[0].searchable

        directory = tmp_path / "index"
        build_chars(root, directory)
        index = CorpusIndex(directory, root)
        assert index.build_occurrences(TOMO)["records"] == 1
        occurrence = index.occurrences(TOMO)
        assert len(occurrence) == 1
        assert occurrence[0].source.corpus == "wikisource"
        assert occurrence[0].source.revision == "42"
        assert occurrence[0].has_crop is False
        assert index.summary(TOMO)["n_page_hits"] == 1


class TestLiveDiscovery:
    """One live call, skipped when the network is unavailable."""

    def test_insource_search_returns_pages_with_provenance(self, tmp_path):
        client = Wikisource(cache=tmp_path / "ws")
        try:
            titles = client.search(TOMO, limit=5)
        except Exception as exc:  # noqa: BLE001 - any transport failure means skip
            pytest.skip(f"wikisource unreachable: {exc}")
        if not titles:
            pytest.skip("no wikisource hits returned")
        for record in client.occurrences(titles, TOMO):
            assert record["codepoint"] == "U+2A708"
            assert record["revision"]
            assert record["licence"] == ws.SITE_LICENCE
