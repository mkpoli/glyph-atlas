"""Keep the character cards of the reference input (`glyph_atlas.reference_input`) in step with the
character table; `scripts/atlas3/reference.py` loads them onto the site.

    python scripts/export_graphemes.py --catalogue work/cloudflare-<date>/catalogue.sqlite [--aliases <file>]
    python scripts/export_graphemes.py

With `--catalogue`, the input's cards become those a full export (`export_cloudflare.py`) wrote, with
its counts of crops and corpus glyphs, and its search names (`aliases`) the export's.

Without it, a card whose family gained or lost a member, or moved to another head, since the card was
written has its family parts rewritten: the family head (`data.grapheme`), the default search scope,
whether the corpus needs the family scope and how many glyphs the family has (`candidates`), the forms
of the family (`detail.characters`), the widening a reader may choose (`detail.expansions`) and the
visual analysis the family is keyed on. Everything else in the card is its own, so the counts stay
the export's until the next full export. A character the input lacks - a sequence the table now keys,
such as 𛂞 + U+3099 - is written whole, the way a full export writes it, with no crops counted, and
gets the search names a full export gives it: its text and its lower-case code point.

The crops and corpus glyphs follow without a write of their own: `reference.py` gives every grapheme
its card's family head (`graphemes.head`), which is where the new layout files a crop under its family.
What this does not reach: the family codes the forms tables store (`form_families`, `form_clusters`,
`form_glyphs`), which describe the clustering as it ran, and the `grapheme` and `family_members` that
published crop records carry, until a publication writes them again.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from glyph_atlas import reference_input, refs, visual_families
from glyph_atlas.corpus.identity import NORMALIZED_CORPORA
from glyph_atlas.review import characters


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def point_order(point: str) -> list[int]:
    return [int(part.removeprefix("U+"), 16) for part in point.split()]


def family_of(data: dict[str, Any]) -> tuple[str | None, frozenset[str]]:
    """A card's family as it was written: its head and the code points of its members."""
    grapheme = data.get("grapheme") or {}
    return grapheme.get("code_point"), frozenset(m.get("code_point") for m in grapheme.get("members") or [])


def changed(cards: dict[str, dict[str, Any]]) -> list[str]:
    """The code points whose head or family is not the one their card was written with, and those with
    no card, in code point order.

    The head matters besides the membership: a family that moves to another representative keeps
    every member but `data.grapheme` still names the old one.
    """
    found = []
    for head, points in refs.graphemes().items():
        members = frozenset(points)
        found += [point for point in points
                  if point not in cards or family_of(cards[point]["data"]) != (head, members)]
    return sorted(found, key=point_order)


