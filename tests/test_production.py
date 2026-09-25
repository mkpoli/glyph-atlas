import pytest

from glyph_atlas import production
from glyph_atlas.production import production_info


def test_reported_print_has_cited_production_even_in_imported_copy():
    result = production_info({"id": "hl:68eb3417ed31bd40b47322289459eeec",
                              "production": "unknown",
                              "source_refs": {"honkoku-data": "68eb3417ed31bd40b47322289459eeec"}})
    assert result["production"] == "printed/type"
    assert result["production_evidence"][0]["source"] == "holding_library"


def test_cursive_collection_does_not_imply_manuscript():
    assert production_info({"title": "くずし字"})["production"] == "unknown"
    assert production_info({"production": "printed/woodblock"})["production_label"] == "Woodblock"


def test_serialized_source_references_keep_production_evidence():
    assert production_info({"source_refs": '{"honkoku-data":"68eb3417ed31bd40b47322289459eeec"}'})[
        "production"] == "printed/type"


def test_a_value_outside_the_vocabulary_is_refused():
    with pytest.raises(ValueError):
        production_info({"production": "woodblock"})
    with pytest.raises(ValueError):
        production.check_scope("not:movable-type")


def test_every_node_has_a_label_and_a_definition():
    for node in production.vocabulary().values():
        assert node["en"] and node["definition"]


def test_a_scope_takes_a_node_with_everything_under_it():
    assert production.in_scope("printed/type/metal/copper", "printed/type")
    assert production.in_scope("printed/type", "printed/type")
    assert not production.in_scope("printed/typewriter", "printed/type")
    assert not production.in_scope("printed", "printed/type")
    assert production.in_scope("printed", "not:printed/type")
    assert not production.in_scope("printed/type/wood", production.REVIEW_SCOPE)
    assert production.in_scope("handwritten", "all")


def test_every_node_has_one_interface_label_and_no_more():
    import json
    from pathlib import Path

    catalogue = json.loads((Path(__file__).parents[1] / "apps/review/src/locales/en.json").read_text(encoding="utf-8"))
    keys = {key.removeprefix("production.kind.") for key in catalogue if key.startswith("production.kind.")}
    assert keys == {node.replace("/", "_") for node in production.vocabulary()}



def test_the_worker_knows_every_node():
    import re
    from pathlib import Path

    worker = (Path(__file__).parents[1] / "apps/cloudflare/src/index.ts").read_text(encoding="utf-8")
    listed = re.search(r"export const PRODUCTIONS = \[(.*?)\];", worker).group(1)
    assert re.findall(r"'([^']+)'", listed) == list(production.vocabulary())
