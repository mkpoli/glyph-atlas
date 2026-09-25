"""The Ainu records' characters in one place: the atlas's units and ainu-records' occurrences, merged.

ainu-records published its own character pages: every occurrence on the pages of its twelve copies,
labelled from the transcription where its lines could be located and by OCR elsewhere, some of them
reviewed. The atlas holds far fewer, aligned only where it could box a line, and has had its own
review rounds. Neither is complete or right everywhere, so this module decides, occurrence by
occurrence, which one the atlas keeps. `plan` only reads and counts; nothing is written.

Units are read as the review store beside the atlas has them, so a person's latest decisions, splits
and merges included, are what the merge sees. The two are paired by ink: the same page, boxes
overlapping by at least `MIN_IOU`, one to one. A unit an earlier merge imported is paired with its
own occurrence by id instead. Then:

* a unit a person decided, or acted on in the review store, keeps its reading: that is a decision
  about this crop, compared by the identity the review gave it;
* a machine unit whose reading ainu-records confirms from its transcription or a review is kept, and
  the agreement lifts a withhold: two independent pairings naming the same character is the
  evidence the withhold was waiting for;
* a machine unit, or one a person flagged as wrong, that ainu-records reads differently from its
  transcription or a review gives way to ainu-records' occurrence: the atlas's machine pairing is
  where the neighbour's name ends up on the ink. A flagged unit ainu-records reads the same stays
  flagged, since the agreement is with the reading the person doubted;
* where ainu-records has only an OCR reading, a unit the atlas stands behind stays as it is, since its
  label comes from the transcription. One the atlas does not stand behind (withheld by the alignment
  repair, rejected by the aligner, or flagged) is confirmed by an OCR reading of the same character
  and gives way to one of another: its label is an unverified pairing, and the OCR reads the ink;
* where ainu-records has no reading, or one a person marked uncertain, the atlas unit stays;
* a machine unit on ink ainu-records rejected as not a character, or measured empty, is withheld,
  a unit an earlier merge imported included;
* an occurrence only ainu-records has is imported, with the source of its label;
* a unit only the atlas has stays.

An occurrence ainu-records rejected or measured empty is not imported. What the merge changes on a
unit the review log names is appended to the log as the merge's own event, so replaying the log over
the merged tables arrives at the same units.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import refs, tables
from .repair import REVIEWS_NAME, STORE_NAME, decode_event
from .review.store import SEEN
from .schema import Box, ReviewState, Script, Unit

MIN_IOU = 0.5
#: The review states a person gave and stands behind. A flag (`disputed`) says the crop is wrong.
DECIDED = {ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED, ReviewState.TRANSCRIBER}
#: Where an ainu-records label comes from, as its character page states it.
TRUSTED_ORIGINS = {"transcription", "review", "manual"}
UPSTREAM = "ainu-records"
META = "ainu_records"
#: What a merged dataset does not carry over from its source: the units it rewrites, the log it
#: writes from the store, the store itself (rebuilt from that log) and the files of running processes.
NOT_COPIED = {"units.parquet", REVIEWS_NAME, STORE_NAME, f"{STORE_NAME}-wal", f"{STORE_NAME}-shm"}


def normal(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def entries(records: Path) -> dict[str, str]:
    """`<source>/<witness>[-<part>]` → the みんなで翻刻 entry id, numbered as ainu-records numbers parts."""
    found: dict[str, str] = {}
    for source in yaml.safe_load((records / "data/sources.yaml").read_text(encoding="utf-8"))["sources"]:
        for witness in source.get("witnesses") or []:
            parts = witness.get("parts") or []
            for index, part in enumerate(parts, start=1):
                if part.get("entry"):
                    suffix = f"-{index}" if len(parts) > 1 else ""
                    found[f"{source['slug']}/{witness['slug']}{suffix}"] = part["entry"]
    return found


@dataclass
class Occurrence:
    """One occurrence of ainu-records' character pages, with the reading it publishes."""

    key: str
    sample: dict[str, Any]
    label: str
    origin: str  # transcription, ocr, manual, or review when a published review changed or confirmed it
    rejected: bool
    checked: bool = False  # a person confirmed both its reading and its box
    doubted: bool = False  # a person marked its reading or its box uncertain

    @property
    def id(self) -> str:
        return self.sample["id"]


