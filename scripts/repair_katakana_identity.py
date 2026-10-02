"""Record the katakana a crop is written in where its identity says the hiragana of the same kana.

The aligner gave a transcribed voiced katakana such as バ the candidates of its kana, which list the
hiragana only, so the crop was identified as ば. The transcription (`text_source`) and the script
(`script = katakana`) say what was written, and so did the crop's reading, which is going away. This
writes the katakana as the crop's identity, one machine event a crop with its own evidence, so the
claim stands on its own and can be undone like any other.

A crop a person decided is never changed: one whose review state a person or a transcriber set, with
any event a person wrote, or with a reconciliation of a person's review. It is listed as a conflict
instead. The site goes first: `dataset --site` asks the site, read-only, how each published crop
stands, and repairs a published crop only once the site holds its katakana, so a review saved on the
site meanwhile keeps its crop on both sides.

    uv run python scripts/repair_katakana_identity.py d1 work/reading-purge/d1-katakana --dataset DIR
    work/reading-purge/d1-katakana/apply.sh
    uv run python scripts/repair_katakana_identity.py dataset DIR --site                  # report
    uv run python scripts/repair_katakana_identity.py dataset DIR --site --apply --conflicts conflicts.tsv

`d1` reads the site's crops read-only and writes SQL parts and their `apply.sh` into the directory:
each part sets a crop's character as the local repair does and moves its revision with it, guarded by
the revision and the character the site held when the parts were built, and only while nobody has
reviewed the crop on the site. A crop the site or the local store has seen decided is a conflict.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from glyph_atlas import refs
from glyph_atlas.review.store import Conflict, ReviewRequest, Store

POLICY = "katakana-identity-v1"
CLIENT = "katakana-identity-repair"
HIRAGANA = range(0x3041, 0x3097)
#: Review states a person or a transcriber sets.
DECIDED = frozenset({"transcriber", "reviewed", "double-reviewed", "adjudicated", "disputed"})
#: Model events that carry a person's review.
RECONCILED = '"kind": "feedback-reconciliation"' 
#: Events that record nothing about a crop's identity.
UNDECIDING = frozenset({"seen", "timing", "note", "written_form"})
PART_SIZE = 200


def katakana_of(hiragana: str) -> str:
    return chr(ord(hiragana) + 0x60)


def written_katakana(unicode: str | None, written: str | None, script: str | None) -> str | None:
    """The katakana a crop is written in, when its identity is the hiragana of that kana.

    `unicode` is one hiragana code point, `written` (the transcription, or the site's reading) the
    katakana of the same kana, and `script` katakana; anything else answers None.
    """
    points = (unicode or "").split()
    if len(points) != 1 or script != "katakana" or not written:
        return None
    try:
        char = chr(int(points[0].upper().removeprefix("U+"), 16))
    except ValueError:
        return None
    if ord(char) not in HIRAGANA:
        return None
    katakana = katakana_of(char)
    return katakana if unicodedata.normalize("NFC", written.strip()) == katakana else None


@dataclass
class Plan:
    repairs: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]


def decisions(store: Store) -> dict[str, list[str]]:
    """The fields a person decided, by crop."""
    decided: dict[str, list[str]] = {}
    for event in store.events():
        if (event.role != "model" and event.field not in UNDECIDING) or RECONCILED in (event.evidence or ""):
            decided.setdefault(event.target_id, []).append(event.field)
    return decided


def plan_dataset(store: Store, site: dict[str, dict[str, Any]] | None = None) -> Plan:
    """The crops of one store to repair, and the ones a person decided.

    `site` maps the published crops to their site rows; a published crop is repaired only once its
    site row holds the katakana at the revision the repair gives the local crop. A crop the site does
    not hold is repaired on the local decision alone.
    """
    decided = decisions(store)
    repairs, conflicts = [], []
    for unit, revision in store.unit_snapshot():
        if not unit.active:
            continue
        katakana = written_katakana(unit.unicode, unit.text_source, str(unit.script))
        if katakana is None:
            continue
        row = {"id": unit.id, "revision": revision, "unicode": unit.unicode,
               "katakana": katakana, "code_point": refs.to_code_point(katakana)}
        held = (site or {}).get(unit.id)
        if str(unit.review) in DECIDED or unit.id in decided:
            conflicts.append({**row, "reason": f"a person decided it: review {unit.review}, "
                                              f"events {','.join(sorted(set(decided.get(unit.id, [])))) or 'none'}"})
        elif held is not None and held.get("reviewed"):
            conflicts.append({**row, "reason": "a person reviewed it on the site; import that review instead"})
        elif held is not None and (held["character"] != katakana or int(held["revision"]) != revision + 1):
            conflicts.append({**row, "reason": f"the site holds {held['character']} at revision {held['revision']}"})
        else:
            repairs.append(row)
    return Plan(repairs, conflicts)


def apply_dataset(store: Store, repairs: list[dict[str, Any]], failed: list[dict[str, Any]] | None = None) -> int:
    """Record each repair as its own machine event; return how many were written.

    A crop that changed since the plan read it is skipped and added to `failed`.
    """
    failed = failed if failed is not None else []
    requests = [ReviewRequest(
        target_type="unit", target_id=row["id"], field="unicode", new=row["code_point"],
        base_revision=row["revision"], client_id=CLIENT, idempotency_key=f"katakana-identity:{row['id']}",
        evidence=json.dumps({"kind": "katakana-identity", "policy": POLICY, "automated": True,
                             "from": row["unicode"], "to": row["code_point"],
                             "basis": "the transcription and the script name the katakana of the same kana"},
                            ensure_ascii=False),
    ) for row in repairs]
    written = 0
    for request in requests:
        try:
            store.record_batch([request], role="model")
        except Conflict as error:
            failed.append({"id": request.target_id, "unicode": request.new, "katakana": request.new,
                           "reason": f"changed since it was read: {error}"})
            continue
        written += 1
    return written


def write_conflicts(path: Path, conflicts: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("id\tidentity\twritten\treason\n")
        for row in conflicts:
            handle.write(f"{row['id']}\t{row['unicode']}\t{row['katakana']}\t{row['reason']}\n")


# -- the site ------------------------------------------------------------------------------------

LIVE = ("SELECT id, character, reading, family, revision, state, json_extract(data,'$.script') AS script, "
        "EXISTS(SELECT 1 FROM events e WHERE e.target=units.id) AS reviewed FROM units "
        "WHERE origin='local' AND reading IS NOT NULL AND length(character)=1 "
        "AND unicode(character) BETWEEN 12353 AND 12438 AND reading=char(unicode(character)+96)")


def sql(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def plan_site(rows: list[dict[str, Any]], local: dict[str, tuple[str | None, int]],
              decided: set[str] = frozenset()) -> Plan:
    """The site's crops to repair and its conflicts.

    `local` maps a crop id to its identity and revision in the local store, which the site's row has
    to agree with: the same hiragana at the same revision (the local repair still to run), or the
    katakana one revision later (run). `decided` names the crops a person decided locally.
    """
    repairs, conflicts = [], []
    for row in rows:
        hiragana = row["character"]
        if row["reviewed"] or row["state"] != "pending":
            conflicts.append({"id": row["id"], "unicode": refs.to_code_point(hiragana), "katakana": row["reading"],
                              "reason": f"decided on the site: state {row['state']}"})
            continue
        if row["id"] in decided:
            conflicts.append({"id": row["id"], "unicode": refs.to_code_point(hiragana), "katakana": row["reading"],
                              "reason": "a person decided it locally"})
            continue
        katakana = written_katakana(refs.to_code_point(hiragana), row["reading"], row.get("script"))
        if katakana is None:
            conflicts.append({"id": row["id"], "unicode": refs.to_code_point(hiragana), "katakana": row["reading"],
                              "reason": f"the record's script is {row.get('script')}"})
            continue
        item = {"id": row["id"], "revision": int(row["revision"]), "unicode": refs.to_code_point(hiragana),
                "hiragana": hiragana, "katakana": katakana, "code_point": refs.to_code_point(katakana),
                "family": refs.grapheme(refs.to_code_point(katakana)) or refs.to_code_point(katakana)}
        held = local.get(row["id"])
        pending = held == (item["unicode"], item["revision"])
        done = held == (item["code_point"], item["revision"] + 1)
        if not (pending or done):
            conflicts.append({**item, "reason": f"the local store holds {held}"})
        else:
            repairs.append(item)
    return Plan(repairs, conflicts)


def statement(item: dict[str, Any]) -> str:
    """One crop's change on the site, the local repair's event as the trigger would apply it."""
    revision = item["revision"] + 1
    return (f"UPDATE units SET character={sql(item['katakana'])}, family={sql(item['family'])}, revision={revision}, "
            f"data=json_set(data,'$.label',{sql(item['katakana'])},'$.revision',{revision}), "
            f"snapshot=json_set(snapshot,'$.character.label',{sql(item['katakana'])},'$.character.revision',{revision}) "
            f"WHERE id={sql(item['id'])} AND origin='local' AND revision={item['revision']} "
            f"AND character={sql(item['hiragana'])} AND NOT EXISTS(SELECT 1 FROM events e WHERE e.target=units.id);")


VERSION_BUMP = ("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',"
                "json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));")

APPLY = """#!/usr/bin/env bash
# Record on the site the katakana {count} crops are written in, as scripts/repair_katakana_identity.py
# records it locally (built {built}). Each part changes a crop only at the revision and character
# the site held then, and only while nobody has reviewed it there, so a part is safe to run again.
# conflicts.tsv lists the crops left alone. Run it before ../d1-null/apply.sh, which clears the readings.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../../apps/cloudflare"
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "undo: (cd apps/cloudflare && bunx wrangler d1 time-travel restore glyph-atlas --timestamp=$now)"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" | jq -ce '.[0].results'; }}
left="SELECT count(*) AS n FROM units WHERE origin='local' AND reading IS NOT NULL AND length(character)=1 AND unicode(character) BETWEEN 12353 AND 12438 AND reading=char(unicode(character)+96)"
echo "hiragana identities with a katakana reading: $(q "$left")"
read -r -p "apply to the live database? [y/N] " answer
[ "$answer" = y ] || {{ echo "nothing applied"; exit 1; }}
for part in "$here"/parts/*.sql; do
  for try in 1 2 3 4; do
    out=$(bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 || true)
    echo "$out" | grep -q Executed && {{ echo "$(basename "$part") done"; break; }}
    [ "$try" -lt 4 ] || {{ echo "$(basename "$part") failed four times: $out" >&2; exit 1; }}
    sleep 30
  done
done
echo "now: $(q "$left") (expected {conflicts}, the crops in conflicts.tsv)"
"""


def write_site_parts(out: Path, plan: Plan, built: str) -> int:
    parts = out / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    for old in parts.glob("*.sql"):
        old.unlink()
    statements = [statement(item) for item in plan.repairs]
    number = 0
    for number, start in enumerate(range(0, len(statements), PART_SIZE), start=1):
        (parts / f"{number:03d}.sql").write_text("\n".join(statements[start:start + PART_SIZE]) + "\n", encoding="utf-8")
    (parts / f"{number + 1:03d}.sql").write_text(VERSION_BUMP + "\n", encoding="utf-8")
    (out / "apply.sh").write_text(APPLY.format(count=len(plan.repairs), conflicts=len(plan.conflicts), built=built), encoding="utf-8")
    (out / "apply.sh").chmod(0o755)
    write_conflicts(out / "conflicts.tsv", [{**row, "unicode": row["unicode"]} for row in plan.conflicts])
    return len(statements)


def live_rows() -> list[dict[str, Any]]:
    done = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", LIVE],
                          cwd=ROOT / "apps/cloudflare", capture_output=True, text=True, check=True)
    return json.loads(done.stdout)[0]["results"]


def local_index(directories: list[Path], ids: set[str]) -> tuple[dict[str, tuple[str | None, int]], set[str]]:
    held: dict[str, tuple[str | None, int]] = {}
    decided: set[str] = set()
    for directory in directories:
        store = Store(directory)
        people = decisions(store)
        for unit, revision in store.unit_snapshot():
            if unit.id in ids and unit.id not in held:
                held[unit.id] = (unit.unicode, revision)
                if str(unit.review) in DECIDED or unit.id in people:
                    decided.add(unit.id)
    return held, decided


def site_rows(ids: list[str]) -> dict[str, dict[str, Any]]:
    """The site's rows for these crops, read-only, a hundred ids a query."""
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        query = ("SELECT id, character, revision, EXISTS(SELECT 1 FROM events e WHERE e.target=units.id) AS reviewed "
                 "FROM units WHERE id IN ("
                 + ",".join(sql(ident) for ident in chunk) + ")")
        done = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", query],
                              cwd=ROOT / "apps/cloudflare", capture_output=True, text=True, check=True)
        found.update({row["id"]: row for row in json.loads(done.stdout)[0]["results"]})
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("dataset", help="repair the crops of local datasets")
    local.add_argument("directories", nargs="+", type=Path)
    local.add_argument("--apply", action="store_true", help="record the repairs; without it, only report")
    local.add_argument("--conflicts", type=Path, help="write the crops a person decided here")
    local.add_argument("--site", action="store_true",
                       help="repair a published crop only once the site holds its katakana (read-only query)")
    site = commands.add_parser("d1", help="write the site's SQL parts")
    site.add_argument("out", type=Path)
    site.add_argument("--dataset", type=Path, action="append", required=True,
                      help="the local datasets the site's crops were published from")
    arguments = parser.parse_args(argv)
    if arguments.command == "dataset":
        conflicts = []
        for directory in arguments.directories:
            store = Store(directory)
            site = site_rows([row["id"] for row in plan_dataset(store).repairs]) if arguments.site else None
            plan = plan_dataset(store, site)
            conflicts += plan.conflicts
            if arguments.apply:
                written = apply_dataset(store, plan.repairs, conflicts)
                print(f"{directory}: {written} crops repaired, {len(plan.conflicts)} left alone (see the conflicts)")
            else:
                print(f"{directory}: {len(plan.repairs)} crops to repair, {len(plan.conflicts)} left alone (see the conflicts)")
        if arguments.conflicts:
            write_conflicts(arguments.conflicts, conflicts)
        return 0
    rows = live_rows()
    held, decided = local_index(arguments.dataset, {row["id"] for row in rows})
    plan = plan_site(rows, held, decided)
    from datetime import UTC, datetime
    count = write_site_parts(arguments.out, plan, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print(f"{arguments.out}: {count} statements, {len(plan.conflicts)} conflicts of {len(rows)} live crops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
