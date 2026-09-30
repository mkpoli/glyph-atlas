"""Write the grapheme families of the character table into the live site, as SQL parts D1 can import.

    git show origin/main:data/vocab/characters.tsv > work/characters-before.tsv
    python scripts/export_graphemes.py OUT --previous work/characters-before.tsv

A character whose family gained or lost a member has its `characters` row rewritten: the family head
(`data.grapheme`), the default search scope, whether the corpus needs the family scope and how many
glyphs the family has (`candidates`), the forms of the family (`detail.characters`), the widening a
reader may choose (`detail.expansions`) and the visual analysis the family is keyed on. Everything else
in the row is the live row's own, which this reads from D1 (read-only) when it runs: the counts of
crops and corpus glyphs are the site's, and only a full publication rewrites them.

The parts fill a staging table, `grapheme_rows_next`; the last part swaps the rows in, moves the crops
(`units.family`, and a corpus crop's `grapheme` and `family_members`) and the corpus glyphs
(`corpus_units.family`) of those characters to their new family, and bumps `units_refreshed_at` and
`corpus_counts_at`, which the Worker keys its cached listings and corpus counts on. D1 imports each
`--file` as one transaction, so a failure before the last part leaves the site as it was, and a rerun
starts the staging table again.

What this does not reach: the corpus record packs in R2, whose records carry `grapheme`,
`family_members` and `written_character` as the corpus export wrote them (a corpus re-export does),
and the Forms tables, whose families and clusters the forms export writes.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
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


def previous_families(path: Path) -> dict[str, frozenset[str]]:
    """Each code point of an earlier character table mapped to the members of its grapheme."""
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader([line for line in handle if not line.startswith("#")], delimiter="\t"))
    members: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        members[row["grapheme"] or row["code_point"]].add(row["code_point"])
    return {row["code_point"]: frozenset(members[row["grapheme"] or row["code_point"]]) for row in rows}


def changed(previous: dict[str, frozenset[str]]) -> list[str]:
    """The code points whose family is not the one the earlier table gave them."""
    now = {}
    for points in refs.graphemes().values():
        for point in points:
            now[point] = frozenset(points)
    return sorted((point for point in now if previous.get(point, frozenset({point})) != now[point]),
                  key=lambda point: int(point.removeprefix("U+"), 16))


def d1(sql: str) -> list[dict[str, Any]]:
    """A read-only query against the live database."""
    done = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                          cwd=WRANGLER, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)[0]["results"]


def live_rows(points: list[str]) -> dict[str, dict[str, Any]]:
    found = {}
    for start in range(0, len(points), FETCH):
        batch = ",".join(quote(point) for point in points[start:start + FETCH])
        for row in d1(f"SELECT code_point,data,detail FROM characters WHERE code_point IN ({batch})"):
            found[row["code_point"]] = {"data": json.loads(row["data"]), "detail": json.loads(row["detail"])}
    return found


def rewritten(point: str, live: dict[str, dict[str, Any]], counts: dict[str, int]) -> tuple[dict, dict]:
    """The live row of one character with its family parts recomputed.

    `counts` are the live rows' crop counts. The visual analysis is keyed on the family head: a
    character that joins a family the analysis covers takes the head's, and any other the analysis
    of its new family, which the registry holds only for the families it was run on.
    """
    row = refs.character(point)
    data, detail = live[point]["data"], live[point]["detail"]
    info = characters._row(row, counts)
    members = [member["code_point"] for member in info["grapheme"]["members"]]
    candidates = dict(data.get("candidates") or {})
    if candidates.get("known"):
        member_candidates = [(live.get(cp) or {}).get("data", {}).get("candidates") or {} for cp in members]
        candidates["requires_family_scope"] = len(members) > 1 and any(
            set(found.get("sources") or []) & NORMALIZED_CORPORA for found in member_candidates)
        candidates["family_glyphs"] = sum(found.get("glyphs") or 0 for found in member_candidates)
    scope = "grapheme" if candidates.get("requires_family_scope") else info["default_scope"]
    data = {**data, "grapheme": info["grapheme"], "default_scope": scope, "candidates": candidates}
    detail = {**detail, **data,
              "characters": [characters.form_row(form, counts) for cp in characters._forms(point)
                             if (form := refs.character(cp))],
              "expansions": characters._expansions(row, counts, expand="none"),
              "visual_analysis": _visual_analysis(point, info["grapheme"]["code_point"], live)}
    return data, detail


def _visual_analysis(point: str, head: str, live: dict[str, dict[str, Any]]) -> dict:
    kept = ((live.get(head) or {}).get("detail") or {}).get("visual_analysis") or {}
    return kept if kept.get("family") == head else visual_families.family_analysis(point)


def statements(rows: dict[str, tuple[dict, dict]]) -> list[list[str]]:
    """The staging fill in statements under D1's statement limit, then the swap as the last group."""
    fill = [f"DROP TABLE IF EXISTS {STAGING};\n",
            (f"CREATE TABLE {STAGING} (code_point TEXT PRIMARY KEY, character TEXT NOT NULL, data TEXT NOT NULL,"
             " detail TEXT NOT NULL) WITHOUT ROWID;\n")]
    head = f"INSERT OR REPLACE INTO {STAGING}(code_point,character,data,detail) VALUES"
    values, size = [], 0
    for point, (data, detail) in rows.items():
        value = f"({quote(point)},{quote(refs.to_char(point))},{quote(encoded(data))},{quote(encoded(detail))})"
        if values and size + len(value.encode()) + 1 > STATEMENT_BYTES - len(head):
            fill.append(head + ",".join(values) + ";\n")
            values, size = [], 0
        values.append(value)
        size += len(value.encode()) + 1
    if values:
        fill.append(head + ",".join(values) + ";\n")
    family = "(SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character={table}.character)"
    moved = f"character IN (SELECT character FROM {STAGING})"
    swap = [
        f"UPDATE characters SET data=n.data,detail=n.detail FROM {STAGING} n WHERE characters.code_point=n.code_point;\n",
        (f"UPDATE units SET family={family.format(table='units')} WHERE {moved} "
         f"AND family IS NOT {family.format(table='units')};\n"),
        ("UPDATE units SET data=json_set(data,'$.grapheme',json_extract(c.data,'$.grapheme.code_point'),"
         "'$.family_members',json_extract(c.data,'$.grapheme.members')) FROM characters c "
         f"WHERE units.origin='corpus' AND c.character=units.character AND units.{moved} "
         "AND json_type(units.data,'$.grapheme') IS NOT NULL;\n"),
        (f"UPDATE corpus_units SET family={family.format(table='corpus_units')} WHERE {moved} "
         f"AND family IS NOT {family.format(table='corpus_units')};\n"),
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
# part is one D1 transaction; the parts fill a staging table and the last one swaps it in, so a
# failure leaves the site as it was. Safe to rerun.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd {wrangler}
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=$started"
published=$(q "SELECT value FROM metadata WHERE key='published_at'")
[ "$published" = '{published}' ] || {{ echo "the site was published again since this export read it ($published); export again" >&2; exit 1; }}
check="SELECT count(*) AS rows FROM characters WHERE json_extract(data,'$.grapheme.code_point')='{probe_head}'"
echo "before: $(q "$check")"
for part in "$here"/sql/part-*.sql; do
  done=0
  for try in 1 2 3 4; do
    if bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 | tee /dev/stderr | grep -q "Executed"; then done=1; break; fi
    sleep 30
  done
  [ "$done" -eq 1 ] || {{ echo "$(basename "$part") failed four times; the site is unchanged unless it was the last part. Rerun." >&2; exit 1; }}
done
after=$(q "$check")
echo "after: $after"
[ "$after" = '{{"rows":{probe_rows}}}' ] || {{ echo 'expected {{"rows":{probe_rows}}}' >&2; exit 1; }}
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
    stale = [point for point in live if {member["code_point"] for member in live[point]["data"]["grapheme"]["members"]}
             != previous.get(point, {point})]
    if stale:
        raise SystemExit(f"{len(stale)} live rows disagree with --previous, e.g. {stale[:5]}; "
                         "pass the table the site was published from")
    counts = {point: found["data"].get("occurrence_count", 0) for point, found in live.items()}
    rows = {point: rewritten(point, live, counts) for point in points if point in live}
    paths = write_parts(out, statements(rows))
    apply = out / "apply.sh"
    apply.write_text(APPLY.format(wrangler=WRANGLER, published=encoded(published), probe_head=head,
                                  probe_rows=sum(point in live for point in refs.graphemes()[head])),
                     encoding="utf-8")
    apply.chmod(0o755)
    print(json.dumps({"changed": len(points), "rows": len(rows), "absent_from_site": len(points) - len(rows),
                      "families": len({data["grapheme"]["code_point"] for data, _ in rows.values()}),
                      "parts": [str(path.relative_to(out)) for path in paths]}))


if __name__ == "__main__":
    main()
