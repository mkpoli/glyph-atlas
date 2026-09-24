from glyph_atlas.review.atlas import review_priority
from glyph_atlas.schema import Candidate, Confidence, Unit


def test_review_priority_uses_measured_scores_not_transcription_candidate():
    singleton = Unit(id="source", candidates=[Candidate(unicode="U+4EEE", p=1.0)])
    uncertain = Unit(id="uncertain", confidence=Confidence(text=0.6, segmentation=0.99))
    certain = Unit(id="certain", confidence=Confidence(text=0.999, segmentation=0.999))
    assert review_priority(uncertain) < review_priority(singleton) < review_priority(certain)
