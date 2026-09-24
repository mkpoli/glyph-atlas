from glyph_atlas.production import production_info


def test_reported_print_has_cited_production_even_in_imported_copy():
    result = production_info({"id": "hl:68eb3417ed31bd40b47322289459eeec",
                              "production": "unknown",
                              "source_refs": {"honkoku-data": "68eb3417ed31bd40b47322289459eeec"}})
    assert result["production"] == "movable-type"
    assert result["production_evidence"][0]["source"] == "holding_library"


def test_cursive_collection_does_not_imply_manuscript():
    assert production_info({"title": "くずし字"})["production"] == "unknown"
    assert production_info({"production": "woodblock"})["production_label"] == "Woodblock"


def test_serialized_source_references_keep_production_evidence():
    assert production_info({"source_refs": '{"honkoku-data":"68eb3417ed31bd40b47322289459eeec"}'})[
        "production"] == "movable-type"
