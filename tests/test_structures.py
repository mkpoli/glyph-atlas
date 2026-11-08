"""The structure layer: the canonical component tree an IDS writes, and the row it is kept in."""

from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from glyph_atlas import representation, structures
from glyph_atlas.cli import app
from glyph_atlas.review import crop_forms, ledger
from glyph_atlas.review.ledger import Claim


def test_a_named_part_and_a_component_form_are_one_structure():
    # ⿰十十 is BabelStone's analysis of 艹, and ⿰亻匕 spells 化; ⺡ is 氵 is 水.
    assert structures.for_ids("⿱⿰十十早") == structures.for_ids("⿱艹早")
    assert structures.for_ids("⿱艹⿰亻匕") == structures.for_ids("⿱艹化")
    assert structures.for_ids("⿰⺡骨") == structures.for_ids("⿰水骨")


def test_a_different_arrangement_is_a_different_structure():
    peak, = structures.for_character("峰")
    mount, = structures.for_character("峯")
    assert (peak.label, mount.label) == ("⿰山夆", "⿱山夆")
    assert peak.id != mount.id


def test_a_spelled_part_reads_as_the_character_it_spells():
    assert [item.label for item in structures.for_character("花")] == ["⿱艸化"]


def test_a_character_without_a_description_has_none():
    assert structures.for_character("あ") == []
    assert structures.for_character("一") == []


def test_an_approximate_or_malformed_description_names_no_structure():
    with pytest.raises(ValueError):
        structures.for_ids("〾⿰木木")
    with pytest.raises(ValueError):
        structures.for_ids("⿰木")


def test_a_form_structure_comes_from_its_anchor():
    assert [item.label for item in structures.for_representation(representation.typed("⿱⿰十十早"))] == ["⿱艸早"]
    assert [item.label for item in structures.for_representation(representation.typed("峰"))] == ["⿰山夆"]
    assert structures.for_representation(representation.Representation("mj", "MJ012345")) == []


def test_an_id_is_derived_from_the_canonical_tree_and_the_policy():
    item = structures.for_ids("⿱艹早")
    assert item.id.startswith("st:") and len(item.id) == 35
    assert item.id == structures.for_ids("⿱⿰十十早").id
    assert item.id != structures.for_ids("⿱早艹").id


def test_the_catalogue_names_the_structure_layer():
    spec = ledger.PREDICATES["has_structure"]
    assert (spec["subject"], spec["objects"], spec["cardinality"]) == ("form", ["structure"], "many")


def test_ensure_structure_writes_one_immutable_row():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE units (id TEXT PRIMARY KEY)")
    ledger.schema(db)
    item = structures.for_ids("⿱艹早")
    assert crop_forms.ensure_structure(db, item, actor="ann", at="2026-11-09T00:00:00Z") == item.id
    assert crop_forms.ensure_structure(db, item, actor="bob", at="2026-11-09T00:00:01Z") == item.id
    assert db.execute("SELECT label,tree,scheme,version,policy,created_by FROM structures").fetchone() == (
        item.label, json.dumps(item.tree, ensure_ascii=False), item.scheme, item.version, item.policy, "ann")
    assert db.execute("SELECT count(*) FROM structures").fetchone()[0] == 1


def test_a_form_structure_claim_resolves():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE units (id TEXT PRIMARY KEY)")
    ledger.schema(db)
    item = structures.for_ids("⿱艹早")
    crop_forms.ensure_structure(db, item, actor="ann", at="2026-11-09T00:00:00Z")
    form = "fm:" + "0" * 32
    db.execute("INSERT INTO forms(id,anchor,created_by,created_at) VALUES(?,NULL,?,?)",
               (form, "ann", "2026-11-09T00:00:00Z"))
    ledger.write_claims(db, key="ann:structure", actor="ann", request={"form": form, "structure": item.id},
                        subject=form, predicate="has_structure", claims=[Claim(object=item.id)],
                        objects={item.id: "structure"}, method="reviewer", at="2026-11-09T00:00:00Z")
    row = db.execute("SELECT status,object FROM current_claims WHERE subject=? AND predicate='has_structure'",
                     (form,)).fetchone()
    assert row == ("asserted", item.id)


def test_the_cli_prints_each_structure():
    result = CliRunner().invoke(app, ["structure", "峰", "峯"])
    assert result.exit_code == 0, result.output
    assert "⿰山夆" in result.output and "⿱山夆" in result.output and "st:" in result.output
