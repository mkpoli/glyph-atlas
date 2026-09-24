"""OCR suggestions preserve sequence boundaries and upstream image preprocessing."""
import numpy as np
from PIL import Image

from glyph_atlas.review.suggestions import Recognizer, decode, preprocess


def test_ocr_stops_at_eos_and_keeps_repeated_characters():
    logits = np.full((1, 5, 4), -9.)
    for position, token in enumerate([1, 1, 2, 0, 3]):
        logits[0, position, token] = 9
    assert decode(logits, 'アカシ')[0]['text'] == 'アアカ'
    logits[0, 0] = [9, -9, -9, -9]
    assert decode(logits, 'アカシ') == []


def test_ocr_alternatives_do_not_repeat_the_best_candidate():
    logits = np.array([[[-8, 3, 2, 1], [5, -8, -8, -8]]], dtype=float)
    results = decode(logits, 'アカシ')
    assert [r['text'] for r in results] == ['ア', 'カ', 'シ']
    assert results[0]['score'] > results[1]['score']


def test_vertical_crop_rotation_and_channel_order():
    pixels = np.zeros((6, 2, 3), dtype=np.uint8)
    pixels[:3] = [255, 0, 0]
    pixels[3:] = [0, 0, 255]
    result = preprocess(Image.fromarray(pixels), (6, 2))
    assert result.shape == (1, 3, 2, 6) and result.dtype == np.float32
    assert np.all(result[0, :, :, :3] == np.array([-1, -1, 1])[:, None, None])
    assert np.all(result[0, :, :, 3:] == np.array([1, -1, -1])[:, None, None])


def test_model_agreement_survives_candidate_deduplication():
    from types import SimpleNamespace

    class Sequence:
        def get_inputs(self):
            return [SimpleNamespace(shape=[1, 3, 8, 8], name="image")]

        def run(self, *_):
            return [np.array([[[-9., 9.], [9., -9.]]])]

    class Classifier:
        classes = ("U+5B57", "U+6587")

        def probabilities(self, _):
            return np.array([.98, .02])

    reader = Recognizer.__new__(Recognizer)
    reader.sequence, reader.classifier = Sequence(), Classifier()
    reader.alphabet, reader.engines = "字", []
    result = reader.read(Image.new("RGB", (8, 8), "white"))
    assert [v["text"] for v in result["votes"]] == ["字", "字"]
    assert [c["text"] for c in result["candidates"]].count("字") == 1


def test_normalized_classifier_groups_probability_without_exact_vote():
    from glyph_atlas.review.suggestions import classifier_results

    candidates, vote = classifier_results(("U+4EEE", "U+5047", "U+5B57", "OTHER"), [.6, .25, .1, .05])
    assert vote["text"] is None and vote["identity_scope"] == "family"
    assert vote["family"] == "U+4EEE" and vote["score"] == .85
    family = [candidate for candidate in candidates if candidate["family"] == "U+4EEE"]
    assert {candidate["text"] for candidate in family} == {"仮", "假"}
    assert all("score" not in candidate and candidate["family_score"] == .85 for candidate in family)
    assert all(candidate["verified"] is False for candidate in family)


def test_sequence_vote_remains_independent_of_normalized_classifier():
    from types import SimpleNamespace

    sequence = SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(shape=[1, 3, 8, 8], name="image")],
        run=lambda *_: [np.array([[[-9., 9.], [9., -9.]]])],
    )
    classifier = SimpleNamespace(classes=("U+4EEE", "OTHER"), probabilities=lambda _: np.array([.98, .02]))
    reader = Recognizer.__new__(Recognizer)
    reader.sequence, reader.classifier, reader.alphabet, reader.engines = sequence, classifier, "假", []
    result = reader.read(Image.new("RGB", (8, 8)))
    assert result["votes"][0]["text"] == "假"
    assert result["votes"][1]["text"] is None
    assert result["votes"][1]["identity_scope"] == "family"
    assert result["candidates"][0]["engine"] == "NDLkotenOCR"


def test_preferred_feature_model_and_explicit_override(tmp_path, monkeypatch):
    from glyph_atlas.review import suggestions

    monkeypatch.setattr(suggestions, "ROOT", tmp_path)
    monkeypatch.delenv("ATLAS_CLASSIFIER_MODEL", raising=False)
    artifacts = tmp_path / "models/classifier/artifacts"
    artifacts.mkdir(parents=True)
    assert suggestions.classifier_path() == artifacts / "classifier.onnx"
    features = artifacts / "classifier-with-features.onnx"
    features.touch()
    assert suggestions.classifier_path() == features
    monkeypatch.setenv("ATLAS_CLASSIFIER_MODEL", "custom.onnx")
    assert str(suggestions.classifier_path()) == "custom.onnx"


def test_anchored_visual_proposal_has_distances_without_probability(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from glyph_atlas.review.suggestions import visual_candidate

    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path))
    path = tmp_path / "classifier.json"
    path.write_text(json.dumps({"model_revision": "test", "encoder_sha256": "encoder",
        "centroids": [[1, 0], [0, 1]], "radii": [.1, .1], "groups": [
            {"id": "old", "family": "U+4EEE", "written_character": "假"},
            {"id": "modern", "family": "U+4EEE", "written_character": "仮"}]}))
    classifier = SimpleNamespace(features=lambda _: np.array([1., 0.]))
    vote = {"family": "U+4EEE", "members": ["仮", "假"], "identity_scope": "family"}
    image = Image.new("RGB", (8, 8))
    engines = [{"name": "Atlas classifier", "sha256": "encoder"}]
    candidate = visual_candidate(classifier, image, vote, engines)
    assert candidate["text"] == "假" and candidate["engine"] == "Atlas visual form"
    assert "score" not in candidate and candidate["verified"] is False
    assert candidate["visual_prediction"]["similarity"] == 1
    assert visual_candidate(classifier, image, vote, [{"name": "Atlas classifier", "sha256": "changed"}]) is None
    assert visual_candidate(SimpleNamespace(features=lambda _: np.array([.7, .7])), image, vote, engines) is None
