"""Tests for the spellings of a word: words.tsv, word-spellings.tsv and their readers in refs."""

from __future__ import annotations

from glyph_atlas import refs
from glyph_atlas.rare_chars import is_han

TIERS = {"attested", "observed", "editorial"}
WORD_BY = {"source", "editorial"}
BASES = {"", "shape"}


def test_every_row_names_a_word_a_cited_source_and_a_tier():
    sources = refs.word_spelling_sources()
    for row in refs.word_spellings():
        assert row["word"] in refs.words(), row
        assert row["source"] in sources, row
        assert row["tier"] in TIERS and row["word_by"] in WORD_BY, row
        assert row["statement"] and row["locator"], row
        assert row["spelling"] and all(is_han(char) for char in row["spelling"]), row


def test_a_reason_is_given_only_with_what_it_stands_for():
    for row in refs.word_spellings():
        assert row["basis"] in BASES, row
        assert bool(row["basis"]) == bool(row["substitutes_for"]), row


def test_ruby_rows_carry_their_counts_and_need_two_documents():
    ruby = [row for row in refs.word_spellings() if row["source"] == "honkoku-ruby"]
    assert ruby
    for row in ruby:
        assert row["documents"] >= 2 and row["occurrences"] >= row["documents"], row
        assert row["statement"] == f"{row['spelling']}（{refs.words()[row['word']]['reading']}）", row


def test_spellings_are_found_through_the_word():
    hakari = refs.spellings_of("ja/ばかり/副助詞")
    assert {"計", "許", "斗"} <= set(hakari)
    assert {row["source"] for row in hakari["許"]} == {"unihan-kjapanese", "wiktionary-ja", "honkoku-ruby"}
    assert refs.words_of("斗") == ["ja/ばかり/副助詞"]
    assert refs.words_of("而已") == ["ja/のみ/副助詞", "ja/ばかり/副助詞"]
    assert refs.words_of("盃") == []


def test_a_shared_word_widens_nothing():
    # 斗 and 計 write one word; neither is a variant the other's gallery widens to.
    edges = {(edge["a"], edge["b"]) for edge in refs.variant_edges() if edge["widens"]}
    for a, b in (("斗", "計"), ("杯", "抔"), ("抔", "等"), ("許", "計")):
        assert (a, b) not in edges and (b, a) not in edges
