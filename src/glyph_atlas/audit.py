"""Draw a blind sample of accepted units and publish the precision it shows.

An audit sample is the evidence behind the precision a release states, so the module is built around
two rules.

The first rule is that the sample is drawn at random with a recorded design. Within each stratum the
draw is simple random without replacement, and every sampled unit carries the probability with which
it entered the sample, because the strata differ in size and the estimate has to weight a small
stratum up rather than let a large one drown it. The draw is reproducible from the sample id: the
seed fixes it, and a rerun against the same tables selects the same units.

The second rule is that an audit sample is not a training set. A unit in an audit sample is reviewed
blind, with the machine's confidence, reading, code point and 字母 hidden, and nothing in the project
may tune a threshold, a cost or a model on it. `sample` leaves out every unit an earlier sample
already holds, and `report` names the sample the units came from.

What the review produces is the truth the sample is scored against. `atlas audit report` reads the
units table, which `atlas review apply` has written, and matches each sampled unit's original
prediction (kept in `meta.audit`) against the unit as the reviewer left it: box precision at IoU 0.5,
label precision over the units whose box held, and the joint figure, each weighted by the inverse of
its inclusion probability, with a Wilson interval per stratum and a cluster bootstrap over pages for
the overall figure, because units on one page are not independent draws. The published figure covers
the units a reviewer has decided on; the whole sample is reported beside it as a progress figure,
because a unit no event has touched stands in for itself and therefore reads as right.

A sample is stored twice, for two readers. The units table carries the sample id, the inclusion
probability and the prediction of every sampled unit in `meta.audit`, so the review service and its
interface can find the queue and can hide the machine's answer; and `<directory>/audit/<sample>.json`
holds the same rows with the design, the seed, the strata and the rule, so that a report can be
reproduced and checked without the tables. The sample id is `<run or all>-s<seed>-n<size>`, or the
name the caller gives.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import tables
from .evaluate import iou, wilson
from .schema import ReviewState, Unit

AUDIT_DIR = "audit"
AUDIT_INDEX = "audit/index.json"
DESIGN_FIELDS = ("document", "script", "kind")
HIDDEN_FIELDS = ("confidence", "reading", "unicode", "jibo", "candidates")
LABEL_IOU = 0.5
BOOTSTRAP_SAMPLES = 2000


class AuditError(RuntimeError):
    """A sample that cannot be drawn or read."""


class SampledUnit(BaseModel):
    """One unit of a sample, with the prediction it was drawn for."""

    id: str
    stratum: str
    document_id: str | None = None
    page_id: str | None = None
    script: str | None = None
    kind: str | None = None
    p: float = Field(description="probability this unit entered the sample")
    frame: int = Field(default=0, description="units of its stratum when the sample was drawn")
    n_stratum: int = Field(description="units of its stratum this sample could still draw from")
    n_sample: int = Field(description="units drawn from its stratum")
    predicted: dict[str, Any] = Field(
        default_factory=dict, description="what the pipeline said, hidden while reviewing"
    )


class Sample(BaseModel):
    """A sample as it is stored: the design, and the units it holds."""

    id: str
    run: str | None = None
    seed: int = 0
    n: int = 0
    strata: list[str] = Field(default_factory=list)
    population: int = 0
    population_pages: int = 0
    drawn: int = 0
    rule: str = Field(
        default=(
            "Simple random sampling without replacement within each stratum, applied to the units "
            "the pipeline accepted and no earlier audit sample holds. A unit's inclusion "
            "probability is n_stratum / n_population_of_stratum."
        )
    )
    hidden: list[str] = Field(default_factory=list)
    units: list[SampledUnit] = Field(default_factory=list)
    source: str | None = Field(
        default=None, description="MANIFEST.json command of the dataset the sample was drawn from"
    )
    drawn_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def sample(
    directory: Path,
    *,
    n: int,
    strata: Sequence[str] = ("document", "script"),
    seed: int = 0,
    run: str | None = None,
    out: Path | None = None,
    sample_id: str | None = None,
    replace: bool = False,
) -> dict[str, int]:
    """Draw an audit sample of the accepted units and store it under `<directory>/audit/`.

    The population is every active unit of the dataset whose `review` is `machine` — accepted by the
    pipeline and untouched by a reviewer — optionally restricted to one run, and never a unit that an
    earlier sample already holds. The sample id names the run, the seed and the size, so a rerun
    either reproduces the same file or fails on the existing one unless `replace` is set.
    """
    directory = Path(directory)
    unknown = [name for name in strata if name not in DESIGN_FIELDS]
    if unknown:
        raise AuditError(f"unknown stratum {', '.join(unknown)}; expected {', '.join(DESIGN_FIELDS)}")
    if n < 1:
        raise AuditError("a sample needs at least one unit")
    if run is not None and ":" in run:
        raise AuditError("a run name cannot contain ':'")

    dataset = tables.Dataset(directory)
    if dataset.tables["units"] is None:
        raise AuditError(f"{directory} has no units table")
    units = [unit for batch in dataset.scan("units") for unit in batch]
    frame_units = [unit for unit in units if _in_population(unit, run)]
    population = list(frame_units)
    if replace:
        # A redraw drops its own earlier units and keeps every other sample's.
        population = [
            unit for unit in population if _sample_of(unit) in (None, _id_of(run, seed, n, sample_id))
        ]
    else:
        population = [unit for unit in population if _sample_of(unit) is None]

    # The design population is the stratum before an earlier sample took units out of the frame:
    # the inclusion probability a unit records belongs to the sample's design, and a unit an earlier
    # sample holds was in that design and cannot also be in this one.
    frame: dict[str, list[Unit]] = defaultdict(list)
    for unit in frame_units:
        frame[_stratum_of(unit, strata)].append(unit)
    grouped: dict[str, list[Unit]] = defaultdict(list)
    for unit in population:
        grouped[_stratum_of(unit, strata)].append(unit)
    rng = random.Random(seed)
    key = _id_of(run, seed, n, sample_id)
    drawn: list[SampledUnit] = []
    for name in sorted(grouped):
        members = sorted(grouped[name], key=lambda unit: unit.id)
        take = _allocation(len(members), len(population), n)
        design = len(frame.get(name, members))
        for unit in rng.sample(members, take):
            drawn.append(
                SampledUnit(
                    id=unit.id,
                    stratum=name,
                    document_id=unit.document_id,
                    page_id=unit.page_id,
                    script=unit.script.value,
                    kind=unit.kind.value,
                    p=take / design,
                    frame=design,
                    n_stratum=len(members),
                    n_sample=take,
                    predicted=_prediction_of(unit),
                )
            )
    drawn.sort(key=lambda row: row.id)

    record = Sample(
        id=key,
        run=run,
        seed=seed,
        n=n,
        strata=list(strata),
        population=len(population),
        population_pages=len({unit.page_id for unit in population if unit.page_id}),
        drawn=len(drawn),
        hidden=list(HIDDEN_FIELDS),
        units=drawn,
        source=_manifest_command(directory),
    )
    target = Path(out) if out is not None else directory / AUDIT_DIR / f"{key}.json"
    if target.exists() and not replace:
        raise AuditError(f"{target} exists; pass replace=True to redraw it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _tag_units(directory, record)
    _index(directory, record, target)
    return {
        "population": record.population,
        "population_pages": record.population_pages,
        "strata": len(grouped),
        "drawn": record.drawn,
    }


def load(directory: Path, sample_id: str) -> Sample:
    """Read a stored sample by id, from `<directory>/audit/<id>.json`."""
    directory = Path(directory)
    target = directory / AUDIT_DIR / f"{sample_id}.json"
    if not target.exists():
        known = [path.stem for path in sorted((directory / AUDIT_DIR).glob("*.json")) if path.stem != "index"]
        raise AuditError(
            f"no sample {sample_id} under {directory / AUDIT_DIR}; there are {', '.join(known) or 'none'}"
        )
    return Sample.model_validate_json(target.read_text(encoding="utf-8"))


def samples(directory: Path) -> list[str]:
    """The ids of the samples stored under a dataset directory."""
    index = Path(directory) / AUDIT_INDEX
    if not index.exists():
        return []
    return [entry["id"] for entry in json.loads(index.read_text(encoding="utf-8")).get("samples", [])]


def report(directory: Path, *, sample: str, out: Path | None = None, notice: str | None = None) -> str:
    """Score a stored sample against the units as they now stand and return Markdown.

    A sampled unit is scored on the unit the tables hold now: `box` right when the reviewer's box
    overlaps the prediction at IoU 0.5 or more, `label` right when the code point agrees, and `joint`
    right when both do. A unit the review retired (split or merged away) counts as wrong on both. A
    unit no event has touched carries the prediction itself, which is what makes the report readable
    before the review is finished.

    `notice` is a sentence, or a file holding one, written under the title before anything else: a
    report from a run that is not a measurement says so at the top rather than in a footnote.
    """
    directory = Path(directory)
    record = load(directory, sample)
    dataset = tables.Dataset(directory)
    if dataset.tables["units"] is None:
        raise AuditError(f"{directory} has no units table")
    current = {unit.id: unit for batch in dataset.scan("units") for unit in batch}
    reviewed = _reviewed_units(directory)
    rows = [_score(row, current.get(row.id), reviewed) for row in record.units]
    text = notice
    if text and Path(text).is_file():
        text = Path(text).read_text(encoding="utf-8").strip()
    return _markdown(record, rows, out=out, notice=text)


def intervals(successes: Sequence[int], totals: Sequence[int]) -> tuple[float, float]:
    """Wilson interval of the pooled proportion, for callers that hold counts rather than units."""
    return wilson(sum(successes), sum(totals))


def _allocation(size: int, population: int, n: int) -> int:
    """How many units to draw from a stratum of `size` out of `population` when drawing `n`."""
    if population and size and n:
        share = round(n * size / population)
    else:
        share = 0
    return max(1, min(size, share))


def _in_population(unit: Unit, run: str | None) -> bool:
    if not unit.active or unit.review is not ReviewState.MACHINE:
        return False
    if run is None:
        return True
    upstream = unit.upstream or {}
    return run in {upstream.get("run"), upstream.get("source")} or unit.id.split(":")[-2:-1] == [run]


def _sample_of(unit: Unit) -> str | None:
    audit = (unit.meta or {}).get("audit")
    return audit.get("sample") if isinstance(audit, dict) else None


def _stratum_of(unit: Unit, strata: Sequence[str]) -> str:
    parts = []
    for name in strata:
        if name == "document":
            parts.append(unit.document_id or "-")
        elif name == "script":
            parts.append(unit.script.value)
        elif name == "kind":
            parts.append(unit.kind.value)
    return "|".join(parts) or "all"


def _prediction_of(unit: Unit) -> dict[str, Any]:
    """What the pipeline said about a unit, kept for the report and hidden while reviewing."""
    return {
        "box": unit.box.model_dump(mode="json") if unit.box else None,
        "reading": unit.reading,
        "unicode": unit.unicode,
        "jibo": unit.jibo,
        "classification": unit.classification.value,
        "review": unit.review.value,
        "confidence": unit.confidence.model_dump(mode="json") if unit.confidence else None,
    }


def _id_of(run: str | None, seed: int, n: int, sample_id: str | None) -> str:
    if sample_id:
        return sample_id
    return f"{run or 'all'}-s{seed}-n{n}"


def _manifest_command(directory: Path) -> str | None:
    manifest = directory / tables.MANIFEST_NAME
    if not manifest.exists():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get("command")


def _tag_units(directory: Path, record: Sample) -> None:
    """Mark every sampled unit with its sample in `meta.audit` and write the units table back."""
    from .schema import Unit as UnitModel

    dataset = tables.Dataset(directory)
    path = dataset.tables["units"]
    if path is None:
        raise AuditError(f"{directory} has no units table")
    wanted = {row.id: row for row in record.units}
    updated = []
    for batch in dataset.scan("units"):
        for unit in batch:
            row = wanted.get(unit.id)
            if row is not None:
                meta = dict(unit.meta or {})
                meta["audit"] = {
                    "sample": record.id,
                    "p": row.p,
                    "stratum": row.stratum,
                    "predicted": row.predicted,
                    "hidden": record.hidden,
                }
                unit.meta = meta
                unit.review = ReviewState.MACHINE
            updated.append(unit)
    tables.write(path, updated, UnitModel)
    # A dataset directory carries MANIFEST.json; a bare table written by a test or a scratch run
    # does not, and the sample is still valid without it.
    manifest = directory / tables.MANIFEST_NAME
    if manifest.exists():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        counts = payload.get("tables") or {}
        if "units" in counts:
            counts["units"] = len(updated)
        payload["tables"] = counts
        payload.setdefault("audit", {})[record.id] = {
            "population": record.population,
            "drawn": record.drawn,
            "seed": record.seed,
        }
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _index(directory: Path, record: Sample, target: Path) -> None:
    index = directory / AUDIT_INDEX
    payload = {"samples": []}
    if index.exists():
        payload = json.loads(index.read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for entry in payload.get("samples", [])}
    entries[record.id] = {
        "id": record.id,
        "run": record.run,
        "seed": record.seed,
        "n": record.n,
        "drawn": record.drawn,
        "population": record.population,
        "strata": record.strata,
        "drawn_at": record.drawn_at.isoformat(),
        "file": str(target.relative_to(directory)),
    }
    payload["samples"] = [entries[key] for key in sorted(entries)]
    index.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reviewed_units(directory: Path) -> dict[str, dict[str, Any]]:
    """The latest value a review set for each field of each unit, from `reviews.jsonl`."""
    path = Path(directory) / "reviews.jsonl"
    latest: dict[str, dict[str, Any]] = defaultdict(dict)
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("target_type") != "unit":
            continue
        field = event.get("field")
        if field in {None, "timing", "note"}:
            continue
        latest[event["target_id"]][field] = event.get("new")
    return latest


def _score(row: SampledUnit, unit: Unit | None, reviewed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    predicted = row.predicted or {}
    weight = 1.0 / row.p if row.p else 0.0
    if unit is None or not unit.active:
        return {
            "id": row.id,
            "stratum": row.stratum,
            "page": row.page_id,
            "weight": weight,
            "n_stratum": row.n_stratum,
            "frame": row.frame,
            "box": 0,
            "label": 0,
            "joint": 0,
            "state": "retired",
        }
    changes = reviewed.get(row.id, {})
    label = changes.get("unicode", unit.unicode)
    box = changes.get("box", predicted.get("box"))
    predicted_box = _box_of(predicted.get("box"))
    current_box = _box_of(box)
    box_ok = bool(predicted_box and current_box and iou(predicted_box, current_box) >= LABEL_IOU)
    label_ok = bool(label) and label == predicted.get("unicode")
    state = "reviewed" if changes else "untouched"
    return {
        "id": row.id,
        "stratum": row.stratum,
        "page": row.page_id,
        "weight": weight,
        "n_stratum": row.n_stratum,
        "frame": row.frame,
        "box": int(box_ok),
        "label": int(label_ok) if box_ok else 0,
        "joint": int(box_ok and label_ok),
        "state": state,
    }


def _box_of(value: Any) -> Any:
    from .schema import Box

    if isinstance(value, dict):
        return Box.model_validate(value)
    return None


def _markdown(
    record: Sample, rows: list[dict[str, Any]], *, out: Path | None = None, notice: str | None = None
) -> str:
    lines = [
        f"# Audit {record.id}",
        "",
    ]
    if notice:
        lines += [f"> {notice}", ""]
    lines += [
        (
            f"Sample `{record.id}` of the run `{record.run or 'any'}`: {record.drawn} units drawn from "
            f"{record.population} accepted units on {record.population_pages} pages, seed {record.seed}, "
            f"strata {', '.join(record.strata)}."
        ),
        "",
        f"The draw is {record.rule[0].lower()}{record.rule[1:]}",
        "",
        (
            "A sampled unit is scored against the unit the tables hold now: box right at IoU 0.5 or "
            "more with the prediction, label right when the code point agrees, joint when both do; a "
            "unit the review retired counts as wrong. Rates are weighted by the inverse of each "
            "unit's inclusion probability."
        ),
        "",
        (
            f"Reviewed {sum(1 for row in rows if row['state'] == 'reviewed')} of {len(rows)} sampled "
            f"units; {sum(1 for row in rows if row['state'] == 'untouched')} untouched, "
            f"{sum(1 for row in rows if row['state'] == 'retired')} retired."
        ),
        "",
        "## The audited units",
        "",
        "| measure | value | Wilson 95% | cluster bootstrap 95% | n |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    reviewed_rows = [row for row in rows if row["state"] == "reviewed"]
    for name in ("box", "label", "joint"):
        rate, low, high = _weighted(reviewed_rows, name)
        count = sum(row[name] for row in reviewed_rows)
        boot_low, boot_high = _bootstrap(reviewed_rows, name, seed=record.seed)
        lines.append(
            f"| {name} precision | {rate:.4f} | {low:.4f} to {high:.4f} | "
            f"{boot_low:.4f} to {boot_high:.4f} | {count} |"
        )
    lines += [
        "",
        (
            "This is the published figure: it covers the units a reviewer has decided on. The table "
            "below it covers the whole sample, where an untouched unit stands in for itself and "
            "therefore reads as right; it is a progress figure, not a measurement."
        ),
        "",
        "## The whole sample",
        "",
        "| measure | value | Wilson 95% | cluster bootstrap 95% | n |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for name in ("box", "label", "joint"):
        rate, low, high = _weighted(rows, name)
        count = sum(row[name] for row in rows)
        boot_low, boot_high = _bootstrap(rows, name, seed=record.seed)
        lines.append(
            f"| {name} precision | {rate:.4f} | {low:.4f} to {high:.4f} | "
            f"{boot_low:.4f} to {boot_high:.4f} | {count} |"
        )
    lines += [
        "",
        "## By stratum",
        "",
        "| stratum | population | sampled | box precision | 95% | label precision | 95% | joint precision | 95% |",
        "| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- |",
    ]
    for name, members in sorted(_by_stratum(reviewed_rows or rows).items()):
        cells = []
        for measure in ("box", "label", "joint"):
            rate, low, high = _simple(members, measure)
            cells.append(f"{rate:.4f} | {low:.4f} to {high:.4f}")
        population = sum(row["frame"] or row["n_stratum"] for row in members) / len(members) if members else 0
        lines.append(f"| {name} | {population:.0f} | {len(members)} | " + " | ".join(cells) + " |")
    lines += [
        "",
        (
            "An audit sample is not a training set: no threshold, cost or model may be tuned on it, "
            "and `atlas audit sample` leaves out every unit an earlier sample holds."
        ),
        "",
    ]
    markdown = "\n".join(lines)
    if out is not None:
        Path(out).write_text(markdown, encoding="utf-8")
    return markdown


def _weighted(rows: list[dict[str, Any]], measure: str) -> tuple[float, float, float]:
    total = sum(row["weight"] for row in rows)
    if not total:
        return (0.0, 0.0, 1.0)
    rate = sum(row["weight"] * row[measure] for row in rows) / total
    successes = sum(row[measure] for row in rows)
    low, high = wilson(successes, len(rows))
    return (rate, low, high)


def _simple(rows: list[dict[str, Any]], measure: str) -> tuple[float, float, float]:
    if not rows:
        return (0.0, 0.0, 1.0)
    successes = sum(row[measure] for row in rows)
    low, high = wilson(successes, len(rows))
    return (successes / len(rows), low, high)


def _by_stratum(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["stratum"]].append(row)
    return grouped


def _bootstrap(
    rows: list[dict[str, Any]], measure: str, *, seed: int = 0, samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float]:
    """Cluster bootstrap over pages: resample the pages, keep every unit of a drawn page."""
    pages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pages[row["page"] or ""].append(row)
    keys = sorted(pages)
    if not keys:
        return (0.0, 1.0)
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        picked = [keys[rng.randrange(len(keys))] for _ in keys]
        weight = sum(row["weight"] for key in picked for row in pages[key])
        if not weight:
            continue
        draws.append(sum(row["weight"] * row[measure] for key in picked for row in pages[key]) / weight)
    if not draws:
        return (0.0, 1.0)
    draws.sort()
    return (draws[max(0, int(0.025 * len(draws)) - 1)], draws[min(len(draws) - 1, int(0.975 * len(draws)))])


def hidden_fields() -> Iterable[str]:
    """The fields a blind review hides; the interface reads them from the sample's `hidden`."""
    return HIDDEN_FIELDS
