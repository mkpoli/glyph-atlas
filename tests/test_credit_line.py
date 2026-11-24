"""Tests for the credit a reader sees, from a recorded attribution."""

from __future__ import annotations

import pytest

from glyph_atlas.credit_line import credit_line
from glyph_atlas.rights import _raw_note


@pytest.mark.parametrize(("attribution", "credit"), [
    ("unstated holder, via Wikimedia Commons, https://commons.wikimedia.org/wiki/File:X.djvu",
     "Wikimedia Commons, https://commons.wikimedia.org/wiki/File:X.djvu"),
    ("National Library of Korea, via Wikimedia Commons, https://example.org", "National Library of Korea, Wikimedia Commons, https://example.org"),
    ("京都大学貴重資料デジタルアーカイブ (free licence with attribution)", "京都大学貴重資料デジタルアーカイブ"),
    ("俄藏黑水城文獻; scans supplied by Maxim Persikov; 第7冊", "俄藏黑水城文獻; Maxim Persikov; 第7冊"),
    (_raw_note(None, None, "福井県文書館"), "福井県文書館"),
    (_raw_note(None, "https://example.org", "神戸大学附属図書館<br />Kobe University Library"), "神戸大学附属図書館"),
    (_raw_note(None, None, '真田宝物館<br>画像ダウンロード可。<a href="https://adeac.jp/x" target="_blank">こちら</a>'), "真田宝物館"),
    (_raw_note(None, None, "holder's own"), "holder's own"),
    (_raw_note(None, None, None), None),
    ("", None),
    (None, None),
    # A name stays as its holder writes it.
    ("国立国会図書館 National Diet Library, JAPAN", "国立国会図書館 National Diet Library, JAPAN"),
    ("Provided by University of Tsukuba Library", "Provided by University of Tsukuba Library"),
    ("nk2028『tshet-uinh-images』廣韻/澤存堂本（韻典網）", "nk2028『tshet-uinh-images』廣韻/澤存堂本（韻典網）"),
])
def test_a_credit_names_sources_and_leaves_out_the_pipelines_words(attribution, credit):
    assert credit_line(attribution) == credit
    assert credit_line(credit) == credit, "a credit is its own credit"
