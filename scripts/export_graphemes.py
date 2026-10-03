"""Write the grapheme families of the character table into the live site, as SQL parts D1 can import.

    git show origin/main:data/vocab/characters.tsv > work/characters-before.tsv
    python scripts/export_graphemes.py OUT --previous work/characters-before.tsv

A character whose family gained or lost a member has its `characters` row rewritten: the family head
(`data.grapheme`), the default search scope, whether the corpus needs the family scope and how many
glyphs the family has (`candidates`), the forms of the family (`detail.characters`), the widening a
reader may choose (`detail.expansions`) and the visual analysis the family is keyed on. Everything else
in the row is the live row's own, which this reads from D1 (read-only) when it runs: the counts of
crops and corpus glyphs are the site's, and only a full publication rewrites them.

A character the site does not hold yet - a sequence the table now keys, such as 𛂞 + U+3099 - is
written whole, the way a publication writes it, and gets the aliases a publication gives it (its text
and its code points). Its crops are not counted: a crop already written as it on the site counts in
its family's totals from the next full publication on.

The parts fill a staging table, `grapheme_rows_next`; the last part swaps the rows in, inserts the
new ones, moves the crops
(`units.family`, and a corpus crop's `grapheme` and `family_members`) and the corpus glyphs
(`corpus_units.family`) of those characters to their new family, remaps the family codes the Forms
tables store (so a later `FORMS_REAPPLY` does not put an old head back on a decided glyph), and
bumps `units_refreshed_at` and `corpus_counts_at`, which the Worker keys its cached listings and
corpus counts on. D1 imports each `--file` as one transaction, so a failure before the last part
leaves the site as it was, and a rerun starts the staging table again. Every move matches at most a
batch's characters: the changed families hold ~140,000 local crops and ~547,000 corpus glyphs and
D1 answers one statement in 30 seconds, and each move skips the rows it has already moved, so a
rerun after a failure completes what the failed part did not.

What this does not reach, and a corpus re-export does:

- the corpus record packs in R2 and their copies in `corpus_gallery.data`, whose records carry
  `grapheme`, `family_members` and `written_character` as the corpus export wrote them — until it
  runs again, a review that materialises one of those records writes its old `grapheme` back into
  `units.family`;
- a row with no character to match: the moves look rows up by character, so a corpus glyph the
  corpus export left unassigned (`units.character` NULL) keeps the family it has until an export
  names it.

The Forms remap moves stored family codes only — `form_bases.family`, `form_units` `family`,
`glyph_family`, `cluster_family` and `written_family`, `form_decisions` `family` and
`written_family`, `form_marks.written_family`, `form_clusters.family`, and `form_families.code_point`,
whose row folds into the head it joins when it would collide there. The counts and labels in
`form_families` describe the clustering as it ran; the reclustering that rewrites them stays with
the session that maintains Forms.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from glyph_atlas import refs, visual_families
from glyph_atlas.corpus.identity import NORMALIZED_CORPORA
from glyph_atlas.review import characters

ROOT = Path(__file__).resolve().parents[1]
WRANGLER = ROOT / "apps" / "cloudflare"
PART_BYTES = 40 * 1024 * 1024
STATEMENT_BYTES = 90 * 1024
FETCH = 300
STAGING = "grapheme_rows_next"
STAMPS = ("units_refreshed_at", "corpus_counts_at")


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def previous_families(path: Path) -> dict[str, tuple[str, frozenset[str]]]:
    """Each code point of an earlier character table mapped to its head and that grapheme's members."""
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader([line for line in handle if not line.startswith("#")], delimiter="\t"))
    members: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        members[row["grapheme"] or row["code_point"]].add(row["code_point"])
    return {row["code_point"]: (row["grapheme"] or row["code_point"],
                                frozenset(members[row["grapheme"] or row["code_point"]]))
            for row in rows}


def changed(previous: dict[str, tuple[str, frozenset[str]]]) -> list[str]:
    """The code points whose head or family is not the one the earlier table gave them.

    The head matters besides the membership: a family that moves to another representative keeps
    every member but `data.grapheme` and `units.family` still name the old one.
    """
    now_head: dict[str, str] = {}
    now_set: dict[str, frozenset[str]] = {}
    for head, points in refs.graphemes().items():
        members = frozenset(points)
        for point in points:
            now_head[point] = head
            now_set[point] = members
    return sorted(
        (point for point in now_set
         if previous.get(point, (point, frozenset({point}))) != (now_head[point], now_set[point])),
        key=lambda point: [int(part.removeprefix("U+"), 16) for part in point.split()])


