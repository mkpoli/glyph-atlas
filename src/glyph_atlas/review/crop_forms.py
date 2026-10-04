"""The form a crop is written in, as the 字形 picker sets it (docs/design/form-model.md).

What a reviewer picks or types names a representation (`glyph_atlas.representation`) and that value's
broad form. The first choice of a value creates the form, together with the reviewer's claim that
the representation names it; the crop then gets a `has_form` claim on the evidence version the reviewer
saw. Clearing retracts the reviewer's own claim. The Worker's `cropForms.ts` does the same.

Every function runs inside its caller's transaction on the caller's connection.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .. import representation
from . import ledger
from .ledger import Claim, LedgerError

METHOD = "form-picker"


def ensure_form(conn: sqlite3.Connection, named: representation.Representation, *, key: str, actor: str, at: str,
                prefix: str = "lc", mint: Callable[[], str] | None = None) -> str:
    """The id of the broad form `named` is the encoded name of, created with its naming claim the first time."""
    form = representation.anchored_form(named)
    conn.execute("INSERT OR IGNORE INTO representations(id,scheme,value,namespace,version) VALUES(?,?,?,?,?)",
                 (named.id, named.scheme, named.value, named.namespace, named.version))
    if conn.execute("SELECT 1 FROM forms WHERE id=?", (form,)).fetchone() is None:
        conn.execute("INSERT INTO forms(id,anchor,created_by,created_at) VALUES(?,?,?,?)", (form, named.id, actor, at))
        ledger.write_claims(conn, key=f"{key}/represented_by", actor=actor, request={"form": form, "representation": named.id},
                            subject=form, predicate="represented_by", claims=[Claim(object=named.id)], tier="observed",
                            method=METHOD, objects={named.id: "representation"}, prefix=prefix, at=at, mint=mint)
    return form


def set_form(conn: sqlite3.Connection, *, key: str, actor: str, request: Mapping[str, Any], crop: str, crop_version: str,
             form: str | None, at: str | None = None, prefix: str = "lc", legacy: str | None = None,
             method: str = METHOD, version_of: Callable[[str], str | None] | None = None,
             mint: Callable[[], str] | None = None) -> dict:
    """Set (or with `form` None, clear) `actor`'s form for one crop, and return the submission.

    `crop_version` is the evidence version the reviewer saw, which the caller has checked is the crop's
    current one. The response names in `replaced` the value of the actor's own claim the save took
    back, or None. A repeat of `key` with the same request answers with the first response.
    """
    signature = ledger.canonical(request)
    saved = ledger.previous(conn, key, signature)
    if saved is not None:
        return saved
    at = at or ledger.now()
    own = conn.execute(
        "SELECT a.id, r.value FROM assertions a LEFT JOIN forms f ON f.id=a.object LEFT JOIN representations r ON r.id=f.anchor "
        "WHERE a.subject=? AND a.predicate='has_form' AND a.scope='' AND a.slot='' AND a.asserted_by=? "
        "AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') "
        "ORDER BY a.rowid DESC LIMIT 1", (crop, actor)).fetchone()
    if form is None:
        if own is None:
            raise LedgerError(409, "You have no form on this crop to clear.")
        ledger.act(conn, key=f"{key}/retract", actor=actor, request={"target": own[0], "action": "retract"}, target=own[0],
                   action="retract", reason="cleared", version_of=version_of or (lambda unit: crop_version if unit == crop else None),
                   prefix=prefix, at=at, mint=mint)
    else:
        try:
            named = representation.typed(form)
        except ValueError as error:
            raise LedgerError(422, str(error)) from error
        chosen = ensure_form(conn, named, key=key, actor=actor, at=at, prefix=prefix, mint=mint)
        ledger.write_claims(conn, key=f"{key}/has_form", actor=actor, request={"crop": crop, "form": chosen}, subject=crop,
                            predicate="has_form", claims=[Claim(object=chosen)], crop_version=crop_version,
                            method=method, legacy=legacy, objects={chosen: "form"}, prefix=prefix, at=at, mint=mint)
    response = {"submission": key, "subject": crop, "replaced": own[1] if own else None}
    conn.execute("INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)",
                 (key, actor, signature, ledger.canonical(response), at))
    return response


def _values(row: dict) -> list[dict]:
    """The values a slot holds: its current members, or, disputed, each standing claim's."""
    if row["members"]:
        return row["members"]
    if row["status"] == "disputed":
        return [member for claim in row["claims"] if claim["standing"] for member in claim["members"]]
    return []


def forms_for(conn: sqlite3.Connection, versions: Mapping[str, str | None]) -> dict[str, dict]:
    """Each crop's form as a page shows it, for crops given with their current evidence versions.

    A form claim made on another version of the crop does not hold. The answer names the status of the
    slot, the forms and states it holds now (all the competing ones when people disagree), each form's
    name, and the claims it rests on.
    """
    wanted = [unit for unit, version in versions.items() if version is not None]
    rows = []
    for start in range(0, len(wanted), 500):
        part = wanted[start:start + 500]
        rows += [ledger.current_row(row) for row in conn.execute(
            f"SELECT {','.join(ledger.COLUMNS)} FROM current_claims WHERE subject IN (SELECT value FROM json_each(?)) "
            "AND predicate='has_form' AND scope='' AND slot=''", (json.dumps(part),))]
    rows = [row for row in rows if row["crop_version"] == versions.get(row["subject"])]
    forms = sorted({value["object"] for row in rows for value in _values(row) if value.get("object")})
    names = {}
    for start in range(0, len(forms), 500):
        for identity, scheme, value in conn.execute(
                "SELECT f.id,r.scheme,r.value FROM forms f JOIN representations r ON r.id=f.anchor "
                "WHERE f.id IN (SELECT value FROM json_each(?))", (json.dumps(forms[start:start + 500]),)):
            names[identity] = {"scheme": scheme, "text": value}
    found = {}
    for row in rows:
        values = [{"form": v["object"], **names.get(v["object"], {"scheme": None, "text": None}), "confidence": v["confidence"]}
                  if v.get("object") else {"state": v["value"], "confidence": v["confidence"]} for v in _values(row)]
        # Who made the claims the slot holds now (each competing one's, when people disagree), so a
        # reader can be offered to clear their own.
        holding = [claim for claim in row["claims"] if (claim["standing"] if row["status"] == "disputed"
                   else any(member["assertion"] in row["supporting"] for member in claim["members"]))]
        found[row["subject"]] = {"status": row["status"], "values": values, "supporting": row["supporting"],
                                 "by": list(dict.fromkeys(claim["asserted_by"] for claim in holding))}
    return found


def with_forms(conn: sqlite3.Connection, items: Iterable[dict], version_of: Callable[[dict], str | None]) -> list[dict]:
    """`items` with the form each holds, `version_of` naming each item's current evidence version."""
    items = list(items)
    forms = forms_for(conn, {item["id"]: version_of(item) for item in items})
    return [{**item, "form": forms.get(item["id"])} for item in items]
