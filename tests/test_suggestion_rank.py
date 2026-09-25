"""The review panel's suggestion order."""
from glyph_atlas.review.suggestions import rank


def test_classifier_leads_and_models_alternate():
    ndl = [{"text": t, "engine": "NDLkotenOCR"} for t in "大太"]
    atlas = [{"text": t, "engine": "Atlas classifier"} for t in "天夭夫"]
    symbol = {"text": "〻", "engine": "Atlas symbol parts", "basis": "symbol-parts"}
    ranked = rank([*ndl, symbol, *atlas])
    assert [c["text"] for c in ranked] == ["〻", "天", "大", "夭", "太", "夫"]
    assert rank(ranked) == ranked