def d1(sql: str, tries: int = 4) -> list[dict[str, Any]]:
    """A read-only query against the live database, retried: the API refuses a request now and then."""
    for attempt in range(tries):
        done = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                              cwd=WRANGLER, capture_output=True, text=True, check=False)
        if done.returncode == 0:
            return json.loads(done.stdout)[0]["results"]
        if attempt < tries - 1:
            time.sleep(10)
    raise SystemExit(f"D1 refused {sql[:80]!r} {tries} times: {done.stdout[-400:]}")


def live_rows(points: list[str]) -> dict[str, dict[str, Any]]:
    found = {}
    for start in range(0, len(points), FETCH):
        batch = ",".join(quote(point) for point in points[start:start + FETCH])
        for row in d1(f"SELECT code_point,data,detail FROM characters WHERE code_point IN ({batch})"):
            found[row["code_point"]] = {"data": json.loads(row["data"]), "detail": json.loads(row["detail"])}
    return found


def rewritten(point: str, live: dict[str, dict[str, Any]], counts: dict[str, int]) -> tuple[dict, dict]:
    """The live row of one character with its family parts recomputed.

    `counts` are the live rows' crop counts. The family's flags are the family's: a row whose own
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
    """The row a publication writes for a character the site does not hold, before its family parts.

    The same fields as `export_cloudflare.py` writes; a character no crop is written as yet has no
    counts, and the corpus index is not read here, so its candidates are what an empty index gives.
    """
    row = refs.character(point)
    info = characters._row(row, {})
    info["origin"] = refs.origin_of(point)
    info["candidates"] = characters.candidate_summary(row.char, live=None, local=0)
    info["kind"] = ("ligature" if row.ligature else "han" if str(row.script) == "han"
                    else "hangul" if str(row.script) == "hangul"
                    else "gugyeol" if str(row.script) == "gugyeol" else "kana")
    detail = {**info, "alias": row.alias, "category": row.category,
              "confusables": [characters.to_row(refs.character(cp)) for cp in row.confusables],
              "derived": [], "expansions": [], "visual_analysis": {}}
    return {"data": info, "detail": detail}


def _expansions(row, counts: dict[str, int], detail: dict[str, Any]) -> list[dict[str, Any]]:
    """The widenings with the family one recomputed. The kana written as a kanji are not among the
    changed rows, so their counts are unknown here and the live row's `jibo` entry is kept as it is."""
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


#: Characters one swap statement moves. The changed families hold ~140,000 local crops and ~547,000
#: corpus glyphs (measured against the live site), and each `units` update fires migration 0032's
#: count trigger, so a statement takes a slice well under the 20,000-row slices this repo has used
#: for `units` backfills and under D1's 30-second answer time.
BATCH = 500


