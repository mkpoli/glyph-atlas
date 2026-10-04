"""Tests for the spellings read off the 振り仮名 of a みんなで翻刻データ clone."""

from __future__ import annotations

from pathlib import Path

from glyph_atlas import ruby_spellings

HEADERS = "id\tlabel\tmanifestUrl\tprojectId\tsize\tprogress\tattribution\tthumbnail\n"


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "honkoku-data"
    pages = {
        ("p", "aaaa"): {1: "半丁 斗（ばかり）に\n十間 斗（ばかり）", 2: "三日 計（バカリ）"},
        ("p", "bbbb"): {1: "千人 許（ばかり）　屋等（など）\n玉をとる／杯（など）といふ"},
        ("q", "cccc"): {3: "抔（など）\n斗（と｜ばかり）\n《振り仮名：計｜ばかり》\n一斗（いつと）"},
    }
    for (project, entry), texts in pages.items():
        info = clone / "v3" / project / "info.tsv"
        info.parent.mkdir(parents=True, exist_ok=True)
        if not info.exists():
            info.write_text(HEADERS, encoding="utf-8")
        with info.open("a", encoding="utf-8") as handle:
            handle.write(f"{entry}\t{entry}の本\t\t{project}\t1\t1\t\t\n")
        for page, text in texts.items():
            path = clone / "v3" / project / entry / f"{page:03d}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return clone


def test_rubies_give_the_whole_base_and_the_right_hand_reading():
    assert ruby_spellings.rubies("千人 許（ばかり）") == [("許", "ばかり")]
    # An uncut run of kanji is the base the transcriber marked.
    assert ruby_spellings.rubies("借屋等（など）") == [("借屋等", "など")]
    assert ruby_spellings.rubies("㗊（シウ　　ヨツコ　）") == [("㗊", "シウヨツコ")]
    # A left-hand reading is not read.
    assert ruby_spellings.rubies("斗（と｜ばかり）") == [("斗", "と")]
    # A nested ruby counts for both.
    assert ruby_spellings.rubies("《振り仮名：孿（ふた）胎｜サンタイ》") == [("孿胎", "サンタイ"), ("孿", "ふた")]
    assert ruby_spellings.rubies("ばかり") == []


def test_count_keys_by_reading_and_counts_documents(tmp_path):
    found = ruby_spellings.count(_clone(tmp_path), workers=1)
    hakari = found[("ばかり", "斗")]
    assert (hakari.occurrences, len(hakari.entries)) == (2, 1)
    assert hakari.examples == ["p/aaaa/1:1"]
    # Katakana ruby is read through hiragana and listed as typed.
    keika = found[("ばかり", "計")]
    assert (keika.occurrences, len(keika.entries), len(keika.projects)) == (2, 2, 2)
    assert set(keika.ruby) == {"バカリ", "ばかり"}
    assert ("など", "屋等") in found and ("など", "杯") in found and ("など", "抔") in found
    # Readings not asked for, and the left-hand ばかり of 斗, are not counted.
    assert ("いつと", "一斗") not in found
    assert all(reading in ruby_spellings.WORDS for reading, _ in found)


def test_rows_order_and_write(tmp_path):
    rows = ruby_spellings.rows(ruby_spellings.count(_clone(tmp_path), ["ばかり"], workers=1))
    assert [row["spelling"] for row in rows] == ["計", "斗", "許"]
    assert rows[0]["code_points"] == "U+8A08" and rows[0]["documents"] == 2
    out = tmp_path / "ruby-spellings.tsv"
    ruby_spellings.write(rows, out, revision="abc")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# ") and "revision abc" in lines[2]
    assert lines[5].split("\t") == list(ruby_spellings.COLUMNS)


def test_damage_marks_stay_and_struck_text_leaves_the_base():
    assert ruby_spellings.rubies("《振り仮名：斗｜の〓み》") == [("斗", "の〓み")]
    assert ruby_spellings.rubies("計（ば■かり）") == [("計", "ば■かり")]
    assert ruby_spellings.rubies("《振り仮名：《見せ消ち：斗｜計》｜ばかり》") == [("計", "ばかり")]
    assert ruby_spellings.rubies("《迎え仮名：計｜ばかり》") == [("計", "ばかり")]
    # A padded reading keeps the whole base: the padding does not say how many characters it skips.
    assert ruby_spellings.rubies("三尺斗（　　　ばかり）") == [("三尺斗", "ばかり")]
