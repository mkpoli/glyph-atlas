"""The transcriptions' rubies as the site reads them beside a line (scripts/atlas3/rubies.py)."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))
import rubies as packs

KNOWN = {"諸": frozenset({"しよ", "もろ"}), "器": frozenset({"き", "うつわ"}), "衆": frozenset({"しゆう"}), "多": frozenset({"た"})}


def test_a_line_is_keyed_by_its_letters_and_a_ruby_placed_on_them():
    counts = Counter()
    key, found = packs.line_rubies("｜緻密（ちみつ）にして、｜諸器（しよき）に｜製（せい）す", KNOWN, counts)
    assert key == "緻密にして諸器に製す"
    assert found == [[0, 2, 0, "ちみつ"], [5, 2, 0, ["しよ", "き"]], [8, 1, 0, "せい"]]
    assert counts["rubies"] == 3 and counts["rubies cut over their characters"] == 1


def test_a_reading_is_cut_only_where_the_table_allows_one_cut():
    assert packs.split(["諸", "器"], "ショキ", KNOWN) == ["ショ", "キ"]
    assert packs.split(["衆", "多"], "しやうた", KNOWN) is None
    assert packs.split(["器"], "き", KNOWN) is None
    # Either cut would do, so none is made.
    assert packs.split(["甲", "乙"], "あああ", {"甲": frozenset({"あ", "ああ"}), "乙": frozenset({"あ", "ああ"})}) is None


def test_a_left_ruby_keeps_its_side_and_a_bracket_ruby_its_base():
    _, found = packs.line_rubies("《振り仮名：鯡漁図｜ヘロキコイキ｜ニシントリ》ヲ見ル", {}, Counter())
    assert found == [[0, 3, 0, "ヘロキコイキ"], [0, 3, 1, "ニシントリ"]]


def test_a_line_is_read_composed_as_the_site_reads_it():
    key, found = packs.line_rubies("か\u3099｜器（き）", KNOWN, Counter())
    assert key == "が器" and found == [[1, 1, 0, "き"]]
