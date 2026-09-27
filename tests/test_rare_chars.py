"""Tests for the rare-character report over a みんなで翻刻データ clone."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from glyph_atlas import rare_chars

HEADERS = "id\tlabel\tmanifestUrl\tprojectId\tsize\tprogress\tattribution\tthumbnail\n"
LINE = "肇（はじめ）て飍（おどろ）く金風（あきかぜ）に"


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "honkoku-data"
    pages = {
        ("p", "aaaa"): {1: "風の音\n之之之", 3: "序\n" + LINE},
        ("p", "bbbb"): {1: "風【飍とも】之\n鬱（うつ）"},
        ("q", "cccc"): {2: "之に鬱\n《振り仮名：鼠｜鼷》《注記：𪚲》《送り仮名：𬻿》⺮\n丁\x0c（じ）\n兦"},
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


def _lines(tmp_path: Path) -> Path:
    path = tmp_path / "lines.jsonl.gz"
    rows = [{"item_id": "AAAA", "image_index": 2, "text": LINE, "iiif_region_url": "https://x/1,2,3,4/full/0/default.jpg",
             "image_license": "CC-BY-4.0"},
            {"item_id": "cccc", "image_index": 1, "text": "兦／", "plain_text": "兦", "iiif_region_url": "https://x/5",
             "image_license": "PDM-1.0"}]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return path


def test_documents_count_entries_and_skip_readings_and_notes(tmp_path):
    counts = rare_chars.count(_clone(tmp_path))
    assert counts.documents["之"] == 3
    assert counts.occurrences["之"] == 5
    # 飍 in a 注記 of bbbb is the transcriber's, and ruby kana are not Han.
    assert counts.documents["飍"] == 1
    assert counts.documents["鬱"] == 2
    assert counts.entries == 3
    # Readings, notes and 送り仮名 inside 《…》 are not text of the document; radicals are not Han.
    assert counts.documents["鼠"] == 1
    assert "鼷" not in counts.documents and "𪚲" not in counts.documents and "𬻿" not in counts.documents
    assert "⺮" not in counts.documents


def test_lines_split_on_newline_only(tmp_path):
    counts = rare_chars.count(_clone(tmp_path))
    assert [(p.page, p.line) for p in counts.examples["兦"]] == [(2, 4)]


def test_readings_come_from_the_enclosing_ruby():
    assert rare_chars.readings(LINE, "飍") == [("おどろ", "")]
    assert rare_chars.readings(LINE, "金") == [("あきかぜ", "")]
    assert rare_chars.readings("飍と飍（おどろ）", "飍") == [("", ""), ("おどろ", "")]


def test_readings_keep_sides_apart_and_skip_nested_ruby():
    assert rare_chars.readings("㩜（かん｜おはしま）", "㩜") == [("かん", "おはしま")]
    assert rare_chars.readings("㗊（シウ　　ヨツコ　）", "㗊") == [("シウヨツコ", "")]
    assert rare_chars.readings("《振り仮名：孿（ふた）胎｜サンタイ》", "胎") == [("サンタイ", "")]
    assert rare_chars.readings("《振り仮名：孿（ふた）胎｜サンタイ》", "孿") == [("ふた", "")]


def test_report_lists_rare_characters_with_reading_and_line_box(tmp_path):
    rows, _ = rare_chars.report(_clone(tmp_path), _lines(tmp_path), max_documents=1)
    by_char = {row["char"]: row for row in rows}
    assert "之" not in by_char and "鬱" not in by_char and "風" not in by_char
    row = by_char["飍"]
    assert (row["code_point"], row["entry"], row["page"], row["line"]) == ("U+98CD", "aaaa", 3, 2)
    assert row["reading"] == "おどろ"
    assert row["line_box"] == "https://x/1,2,3,4/full/0/default.jpg"
    assert row["line_box_match"] == "text"
    assert row["honkoku_url"] == "https://app.honkoku.org/transcription/aaaa/3"
    assert by_char["序"]["line_box"] == ""
    assert (by_char["兦"]["line_box"], by_char["兦"]["line_box_match"]) == ("https://x/5", "plain")


def test_examples_come_from_different_entries(tmp_path):
    rows, _ = rare_chars.report(_clone(tmp_path), None, max_documents=2, examples=5)
    assert sorted(row["entry"] for row in rows if row["char"] == "鬱") == ["bbbb", "cccc"]
    assert [row["reading"] for row in rows if row["char"] == "鬱" and row["entry"] == "bbbb"] == ["うつ"]
