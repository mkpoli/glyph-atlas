from copy import deepcopy

from glyph_atlas.visual_aliases import alias_assignments


def records():
    return ({"original": {"id": "original", "family": "U+4EEE", "source_label": "仮",
                          "source_code_point": "U+4EEE", "source_signature": "original-signature",
                          "crop_sha256": "identical-image", "written_character": "假",
                          "visual_group": {"id": "group-one"}, "verified": False}},
            [{"excluded_id": "alias", "kept_id": "original", "family": "U+4EEE",
              "source_label": "假", "source_code_point": "U+5047", "source_signature": "alias-signature",
              "kept_source_signature": "original-signature", "crop_sha256": "identical-image",
              "same_crop_bytes": True}])


def test_identical_image_reuses_visual_result_with_own_source_provenance():
    assignments, duplicates = records()
    original = deepcopy(assignments)
    aliases = alias_assignments(assignments, duplicates)
    row = aliases["alias"]
    assert row["source_label"] == "假"
    assert row["source_code_point"] == "U+5047"
    assert row["source_signature"] == "alias-signature"
    assert row["written_character"] == "假"
    assert row["visual_group"] == {"id": "group-one"}
    assert row["verified"] is False and row["confirmed_by_human"] is False
    assert assignments == original
    row["visual_group"]["id"] = "changed"
    assert assignments == original


def test_alias_refuses_changed_bytes_source_signature_family_or_existing_assignment():
    for field in ("crop_sha256", "kept_source_signature", "family"):
        assignments, duplicates = records()
        duplicates[0][field] = "changed"
        assert alias_assignments(assignments, duplicates) == {}
    assignments, duplicates = records()
    assignments["alias"] = {"written_character": "another reviewed identity"}
    assert alias_assignments(assignments, duplicates) == {}


def test_an_alias_does_not_inherit_the_kept_source_s_inspection():
    assignments, duplicates = records()
    assignments["original"].update(inspection={"id": "original", "source_signature": "original-signature"},
                                   assignment_method="visual_inspection")
    row = alias_assignments(assignments, duplicates)["alias"]
    assert row["inspection"] is None and row["assignment_method"] == "identical_crop"