def statements(rows: dict[str, tuple[dict, dict]]) -> list[list[str]]:
    """The staging fill in statements under D1's statement limit, then the swap as the last group.

    Each move names its batch's characters, so `unit_character (origin, character)` and
    `corpus_character` are index prefixes with one origin per statement, and every move carries the
    mismatch guard that makes a rerun skip what it already moved.
    """
    fill = [f"DROP TABLE IF EXISTS {STAGING};\n",
            (f"CREATE TABLE {STAGING} (code_point TEXT PRIMARY KEY, character TEXT NOT NULL, name TEXT NOT NULL,"
             " family TEXT NOT NULL, data TEXT NOT NULL, detail TEXT NOT NULL) WITHOUT ROWID;\n")]
    head = f"INSERT OR REPLACE INTO {STAGING}(code_point,character,name,family,data,detail) VALUES"
    values, size = [], 0
    for point, (data, detail) in rows.items():
        value = (f"({quote(point)},{quote(refs.to_char(point))},{quote(refs.character(point).name or '')},"
                 f"{quote(data['grapheme']['code_point'])},{quote(encoded(data))},{quote(encoded(detail))})")
        if values and size + len(value.encode()) + 1 > STATEMENT_BYTES - len(head):
            fill.append(head + ",".join(values) + ";\n")
            values, size = [], 0
        values.append(value)
        size += len(value.encode()) + 1
    if values:
        fill.append(head + ",".join(values) + ";\n")
    # A character the site lacks is inserted, with the aliases a publication gives it; a rerun finds it.
    # A plain INSERT: a row that collides with another's text fails the part rather than going missing.
    swap: list[str] = [
        (f"INSERT INTO characters(code_point,character,name,data,detail)"
         f" SELECT code_point,character,name,data,detail FROM {STAGING} n"
         " WHERE NOT EXISTS (SELECT 1 FROM characters c WHERE c.code_point=n.code_point);\n"),
        (f"INSERT OR IGNORE INTO aliases(query,code_point,rank) SELECT character,code_point,0 FROM {STAGING}"
         " UNION ALL SELECT lower(code_point),code_point,0 FROM " + STAGING + ";\n"),
    ]
    points = sorted(rows, key=lambda point: [int(part.removeprefix("U+"), 16) for part in point.split()])
    for start in range(0, len(points), BATCH):
        batch = points[start:start + BATCH]
        cps = ",".join(quote(point) for point in batch)
        chars = ",".join(quote(refs.to_char(point)) for point in batch)
        swap += [
            (f"UPDATE characters SET data=n.data,detail=n.detail FROM {STAGING} n"
             f" WHERE characters.code_point=n.code_point AND n.code_point IN ({cps});\n"),
            (f"UPDATE units SET family=n.family FROM {STAGING} n WHERE units.origin='local'"
             f" AND units.character=n.character AND n.character IN ({chars})"
             " AND units.family IS NOT n.family;\n"),
            (f"UPDATE units SET family=n.family FROM {STAGING} n WHERE units.origin='corpus'"
             f" AND units.character=n.character AND n.character IN ({chars})"
             " AND units.family IS NOT n.family;\n"),
            # No mismatch guard: the head can already be right while `family_members` still names
            # the family before it grew, and the statement is idempotent either way.
            ("UPDATE units SET data=json_set(units.data,'$.grapheme',n.family,"
             "'$.family_members',json_extract(n.data,'$.grapheme.members')) "
             f"FROM {STAGING} n WHERE units.origin='corpus' AND units.character=n.character"
             f" AND n.character IN ({chars}) AND json_type(units.data,'$.grapheme') IS NOT NULL;\n"),
            # A local crop's record carries `grapheme` too (95 of them do on the live site), and a
            # review's `event_apply` restores family from it; those follow the new head as well.
            ("UPDATE units SET data=json_set(units.data,'$.grapheme',n.family) "
             f"FROM {STAGING} n WHERE units.origin='local' AND units.character=n.character"
             f" AND n.character IN ({chars}) AND json_type(units.data,'$.grapheme') IS NOT NULL"
             " AND json_extract(units.data,'$.grapheme') IS NOT n.family;\n"),
            (f"UPDATE corpus_units SET family=n.family FROM {STAGING} n WHERE corpus_units.character=n.character"
             f" AND n.character IN ({chars}) AND corpus_units.family IS NOT n.family;\n"),
        ]
    # The Forms tables store family codes, and FORMS_REAPPLY writes form_units.written_family and
    # form_bases.family back onto corpus_units at the next corpus or forms publication: the codes
    # move with the characters here (measured: 1,640 + 249 + 221 rows to move now), the clustering
    # that counts them follows the reclustering.
    swap += [
        (f"UPDATE form_units SET family=n.family FROM {STAGING} n WHERE form_units.family=n.code_point"
         " AND n.family IS NOT n.code_point AND form_units.family IS NOT n.family;\n"),
        (f"UPDATE form_units SET glyph_family=n.family FROM {STAGING} n"
         " WHERE form_units.glyph_family=n.code_point AND n.family IS NOT n.code_point"
         " AND form_units.glyph_family IS NOT n.family;\n"),
        (f"UPDATE form_units SET cluster_family=n.family FROM {STAGING} n"
         " WHERE form_units.cluster_family=n.code_point AND n.family IS NOT n.code_point"
         " AND form_units.cluster_family IS NOT n.family;\n"),
        (f"UPDATE form_units SET written_family=n.family FROM {STAGING} n"
         " WHERE form_units.written_family=n.code_point AND n.family IS NOT n.code_point"
         " AND form_units.written_family IS NOT n.family;\n"),
        (f"UPDATE form_decisions SET family=n.family FROM {STAGING} n WHERE form_decisions.family=n.code_point"
         " AND n.family IS NOT n.code_point AND form_decisions.family IS NOT n.family;\n"),
        (f"UPDATE form_decisions SET written_family=n.family FROM {STAGING} n"
         " WHERE form_decisions.written_family=n.code_point AND n.family IS NOT n.code_point"
         " AND form_decisions.written_family IS NOT n.family;\n"),
        (f"UPDATE form_marks SET written_family=n.family FROM {STAGING} n"
         " WHERE form_marks.written_family=n.code_point AND n.family IS NOT n.code_point"
         " AND form_marks.written_family IS NOT n.family;\n"),
        (f"UPDATE form_clusters SET family=n.family FROM {STAGING} n WHERE form_clusters.family=n.code_point"
         " AND n.family IS NOT n.code_point AND form_clusters.family IS NOT n.family;\n"),
        (f"UPDATE form_bases SET family=n.family FROM {STAGING} n WHERE form_bases.family=n.code_point"
         " AND n.family IS NOT n.code_point AND form_bases.family IS NOT n.family;\n"),
        # A moved head's own row rekeys onto the head it joins; where that key already exists the
        # rekey is ignored and the leftover row goes, its clusters having moved with it.
        (f"UPDATE OR IGNORE form_families SET code_point=n.family FROM {STAGING} n"
         " WHERE form_families.code_point=n.code_point AND n.family IS NOT n.code_point;\n"),
        (f"DELETE FROM form_families WHERE EXISTS (SELECT 1 FROM {STAGING} n"
         " WHERE form_families.code_point=n.code_point AND n.family IS NOT form_families.code_point);\n"),
        f"DROP TABLE {STAGING};\n",
        *(f"INSERT OR REPLACE INTO metadata(key,value) VALUES('{stamp}',"
          "json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));\n" for stamp in STAMPS),
    ]
    return [fill, swap]


