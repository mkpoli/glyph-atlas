"""Keep known extraction failures out of shape proposals without erasing reviews."""
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import form_clusters, form_quality, forms


@pytest.fixture
def admission(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    return form_quality.Admission(tmp_path)


def unit(**changes):
    return {"id": "u1", "document_id": "d1", "line_id": "l1", "unicode": "U+306F",
            "review": "machine", "method": "detect-align", "seq": 1,
            "box": {"x": 10, "y": 40, "w": 20, "h": 20}, **changes}


def test_known_source_failures_are_excluded(admission):
    assert admission.reason(unit(review="rejected")) == "alignment-rejected"
    assert admission.reason(unit(review="disputed")) == "alignment-disputed"
    assert admission.reason(unit(meta=json.dumps({"alignment_repair": {"withheld": True}}))) == "alignment-withheld"
    assert admission.reason(unit(granularity="sequence")) == "multiple-glyphs"
    assert admission.reason(unit(group_id="g1")) == "multiple-glyphs"
    assert admission.reason(unit()) is None


def test_type_metadata_is_resolved_through_the_existing_source_override(admission):
    admission.documents = {"d1": {"id": "d1", "production": "unknown", "source_refs": json.dumps({
        "honkoku-data": "68eb3417ed31bd40b47322289459eeec"})}}
    assert admission.reason(unit()) == "movable-type"


def test_one_broken_column_withholds_the_line_but_keeps_a_human_confirmation(admission):
    rows = [unit(seq=1), unit(id="u2", seq=2, box={"x": 11, "y": 10, "w": 20, "h": 20})]
    admission.bad_lines = form_quality.backwards_lines(rows, set())
    assert admission.bad_lines == {"l1"}
    assert admission.reason(rows[0]) == "line-order"
    assert admission.reason(unit(review="reviewed")) is None
    admission.decisions["u1"] = {"form": "は", "basis": "form_cluster"}
    assert admission.reason(unit(review="rejected")) is None


def test_a_new_column_and_a_horizontal_line_are_not_vertical_reversals():
    rows = [unit(seq=1), unit(id="u2", seq=2, box={"x": 40, "y": 10, "w": 20, "h": 20})]
    assert form_quality.backwards_lines(rows, set()) == set()
    horizontal = [unit(seq=1, box={"x": 40, "y": 10, "w": 20, "h": 20}),
                  unit(id="u2", seq=2, box={"x": 10, "y": 11, "w": 20, "h": 20})]
    assert form_quality.backwards_lines(horizontal, {"l1"}) == {"l1"}


def test_decision_precedence_reopens_withdrawn_reports_without_discarding_mixed_clusters(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(path))
    base = {"id": "d1", "at": "2026-09-26", "kind": "cluster", "cluster": "c1", "units": ["u1"], "form": None}
    events = [{**base, "issue": "character"}, {**base, "id": "d2", "kind": "glyph", "form": "は"}]
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert form_quality.Admission(tmp_path).reason(unit()) is None
    events.append({**base, "id": "d3", "kind": "inherit"})
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    forms._DECISIONS.invalidate()
    assert form_quality.Admission(tmp_path).reason(unit()) == "reported-character"
    events.append({**base, "id": "d4", "issue": "mixed"})
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    forms._DECISIONS.invalidate()
    assert form_quality.Admission(tmp_path).reason(unit()) is None


def test_only_current_reviews_of_the_same_crop_and_label_are_used(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    path = tmp_path / "reviews.json"
    event = {"event": {"target_id": "u1", "role": "reviewer", "evidence": json.dumps({
        "verdict": "wrong", "issue": "merged"})}, "current": True,
        "reviewed": {"character": {"label": "は", "box": unit()["box"]}}}
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit()) == "review-merged"
    assert form_quality.Admission(tmp_path, path).reason(unit(box={"x": 10, "y": 5, "w": 20, "h": 20})) is None
    assert form_quality.Admission(tmp_path, path).reason(unit(unicode="U+304B")) is None
    event["current"] = False
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit()) is None


def test_local_corpus_review_export_carries_its_actor_and_decision_differently(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    path = tmp_path / "reviews.json"
    event = {"event": {"target_id": "u1", "actor_kind": "human", "new": {
        "verdict": "wrong", "issue": "crop"}}, "current": True,
        "reviewed": {"source_label": "は", "box": unit()["box"]}}
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit()) == "review-crop"
    event["event"]["actor_kind"] = "model"
    event["event"]["role"] = "reviewer"
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit()) is None


