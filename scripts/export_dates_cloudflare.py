"""Write the D1 SQL that publishes the documents' dates (`atlas dates`): ledger claims and what each document shows.

    uv run atlas dates --fetch --site SITE_DOCUMENTS.txt
    uv run scripts/export_dates_cloudflare.py work/dates OUTPUT
    OUTPUT/apply.sh

Each date is an assertion of the ledger (data/ledger.json, `date_<kind>`) with its source as evidence,
written once by its id; a date of an earlier export that no source states now is retracted by its
asserter, the source. The slots each touches are resolved again with the ledger's resolver. Then
`document_dating` is written with this export's stamp, the rows of earlier exports are removed, and
`dates_at` is stamped, which the Worker keys its cached date listings by. Every part repeats safely, so
a failed apply is rerun from the start.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from prepare_publication import write_parts

from glyph_atlas.date_claims import assertion
from glyph_atlas.dates import RESOLVER
from glyph_atlas.review import ledger
from glyph_atlas.schema import DateClaim

ASSERTION_COLUMNS = ("id", "subject", "predicate", "scope", "slot", "value", "tier", "asserted_by", "asserted_at",
                     "method", "run")
DATING_COLUMNS = ("document", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label",
                  "status", "claims", "calendar", "conversion", "source")
#: The ids this export states, kept while its parts run so the last one can retract the rest.
STAGING = "date_export"
METHOD = "atlas dates"
q = ledger.sql_literal


def literal(value) -> str:
    if isinstance(value, (dict, list)):
        return q(ledger.canonical(value))
    return q(value)


def _live(key: str, alias: str = "a") -> str:
    """SQL true when an assertion of claim `key` stands: `key` itself, or its re-assertion `key@<export>`."""
    return (f"({alias}.id={q(key)} OR ({alias}.id>{q(key + '@')} AND {alias}.id<{q(key + 'A')})) "
            f"AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion={alias}.id AND x.action='retract')")


def claim_statements(claims: list[DateClaim], stamp: str) -> tuple[list[str], list[tuple[str, str, str, str]]]:
    """Each claim's assertion and evidence rows, and the slots they fall in.

    A claim is its id. One that stands already is left as it is; one an earlier export retracted, and that
    a source states again, is asserted anew as `<id>@<export>`, since a retraction is never undone.
    """
    rows, keys = [], []
    for claim in claims:
        found = assertion(claim)
        key = found["id"]
        value = ledger.canonical(found["value"])
        values = {"subject": found["subject"], "predicate": found["predicate"], "scope": "", "slot": value,
                  "value": value, "tier": found["tier"], "asserted_by": found["asserted_by"], "asserted_at": stamp,
                  "method": METHOD, "run": RESOLVER}
        identity = (f"CASE WHEN EXISTS (SELECT 1 FROM assertions r WHERE r.id={q(key)}) THEN {q(key + '@' + stamp)} "
                    f"ELSE {q(key)} END")
        rows.append(f"INSERT OR IGNORE INTO assertions({','.join(ASSERTION_COLUMNS)}) "
                    f"SELECT {identity},{','.join(q(values[c]) for c in ASSERTION_COLUMNS[1:])} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM assertions a WHERE {_live(key)});\n")
        evidence = found["evidence"]
        rows.append("INSERT OR IGNORE INTO assertion_evidence(assertion,kind,ref,locator) "
                    f"SELECT a.id,{q(evidence['kind'])},{q(evidence['ref'])},{q(evidence['locator'])} "
                    f"FROM assertions a WHERE {_live(key)};\n")
        rows.append(f"INSERT OR IGNORE INTO {STAGING}(id) VALUES({q(key)});\n")
        keys.append((found["subject"], found["predicate"], "", value))
    return rows, keys


def retractions(stamp: str) -> list[str]:
    """Retract the dates an earlier export stated and this one does not, then resolve their slots."""
    mark = f"dt-retract:%:{stamp}"
    key = "CASE WHEN instr(a.id,'@')>0 THEN substr(a.id,1,instr(a.id,'@')-1) ELSE a.id END"
    retract = (
        "INSERT OR IGNORE INTO assertion_actions(id,submission,assertion,action,actor,at,reason) "
        f"SELECT 'dt-retract:'||a.id||':'||{q(stamp)},NULL,a.id,'retract',a.asserted_by,{q(stamp)},"
        f"{q('no source states it at export ' + stamp)} FROM assertions a "
        "WHERE substr(a.predicate,1,5)='date_' AND substr(a.asserted_by,1,7)='source:' "
        f"AND {key} NOT IN (SELECT id FROM {STAGING}) "
        "AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract');\n")
    retracted = ("(SELECT json_group_array(json_array(a.subject,a.predicate,a.scope,a.slot)) FROM assertions a "
                 f"JOIN assertion_actions x ON x.assertion=a.id WHERE x.id LIKE {q(mark)})")
    clear, write = ledger.resolve_statements(ledger.D1_CROP_NOW)
    return [retract] + [" ".join(line.strip() for line in s.splitlines()).replace("?1", retracted) + ";\n"
                        for s in (clear, write)]


def dating_statements(resolved: list[dict], stamp: str) -> list[str]:
    rows = []
    for document in resolved:
        for axis in ("witness", "composed"):
            if axis in document:
                found = {**document[axis], "document": document["document"]}
                rows.append(f"INSERT OR REPLACE INTO document_dating({','.join(DATING_COLUMNS)},resolver,export) "
                            f"VALUES({','.join(literal(found.get(c)) for c in DATING_COLUMNS)},{q(RESOLVER)},{q(stamp)});\n")
    return rows


def statements(source: Path, stamp: str) -> tuple[list[list[str]], dict[str, int]]:
    """The groups of statements that publish `source` (an `atlas dates` output), in order."""
    read = lambda name: [json.loads(line) for line in (source / name).read_text(encoding="utf-8").splitlines() if line]
    claims = [DateClaim.model_validate(c) for c in read("claims.jsonl")]
    resolved = read("resolved.jsonl")
    start = [f"CREATE TABLE IF NOT EXISTS {STAGING}(id TEXT PRIMARY KEY) WITHOUT ROWID;\n", f"DELETE FROM {STAGING};\n"]
    rows, keys = claim_statements(claims, stamp)
    resolve = ledger.d1_resolve_statements(sorted(set(keys)))
    dating = dating_statements(resolved, stamp)
    end = retractions(stamp) + [f"DROP TABLE {STAGING};\n",
                                f"DELETE FROM document_dating WHERE export<>{q(stamp)};\n",
                                f"INSERT OR REPLACE INTO metadata(key,value) VALUES('dates_at',{q(json.dumps(stamp))});\n"]
    return [start, rows, resolve, dating, end], {"claims": len(claims), "documents": len(resolved),
                                                 "dated_axes": len(dating), "slots": len(set(keys))}


APPLY = """#!/usr/bin/env bash
# Publish the documents' dates of export {stamp}. Each part is one D1 transaction and repeats safely;
# a failed run is rerun from the start. The ledger (0048) and document_dating (0052) must be on the site.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# The checkout the export sits in (under its work/), whichever one that is.
cd "$(git -C "$here" rev-parse --show-toplevel)/apps/cloudflare"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
tables=$(q "SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('assertions','document_dating')")
[ "$tables" = '{{"n":2}}' ] || {{ echo "apply migrations 0048 and 0052 first ($tables)" >&2; exit 1; }}
for part in "$here"/sql/part-*.sql; do
  done=0
  for try in 1 2 3 4; do
    if bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 | tee /dev/stderr | grep -q "Executed"; then done=1; break; fi
    sleep 30
  done
  [ "$done" -eq 1 ] || {{ echo "$(basename "$part") failed four times; rerun the apply." >&2; exit 1; }}
done
echo "now: $(q "SELECT (SELECT count(*) FROM assertions WHERE substr(predicate,1,5)='date_') AS claims, (SELECT count(*) FROM document_dating) AS axes, (SELECT value FROM metadata WHERE key='dates_at') AS stamp")"
echo "expected: {claims} claims or more (retracted ones stay), {axes} axes, stamp \\"{stamp}\\""
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="the directory `atlas dates` wrote")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    groups, counts = statements(args.source, stamp)
    args.output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(args.output, groups)
    apply = args.output / "apply.sh"
    apply.write_text(APPLY.format(stamp=stamp, claims=counts["claims"], axes=counts["dated_axes"]), encoding="utf-8")
    apply.chmod(0o755)
    print(json.dumps({**counts, "stamp": stamp, "parts": parts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
