"""The 異体字 graph as a character card lists it, and as the site's `character_variants` table holds it."""

import glob
import importlib.util
import sqlite3
from pathlib import Path

from glyph_atlas import refs
from glyph_atlas.review import characters

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_character_variants", ROOT / "scripts/export_character_variants.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_every_edge_is_kept_and_only_written_relations_are_written():
    edges = refs.variant_edges_of("刻")
    assert {(e["a"], e["b"], e["relation"], e["written"]) for e in edges} >= {
        ("刻", "𠚰", "variant", True), ("克", "刻", "borrowed", False), ("刻", "剋", "borrowed", False)}
    assert len(refs.variant_edges()) == sum(1 for line in (refs.VOCAB / refs.VARIANTS_TSV).open(encoding="utf-8")
                                            if not line.startswith("#")) - 1


def test_the_card_lists_written_variants_apart_from_related_characters(monkeypatch):
    monkeypatch.setattr(characters.corpus_source, "counts", lambda chars: {"𠚰": {"n_glyphs": 3}})
    card = characters.variant_card("刻", {"U+206B0": 2})
    written = [row["char"] for row in card["written"]]
    assert written[:4] == ["𠚰", "𠛳", "𠜇", "𠞫"]
    assert [row["char"] for row in card["related"]] == ["克", "剋"]
    first = card["written"][0]
    assert first["sources"] == ["cjkvi-variants", "mj-shrink-map", "wikidata"]
    assert (first["count"], first["corpus_count"]) == (2, 3)
    assert {r["relation"] for r in card["related"][0]["relations"]} == {"borrowed"}
    assert card["sources"]["wikidata"].startswith("Wikidata, property P5475")


def test_the_export_loads_every_edge_under_the_statement_limit():
    db = sqlite3.connect(":memory:")
    for path in sorted(glob.glob(str(ROOT / "apps/cloudflare/migrations/*.sql"))):
        db.executescript(Path(path).read_text(encoding="utf-8"))
    found = export.statements()
    assert max(len(s.encode()) for s in found) <= export.STATEMENT_BYTES + 1024
    db.executescript("".join(found))
    assert db.execute("SELECT count(*) FROM character_variants").fetchone()[0] == len(
        {(e["a"], e["b"], e["relation"], e["source"]) for e in refs.variant_edges()})
    assert db.execute("SELECT written FROM character_variants WHERE a='克' AND b='刻'").fetchone() == (0,)
    assert "cjkvi-variants" in db.execute("SELECT value FROM metadata WHERE key='variant_sources'").fetchone()[0]
