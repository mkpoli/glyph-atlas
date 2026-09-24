"""The bounded index over synthetic corpora.

These tests build miniature corpora in ``tmp_path`` rather than reading the shared
ones, so they assert the *behaviour* (counts, tiers, build-on-demand, no full scan per
query) and stay fast.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from glyph_atlas import tables
from glyph_atlas.corpus import CorpusIndex, build_chars, discover
from glyph_atlas.corpus.index import (
    PAGE_SKETCH_REGISTERS,
    _sketch_add,
    _sketch_count,
    _split_literal_annotated,
    located_units,
)
from glyph_atlas.corpus.occurrence import TOMO
from glyph_atlas.schema import (
    Box,
    Document,
    Line,
    Page,
    PageText,
    Unit,
)

PAGE_W, PAGE_H = 1000, 1000


def build_corpus(root: Path, name: str = "honkoku-lines") -> Path:
    """A three-line corpus: one literal ligature, one annotated, one plain text."""
    out = root / name
    out.mkdir(parents=True, exist_ok=True)
    doc = Document(
        id="hl:DOC",
        title="Test book",
        holder="Test holder",
        shelfmark="S-1",
        image_rights={"licence": "PDM-1.0", "holder": "Test holder", "attribution": "Test holder"},
        text_rights={"licence": "CC-BY-SA-4.0", "holder": "Test holder", "attribution": "Test holder"},
    )
    page = Page(
        id="hl:DOC:1",
        document_id="hl:DOC",
        seq=1,
        image="https://example.org/iiif/S-1/canvas1",
        width=PAGE_W,
        height=PAGE_H,
    )
    lines = [
        # 0: literal ligature, with a real rectangle
        Line(
            id="hl:DOC_1_000",
            page_id="hl:DOC:1",
            seq=0,
            box=Box(x=10, y=20, w=40, h=900),
            text_raw="\u3000\u5c71" + TOMO + "\u4e91",
            text="\u5c71" + TOMO + "\u4e91",
            meta={"iiif_region_url": "https://example.org/iiif/S-1/canvas1/10,20,40,900/full/0/default.jpg"},
        ),
        # 1: the transcription writes the expansion and annotates the glyph
        Line(
            id="hl:DOC_1_001",
            page_id="hl:DOC:1",
            seq=1,
            box=Box(x=60, y=20, w=40, h=900),
            text_raw="\u5175\u30c8\u30e2\u3016" + TOMO + "\uff1a\u5408\u5b57\u3017\u601d",
            text="\u5175\u601d",
            meta={"iiif_region_url": "https://example.org/iiif/S-1/canvas1/60,20,40,900/full/0/default.jpg"},
        ),
        # 2: no ligature, and a ト immediately followed by a モ that is NOT one
        Line(
            id="hl:DOC_1_002",
            page_id="hl:DOC:1",
            seq=2,
            box=Box(x=110, y=20, w=40, h=900),
            text_raw="\u30c8\u30e2\u30ab\u30ca",
            text="\u30c8\u30e2\u30ab\u30ca",
            meta={},
        ),
        # 3: no box at all
        Line(
            id="hl:DOC_1_003",
            page_id="hl:DOC:1",
            seq=3,
            box=None,
            text_raw="\u3088\u308a" + TOMO,
            text="\u3088\u308a" + TOMO,
            meta={},
        ),
    ]
    texts = [
        PageText(
            page_id="hl:DOC:1",
            source="honkoku-data",
            revision="abc",
            text_raw="page level " + TOMO + " and \u30c8\u30e2 apart",
        )
    ]
    tables.write(out / "documents.parquet", [doc], Document, command="test")
    tables.write(out / "pages.parquet", [page], Page)
    (out / "lines").mkdir(exist_ok=True)
    tables.write(out / "lines" / "00.parquet", lines, Line)
    tables.write(out / "page_texts.parquet", texts, PageText)
    (out / "MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": tables.SCHEMA_VERSION,
                "tables": {"documents": 1, "pages": 1, "lines": len(lines), "page_texts": 1},
                "files": {},
                "writer": "test",
                "command": "test",
            }
        ),
        encoding="utf-8",
    )
    return out


def built(root, directory, char=TOMO):
    """Build one character, then read it back the way the API does."""
    CorpusIndex(directory, root).build_occurrences(char)
    return CorpusIndex(directory, root).occurrences(char)


@pytest.fixture()
def indexed(tmp_path: Path):
    root = tmp_path / "shared-work"
    root.mkdir()
    build_corpus(root)
    directory = tmp_path / "index"
    stats = build_chars(root, directory)
    return root, directory, stats


class TestSketch:
    @pytest.mark.parametrize("target", [50, 500, 5000, 50_000])
    def test_distinct_count_is_close(self, target):
        registers = __import__("numpy").zeros(PAGE_SKETCH_REGISTERS, dtype="uint8")
        for value in range(target):
            _sketch_add(registers, value * 7 + 3)
        estimate = _sketch_count(registers)
        assert abs(estimate - target) / target < 0.15

    def test_empty_is_zero(self):
        import numpy as np

        assert _sketch_count(np.zeros(PAGE_SKETCH_REGISTERS, dtype="uint8")) == 0


class TestSplitLiteralAnnotated:
    def test_a_literal_glyph_and_its_annotation_are_counted_apart(self):
        assert _split_literal_annotated(
            "\u7136" + TOMO + "\u3010" + TOMO + "\uff1a\u5408\u5b57\u3011", TOMO
        ) == (1, 1)

    def test_an_expansion_with_an_annotation_is_only_annotated(self):
        assert _split_literal_annotated(
            "\u5175\u30c8\u30e2\u3016" + TOMO + "\uff1a\u5408\u5b57\u3017", TOMO
        ) == (0, 1)

    def test_a_plain_glyph_is_literal(self):
        assert _split_literal_annotated("\u5c71" + TOMO + "\u4e91", TOMO) == (1, 0)

    def test_a_separate_to_and_mo_is_never_counted(self):
        assert _split_literal_annotated("\u30c8\u30e2", TOMO) == (0, 0)


class TestCharsSummary:
    def test_the_index_is_small_and_bounded(self, indexed):
        _, _, stats = indexed
        assert stats.chars > 0
        # The whole point: a summary, not the corpus.
        assert stats.bytes_on_disk < 2_000_000

    def test_counts_separate_literal_from_annotated(self, indexed):
        """Fixture: line 0 literal, line 1 トモ〖𪜈：合字〗, line 3 literal, plus a page text."""
        _, directory, _ = indexed
        summary = CorpusIndex(directory).summary(TOMO)
        assert summary["n_literal"] == 3  # lines 0 and 3, and the page text
        assert summary["n_annotated"] == 1  # line 1's marker only
        assert summary["n_located"] == 2  # lines 0 and 1 have box + region
        assert summary["n_line_hits"] == 3  # lines 0, 1, 3
        assert summary["n_page_hits"] == 1

    def test_a_separate_to_and_mo_does_not_inflate_the_ligature_count(self, indexed):
        """The requirement: counts must not be ordinary separate ト+モ sequences."""
        _, directory, _ = indexed
        idx = CorpusIndex(directory)
        # Line 2 is トモカナ: two kana, no ligature. They are counted as themselves.
        assert idx.summary("\u30c8")["n_literal"] == 3  # lines 1, 2, page text
        assert idx.summary("\u30e2")["n_literal"] == 3
        # ...and the ligature count is independent of them.
        assert idx.summary(TOMO)["n_literal"] == 3
        assert idx.summary("\u30c8")["n_annotated"] == 0
        assert idx.summary("\u30e2")["n_annotated"] == 0

    def test_page_counts_are_flagged_as_estimates(self, indexed):
        _, directory, _ = indexed
        row = CorpusIndex(directory).summary(TOMO)
        assert "n_pages_approx" in row and "n_pages" not in row

    def test_lookup_accepts_a_character_or_a_code_point(self, indexed):
        root, directory, _ = indexed
        idx = CorpusIndex(directory, root)
        assert idx.summary_by_codepoint("U+2A708")["char"] == TOMO
        assert idx.summary_by_codepoint(TOMO)["char"] == TOMO

    def test_by_reading_finds_the_ligature_for_its_kana(self, indexed):
        root, directory, _ = indexed
        idx = CorpusIndex(directory, root)
        assert [r["char"] for r in idx.by_reading("\u30c8\u30e2")] == [TOMO]

    def test_an_index_built_for_one_root_does_not_scan_another(self, indexed, tmp_path):
        """The root is a property of the index, not a guess made at query time."""
        _, directory, _ = indexed
        idx = CorpusIndex(directory, tmp_path / "somewhere-else")
        assert idx.build_occurrences(TOMO)["records"] == 0


class TestOccurrences:
    def test_a_line_hit_with_a_region_is_located(self, indexed):
        """Both line 0 (literal) and line 1 (annotated) carry a real line rectangle."""
        root, directory, _ = indexed
        occ = built(root, directory)
        located = [o for o in occ if o.has_crop]
        assert len(located) == 2
        assert all(o.tier == "line_rect" for o in located)
        assert all(o.rects[0].basis == "upstream_bbox" for o in located)
        assert all(o.rects[0].confidence is None for o in located)
        # a line rectangle is never a glyph rectangle
        assert all(o.has_glyph_rect is False for o in located)

    def test_two_lines_of_one_page_are_not_merged(self, indexed):
        """Page + offset is not unique: line 0 and line 3 both have the char at 2."""
        root, directory, _ = indexed
        occ = built(root, directory)
        by_line = {o.source.line_id for o in occ}
        assert {"hl:DOC_1_000", "hl:DOC_1_003"} <= by_line
        keys = [o.identity_key for o in occ]
        assert len(keys) == len(set(keys))

    def test_a_line_without_a_box_is_text_only(self, indexed):
        root, directory, _ = indexed
        occ = built(root, directory)
        plain = [o for o in occ if o.source.line_id == "hl:DOC_1_003"]
        assert plain and plain[0].tier == "line_text"
        assert plain[0].has_crop is False

    def test_a_page_text_hit_has_no_geometry(self, indexed):
        root, directory, _ = indexed
        occ = built(root, directory)
        page = [o for o in occ if o.tier == "page_text"]
        assert page and all(o.rects == [] for o in page)

    def test_the_annotation_marker_is_classified_as_such(self, indexed):
        root, directory, _ = indexed
        occ = built(root, directory)
        classes = {o.source.line_id: o.char_class for o in occ if o.source.line_id}
        assert classes["hl:DOC_1_001"] == "annotated_ligature"
        assert classes["hl:DOC_1_000"] == "literal_text"


class TestOnDemandBuild:
    def test_a_cold_character_is_built_rather_than_reported_empty(self, indexed):
        root, directory, _ = indexed
        # The root must be passed explicitly: an index that guesses the default root
        # would scan the wrong corpora.
        idx = CorpusIndex(directory, root)
        assert idx.has_occurrences(TOMO) is False
        status = idx.build_occurrences(TOMO)
        assert status["records"] > 0 and status["truncated"] is False
        assert idx.has_occurrences(TOMO) is True
        assert idx.occurrence(idx.occurrences(TOMO)[0].occurrence_id) is not None

    def test_the_occurrence_cache_is_bounded(self, indexed):
        root, directory, _ = indexed
        idx = CorpusIndex(directory, root, cache_size=1)
        idx.build_occurrences(TOMO)
        idx.occurrences(TOMO)
        idx.occurrences("\u30c8")
        assert len(idx._occ_cache) == 1


class TestLocatedUnits:
    def test_corpora_without_units_return_nothing(self, indexed):
        root, _, _ = indexed
        rows, total = located_units(TOMO, root)
        assert rows == [] and total == 0


class TestDiscovery:
    def test_a_corpus_reports_its_shape_without_local_paths_in_the_api(self, indexed):
        from glyph_atlas.corpus import discover

        root, _, _ = indexed
        found = discover(root)
        assert len(found) == 1
        assert found[0].has_lines and found[0].has_page_texts
        assert found[0].searchable


class TestLocatedUnitsScan:
    """The scan that feeds the glyph grid, and the paging it must support.

    These cover a real regression: the per-row block lost its loop nesting, so only
    one row per corpus survived and an empty filtered batch raised UnboundLocalError.
    """

    @staticmethod
    def corpus(root: Path, name: str, units: list[Unit]) -> None:
        out = root / name
        out.mkdir(parents=True, exist_ok=True)
        doc = Document(
            id=f"d:{name}",
            title=f"{name} source",
            holder="H",
            image_rights={"licence": "CC-BY-4.0", "holder": "H", "attribution": "H"},
        )
        page = Page(
            id=f"d:{name}:p1",
            document_id=f"d:{name}",
            seq=1,
            image="https://example.org/iiif/x",
            width=1000,
            height=1000,
        )
        tables.write(out / "documents.parquet", [doc], Document)
        tables.write(out / "pages.parquet", [page], Page)
        tables.write(out / "units.parquet", units, Unit)

    @staticmethod
    def unit(name: str, n: int, *, box=True, active=True) -> Unit:
        return Unit(
            id=f"{name}:u{n}",
            document_id=f"d:{name}",
            page_id=f"d:{name}:p1",
            seq=n,
            box=Box(x=n, y=n, w=10, h=10) if box else None,
            text_source="一",
            unicode="U+4E00",
            kind="char",
            method="import",
            active=active,
        )

    @pytest.fixture()
    def two_corpora(self, tmp_path):
        root = tmp_path / "shared-work"
        root.mkdir()
        self.corpus(root, "codh-full", [self.unit("codh-full", n) for n in range(1, 6)])
        self.corpus(root, "kokatsuji", [self.unit("kokatsuji", n) for n in range(1, 6)])
        directory = tmp_path / "index"
        build_chars(root, directory)
        return root, directory

    def test_many_matches_in_one_batch_all_survive(self, two_corpora):
        """The regression: a batch of ten returned one row, not ten."""
        root, _ = two_corpora
        rows, total = located_units("一", root)
        assert total == 10
        assert len(rows) == 10
        assert {r["unit_id"] for r in rows} == {f"codh-full:u{n}" for n in range(1, 6)} | {
            f"kokatsuji:u{n}" for n in range(1, 6)
        }

    def test_a_batch_with_no_matches_is_not_an_error(self, two_corpora):
        """An empty filtered batch must not raise."""
        root, _ = two_corpora
        rows, total = located_units("漢", root)
        assert rows == [] and total == 0

    def test_an_empty_corpus_yields_nothing(self, tmp_path):
        root = tmp_path / "shared-work"
        root.mkdir()
        self.corpus(root, "codh-full", [])
        rows, total = located_units("一", root)
        assert rows == [] and total == 0

    def test_an_inactive_or_unboxed_row_is_not_counted(self, tmp_path):
        """The total must not promise rows the grid cannot show."""
        root = tmp_path / "shared-work"
        root.mkdir()
        self.corpus(
            root,
            "codh-full",
            [
                self.unit("codh-full", 1),  # good
                self.unit("codh-full", 2, active=False),  # inactive
                self.unit("codh-full", 3, box=False),  # no rectangle
            ],
        )
        rows, total = located_units("一", root)
        assert total == 1
        assert [r["unit_id"] for r in rows] == ["codh-full:u1"]

    def test_paging_is_global_across_corpora(self, two_corpora):
        """Page two must continue, not restart each corpus."""
        root, _ = two_corpora
        first, total = located_units("一", root, offset=0, limit=4)
        second, _ = located_units("一", root, offset=4, limit=4)
        third, _ = located_units("一", root, offset=8, limit=4)
        seen = [r["unit_id"] for r in first + second + third]
        assert total == 10
        assert len(seen) == 10  # every row reached exactly once
        assert len(set(seen)) == 10  # and none twice

    def test_every_page_agrees_on_the_total(self, two_corpora):
        root, _ = two_corpora
        totals = {located_units("一", root, offset=o, limit=3)[1] for o in (0, 3, 6, 9)}
        assert totals == {10}

    def test_a_page_past_the_end_is_empty_but_still_reports_the_total(self, two_corpora):
        root, _ = two_corpora
        rows, total = located_units("一", root, offset=99, limit=5)
        assert rows == [] and total == 10

    def test_scan_order_is_stable_between_calls(self, two_corpora):
        root, _ = two_corpora
        a = [r["unit_id"] for r in located_units("一", root, offset=0, limit=10)[0]]
        b = [r["unit_id"] for r in located_units("一", root, offset=0, limit=10)[0]]
        assert a == b


class TestDerivedCorporaAreNotSources:
    """A derived gallery must not be counted as another copy of its sources.

    ``work/ainu-gallery`` holds crops built from the Ainu record imports. Treating it
    as a corpus would list the same manuscript glyphs twice and inflate every count.
    """

    def test_a_named_derived_directory_is_skipped(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_corpus(root, "honkoku-lines")
        _derived = build_corpus(root, "ainu-gallery")
        names = [c.name for c in discover(root)]
        assert names == ["honkoku-lines"]

    def test_a_directory_that_declares_itself_derived_is_skipped(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_corpus(root, "honkoku-lines")
        gallery = build_corpus(root, "some-future-gallery")
        manifest = json.loads((gallery / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest["derived_from"] = ["honkoku-lines"]
        (gallery / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert [c.name for c in discover(root)] == ["honkoku-lines"]

    def test_a_plain_corpus_is_still_discovered(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_corpus(root, "honkoku-lines")
        build_corpus(root, "wikisource")
        assert sorted(c.name for c in discover(root)) == ["honkoku-lines", "wikisource"]

    def test_the_exclusion_keeps_the_index_from_double_counting(self, tmp_path):
        """The counts must not change when a derived gallery sits beside its source."""
        root = tmp_path / "work"
        root.mkdir()
        build_corpus(root, "honkoku-lines")
        without = tmp_path / "index-a"
        build_chars(root, without)

        build_corpus(root, "ainu-gallery")
        with_gallery = tmp_path / "index-b"
        build_chars(root, with_gallery)

        a = {r["char"]: r["n_occurrences"] for r in CorpusIndex(without, root).characters()}
        b = {r["char"]: r["n_occurrences"] for r in CorpusIndex(with_gallery, root).characters()}
        assert a == b
        assert a[TOMO] > 0


def test_character_gallery_excludes_blocks_but_keeps_encoded_ligatures(tmp_path):
    from glyph_atlas.corpus.index import _renderable_unit_counts, sample_units
    from glyph_atlas.corpus.sources import discover
    from glyph_atlas.schema import UnitKind

    root, directory = tmp_path / "corpora", tmp_path / "index"
    units = [TestLocatedUnitsScan.unit("kokatsuji", i) for i in range(1, 5)]
    units[1] = units[1].model_copy(update={"text_source": "とりい", "reading": "とりい",
        "unicode": "U+3068 U+308A U+3044", "kind": UnitKind.LIGATURE, "granularity": "block"})
    units[2] = units[2].model_copy(update={"text_source": "トモ", "reading": "トモ",
        "unicode": "U+2A708", "kind": UnitKind.LIGATURE})
    units[3] = units[3].model_copy(update={"granularity": "block"})
    TestLocatedUnitsScan.corpus(root, "kokatsuji", units)
    directory.mkdir()
    (directory / "sample-units.json").write_text(json.dumps({"items": [{"char": "とりい"}], "meta": {}}))
    rows, _ = sample_units(root, directory)
    assert {r['unit_id'] for r in rows} == {'kokatsuji:u1', 'kokatsuji:u3'}
    assert next(r for r in rows if r['unit_id'] == 'kokatsuji:u3')['char'] == '𪜈'
    assert all(r['character_count'] == 1 and not r['needs_segmentation'] for r in rows)
    cached, _ = sample_units(root, directory)
    assert cached == rows
    retrieved, total = located_units('一', root)
    assert total == 1 and retrieved[0]['unit_id'] == 'kokatsuji:u1'
    assert _renderable_unit_counts(discover(root)) == {'U+4E00': 1, 'U+2A708': 1}
