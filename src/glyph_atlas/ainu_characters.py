"""The Ainu records' characters in one place: the atlas's units and ainu-records' occurrences, merged.

ainu-records published its own character pages: every occurrence on the pages of its twelve copies,
labelled from the transcription where its lines could be located and by OCR elsewhere, some of them
reviewed. The atlas holds far fewer, aligned only where it could box a line, and has had its own
review rounds. Neither is complete or right everywhere, so this module decides, occurrence by
occurrence, which one the atlas keeps. `plan` only reads and counts; nothing is written.

The two are paired by ink: the same page, boxes overlapping by at least `MIN_IOU`, one to one. Then:

* an atlas unit a person reviewed and did not flag is kept: that is a decision about this crop,
  compared by the identity the review gave it rather than the transcribed text;
* a machine unit whose reading ainu-records confirms from its transcription or a review is kept, and
  the agreement lifts a withhold: two independent pairings naming the same character is the
  evidence the withhold was waiting for;
* a machine unit, or one a person flagged as wrong, that ainu-records reads differently from its
  transcription or a review gives way to ainu-records' occurrence: the atlas's machine pairing is
  where the neighbour's name ends up on the ink;
* where ainu-records has only an OCR reading, the atlas unit stays as it is, since its label comes
  from the transcription;
* an occurrence only ainu-records has is imported, with the source of its label;
* a unit only the atlas has stays.

An occurrence ainu-records rejected as not a character, or an empty crop, is not imported.
"""
from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import refs, tables
from .schema import ReviewState, Script, Unit

MIN_IOU = 0.5
#: The review states a person gave and stands behind. A flag (`disputed`) says the crop is wrong.
DECIDED = {ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED, ReviewState.TRANSCRIBER}
#: Where an ainu-records label comes from, as its character page states it.
TRUSTED_ORIGINS = {"transcription", "review", "manual"}


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
    """`<source>/<witness>[-<part>]` → the みんなで翻刻 entry id, as ainu-records names its units."""
    found: dict[str, str] = {}
    for source in yaml.safe_load((records / "data/sources.yaml").read_text(encoding="utf-8"))["sources"]:
        for witness in source.get("witnesses") or []:
            parts = [p for p in witness.get("parts") or [] if p.get("entry")]
            if witness.get("entry"):
                parts = [witness]
            for index, part in enumerate(parts, start=1):
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
            if edit and (edit.get("reading") == "confirmed" or normal(edit.get("label")) != normal(sample["proposed"])):
                origin = "review"
            rejected = bool(edit and ("rejected" in (edit.get("reading"), edit.get("boundary")))) or sample["id"] in empty
            box = edit["box"] if edit and edit.get("box") else sample["box"]
            rows.append(Occurrence(key=unit["key"], sample={**sample, "box": list(box)}, label=label, origin=origin,
                                   rejected=rejected))
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


@dataclass
class Plan:
    keep_atlas: list[tuple[str, Occurrence]] = field(default_factory=list)  # atlas unit id, its ainu-records partner
    confirm: list[tuple[str, Occurrence]] = field(default_factory=list)  # kept, and the agreement lifts a withhold
    replace: list[tuple[str, Occurrence]] = field(default_factory=list)  # atlas unit id, the occurrence taking its place
    keep_withheld: list[tuple[str, Occurrence]] = field(default_factory=list)
    import_new: list[Occurrence] = field(default_factory=list)
    atlas_only: list[str] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    agree: Counter = field(default_factory=Counter)

    def counts(self) -> dict[str, Any]:
        return {"atlas kept, reviewed by a person": len(self.keep_atlas),
                "atlas kept, confirmed by ainu-records": len(self.confirm),
                "atlas replaced by ainu-records": len(self.replace),
                "atlas kept, ainu-records has only OCR": len(self.keep_withheld),
                "imported from ainu-records": len(self.import_new),
                "imported by label source": dict(Counter(o.origin for o in self.import_new)),
                "atlas only": len(self.atlas_only),
                "not imported": dict(self.skipped),
                "shared ink, labels agree": dict(self.agree)}


