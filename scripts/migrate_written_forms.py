"""Move written forms, the layer migration 0038 added, into the assertion ledger as form claims.

    uv run scripts/migrate_written_forms.py d1 OUTPUT
    uv run scripts/migrate_written_forms.py dataset DATASET [--corpus-index DIR] [--apply]

`d1` reads the site's `written_forms` journal (one read-only query) and writes the SQL that records its
claims in D1 under OUTPUT/sql, D1-sized parts ending with the stamp the Worker keys its cached listings
on. `dataset` reads a review store's `written_form` events, and the corpus reviews' `glyph_forms` with
`--corpus-index`, and records their claims in the store's own ledger; without `--apply` it reports
what it would write and writes nothing.

Each journal row is one reviewer's save, replayed in the order they were made:

- a form: a claim that the crop is written in that value's form, which is created with its
  representation the first time a value is chosen;
- no form, where the request the row keeps typed the crop's own character: the layer stored that as
  no form, and it was a confirmation, so it is a claim of the character's own form;
- no form, where the request cleared the field: the reviewer's earlier claim on the crop is retracted;
- no form and a request that says neither: left as it is, and counted.

A claim rests on the crop version its reviewer saw: the crop, the pixels the row names, and the box
when it can be shown to be the one they saw (the crop still has those pixels, and on the site only
one version with them). Where it cannot, the box is written `?`, which no version has, so the claim is
kept and stands on nothing until someone looks at the crop again. The rows' ids derive from the
journal row, so applying the output twice, or running a dataset twice, adds nothing.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from prepare_publication import write_parts
from refresh_published_units import VERSION_BUMP

from glyph_atlas.review import crop_forms, ledger
from glyph_atlas.review.atlas import identity_text
from glyph_atlas.review.ledger import LedgerError

ROOT = Path(__file__).resolve().parents[1]
METHOD = "written-form-layer"
#: The site's journal, oldest first, with the version each row's crop has now when the row's pixels
#: are its only ones (0047), which is then the version the reviewer saw.
D1_JOURNAL = """SELECT w.id,w.target,w.actor,w.pixels,w.label,w.form,w.request,w.at,
  CASE WHEN coalesce(json_extract(u.data,'$.image_sha256'),json_extract(u.data,'$.source_revision'))=w.pixels
    AND (SELECT count(*) FROM crop_versions v WHERE v.unit=w.target AND v.pixels=w.pixels)=1 THEN u.crop_version END AS seen
  FROM written_forms w LEFT JOIN units u ON u.id=w.target ORDER BY w.rowid"""


def chosen(row: dict) -> tuple[str, str | None]:
    """What one journal row said: ("form", value), ("clear", None) or ("unknown", None).

    `row` has the stored `form`, the crop's `label` then, and `typed`: the form its request typed,
    None when the request typed none, or the `...` Ellipsis when the request is lost.
    """
    if row["form"]:
        return "form", row["form"]
    typed = row["typed"]
    if typed is Ellipsis:
        return "unknown", None
    if typed is None or not str(typed).strip():
        return "clear", None
    value = identity_text(str(typed))
    return ("form", value) if value == row["label"] else ("unknown", None)


def replay(conn: sqlite3.Connection, rows: Iterable[dict]) -> tuple[Counter, list[dict]]:
    """Record each journal row's claim in the ledger on `conn`, oldest first: what each became, and
    the rows the ledger refused with the reason.

    A row has `id`, `target`, `actor`, `at`, `label`, `form`, `typed`, `version` (the crop version its
    reviewer saw, with a `?` box where that is not known) and `legacy`. The slots are resolved as if
    that version were current; the caller resolves them again against the crops as they are.
    """
    counts: Counter = Counter()
    refused = []
    for row in rows:
        kind, value = chosen(row)
        if kind == "unknown":
            counts["unknown"] += 1
            continue
        serial = itertools.count(1)
        mint: Callable[[], str] = lambda serial=serial, row=row: f"mg:{row['id']}:{next(serial)}"
        try:
            crop_forms.set_form(conn, key=f"mg:{row['id']}", actor=row["actor"], request={"legacy": row["id"]},
                                crop=row["target"], crop_version=row["version"], form=value, at=row["at"], prefix="mg",
                                legacy=row["legacy"], method=METHOD, version_of=lambda unit: None, mint=mint)
        except LedgerError as error:
            # A clear with nothing of the reviewer's to retract, or a value the forms refuse.
            counts[f"{kind}-refused"] += 1
            refused.append({"row": row["id"], "reason": str(error)})
            continue
        counts["confirmation" if kind == "form" and not row["form"] else kind] += 1
    return counts, refused


def _typed(request: dict | None, path: tuple[str, ...]):
    """The form a saved request typed, None for none, or Ellipsis when the request does not say."""
    found = request
    for key in path:
        if not isinstance(found, dict) or key not in found:
            return Ellipsis
        found = found[key]
    return found


def d1(sql: str) -> list[dict]:
    result = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                            cwd=ROOT / "apps" / "cloudflare", capture_output=True, text=True, check=True)
    return json.loads(result.stdout)[0]["results"]


def site_rows(journal: list[dict]) -> list[dict]:
    """The site's journal rows as `replay` reads them."""
    rows = []
    for row in journal:
        try:
            request = json.loads(row["request"])
        except (TypeError, ValueError):
            request = None
        rows.append({"id": row["id"], "target": row["target"], "actor": row["actor"], "at": row["at"],
                     "label": row["label"], "form": row["form"], "typed": _typed(request, ("input", "form")),
                     "version": row["seen"] or f"{row['target']}@{row['pixels']}@?", "legacy": f"written_forms:{row['id']}"})
    return rows


