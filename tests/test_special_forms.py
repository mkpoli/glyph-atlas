from pathlib import Path

import numpy as np
import pytest
import yaml

from glyph_atlas.special_forms import decide


def write(tmp_path: Path, forms: list[dict]) -> Path:
    path = tmp_path / "U+5019.yaml"
    path.write_text(yaml.safe_dump({"code_point": "U+5019", "run": "abc", "forms": forms}, allow_unicode=True))
    return path


def test_load_accepts_forms_covering_every_leaf(tmp_path):
    path = write(tmp_path, [{"title": "候", "written": True, "leaves": [1, 2]}, {"title": "ん", "leaves": [3, 4]}])
    assert [f["title"] for f in decide.load(path, 4)["forms"]] == ["候", "ん"]


@pytest.mark.parametrize(("forms", "message"), [
    ([{"title": "a", "leaves": [1, 2]}, {"title": "b", "leaves": [2, 3, 4]}], "leaf 2"),
    ([{"title": "a", "leaves": [1, 2]}], r"leaves \[3, 4\]"),
    ([{"title": "a", "written": True, "leaves": [1, 2]}, {"title": "b", "written": True, "leaves": [3, 4]}], "more than one"),
])
def test_load_rejects_overlap_gaps_and_two_written_forms(tmp_path, forms, message):
    with pytest.raises(ValueError, match=message):
        decide.load(write(tmp_path, forms), 4)


def test_proposal_names_one_form_per_group():
    proposal = decide.proposal("U+5019", "abc", [[1, 2], [3]])
    assert [f["title"] for f in proposal["forms"]] == ["Group 1", "Group 2"]
    assert proposal["forms"][1]["leaves"] == [3]


def test_assign_without_a_written_form_follows_the_shape_clusters():
    forms = [{"title": "a", "leaves": [1, 2]}, {"title": "b", "leaves": [3]}]
    leaf = np.array([1, 3, 2, 3])
    form, stats = decide.assign(forms, leaf, np.ones(4, int), np.ones((4, 2)), np.ones((4, 2)))
    assert form.tolist() == [0, 1, 0, 1]
    assert stats == {}


def test_keep_mask_drops_specks_and_edge_fragments():
    pytest.importorskip("skimage")
    from glyph_atlas.special_forms.ink import keep_mask

    ink = np.zeros((40, 40), bool)
    ink[10:30, 18:22] = True     # the stroke
    ink[0:6, 0:3] = True         # a fragment of the neighbour at the edge
    ink[35, 35] = True           # a speck
    keep = keep_mask(ink)
    assert keep[10:30, 18:22].all()
    assert not keep[0:6, 0:3].any()
    assert not keep[35, 35]


def test_frame_keeps_aspect_and_marks_faint_crops_empty():
    pytest.importorskip("skimage")
    from glyph_atlas.special_forms.ink import FRAME, frame_one

    crop = np.full((160, 160), 230, np.uint8)
    crop[20:140, 30:40] = 20
    grey, mask, quality = frame_one((crop, (160, 80)))
    assert grey.shape == mask.shape == (FRAME, FRAME)
    assert mask[:, :24].max() == 0 and mask.max() == 255
    assert quality[0] > 0.5
    _, mask, quality = frame_one((np.full((160, 160), 200, np.uint8), (160, 160)))
    assert mask.max() == 0 and quality[0] == 0