def occurrences(records: Path) -> dict[str, list[Occurrence]]:
    """Every occurrence of every copy, with its published review applied."""
    found: dict[str, list[Occurrence]] = {}
    for folder in sorted((records / "data/characters").iterdir()):
        samples_path = folder / "samples.json"
        if not samples_path.is_file():
            continue
        unit = json.loads(samples_path.read_text(encoding="utf-8"))
        # Crops the page's own measurement found empty are not characters.
        empty_path = folder / "empty-exclusions.json"
        empty = ({r["id"] for r in json.loads(empty_path.read_text(encoding="utf-8")).get("records", [])}
                 if empty_path.is_file() else set())
        reviews_path = folder / "reviews.json"
        edits = ({e["id"]: e for e in json.loads(reviews_path.read_text(encoding="utf-8")).get("edits", [])}
                 if reviews_path.is_file() else {})
        rows = []
        for sample in unit["samples"]:
            edit = edits.get(sample["id"])
            label = normal(edit["label"] if edit else sample["proposed"])
            origin = sample["origin"]
            # As ainu-records' `labelSource` names it: a confirmed or changed reading is a review's.
            if origin != "manual" and edit and (edit.get("reading") == "confirmed"
                                                or normal(edit.get("label")) != normal(sample["proposed"])):
                origin = "review"
            rejected = bool(edit and ("rejected" in (edit.get("reading"), edit.get("boundary")))) or sample["id"] in empty
            checked = bool(edit and edit.get("reading") == "confirmed" and edit.get("boundary") == "confirmed")
            doubted = bool(edit and "uncertain" in (edit.get("reading"), edit.get("boundary")))
            box = edit["box"] if edit and edit.get("box") else sample["box"]
            rows.append(Occurrence(key=unit["key"], sample={**sample, "box": list(box)}, label=label, origin=origin,
                                   rejected=rejected, checked=checked, doubted=doubted))
        found[unit["key"]] = rows
    return found


def written(unit: Unit) -> str:
    """The character a unit is: the identity a review set, else its transcribed text."""
    if unit.review in DECIDED and unit.unicode:
        try:
            return normal(refs.from_code_points(unit.unicode.split()))
        except ValueError:
            pass
    return normal(unit.text_source)


def trusted(unit: Unit) -> bool:
    """Whether the atlas stands behind a unit's label: decided by a person, or aligned and not withheld."""
    if unit.review in DECIDED:
        return True
    repair = unit.meta.get("alignment_repair") or {}
    return unit.review not in (ReviewState.REJECTED, ReviewState.DISPUTED) and repair.get("withheld") is not True


def unit_id(row: Occurrence) -> str:
    return f"ar:{row.key.replace('/', '--')}:{row.id}"


@dataclass
class ReviewLog:
    """The review store beside a dataset, read read-only: every unit as it stands, and the events."""

    units: dict[str, Unit]
    events: list[dict[str, Any]] = field(default_factory=list)
    decided: set[str] = field(default_factory=set)  # units a person acted on
    logged: set[str] = field(default_factory=set)  # units any event names
    last_seq: int = 0  # the highest event number the store has handed out, erased ones included