def statements(conn: sqlite3.Connection) -> list[str]:
    """The ledger rows `replay` wrote, as D1 statements, and the resolution of their slots."""
    out = [statement for table in ("representations", "forms", *ledger.TABLES) for statement in ledger.insert_statements(conn, table)]
    return out + ledger.d1_resolve_statements(ledger.slots(conn))


def migrate_d1(output: Path) -> dict:
    journal = d1(" ".join(D1_JOURNAL.split()))
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE units (id TEXT PRIMARY KEY)")
    ledger.schema(conn)
    counts, refused = replay(conn, site_rows(journal))
    found = statements(conn)
    output.mkdir(parents=True, exist_ok=True)
    parts = write_parts(output, [found, [VERSION_BUMP]]) if found else []
    return {"journal": len(journal), **counts, "refused": refused, "statements": len(found), "parts": parts}


def dataset_rows(store, corpus=None) -> list[dict]:
    """A review store's written-form events, and with `corpus` its corpus reviews' `glyph_forms`, oldest first."""
    from glyph_atlas import evidence
    from glyph_atlas.review.characters import _source_digest
    from glyph_atlas.review.store import WRITTEN_FORM

    rows = []
    events = store.events()
    later = {}
    for event in events:
        if event.field in ("box", "segmentation", "create"):
            later[event.target_id] = event.at
    for event in events:
        if event.field != WRITTEN_FORM:
            continue
        try:
            stated = json.loads(event.evidence or "{}")
        except ValueError:
            stated = {}
        request = stated.get("request") if isinstance(stated, dict) else None
        pixels = request.get("image_sha256") if isinstance(request, dict) else None
        unit = store.unit(event.target_id)
        # The box the reviewer saw is the crop's own while nothing has cut it again since.
        version = None
        if unit is not None and pixels and _source_digest(store, unit) == pixels and later.get(unit.id, event.at) <= event.at:
            version = evidence.crop_version(unit.id, pixels, unit.box)
        rows.append({"id": event.id, "target": event.target_id, "actor": event.actor or "unknown", "at": event.at.isoformat(),
                     "label": stated.get("label") if isinstance(stated, dict) else None, "form": event.new,
                     "typed": _typed(request, ("form",)), "version": version or f"{event.target_id}@{pixels}@?",
                     "legacy": f"events:{event.id}"})
    if corpus is not None:
        path = Path(corpus.api.directory) / "reviews.sqlite"
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='glyph_forms'").fetchone():
                for identity, glyph, actor, request, revision, label, form, at in db.execute(
                        "SELECT id,identity,client_id,request,source_revision,label,form,at FROM glyph_forms ORDER BY seq"):
                    try:
                        source = corpus.source(glyph)
                    except Exception:  # noqa: BLE001 — a glyph no longer published keeps the unknown box
                        source = None
                    current = evidence.record_version(source) if source and source.get("source_revision") == revision else None
                    rows.append({"id": identity, "target": glyph, "actor": actor, "at": at, "label": label, "form": form,
                                 "typed": _typed(json.loads(request), ("form",)), "version": current or f"{glyph}@{revision}@?",
                                 "legacy": f"glyph_forms:{identity}"})
    return sorted(rows, key=lambda row: row["at"])


def migrate_dataset(directory: Path, corpus_index: Path | None, apply: bool) -> dict:
    from glyph_atlas.review.store import Store

    store = Store(directory)
    corpus = None
    if corpus_index is not None:
        from glyph_atlas.corpus.api import CorpusAPI
        from glyph_atlas.review.corpus_reviews import CorpusReviews
        corpus = CorpusReviews(CorpusAPI(str(corpus_index.parent), str(corpus_index), autobuild=False))
    rows = dataset_rows(store, corpus)

    class Preview(Exception):
        pass

    from glyph_atlas import evidence
    from glyph_atlas.review.characters import _source_digest

    def version_of(subject: str) -> str | None:
        unit = store.unit(subject)
        if unit is not None:
            return evidence.crop_version(unit.id, _source_digest(store, unit), unit.box) if unit.active else None
        try:
            return evidence.record_version(corpus.source(subject)) if corpus is not None else None
        except Exception:  # noqa: BLE001 — a glyph no longer published has no version
            return None

    counts, refused = Counter(), []
    try:
        with store._lock, store._connection() as conn, store._transaction(conn):
            counts, refused = replay(conn, rows)
            # Each slot as the crops stand now: a claim on a version the crop no longer has does not hold.
            ledger.resolve(conn, ledger.slots(conn, {row["target"] for row in rows}), version_of)
            if not apply:
                raise Preview
    except Preview:
        pass
    return {"journal": len(rows), **counts, "refused": refused, "applied": apply}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    site = commands.add_parser("d1")
    site.add_argument("output", type=Path)
    local = commands.add_parser("dataset")
    local.add_argument("dataset", type=Path)
    local.add_argument("--corpus-index", type=Path)
    local.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = migrate_d1(args.output) if args.command == "d1" else migrate_dataset(args.dataset, args.corpus_index, args.apply)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
