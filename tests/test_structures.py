"""The structure layer: the canonical component tree an IDS writes, and the row it is kept in."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from glyph_atlas import han_component_variants, han_components, representation, structures
from glyph_atlas.cli import app
from glyph_atlas.review import crop_forms, ledger
from glyph_atlas.review.ledger import Claim

ROOT = Path(__file__).resolve().parents[1]


def test_a_named_part_and_a_component_form_are_one_structure():
    # ⿰十十 is BabelStone's analysis of 艹, and ⿰亻匕 spells 化; ⺡ is 氵 and 氵 at the left is 水.
    assert structures.for_ids("⿱⿰十十早") == structures.for_ids("⿱艹早")
    assert structures.for_ids("⿱艹⿰亻匕") == structures.for_ids("⿱艹化")
    assert structures.for_ids("⿰⺡骨") == structures.for_ids("⿰水骨")
    assert structures.for_ids("⿰亻木") == structures.for_ids("⿰人木")


def test_a_part_spelled_with_a_read_component_is_still_named():
    # 休 is ⿰亻木 in BabelStone; a description that writes the part ⿰人木 names the same character.
    assert structures.for_ids("⿱⿰人木？") == structures.for_ids("⿱⿰亻木？")
    assert structures.for_ids("⿱⿰人木？").label == "⿱休？"
    assert structures.for_ids("⿰言⿱丷兄") == structures.for_character("說")


def test_a_variation_selector_in_a_description_is_dropped():
    assert structures.for_ids("⿰葛\U000e0100木") == structures.for_ids("⿰葛木")


def test_an_index_equivalence_is_not_a_composition():
    # 苗/苖, 細/紳 and 鴨/鴫 differ in a component; 田/由/申 and 甲/田 are radical-index rows.
    assert structures.for_ids("⿱艹田").id != structures.for_ids("⿱艹由").id
    assert structures.for_ids("⿰糸田").id != structures.for_ids("⿰糸申").id
    assert structures.for_ids("⿰甲鳥").id != structures.for_ids("⿰田鳥").id
    assert structures.for_character("細") != structures.for_character("紳")


def test_a_radical_form_folds_only_in_its_position():
    assert structures.for_ids("⿰東阝").label == "⿰東邑"
    assert structures.for_ids("⿰阝東").label == "⿰阝東"


def test_a_different_arrangement_is_a_different_structure():
    peak, mount = structures.for_character("峰"), structures.for_character("峯")
    assert (peak.label, mount.label) == ("⿰山夆", "⿱山夆")
    assert peak.id != mount.id


def test_a_spelled_part_reads_as_the_character_it_spells():
    assert structures.for_character("花").label == "⿱艸化"


@pytest.mark.parametrize("chars", ["說説", "內内", "戶户戸", "靑青", "清淸", "吳吴呉", "煮煑"])
def test_shapes_the_irg_unifies_are_one_structure(chars):
    # The IRG list, level 1: 兌 兑, 內 内, 戶 户 戸, 靑 青, 吳 吴 呉; 灬 is 火 at the bottom.
    assert len({structures.for_character(char) for char in chars}) == 1


def test_a_unified_shape_reads_as_the_first_its_entry_names():
    assert structures.for_character("説").label == "⿰言兌"
    assert structures.for_character("納").label == "⿰糸內"
    assert structures.for_ids("⿰糹内") == structures.for_character("納")


def test_two_characters_the_irg_lists_together_stay_apart_unless_they_are_variants():
    # 主 and 龶 are one shape at the top of 青 (entry 201), and 住 is still ⿰亻主; level 2 lists 壬
    # and 王 with 任 and 仼 as characters the variation tells apart.
    assert structures.for_character("住").label == "⿰人主"
    assert structures.for_character("任") != structures.for_character("仼")
    assert structures.for_character("土") != structures.for_character("士")


def test_a_character_has_one_structure_whatever_its_regional_descriptions():
    # 胃 is ⿱田⺝ in one region and ⿱田⺼ in another; 緅 is ⿰糹取 and ⿰糸⿸耳又.
    stomach = structures.for_character("胃")
    assert stomach.label == "⿱田月"
    assert structures.for_ids("⿱田⺼") == stomach
    assert structures.for_ids("⿰糸⿸耳又") == structures.for_character("緅")


def test_a_character_described_as_itself_is_its_own_structure():
    assert structures.for_character("一").label == "一"


def test_a_character_without_a_usable_description_has_none():
    assert structures.for_character("あ") is None
    assert structures.for_character("七") is None  # 〾⿻一乚: approximate


def test_an_approximate_or_malformed_description_names_no_structure():
    with pytest.raises(ValueError):
        structures.for_ids("〾⿰木木")
    with pytest.raises(ValueError):
        structures.for_ids("⿰木")


def test_a_written_description_may_keep_an_unnamed_part():
    assert structures.for_ids("⿰木？").label == "⿰木？"


def test_a_form_structure_comes_from_its_anchor():
    assert structures.for_representation(representation.typed("⿱⿰十十早")).label == "⿱艸早"
    assert structures.for_representation(representation.typed("峰")).label == "⿰山夆"
    assert structures.for_representation(representation.Representation("mj", "MJ012345")) is None


def test_a_representation_that_names_no_structure_returns_none():
    assert structures.for_representation(representation.Representation("ids", "〾⿰木木")) is None
    assert structures.for_representation(representation.Representation("ivs", "")) is None


def test_every_label_names_its_own_structure():
    for char in han_components._sequences():
        item = structures.for_character(char)
        if item is None:
            continue
        assert structures.from_tree(han_component_variants.parse(item.label)) == item, char
        if len(item.label) > 1 and "{" not in item.label:
            assert structures.for_ids(item.label) == item, char


def test_a_radical_row_that_is_another_shape_elsewhere_is_not_read():
    # CJKVI gives 䒑 as 艸 at the top; in 前 it is 丷 over 一 (IRG entry 23).
    assert structures.for_character("前").label == "⿱䒑刖"
    assert structures.for_character("前") != structures.for_character("𦮯")


def test_characters_the_irg_lists_as_encoded_apart_keep_their_own_structure():
    # Entry 22 unifies 丷 and 八 as components and lists the two characters as disunified.
    assert structures.for_character("八") != structures.for_character("丷")
    assert structures.for_character("兌") == structures.for_character("兑")


def test_a_description_that_is_a_characters_own_structure_names_it():
    # 黒 has 黑's structure, and ⿱里火 is 𭴬's own.
    assert structures.for_character("黒") == structures.for_character("黑")
    assert structures.for_ids("⿱里火") == structures.for_character("𭴬")


def test_the_policy_names_the_tables_it_reads():
    for name, digest in structures.TABLES.items():
        assert hashlib.sha256((ROOT / "data/vocab" / name).read_bytes()).hexdigest()[:16] == digest, name


def test_an_id_is_derived_from_the_canonical_tree_and_the_policy():
    item = structures.for_ids("⿱艹早")
    assert re.fullmatch(r"st:[0-9a-f]{32}", item.id)
    assert item.id == structures.for_ids("⿱⿰十十早").id
    assert item.id != structures.for_ids("⿱早艹").id


def test_the_ids_version_is_the_source_files():
    source = yaml.safe_load((ROOT / "data/sources/babelstone-ids.yaml").read_text(encoding="utf-8"))
    assert structures.VERSION in source["version"]
    assert f"File Date {structures.VERSION}" in (ROOT / "data/vocab/han-ids.tsv").read_text(encoding="utf-8")[:2000]


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
    with pytest.raises(sqlite3.DatabaseError, match="structure_immutable"):
        db.execute("UPDATE structures SET label='x' WHERE id=?", (item.id,))
    with pytest.raises(sqlite3.DatabaseError, match="structure_immutable"):
        db.execute("DELETE FROM structures WHERE id=?", (item.id,))


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


def test_the_cli_prints_a_structure():
    result = CliRunner().invoke(app, ["structure", "峰", "峯"])
    assert result.exit_code == 0, result.output
    assert "⿰山夆" in result.output and "⿱山夆" in result.output and "st:" in result.output


def test_the_cli_reads_a_bare_code_point():
    result = CliRunner().invoke(app, ["structure", "5CF0", "U+8AAC"])
    assert result.exit_code == 0, result.output
    assert "U+5CF0 峰  ⿰山夆" in result.output and "U+8AAC 説  ⿰言兌" in result.output
