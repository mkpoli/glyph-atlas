"""The assertion ledger: attributed claims, the actions taken on them, and their resolution.

A claim names a subject, a predicate and an object (an entity id) or a typed value, with its tier, who
asserted it, its confidence and its evidence. An accept, a reject, a retraction or an adjudication is
a row of its own, and no row is ever changed. `data/ledger.json` holds the predicate catalogue that
every write is checked against, and the SQL resolver that turns a slot's claims into its
`current_claims` row; the Worker runs the same resolver over D1, so the two cannot disagree on what a
slot holds. The tables are made from the Worker's migrations (`0048_assertion_ledger.sql`,
`0050_forms.sql`), so the review store and D1 hold the same shape.

Every function here runs inside its caller's transaction on the caller's connection. `version_of`
gives a crop subject's current evidence version (`glyph_atlas.evidence`), and None for a subject that
is no crop: the caller knows the image a crop is cut from, the ledger does not.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CATALOGUE_PATH = ROOT / "data" / "ledger.json"
#: The Worker's migrations that make the ledger's tables and the forms it names (0048, 0050).
DDL_PATHS = tuple(ROOT / "apps" / "cloudflare" / "migrations" / name for name in ("0048_assertion_ledger.sql", "0050_forms.sql"))

CATALOGUE: dict[str, Any] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
RESOLVER: str = CATALOGUE["resolver"]
TIERS = frozenset(CATALOGUE["tiers"])
ACTIONS = frozenset(CATALOGUE["actions"])
PREDICATES: dict[str, dict[str, Any]] = CATALOGUE["predicates"]
#: The resolver after the caller's `crop_now`, its comment lines left out.
RESOLVE_BODY = "\n".join(line for line in CATALOGUE["resolve"] if not line.lstrip().startswith("--"))
#: `crop_now` from the versions a local caller binds as ?2, a JSON array of [unit, version].
LOCAL_CROP_NOW = "SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?2)"
#: `crop_now` from D1's own crops (`units.crop_version`, migration 0047), as the publication writes it.
D1_CROP_NOW = "SELECT id,crop_version FROM units WHERE id IN (SELECT json_extract(value,'$[0]') FROM json_each(?1))"
COLUMNS = ("subject", "predicate", "scope", "slot", "status", "object", "value", "members", "supporting", "claims",
           "crop_version", "resolver", "at")
#: How many slots one resolution statement names; a publication writes its keys into the statement.
#: The resolver compares each slot's claims with one another, so a statement is kept to a few slots.
KEYS_PER_STATEMENT = 50


class LedgerError(ValueError):
    """A write the ledger refuses. `status` is the HTTP status a service answers with."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def resolve_statements(crop_now: str) -> tuple[str, str]:
    """The two statements that resolve the slots bound as ?1: clear their rows, then write them."""
    # By key: the slots are rows of `current_claims`' primary key, each found by it.
    clear = ("DELETE FROM current_claims WHERE (subject,predicate,scope,slot) IN (SELECT json_extract(value,'$[0]'),"
             "json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?1))")
    write = (f"INSERT INTO current_claims({','.join(COLUMNS)}) WITH crop_now(unit,version) AS ({crop_now}),\n"
             + RESOLVE_BODY)
    return clear, write