def write_parts(out: Path, groups: list[list[str]]) -> list[Path]:
    """The fill split into parts under D1's upload size; the swap always a part of its own, last."""
    directory = out / "sql"
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.sql"):
        old.unlink()
    fill, swap = groups
    parts, part, size = [], [], 0
    for statement in fill:
        length = len(statement.encode())
        if length > STATEMENT_BYTES + 1024:
            raise SystemExit(f"a statement of {length} bytes is over D1's limit")
        if part and size + length > PART_BYTES:
            parts.append(part)
            part, size = [], 0
        part.append(statement)
        size += length
    parts += [part, swap] if part else [swap]
    paths = []
    for index, lines in enumerate(parts, 1):
        path = directory / f"part-{index:02d}.sql"
        path.write_text("".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


APPLY = """#!/usr/bin/env bash
# Move the site's characters, crops and corpus glyphs to the grapheme families of this export. Each
# part is one D1 transaction; the parts fill a staging table and the last one swaps it in. Every
# statement repeats safely, so a failed run is rerun from the start (the undo line above restores
# the site outright).
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd {wrangler}
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=$started"
published=$(q "SELECT value FROM metadata WHERE key='published_at'")
[ "$published" = '{published}' ] || {{ echo "the site was published again since this export read it ($published); export again" >&2; exit 1; }}
check="SELECT count(*) AS rows FROM characters WHERE json_extract(data,'$.grapheme.code_point')='{probe_head}'"
check_units="SELECT count(*) AS wrong FROM units WHERE character IN ({probe_chars}) AND family IS NOT '{probe_head}'"
echo "before: $(q "$check"), crops not under the head: $(q "$check_units")"
for part in "$here"/sql/part-*.sql; do
  {importer} "$part" || {{ echo "$(basename "$part") did not apply; rerun the apply — every part repeats safely." >&2; exit 1; }}
done
after=$(q "$check")
echo "after: $after"
[ "$after" = '{{"rows":{probe_rows}}}' ] || {{ echo 'expected {{"rows":{probe_rows}}}' >&2; exit 1; }}
units_after=$(q "$check_units")
echo "crops not under the head: $units_after"
[ "$units_after" = '{{"wrong":0}}' ] || {{ echo 'expected every crop of the family to be filed under {probe_head}' >&2; exit 1; }}
echo "done"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--previous", type=Path, required=True, help="the character table the site was published from")
    parser.add_argument("--probe", default="還", help="a character whose family apply.sh checks (default 還)")
    arguments = parser.parse_args()
    out = arguments.output
    previous = previous_families(arguments.previous)
    points = changed(previous)
    head = refs.grapheme(refs.to_code_point(arguments.probe))
    if head not in points:
        raise SystemExit(f"--probe {arguments.probe}: its family has not changed")
    published = d1("SELECT value FROM metadata WHERE key='published_at'")[0]
    live = live_rows(points)
    created = [point for point in points if point not in live]
    stale = [point for point in live
             if {member["code_point"] for member in live[point]["data"]["grapheme"]["members"]}
             != previous.get(point, (point, frozenset({point})))[1]]
    if stale:
        raise SystemExit(f"{len(stale)} live rows disagree with --previous, e.g. {stale[:5]}; "
                         "pass the table the site was published from")
    counts = {point: found["data"].get("occurrence_count", 0) for point, found in live.items()}
    live.update({point: fresh(point) for point in created})
    rows = {point: rewritten(point, live, counts) for point in points}
    paths = write_parts(out, statements(rows))
    apply = out / "apply.sh"
    apply.write_text(APPLY.format(
        wrangler=WRANGLER, importer=ROOT / "scripts" / "d1_import.sh", published=encoded(published), probe_head=head,
        probe_chars=",".join(quote(refs.to_char(point)) for point in refs.graphemes()[head]),
        probe_rows=sum(point in rows for point in refs.graphemes()[head])),
        encoding="utf-8")
    apply.chmod(0o755)
    print(json.dumps({"changed": len(points), "rows": len(rows), "created": len(created),
                      "families": len({data["grapheme"]["code_point"] for data, _ in rows.values()}),
                      "parts": [str(path.relative_to(out)) for path in paths]}))


if __name__ == "__main__":
    main()