def read_log(atlas: Path) -> ReviewLog:
    """Every unit as the store beside `atlas` has it; the tables alone where there is no store."""
    from .review.reset import _stamp

    path = atlas / STORE_NAME
    if not path.exists():
        return ReviewLog(units={u.id: u for u in tables.Dataset(atlas).read("units")})
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
        events = [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY seq")]
        units = {row["id"]: Unit.model_validate_json(row["data"]) for row in connection.execute("SELECT id, data FROM units")}
        issued = connection.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
    finally:
        connection.close()
    # A store loaded from other tables than those beside it holds units they no longer have.
    if meta.get("source_stamp") != _stamp(atlas):
        raise RuntimeError(f"the review store beside {atlas} was loaded from other tables; open it once "
                           "(`atlas review replay`) so that it reloads them")
    named = [e for e in events if e["target_type"] == "unit" and e["field"] != SEEN]
    last_seq = max([issued[0] if issued else 0, int(meta.get("reset_seq") or 0), *(e["seq"] for e in events)])
    return ReviewLog(units=units, events=events, decided={e["target_id"] for e in named if e["role"] != "model"},
                     logged={e["target_id"] for e in named}, last_seq=last_seq)


@dataclass
class Plan:
    keep_atlas: list[tuple[str, Occurrence]] = field(default_factory=list)  # atlas unit id, its ainu-records partner
    confirm: list[tuple[str, Occurrence]] = field(default_factory=list)  # kept, and the agreement lifts a withhold
    replace: list[tuple[str, Occurrence]] = field(default_factory=list)  # atlas unit id, the occurrence taking its place
    keep_withheld: list[tuple[str, Occurrence]] = field(default_factory=list)  # ainu-records has no reading to weigh against it
    withhold: list[tuple[str, Occurrence]] = field(default_factory=list)  # on ink ainu-records rejected
    import_new: list[Occurrence] = field(default_factory=list)
    imported_before: list[str] = field(default_factory=list)  # units an earlier merge imported
    atlas_only: list[str] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    agree: Counter = field(default_factory=Counter)

    def counts(self) -> dict[str, Any]:
        return {"atlas kept, decided by a person": len(self.keep_atlas),
                "atlas kept, confirmed by ainu-records": len(self.confirm),
                "atlas replaced by ainu-records": len(self.replace),
                "atlas kept, ainu-records has no reading to weigh": len(self.keep_withheld),
                "atlas withheld, ainu-records rejected the ink": len(self.withhold),
                "imported from ainu-records": len(self.import_new),
                "imported by label source": dict(Counter(o.origin for o in self.import_new)),
                "imported by an earlier merge": len(self.imported_before),
                "atlas only": len(self.atlas_only),
                "not imported": dict(self.skipped),
                "shared ink, labels agree": dict(self.agree)}


def plan(atlas: Path, records: Path, *, min_iou: float = MIN_IOU, log: ReviewLog | None = None) -> Plan:
    """Decide, for every occurrence and unit, what the merged atlas holds. Reads only."""
    log = log if log is not None else read_log(atlas)
    units = [u for u in log.units.values() if u.active and u.box and u.page_id]
    # An import a reviewer since retired still names its occurrence, which is not imported again.
    earlier = {u.upstream["id"]: u for u in log.units.values() if u.upstream.get("source") == UPSTREAM}
    by_page: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.upstream.get("source") != UPSTREAM:
            by_page[unit.page_id].append(unit)
    by_key = entries(records)
    result = Plan()
    matched_units: set[str] = set()

    def decided(unit: Unit) -> bool:
        return unit.review in DECIDED or (unit.id in log.decided and unit.review != ReviewState.DISPUTED)

    for key, rows in occurrences(records).items():
        entry = by_key.get(key)
        pages: dict[int, list[Occurrence]] = defaultdict(list)
        for row in rows:
            before = earlier.get(f"{key}#{row.id}")
            if before is not None:
                if row.rejected and before.active and not decided(before):
                    result.withhold.append((before.id, row))
                else:
                    result.imported_before.append(before.id)
                continue
            if not entry:
                result.skipped["no みんなで翻刻 entry"] += 1
                continue
            pages[row.sample["page"]].append(row)
        for n, page_rows in pages.items():
            atlas_units = by_page.get(f"hk:{entry}:{n - 1}", [])
            pairs = sorted(((iou(r.sample["box"], (u.box.x, u.box.y, u.box.w, u.box.h)), r.id, u.id, r, u)
                            for r in page_rows for u in atlas_units), key=lambda p: p[0], reverse=True)
            taken_r: set[str] = set()
            for score, rid, uid, row, unit in pairs:
                if score < min_iou:
                    break
                if rid in taken_r or uid in matched_units:
                    continue
                taken_r.add(rid)
                matched_units.add(uid)
                same = written(unit) == row.label
                if decided(unit):
                    result.keep_atlas.append((uid, row))
                    result.agree["decided, same" if same else "decided, different"] += 1
                elif row.rejected:
                    result.withhold.append((uid, row))
                elif not row.label or row.doubted or (row.origin not in TRUSTED_ORIGINS and trusted(unit)):
                    result.keep_withheld.append((uid, row))
                elif same:
                    (result.keep_atlas if unit.review == ReviewState.DISPUTED else result.confirm).append((uid, row))
                else:
                    result.replace.append((uid, row))
            for row in page_rows:
                if row.id in taken_r:
                    continue
                if row.rejected:
                    result.skipped["rejected or empty in ainu-records"] += 1
                else:
                    result.import_new.append(row)
    result.atlas_only = [u.id for u in units if u.id not in matched_units and u.upstream.get("source") != UPSTREAM]
    return result


# --- the merged dataset --------------------------------------------------------------------------


def provenance(row: Occurrence) -> dict[str, Any]:
    """What the character page needs to show an occurrence in its place: order, block and context."""
    s = row.sample
    return {"key": row.key, "id": row.id, "page": s["page"], "line": s.get("line"), "block": s.get("block"),
            "position": s.get("position"), "context": s.get("context"), "origin": row.origin,
            "revision": s.get("revision")}


def imported(row: Occurrence, entry: str, lines: set[str]) -> Unit:
    """An ainu-records occurrence as an atlas unit, with where its label came from."""
    s = row.sample
    page = s["page"] - 1
    line_id = f"hk:{entry}:{page}:L{s['line'] - 1}" if s.get("line") else None
    return Unit(
        id=unit_id(row), document_id=f"hk:{entry}", page_id=f"hk:{entry}:{page}",
        line_id=line_id if line_id in lines else None, seq=s.get("position"),
        box=Box(x=s["box"][0], y=s["box"][1], w=s["box"][2], h=s["box"][3]),
        text_source=row.label or None, reading=row.label or None,
        unicode=" ".join(refs.to_code_points(row.label)) if row.label else None,
        script=refs.script_of(row.label[:1]) if row.label else Script.UNKNOWN,
        method="import", review=ReviewState.REVIEWED if row.checked else ReviewState.MACHINE,
        upstream={"source": UPSTREAM, "id": f"{row.key}#{row.id}"},
        meta={META: provenance(row)},
    )


def copy_dataset(atlas: Path, out: Path) -> None:
    """Everything of the source but what the merge rewrites: tables, repair logs, receipts, shapes."""
    for path in atlas.iterdir():
        if path.name in NOT_COPIED or path.name.startswith(".") or path.suffix in (".lock", ".log"):
            continue
        target = out / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)