def plan(atlas: Path, records: Path, *, min_iou: float = MIN_IOU) -> Plan:
    """Decide, for every occurrence and unit, what the merged atlas holds. Reads only."""
    units = [u for u in tables.read(atlas / "units.parquet", Unit) if u.active and u.box and u.page_id]
    by_page: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        by_page[unit.page_id].append(unit)
    by_key = entries(records)
    result = Plan()
    matched_units: set[str] = set()
    for key, rows in occurrences(records).items():
        entry = by_key.get(key)
        pages: dict[int, list[Occurrence]] = defaultdict(list)
        for row in rows:
            if row.rejected:
                result.skipped["rejected or empty in ainu-records"] += 1
                continue
            pages[row.sample["page"]].append(row)
        for n, page_rows in pages.items():
            atlas_units = by_page.get(f"hk:{entry}:{n - 1}", []) if entry else []
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
                if unit.review in DECIDED:
                    result.keep_atlas.append((uid, row))
                    result.agree["reviewed, same" if same else "reviewed, different"] += 1
                elif row.origin not in TRUSTED_ORIGINS:
                    result.keep_withheld.append((uid, row))
                elif same:
                    result.confirm.append((uid, row))
                else:
                    result.replace.append((uid, row))
            for row in page_rows:
                if row.id not in taken_r:
                    result.import_new.append(row)
    result.atlas_only = [u.id for u in units if u.id not in matched_units]
    return result


# --- the merged dataset --------------------------------------------------------------------------

UPSTREAM = "ainu-records"
META = "ainu_records"


def unit_id(row: Occurrence) -> str:
    return f"ar:{row.key.replace('/', '--')}:{row.id}"


def provenance(row: Occurrence) -> dict[str, Any]:
    """What the character page needs to show an occurrence in its place: order, block and context."""
    s = row.sample
    return {"key": row.key, "id": row.id, "page": s["page"], "line": s.get("line"), "block": s.get("block"),
            "position": s.get("position"), "context": s.get("context"), "origin": row.origin,
            "revision": s.get("revision")}


def imported(row: Occurrence, entry: str, lines: set[str]) -> Unit:
    """An ainu-records occurrence as an atlas unit, with where its label came from."""
    from .schema import Box

    s = row.sample
    page = s["page"] - 1
    line_id = f"hk:{entry}:{page}:L{s['line'] - 1}" if s.get("line") else None
    reviewed = row.origin == "review"
    return Unit(
        id=unit_id(row), document_id=f"hk:{entry}", page_id=f"hk:{entry}:{page}",
        line_id=line_id if line_id in lines else None, seq=s.get("position"),
        box=Box(x=s["box"][0], y=s["box"][1], w=s["box"][2], h=s["box"][3]),
        text_source=row.label or None, reading=row.label or None,
        unicode=" ".join(refs.to_code_points(row.label)) if row.label else None,
        script=refs.script_of(row.label[:1]) if row.label else Script.UNKNOWN,
        method="import", review=ReviewState.REVIEWED if reviewed else ReviewState.MACHINE,
        upstream={"source": UPSTREAM, "id": f"{row.key}#{row.id}"},
        meta={META: provenance(row)},
    )


def build(result: Plan, atlas: Path, records: Path, out: Path) -> dict[str, int]:
    """Write the merged dataset to `out`: the atlas's tables, its review log, and the merged units."""
    import shutil

    from .schema import Line

    out.mkdir(parents=True, exist_ok=False)
    for name in ("documents", "pages", "page_texts", "lines"):
        source = atlas / f"{name}.parquet"
        if source.exists():
            shutil.copy2(source, out / source.name)
    if (atlas / "reviews.jsonl").exists():
        shutil.copy2(atlas / "reviews.jsonl", out / "reviews.jsonl")
    lines = {line.id for line in tables.read(atlas / "lines.parquet", Line)}
    by_key = entries(records)
    units = {u.id: u for u in tables.read(atlas / "units.parquet", Unit)}
    counts: Counter = Counter()
    for uid, row in result.keep_atlas + result.keep_withheld:
        units[uid] = units[uid].model_copy(update={"meta": {**units[uid].meta, META: provenance(row)}})
        counts["atlas kept"] += 1
    for uid, row in result.confirm:
        unit = units[uid]
        repair = {**(unit.meta.get("alignment_repair") or {}), "status": "confirmed", "reliable": True,
                  "withheld": False, "quiz": True,
                  "reason": "ainu-records reads this ink as the same character from its transcription"}
        units[uid] = unit.model_copy(update={"meta": {**unit.meta, "alignment_repair": repair, META: provenance(row)}})
        counts["atlas confirmed"] += 1
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
    merged = list(units.values()) + new
    tables.write(out / "units.parquet", merged, Unit, command="ainu characters merge")
    counts["units"] = len(merged)
    counts["active"] = sum(1 for u in merged if u.active)
    return dict(counts)