def test_implicit_quiz_match_does_not_override_a_bad_alignment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    path = tmp_path / "reviews.json"
    event = {"event": {"target_id": "u1", "role": "reviewer", "evidence": json.dumps({
        "kind": "visual-quiz", "verdict": "match", "issue": "reading"})}, "current": True,
        "reviewed": {"character": {"label": "は", "box": unit()["box"]}}}
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit(review="rejected")) == "alignment-rejected"
    event["event"]["evidence"] = json.dumps({"kind": "character-review", "verdict": "match"})
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit(review="rejected")) is None
    event["event"]["evidence"] = json.dumps({"verdict": "wrong", "issue": "reading", "layer": "reading"})
    path.write_text(json.dumps({"kind": "atlas-character-reviews", "reviews": [event]}))
    assert form_quality.Admission(tmp_path, path).reason(unit()) is None


def source_clustering(root: Path, ids: list[str], directory: Path):
    directory.mkdir()
    pq.write_table(pa.table({"id": ids, "family": ["U+306F"] * len(ids), "cluster": ["c1"] * len(ids),
                            "similarity": [0.9] * len(ids), "rank": list(range(len(ids)))}), directory / "units.parquet")
    np.save(directory / "embeddings.npy", np.arange(len(ids) * 4, dtype=np.float16).reshape(len(ids), 4))
    (directory / "clusters.json").write_text(json.dumps({"revision": "old", "method": form_clusters.METHOD,
        "families": {"U+306F": {"family": "U+306F", "count": len(ids), "clusters": [{"id": "c1",
            "count": len(ids), "representatives": ids[:2], "coherence": 0.9}]}}}))
    inputs = {name: {c.name: [form_clusters._digest(p) for p in c.parquet_files(name)]
                    for c in form_clusters._corpora(root)} for name in ("units", "pages")}
    (directory / "manifest.json").write_text(json.dumps({"revision": "old", "inputs": inputs}))


def test_clean_preserves_embeddings_decisions_and_source_revision(form_corpora, tmp_path, monkeypatch):
    root, ids = form_corpora
    source, out = tmp_path / "source", tmp_path / "clean"
    decisions = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(decisions))
    event = {"id": "e1", "kind": "cluster", "cluster": "c1", "at": "2026-09-26", "units": [ids["held"][0]],
             "form": None, "issue": "crop"}
    decisions.write_text(json.dumps(event) + "\n")
    all_ids = [*ids["held"], ids["crop"]]
    source_clustering(root, all_ids, source)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = form_clusters.clean(root, source, out)
    assert result["units"] == 2 and result["excluded"] == 1
    current = out / "current"
    table = pq.read_table(current / "units.parquet").to_pydict()
    assert table["id"] == all_ids[1:] and table["rank"] == [0, 1]
    assert np.array_equal(np.load(current / "embeddings.npy"), np.load(source / "embeddings.npy")[1:])
    clusters = json.loads((current / "clusters.json").read_text())["families"]["U+306F"]["clusters"]
    assert clusters[0]["representatives"] == all_ids[1:]
    assert json.loads((current / "exclusions.jsonl").read_text())["reason"] == "reported-crop"
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert decisions.read_text() == json.dumps(event) + "\n"
    assert form_clusters.clean(root, source, out) == result
    (root / "hilab/units.parquet").touch()  # timestamps alone do not make embeddings stale
    assert form_clusters.clean(root, source, out) == result
    table = pq.read_table(root / "hilab/units.parquet").to_pydict()
    table["unicode"][0] = "U+304B"
    pq.write_table(pa.table(table), root / "hilab/units.parquet")
    with pytest.raises(ValueError, match="units changed"):
        form_clusters.clean(root, source, out)


def test_clustering_applies_the_same_gate_before_loading_a_model(form_corpora, tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "torch", None)
    root, ids = form_corpora
    decisions = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(decisions))
    decisions.write_text(json.dumps({"id": "e1", "kind": "cluster", "cluster": "c1", "at": "2026-09-26",
        "units": [*ids["held"], ids["crop"], ids["unheld"]], "form": None, "issue": "crop"}) + "\n")
    checkpoint = tmp_path / "unused.pt"
    checkpoint.write_bytes(b"unused")
    result = form_clusters.run(root, tmp_path / "out", checkpoint=checkpoint, workers=0)
    assert result["units"] == 0 and result["clusters"] == 0
    assert pq.read_table(tmp_path / "out/current/units.parquet").num_rows == 0


def test_clean_can_exclude_the_last_cluster(form_corpora, tmp_path, monkeypatch):
    root, ids = form_corpora
    source = tmp_path / "source"
    source_clustering(root, ids["held"], source)
    decisions = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(decisions))
    decisions.write_text(json.dumps({"id": "e1", "kind": "cluster", "cluster": "c1", "at": "2026-09-26",
        "units": ids["held"], "form": None, "issue": "crop"}) + "\n")
    result = form_clusters.clean(root, source, tmp_path / "empty")
    assert result["units"] == result["clusters"] == result["families"] == 0
    assert result["excluded"] == 2
    assert np.load(tmp_path / "empty/current/embeddings.npy").shape == (0, 4)
