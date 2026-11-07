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

from .. import refs, representation
from . import ledger
from .ledger import Claim, LedgerError

METHOD = "form-picker"
#: The statuses of a form's `written_for` slot that file it on a grapheme's card.
FILED = ("asserted", "accepted", "adjudicated")


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


def _head_of(grapheme: str) -> str:
    """`grapheme` when it heads its family; the head's text when it is a member, and no such character otherwise."""
    head = refs.grapheme(grapheme)
    if head is None and grapheme not in refs.graphemes():
        raise LedgerError(422, "No such character.")
    head = head or grapheme
    if head != grapheme:
        raise LedgerError(422, f"This character is filed under {refs.from_code_points(head.split())}.")
    return head


def written_for(conn: sqlite3.Connection, form: str) -> list[dict]:
    """The graphemes a form is written for, each with who says so now: one row each. A slot people
    disagree on holds no grapheme until it is adjudicated."""
    rows = conn.execute("SELECT object,status,members,claims FROM current_claims "
                        "WHERE subject=? AND predicate='written_for' AND scope=''", (form,)).fetchall()
    found = []
    for row in rows:
        if row["status"] not in FILED:
            continue
        by = sorted({claim["asserted_by"] for claim in json.loads(row["claims"]) if claim["standing"]})
        for grapheme in sorted({member["object"] for member in json.loads(row["members"]) if member.get("object")}):
            found.append({"grapheme": grapheme, "char": refs.from_code_points(grapheme.split()), "by": by})
    return found


def written_forms(conn: sqlite3.Connection, grapheme: str) -> list[dict]:
    """The described forms a grapheme's card carries from reviewers' claims, by their value.

    A form a reviewer writes as an IDS has no character of its own, so no card row holds it; the claim
    that it is written for the grapheme is how it joins the grapheme's 字体. The Worker counts its crops;
    the review service leaves the count at zero rather than walking every unit."""
    rows = conn.execute(
        "SELECT c.subject AS form,r.scheme,r.value FROM current_claims c "
        "JOIN forms f ON f.id=c.subject JOIN representations r ON r.id=f.anchor "
        "WHERE c.predicate='written_for' AND c.object=? AND c.scope='' AND r.scheme='ids' "
        "AND c.status IN ('asserted','accepted','adjudicated') ORDER BY r.value", (grapheme,)).fetchall()
    return [{"char": row["value"], "code_point": None, "form": row["form"], "scheme": row["scheme"],
             "count": 0, "corpus_count": 0} for row in rows]


def set_written_for(conn: sqlite3.Connection, *, key: str, actor: str, request: Mapping[str, Any], form: str, grapheme: str,
                    remove: bool = False, at: str | None = None, prefix: str = "lc",
                    mint: Callable[[], str] | None = None) -> dict:
    """Say that a form is written for a grapheme (`remove` false), or take back the reader's own claim.

    A form can be written for several graphemes; each is a claim of its own, and removing one leaves the
    others. A retry answers with the first response. The claim is not about a crop, so it names no
    evidence version and no recut can take it back.
    """
    signature = ledger.canonical(request)
    saved = ledger.previous(conn, key, signature)
    if saved is not None:
        return saved
    at = at or ledger.now()
    if conn.execute("SELECT 1 FROM forms WHERE id=?", (form,)).fetchone() is None:
        raise LedgerError(404, "No such form.")
    _head_of(grapheme)
    own = conn.execute(
        "SELECT a.id FROM assertions a WHERE a.subject=? AND a.predicate='written_for' AND a.scope='' AND a.slot=? "
        "AND a.asserted_by=? AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') "
        "ORDER BY a.rowid DESC LIMIT 1", (form, grapheme, actor)).fetchone()
    linked = any(value["grapheme"] == grapheme for value in written_for(conn, form))
    if remove and own is None:
        raise LedgerError(409, f"You have not said this form writes {refs.from_code_points(grapheme.split())}.")
    # A claim already standing is not made again; a picker that lost the answer is told what holds.
    if not remove and own is not None and linked:
        response = {"submission": key, "subject": form}
        conn.execute("INSERT OR IGNORE INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)",
                     (key, actor, signature, ledger.canonical(response), at))
        return response
    if own is not None:
        ledger.act(conn, key=f"{key}/retract", actor=actor, request={"target": own[0], "action": "retract"}, target=own[0],
                   action="retract", reason="cleared" if remove else "replaced", version_of=lambda _unit: None,
                   prefix=prefix, at=at, mint=mint)
    if not remove:
        ledger.write_claims(conn, key=key, actor=actor, request=request, subject=form, predicate="written_for",
                            claims=[Claim(object=grapheme)], objects={grapheme: "grapheme"}, method=METHOD,
                            prefix=prefix, at=at, mint=mint)
        return {"submission": key, "subject": form}
    # The retraction is its own submission; this one answers the request and names the form.
    response = {"submission": key, "subject": form}
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