def schema(conn: sqlite3.Connection) -> None:
    """Make the ledger's tables and the forms', as migrations 0048 and 0050 make them in D1."""
    for path in DDL_PATHS:
        conn.executescript(path.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: Any) -> str:
    """A request as the Worker's `canonical` writes it: keys sorted, no spaces."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Claim:
    """One member of a claim: an object or a value, and its confidence."""

    object: str | None = None
    value: Any = None
    confidence: float | None = None
    confidence_scheme: str | None = None


def slot_of(predicate: str, claim: Claim) -> str:
    """The slot a claim falls in: '' for a predicate with one value, else its object or value."""
    if PREDICATES[predicate]["cardinality"] == "one":
        return ""
    return claim.object if claim.object is not None else canonical(claim.value)


def check(predicate: str, claims: Sequence[Claim], *, objects: Mapping[str, str] | None = None) -> None:
    """Refuse claims the catalogue does not allow. `objects` gives the kind of each named entity."""
    spec = PREDICATES.get(predicate)
    if spec is None:
        raise LedgerError(422, f"Unknown predicate {predicate!r}.")
    if not claims:
        raise LedgerError(422, "A claim names a value.")
    if len(claims) > 1 and not (spec.get("alternatives") and spec["cardinality"] == "one"):
        raise LedgerError(422, f"{predicate} takes one value a claim.")
    seen = set()
    for claim in claims:
        if (claim.object is None) == (claim.value is None):
            raise LedgerError(422, "A claim names an object or a value.")
        if claim.object is not None:
            kind = (objects or {}).get(claim.object)
            if kind is None or kind not in spec["objects"]:
                raise LedgerError(422, f"{claim.object} cannot be the object of {predicate}.")
        elif claim.value not in spec.get("values", []):
            raise LedgerError(422, f"{claim.value!r} is not a value of {predicate}.")
        if (claim.confidence is None) != (claim.confidence_scheme is None):
            raise LedgerError(422, "A confidence names its scheme.")
        if claim.confidence is not None and not math.isfinite(claim.confidence):
            raise LedgerError(422, "A confidence is a number.")
        key = (claim.object, canonical(claim.value))
        if key in seen:
            raise LedgerError(422, "The alternatives of a claim differ.")
        seen.add(key)


def previous(conn: sqlite3.Connection, key: str, request: str) -> dict | None:
    """The response saved for submission `key`; LedgerError when it was saved with another request."""
    row = conn.execute("SELECT request,response FROM ledger_submissions WHERE id=?", (key,)).fetchone()
    if row is None:
        return None
    if row[0] != request:
        raise LedgerError(409, "This submission was already saved with different values.")
    return json.loads(row[1])


def _retracted(conn: sqlite3.Connection, assertion: str) -> bool:
    return conn.execute("SELECT 1 FROM assertion_actions WHERE assertion=? AND action='retract'",
                        (assertion,)).fetchone() is not None


def write_claims(conn: sqlite3.Connection, *, key: str, actor: str, request: Mapping[str, Any], subject: str,
                 predicate: str, claims: Sequence[Claim], scope: str = "", crop_version: str | None = None,
                 tier: str = "observed", method: str | None = None, run: str | None = None, legacy: str | None = None,
                 objects: Mapping[str, str] | None = None, prefix: str = "lc", at: str | None = None) -> dict:
    """Record one claim (several members make an alternative set) and resolve its slot.

    `key` is the submission's own key (its actor and the client's id); a repeat with the same request
    answers with the first response. A crop subject's claim names the evidence version it was made on,
    which the caller has checked is the crop's current one. A new claim in a slot that takes one value
    retracts the asserter's own earlier claims there in the same write. The response names the
    submission, the subject and the rows written, as the Worker's does.
    """
    signature = canonical(request)
    saved = previous(conn, key, signature)
    if saved is not None:
        return saved
    if tier not in TIERS:
        raise LedgerError(422, f"Unknown tier {tier!r}.")
    check(predicate, claims, objects=objects)
    spec = PREDICATES[predicate]
    if spec["subject"] == "crop" and not crop_version:
        raise LedgerError(409, "This crop has no image to make a claim about.")
    at = at or now()
    alternative = f"{prefix}:{uuid.uuid4()}" if len(claims) > 1 else None
    slots = {slot_of(predicate, claim) for claim in claims}
    retracted = []
    if spec["cardinality"] == "one":
        for (own,) in conn.execute("SELECT id FROM assertions WHERE subject=? AND predicate=? AND scope=? AND slot='' "
                                   "AND asserted_by=? ORDER BY rowid", (subject, predicate, scope, actor)).fetchall():
            if not _retracted(conn, own):
                retracted.append(own)
    ids = []
    for claim in claims:
        identity = f"{prefix}:{uuid.uuid4()}"
        conn.execute(
            "INSERT INTO assertions(id,submission,subject,predicate,scope,slot,object,value,alternative_set,tier,"
            "asserted_by,asserted_at,confidence,confidence_scheme,method,run,legacy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, key, subject, predicate, scope, slot_of(predicate, claim), claim.object,
             None if claim.value is None else canonical(claim.value), alternative, tier, actor, at,
             claim.confidence, claim.confidence_scheme, method, run, legacy))
        if crop_version:
            conn.execute("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES(?,'crop',?)", (identity, crop_version))
        ids.append(identity)
    for own in retracted:
        conn.execute("INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,?,?,?,?,?,?)",
                     (f"{prefix}:{uuid.uuid4()}", key, own, "retract", actor, at, "superseded by " + ids[0]))
    keys = [(subject, predicate, scope, slot) for slot in sorted(slots)]
    resolve(conn, keys, lambda unit: crop_version if unit == subject else None)
    response = {"submission": key, "subject": subject, "assertions": ids, "retracted": retracted}
    conn.execute("INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)",
                 (key, actor, signature, canonical(response), at))
    return response


def assertion(conn: sqlite3.Connection, identity: str) -> dict | None:
    row = conn.execute("SELECT * FROM assertions WHERE id=?", (identity,)).fetchone()
    if row is None:
        return None
    names = [c[0] for c in conn.execute("SELECT * FROM assertions LIMIT 0").description]
    return dict(zip(names, row, strict=True))


def act(conn: sqlite3.Connection, *, key: str, actor: str, request: Mapping[str, Any], target: str, action: str,
        version_of: Callable[[str], str | None], reason: str = "", owned: Iterable[str] = (),
        adjudicator: bool = False, prefix: str = "lc", at: str | None = None) -> dict:
    """Accept, reject, retract or adjudicate one claim, and resolve its slot.

    A retraction is the asserter's (`owned` names the actor's other journal ids); an accept or reject
    is anyone else's; an adjudication needs `adjudicator`. Retracting one member of an alternative set
    retracts the set.
    """
    signature = canonical(request)
    saved = previous(conn, key, signature)
    if saved is not None:
        return saved
    if action not in ACTIONS:
        raise LedgerError(422, f"Unknown action {action!r}.")
    found = assertion(conn, target)
    if found is None:
        raise LedgerError(404, "No such claim.")
    mine = found["asserted_by"] == actor or found["asserted_by"] in set(owned)
    if action == "retract" and not mine:
        raise LedgerError(403, "Only the person who made a claim can retract it.")
    if action in ("accept", "reject") and mine:
        raise LedgerError(422, "A claim is already its asserter's own; retract it instead.")
    if action == "adjudicate" and not adjudicator:
        raise LedgerError(403, "Only an adjudicator can decide between claims.")
    targets = [target]
    if found["alternative_set"] and action == "retract":
        targets = [i for (i,) in conn.execute("SELECT id FROM assertions WHERE alternative_set=? ORDER BY rowid",
                                              (found["alternative_set"],))]
    if action == "retract" and all(_retracted(conn, t) for t in targets):
        raise LedgerError(409, "This claim was already retracted.")
    at = at or now()
    ids = []
    for one in targets:
        if action == "retract" and _retracted(conn, one):
            continue
        identity = f"{prefix}:{uuid.uuid4()}"
        conn.execute("INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,?,?,?,?,?,?)",
                     (identity, key, one, action, actor, at, reason))
        ids.append(identity)
    keys = [(found["subject"], found["predicate"], found["scope"], found["slot"])]
    resolve(conn, keys, version_of)
    response = {"submission": key, "subject": found["subject"], "actions": ids}
    conn.execute("INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)",
                 (key, actor, signature, canonical(response), at))
    return response


def resolve(conn: sqlite3.Connection, keys: Iterable[Sequence[str]], version_of: Callable[[str], str | None]) -> None:
    """Write the `current_claims` rows of these slots from the ledger as it stands."""
    keys = [list(key) for key in dict.fromkeys(tuple(key) for key in keys)]
    clear, write = resolve_statements(LOCAL_CROP_NOW)
    for start in range(0, len(keys), KEYS_PER_STATEMENT):
        part = keys[start:start + KEYS_PER_STATEMENT]
        versions = canonical([[unit, version] for unit in dict.fromkeys(key[0] for key in part)
                              if (version := version_of(unit)) is not None])
        bound = canonical(part)
        conn.execute(clear, (bound,))
        conn.execute(write, (bound, versions))


def slots(conn: sqlite3.Connection, subjects: Iterable[str] | None = None) -> list[tuple[str, str, str, str]]:
    """Every slot the ledger holds a claim in, or those of `subjects`."""
    if subjects is None:
        return [tuple(r) for r in conn.execute("SELECT DISTINCT subject,predicate,scope,slot FROM assertions ORDER BY 1,2,3,4")]
    found = []
    for subject in dict.fromkeys(subjects):
        found += [tuple(r) for r in conn.execute("SELECT DISTINCT subject,predicate,scope,slot FROM assertions WHERE subject=?",
                                                 (subject,))]
    return found


def rebuild(conn: sqlite3.Connection, version_of: Callable[[str], str | None]) -> int:
    """Resolve every slot again, as a replay of the whole ledger; returns how many slots it names."""
    keys = slots(conn)
    conn.execute("DELETE FROM current_claims")
    resolve(conn, keys, version_of)
    return len(keys)


def rows_for(conn: sqlite3.Connection, keys: Iterable[Sequence[str]]) -> list[sqlite3.Row | tuple]:
    rows = []
    for key in keys:
        row = conn.execute(f"SELECT {','.join(COLUMNS)} FROM current_claims WHERE subject=? AND predicate=? AND scope=? AND slot=?",
                           tuple(key)).fetchone()
        if row is not None:
            rows.append(row)
    return rows


def current_row(row: Sequence[Any]) -> dict:
    """A `current_claims` row as a service answers with it, its JSON columns read."""
    item = dict(zip(COLUMNS, row, strict=True))
    for name in ("members", "supporting", "claims"):
        item[name] = json.loads(item[name])
    item["value"] = None if item["value"] is None else json.loads(item["value"])
    return item


def current(conn: sqlite3.Connection, subject: str, crop_version: str | None = None) -> list[dict]:
    """A subject's resolved slots. For a crop, only those resolved on `crop_version` still hold."""
    rows = conn.execute(f"SELECT {','.join(COLUMNS)} FROM current_claims WHERE subject=? ORDER BY predicate,scope,slot",
                        (subject,)).fetchall()
    return [current_row(row) for row in rows if row[COLUMNS.index("crop_version")] == crop_version]