def build(result: Plan, atlas: Path, records: Path, out: Path, *, log: ReviewLog | None = None) -> dict[str, int]:
    """Write the merged dataset to `out`: the atlas's files, the merged units and the review log.

    The units are the store's, with the merge's changes. The log is the store's events, followed by one
    event of the merge for each unit the log names whose `meta` the merge changed, so that replaying
    the log over the merged tables arrives at the same units. The store is then rebuilt from them.
    """
    from .review import store as review_store

    log = log if log is not None else read_log(atlas)
    out.mkdir(parents=True, exist_ok=False)
    copy_dataset(atlas, out)
    lines = {line.id for line in tables.Dataset(atlas).read("lines")}
    by_key = entries(records)
    units = dict(log.units)
    counts: Counter = Counter()

    def note(uid: str, row: Occurrence, **meta: Any) -> None:
        unit = units[uid]
        units[uid] = unit.model_copy(update={"meta": {**unit.meta, **meta, META: provenance(row)}})

    for uid, row in result.keep_atlas + result.keep_withheld:
        note(uid, row)
        counts["atlas kept"] += 1
    for uid, row in result.confirm:
        source = "its OCR" if row.origin == "ocr" else "its transcription"
        repair = {**(units[uid].meta.get("alignment_repair") or {}), "status": "confirmed", "reliable": True,
                  "withheld": False, "quiz": True,
                  "reason": f"ainu-records reads this ink as the same character from {source}"}
        note(uid, row, alignment_repair=repair)
        counts["atlas confirmed"] += 1
    for uid, row in result.withhold:
        repair = {**(units[uid].meta.get("alignment_repair") or {}), "status": "withheld", "reliable": False,
                  "withheld": True, "quiz": False,
                  "reason": "ainu-records rejected this ink as a character, or found the crop empty"}
        note(uid, row, alignment_repair=repair)
        counts["atlas withheld"] += 1
    new: list[Unit] = []
    for uid, row in result.replace:
        replacement = imported(row, by_key[row.key], lines)
        unit = units[uid]
        units[uid] = unit.model_copy(update={"active": False, "meta": {**unit.meta, META: {
            "replaced_by": replacement.id, "reason": "ainu-records reads this ink as another character"}}})
        new.append(replacement)
        counts["atlas replaced"] += 1
    for row in result.import_new:
        new.append(imported(row, by_key[row.key], lines))
        counts["imported"] += 1
    clash = {u.id for u in new} & set(units)
    if clash:
        raise RuntimeError(f"{len(clash)} imported ids already exist, e.g. {min(clash)}")
    merged = list(units.values()) + new
    tables.write(out / "units.parquet", merged, Unit, command="atlas ainu merge")
    at = datetime.now(UTC).isoformat()
    rows = [decode_event(row) for row in log.events]
    seq = log.last_seq
    for uid in sorted(log.logged):
        if uid in units and units[uid].meta != log.units[uid].meta:
            seq += 1
            rows.append({"id": f"rv{seq:08d}", "target_type": "unit", "target_id": uid, "field": "meta",
                         "old": log.units[uid].meta, "new": units[uid].meta, "role": "model", "actor": "ainu-records-merge",
                         "evidence": json.dumps({"source": UPSTREAM}), "at": at})
            counts["merge events"] += 1
    with (out / REVIEWS_NAME).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    counts["units"] = len(merged)
    counts["active"] = sum(1 for u in merged if u.active)
    repaired = review_store.replay(out)["repaired"]
    if repaired:
        raise RuntimeError(f"replaying the merged log changed {repaired} units of the merged tables")
    # The log already holds every event; exporting marks it so, and writes the tables the store holds.
    review_store.Store(out).export()
    # Exporting rewrites the tables under the store; opening it again records the tables it now holds.
    review_store.Store(out)
    return dict(counts)
