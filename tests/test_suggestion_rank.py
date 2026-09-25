"""The review panel's suggestion order."""
from glyph_atlas.review.suggestions import rank


def test_classifier_leads_and_models_alternate():
    ndl = [{"text": t, "engine": "NDLkotenOCR"} for t in "大太"]
    atlas = [{"text": t, "engine": "Atlas classifier"} for t in "天夭夫"]
    symbol = {"text": "〻", "engine": "Atlas symbol parts", "basis": "symbol-parts"}
    ranked = rank([*ndl, symbol, *atlas])
    assert [c["text"] for c in ranked] == ["〻", "天", "大", "夭", "太", "夫"]
    assert rank(ranked) == ranked


def test_an_answer_both_models_give_leads():
    # The classifier's copy of 上 was dropped as a duplicate of NDL's; its vote still names it.
    candidates = [{"text": "上", "engine": "NDLkotenOCR"}, {"text": "五", "engine": "Atlas classifier"}]
    votes = [{"text": "上", "engine": "NDLkotenOCR"}, {"text": "上", "engine": "Atlas classifier"}]
    ranked = rank(candidates, votes)
    assert [c["text"] for c in ranked] == ["上", "五"]
    assert rank(ranked, votes) == ranked