def held_cards(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {point: {"data": json.loads(data), "detail": json.loads(detail)}
            for point, data, detail in db.execute("SELECT code_point, data, detail FROM characters")}


def rewritten(point: str, live: dict[str, dict[str, Any]], counts: dict[str, int]) -> tuple[dict, dict]:
    """The card of one character with its family parts recomputed.

    `counts` are the cards' crop counts. The family's flags are the family's: a row whose own
    corpus counts were never loaded still joins a family that has them, and its gallery reads
    `requires_family_scope` and `family_glyphs` like every other member's.
    """
    row = refs.character(point)
    data, detail = live[point]["data"], live[point]["detail"]
    info = characters._row(row, counts)
    members = [member["code_point"] for member in info["grapheme"]["members"]]
    candidates = dict(data.get("candidates") or {})
    member_candidates = [(live.get(cp) or {}).get("data", {}).get("candidates") or {} for cp in members]
    candidates["requires_family_scope"] = len(members) > 1 and any(
        set(found.get("sources") or []) & NORMALIZED_CORPORA for found in member_candidates)
    candidates["family_glyphs"] = sum(found.get("glyphs") or 0 for found in member_candidates)
    scope = "grapheme" if candidates["requires_family_scope"] else info["default_scope"]
    data = {**data, "grapheme": info["grapheme"], "default_scope": scope, "candidates": candidates}
    detail = {**detail, **data,
              "characters": [characters.form_row(form, counts) for cp in characters._forms(point)
                             if (form := refs.character(cp))],
              "expansions": _expansions(row, counts, detail),
              "visual_analysis": _visual_analysis(point, info["grapheme"]["code_point"], live, members)}
    return data, detail


def fresh(point: str) -> dict[str, Any]:
    """The card a full export writes for a character the input does not hold, before its family parts.

    The same fields as `export_cloudflare.py` writes; a character no crop is written as yet has no
    counts, and the corpus index is not read here, so its candidates are what an empty index gives.
    """
    row = refs.character(point)
    info = characters._row(row, {})
    info["origin"] = refs.origin_of(point)
    info["candidates"] = characters.candidate_summary(row.char, live=None, local=0)
    info["kind"] = characters.kind_of(row)
    detail = {**info, "alias": row.alias, "category": row.category,
              "confusables": [characters.to_row(refs.character(cp)) for cp in row.confusables],
              "derived": [], "expansions": [], "visual_analysis": {}}
    return {"data": info, "detail": detail}


def _expansions(row, counts: dict[str, int], detail: dict[str, Any]) -> list[dict[str, Any]]:
    """The widenings with the family one recomputed. The kana written as a kanji keep the card's own
    `jibo` entry: its counts are the export's, which a card's change of family does not move."""
    kept = {entry["key"]: entry for entry in detail.get("expansions") or []}
    return [kept.get("jibo", entry) if entry["key"] == "jibo" else entry
            for entry in characters._expansions(row, counts, expand="none")]


def _visual_analysis(point: str, head: str, live: dict[str, dict[str, Any]], members: list[str]) -> dict:
    """The head's stored analysis when it covers the family as it now stands, else `not_analyzed`.

    The grouping runs under the head the family had when it ran, so an analysis made before a
    member arrived covers fewer characters than the row now shows — its groups would be shown for
    members it never looked at. An analysis is reused only when its `members` are exactly the
    family's; anything else reports `not_analyzed` until the grouping runs again on this family.
    """
    wanted = set(members)
    stored = ((live.get(head) or {}).get("detail") or {}).get("visual_analysis") or {}
    if stored.get("family") == head and set(stored.get("members") or ()) == wanted:
        return stored
    found = visual_families.family_analysis(point)
    if found.get("family") == head and set(found.get("members") or ()) == wanted:
        return found
    return {"status": "not_analyzed", "family": head, "model_revision": found.get("model_revision"),
            "sample_count": 0, "assigned_count": 0, "unassigned_count": 0, "groups": []}


def copy_catalogue(db: sqlite3.Connection, catalogue: Path, aliases: Path | None = None) -> dict[str, int]:
    """The input's cards and their search names replaced by those of a full export's catalogue; the names
    from `aliases` instead when the cards come from a file without them."""
    source = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True)
    named = sqlite3.connect(f"file:{aliases}?mode=ro", uri=True) if aliases else source
    n = {"cards": reference_input.replace(db, "characters", source.execute(
             "SELECT code_point, character, name, data, detail FROM characters ORDER BY code_point")),
         "aliases": reference_input.replace(db, "aliases", named.execute(
             "SELECT query, code_point, rank FROM aliases ORDER BY query, code_point"))}
    named.close()
    source.close()
    return n


def rewrite(db: sqlite3.Connection) -> dict[str, int]:
    """The cards whose family changed rewritten, the new ones added; figures of what changed."""
    cards = held_cards(db)
    points = changed(cards)
    created = [point for point in points if point not in cards]
    cards.update({point: fresh(point) for point in created})
    counts = {point: found["data"].get("occurrence_count", 0) for point, found in cards.items()}
    rows = {point: rewritten(point, cards, counts) for point in points}
    db.executemany("INSERT OR REPLACE INTO characters(code_point, character, name, data, detail) VALUES(?,?,?,?,?)",
                   [(point, refs.to_char(point), refs.character(point).name or "", encoded(data), encoded(detail))
                    for point, (data, detail) in rows.items()])
    # A new character is found by its text and its lower-case code point, as a full export names it.
    before = db.total_changes
    db.executemany("INSERT OR IGNORE INTO aliases(query, code_point, rank) VALUES(?,?,0)",
                   [(query, point) for point in created for query in (refs.to_char(point), point.lower())])
    return {"changed": len(points), "created": len(created), "aliases": db.total_changes - before,
            "families": len({data["grapheme"]["code_point"] for data, _ in rows.values()})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalogue", type=Path, help="a full export's catalogue.sqlite, whose cards the input takes")
    parser.add_argument("--aliases", type=Path,
                        help="with --catalogue, a file holding the search names when the catalogue does not")
    parser.add_argument("--input", type=Path, help="the reference input to write (default: reference-input.sqlite in ATLAS3_DIR)")
    arguments = parser.parse_args()
    with reference_input.writing(arguments.input) as db:
        if arguments.catalogue:
            print(json.dumps(copy_catalogue(db, arguments.catalogue, arguments.aliases)))
        else:
            print(json.dumps(rewrite(db)))


if __name__ == "__main__":
    main()
