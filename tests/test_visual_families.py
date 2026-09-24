import json

from glyph_atlas.visual_families import (
    assignment_for,
    evidence_signature,
    family_analysis,
    get_sample_image,
)


def test_proposals_preserve_source_and_refuse_changed_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path))
    row = {"id": "test:1", "source_label": "仮", "written_character": "假",
           "source_revision": "r1", "crop_sha256": "sha1", "verified": True,
           "visual_group": {"id": "U+4EEE:1"}}
    (tmp_path / "assignments.json").write_text(json.dumps({"version": 1,
        "assignments": {"test:1": row}, "families": {"U+4EEE": {"sample_count": 1}}}))
    result = assignment_for("test:1", "r1", source_label="仮")
    assert result["source_label"] == "仮"
    assert result["written_character"] == "假"
    assert result["verified"] is False
    assert assignment_for("test:1", "r2") is None
    assert assignment_for("test:1", crop_sha256="changed") is None
    assert assignment_for("test:1", source_label="別") is None
    assert family_analysis("U+5047")["sample_count"] == 1


def test_missing_registry_is_not_an_empty_verified_assignment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path))
    assert assignment_for("test:1") is None
    assert family_analysis("U+5047")["status"] == "not_analyzed"


def test_geometry_signature_is_required_and_stale_boxes_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path))
    box = {"x": 1, "y": 2, "w": 30, "h": 40}
    signature = evidence_signature("x", "U+4EEE", "p1", box)
    assert evidence_signature("x", "U+4EEE", "p1", json.dumps(box)) == signature
    (tmp_path / "assignments.json").write_text(json.dumps({"version": 1, "assignments": {
        "x": {"written_character": "假", "source_signature": signature}}}))
    assert assignment_for("x") is None
    assert assignment_for("x", source_signature=signature)["written_character"] == "假"
    changed = evidence_signature("x", "U+4EEE", "p1", {**box, "y": 3})
    assert assignment_for("x", source_signature=changed) is None


def test_sample_route_cannot_escape_prepared_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path))
    (tmp_path / "safe.jpg").write_bytes(b"crop")
    (tmp_path / "samples.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"id": "ok", "image_path": "safe.jpg"}, {"id": "bad", "image_path": "../secret.jpg"}]))
    assert get_sample_image("ok") == tmp_path / "safe.jpg"
    assert get_sample_image("bad") is None
    assert get_sample_image("../secret.jpg") is None