HISTORY_LIMIT = 200


def history(conn: sqlite3.Connection, subject: str) -> list[dict]:
    """A subject's latest claims, oldest first, each with its evidence and the actions taken on it."""
    names = [c[0] for c in conn.execute("SELECT * FROM assertions LIMIT 0").description]
    out = []
    for row in conn.execute("SELECT * FROM (SELECT * FROM assertions WHERE subject=? ORDER BY asserted_at DESC,id DESC LIMIT ?) "
                            "ORDER BY asserted_at,id", (subject, HISTORY_LIMIT)):
        item = dict(zip(names, row, strict=True))
        item["value"] = None if item["value"] is None else json.loads(item["value"])
        item["evidence"] = [dict(zip(("kind", "ref", "locator"), e, strict=True)) for e in conn.execute(
            "SELECT kind,ref,locator FROM assertion_evidence WHERE assertion=? ORDER BY kind,ref", (item["id"],))]
        item["actions"] = [dict(zip(("id", "action", "actor", "at", "reason"), a, strict=True)) for a in conn.execute(
            "SELECT id,action,actor,at,reason FROM assertion_actions WHERE assertion=? ORDER BY at,id", (item["id"],))]
        out.append(item)
    return out


# -- publication ---------------------------------------------------------------------------------

#: The ledger's tables in the order a publication copies them, each claim before its rows.
TABLES = ("assertions", "assertion_evidence", "assertion_premises", "assertion_actions")


