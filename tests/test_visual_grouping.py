"""Small behavioral checks for visual evidence naming a cluster."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")


@pytest.fixture(scope="module")
def grouping():
    spec = importlib.util.spec_from_file_location("visual_grouping_script", Path("scripts/group_visual_families.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def anchored_rows(count=6):
    members = [{"id": str(i)} for i in range(count)]
    anchors = {m["id"]: {"character": "假", "actor_kind": "machine_visual_inspection", "verified": False}
               for m in members}
    return members, anchors


def test_six_agreeing_inspections_are_required_inside_support(grouping):
    members, anchors = anchored_rows()
    assert grouping.anchored_support(members[:5], [.01] * 5, anchors, .2)[0] is None
    assert grouping.anchored_support(members, [.01] * 6, anchors, .2)[:2] == ("假", .2)
    assert grouping.anchored_support(members, [.01] * 5 + [.21], anchors, .2)[0] is None


@pytest.mark.parametrize("counterexample", [None, "仮"])
def test_uncertain_or_conflicting_inspection_shrinks_support(grouping, counterexample):
    members, anchors = anchored_rows()
    members.append({"id": "uncertain"})
    anchors["uncertain"] = {"character": counterexample}
    name, radius, votes = grouping.anchored_support(members, [.01] * 6 + [.08], anchors, .2)
    assert name == "假"
    assert radius == pytest.approx(.075)
    assert any(row["id"] == "uncertain" and row["character"] == counterexample for row in votes)
    # A central uncertainty blocks propagation even when six outer examples agree.
    assert grouping.anchored_support(members, [.02] * 6 + [.01], anchors, .2)[0] is None


def test_anchor_metadata_cannot_replace_measured_distance_or_identity(grouping):
    members, anchors = anchored_rows()
    anchors["0"].update(id="invented", distance=999.)
    name, radius, votes = grouping.anchored_support(members, [.01] * 6, anchors, .2)
    assert name == "假" and radius == .2
    assert votes[0]["id"] == "0" and votes[0]["distance"] == .01


@pytest.fixture
def small_run(grouping, tmp_path, monkeypatch):
    """Exercise publication logic on eight toy vectors without starting CUDA work."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    encoder = Path("models/classifier/artifacts/classifier-with-features.onnx")
    encoder.parent.mkdir(parents=True)
    encoder.write_bytes(b"test encoder")
    rows = [{"id": str(i), "row_index": i, "family": "U+4EEE", "corpus": "codh-full",
             "source_label": "仮", "source_code_point": "U+4EEE", "crop_sha256": f"crop-{i}",
             "source_signature": f"signature-{i}", "production": "unknown",
             "member_characters": ["仮", "假"], "members": ["U+4EEE", "U+5047"]} for i in range(8)]
    (root / "samples.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    np.save(root / "embeddings.npy", np.tile([1., 0.], (8, 1)).astype(np.float32))
    manifest = hashlib.sha256((root / "samples.jsonl").read_bytes()).hexdigest()
    embedding = hashlib.sha256((root / "embeddings.npy").read_bytes()).hexdigest()
    (root / "embeddings-metadata.json").write_text(json.dumps({"manifest_sha256": manifest,
        "embeddings_sha256": embedding, "checkpoint_sha256": "checkpoint"}))
    (root / "onnx-parity.json").write_text(json.dumps({"passed": True, "manifest_sha256": manifest,
        "checkpoint_sha256": "checkpoint", "onnx_sha256": {encoder.name: hashlib.sha256(encoder.read_bytes()).hexdigest()}}))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "set_per_process_memory_fraction", lambda *args: None)
    original_as_tensor = torch.as_tensor
    monkeypatch.setattr(torch, "as_tensor", lambda data, **kwargs: original_as_tensor(data))
    monkeypatch.setattr(grouping, "choose_groups", lambda x: (
        torch.zeros(len(x), dtype=torch.long), torch.tensor([[1., 0.]]), 0.))

    def run(anchors):
        bound = {identity: {"crop_sha256": rows[int(identity)]["crop_sha256"],
                            "source_signature": rows[int(identity)]["source_signature"], **anchor}
                 for identity, anchor in anchors.items()}
        path = root / "anchors.json"
        path.write_text(json.dumps(bound))
        grouping.run(root, path)
        return json.loads((root / "assignments.json").read_text())
    return run


@pytest.mark.parametrize("count", [1, 6])
def test_out_of_family_anchor_cannot_name_sample_or_cluster(small_run, count):
    with pytest.raises(ValueError, match="outside.*family"):
        small_run({str(i): {"character": "國"} for i in range(count)})


@pytest.mark.parametrize("field", ["crop_sha256", "source_signature"])
def test_changed_inspection_source_is_refused_before_publication(small_run, field):
    with pytest.raises(ValueError, match="stale or missing crop provenance"):
        small_run({"0": {"character": "假", field: "changed"}})


def test_direct_inspection_stays_machine_proposal_and_does_not_name_whole_group(small_run):
    result = small_run({"0": {"character": "假", "actor_kind": "machine_visual_inspection", "verified": False},
                        "1": {"character": None, "actor_kind": "machine_visual_inspection", "verified": False}})
    inspected = result["assignments"]["0"]
    assert inspected["written_character"] == "假"
    assert inspected["source_label"] == "仮"
    assert inspected["source_code_point"] == "U+4EEE"
    assert inspected["verified"] is False
    assert inspected.get("confirmed_by_human", False) is False
    assert inspected["identity_basis"] == "visual_model"
    assert inspected["assignment_method"] == "visual_inspection"
    assert inspected["inspection"]["actor_kind"] == "machine_visual_inspection"
    assert all(row["written_character"] is None for key, row in result["assignments"].items() if key != "0")
    assert result["families"]["U+4EEE"]["groups"][0]["written_character"] is None


def test_failed_independent_audit_blocks_group_assignment_but_keeps_inspected_crops(small_run):
    anchors = {str(i): {"character": "假"} for i in range(6)}
    anchors["0"]["blocks_group_assignment"] = True
    result = small_run(anchors)
    assert all(result["assignments"][str(i)]["written_character"] == "假" for i in range(6))
    assert result["assignments"]["6"]["written_character"] is None
    assert result["assignments"]["7"]["written_character"] is None
    assert result["families"]["U+4EEE"]["groups"][0]["assignment_enabled"] is False
