"""Tests for the reading text of a line, the constraints its rubies set, and the text build."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from glyph_atlas import text_build
from glyph_atlas import tokens as tk


def _runs(raw: str) -> list[tuple[str, str]]:
    return [(run.kind, run.text) for run in tk.read_line(raw).runs]


def _forced(raw: str) -> list[tuple[str, bool, str]]:
    reading = tk.read_line(raw)
    return [(reading.runs[c.run].text[c.start:c.end], c.whole, c.reading) for c in reading.constraints]


def test_kana_key_meets_modern_kana():
    assert tk.kana_key("しやうばい") == tk.kana_key("ショウバイ")
    assert tk.kana_key("てかた") == tk.kana_key("テガタ")
    assert tk.kana_key("くわじ") == tk.kana_key("カジ")
    assert tk.kana_key("こゝろえ") == tk.kana_key("ココロエ")
    assert tk.kana_key("いへ") == tk.kana_key("イエ")
    assert tk.kana_key("ほとゝぎす") == tk.kana_key("ホトトギス")
    assert tk.kana_key("たなはた") == tk.kana_key("タナバタ")
    assert tk.kana_key("はぢ") == tk.kana_key("ハジ")
    assert tk.kana_key("れいぢやう") == tk.kana_key("レイジョウ")
    assert tk.kana_key("たなばた") != tk.kana_key("シチセキ")


def test_the_reading_text_leaves_out_rubies_notes_and_struck_text():
    assert _runs("村落（ざいご）へ") == [("main", "村落へ")]
    assert _runs("其【ママ】国") == [("main", "其"), ("main", "国")]
    assert _runs("《見せ消ち：誤｜正》字") == [("main", "正字")]
    # Struck text is skipped without breaking the corrected word.
    assert _runs("《振り仮名：日《見せ消ち：本｜光》｜にっこう》") == [("main", "日光")]
    # A correction nested in a reading belongs to the reading.
    assert _runs("《振り仮名：恵｜《見せ消ち：カ｜メグ》》の") == [("main", "恵の")]


def test_damage_marks_and_spaces_end_a_run():
    assert _runs("甲■乙") == [("main", "甲"), ("main", "乙")]
    assert _runs("甲　乙") == [("main", "甲"), ("main", "乙")]
    assert _runs("面\x08体") == [("main", "面"), ("main", "体")]


def test_a_warigaki_is_a_run_of_its_own():
    # Its columns are read in order, as one text.
    assert _runs("村《割書：注｜記》落") == [("main", "村"), ("warigaki", "注記"), ("main", "落")]
    # Two 割書 in one 題 are two runs, and a correction inside one stays in it.
    assert _runs("《題：《割書：一｜二》《割書：三｜四》》") == [("warigaki", "一二"), ("warigaki", "三四")]
    assert _runs("《割書：甲《見せ消ち：誤｜正》乙｜丙》") == [("warigaki", "甲正乙丙")]


def test_a_kanbun_line_is_not_read():
    reading = tk.read_line("月（つき）＿レ見")
    assert reading.kanbun and reading.runs == []


def test_rubies_force_their_bases():
    assert _forced("村落（ざいご）へ支給（わりわた）し") == [("村落", True, "ざいご"), ("支給", True, "わりわた")]
    # The outer base of a nested ruby is bounded at its ends only.
    assert _forced("《振り仮名：孿（ふた）胎｜サンタイ》") == [("孿胎", False, "サンタイ"), ("孿", True, "ふた")]
    # A base broken by a damage mark is not forced.
    assert _forced("甲《振り仮名：乙■丙｜かな》丁") == []
    # Nested rubies over the same characters: the outer is bounded, the inner is one token.
    assert _forced("《振り仮名：《振り仮名：甲｜こう》｜かぶと》") == [("甲", False, "かぶと"), ("甲", True, "こう")]
    assert _forced("《振り仮名：日《見せ消ち：本｜光》｜にっこう》") == [("日光", True, "にっこう")]


def test_an_entry_with_too_few_features_is_unknown():
    item = tk.entry("支給", "名詞,普通名詞,サ変可能,*,*,*")
    assert not item.known and (item.lemma, item.reading, item.pos, item.kana) == ("", "", "", "")


class _Stub:
    """One token per character; 落 is unknown. Stands in for MeCab in the build test."""

    name, version = "stub", "0"

    def __init__(self, dictionary: Path) -> None:
        pass

    def line(self, reading: tk.Reading) -> tuple[list[tk.Token], dict[int, str]]:
        out = []
        for index, run in enumerate(reading.runs):
            for at, char in enumerate(run.text):
                lemma = None if char == "落" else ord(char)
                item = tk.Entry(char, lemma, char, "カナ", "名詞", "", "", "カナ")
                out.append(tk.Token(index, at, at + 1, item, tk.UNKNOWN if lemma is None else tk.PARSED))
        return out, {}


def _clone(root: Path) -> Path:
    project = root / "v3" / "demo"
    (project / "E1").mkdir(parents=True)
    (project / "info.tsv").write_text("id\tlabel\nE1\t試\n", encoding="utf-8")
    (project / "E1" / "001.txt").write_text("村落（ざいご）へ\n\n月（つき）＿レ見\n", encoding="utf-8")
    (project / "E1" / "002.txt").write_text("■\n", encoding="utf-8")
    return root


def test_build_writes_tokens_rubies_and_keeps_page_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "Analyser", _Stub)
    clone, out = _clone(tmp_path / "clone"), tmp_path / "text.sqlite"
    (tmp_path / "sys.dic").write_bytes(b"stub")
    figures = text_build.build(clone, out, tmp_path, workers=1)
    assert figures["tokens"] == 3 and figures["tokens unknown"] == 1
    assert figures["kanbun lines"] == 1 and figures["lines without text"] == 1
    db = sqlite3.connect(out)
    raw, blob, start, end = db.execute(
        "SELECT l.raw, r.offsets, t.start, t.end FROM tokens t JOIN runs r USING (page, line, run) "
        "JOIN lines l USING (page, line) JOIN forms f ON f.id = t.form WHERE f.surface = 'へ'").fetchone()
    assert [raw[at] for at in text_build.offsets(blob)[start:end]] == ["へ"]
    assert db.execute("SELECT rb, side, rt FROM pairs ORDER BY id").fetchall() == [
        ("村落", "right", "ざいご"), ("月", "right", "つき")]
    pages = dict(db.execute("SELECT page, id FROM pages WHERE entry = 'E1'").fetchall())
    db.close()
    # A page that loses its text keeps its number, and a new page takes a fresh one.
    (clone / "v3" / "demo" / "E1" / "001.txt").write_text("\n", encoding="utf-8")
    (clone / "v3" / "demo" / "E1" / "003.txt").write_text("月\n", encoding="utf-8")
    text_build.build(clone, out, tmp_path, workers=1)
    (clone / "v3" / "demo" / "E1" / "001.txt").write_text("村\n", encoding="utf-8")
    text_build.build(clone, out, tmp_path, workers=1)
    with sqlite3.connect(out) as again:
        now = dict(again.execute("SELECT page, id FROM pages WHERE entry = 'E1'").fetchall())
    assert now[1] == pages[1] and now[2] == pages[2] and now[3] not in pages.values()


UNIDIC = os.environ.get("GLYPH_ATLAS_UNIDIC")


@pytest.mark.skipif(not UNIDIC, reason="GLYPH_ATLAS_UNIDIC names no dictionary")
def test_rubies_choose_entries_with_a_real_dictionary():
    analyser = tk.Analyser(Path(UNIDIC))
    reading = tk.read_line("村落（ざいご）へ支給（わりわた）し七夕（たなばた）の祭")
    tokens, _ = analyser.line(reading)
    found = {reading.runs[t.run].text[t.start:t.end]: (t.entry.reading, t.basis) for t in tokens}
    assert found["七夕"] == ("タナバタ", tk.PARSED)
    assert found["支給"][1] == tk.UNKNOWN
    assert found["村落"] == ("ソンラク", tk.RUBY_UNMATCHED)


def _outcomes(analyser: tk.Analyser, raw: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    reading = tk.read_line(raw)
    found, outcomes = analyser.line(reading)
    words = {reading.runs[t.run].text[t.start:t.end]: (t.entry.kana, t.basis) for t in found}
    return words, [outcomes.get(n) for n in range(len(reading.marks))]


@pytest.mark.skipif(not UNIDIC, reason="GLYPH_ATLAS_UNIDIC names no dictionary")
def test_the_ruby_test_compares_the_token_kana():
    analyser = tk.Analyser(Path(UNIDIC))
    words, outcomes = _outcomes(analyser, "七夕（たなはた）の祭")
    assert words["七夕"] == ("タナバタ", tk.PARSED) and outcomes == [tk.AGREED]
    # An outer ruby over several tokens is tested on their joined kana.
    _, outcomes = _outcomes(analyser, "《振り仮名：一（いち）日｜でたらめ》")
    assert outcomes == [tk.UNMATCHED, tk.AGREED]


@pytest.mark.skipif(not UNIDIC, reason="GLYPH_ATLAS_UNIDIC names no dictionary")
def test_control_characters_and_a_terminator_segment_parse_whole():
    analyser = tk.Analyser(Path(UNIDIC))
    for raw in ("甲\x00乙", "EOS《振り仮名：甲｜こう》", "《振り仮名：甲｜こう》EOS《振り仮名：乙｜おつ》"):
        reading = tk.read_line(raw)
        found, _ = analyser.line(reading)
        assert sum(t.end - t.start for t in found) == sum(len(run.text) for run in reading.runs)