def copy_published(source: sqlite3.Connection, target: sqlite3.Connection,
                   keep: Callable[[str], bool] = lambda subject: True) -> list[tuple[str, str, str, str]]:
    """Copy the rows this store made into a publication's catalogue, and return the slots they touch.

    The site's own rows (`cf:`) are on the site already. An action made here on a claim made there
    takes that claim along, so the publication can name the slot it resolves again. A claim whose
    subject `keep` refuses (a crop of a withdrawn document) is left out, with its actions.
    """
    names = {table: [c[1] for c in source.execute(f"PRAGMA table_info({table})")] for table in TABLES}
    columns = names["assertion_actions"]
    actions = source.execute(f"SELECT {','.join(columns)} FROM assertion_actions WHERE substr(id,1,3)<>'cf:' ORDER BY rowid").fetchall()
    wanted = [row[0] for row in source.execute("SELECT id FROM assertions WHERE substr(id,1,3)<>'cf:' ORDER BY rowid")]
    own = set(wanted)
    wanted += sorted({row[columns.index("assertion")] for row in actions} - own)
    subjects = {}
    for start in range(0, len(wanted), 500):
        subjects.update(source.execute("SELECT id,subject FROM assertions WHERE id IN (SELECT value FROM json_each(?))",
                                       (canonical(wanted[start:start + 500]),)).fetchall())
    wanted = [identity for identity in wanted if identity in subjects and keep(subjects[identity])]
    kept = set(wanted)
    for start in range(0, len(wanted), 500):
        part = canonical(wanted[start:start + 500])
        for table, column in (("assertions", "id"), ("assertion_evidence", "assertion"), ("assertion_premises", "assertion")):
            rows = source.execute(f"SELECT {','.join(names[table])} FROM {table} WHERE {column} IN (SELECT value FROM json_each(?))",
                                  (part,)).fetchall()
            target.executemany(f"INSERT OR IGNORE INTO {table}({','.join(names[table])}) VALUES({','.join('?' * len(names[table]))})",
                               rows)
    target.executemany(f"INSERT OR IGNORE INTO assertion_actions({','.join(columns)}) VALUES({','.join('?' * len(columns))})",
                       [row for row in actions if row[columns.index("assertion")] in kept])
    keys = set()
    for start in range(0, len(wanted), 500):
        keys.update(tuple(r) for r in source.execute(
            "SELECT DISTINCT subject,predicate,scope,slot FROM assertions WHERE id IN (SELECT value FROM json_each(?))",
            (canonical(wanted[start:start + 500]),)))
    return sorted(keys)


