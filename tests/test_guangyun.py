"""Tests for reading the 澤存堂本 廣韻 headwords from nk2028's tables."""

from __future__ import annotations

from pathlib import Path

from glyph_atlas import guangyun
from glyph_atlas.leaf_text import Head

ENTRIES = """小韻號,小韻字號,韻目原貌,音韻地位,反切,直音,字頭,字頭說明,釋義,釋義參照
1,1,東,端一東平,德紅,,東,,春方也德紅切三,
1,2,東,端一東平,德紅,,菄,,東風菜,上
1,3,東,端一東平,德紅,,鶇,,鳥名,
2a,1,東,定一東平,徒紅,,同,,齊也徒紅切二,
2a,2,東,定一東平,徒紅,,仝,,古文,
"""
ORDER = """原書小韻號,小韻字號,字,sbgy_id
1,1,東,w107b0601
1,2,汦〈泜〉,w107b0602
1,3,｛鶇｝,w107b0603
2,1,［嬹］,w107b0701
2,2,⿰隺犬,w107b0702
"""


def tables(root: Path) -> Path:
    (root / "韻書").mkdir()
    (root / "src").mkdir()
    (root / "韻書" / "廣韻.csv").write_text(ENTRIES, encoding="utf-8")
    (root / "src" / "字序表.csv").write_text(ORDER, encoding="utf-8")
    return root


def test_a_headword_is_read_as_the_leaf_prints_it() -> None:
    assert guangyun.printed("東") == ("東", None)
    assert guangyun.printed("汦〈泜〉") == ("汦", "泜")
    assert guangyun.printed("｛𪈥｝") == ("𪈥", None)
    assert guangyun.printed("［嬹］") is None
    assert guangyun.printed("⿰隺犬") == ("⿰隺犬", None)


def test_every_printed_headword_keeps_its_place_in_its_column(tmp_path: Path) -> None:
    found = guangyun.read_entries(tables(tmp_path))
    assert list(found) == [("1", 7, "b")]
    six, seven = found[("1", 7, "b")][6], found[("1", 7, "b")][7]
    # A corrected and a deleted headword are printed, so they stay in the column and its gaps.
    assert [(entry.position, entry.head) for entry in six] == [(1, Head("東", 7)), (2, Head("汦", 3)), (3, Head("鶇", 2))]
    assert [entry.corrected for entry in six] == [None, "泜", None]
    # A supplied headword is not printed; one Unicode lacks is, under its description.
    assert [(entry.entry_id, entry.head.char, entry.encoded) for entry in seven] == [("w107b0702", "⿰隺犬", False)]


def test_a_gloss_is_counted_as_the_table_gives_it(tmp_path: Path) -> None:
    six = guangyun.read_entries(tables(tmp_path))[("1", 7, "b")][6]
    # The first headword of a 小韻 already ends with its 反切, 切 and count: nothing is added.
    assert six[0].head.gloss == len("春方也德紅切三")