def sql_literal(value: Any) -> str:
    """A value as one line of SQL: a newline or carriage return inside text is written as char(10) or
    char(13), so a publication's one-statement-a-line files keep each statement whole."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int | float):
        return repr(value)
    text = "'" + str(value).replace("'", "''") + "'"
    return text.replace("\r", "'||char(13)||'").replace("\n", "'||char(10)||'")


def insert_statements(conn: sqlite3.Connection, table: str) -> list[str]:
    """Every row of one of the ledger's tables as an `INSERT OR IGNORE`, one line each."""
    names = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
    return [f"INSERT OR IGNORE INTO {table}({','.join(names)}) VALUES({','.join(sql_literal(v) for v in row)});\n"
            for row in conn.execute(f"SELECT {','.join(names)} FROM {table} ORDER BY rowid" if table in ("assertions", "assertion_actions")
                                    else f"SELECT {','.join(names)} FROM {table}")]


def d1_resolve_statements(keys: Sequence[Sequence[str]]) -> list[str]:
    """One-line SQL that resolves these slots in D1 after a publication's rows, with the keys written in."""
    clear, write = resolve_statements(D1_CROP_NOW)
    out = []
    for start in range(0, len(keys), KEYS_PER_STATEMENT):
        bound = sql_literal(canonical([list(key) for key in keys[start:start + KEYS_PER_STATEMENT]]))
        for statement in (clear, write):
            # One line, as the publication's SQL files hold one statement a line.
            out.append(" ".join(line.strip() for line in statement.splitlines()).replace("?1", bound) + ";\n")
    return out
